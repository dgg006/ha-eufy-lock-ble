"""eufy cloud client against mocked endpoints."""

from __future__ import annotations

import base64

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.eufy_lock_ble.eufy_ble import crypto
from custom_components.eufy_lock_ble.eufy_ble.cloud import (
    SERVER_PUBLIC_KEY,
    AccountLockedOut,
    CaptchaRequired,
    EufyCloud,
    EufyCloudError,
    InvalidAuth,
    InvalidVerifyCode,
    VerifyCodeRequired,
    encrypt_password,
)

from .cloud_fixtures import (
    ADMIN,
    API_BASE,
    EMAIL,
    LOCK_BT,
    LOCK_SN,
    OTHER_SN,
    PUBLIC_KEY,
    login_need_captcha,
    login_need_verify,
    login_ok,
    mock_domain,
    mock_inventory,
    mock_login_sequence,
    ok,
)


def make_cloud(hass: HomeAssistant, country: str = "ca") -> EufyCloud:
    return EufyCloud(async_get_clientsession(hass), EMAIL, "hunter2", country)


def test_encrypt_password_roundtrip() -> None:
    """The server can recover the password from the ECDH-derived key."""
    client = crypto.generate_keypair()
    ct = base64.b64decode(encrypt_password("p@ss wörd", client))
    # simulate the server: it knows the static private key only in production, so
    # re-derive the shared secret from the client's side and decrypt.
    server_pub = crypto.load_public_key(SERVER_PUBLIC_KEY)
    key = crypto.ecdh_shared(client, server_pub)
    assert crypto.aes_cbc_decrypt(key, key[:16], ct) == "p@ss wörd".encode()


async def test_login_and_inventory(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_domain(aioclient_mock)
    bodies = mock_login_sequence(aioclient_mock, [login_ok()])
    mock_inventory(aioclient_mock, bodies)  # encrypted lists, like the real service

    cloud = make_cloud(hass)
    await cloud.login()
    assert cloud.authenticated
    body = bodies[0]
    assert body["email"] == EMAIL
    assert body["ab"] == "CA"
    assert body["client_secret_info"]["public_key"].startswith("04")
    assert len(body["client_secret_info"]["public_key"]) == 130
    assert "hunter2" not in str(body)

    locks = await cloud.list_locks()
    assert [lock.serial for lock in locks] == [LOCK_SN]
    lock = locks[0]
    assert lock.admin_user_id == ADMIN
    assert lock.short_user_id == 0
    assert lock.bt_mac == LOCK_BT
    assert lock.matches_address("aa:bb:cc:dd:ee:ff")
    assert not lock.matches_address("11:22:33:44:55:66")
    assert lock.public_key == PUBLIC_KEY
    assert lock.nickname == "abc***"  # from the "<nick>'s Home" house name
    assert lock.firmware == "2.0.4.2"

    # authenticated calls carry the token and gtoken headers
    _method, _url, _data, headers = aioclient_mock.mock_calls[-1]
    assert headers["X-Auth-Token"] == "token-123"
    assert "gtoken" in headers


async def test_two_factor_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_domain(aioclient_mock)
    bodies = mock_login_sequence(aioclient_mock, [login_need_verify(), login_ok()])
    cloud = make_cloud(hass)
    with pytest.raises(VerifyCodeRequired):
        await cloud.login()
    assert not cloud.authenticated
    sent = [call for call in aioclient_mock.mock_calls if "verify_code" in str(call[1])]
    assert len(sent) == 1 and sent[0][3]["X-Auth-Token"] == "tmp-token"
    await cloud.login(verify_code=" 123456 ")
    assert cloud.authenticated
    assert bodies[1]["verify_code"] == "123456"


async def test_captcha_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_domain(aioclient_mock)
    bodies = mock_login_sequence(aioclient_mock, [login_need_captcha(), login_ok()])
    cloud = make_cloud(hass)
    with pytest.raises(CaptchaRequired) as exc:
        await cloud.login()
    assert exc.value.captcha_id == "cap-1"
    assert exc.value.image_data_url.startswith("data:image/png;base64,")
    await cloud.login(captcha=("cap-1", "AB12"))
    assert bodies[1]["captcha_id"] == "cap-1" and bodies[1]["answer"] == "AB12"


@pytest.mark.parametrize(
    ("code", "exc"),
    [
        (26055, InvalidAuth),
        (22008, InvalidAuth),
        (100028, AccountLockedOut),
        (26050, InvalidVerifyCode),
        (999, EufyCloudError),
    ],
)
async def test_login_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, code: int, exc: type[Exception]
) -> None:
    mock_domain(aioclient_mock)
    mock_login_sequence(aioclient_mock, [{"code": code, "msg": "nope", "data": None}])
    with pytest.raises(exc):
        await make_cloud(hass).login()


async def test_domain_lookup_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get("https://extend.eufylife.com/domain/CA", status=500)
    with pytest.raises(EufyCloudError, match="HTTP 500"):
        await make_cloud(hass).login()


async def test_inventory_multiple_locks_and_missing_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_domain(aioclient_mock)
    bodies = mock_login_sequence(aioclient_mock, [login_ok()])
    # second lock: public key query fails (registered first, the mocker matches in order)
    aioclient_mock.get(
        f"{API_BASE}/v1/app/public_key/query?device_sn={OTHER_SN}&type=2",
        json={"code": 999, "msg": "no key", "data": None},
    )
    mock_inventory(aioclient_mock, bodies, locks=2)
    cloud = make_cloud(hass)
    await cloud.login()
    locks = await cloud.list_locks()
    assert [lock.serial for lock in locks] == [LOCK_SN, OTHER_SN]
    assert locks[1].public_key is None
    assert locks[1].device_type == 203


async def test_inventory_requires_login(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    with pytest.raises(EufyCloudError, match="not logged in"):
        await make_cloud(hass).list_locks()


async def test_plain_json_lists_still_parse(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Older servers answered with plain JSON lists."""
    mock_domain(aioclient_mock)
    mock_login_sequence(aioclient_mock, [login_ok()])
    mock_inventory(aioclient_mock)
    cloud = make_cloud(hass)
    await cloud.login()
    assert [lock.serial for lock in await cloud.list_locks()] == [LOCK_SN]


async def test_undecryptable_list_is_an_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_domain(aioclient_mock)
    mock_login_sequence(aioclient_mock, [login_ok()])
    aioclient_mock.post(f"{API_BASE}/v2/house/station_list", json=ok("bm90IGJhc2U2NCBvZiBhZXM="))
    cloud = make_cloud(hass)
    await cloud.login()
    with pytest.raises(EufyCloudError, match="cannot decrypt"):
        await cloud.list_locks()


async def test_nickname_fallback_to_email(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_domain(aioclient_mock)
    bodies = mock_login_sequence(aioclient_mock, [login_ok()])
    mock_inventory(aioclient_mock, bodies)
    aioclient_mock.post(f"{API_BASE}/v1/house/list", json=ok([]))
    cloud = make_cloud(hass)
    await cloud.login()
    (lock,) = await cloud.list_locks()
    assert lock.nickname == "abc***"

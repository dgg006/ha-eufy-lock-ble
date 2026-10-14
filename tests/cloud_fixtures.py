"""Canned eufy cloud responses for tests (shapes mirror the real API)."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.eufy_lock_ble.eufy_ble import crypto
from custom_components.eufy_lock_ble.eufy_ble.cloud import (
    API_DOMAIN_BASE,
    CODE_LOGIN_NEED_CAPTCHA,
    CODE_NEED_VERIFY_CODE,
    CODE_OK,
    api_shared_key,
    encrypt_api_data,
)

Handler = Callable[[str, Any, Any], Any]

API_BASE = "https://security-app.eufylife.com"
EMAIL = "abcuser@example.com"
ADMIN = "7e" * 20
USER_ID = "7e" * 20
LOCK_SN = "T8510Q1500000001"
LOCK_BT = "AABBCCDDEEFF"
OTHER_SN = "T85V0Q1500000002"
OTHER_BT = "112233445566"
# the server-side half of the login ECDH, used to encrypt list responses like the real API
SERVER_PRIV = crypto.private_key_from_bytes(bytes([0x33]) * 32)
SERVER_PUB_HEX = "04" + crypto.public_key_xy(SERVER_PRIV).hex()
# a genuine P-256 point, so the credentials step accepts it
PUBLIC_KEY = "04" + crypto.public_key_xy(crypto.private_key_from_bytes(bytes([0x22]) * 32)).hex()
CAPTCHA_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def ok(data: Any) -> dict[str, Any]:
    return {"code": CODE_OK, "msg": "Succeed.", "data": data}


def login_ok() -> dict[str, Any]:
    return ok(
        {
            "user_id": USER_ID,
            "email": "ZW5j",
            "nick_name": "",
            "auth_token": "token-123",
            "token_expires_at": 4102444800,
            "server_secret_info": {"public_key": SERVER_PUB_HEX},
        }
    )


def login_need_verify() -> dict[str, Any]:
    return {
        "code": CODE_NEED_VERIFY_CODE,
        "msg": "need verify code",
        "data": {"user_id": USER_ID, "auth_token": "tmp-token", "token_expires_at": 0},
    }


def login_need_captcha(captcha_id: str = "cap-1") -> dict[str, Any]:
    return {
        "code": CODE_LOGIN_NEED_CAPTCHA,
        "msg": "need captcha",
        "data": {"captcha_id": captcha_id, "item": CAPTCHA_PNG_B64},
    }


def member(nick: str = "") -> dict[str, Any]:
    return {
        "admin_user_id": ADMIN,
        "member_user_id": ADMIN,
        "short_user_id": "0000",
        "member_type": 2,
        "nick_name": nick,
    }


def station(sn: str, bt: str, name: str) -> dict[str, Any]:
    return {
        "station_sn": sn,
        "station_name": name,
        "bt_mac": bt,
        "member": member(),
        "devices": [{"device_sn": sn}],
    }


def device(sn: str, bt: str, name: str, dtype: int, fw: str) -> dict[str, Any]:
    return {
        "device_sn": sn,
        "device_name": name,
        "device_type": dtype,
        "device_model": sn[:5],
        "bt_mac": bt,
        "main_sw_version": fw,
        "member": member(),
        "station_sn": sn,
    }


def mock_domain(aioclient_mock: AiohttpClientMocker, country: str = "CA") -> None:
    aioclient_mock.get(
        f"{API_DOMAIN_BASE}/domain/{country}",
        json=ok({"domain": API_BASE.removeprefix("https://")}),
    )


def encrypt_for_client(client_pub_hex: str, payload: Any) -> str:
    """What the server does: AES the JSON with ECDH(server key, client login key)."""
    key = api_shared_key(SERVER_PRIV, client_pub_hex)
    return encrypt_api_data(json.dumps(payload).encode(), key)


def mock_inventory(
    aioclient_mock: AiohttpClientMocker,
    bodies: list[dict[str, Any]] | None = None,
    *,
    locks: int = 1,
    public_key: str = PUBLIC_KEY,
    houses_nick: str = "abc***",
) -> None:
    """Register inventory endpoints.

    When ``bodies`` (the captured login requests) is given, station and device
    lists are returned encrypted for the client key found in the last login body,
    exactly like the real service; otherwise they are plain JSON.
    """
    devs = [
        device(LOCK_SN, LOCK_BT, "Back Door", 50, "2.0.4.2"),
        {"device_sn": "T8010P0000000001", "device_name": "cam", "device_type": 1},
    ]
    stations = [station(LOCK_SN, LOCK_BT, "Back Door")]
    if locks > 1:
        devs.append(device(OTHER_SN, OTHER_BT, "Front Door", 203, "1.1.5.5"))
        stations.append(station(OTHER_SN, OTHER_BT, "Front Door"))
    if bodies is None:
        aioclient_mock.post(f"{API_BASE}/v2/house/station_list", json=ok(stations))
        aioclient_mock.post(f"{API_BASE}/v2/house/device_list", json=ok(devs))
    else:

        def _encrypted(payload: Any) -> Handler:
            async def _handler(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
                client_pub = bodies[-1]["client_secret_info"]["public_key"]
                return AiohttpClientMockResponse(
                    method, url, json=ok(encrypt_for_client(client_pub, payload))
                )

            return _handler

        aioclient_mock.post(f"{API_BASE}/v2/house/station_list", side_effect=_encrypted(stations))
        aioclient_mock.post(f"{API_BASE}/v2/house/device_list", side_effect=_encrypted(devs))
    aioclient_mock.post(
        f"{API_BASE}/v1/house/list",
        json=ok([{"house_id": "h1", "house_name": f"{houses_nick}'s Home"}]),
    )
    for sn in (LOCK_SN, OTHER_SN):
        aioclient_mock.get(
            f"{API_BASE}/v1/app/public_key/query?device_sn={sn}&type=2",
            json=ok({"public_key": public_key}),
        )


def mock_login_sequence(
    aioclient_mock: AiohttpClientMocker, responses: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Answer successive login_sec calls with ``responses`` (last one repeats); return the posted bodies."""
    bodies: list[dict[str, Any]] = []

    async def _handler(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        bodies.append(json.loads(data) if isinstance(data, str | bytes) else dict(data))
        payload = responses[min(len(bodies), len(responses)) - 1]
        return AiohttpClientMockResponse(method, url, json=payload)

    aioclient_mock.post(f"{API_BASE}/v2/passport/login_sec", side_effect=_handler)
    aioclient_mock.post(f"{API_BASE}/v1/sms/send/verify_code", json=ok(None))
    return bodies

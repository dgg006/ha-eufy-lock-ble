"""Minimal eufy cloud client used once, at setup, to fetch the per-lock secrets.

Only what the integration needs is implemented: account login (with the
captcha / e-mail verification round-trips the service may demand), listing the
account's stations, devices and houses, and fetching a lock's static P-256
public key.  The request shapes follow eufy-security-client.  Nothing is
persisted: the caller discards this object, the token and the password once
the secrets have been extracted.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
import json as _json
import logging
import os
import re
import time
from typing import Any

import aiohttp
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import crypto

_LOGGER = logging.getLogger(__name__)

API_DOMAIN_BASE = "https://extend.eufylife.com"
# Static server key used to wrap the password in ``v2/passport/login_sec``.
SERVER_PUBLIC_KEY = (
    "04c5c00c4f8d1197cc7c3167c52bf7acb054d722f0ef08dcd7e0883236e0d72a38"
    "68d9750cb47fa4619248f3d83f0f662671dadc6e2d31c2f41db0161651c7c076"
)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# response codes
CODE_OK = 0
CODE_NEED_VERIFY_CODE = 26052
CODE_VERIFY_CODE_ERROR = 26050
CODE_VERIFY_CODE_EXPIRED = 26051
CODE_VERIFY_CODE_MAX = 26053
CODE_VERIFY_CODE_NONE_MATCH = 26054
CODE_VERIFY_PASSWORD_ERROR = 26055
CODE_MULTIPLE_EMAIL_PASSWORD_ERROR = 26006
CODE_EMAIL_NOT_REGISTERED = 22008
CODE_LOGIN_NEED_CAPTCHA = 100032
CODE_LOGIN_CAPTCHA_ERROR = 100033
CODE_MAX_LOGIN_LIMIT = 100028
CODE_PASSWORD_TOO_MANY_INCORRECT = 10019
CODE_PASSWORD_WRONG_FIVE_TIMES = 100056
CODE_REQUEST_TOO_FAST = 250999

_BAD_CREDENTIAL_CODES = {
    CODE_VERIFY_PASSWORD_ERROR,
    CODE_MULTIPLE_EMAIL_PASSWORD_ERROR,
    CODE_EMAIL_NOT_REGISTERED,
}
_LOCKED_OUT_CODES = {
    CODE_MAX_LOGIN_LIMIT,
    CODE_PASSWORD_TOO_MANY_INCORRECT,
    CODE_PASSWORD_WRONG_FIVE_TIMES,
    CODE_REQUEST_TOO_FAST,
}
_BAD_VERIFY_CODES = {
    CODE_VERIFY_CODE_ERROR,
    CODE_VERIFY_CODE_EXPIRED,
    CODE_VERIFY_CODE_MAX,
    CODE_VERIFY_CODE_NONE_MATCH,
}

VERIFY_CODE_TYPE_EMAIL = 2
PUBLIC_KEY_TYPE_LOCK = 2

# Serial prefixes of eufy locks; anything else in the account is ignored.
LOCK_SERIAL_PREFIX = "T85"


class EufyCloudError(Exception):
    """Generic cloud failure (network, HTTP, unexpected payload, error code)."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class InvalidAuth(EufyCloudError):
    """Wrong e-mail / password."""


class AccountLockedOut(EufyCloudError):
    """Too many attempts; the service refuses further logins for a while."""


class InvalidVerifyCode(EufyCloudError):
    """The e-mail verification code was wrong or expired."""


class CaptchaRequired(Exception):
    """The service wants a captcha solved before it accepts the password."""

    def __init__(self, captcha_id: str, image_data_url: str) -> None:
        super().__init__("captcha required")
        self.captcha_id = captcha_id
        self.image_data_url = image_data_url


class VerifyCodeRequired(Exception):
    """A verification code has been e-mailed to the account."""


@dataclass(frozen=True, slots=True)
class CloudLock:
    """Everything the BLE integration needs about one lock."""

    serial: str
    name: str
    bt_mac: str | None
    model: str | None
    device_type: int | None
    firmware: str | None
    admin_user_id: str
    short_user_id: int
    nickname: str
    public_key: str | None

    def matches_address(self, address: str) -> bool:
        """Return True when ``address`` (``AA:BB:...``) is this lock's Bluetooth MAC."""
        return bool(self.bt_mac) and _norm_mac(self.bt_mac) == _norm_mac(address)


def _norm_mac(value: str | None) -> str:
    return re.sub(r"[^0-9a-f]", "", (value or "").lower())


def _tz_offset_ms(now: datetime) -> int:
    offset = now.utcoffset()
    return int(offset.total_seconds() * 1000) if offset else 0


def _tz_header(now: datetime) -> str:
    offset = now.utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset else 0
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return f"GMT{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def api_shared_key(
    client_priv: ec.EllipticCurvePrivateKey, server_pub_hex: str = SERVER_PUBLIC_KEY
) -> bytes:
    """32-byte ECDH secret between our client key and the server's P-256 key."""
    return crypto.ecdh_shared(client_priv, crypto.load_public_key(server_pub_hex))


def encrypt_api_data(data: bytes, key: bytes) -> str:
    """AES-256-CBC with IV = key[:16], base64 (the cloud's payload wrapping)."""
    enc = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(enc.update(crypto.pkcs7_pad(data)) + enc.finalize()).decode("ascii")


def decrypt_api_data(data: str, key: bytes) -> bytes:
    """Inverse of :func:`encrypt_api_data`; trailing NULs are stripped."""
    dec = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
    raw = dec.update(base64.b64decode(data)) + dec.finalize()
    return crypto.pkcs7_unpad(raw).split(b"\x00", 1)[0]


def encrypt_password(password: str, client_priv: ec.EllipticCurvePrivateKey) -> str:
    """Wrap the password for ``login_sec`` (keyed with the static server key)."""
    return encrypt_api_data(password.encode("utf-8"), api_shared_key(client_priv))


@dataclass
class EufyCloud:
    """One short-lived authenticated session with the eufy cloud."""

    session: aiohttp.ClientSession
    email: str
    password: str
    country: str = "US"
    language: str = "en"
    now: Any = field(default=datetime.now, repr=False)

    def __post_init__(self) -> None:
        self.country = (self.country or "US").upper()[:2]
        self._api_base: str | None = None
        self._token: str | None = None
        self._client_priv = crypto.generate_keypair()
        self._openudid = os.urandom(8).hex()
        self._logged_in = False
        self._server_pub = SERVER_PUBLIC_KEY
        self.user_id: str | None = None
        self.nick_name: str | None = None

    # ------------------------------------------------------------- plumbing
    def _headers(self) -> dict[str, str]:
        now = self.now()
        headers = {
            "User-Agent": "got (https://github.com/sindresorhus/got)",
            "app_version": "v4.6.0_1630",
            "os_type": "android",
            "os_version": "31",
            "phone_model": "ONEPLUS A3003",
            "country": self.country,
            "language": self.language,
            "openudid": self._openudid,
            "net_type": "wifi",
            "mnc": "02",
            "mcc": "262",
            "sn": "75814221ee75",
            "model_type": "PHONE",
            "timezone": _tz_header(now),
            "Cache-Control": "no-cache",
        }
        if self._token:
            headers["X-Auth-Token"] = self._token
        if self.user_id:
            headers["gtoken"] = _md5(self.user_id)
        return headers

    async def _api_base_url(self) -> str:
        if self._api_base is None:
            body = await self._fetch("GET", f"{API_DOMAIN_BASE}/domain/{self.country}")
            domain = (body.get("data") or {}).get("domain")
            if body.get("code") != CODE_OK or not domain:
                raise EufyCloudError(
                    f"cannot resolve API domain for {self.country}: {body.get('msg')}",
                    body.get("code"),
                )
            self._api_base = f"https://{domain}"
        return self._api_base

    async def _fetch(
        self, method: str, url: str, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            async with self.session.request(
                method, url, json=json, headers=self._headers(), timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status != 200:
                    raise EufyCloudError(f"HTTP {resp.status} from {url}")
                body = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise EufyCloudError(f"network error talking to eufy cloud: {err}") from err
        if not isinstance(body, dict):
            raise EufyCloudError(f"unexpected response from {url}")
        return body

    async def _request(
        self, method: str, endpoint: str, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        base = await self._api_base_url()
        return await self._fetch(method, f"{base}/{endpoint}", json)

    def _transaction(self) -> str:
        return str(int(time.time() * 1000))

    # ---------------------------------------------------------------- login
    async def login(
        self,
        *,
        verify_code: str | None = None,
        captcha: tuple[str, str] | None = None,
    ) -> None:
        """Authenticate.

        Raises :class:`CaptchaRequired` or :class:`VerifyCodeRequired` when the
        service needs another round-trip; call again with ``captcha=(id, answer)``
        or ``verify_code=...``.
        """
        now = self.now()
        data: dict[str, Any] = {
            "ab": self.country,
            "client_secret_info": {"public_key": crypto.public_key_xy(self._client_priv).hex()},
            "enc": 0,
            "email": self.email,
            "password": encrypt_password(self.password, self._client_priv),
            "time_zone": _tz_offset_ms(now),
            "transaction": self._transaction(),
        }
        # the app sends the uncompressed point with its 0x04 prefix
        data["client_secret_info"]["public_key"] = "04" + data["client_secret_info"]["public_key"]
        if verify_code:
            data["verify_code"] = verify_code.strip()
        elif captcha:
            data["captcha_id"], data["answer"] = captcha[0], captcha[1].strip()

        body = await self._request("POST", "v2/passport/login_sec", data)
        code = body.get("code")
        result = body.get("data") or {}
        if code == CODE_OK:
            self._token = result.get("auth_token")
            self.user_id = result.get("user_id")
            self.nick_name = result.get("nick_name") or None
            if not self._token:
                raise EufyCloudError("login succeeded without a token")
            # list responses are encrypted with ECDH(client key, this per-session server key)
            server_pub = (result.get("server_secret_info") or {}).get("public_key")
            if server_pub:
                try:
                    crypto.load_public_key(str(server_pub))
                    self._server_pub = str(server_pub)
                except crypto.CryptoError:
                    _LOGGER.debug("ignoring invalid server public key from login")
            self._logged_in = True
            return
        if code == CODE_NEED_VERIFY_CODE:
            # temporary token lets us ask for the e-mail code
            self._token = result.get("auth_token")
            self.user_id = result.get("user_id") or self.user_id
            await self._send_verify_code()
            raise VerifyCodeRequired
        if code in (CODE_LOGIN_NEED_CAPTCHA, CODE_LOGIN_CAPTCHA_ERROR):
            raise CaptchaRequired(
                str(result.get("captcha_id", "")), _as_data_url(result.get("item", ""))
            )
        if code in _BAD_VERIFY_CODES:
            raise InvalidVerifyCode(body.get("msg") or "invalid verification code", code)
        if code in _BAD_CREDENTIAL_CODES:
            raise InvalidAuth(body.get("msg") or "invalid e-mail or password", code)
        if code in _LOCKED_OUT_CODES:
            raise AccountLockedOut(body.get("msg") or "too many attempts", code)
        raise EufyCloudError(f"login failed: {body.get('msg')} (code {code})", code)

    async def _send_verify_code(self) -> None:
        body = await self._request(
            "POST",
            "v1/sms/send/verify_code",
            {"message_type": VERIFY_CODE_TYPE_EMAIL, "transaction": self._transaction()},
        )
        if body.get("code") != CODE_OK:
            raise EufyCloudError(
                f"cannot send verification code: {body.get('msg')}", body.get("code")
            )

    @property
    def authenticated(self) -> bool:
        """True after a successful :meth:`login`."""
        return self._logged_in and self._token is not None

    # ------------------------------------------------------------ inventory
    def _list_body(self) -> dict[str, Any]:
        return {
            "device_sn": "",
            "num": 1000,
            "orderby": "",
            "page": 0,
            "station_sn": "",
            "time_zone": _tz_offset_ms(self.now()),
            "transaction": self._transaction(),
        }

    def _decode_list(self, endpoint: str, data: Any) -> list[dict[str, Any]]:
        """Decode a list payload: AES-encrypted base64 string, or plain JSON on older servers."""
        if isinstance(data, str) and data:
            try:
                plain = decrypt_api_data(data, api_shared_key(self._client_priv, self._server_pub))
                data = _json.loads(plain.decode("utf-8"))
            except (ValueError, crypto.CryptoError) as err:
                raise EufyCloudError(f"cannot decrypt {endpoint} response: {err}") from err
        if data is None:
            return []
        if isinstance(data, dict):
            # some endpoints wrap the list, e.g. {"list": [...]} or {"data": [...]}
            for key in ("list", "data", "devices", "stations"):
                if isinstance(data.get(key), list):
                    return data[key]
            return []
        return data if isinstance(data, list) else []

    async def _list(self, endpoint: str) -> list[dict[str, Any]]:
        body = await self._request("POST", endpoint, self._list_body())
        if body.get("code") != CODE_OK:
            raise EufyCloudError(f"{endpoint} failed: {body.get('msg')}", body.get("code"))
        items = self._decode_list(endpoint, body.get("data"))
        _LOGGER.debug("%s returned %d entries", endpoint, len(items))
        return items

    async def get_public_key(self, serial: str) -> str | None:
        """Fetch a lock's static P-256 public key (hex X‖Y)."""
        body = await self._request(
            "GET", f"v1/app/public_key/query?device_sn={serial}&type={PUBLIC_KEY_TYPE_LOCK}"
        )
        if body.get("code") != CODE_OK:
            _LOGGER.debug("Public key query failed (code %s)", body.get("code"))
            return None
        key = (body.get("data") or {}).get("public_key")
        return str(key) if key else None

    async def list_locks(self) -> list[CloudLock]:
        """Enumerate the account's locks with the secrets the BLE protocol needs."""
        if not self.authenticated:
            raise EufyCloudError("not logged in")
        stations = {s.get("station_sn"): s for s in await self._list("v2/house/station_list")}
        devices = await self._list("v2/house/device_list")
        houses = await self._list("v1/house/list")
        fallback_nick = self._fallback_nickname(houses)

        locks: list[CloudLock] = []
        for dev in devices:
            serial = str(dev.get("device_sn") or "")
            if not serial.startswith(LOCK_SERIAL_PREFIX):
                continue
            station = stations.get(serial) or stations.get(dev.get("station_sn")) or {}
            member = dev.get("member") or station.get("member") or {}
            admin = str(member.get("admin_user_id") or "")
            if not re.fullmatch(r"[0-9a-fA-F]{40}", admin):
                _LOGGER.debug("Skipping a lock without an admin user ID")
                continue
            try:
                short = int(str(member.get("short_user_id") or "0"), 10)
            except ValueError:
                short = 0
            locks.append(
                CloudLock(
                    serial=serial,
                    name=str(dev.get("device_name") or station.get("station_name") or serial),
                    bt_mac=dev.get("bt_mac") or station.get("bt_mac"),
                    model=dev.get("device_model") or station.get("station_model"),
                    device_type=dev.get("device_type"),
                    firmware=dev.get("main_sw_version"),
                    admin_user_id=admin.lower(),
                    short_user_id=short,
                    nickname=str(member.get("nick_name") or fallback_nick),
                    public_key=await self.get_public_key(serial),
                )
            )
        return locks

    def _fallback_nickname(self, houses: list[dict[str, Any]]) -> str:
        """Derive the account's *masked* nickname (e.g. ``abc***``) that the lock expects.

        Station members usually carry an empty ``nick_name``; the masked form shows
        up in the login profile and as the ``<nick>'s Home`` house name.  Last
        resort: first three characters of the e-mail plus ``***``, which is how the
        service masks it.
        """
        if self.nick_name:
            return self.nick_name
        for house in houses:
            name = str(house.get("house_name") or "")
            if name.endswith("'s Home") and name[:-7]:
                return name[:-7]
        local = self.email.split("@", 1)[0]
        return f"{local[:3]}***"


def _md5(value: str) -> str:
    import hashlib

    return hashlib.md5(value.encode("utf-8"), usedforsecurity=False).hexdigest()


def _as_data_url(item: str) -> str:
    item = str(item or "")
    if not item:
        return ""
    if item.startswith("data:"):
        return item
    return f"data:image/png;base64,{item}"

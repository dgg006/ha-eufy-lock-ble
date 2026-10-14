"""Cryptographic primitives used by eufy BLE locks.

Port of eufy-security-client's lock crypto (``eufyKDF`` / ``getLockV12Key`` /
``encryptPayloadData``), verified byte-exact against a real lock.  Everything here is
pure and synchronous; nothing depends on Home Assistant.

* ECDH on P-256 (prime256v1), shared secret = X coordinate.
* ``eufy_kdf(shared)`` -> 48 bytes: HMAC-SHA256 chain with the static label ``"ECIES"``.
* ECIES blob layout::

      compressed_ephemeral_pub(33) || rand(16)
      || AES-128-CBC( PKCS7(data), dk[0:16], iv=rand )
      || HMAC-SHA256( dk[16:48], rand || enc )(32)

* Session cipher: AES-128-CBC + PKCS7, key = session key ``K`` handed out by the lock,
  IV = the 16-character lock serial as ASCII.
"""

from __future__ import annotations

import hashlib
import hmac
import os

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

P256 = ec.SECP256R1()
_KDF_LABEL = b"ECIES"


class CryptoError(ValueError):
    """Raised when a blob fails authentication or has an invalid shape."""


def _hmac256(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def eufy_kdf(secret: bytes) -> bytes:
    """Derive the 48-byte ECIES key material from an ECDH shared secret."""
    out = bytearray()
    tmp = _KDF_LABEL
    for _ in range(2):
        tmp = _hmac256(secret, tmp)
        out += _hmac256(secret, tmp + _KDF_LABEL)
    return bytes(out[:48])


def pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    """PKCS#7 pad ``data`` to a multiple of ``block``."""
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


def pkcs7_unpad(data: bytes) -> bytes:
    """Strip PKCS#7 padding, raising :class:`CryptoError` if it is malformed."""
    if not data or len(data) % 16:
        raise CryptoError("ciphertext length is not a multiple of 16")
    pad = data[-1]
    if not 1 <= pad <= 16 or data[-pad:] != bytes([pad]) * pad:
        raise CryptoError("bad PKCS#7 padding")
    return data[:-pad]


def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """AES-128-CBC encrypt ``plaintext`` (PKCS#7 padded here)."""
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(pkcs7_pad(plaintext)) + enc.finalize()


def aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """AES-128-CBC decrypt and strip PKCS#7 padding."""
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return pkcs7_unpad(dec.update(ciphertext) + dec.finalize())


def serial_iv(serial: str) -> bytes:
    """Session-cipher IV: the lock serial as ASCII, zero padded/truncated to 16 bytes."""
    raw = serial.encode("ascii")
    return (raw + b"\x00" * 16)[:16]


def generate_keypair() -> ec.EllipticCurvePrivateKey:
    """Generate a fresh P-256 keypair (used once per BLE session)."""
    return ec.generate_private_key(P256)


def private_key_from_bytes(raw: bytes) -> ec.EllipticCurvePrivateKey:
    """Load a P-256 private scalar (tests / deterministic vectors)."""
    return ec.derive_private_key(int.from_bytes(raw, "big"), P256)


def public_key_xy(priv: ec.EllipticCurvePrivateKey) -> bytes:
    """Uncompressed public point as raw ``X || Y`` (64 bytes, no 0x04 prefix)."""
    return priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)[1:]


def public_key_compressed(priv: ec.EllipticCurvePrivateKey) -> bytes:
    """Compressed public point (33 bytes)."""
    return priv.public_key().public_bytes(Encoding.X962, PublicFormat.CompressedPoint)


def load_public_key(encoded: bytes | str) -> ec.EllipticCurvePublicKey:
    """Load a P-256 public key from hex or bytes.

    Accepts raw ``X || Y`` (64 bytes), SEC1 uncompressed (65 bytes, ``0x04`` prefix)
    or SEC1 compressed (33 bytes).
    """
    raw = bytes.fromhex(encoded) if isinstance(encoded, str) else bytes(encoded)
    if len(raw) == 64:
        raw = b"\x04" + raw
    if len(raw) not in (33, 65):
        raise CryptoError(f"unsupported public key length {len(raw)}")
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(P256, raw)
    except ValueError as err:
        raise CryptoError("public key is not a valid P-256 point") from err


def ecdh_shared(priv: ec.EllipticCurvePrivateKey, peer: ec.EllipticCurvePublicKey) -> bytes:
    """ECDH shared secret (32-byte X coordinate)."""
    return priv.exchange(ec.ECDH(), peer)


def ecies_encrypt(
    data: bytes,
    peer_pub: ec.EllipticCurvePublicKey,
    *,
    ephemeral: ec.EllipticCurvePrivateKey | None = None,
    rand: bytes | None = None,
) -> bytes:
    """Encrypt ``data`` to ``peer_pub`` with eufy's ECIES construction.

    ``ephemeral`` and ``rand`` are only meant to be supplied by tests.
    """
    eph = ephemeral or generate_keypair()
    rand = rand if rand is not None else os.urandom(16)
    if len(rand) != 16:
        raise CryptoError("ECIES nonce must be 16 bytes")
    dk = eufy_kdf(ecdh_shared(eph, peer_pub))
    enc = aes_cbc_encrypt(dk[:16], rand, data)
    mac = _hmac256(dk[16:48], rand + enc)
    return public_key_compressed(eph) + rand + enc + mac


def ecies_decrypt(blob: bytes, priv: ec.EllipticCurvePrivateKey) -> bytes:
    """Inverse of :func:`ecies_encrypt` for blobs addressed to ``priv``."""
    if len(blob) < 33 + 16 + 16 + 32:
        raise CryptoError(f"ECIES blob too short ({len(blob)} bytes)")
    comp, rand, rest = blob[:33], blob[33:49], blob[49:]
    enc, mac = rest[:-32], rest[-32:]
    eph_pub = load_public_key(comp)
    dk = eufy_kdf(ecdh_shared(priv, eph_pub))
    if not hmac.compare_digest(mac, _hmac256(dk[16:48], rand + enc)):
        raise CryptoError("ECIES HMAC mismatch")
    return aes_cbc_decrypt(dk[:16], rand, enc)

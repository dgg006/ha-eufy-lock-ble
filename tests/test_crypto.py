"""Crypto primitives against vectors produced by the verified reference implementation."""

from __future__ import annotations

import pytest

from custom_components.eufy_lock_ble.eufy_ble import crypto

# Generated with the byte-exact reference (eufy_crypto.py) on 2026-09-07.
KDF_IN = bytes(range(32))
KDF_OUT = "966d8847570f2ddd76181dbff182b22ceb4e4447a22edefe2584a4b1e8d11d80fa964aeffb90b9818f37fe86f14db109"
EPH_PRIV = bytes.fromhex("11" * 32)
RCPT_PRIV = bytes.fromhex("22" * 32)
RCPT_PUB = "d65a93977caa3d1b081852ff57a79e465f1660577304baead505dd3a48589cf350185e895372df6221ea3a137557e473fddb6755f05bd507c3c533fce9c91285"
RAND = bytes.fromhex("33" * 16)
DATA = bytes.fromhex("a1140102030405060708090a0b0c0d0e0f1011121314a20701020304050684")
BLOB = (
    "020217e617f0b6443928278f96999e69a23a4f2c152bdf6d6cdf66e5b80282d4ed"
    "33333333333333333333333333333333"
    "ca215370d607bec148e5fc891601be138db0c95abebbdd0ce1dc217a12300f102b5e05d71863cae7adaf005d5e90e714"
    "1dbf07a57dcd7e16399e2b530cd2866e"
)
SESSION_KEY = bytes.fromhex("3edbcd444760a8a098ecce79da99c1eb")
SERIAL = "T8510Q1500000001"
ONOFF_PLAIN = bytes.fromhex("a1020000a20100a304a1b2c3d4a4066162632a2a2a")
ONOFF_CT = "c2de1b3a394809961262496b14c7c15cfe122430cbd5e56b8c8a173ffe82ef8a"


def test_kdf_vector() -> None:
    assert crypto.eufy_kdf(KDF_IN).hex() == KDF_OUT
    assert len(crypto.eufy_kdf(b"\x00" * 32)) == 48


def test_ecies_encrypt_matches_reference() -> None:
    eph = crypto.private_key_from_bytes(EPH_PRIV)
    rcpt = crypto.load_public_key(RCPT_PUB)
    assert crypto.public_key_xy(crypto.private_key_from_bytes(RCPT_PRIV)).hex() == RCPT_PUB
    blob = crypto.ecies_encrypt(DATA, rcpt, ephemeral=eph, rand=RAND)
    assert blob.hex() == BLOB


def test_ecies_decrypt_reference_blob() -> None:
    rcpt = crypto.private_key_from_bytes(RCPT_PRIV)
    assert crypto.ecies_decrypt(bytes.fromhex(BLOB), rcpt) == DATA


def test_ecies_roundtrip_random() -> None:
    rcpt = crypto.generate_keypair()
    for size in (1, 15, 16, 17, 97, 112):
        data = bytes(range(size))
        blob = crypto.ecies_encrypt(data, rcpt.public_key())
        assert len(blob) == 33 + 16 + len(crypto.pkcs7_pad(data)) + 32
        assert crypto.ecies_decrypt(blob, rcpt) == data


def test_ecies_tamper_detected() -> None:
    rcpt = crypto.private_key_from_bytes(RCPT_PRIV)
    blob = bytearray(bytes.fromhex(BLOB))
    blob[60] ^= 0x01
    with pytest.raises(crypto.CryptoError, match="HMAC"):
        crypto.ecies_decrypt(bytes(blob), rcpt)
    with pytest.raises(crypto.CryptoError, match="short"):
        crypto.ecies_decrypt(b"\x02" * 40, rcpt)


def test_session_cipher_vector() -> None:
    iv = crypto.serial_iv(SERIAL)
    assert iv == SERIAL.encode()
    assert crypto.aes_cbc_encrypt(SESSION_KEY, iv, ONOFF_PLAIN).hex() == ONOFF_CT
    assert crypto.aes_cbc_decrypt(SESSION_KEY, iv, bytes.fromhex(ONOFF_CT)) == ONOFF_PLAIN


def test_serial_iv_padding() -> None:
    assert crypto.serial_iv("ABC") == b"ABC" + b"\x00" * 13
    assert crypto.serial_iv("A" * 20) == b"A" * 16


def test_pkcs7_errors() -> None:
    with pytest.raises(crypto.CryptoError):
        crypto.pkcs7_unpad(b"")
    with pytest.raises(crypto.CryptoError):
        crypto.pkcs7_unpad(b"\x00" * 15 + b"\x11")
    with pytest.raises(crypto.CryptoError):
        crypto.pkcs7_unpad(b"\x00" * 14 + b"\x01\x02")


def test_load_public_key_formats() -> None:
    xy = bytes.fromhex(RCPT_PUB)
    a = crypto.load_public_key(xy)
    b = crypto.load_public_key(b"\x04" + xy)
    c = crypto.load_public_key(RCPT_PUB)
    assert a.public_numbers() == b.public_numbers() == c.public_numbers()
    with pytest.raises(crypto.CryptoError):
        crypto.load_public_key(b"\x00" * 64)
    with pytest.raises(crypto.CryptoError):
        crypto.load_public_key(b"\x04" * 10)

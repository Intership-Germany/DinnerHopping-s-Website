from passlib.hash import bcrypt as legacy_bcrypt

from app.auth import hash_password, verify_password


def test_hash_password_supports_long_input():
    long_password = "A" * 200
    hashed = hash_password(long_password)

    assert isinstance(hashed, str) and len(hashed) > 0
    assert verify_password(long_password, hashed)


def test_verify_password_accepts_legacy_bcrypt_hash():
    password = "LegacyPass123!"
    legacy_hash = legacy_bcrypt.hash(password)

    assert verify_password(password, legacy_hash)

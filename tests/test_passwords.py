from app.security.passwords import hash_password, needs_rehash, verify_password


def test_argon2_or_pbkdf2_roundtrip():
    stored = hash_password("correct-horse")
    assert verify_password("correct-horse", stored)
    assert not verify_password("wrong-horse", stored)


def test_legacy_pbkdf2_verifies_and_needs_rehash():
    import hashlib
    import secrets

    password = "legacy-pass"
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    stored = f"pbkdf2_sha256$120000${salt}${digest}"
    assert verify_password(password, stored)
    assert not verify_password("nope", stored)
    assert needs_rehash(stored)

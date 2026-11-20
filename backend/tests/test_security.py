"""Synthetic password policy and real Argon2id checks; no database access."""

import pytest
from pydantic import ValidationError

from app.core import security
from app.schemas.auth import RegisterRequest, UserResponse


@pytest.mark.parametrize("password", ["a" * 15, "a" * 128, "猫" * 15, "😀" * 128, " " * 15, "  spaces are retained  "])
def test_password_policy_preserves_valid_unicode_and_spaces(password):
    assert security.validate_password(password) == password


@pytest.mark.parametrize("password", ["a" * 14, "a" * 129, "😀" * 257, "\ud800" * 15, None, 123, True, b"a" * 15, ["secret"], {"secret": "value"}])
def test_invalid_password_never_reaches_hasher(password, monkeypatch):
    calls = []
    monkeypatch.setattr(security._password_hash, "hash", lambda value: calls.append(value))
    with pytest.raises(ValueError):
        security.hash_password(password)
    assert calls == []


def test_real_argon2id_random_salts_and_verification():
    password = "  synthetic Unicode 猫 passphrase  "
    first = security.hash_password(password)
    second = security.hash_password(password)
    assert first.startswith("$argon2id$") and second.startswith("$argon2id$")
    assert first != second and first != password
    assert len(first) <= 255 and len(second) <= 255
    assert security.verify_password(password, first)
    assert not security.verify_password(password.strip(), first)
    assert not security.verify_password(password.upper(), first)


def test_request_repr_and_public_response_allowlist():
    password = "synthetic-private-password"
    request = RegisterRequest(email=" Researcher@Example.COM ", password=password)
    assert request.email == "researcher@example.com"
    assert password not in repr(request)
    assert password not in request.model_dump_json()
    assert set(UserResponse.model_fields) == {"id", "email", "is_active", "created_at", "updated_at"}


def test_email_validation_does_not_query_dns(monkeypatch):
    import dns.resolver

    def no_dns(*args, **kwargs):
        raise AssertionError("Registration must not perform DNS validation")

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", no_dns)
    assert RegisterRequest(email="User+tag@Example.com", password="a" * 15).email == "user+tag@example.com"


def test_byte_limit_remains_independent_if_character_policy_changes(monkeypatch):
    # The production 128-character cap already limits valid UTF-8 to 512 bytes.
    # Widen only this test's character cap to exercise the independent byte cap.
    monkeypatch.setattr(security, "MAX_PASSWORD_CHARACTERS", 1024)
    assert security.validate_password("😀" * 256) == "😀" * 256
    with pytest.raises(ValueError):
        security.validate_password("😀" * 257)

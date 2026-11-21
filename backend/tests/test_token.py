"""JWT and authentication configuration tests without database access."""

from base64 import urlsafe_b64encode
from datetime import datetime, timezone
import json
from uuid import uuid4

import jwt
import pytest
from pydantic import ValidationError

from app.core import config
from app.core.config import AuthSettings, Settings
from app.core.token import InvalidAccessToken, issue_access_token, read_access_token


@pytest.fixture
def claims():
    now = int(datetime.now(timezone.utc).timestamp())
    return {
        "sub": str(uuid4()),
        "iat": now - 30,
        "nbf": now - 30,
        "exp": now + 870,
        "iss": "paperpilot-researchops",
        "aud": "paperpilot-api",
        "token_type": "access",
    }


@pytest.fixture
def key(auth_settings):
    return bytes.fromhex(auth_settings.jwt_secret_key.get_secret_value())


def test_access_token_issues_expected_claims_and_round_trips(auth_settings, key):
    user_id = uuid4()
    token = issue_access_token(user_id, auth_settings)

    assert read_access_token(token, auth_settings) == user_id
    assert jwt.get_unverified_header(token)["alg"] == "HS256"
    payload = jwt.decode(token, key, algorithms=["HS256"], audience="paperpilot-api", issuer="paperpilot-researchops")
    assert set(payload) == {"sub", "iat", "nbf", "exp", "iss", "aud", "token_type"}
    assert payload["sub"] == str(user_id) and payload["token_type"] == "access"
    assert payload["iat"] == payload["nbf"] and payload["exp"] - payload["iat"] == 900


@pytest.mark.parametrize("missing", ["sub", "iat", "nbf", "exp", "iss", "aud", "token_type"])
def test_required_claim_cannot_be_omitted(auth_settings, key, claims, missing):
    del claims[missing]
    with pytest.raises(InvalidAccessToken):
        read_access_token(jwt.encode(claims, key, algorithm="HS256"), auth_settings)


@pytest.mark.parametrize("updates", [
    {"iss": "incorrect-issuer"},
    {"iss": ["paperpilot-researchops"]},
    {"aud": "incorrect-audience"},
    {"aud": ["paperpilot-api"]},
    {"token_type": "refresh"},
    {"sub": "not-a-uuid"},
    {"sub": 123},
    {"iat": None},
    {"exp": True},
    {"nbf": "0"},
    {"nbf": []},
])
def test_invalid_signed_claims_are_rejected(auth_settings, key, claims, updates):
    claims.update(updates)
    token = jwt.api_jws.encode(json.dumps(claims).encode(), key, algorithm="HS256")
    with pytest.raises(InvalidAccessToken):
        read_access_token(token, auth_settings)


@pytest.mark.parametrize("change", ["expired", "future-issued", "future-not-before", "excessive-lifetime"])
def test_token_time_constraints(auth_settings, key, claims, change):
    now = claims["iat"] + 30
    changes = {
        "expired": {"iat": now - 901, "nbf": now - 901, "exp": now - 1},
        "future-issued": {"iat": now + 60},
        "future-not-before": {"nbf": now + 60},
        "excessive-lifetime": {"exp": now + 1800},
    }
    claims.update(changes[change])
    with pytest.raises(InvalidAccessToken):
        read_access_token(jwt.encode(claims, key, algorithm="HS256"), auth_settings)


@pytest.mark.parametrize("algorithm", ["HS384", "none"])
def test_other_algorithm_is_rejected(auth_settings, key, claims, algorithm):
    signing_key = key + key if algorithm == "HS384" else ""
    with pytest.raises(InvalidAccessToken):
        read_access_token(jwt.encode(claims, signing_key, algorithm=algorithm), auth_settings)


def test_token_with_wrong_signature_is_rejected(auth_settings, key, claims):
    wrong_key = bytes(byte ^ 1 for byte in key)
    with pytest.raises(InvalidAccessToken):
        read_access_token(jwt.encode(claims, wrong_key, algorithm="HS256"), auth_settings)


def test_payload_tampering_is_rejected(auth_settings, key, claims):
    header, _, signature = jwt.encode(claims, key, algorithm="HS256").split(".")
    claims["sub"] = str(uuid4())
    new_payload = urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    with pytest.raises(InvalidAccessToken):
        read_access_token(f"{header}.{new_payload}.{signature}", auth_settings)


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c.d", "a.b. c", "a" * 4097])
def test_malformed_token_is_rejected(auth_settings, token):
    with pytest.raises(InvalidAccessToken):
        read_access_token(token, auth_settings)


@pytest.mark.parametrize("secret", ["", "weak", "ab" * 32, "0123456789abcdef" * 4, "g1" * 32])
def test_weak_keys_are_refused_without_echo(secret):
    with pytest.raises(ValidationError) as error:
        AuthSettings(jwt_secret_key=secret, _env_file=None)
    if secret:
        assert secret not in str(error.value)


def test_auth_config_uses_dotenv_without_requiring_database_credentials(tmp_path, monkeypatch, auth_settings):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    dotenv = tmp_path / "synthetic.env"
    key = auth_settings.jwt_secret_key.get_secret_value()
    dotenv.write_text(f"JWT_SECRET_KEY={key}\nUNRELATED_SETTING=ignored\n", encoding="utf-8")
    settings = AuthSettings(_env_file=dotenv)
    assert settings.jwt_secret_key == auth_settings.jwt_secret_key
    assert key not in repr(settings)


def test_database_config_does_not_validate_the_authentication_key(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "synthetic-invalid-key")
    assert Settings(_env_file=None, db_password="synthetic-db-password").db_name


def test_auth_configuration_error_has_no_sensitive_exception_chain(monkeypatch):
    marker = "synthetic-invalid-signing-key-marker"
    monkeypatch.setattr(config, "AuthSettings", lambda: AuthSettings(jwt_secret_key=marker, _env_file=None))
    config.get_auth_settings.cache_clear()
    try:
        with pytest.raises(config.AuthenticationConfigurationError) as error:
            config.get_auth_settings()
        final = error.value
        assert marker not in str(final) + repr(final)
        assert final.__cause__ is None
        chain_has_input = isinstance(final.__context__, ValidationError) and any(
            item.get("input") == marker for item in final.__context__.errors()
        )
        assert not chain_has_input
        assert final.__context__ is None
    finally:
        config.get_auth_settings.cache_clear()

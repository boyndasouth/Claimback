"""Tests for the embedded-chat JWT signer."""
from __future__ import annotations

import pathlib
import sys

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

import services.chat_token as ct


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    for var in ("WXO_EMBED_PRIVATE_KEY", "WXO_EMBED_PRIVATE_KEY_PATH", "WXO_EMBED_TOKEN_TTL_SECONDS"):
        monkeypatch.delenv(var, raising=False)
    ct._private_key_pem.cache_clear()
    yield
    ct._private_key_pem.cache_clear()


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return priv, pub


def test_security_off_without_key():
    assert ct.sign_chat_token() is None
    assert ct.chat_security_enabled() is False


def test_signs_rs256_token_verifiable_with_public_key(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setenv("WXO_EMBED_PRIVATE_KEY", priv.replace("\n", "\n"))  # env-style escaped
    token, ttl = ct.sign_chat_token("visitor-1")
    claims = jwt.decode(token, pub, algorithms=["RS256"])
    assert claims["sub"] == "visitor-1"
    assert claims["exp"] - claims["iat"] == ttl == 3600


def test_ttl_is_capped_at_ibm_maximum(monkeypatch):
    priv, _ = _keypair()
    monkeypatch.setenv("WXO_EMBED_PRIVATE_KEY", priv)
    monkeypatch.setenv("WXO_EMBED_TOKEN_TTL_SECONDS", "999999")
    _, ttl = ct.sign_chat_token()
    assert ttl == 7100

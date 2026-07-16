"""Shared fixtures for OnboardKit tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _safe_app_env(monkeypatch):
    """Keep tests out of production-strict mode unless a test opts in."""
    monkeypatch.setenv("APP_ENV", "development")


@pytest.fixture
def env_auth(monkeypatch):
    """Configure the local .env credential fallback (no LDAP)."""
    monkeypatch.setenv("LDAP_HOST", "")
    monkeypatch.setenv("ADMIN_USERNAME", "testadmin")
    monkeypatch.setenv("ADMIN_PASSWORD", "local-dev-pass-99!")
    return {"username": "testadmin", "password": "local-dev-pass-99!"}


@pytest.fixture
def client(env_auth):
    """HTTP client with lifespan startup (config/presets init)."""
    from main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def authed_client(client, env_auth):
    """Signed-in portal admin via env-fallback credentials."""
    response = client.post(
        "/login",
        data={
            "username": env_auth["username"],
            "password": env_auth["password"],
            "next": "/",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client

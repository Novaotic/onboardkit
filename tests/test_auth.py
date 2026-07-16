"""Unit tests for auth helpers (startup gate + env fallback)."""

from __future__ import annotations

import pytest

from auth import (
    AuthResult,
    authenticate_user,
    auth_uses_env_fallback,
    validate_startup_config,
)


@pytest.mark.parametrize(
    ("app_env", "ldap_host", "should_raise"),
    [
        ("production", "", True),
        ("production", "   ", True),
        ("production", "dc.example.com", False),
        ("development", "", False),
        ("", "", False),  # defaults to development
    ],
)
def test_validate_startup_config(monkeypatch, app_env, ldap_host, should_raise):
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("LDAP_HOST", ldap_host)
    if should_raise:
        with pytest.raises(RuntimeError, match="LDAP_HOST"):
            validate_startup_config()
    else:
        validate_startup_config()


def test_auth_uses_env_fallback(monkeypatch):
    monkeypatch.setenv("LDAP_HOST", "")
    assert auth_uses_env_fallback() is True
    monkeypatch.setenv("LDAP_HOST", "dc.example.com")
    assert auth_uses_env_fallback() is False


def test_env_auth_success(env_auth):
    result = authenticate_user(env_auth["username"], env_auth["password"])
    assert result == AuthResult(
        username="testadmin",
        display_name="testadmin",
        is_admin=True,
    )


def test_env_auth_wrong_password(env_auth):
    assert authenticate_user(env_auth["username"], "nope") is None


def test_env_auth_wrong_username(env_auth):
    assert authenticate_user("other", env_auth["password"]) is None


@pytest.mark.parametrize("blank", ["", None])
def test_env_auth_rejects_blank_inputs(env_auth, blank):
    # authenticate_user treats falsy username/password as failure before env check
    if blank is None:
        assert authenticate_user("", env_auth["password"]) is None
        assert authenticate_user(env_auth["username"], "") is None
    else:
        assert authenticate_user(blank, env_auth["password"]) is None
        assert authenticate_user(env_auth["username"], blank) is None


@pytest.mark.parametrize(
    "bad_password",
    [
        "",
        "change-this-password",
        "changeme",
        "password",
        "admin",
        "your_password",
        "secret",
        "Change-This-Password",  # case-insensitive via strip().lower()
    ],
)
def test_env_auth_rejects_default_passwords(monkeypatch, bad_password):
    monkeypatch.setenv("LDAP_HOST", "")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", bad_password)
    assert authenticate_user("admin", bad_password) is None


def test_env_auth_rejects_unset_credentials(monkeypatch):
    monkeypatch.setenv("LDAP_HOST", "")
    monkeypatch.setenv("ADMIN_USERNAME", "")
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    assert authenticate_user("admin", "anything") is None


def test_ldap_host_routes_to_ldap_authenticate(monkeypatch):
    monkeypatch.setenv("LDAP_HOST", "dc.example.com")
    sentinel = AuthResult(username="jdoe", display_name="jdoe", is_admin=False)

    def fake_ldap(username, password, ldap_host):
        assert username == "jdoe"
        assert password == "secret-pass"
        assert ldap_host == "dc.example.com"
        return sentinel

    monkeypatch.setattr("auth._ldap_authenticate", fake_ldap)
    assert authenticate_user("jdoe", "secret-pass") is sentinel


def test_ldap_authenticate_admin_membership(monkeypatch):
    """Portal Admin membership grants is_admin without requiring Users group."""
    monkeypatch.setenv("LDAP_DOMAIN", "example.com")
    monkeypatch.setenv("LDAP_PORT", "389")
    monkeypatch.setenv("LDAP_BASE_DN", "DC=example,DC=local")
    monkeypatch.setenv("LDAP_USERS_GROUP", "CN=Portal Users,OU=Groups,DC=example,DC=local")
    monkeypatch.setenv("LDAP_ADMIN_GROUP", "CN=Portal Admin,OU=Groups,DC=example,DC=local")

    class FakeConn:
        bound = True

        def unbind(self):
            self.bound = False

    monkeypatch.setattr("auth.Server", lambda *a, **k: object())
    monkeypatch.setattr("auth.Connection", lambda *a, **k: FakeConn())
    monkeypatch.setattr("auth._naming_info_from_ldap3_server", lambda s: {})
    monkeypatch.setattr("auth._read_root_dse", lambda c: {})
    monkeypatch.setattr("auth._read_root_dse_anonymous", lambda *a: {})

    def fake_in_group(server, conn, base_dn, group_dn, *args, **kwargs):
        return "Portal Admin" in group_dn

    monkeypatch.setattr("auth._is_in_group", fake_in_group)

    from auth import _ldap_authenticate

    result = _ldap_authenticate("alice", "pw", "dc.example.com")
    assert result == AuthResult(username="alice", display_name="alice", is_admin=True)


def test_ldap_authenticate_users_only(monkeypatch):
    monkeypatch.setenv("LDAP_DOMAIN", "example.com")
    monkeypatch.setenv("LDAP_PORT", "389")
    monkeypatch.setenv("LDAP_BASE_DN", "DC=example,DC=local")
    monkeypatch.setenv("LDAP_USERS_GROUP", "CN=Portal Users,OU=Groups,DC=example,DC=local")
    monkeypatch.setenv("LDAP_ADMIN_GROUP", "CN=Portal Admin,OU=Groups,DC=example,DC=local")

    class FakeConn:
        bound = True

        def unbind(self):
            self.bound = False

    monkeypatch.setattr("auth.Server", lambda *a, **k: object())
    monkeypatch.setattr("auth.Connection", lambda *a, **k: FakeConn())
    monkeypatch.setattr("auth._naming_info_from_ldap3_server", lambda s: {})
    monkeypatch.setattr("auth._read_root_dse", lambda c: {})
    monkeypatch.setattr("auth._read_root_dse_anonymous", lambda *a: {})

    def fake_in_group(server, conn, base_dn, group_dn, *args, **kwargs):
        return "Portal Users" in group_dn

    monkeypatch.setattr("auth._is_in_group", fake_in_group)

    from auth import _ldap_authenticate

    result = _ldap_authenticate("bob", "pw", "dc.example.com")
    assert result == AuthResult(username="bob", display_name="bob", is_admin=False)


def test_ldap_authenticate_denies_non_member(monkeypatch):
    monkeypatch.setenv("LDAP_DOMAIN", "example.com")
    monkeypatch.setenv("LDAP_PORT", "389")
    monkeypatch.setenv("LDAP_BASE_DN", "DC=example,DC=local")
    monkeypatch.setenv("LDAP_USERS_GROUP", "CN=Portal Users,OU=Groups,DC=example,DC=local")
    monkeypatch.setenv("LDAP_ADMIN_GROUP", "CN=Portal Admin,OU=Groups,DC=example,DC=local")

    class FakeConn:
        bound = True

        def unbind(self):
            self.bound = False

    monkeypatch.setattr("auth.Server", lambda *a, **k: object())
    monkeypatch.setattr("auth.Connection", lambda *a, **k: FakeConn())
    monkeypatch.setattr("auth._naming_info_from_ldap3_server", lambda s: {})
    monkeypatch.setattr("auth._read_root_dse", lambda c: {})
    monkeypatch.setattr("auth._read_root_dse_anonymous", lambda *a: {})
    monkeypatch.setattr("auth._is_in_group", lambda *a, **k: False)

    from auth import _ldap_authenticate

    assert _ldap_authenticate("eve", "pw", "dc.example.com") is None


def test_ldap_authenticate_requires_group_config(monkeypatch):
    monkeypatch.setenv("LDAP_DOMAIN", "")
    monkeypatch.setenv("LDAP_PORT", "389")
    monkeypatch.setenv("LDAP_BASE_DN", "")
    monkeypatch.setenv("LDAP_USERS_GROUP", "")
    monkeypatch.setenv("LDAP_ADMIN_GROUP", "")

    class FakeConn:
        bound = True

        def unbind(self):
            self.bound = False

    monkeypatch.setattr("auth.Server", lambda *a, **k: object())
    monkeypatch.setattr("auth.Connection", lambda *a, **k: FakeConn())

    from auth import _ldap_authenticate

    assert _ldap_authenticate("eve", "pw", "dc.example.com") is None

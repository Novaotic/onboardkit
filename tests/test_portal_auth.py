"""Portal front-gate and requester-mapping HTTP tests."""

from __future__ import annotations

import pytest

from auth import AuthResult
from main import _clear_wizard, _requester_name, _safe_next_url, build_final_json


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "/"),
        ("", "/"),
        ("/step/1", "/step/1"),
        ("/admin/", "/admin/"),
        ("//evil.example", "/"),
        ("https://evil.example", "/"),
        ("step/1", "/"),
    ],
)
def test_safe_next_url(raw, expected):
    assert _safe_next_url(raw) == expected


def test_requester_name_prefers_display_name():
    assert _requester_name({"display_name": "Pat Lee", "username": "plee"}) == "Pat Lee"
    assert _requester_name({"display_name": "", "username": "plee"}) == "plee"


def test_clear_wizard_preserves_auth_session():
    session = {
        "user": {"username": "pat", "is_admin": False},
        "step1": {"first_name": "A"},
        "step2": {"office": "HQ"},
    }
    _clear_wizard(session)
    assert session["user"]["username"] == "pat"
    assert "step1" not in session
    assert "step2" not in session


def test_unauthenticated_portal_redirects_to_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/login")


def test_unauthenticated_step_and_api_redirect(client):
    for path in ("/step/1", "/start", "/api/presets/Analyst"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302, path
        assert "/login" in response.headers["location"], path


def test_login_success_and_home_access(client, env_auth):
    bad = client.post(
        "/login",
        data={"username": env_auth["username"], "password": "wrong", "next": "/"},
        follow_redirects=False,
    )
    assert bad.status_code == 401

    ok = client.post(
        "/login",
        data={
            "username": env_auth["username"],
            "password": env_auth["password"],
            "next": "/",
        },
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert ok.headers["location"] == "/"

    home = client.get("/", follow_redirects=False)
    assert home.status_code == 200
    assert "New Hire" in home.text


def test_admin_access_for_env_fallback_admin(authed_client):
    response = authed_client.get("/admin/", follow_redirects=False)
    assert response.status_code == 200
    assert "Preset" in response.text


def test_non_admin_blocked_from_admin(client, monkeypatch):
    monkeypatch.setattr(
        "main.authenticate_user",
        lambda u, p: AuthResult(username="bob", display_name="Bob", is_admin=False),
    )
    login = client.post(
        "/login",
        data={"username": "bob", "password": "x", "next": "/"},
        follow_redirects=False,
    )
    assert login.status_code == 303

    portal = client.get("/", follow_redirects=False)
    assert portal.status_code == 200

    admin = client.get("/admin/", follow_redirects=False)
    assert admin.status_code == 303
    assert admin.headers["location"] == "/"


def test_requester_locked_to_session_user(authed_client, env_auth):
    forged = authed_client.post(
        "/step/1",
        data={
            "first_name": "New",
            "last_name": "Hire",
            "start_date": "2026-08-01",
            "title": "Other",
            "custom_title": "Intern",
            "manager_name": "Evil Forged Name",
        },
        follow_redirects=False,
    )
    assert forged.status_code == 303
    assert forged.headers["location"] == "/step/2"

    step1 = authed_client.get("/step/1", follow_redirects=False)
    assert step1.status_code == 200
    assert "Evil Forged Name" not in step1.text
    assert env_auth["username"] in step1.text
    assert "readonly" in step1.text.lower()


def test_build_final_json_uses_manager_name_as_requested_by():
    session = {
        "step1": {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "title": "Analyst",
            "manager_name": "testadmin",
            "start_date": "2026-01-01",
        },
        "step2": {"office": "Headquarters", "area": ""},
        "step3": {},
        "step4": {},
        "step5": {},
        "step6": {},
    }
    final = build_final_json(session)
    assert final["requested_by"] == "testadmin"


def test_logout_clears_session(authed_client):
    out = authed_client.post("/logout", follow_redirects=False)
    assert out.status_code == 303
    assert out.headers["location"] == "/login"
    blocked = authed_client.get("/", follow_redirects=False)
    assert blocked.status_code == 302


def test_admin_login_bookmark_redirects(client):
    response = client.get("/admin/login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")

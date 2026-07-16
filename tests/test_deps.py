"""Tests for session gate dependencies."""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi import HTTPException

from deps import get_session_user, require_admin, require_user


def _request(session: dict, path: str = "/step/1", query: str = ""):
    url = SimpleNamespace(path=path, query=query)
    return SimpleNamespace(session=session, url=url)


@pytest.mark.asyncio
async def test_require_user_redirects_when_anonymous():
    request = _request({})
    with pytest.raises(HTTPException) as excinfo:
        await require_user(request)
    assert excinfo.value.status_code == 302
    location = unquote(excinfo.value.headers["Location"])
    assert location.startswith("/login?next=")
    assert "/step/1" in location


@pytest.mark.asyncio
async def test_require_user_returns_session_user():
    user = {"username": "pat", "display_name": "Pat", "is_admin": False}
    result = await require_user(_request({"user": user}))
    assert result == user


@pytest.mark.asyncio
async def test_require_admin_redirects_anonymous_to_login():
    with pytest.raises(HTTPException) as excinfo:
        await require_admin(_request({}, path="/admin/"))
    assert excinfo.value.status_code == 302
    assert "/login" in excinfo.value.headers["Location"]


@pytest.mark.asyncio
async def test_require_admin_sends_non_admin_home():
    user = {"username": "pat", "display_name": "Pat", "is_admin": False}
    with pytest.raises(HTTPException) as excinfo:
        await require_admin(_request({"user": user}, path="/admin/"))
    assert excinfo.value.status_code == 303
    assert excinfo.value.headers["Location"] == "/"


@pytest.mark.asyncio
async def test_require_admin_allows_admin():
    user = {"username": "root", "display_name": "Root", "is_admin": True}
    result = await require_admin(_request({"user": user}, path="/admin/"))
    assert result == user


def test_get_session_user_rejects_invalid_shapes():
    assert get_session_user(_request({})) is None
    assert get_session_user(_request({"user": "pat"})) is None
    assert get_session_user(_request({"user": {}})) is None
    assert get_session_user(_request({"user": {"username": "pat"}}))["username"] == "pat"

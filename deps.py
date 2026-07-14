from urllib.parse import quote

from fastapi import HTTPException, Request


def get_session_user(request: Request) -> dict | None:
    """Return the authenticated user dict from the session, or None."""
    user = request.session.get("user")
    if isinstance(user, dict) and user.get("username"):
        return user
    return None


def _login_redirect(request: Request) -> HTTPException:
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    location = f"/login?next={quote(next_path, safe='')}"
    return HTTPException(status_code=302, headers={"Location": location})


async def require_user(request: Request) -> dict:
    """Gate portal routes — any Portal User or Portal Admin."""
    user = get_session_user(request)
    if not user:
        raise _login_redirect(request)
    return user


async def require_admin(request: Request) -> dict:
    """Gate /admin/* — authenticated Portal Admin only."""
    user = get_session_user(request)
    if not user:
        raise _login_redirect(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=303, headers={"Location": "/"})
    return user

from fastapi import HTTPException, Request


async def require_admin(request: Request) -> bool:
    """Dependency that gates all /admin/* routes.

    Redirects unauthenticated requests to /admin/login.
    Once the user successfully logs in (via LDAP or env-var fallback),
    admin.py sets ``request.session["admin_authenticated"] = True``.
    """
    if not request.session.get("admin_authenticated"):
        raise HTTPException(
            status_code=302,
            headers={"Location": "/admin/login"},
        )
    return True

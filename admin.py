from urllib.parse import unquote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config_store import get_config, get_offices, get_preset_flags, template_context
from deps import get_session_user, require_admin
from paths import TEMPLATES_DIR
from employee_store import list_all_employees, search_employees
from preset_store import get_presets, save_presets
from preset_validation import validate_presets

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _form_options() -> dict:
    ctx = template_context()
    return {
        "hardware_options": ctx["hardware_options"],
        "software_options": ctx["software_options"],
        "portal_options": ctx["portal_options"],
        "mailbox_options": ctx["mailbox_options"],
        "office_options": ctx["offices"],
        "preset_flags": ctx["preset_flags"],
    }


def _parse_preset_form(form) -> dict:
    def _csv_to_list(raw: str) -> list[str]:
        return [v.strip() for v in raw.split(",") if v.strip()]

    hardware = []
    computer_type = form.get("computer_type", "")
    monitors = form.get("monitors", "")
    if computer_type:
        hardware.append(computer_type)
    if monitors:
        hardware.append(monitors)
    hardware += list(form.getlist("peripherals"))

    location_email_groups: dict[str, list[str]] = {}
    for office in get_offices():
        raw = form.get(f"loc_groups_{office}", "")
        groups = _csv_to_list(raw)
        if groups:
            location_email_groups[office] = groups

    preset: dict = {
        "hardware": hardware,
        "software": list(form.getlist("software")),
        "portals": list(form.getlist("portals")),
        "mailboxes": list(form.getlist("mailboxes")),
        "email_groups": _csv_to_list(form.get("email_groups", "")),
        "location_email_groups": location_email_groups,
        "mobile_access": form.get("mobile_access") == "yes",
        "needs_email": form.get("needs_email") == "yes",
        "needs_computer": form.get("needs_computer") == "yes",
    }
    for flag in get_preset_flags():
        flag_id = flag["id"]
        preset[flag_id] = form.get(flag_id) == "yes"
    return preset


def _admin_ctx(request: Request, extra: dict | None = None) -> dict:
    ctx = template_context()
    ctx["user"] = get_session_user(request)
    if extra:
        ctx.update(extra)
    return ctx


@router.get("/login", response_class=HTMLResponse)
async def admin_login_get(request: Request):
    """Keep old bookmark working — portal login is the front gate."""
    return RedirectResponse(url="/login?next=/admin/", status_code=303)


@router.post("/login")
async def admin_login_post(request: Request):
    return RedirectResponse(url="/login?next=/admin/", status_code=303)


@router.post("/logout")
async def admin_logout(request: Request):
    return RedirectResponse(url="/logout", status_code=307)


@router.get("/", response_class=HTMLResponse)
async def admin_index(request: Request, _: dict = Depends(require_admin)):
    presets = get_presets()
    return templates.TemplateResponse(request, "admin/index.html", _admin_ctx(request, {
        "presets": presets,
        "preset_flags": get_preset_flags(),
    }))


@router.get("/employees", response_class=HTMLResponse)
async def admin_employees(request: Request, _: dict = Depends(require_admin)):
    query = (request.query_params.get("q") or "").strip()
    if query:
        employees = search_employees(query, username="", is_admin=True)
    else:
        employees = list_all_employees()
    return templates.TemplateResponse(
        request,
        "admin/employees.html",
        _admin_ctx(request, {"employees": employees, "query": query}),
    )


@router.get("/new", response_class=HTMLResponse)
async def admin_new_get(request: Request, _: dict = Depends(require_admin)):
    return templates.TemplateResponse(request, "admin/preset_form.html", _admin_ctx(request, {
        "preset": {},
        "role_name": "",
        "is_edit": False,
        "error": None,
        **_form_options(),
    }))


@router.post("/new")
async def admin_new_post(request: Request, _: dict = Depends(require_admin)):
    form = await request.form()
    role_name = form.get("role_name", "").strip()
    opts = _form_options()
    parsed = _parse_preset_form(form)

    if not role_name:
        return templates.TemplateResponse(request, "admin/preset_form.html", _admin_ctx(request, {
            "preset": {},
            "role_name": "",
            "is_edit": False,
            "error": "Role name is required.",
            **opts,
        }))

    presets = get_presets()
    if role_name in presets:
        return templates.TemplateResponse(request, "admin/preset_form.html", _admin_ctx(request, {
            "preset": {},
            "role_name": role_name,
            "is_edit": False,
            "error": f'A preset named "{role_name}" already exists. Choose a different name or edit the existing one.',
            **opts,
        }))

    errors = validate_presets({role_name: parsed}, get_config())
    if errors:
        return templates.TemplateResponse(request, "admin/preset_form.html", _admin_ctx(request, {
            "preset": parsed,
            "role_name": role_name,
            "is_edit": False,
            "error": "\n".join(errors),
            **opts,
        }))

    presets[role_name] = parsed
    save_presets(presets)
    return RedirectResponse(url="/admin/", status_code=303)


@router.get("/edit/{role_name}", response_class=HTMLResponse)
async def admin_edit_get(
    role_name: str,
    request: Request,
    _: dict = Depends(require_admin),
):
    role_name = unquote(role_name)
    presets = get_presets()
    preset = presets.get(role_name)
    if preset is None:
        return RedirectResponse(url="/admin/")

    return templates.TemplateResponse(request, "admin/preset_form.html", _admin_ctx(request, {
        "preset": preset,
        "role_name": role_name,
        "is_edit": True,
        "error": None,
        **_form_options(),
    }))


@router.post("/edit/{role_name}")
async def admin_edit_post(
    role_name: str,
    request: Request,
    _: dict = Depends(require_admin),
):
    role_name = unquote(role_name)
    presets = get_presets()
    if role_name not in presets:
        return RedirectResponse(url="/admin/")

    form = await request.form()
    parsed = _parse_preset_form(form)
    errors = validate_presets({role_name: parsed}, get_config())
    if errors:
        return templates.TemplateResponse(request, "admin/preset_form.html", _admin_ctx(request, {
            "preset": parsed,
            "role_name": role_name,
            "is_edit": True,
            "error": "\n".join(errors),
            **_form_options(),
        }))

    presets[role_name] = parsed
    save_presets(presets)
    return RedirectResponse(url="/admin/", status_code=303)


@router.post("/delete/{role_name}")
async def admin_delete(
    role_name: str,
    request: Request,
    _: dict = Depends(require_admin),
):
    role_name = unquote(role_name)
    presets = get_presets()
    presets.pop(role_name, None)
    save_presets(presets)
    return RedirectResponse(url="/admin/", status_code=303)

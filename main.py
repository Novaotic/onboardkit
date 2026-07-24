import logging
import os
import json
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import unquote
from preset_validation import validate_presets
from config_store import get_config

from dotenv import load_dotenv

from paths import ENV_FILE, STATIC_DIR, TEMPLATES_DIR

load_dotenv(ENV_FILE)

_level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
_root_level = getattr(logging, _level_name, logging.INFO)
if not isinstance(_root_level, int):
    _root_level = logging.INFO
logging.basicConfig(level=_root_level)
logging.getLogger("ldap3").setLevel(logging.WARNING)

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from config_store import (
    employee_field_enabled,
    get_option_labels,
    init_config,
    preset_triggers_followup,
    template_context,
)
from preset_store import get_presets, init_store
from employee_store import (
    delete_employee,
    get_employee,
    init_db,
    search_employees,
    upsert_employee,
)
from email_service import send_it_checklist
from admin import router as admin_router
from auth import authenticate_user, auth_uses_env_fallback, validate_startup_config
from deps import get_session_user, require_user

log = logging.getLogger(__name__)

_WIZARD_KEYS = ("step1", "step2", "step3", "step4", "step5", "step6")
_FLOW_KEYS = ("flow", "employee_id")
_VALID_FLOWS = frozenset({"onboard", "transition", "offboard"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        validate_startup_config()
    except RuntimeError as exc:
        log.critical("%s", exc)
        raise SystemExit(1) from exc
    init_config()
    init_store()
    init_db()
    errors = validate_presets(get_presets(), get_config())
    for msg in errors:
        log.warning("Preset validation: %s", msg)
    if auth_uses_env_fallback():
        log.warning(
            "Authentication is using ADMIN_USERNAME / ADMIN_PASSWORD from .env "
            "(LDAP_HOST is not set). This is intended for local development only — "
            "configure LDAP before deploying to production."
        )
    yield


app = FastAPI(title="OnboardKit", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "dev-secret-change-me"),
    max_age=7200,
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.include_router(admin_router)

TOTAL_STEPS = 6


def _ctx(request: Request, extra: dict | None = None) -> dict:
    ctx = template_context()
    ctx["user"] = get_session_user(request)
    if extra:
        ctx.update(extra)
    return ctx


def _clear_wizard(session: dict) -> None:
    """Clear form-wizard data without signing the user out."""
    for key in _WIZARD_KEYS:
        session.pop(key, None)
    for key in _FLOW_KEYS:
        session.pop(key, None)


def _session_flow(session: dict) -> str:
    flow = session.get("flow") or "onboard"
    return flow if flow in _VALID_FLOWS else "onboard"


def _can_access_employee(user: dict, row: dict) -> bool:
    if user.get("is_admin"):
        return True
    return row.get("requested_by_username") == user.get("username")


def _begin_flow(session: dict, flow: str, employee_id: str | None = None) -> None:
    _clear_wizard(session)
    session["flow"] = flow if flow in _VALID_FLOWS else "onboard"
    if employee_id:
        session["employee_id"] = employee_id


def apply_payload_to_session(session: dict, payload: dict) -> None:
    """Map a stored checklist payload back into wizard session keys."""
    emp = payload.get("employee") or {}
    loc = payload.get("location") or {}
    hw = payload.get("hardware") or {}
    acc = payload.get("access") or {}
    eg = payload.get("email_groups") or {}
    sec = payload.get("security") or {}

    title = (emp.get("title") or "").strip()
    if title and title in get_presets():
        step_title, custom_title = title, ""
    else:
        step_title, custom_title = ("Other", title) if title else ("", "")

    step1 = {
        "first_name": emp.get("first_name", ""),
        "middle_name": emp.get("middle_name", ""),
        "last_name": emp.get("last_name", ""),
        "preferred_name": emp.get("preferred_name", ""),
        "credentials": emp.get("credentials", ""),
        "start_date": emp.get("start_date", ""),
        "title": step_title,
        "custom_title": custom_title,
        "manager_name": payload.get("requested_by", ""),
    }
    if employee_field_enabled("student_or_resident"):
        step1["is_student_or_resident"] = bool(emp.get("is_student_or_resident"))
    if employee_field_enabled("bilingual"):
        step1["is_bilingual"] = bool(emp.get("is_bilingual"))

    session["step1"] = step1
    session["step2"] = {
        "office": loc.get("office", ""),
        "area": loc.get("area", ""),
    }
    session["step3"] = {
        "needs_computer": bool(hw.get("needs_computer")),
        "computer_type": hw.get("computer_type", ""),
        "monitors": hw.get("monitors", ""),
        "peripherals": list(hw.get("peripherals") or []),
    }
    session["step4"] = {
        "needs_email": bool(acc.get("needs_email")),
        "portals": list(acc.get("portals") or []),
        "software": list(acc.get("software") or []),
        "other_software": acc.get("other_software", ""),
        "mobile_access": bool(acc.get("mobile_access")),
        "network_printers": acc.get("network_printers", ""),
    }
    step5 = {
        "email_groups": eg.get("groups", ""),
        "mailboxes": list(eg.get("mailboxes") or []),
        "fax_numbers": eg.get("fax_numbers", ""),
    }
    if "role_followup" in eg:
        step5["role_followup"] = eg.get("role_followup")
    session["step5"] = step5
    session["step6"] = {
        "alarm_code": bool(sec.get("alarm_code")),
        "alarm_facilities": list(sec.get("alarm_facilities") or []),
        "gate_access": bool(sec.get("gate_access")),
    }


def _safe_next_url(raw: str | None) -> str:
    """Allow only same-site relative paths as post-login redirects."""
    if not raw:
        return "/"
    path = unquote(raw).strip()
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return path


def _requester_name(user: dict) -> str:
    return (user.get("display_name") or user.get("username") or "").strip()


def _get_preset(session: dict) -> dict:
    title = session.get("step1", {}).get("title", "")
    return get_presets().get(title, {})


def _step3_defaults(preset: dict) -> dict:
    hardware = preset.get("hardware", [])
    computer_types = [h for h in hardware if h in ("desktop", "laptop")]
    monitor_types = [h for h in hardware if h in ("single_monitor", "dual_monitor")]
    peripherals = [
        h for h in hardware
        if h not in ("desktop", "laptop", "single_monitor", "dual_monitor")
    ]
    return {
        "needs_computer": preset.get("needs_computer", bool(hardware)),
        "computer_type": computer_types[0] if computer_types else "",
        "monitors": monitor_types[0] if monitor_types else "",
        "peripherals": peripherals,
    }


def _step4_defaults(preset: dict) -> dict:
    return {
        "needs_email": preset.get("needs_email", False),
        "portals": preset.get("portals", []),
        "software": preset.get("software", []),
        "other_software": "",
        "mobile_access": preset.get("mobile_access", False),
        "network_printers": "",
    }


def _step5_defaults(preset: dict, location: str = "") -> dict:
    groups = list(preset.get("email_groups", []))
    loc_rules = preset.get("location_email_groups", {})
    if location and location in loc_rules:
        groups += loc_rules[location]
    return {
        "email_groups": ", ".join(groups) if groups else "",
        "mailboxes": preset.get("mailboxes", []),
        "role_followup": None,
        "fax_numbers": "",
    }


def build_final_json(session: dict) -> dict:
    s1 = session.get("step1", {})
    s2 = session.get("step2", {})
    s3 = session.get("step3", {})
    s4 = session.get("step4", {})
    s5 = session.get("step5", {})
    s6 = session.get("step6", {})

    employee = {
        "first_name": s1.get("first_name", ""),
        "middle_name": s1.get("middle_name", ""),
        "last_name": s1.get("last_name", ""),
        "preferred_name": s1.get("preferred_name", ""),
        "credentials": s1.get("credentials", ""),
        "title": s1.get("custom_title") or s1.get("title", ""),
        "start_date": s1.get("start_date", ""),
    }
    if employee_field_enabled("student_or_resident"):
        employee["is_student_or_resident"] = s1.get("is_student_or_resident", False)
    if employee_field_enabled("bilingual"):
        employee["is_bilingual"] = s1.get("is_bilingual", False)

    email_groups = {
        "groups": s5.get("email_groups", ""),
        "mailboxes": s5.get("mailboxes", []),
        "fax_numbers": s5.get("fax_numbers", ""),
    }
    if s5.get("role_followup") is not None:
        email_groups["role_followup"] = s5.get("role_followup")

    return {
        "submitted_at": datetime.now().isoformat(),
        "requested_by": s1.get("manager_name", ""),
        "employee": employee,
        "location": {
            "office": s2.get("office", ""),
            "area": s2.get("area", ""),
        },
        "hardware": {
            "needs_computer": s3.get("needs_computer", False),
            "computer_type": s3.get("computer_type", ""),
            "monitors": s3.get("monitors", ""),
            "peripherals": s3.get("peripherals", []),
        },
        "access": {
            "needs_email": s4.get("needs_email", False),
            "portals": s4.get("portals", []),
            "software": s4.get("software", []),
            "other_software": s4.get("other_software", ""),
            "mobile_access": s4.get("mobile_access", False),
            "network_printers": s4.get("network_printers", ""),
        },
        "email_groups": email_groups,
        "security": {
            "alarm_code": s6.get("alarm_code", False),
            "alarm_facilities": s6.get("alarm_facilities", []),
            "gate_access": s6.get("gate_access", False),
        },
    }


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if get_session_user(request):
        return RedirectResponse(url=_safe_next_url(request.query_params.get("next")), status_code=303)
    return templates.TemplateResponse(request, "login.html", _ctx(request, {
        "error": None,
        "env_auth_mode": auth_uses_env_fallback(),
        "next_url": _safe_next_url(request.query_params.get("next")),
    }))


@app.post("/login")
async def login_post(request: Request):
    form = await request.form()
    username = form.get("username", "").strip()
    password = form.get("password", "")
    next_url = _safe_next_url(form.get("next"))

    result = authenticate_user(username, password)
    if result:
        request.session["user"] = {
            "username": result.username,
            "display_name": result.display_name,
            "is_admin": result.is_admin,
        }
        return RedirectResponse(url=next_url, status_code=303)

    return templates.TemplateResponse(
        request,
        "login.html",
        _ctx(request, {
            "error": "Invalid username or password, or you are not allowed to use this portal.",
            "env_auth_mode": auth_uses_env_fallback(),
            "next_url": next_url,
        }),
        status_code=401,
    )


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, user: dict = Depends(require_user)):
    return templates.TemplateResponse(request, "home.html", _ctx(request))


@app.get("/start")
async def start_new_request(request: Request, user: dict = Depends(require_user)):
    _begin_flow(request.session, "onboard")
    return RedirectResponse(url="/step/1", status_code=303)


def _search_page(
    request: Request,
    user: dict,
    *,
    flow: str,
    query: str = "",
    error: str | None = None,
):
    results = []
    if query.strip():
        results = search_employees(
            query,
            username=user.get("username", ""),
            is_admin=bool(user.get("is_admin")),
        )
    titles = {
        "transition": "Role Transition",
        "offboard": "Offboarding",
    }
    leads = {
        "transition": (
            "Search employees you previously submitted. Admins can search everyone. "
            "No match? Start a blank form and use a role preset to fill defaults."
        ),
        "offboard": (
            "Search employees you previously submitted, then confirm what to revoke. "
            "Admins can search everyone. Offboarding requires an existing inventory "
            "record — use Transition first if they are not in the system yet."
        ),
    }
    return templates.TemplateResponse(request, "employee_search.html", _ctx(request, {
        "flow": flow,
        "page_title": titles.get(flow, flow.title()),
        "page_lead": leads.get(flow, ""),
        "query": query,
        "results": results,
        "error": error,
        "blank_url": f"/{flow}/blank",
        "allow_blank": flow != "offboard",
    }))


@app.get("/transition", response_class=HTMLResponse)
async def transition_get(request: Request, user: dict = Depends(require_user)):
    return _search_page(
        request, user, flow="transition", query=request.query_params.get("q", ""),
    )


@app.get("/offboard", response_class=HTMLResponse)
async def offboard_get(request: Request, user: dict = Depends(require_user)):
    return _search_page(
        request, user, flow="offboard", query=request.query_params.get("q", ""),
    )


@app.get("/transition/blank")
async def transition_blank(request: Request, user: dict = Depends(require_user)):
    _begin_flow(request.session, "transition")
    return RedirectResponse(url="/step/1", status_code=303)


@app.get("/offboard/blank")
async def offboard_blank(request: Request, user: dict = Depends(require_user)):
    """Blank offboard is not allowed — inventory record required for deletion."""
    return _search_page(
        request,
        user,
        flow="offboard",
        query="",
        error=(
            "Offboarding requires an existing employee record. Search for the "
            "employee above, or use Role Transition to add them to inventory first."
        ),
    )


@app.post("/requests/{employee_id}/load")
async def load_employee_request(
    employee_id: str,
    request: Request,
    user: dict = Depends(require_user),
):
    form = await request.form()
    flow = form.get("flow", "transition")
    if flow not in ("transition", "offboard"):
        flow = "transition"

    row = get_employee(employee_id)
    if row is None or not _can_access_employee(user, row):
        return _search_page(
            request,
            user,
            flow=flow,
            query="",
            error="Employee not found, or you do not have access to that record.",
        )

    _begin_flow(request.session, flow, employee_id=employee_id)
    apply_payload_to_session(request.session, row.get("payload") or {})
    step1 = dict(request.session.get("step1") or {})
    step1["manager_name"] = _requester_name(user)
    request.session["step1"] = step1
    return RedirectResponse(url="/step/1", status_code=303)


@app.get("/step/1", response_class=HTMLResponse)
async def step1_get(request: Request, user: dict = Depends(require_user)):
    if not request.session.get("flow"):
        request.session["flow"] = "onboard"
    data = dict(request.session.get("step1") or {})
    data["manager_name"] = _requester_name(user)
    return templates.TemplateResponse(request, "step1_info.html", _ctx(request, {
        "roles": list(get_presets().keys()),
        "data": data,
        "flow": _session_flow(request.session),
        "current_step": 1,
        "total_steps": TOTAL_STEPS,
    }))


@app.post("/step/1")
async def step1_post(request: Request, user: dict = Depends(require_user)):
    form = await request.form()
    title = form.get("title", "")
    custom_title = form.get("custom_title", "").strip()
    step1 = {
        "first_name": form.get("first_name", "").strip(),
        "middle_name": form.get("middle_name", "").strip(),
        "last_name": form.get("last_name", "").strip(),
        "preferred_name": form.get("preferred_name", "").strip(),
        "credentials": form.get("credentials", "").strip(),
        "start_date": form.get("start_date", ""),
        "title": title,
        "custom_title": custom_title,
        # Always trust the session identity — never the form value.
        "manager_name": _requester_name(user),
    }
    if employee_field_enabled("student_or_resident"):
        step1["is_student_or_resident"] = form.get("is_student_or_resident") == "yes"
    if employee_field_enabled("bilingual"):
        step1["is_bilingual"] = form.get("is_bilingual") == "yes"
    request.session["step1"] = step1
    for step in ("step3", "step4", "step5"):
        request.session.pop(step, None)
    return RedirectResponse(url="/step/2", status_code=303)


@app.get("/step/2", response_class=HTMLResponse)
async def step2_get(request: Request, user: dict = Depends(require_user)):
    if not request.session.get("step1"):
        return RedirectResponse(url="/step/1")
    return templates.TemplateResponse(request, "step2_location.html", _ctx(request, {
        "data": request.session.get("step2", {}),
        "current_step": 2,
        "total_steps": TOTAL_STEPS,
    }))


@app.post("/step/2")
async def step2_post(request: Request, user: dict = Depends(require_user)):
    form = await request.form()
    request.session["step2"] = {
        "office": form.get("office", ""),
        "area": form.get("area", "").strip(),
    }
    request.session.pop("step5", None)
    return RedirectResponse(url="/step/3", status_code=303)


@app.get("/step/3", response_class=HTMLResponse)
async def step3_get(request: Request, user: dict = Depends(require_user)):
    if not request.session.get("step1"):
        return RedirectResponse(url="/step/1")
    preset = _get_preset(request.session)
    data = request.session.get("step3") or _step3_defaults(preset)
    return templates.TemplateResponse(request, "step3_hardware.html", _ctx(request, {
        "data": data,
        "preset_hardware": preset.get("hardware", []),
        "current_step": 3,
        "total_steps": TOTAL_STEPS,
    }))


@app.post("/step/3")
async def step3_post(request: Request, user: dict = Depends(require_user)):
    form = await request.form()
    request.session["step3"] = {
        "needs_computer": form.get("needs_computer") == "yes",
        "computer_type": form.get("computer_type", ""),
        "monitors": form.get("monitors", ""),
        "peripherals": list(form.getlist("peripherals")),
    }
    return RedirectResponse(url="/step/4", status_code=303)


@app.get("/step/4", response_class=HTMLResponse)
async def step4_get(request: Request, user: dict = Depends(require_user)):
    if not request.session.get("step1"):
        return RedirectResponse(url="/step/1")
    preset = _get_preset(request.session)
    data = request.session.get("step4") or _step4_defaults(preset)
    return templates.TemplateResponse(request, "step4_software.html", _ctx(request, {
        "data": data,
        "preset_portals": preset.get("portals", []),
        "preset_software": preset.get("software", []),
        "current_step": 4,
        "total_steps": TOTAL_STEPS,
    }))


@app.post("/step/4")
async def step4_post(request: Request, user: dict = Depends(require_user)):
    form = await request.form()
    request.session["step4"] = {
        "needs_email": form.get("needs_email") == "yes",
        "portals": list(form.getlist("portals")),
        "software": list(form.getlist("software")),
        "other_software": form.get("other_software", "").strip(),
        "mobile_access": form.get("mobile_access") == "yes",
        "network_printers": form.get("network_printers", "").strip(),
    }
    return RedirectResponse(url="/step/5", status_code=303)


@app.get("/step/5", response_class=HTMLResponse)
async def step5_get(request: Request, user: dict = Depends(require_user)):
    if not request.session.get("step1"):
        return RedirectResponse(url="/step/1")
    preset = _get_preset(request.session)
    location = request.session.get("step2", {}).get("office", "")
    data = request.session.get("step5") or _step5_defaults(preset, location)
    return templates.TemplateResponse(request, "step5_groups.html", _ctx(request, {
        "data": data,
        "preset_mailboxes": preset.get("mailboxes", []),
        "show_role_followup": preset_triggers_followup(preset),
        "current_step": 5,
        "total_steps": TOTAL_STEPS,
    }))


@app.post("/step/5")
async def step5_post(request: Request, user: dict = Depends(require_user)):
    form = await request.form()
    followup_val = form.get("role_followup")
    step5 = {
        "email_groups": form.get("email_groups", "").strip(),
        "mailboxes": list(form.getlist("mailboxes")),
        "fax_numbers": form.get("fax_numbers", "").strip(),
    }
    if followup_val in ("yes", "no"):
        step5["role_followup"] = followup_val == "yes"
    request.session["step5"] = step5
    return RedirectResponse(url="/step/6", status_code=303)


@app.get("/step/6", response_class=HTMLResponse)
async def step6_get(request: Request, user: dict = Depends(require_user)):
    if not request.session.get("step1"):
        return RedirectResponse(url="/step/1")
    return templates.TemplateResponse(request, "step6_security.html", _ctx(request, {
        "data": request.session.get("step6", {}),
        "current_step": 6,
        "total_steps": TOTAL_STEPS,
    }))


@app.post("/step/6")
async def step6_post(request: Request, user: dict = Depends(require_user)):
    form = await request.form()
    request.session["step6"] = {
        "alarm_code": form.get("alarm_code") == "yes",
        "alarm_facilities": list(form.getlist("alarm_facilities")),
        "gate_access": form.get("gate_access") == "yes",
    }
    return RedirectResponse(url="/confirmation", status_code=303)


@app.get("/confirmation", response_class=HTMLResponse)
async def confirmation_get(request: Request, user: dict = Depends(require_user)):
    if not request.session.get("step1"):
        return RedirectResponse(url="/step/1")
    if _session_flow(request.session) == "offboard" and not request.session.get("employee_id"):
        return RedirectResponse(url="/offboard", status_code=303)
    final = build_final_json(request.session)
    return templates.TemplateResponse(request, "confirmation.html", _ctx(request, {
        "json_data": json.dumps(final, indent=2),
        "final": final,
        "software_labels": get_option_labels("software"),
        "portal_labels": get_option_labels("portals"),
        "mailbox_labels": get_option_labels("mailboxes"),
        "flow": _session_flow(request.session),
        "current_step": 7,
        "total_steps": TOTAL_STEPS,
    }))


@app.post("/submit")
async def submit_form(request: Request, user: dict = Depends(require_user)):
    if not request.session.get("step1"):
        return RedirectResponse(url="/step/1")
    flow = _session_flow(request.session)
    employee_id = request.session.get("employee_id")
    final = build_final_json(request.session)
    employee_name = f"{final['employee']['first_name']} {final['employee']['last_name']}"

    if flow == "offboard" and not employee_id:
        return templates.TemplateResponse(request, "submitted.html", _ctx(request, {
            "success": False,
            "email_sent": False,
            "blocked": True,
            "error": (
                "Offboarding requires an existing inventory record. Go back to "
                "Offboarding, search for the employee, and use that record."
            ),
            "employee_name": employee_name,
            "flow": flow,
            "removed_from_inventory": False,
        }))

    email_ok, error = send_it_checklist(final, flow=flow)
    persisted = False
    removed_from_inventory = False
    if email_ok:
        try:
            if flow == "offboard":
                row = get_employee(employee_id)
                if row is None or not _can_access_employee(user, row):
                    raise RuntimeError("Employee record missing or access denied")
                if not delete_employee(employee_id):
                    raise RuntimeError("Employee record was not deleted")
                removed_from_inventory = True
                persisted = True
            else:
                upsert_employee(
                    username=user.get("username", ""),
                    display=_requester_name(user),
                    payload=final,
                    employee_id=employee_id,
                )
                persisted = True
            _clear_wizard(request.session)
        except Exception:
            log.exception(
                "Email sent but failed to update employee inventory for %s (%s)",
                employee_name,
                flow,
            )
            if flow == "offboard":
                error = (
                    "The offboarding email was sent, but removing this employee from "
                    "inventory failed. Your form answers are still in this session — "
                    "try again from confirmation."
                )
            else:
                error = (
                    "The IT checklist email was sent, but saving this employee to "
                    "inventory failed. Your form answers are still in this session — "
                    "try again from confirmation, or contact IT if this keeps happening."
                )
    success = email_ok and persisted
    return templates.TemplateResponse(request, "submitted.html", _ctx(request, {
        "success": success,
        "email_sent": email_ok,
        "blocked": False,
        "error": error,
        "employee_name": employee_name,
        "flow": flow,
        "removed_from_inventory": removed_from_inventory,
    }))


@app.get("/api/presets/{role}")
async def api_presets(role: str, user: dict = Depends(require_user)):
    return get_presets().get(role, {})

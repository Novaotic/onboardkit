"""Home hub, search ACL, load-by-id, and admin employees roster."""

from __future__ import annotations

from auth import AuthResult
from employee_store import init_db, upsert_employee
from main import apply_payload_to_session, build_final_json


def _seed_employee(*, username: str, first: str, last: str, title: str = "Analyst") -> str:
    init_db()
    payload = {
        "submitted_at": "2026-01-01T00:00:00",
        "requested_by": username,
        "employee": {
            "first_name": first,
            "middle_name": "",
            "last_name": last,
            "preferred_name": "",
            "credentials": "",
            "title": title,
            "start_date": "2026-02-01",
        },
        "location": {"office": "HQ", "area": "Ops"},
        "hardware": {
            "needs_computer": True,
            "computer_type": "laptop",
            "monitors": "single_monitor",
            "peripherals": [],
        },
        "access": {
            "needs_email": True,
            "portals": [],
            "software": [],
            "other_software": "",
            "mobile_access": False,
            "network_printers": "",
        },
        "email_groups": {"groups": "team@example.com", "mailboxes": [], "fax_numbers": ""},
        "security": {"alarm_code": False, "alarm_facilities": [], "gate_access": False},
    }
    return upsert_employee(username=username, display=username, payload=payload)


def test_home_hub_has_three_ctas(authed_client):
    home = authed_client.get("/", follow_redirects=False)
    assert home.status_code == 200
    assert 'href="/start"' in home.text
    assert 'href="/transition"' in home.text
    assert 'href="/offboard"' in home.text


def test_start_sets_onboard_flow(authed_client):
    authed_client.get("/start", follow_redirects=False)
    step1 = authed_client.get("/step/1", follow_redirects=False)
    assert step1.status_code == 200


def test_transition_search_need_to_know(client, monkeypatch):
    mine = _seed_employee(username="alice", first="Pat", last="Mine")
    _seed_employee(username="bob", first="Pat", last="Theirs")

    monkeypatch.setattr(
        "main.authenticate_user",
        lambda u, p: AuthResult(username="alice", display_name="Alice", is_admin=False),
    )
    client.post(
        "/login",
        data={"username": "alice", "password": "x", "next": "/"},
        follow_redirects=False,
    )

    page = client.get("/transition?q=pat", follow_redirects=False)
    assert page.status_code == 200
    assert "Pat Mine" in page.text
    assert "Pat Theirs" not in page.text
    assert mine in page.text


def test_non_admin_cannot_load_others_employee(client, monkeypatch):
    other_id = _seed_employee(username="owner", first="Sam", last="Other")

    monkeypatch.setattr(
        "main.authenticate_user",
        lambda u, p: AuthResult(username="intruder", display_name="Intruder", is_admin=False),
    )
    client.post(
        "/login",
        data={"username": "intruder", "password": "x", "next": "/"},
        follow_redirects=False,
    )

    denied = client.post(
        f"/requests/{other_id}/load",
        data={"flow": "transition"},
        follow_redirects=False,
    )
    assert denied.status_code == 200
    assert "do not have access" in denied.text.lower() or "not found" in denied.text.lower()

    step1 = client.get("/step/1", follow_redirects=False)
    # Should not have loaded Sam into wizard
    assert "Sam" not in step1.text


def test_admin_can_load_any_employee(authed_client):
    row_id = _seed_employee(username="someone_else", first="Riley", last="AdminLoad")
    loaded = authed_client.post(
        f"/requests/{row_id}/load",
        data={"flow": "offboard"},
        follow_redirects=False,
    )
    assert loaded.status_code == 303
    assert loaded.headers["location"] == "/step/1"

    step1 = authed_client.get("/step/1", follow_redirects=False)
    assert step1.status_code == 200
    assert "Riley" in step1.text
    assert "AdminLoad" in step1.text


def test_apply_payload_round_trip():
    session = {}
    payload = {
        "requested_by": "mgr",
        "employee": {
            "first_name": "Ada",
            "middle_name": "",
            "last_name": "Lovelace",
            "preferred_name": "",
            "credentials": "PhD",
            "title": "Custom Role",
            "start_date": "2026-05-01",
        },
        "location": {"office": "East", "area": "Lab"},
        "hardware": {
            "needs_computer": True,
            "computer_type": "desktop",
            "monitors": "dual_monitor",
            "peripherals": ["dock"],
        },
        "access": {
            "needs_email": True,
            "portals": ["ehr"],
            "software": ["office"],
            "other_software": "Visio",
            "mobile_access": True,
            "network_printers": "Floor 2",
        },
        "email_groups": {
            "groups": "a@x.com",
            "mailboxes": ["shared_front"],
            "fax_numbers": "555",
            "role_followup": True,
        },
        "security": {
            "alarm_code": True,
            "alarm_facilities": ["main"],
            "gate_access": False,
        },
    }
    apply_payload_to_session(session, payload)
    rebuilt = build_final_json(session)
    assert rebuilt["employee"]["first_name"] == "Ada"
    assert rebuilt["employee"]["title"] == "Custom Role"
    assert rebuilt["hardware"]["computer_type"] == "desktop"
    assert rebuilt["access"]["other_software"] == "Visio"
    assert rebuilt["email_groups"]["role_followup"] is True


def test_admin_employees_roster(authed_client):
    _seed_employee(username="mgr1", first="Global", last="View")
    page = authed_client.get("/admin/employees", follow_redirects=False)
    assert page.status_code == 200
    assert "Global View" in page.text
    assert "mgr1" in page.text


def test_non_admin_blocked_from_employees_roster(client, monkeypatch):
    monkeypatch.setattr(
        "main.authenticate_user",
        lambda u, p: AuthResult(username="bob", display_name="Bob", is_admin=False),
    )
    client.post(
        "/login",
        data={"username": "bob", "password": "x", "next": "/"},
        follow_redirects=False,
    )
    blocked = client.get("/admin/employees", follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/"


def test_blank_transition_sets_flow(authed_client):
    resp = authed_client.get("/transition/blank", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/step/1"


def test_offboard_blank_rejected(authed_client):
    resp = authed_client.get("/offboard/blank", follow_redirects=False)
    assert resp.status_code == 200
    assert "requires an existing" in resp.text.lower()
    assert 'href="/offboard/blank"' not in resp.text

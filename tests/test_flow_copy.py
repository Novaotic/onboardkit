"""Flow-aware email HTML and confirmation copy."""

from __future__ import annotations

from email_service import build_html_email


def _sample_payload() -> dict:
    return {
        "submitted_at": "2026-07-01T12:00:00",
        "requested_by": "manager1",
        "employee": {
            "first_name": "Jamie",
            "last_name": "Lee",
            "title": "Analyst",
            "start_date": "2026-08-01",
        },
        "location": {"office": "HQ", "area": ""},
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
        "email_groups": {"groups": "", "mailboxes": [], "fax_numbers": ""},
        "security": {"alarm_code": False, "alarm_facilities": [], "gate_access": False},
    }


def test_offboard_email_uses_revoke_framing():
    html = build_html_email(_sample_payload(), flow="offboard")
    assert "Offboarding IT Request" in html
    assert "Hardware to Collect / Return" in html
    assert "Access to Revoke" in html
    assert "Action required" in html
    assert "Last Day / Effective" in html


def test_transition_email_uses_target_framing():
    html = build_html_email(_sample_payload(), flow="transition")
    assert "Role Transition IT Request" in html
    assert "Target Hardware" in html
    assert "Target Access" in html
    assert "Target state" in html
    assert "Effective Date" in html


def test_onboard_email_keeps_default_framing():
    html = build_html_email(_sample_payload(), flow="onboard")
    assert "Hardware to Collect" not in html
    assert "Target Hardware" not in html
    assert ">Hardware<" in html or "Hardware</p>" in html
    assert "Start Date" in html


def test_confirmation_offboard_copy(authed_client):
    from employee_store import init_db, upsert_employee

    init_db()
    row_id = upsert_employee(
        username="testadmin",
        display="testadmin",
        payload=_sample_payload(),
    )
    authed_client.post(
        f"/requests/{row_id}/load",
        data={"flow": "offboard"},
        follow_redirects=False,
    )
    page = authed_client.get("/confirmation", follow_redirects=False)
    assert page.status_code == 200
    assert "Review Offboarding" in page.text
    assert "revoke / return" in page.text.lower() or "Revoke" in page.text
    assert "Send offboarding" in page.text


def test_confirmation_transition_copy(authed_client):
    authed_client.get("/transition/blank", follow_redirects=False)
    # Minimal session via step posts so confirmation works
    authed_client.post(
        "/step/1",
        data={
            "first_name": "Jamie",
            "last_name": "Lee",
            "start_date": "2026-08-01",
            "title": "Other",
            "custom_title": "Analyst",
        },
        follow_redirects=False,
    )
    for path, data in (
        ("/step/2", {"office": "HQ", "area": ""}),
        ("/step/3", {"needs_computer": "no"}),
        ("/step/4", {"needs_email": "yes"}),
        ("/step/5", {"email_groups": "", "fax_numbers": ""}),
        ("/step/6", {"alarm_code": "no", "gate_access": "no"}),
    ):
        authed_client.post(path, data=data, follow_redirects=False)

    page = authed_client.get("/confirmation", follow_redirects=False)
    assert page.status_code == 200
    assert "Review Transition" in page.text
    assert "target" in page.text.lower()
    assert "Send transition" in page.text


def test_transition_submit_upserts(authed_client, monkeypatch):
    from employee_store import get_employee, init_db, list_all_employees, upsert_employee

    monkeypatch.setattr(
        "main.send_it_checklist",
        lambda _final, **_kwargs: (True, None),
    )
    init_db()
    row_id = upsert_employee(
        username="old_owner",
        display="old_owner",
        payload=_sample_payload(),
    )
    # Admin authed_client loads and transitions — ownership flips
    authed_client.post(
        f"/requests/{row_id}/load",
        data={"flow": "transition"},
        follow_redirects=False,
    )
    authed_client.post(
        "/step/1",
        data={
            "first_name": "Jamie",
            "last_name": "Lee",
            "start_date": "2026-09-01",
            "title": "Other",
            "custom_title": "Senior Analyst",
        },
        follow_redirects=False,
    )
    response = authed_client.post("/submit", follow_redirects=False)
    assert response.status_code == 200
    assert "transition" in response.text.lower()
    row = get_employee(row_id)
    assert row is not None
    assert row["requested_by_username"] == "testadmin"
    assert row["employee_title"] == "Senior Analyst"
    assert len(list_all_employees()) == 1

"""Submit path: email + inventory persistence + failed-submit wizard keep."""

from __future__ import annotations

from employee_store import list_all_employees, search_employees


def _fill_minimal_wizard(client) -> None:
    client.get("/start", follow_redirects=False)
    client.post(
        "/step/1",
        data={
            "first_name": "Casey",
            "middle_name": "",
            "last_name": "Nguyen",
            "preferred_name": "",
            "credentials": "",
            "start_date": "2026-03-01",
            "title": "Other",
            "custom_title": "Analyst",
        },
        follow_redirects=False,
    )
    client.post(
        "/step/2",
        data={"office": "Main Office", "area": "Ops"},
        follow_redirects=False,
    )
    client.post(
        "/step/3",
        data={
            "needs_computer": "yes",
            "computer_type": "laptop",
            "monitors": "single_monitor",
        },
        follow_redirects=False,
    )
    client.post(
        "/step/4",
        data={"needs_email": "yes"},
        follow_redirects=False,
    )
    client.post(
        "/step/5",
        data={"email_groups": "team@example.com", "fax_numbers": ""},
        follow_redirects=False,
    )
    client.post(
        "/step/6",
        data={"alarm_code": "no", "gate_access": "no"},
        follow_redirects=False,
    )


def test_successful_submit_persists_and_clears_wizard(authed_client, monkeypatch):
    monkeypatch.setattr(
        "main.send_it_checklist",
        lambda _final: (True, None),
    )
    _fill_minimal_wizard(authed_client)

    response = authed_client.post("/submit", follow_redirects=False)
    assert response.status_code == 200
    assert "Casey" in response.text

    rows = list_all_employees()
    assert len(rows) == 1
    assert rows[0]["employee_first"] == "Casey"
    assert rows[0]["requested_by_username"] == "testadmin"

    # Wizard cleared — confirmation should bounce to step 1
    confirm = authed_client.get("/confirmation", follow_redirects=False)
    assert confirm.status_code in (302, 303, 307)
    assert "/step/1" in confirm.headers["location"]


def test_failed_submit_keeps_wizard_and_skips_persist(authed_client, monkeypatch):
    monkeypatch.setattr(
        "main.send_it_checklist",
        lambda _final: (False, "SMTP unavailable"),
    )
    _fill_minimal_wizard(authed_client)

    response = authed_client.post("/submit", follow_redirects=False)
    assert response.status_code == 200
    assert "SMTP unavailable" in response.text or "success" in response.text.lower()

    assert list_all_employees() == []

    confirm = authed_client.get("/confirmation", follow_redirects=False)
    assert confirm.status_code == 200
    assert "Casey" in confirm.text


def test_second_onboard_same_name_upserts_for_owner(authed_client, monkeypatch):
    monkeypatch.setattr(
        "main.send_it_checklist",
        lambda _final: (True, None),
    )
    _fill_minimal_wizard(authed_client)
    authed_client.post("/submit", follow_redirects=False)

    _fill_minimal_wizard(authed_client)
    # Change title via step 1 again after start cleared — re-fill already done
    # Resubmit same person; should still be one row
    authed_client.post("/submit", follow_redirects=False)

    hits = search_employees("casey", username="testadmin", is_admin=False)
    assert len(hits) == 1
    assert len(list_all_employees()) == 1

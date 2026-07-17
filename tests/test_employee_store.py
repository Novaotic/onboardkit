"""Unit tests for the SQLite employee inventory."""

from __future__ import annotations

import paths
from employee_store import (
    delete_employee,
    get_employee,
    init_db,
    list_all_employees,
    search_employees,
    upsert_employee,
)


def _payload(first: str = "Jane", last: str = "Doe", title: str = "Nurse") -> dict:
    return {
        "submitted_at": "2026-01-01T00:00:00",
        "requested_by": "manager1",
        "employee": {
            "first_name": first,
            "middle_name": "",
            "last_name": last,
            "preferred_name": "Janie",
            "credentials": "",
            "title": title,
            "start_date": "2026-02-01",
        },
        "location": {"office": "Main", "area": ""},
        "hardware": {},
        "access": {},
        "email_groups": {},
        "security": {},
    }


def test_init_db_creates_wal_capable_directory(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "DB_FILE", data_dir / "onboardkit.db")
    init_db()
    assert data_dir.is_dir()
    assert (data_dir / "onboardkit.db").is_file()
    # Writing should be allowed to create WAL sidecars in the same directory
    row_id = upsert_employee(
        username="mgr",
        display="mgr",
        payload=_payload(),
    )
    assert get_employee(row_id) is not None


def test_upsert_insert_then_update_same_owner_name():
    init_db()
    first_id = upsert_employee(
        username="mgr_a",
        display="Manager A",
        payload=_payload("Alex", "River"),
    )
    second_id = upsert_employee(
        username="mgr_a",
        display="Manager A",
        payload=_payload("Alex", "River", title="Lead Nurse"),
    )
    assert first_id == second_id
    row = get_employee(first_id)
    assert row is not None
    assert row["employee_title"] == "Lead Nurse"
    assert row["requested_by_username"] == "mgr_a"
    assert len(list_all_employees()) == 1


def test_upsert_flips_owner_when_admin_updates_by_id():
    init_db()
    row_id = upsert_employee(
        username="mgr_a",
        display="Manager A",
        payload=_payload("Sam", "Lee"),
    )
    upsert_employee(
        username="hr_admin",
        display="HR Admin",
        payload=_payload("Sam", "Lee", title="Coordinator"),
        employee_id=row_id,
    )
    row = get_employee(row_id)
    assert row is not None
    assert row["requested_by_username"] == "hr_admin"
    assert row["employee_title"] == "Coordinator"


def test_search_need_to_know_filters_non_admin():
    init_db()
    upsert_employee(username="mgr_a", display="A", payload=_payload("Pat", "One"))
    upsert_employee(username="mgr_b", display="B", payload=_payload("Pat", "Two"))

    own = search_employees("pat", username="mgr_a", is_admin=False)
    assert len(own) == 1
    assert own[0]["employee_last"] == "One"

    all_hits = search_employees("pat", username="mgr_a", is_admin=True)
    assert len(all_hits) == 2


def test_delete_employee_removes_row():
    init_db()
    row_id = upsert_employee(
        username="mgr_a",
        display="A",
        payload=_payload("Out", "Going"),
    )
    assert delete_employee(row_id) is True
    assert get_employee(row_id) is None
    assert delete_employee(row_id) is False

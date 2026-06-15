import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config_store import get_branding, get_option_labels, get_role_followup


def _tags(items: list[str], label_map: dict[str, str]) -> str:
    if not items:
        return "<em style='color:#a0aec0'>None</em>"
    return "".join(
        f'<span style="display:inline-block;background:#ebf8ff;color:#2b6cb0;'
        f'border-radius:4px;padding:2px 8px;margin:2px 2px;font-size:12px;">'
        f"{label_map.get(v, v)}</span>"
        for v in items
    )


def _yn(value: bool | None) -> str:
    if value is None:
        return "—"
    return "Yes" if value else "No"


def _row(label: str, value: str) -> str:
    return (
        f"<tr>"
        f"<td style='padding:7px 0;width:210px;color:#4a5568;font-weight:600;"
        f"vertical-align:top'>{label}</td>"
        f"<td style='padding:7px 0;vertical-align:top'>{value}</td>"
        f"</tr>"
    )


def _section(title: str, rows: str) -> str:
    return (
        f"<div style='padding:20px 32px;border-bottom:1px solid #e2e8f0'>"
        f"<p style='font-size:11px;text-transform:uppercase;letter-spacing:.07em;"
        f"color:#718096;font-weight:700;margin:0 0 12px'>{title}</p>"
        f"<table style='width:100%;border-collapse:collapse'>{rows}</table>"
        f"</div>"
    )


def build_html_email(data: dict) -> str:
    branding = get_branding()
    hardware_labels = get_option_labels("hardware")
    software_labels = get_option_labels("software")
    portal_labels = get_option_labels("portals")
    mailbox_labels = get_option_labels("mailboxes")

    emp = data.get("employee", {})
    loc = data.get("location", {})
    hw = data.get("hardware", {})
    acc = data.get("access", {})
    eg = data.get("email_groups", {})
    sec = data.get("security", {})

    full_name = f"{emp.get('first_name', '')} {emp.get('last_name', '')}".strip()
    preferred = (
        f" &nbsp;<em>(prefers: {emp['preferred_name']})</em>"
        if emp.get("preferred_name")
        else ""
    )
    credentials = f", {emp['credentials']}" if emp.get("credentials") else ""
    title = emp.get("title", "")
    start = emp.get("start_date", "TBD")

    hw_parts: list[str] = []
    if hw.get("computer_type"):
        hw_parts.append(hw["computer_type"].title())
    if hw.get("monitors"):
        hw_parts.append(hw["monitors"].replace("_", " ").title())
    hw_main = ", ".join(hw_parts) if hw_parts else "—"
    hw_peripherals = _tags(hw.get("peripherals", []), hardware_labels)

    followup_row = ""
    followup = get_role_followup()
    if followup and eg.get("role_followup") is not None:
        followup_row = _row(followup["label"], _yn(eg["role_followup"]))

    alarm_note = ""
    if sec.get("alarm_code") and sec.get("alarm_facilities"):
        alarm_note = " — " + ", ".join(f.replace("_", " ").title() for f in sec["alarm_facilities"])

    employee_rows = (
        _row("Full Name", f"{full_name}{preferred}{credentials}") +
        _row("Title", title) +
        _row("Start Date", start)
    )
    if emp.get("is_student_or_resident") is not None:
        employee_rows += _row("Student / Resident", _yn(emp.get("is_student_or_resident")))
    if emp.get("is_bilingual") is not None:
        employee_rows += _row("Bilingual", _yn(emp.get("is_bilingual")))

    employee_section = _section("Employee Information", employee_rows)

    location_section = _section("Location", (
        _row("Office", loc.get("office") or "—") +
        _row("Area", loc.get("area") or "—")
    ))

    hardware_rows = _row("Needs Computer", _yn(hw.get("needs_computer")))
    if hw.get("needs_computer"):
        hardware_rows += _row("Computer / Monitors", hw_main)
        hardware_rows += _row("Peripherals", hw_peripherals)
    hardware_section = _section("Hardware", hardware_rows)

    access_rows = (
        _row("Email Access", _yn(acc.get("needs_email"))) +
        _row("Portal Access", _tags(acc.get("portals", []), portal_labels)) +
        _row("Software", _tags(acc.get("software", []), software_labels))
    )
    if acc.get("other_software"):
        access_rows += _row("Additional Software", acc["other_software"])
    access_rows += (
        _row("Mobile Access", _yn(acc.get("mobile_access"))) +
        _row("Network Printers", acc.get("network_printers") or "—")
    )
    access_section = _section("Access &amp; Software", access_rows)

    groups_rows = (
        _row("Email Groups", eg.get("groups") or "—") +
        _row("Shared Mailboxes", _tags(eg.get("mailboxes", []), mailbox_labels)) +
        followup_row +
        _row("Fax Numbers", eg.get("fax_numbers") or "—")
    )
    groups_section = _section("Email Groups &amp; Mailboxes", groups_rows)

    security_section = _section("Security", (
        _row("Alarm Code", (_yn(True) + alarm_note) if sec.get("alarm_code") else _yn(False)) +
        _row("Gate Access", _yn(sec.get("gate_access")))
    ))

    submitted_at = data.get("submitted_at", "")[:10]
    subject_prefix = branding.get("email_subject_prefix", "New Hire IT Request")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:20px;background:#f7fafc;font-family:Arial,sans-serif;font-size:14px;color:#2d3748">
<div style="max-width:680px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)">

  <div style="background:#2563eb;color:#fff;padding:24px 32px">
    <h1 style="margin:0 0 4px;font-size:20px">{subject_prefix}</h1>
    <p style="margin:0;opacity:.85;font-size:13px">
      Submitted {submitted_at} &nbsp;&middot;&nbsp; Requested by <strong>{data.get('requested_by', '')}</strong>
    </p>
  </div>

  {employee_section}
  {location_section}
  {hardware_section}
  {access_section}
  {groups_section}
  {security_section}

  <div style="padding:16px 32px;font-size:12px;color:#a0aec0">
    {branding.get('email_footer', '')}
  </div>
</div>
</body></html>"""


def _it_team_recipients(raw: str) -> list[str]:
    if not (raw or "").strip():
        return []
    return [p.strip() for p in re.split(r"[,;]+", raw) if p.strip()]


def send_it_checklist(data: dict) -> tuple[bool, str]:
    branding = get_branding()
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME", "")
    smtp_pass = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)
    it_raw = os.getenv("IT_TEAM_EMAIL", "")
    recipients = _it_team_recipients(it_raw)

    if not all([smtp_host, smtp_user, smtp_pass]) or not recipients:
        return False, "Email settings are not configured. Please fill in .env and restart."

    emp = data.get("employee", {})
    full_name = f"{emp.get('first_name', '')} {emp.get('last_name', '')}".strip()
    title = emp.get("title", "")
    start = emp.get("start_date", "TBD")
    prefix = branding.get("email_subject_prefix", "New Hire IT Request")
    subject = f"{prefix}: {full_name} ({title}) — Start {start}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(build_html_email(data), "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, recipients, msg.as_string())
        return True, ""
    except Exception as exc:
        return False, str(exc)

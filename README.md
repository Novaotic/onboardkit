# OnboardKit

Role-based IT intake for employee onboarding. Managers complete a guided six-step form; IT receives a structured HTML checklist email. Role presets pre-fill hardware, software, access, and email groups. This project is still in very early stages of development, and not meant for use in prouction at this stage.

## Features

- Six-step wizard: employee info, location, hardware, software, groups, security
- Role presets with admin CRUD — selecting a job role auto-fills steps 3–5
- Organization config via `config.json` (offices, software catalogs, branding, optional fields)
- Confirmation page with review and raw JSON payload
- SMTP email delivery to your IT team
- LDAP admin auth with env-var fallback for local development

## Requirements

- Python 3.10+

## Quick start

```powershell
cd onboardkit
python -m pip install -r requirements.txt
copy .env.example .env
# Edit .env — at minimum set SECRET_KEY and ADMIN_PASSWORD for local dev
python -m uvicorn main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

On first run, OnboardKit copies `config.example.json` → `config.json` and `presets.example.json` → `presets.json` if those files do not exist.

## Configuration

### `config.json`

Copy from `config.example.json` or let the app create it on first start. Key sections:

| Section | Purpose |
|---------|---------|
| `branding` | App name, tagline, email subject/footer |
| `offices` | Office locations for step 2 and location-conditional email groups |
| `alarm_facilities` | Physical security facilities for step 6 |
| `option_groups` | Hardware, software, portal, and mailbox catalogs |
| `employee_fields` | Enable/disable optional step-1 fields |
| `role_followup` | Optional conditional question on step 5 for flagged presets |
| `placeholders` | Form placeholder text |

### Role presets (`presets.json`)

Managed via `/admin` or by editing `presets.json` directly. Each preset defines default hardware, software, portals, mailboxes, and email groups for a job role.

### Environment (`.env`)

SMTP settings, `IT_TEAM_EMAIL`, `SECRET_KEY`, and admin authentication (LDAP or `ADMIN_USERNAME` / `ADMIN_PASSWORD`).

## Admin

- Sign in at `/admin/login`
- Create and edit role presets
- **Local dev:** leave `LDAP_HOST` blank in `.env` and use `ADMIN_USERNAME` / `ADMIN_PASSWORD`
- **Production:** set `LDAP_HOST` and related LDAP variables for Active Directory

## Security

**Do not use `.env` admin credentials in production.**

This project is still in very early stages and not meant for use in production yet.

When `LDAP_HOST` is blank, OnboardKit falls back to `ADMIN_USERNAME` and `ADMIN_PASSWORD` from `.env`. That mode is only for local development and quick demos:

- Passwords are stored in plain text on disk
- There is no lockout, MFA, or central identity policy
- Anyone with shell or backup access to the server can read the credentials

Before deploying to production:

1. Set `LDAP_HOST` (and `LDAP_DOMAIN`, `LDAP_BASE_DN`, `LDAP_ADMIN_GROUP`, etc.) for Active Directory
2. Use a strong, unique `SECRET_KEY`
3. Serve only over HTTPS (see `deploy/nginx/onboardkit.conf.example`)
4. Restrict network access to `/admin` if possible (VPN, internal DNS, firewall)

On startup, the app logs a warning if env-based admin auth is active.

## Deployment

See `deploy/systemd/onboardkit.service` and `deploy/nginx/onboardkit.conf.example` for a Fedora-style production layout (Uvicorn behind nginx).

## Project structure

```
onboardkit/
├── main.py              # Wizard routes and session logic
├── admin.py             # Preset manager
├── config_store.py      # Load org config
├── config.example.json  # Shipped default config
├── preset_store.py      # presets.json persistence
├── presets.example.json # Sample Acme Corp presets
├── email_service.py     # HTML checklist email
├── auth.py              # LDAP / env admin auth
└── templates/           # Jinja2 wizard and admin UI
```

## License

MIT — see [LICENSE](LICENSE).

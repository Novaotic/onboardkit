# OnboardKit

Role-based IT intake for employee onboarding. Managers complete a guided six-step form; IT receives a structured HTML checklist email. Role presets pre-fill hardware, software, access, and email groups.

This project is still in early development and is **not** meant for production use yet.

## Features

- Six-step wizard: employee info, location, hardware, software, groups, security
- Role presets with admin CRUD — selecting a job role auto-fills steps 3–5
- Organization config via `config.json` (offices, software catalogs, branding, optional fields)
- Confirmation page with review and raw JSON payload
- SMTP email delivery to your IT team
- Front-gate LDAP auth (Portal Users / Portal Admin) with env-var fallback for local development

## Requirements

- **Docker Compose** (recommended for development / LDAP testing), **or**
- Python 3.10+ for a native run

## Quick start (Docker)

Best path for day-to-day development and LDAP integration testing.

```powershell
cd onboardkit
copy .env.example .env
# Edit .env — set at least SECRET_KEY (and SMTP later if you want real mail)
docker compose up --build
```

| Service | URL / port | Purpose |
|---------|------------|---------|
| Web app | [http://127.0.0.1:8000](http://127.0.0.1:8000) | OnboardKit portal |
| OpenLDAP | `localhost:389` | Test directory (`example.org`, admin password `admin`) |

`docker-compose.yml` loads `.env`, then overrides LDAP settings so the app talks to the `ldap-server` container (`LDAP_HOST=ldap-server`, Portal Users / Portal Admin group DNs, bind as `cn=admin,dc=example,dc=org`).

**LDAP groups:** the OpenLDAP image does not create `Portal Users` / `Portal Admin` for you. Seed those groups (and members) to match the DNs in compose, or change `LDAP_USERS_GROUP` / `LDAP_ADMIN_GROUP` to groups you add.

**Env-only login inside Docker** (no directory): temporarily clear LDAP in compose overrides, or run:

```powershell
docker compose run --rm -e LDAP_HOST= -e ADMIN_USERNAME=admin -e ADMIN_PASSWORD=your-local-pass -p 8000:8000 web
```

Use a non-default `ADMIN_PASSWORD` — empty and known placeholders are rejected.

Stop the stack with `Ctrl+C`, or `docker compose down`.

## Quick start (native Python)

```powershell
cd onboardkit
python -m pip install -r requirements.txt
copy .env.example .env
# Edit .env — set SECRET_KEY; leave LDAP_HOST blank and set ADMIN_USERNAME / ADMIN_PASSWORD
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

| Area | Variables |
|------|-----------|
| Runtime | `APP_ENV` (`development` \| `production`), `LOG_LEVEL`, `SECRET_KEY` |
| Email | `SMTP_*`, `IT_TEAM_EMAIL` |
| LDAP | `LDAP_HOST`, `LDAP_DOMAIN`, `LDAP_BASE_DN`, `LDAP_USERS_GROUP`, `LDAP_ADMIN_GROUP`, bind DN/password |
| Dev fallback | `ADMIN_USERNAME` / `ADMIN_PASSWORD` when `LDAP_HOST` is blank |

`APP_ENV=production` refuses to start unless `LDAP_HOST` is set.

## Auth model

| Group | Access |
|-------|--------|
| **Portal Users** (`LDAP_USERS_GROUP`) | Sign in and submit new-hire requests |
| **Portal Admin** (`LDAP_ADMIN_GROUP`) | Same as Portal Users, plus `/admin` (role presets) |

Unauthenticated visitors are sent to `/login`. The signed-in identity is locked into the Requester field.

- **Local / Docker without LDAP:** leave `LDAP_HOST` blank; use `ADMIN_USERNAME` / `ADMIN_PASSWORD`
- **Docker with OpenLDAP sidecar:** compose sets `LDAP_HOST=ldap-server` (see Quick start above)
- **Production AD:** set `LDAP_HOST` and related variables against your domain

## Security

**Do not use `.env` admin credentials in production.**

When `LDAP_HOST` is blank, OnboardKit falls back to `ADMIN_USERNAME` and `ADMIN_PASSWORD` from `.env`. That mode is only for local development and quick demos:

- Passwords are stored in plain text on disk
- There is no lockout, MFA, or central identity policy
- Anyone with shell or backup access to the server can read the credentials

Before deploying to production:

1. Set `APP_ENV=production` and `LDAP_HOST` (plus `LDAP_DOMAIN`, `LDAP_BASE_DN`, `LDAP_USERS_GROUP`, `LDAP_ADMIN_GROUP`, etc.)
2. Use a strong, unique `SECRET_KEY`
3. Serve only over HTTPS (see `deploy/nginx/onboardkit.conf.example`)
4. Restrict network access to `/admin` if possible (VPN, internal DNS, firewall)

On startup, the app logs a warning if env-based auth is active.

## Deployment

See `deploy/systemd/onboardkit.service` and `deploy/nginx/onboardkit.conf.example` for a Fedora-style production layout (Uvicorn behind nginx). The root `Dockerfile` / `docker-compose.yml` pair is aimed at **development and LDAP testing**, not a hardened production deploy.

## Project structure

```
onboardkit/
├── main.py              # Portal routes, login gate, wizard
├── admin.py             # Preset manager (/admin)
├── auth.py              # LDAP / env authentication
├── paths.py             # Cross-platform app paths
├── config_store.py      # Load org config
├── config.example.json  # Shipped default config
├── preset_store.py      # presets.json persistence
├── presets.example.json # Sample presets
├── email_service.py     # HTML checklist email
├── Dockerfile           # App image (uvicorn on :8000)
├── docker-compose.yml   # App + OpenLDAP for local testing
└── templates/           # Jinja2 wizard and admin UI
```

## License

MIT — see [LICENSE](LICENSE).

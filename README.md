# OnboardKit

Role-based IT intake for employee onboarding, role transitions, and offboarding. Managers complete a guided six-step form; IT receives a structured HTML checklist email. Role presets pre-fill hardware, software, access, and email groups. Successful onboard/transition submissions update a SQLite current-state inventory; offboarding removes the employee after email succeeds.

This project is still in early development and is **not** meant for production use yet.

## Features

- Six-step wizard: employee info, location, hardware, software, groups, security
- Home hub: **New hire**, **Role transition**, and **Offboarding**
- Role presets with admin CRUD — selecting a job role auto-fills steps 3–5
- SQLite employee inventory (current-state only; delete on successful offboard)
- Organization config via `config.json` (offices, software catalogs, branding, optional fields)
- Confirmation page with review and raw JSON payload
- SMTP email delivery to your IT team (subjects/body framed by request type)
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

The web service waits until OpenLDAP reports healthy (`ldapsearch` bind succeeds) before starting, so early login attempts are less likely to hit a half-booted directory.

Compose bind-mounts `./data` → `/app/data` so the SQLite inventory (and WAL sidecars) persist across rebuilds. The `data/` folder is gitignored.

### Seeded LDAP test data

On startup, the `ldap-seed` one-shot service loads `ldap/bootstrap/50-onboardkit.ldif` into OpenLDAP (after slapd is healthy). This avoids osixia’s bootstrap bind-mount path, which fails on Windows Docker.

| Account | Password | Groups |
|---------|----------|--------|
| `portaluser` | `password123` | Portal Users |
| `portaladmin` | `password123` | Portal Users + Portal Admin |

Directory admin (for `ldapsearch` / tooling): `cn=admin,dc=example,dc=org` / `admin`.

Seed is skipped if `ou=users` already exists. After editing the LDIF, re-seed with:

```powershell
docker compose down -v
docker compose up --build
```

`docker compose down -v` removes the LDAP volumes. The `./data` bind mount is **not** a named volume, so inventory files under `data/` remain unless you delete that folder yourself.

**Note:** OnboardKit’s login path is Active Directory–shaped (UPN bind, `sAMAccountName`, AD `memberOf`). This OpenLDAP seed is for directory structure and ops testing. For a reliable UI smoke test without fighting AD vs OpenLDAP differences, use env-only login:

```powershell
docker compose run --rm -e LDAP_HOST="" -e ADMIN_USERNAME=admin -e ADMIN_PASSWORD=your-local-pass -p 8000:8000 web
```

Use a non-default `ADMIN_PASSWORD` — empty and known placeholders are rejected.

Stop the stack with `Ctrl+C`, or `docker compose down` (add `-v` to wipe LDAP volumes).

## Quick start (native Python)

```powershell
cd onboardkit
python -m pip install -r requirements.txt
copy .env.example .env
# Edit .env — set SECRET_KEY; leave LDAP_HOST blank and set ADMIN_USERNAME / ADMIN_PASSWORD
python -m uvicorn main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

On first run, OnboardKit copies `config.example.json` → `config.json` and `presets.example.json` → `presets.json` if those files do not exist. It also creates `data/onboardkit.db` for the employee inventory.

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

### Employee inventory (`data/`)

SQLite file: `data/onboardkit.db` (WAL mode). Successful **onboard** and **transition** submissions upsert a current-state row. Successful **offboarding** deletes that row (offboard requires loading an existing record — no blank offboard).

| Concern | Detail |
|---------|--------|
| Permissions | The app user must be able to **create and delete files** in `data/`, not only overwrite the `.db`. WAL creates `onboardkit.db-wal` and `onboardkit.db-shm` beside the database. |
| Windows / Linux / Docker | Same rule: directory ACLs or mount must allow create/delete inside `data/`. Compose uses `./data:/app/data`. |
| Backup | Copy `data/onboardkit.db` during a quiet period (or after checkpointing). Include or quiesce the WAL sidecars if the app is still running. |
| Git | `data/` is gitignored — never commit live inventory. |

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
| **Portal Users** (`LDAP_USERS_GROUP`) | Sign in; submit new-hire, transition, and offboard requests |
| **Portal Admin** (`LDAP_ADMIN_GROUP`) | Same as Portal Users, plus `/admin` (role presets) and `/admin/employees` (global inventory) |

Unauthenticated visitors are sent to `/login`. The signed-in identity is locked into the Requester field.

### Need-to-know inventory search

Managers searching on `/transition` or `/offboard` only see employees whose `requested_by_username` matches their session. Portal Admins see all matches. Load-by-id uses the same rule.

### Ownership on write

Every successful onboard/transition write sets the row owner to the **submitting user**. If an admin runs a transition for someone a frontline manager originally onboarded, that manager **loses** the employee in their search. Use **`/admin/employees`** for the global active roster (owner column included).

### Offboarding

Requires an existing inventory record. Legacy staff with no prior OnboardKit row should be added via **Role transition** (blank form + role preset) first, then offboarded.

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
5. Ensure the service account can create/delete files under `data/` (WAL sidecars)

On startup, the app logs a warning if env-based auth is active.

## Deployment

See `deploy/systemd/onboardkit.service` and `deploy/nginx/onboardkit.conf.example` for a Fedora-style production layout (Uvicorn behind nginx). The root `Dockerfile` / `docker-compose.yml` pair is aimed at **development and LDAP testing**, not a hardened production deploy.

Ensure the systemd `User=` owns (or can write) `WorkingDirectory`, including `data/` for SQLite and WAL sidecars.

## Project structure

```
onboardkit/
├── main.py              # Portal routes, login gate, wizard, flows
├── admin.py             # Presets + /admin/employees roster
├── auth.py              # LDAP / env authentication
├── employee_store.py    # SQLite current-state inventory
├── paths.py             # Cross-platform app paths (includes data/)
├── config_store.py      # Load org config
├── config.example.json  # Shipped default config
├── preset_store.py      # presets.json persistence
├── presets.example.json # Sample presets
├── email_service.py     # HTML checklist email
├── data/                # SQLite DB + WAL (gitignored, created at runtime)
├── Dockerfile           # App image (uvicorn on :8000)
├── docker-compose.yml   # App + OpenLDAP; ./data bind mount
├── ldap/bootstrap/      # OpenLDAP seed LDIF (Portal Users / Admin)
└── templates/           # Jinja2 wizard and admin UI
```

## License

MIT — see [LICENSE](LICENSE).

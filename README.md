# Demo HR Source of Truth App

A lightweight, containerized HR system designed as a Source of Truth for demonstrating and testing integration with **Saviynt Identity Cloud** (or any other IGA/IAM platform that consumes HR data via REST API).

This is **not** a production HR system. It is intentionally simple, self-contained, and easy to deploy so that integrators, consultants, and POC engineers can stand up a realistic HR data source in minutes for connector testing, demos, and workflow validation.

## What it does

- Stores employee records with realistic HR fields (employee number, name, contact info, department, job title, supervisor, employment status, hire/termination dates)
- Provides managed lookup tables for countries, states/provinces, employment statuses, departments, and job titles
- Exposes a full REST API for employee, lookup, and console-user management, plus backup export — suitable for IGA/IAM connector consumption
- Includes a refined-minimal web UI for managing employees, lookups, console users, and API credentials, with a consolidated **Settings** area for admins
- **Role-based UI access**: `admin`, `management` (employee CRUD), and `view_only` roles
- Supports both **API key** and **OAuth 2.0 Client Credentials** authentication for the REST API, with **least-privilege scopes on API keys** (e.g. an employee-only key vs. a full-admin key)
- **Backup & Restore**: export the whole instance to a (optionally password-encrypted) zip and restore from it
- Optional **OIDC single sign-on** for the UI (multiple identity providers)
- Reset-data feature for returning to a clean state between demos
- Ships as a single Docker container (built from source) with SQLite persistence
- Deployable locally, via Portainer, to AWS ECS/Fargate (scripted, one command), Azure Container Apps, or any Kubernetes cluster

## Quick start

### Local Docker Compose (recommended)

```bash
git clone https://github.com/tmanmidwest/hrDemoWebApp.git
cd hrDemoWebApp
docker compose up -d --build
```

The bundled `docker-compose.yml` builds the image from source — there is no public prebuilt image to pull. Then open your browser to **http://localhost:8000** — the root URL redirects to the web UI, which redirects to the login page if you're not signed in.

### Local Docker (build and run)

```bash
git clone https://github.com/tmanmidwest/hrDemoWebApp.git
cd hrDemoWebApp
docker build -t demo-hr-sot:local .
docker run -d \
  --name demo-hr-sot \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  demo-hr-sot:local
```

### Portainer (Docker host with a web UI)

Running this on a home server, NAS, or any Docker host you manage through [Portainer](https://www.portainer.io/)? Create a **Stack** pointed at this Git repo — Portainer builds the image from source, manages the persistent `/data` volume, and starts the container for you. (There is no public prebuilt image; build-from-source is the supported path.)

See the [Portainer section in DEPLOYMENT.md](docs/DEPLOYMENT.md#portainer) for the step-by-step repository-stack setup.

### AWS ECS Fargate (scripted)

A set of shell scripts in [`docs/fargate/`](docs/fargate/) automates the full deployment to your own AWS account — no manual AWS console steps required.

```bash
cd docs/fargate
chmod +x *.sh
./setup.sh    # verify prerequisites
./deploy.sh   # deploy to AWS (~10 minutes)
```

See [`docs/fargate/README.md`](docs/fargate/README.md) for the complete guide including how to update, stop, and tear down.

### What's at each URL

| URL | What you get |
|---|---|
| `/` | Redirects to the web UI |
| `/ui/login` | Login page |
| `/ui/employees` | Employee list (after login) |
| `/ui/lookups/...` | Manage countries, states, departments, statuses, job titles |
| `/ui/settings` | Admin-only settings hub: users, API keys, OAuth clients, identity providers, branding, system, backup & restore, reset data |
| `/docs` | Swagger UI for the REST API |
| `/redoc` | Alternative REST API documentation |
| `/health` | JSON health probe (status + DB check) |

### Default credentials

| Field | Value |
|---|---|
| Username | `robbytheadmin` |
| Password | `N0nPr0dF0r$@viynt8` |

> These credentials are intended for non-production POC use only. Change the password immediately via **Settings → Users → Change Password** in the UI, or rotate it using the included reset script. See [docs/SECURITY.md](docs/SECURITY.md).

## The web UI

The UI uses a refined-minimal admin aesthetic — quiet, professional, easy on the eyes. Key features:

- **Employee list** with tabs (Active / All / Archived), sortable columns, and per-machine column visibility (saved to your browser)
- **Add/Edit Employee** form with HTMX-powered dependent dropdowns: pick a country and the state/province list updates; pick a department and the job title list updates
- **Lookup management** for all six lookup tables. Deletes are blocked (with a helpful 409 message) if any other row still references the target — set `is_active=false` instead
- **Role-based access**: the sidebar and available actions adapt to the signed-in user's role (`admin` / `management` / `view_only`)
- **Users** management with role assignment and enable/disable
- **API keys** with least-privilege **scope selection** (presets: Employee Management, Read-Only, Full Admin) — plus OAuth clients. Secrets are displayed **once** at creation, then only the prefix is shown
- **Backup & Restore** — export the instance to a (optionally password-encrypted) zip and restore from one
- **Reset Data** page with checkboxes per table and a typed-phrase confirmation guard (you must type `RESET` to enable the destructive button)

For the full UI overview, see [docs/UI.md](docs/UI.md).

## Documentation

| Document | What it covers |
|---|---|
| [CHANGELOG.md](CHANGELOG.md) | Notable changes, newest first |
| [UI.md](docs/UI.md) | Web UI walkthrough, design system, reset feature |
| [REQUIREMENTS.md](docs/REQUIREMENTS.md) | Functional and non-functional requirements |
| [SCHEMA.md](docs/SCHEMA.md) | Database schema, all tables and fields |
| [API.md](docs/API.md) | REST API endpoints, auth, and examples |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture and deployment topology |
| [SAVIYNT_INTEGRATION.md](docs/SAVIYNT_INTEGRATION.md) | How to point Saviynt at this app |
| [SECURITY.md](docs/SECURITY.md) | Auth model, password reset, API key management |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Deploying to AWS, Azure, Kubernetes |
| [fargate/README.md](docs/fargate/README.md) | Scripted AWS ECS Fargate deployment — one command deploy, update, and teardown |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute or extend the app |

## Tech stack

- **Backend**: Python 3.12 + FastAPI
- **Database**: SQLite (single file, mounted volume for persistence)
- **ORM**: SQLAlchemy 2.x with Alembic migrations
- **Frontend**: Server-rendered Jinja2 templates + HTMX for dependent dropdowns
- **Styling**: Hand-crafted CSS design system (no framework) with Geist Sans + Geist Mono
- **Auth**: Session cookies + role-based access + optional OIDC SSO (UI); scoped API keys + OAuth 2.0 Client Credentials (REST API)
- **Container**: Python slim base image, ~180MB

## License

MIT — see [LICENSE](LICENSE).

## Maintainer

[tmanmidwest](https://github.com/tmanmidwest)

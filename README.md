# Demo HR Source of Truth App

A lightweight, containerized HR system designed as a Source of Truth for demonstrating and testing integration with **Saviynt Identity Cloud** (or any other IGA/IAM platform that consumes HR data via REST API).

This is **not** a production HR system. It is intentionally simple, self-contained, and easy to deploy so that integrators, consultants, and POC engineers can stand up a realistic HR data source in minutes for connector testing, demos, and workflow validation.

## What it does

- Stores employee records with realistic HR fields (employee number, name, contact info, department, job title, manager, employment status, hire/termination dates)
- Provides managed lookup tables for countries, states/provinces, employment statuses, departments, job titles, and managers
- Exposes a full REST API for employee CRUD operations, suitable for IGA/IAM connector consumption
- Includes a web UI for managing employees, lookups, admin users, and API credentials
- Supports both **API key** and **OAuth 2.0 Client Credentials** authentication for the REST API
- Ships as a single Docker container with SQLite persistence
- Deployable locally, to AWS ECS/Fargate, Azure Container Apps, or any Kubernetes cluster

## Quick start

### Local Docker

```bash
docker run -d \
  --name demo-hr-sot \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  ghcr.io/tmanmidwest/hrwebapp:latest
```

Then open:

- **Web UI**: http://localhost:8000
- **API docs (Swagger)**: http://localhost:8000/docs
- **Health check**: http://localhost:8000/health

### Docker Compose

```bash
git clone https://github.com/tmanmidwest/hrWebApp.git
cd hrWebApp
docker compose up -d
```

### Default credentials

| Field | Value |
|---|---|
| Username | `robbytheadmin` |
| Password | `N0nPr0dF0r$@viynt8` |

> These credentials are intended for non-production POC use only. Change the password immediately via the UI or rotate it using the included reset script. See [docs/SECURITY.md](docs/SECURITY.md).

## Documentation

| Document | What it covers |
|---|---|
| [REQUIREMENTS.md](docs/REQUIREMENTS.md) | Functional and non-functional requirements |
| [SCHEMA.md](docs/SCHEMA.md) | Database schema, all tables and fields |
| [API.md](docs/API.md) | REST API endpoints, auth, and examples |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture and deployment topology |
| [SAVIYNT_INTEGRATION.md](docs/SAVIYNT_INTEGRATION.md) | How to point Saviynt at this app |
| [SECURITY.md](docs/SECURITY.md) | Auth model, password reset, API key management |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Deploying to AWS, Azure, Kubernetes |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute or extend the app |

## Tech stack

- **Backend**: Python 3.12 + FastAPI
- **Database**: SQLite (single file, mounted volume for persistence)
- **ORM**: SQLAlchemy 2.x
- **Frontend**: Server-rendered Jinja2 templates + HTMX for dynamic interactions
- **Auth**: Session cookies (UI), API keys + OAuth 2.0 Client Credentials (REST API)
- **Container**: Python slim base image, ~180MB

## License

MIT — see [LICENSE](LICENSE).

## Maintainer

[tmanmidwest](https://github.com/tmanmidwest)

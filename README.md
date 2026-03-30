# sCron Backend

## Overview

A self-hosted cron job management platform with encrypted environment variables, DAG-based job dependencies, real-time log streaming, and multi-channel notifications. Built to replace fragile crontab entries with a managed, observable, and auditable system.

### Problem Statement

Managing scheduled tasks on Linux servers is painful. Crontab entries are invisible, unversioned, and fail silently. When a nightly backup script breaks, nobody knows until the data is gone. Teams that outgrow crontab typically jump to Airflow or Temporal — systems designed for 500-engineer orgs, not a solo developer running 10 scripts on a VPS.

sCron fills the gap: a lightweight, single-binary-deployable cron manager that gives you visibility, versioning, encryption, and alerting without the operational overhead of enterprise orchestrators.

---

## 1. Architecture Diagrams

![alt text](mermaid-diagram-2026-03-08T10-51-08.png)

---

## 2. Detailed Flow & File Breakdown

![alt text](mermaid-diagram-2026-03-08T11-05-52.png)

![alt text](mermaid-diagram-2026-03-08T11-07-35.png)

---

## 3. Features

### Core Scheduling

- **Cron-based scheduling** with standard 5-field expressions, powered by APScheduler
- **Per-job configurable timeout** (defaults to system-wide `DEFAULT_JOB_TIMEOUT`, overridable per job)
- **Concurrency control** via semaphore — configurable max concurrent jobs to prevent resource exhaustion
- **Manual trigger** — run any job on-demand outside its schedule, returns an execution ID immediately
- **Job cancellation** — send SIGTERM to a running subprocess via PID tracking; graceful shutdown with fallback to SIGKILL

### DAG Execution (Job Dependencies)

- Jobs can declare `depends_on: [job_id_1, job_id_2]` — a list of upstream jobs
- Before a scheduled run, the scheduler checks that **all** upstream jobs' most recent execution succeeded
- If dependencies aren't met, the run is skipped (logged, not failed)
- When a job succeeds, the scheduler scans for downstream dependents and auto-triggers any whose deps are now fully satisfied
- Circular dependency prevention at the API layer (self-reference blocked; further validation possible)

### Script Management

- Scripts (Python or Bash) stored in the database as source of truth
- **Immutable version history** — every edit creates a new `JobScriptVersion` row; old versions are never deleted
- **Version restore** — revert to any previous version (creates a new version entry for auditability)
- **Execution replay** — re-run a past execution using the exact script version and env vars from that run

### Security

- **JWT authentication** with access + refresh token rotation (refresh tokens stored as JTI in DB, not full JWT)
- **PBKDF2-SHA256** password hashing with per-user random salt (100K iterations, constant-time comparison)
- **Fernet encryption** for environment variables — derived from `SECRET_KEY + user.salt` via PBKDF2; each user gets a unique derived key
- **LRU-cached** Fernet instances (bounded to 128 entries) to avoid re-deriving keys on every request
- **Rate limiting** on auth endpoints — sliding-window, per-IP (5 req/min login, 10 req/min signup/refresh)
- **Ownership checks** on all resources including WebSocket log streams

### Notifications

- **Telegram** — via Bot API (`TELEGRAM_BOT_TOKEN`); user configures their `chat_id`
- **Email** — via SMTP (Gmail App Password); user provides email in profile
- **Configurable trigger**: `failure_only` (default), `always`, or `never`
- Notifications fire in background threads to avoid blocking the scheduler

### Organization

- **Tags** — user-created, color-coded labels for grouping jobs (many-to-many)
- **Filter by tag** — `GET /api/jobs?tag_id=N`
- **Job templates** — system-seeded (Health Check, DB Backup, Disk Alert, Slack Message, File Cleanup, Python Starter) + user-created
- **Job duplication** — clone a job with all its env vars, tags, and script content

### Observability

- **Real-time log streaming** via WebSocket — thread-safe pub/sub broadcaster with buffered catch-up for late joiners
- **Structured JSON logging** (NDJSON) to file — Grafana Loki / Promtail ready
- **Colored terminal output** with IST timestamps, file/line info
- **Analytics dashboard data**:
  - Global overview (job counts, success rate, avg duration)
  - Daily execution timeline (last N days, by status)
  - Hourly heatmap (hour × day-of-week)
  - Per-job stats (success rate, min/max/avg duration, last execution)
  - Per-job duration trend (for sparklines/charts)

### Database

- **PostgreSQL** with configurable schema (`DB_SCHEMA`)
- **SQLite** supported for development/testing (auto-detected from `DATABASE_URL`)
- **Alembic migrations** configured and ready (`alembic upgrade head`)
- **Connection pooling** tuned for PostgreSQL (pool_size=10, max_overflow=20, recycle=1800s)
- **Seed migration** for default job templates

---

## 4. Tech Stack

| Layer         | Technology                                       |
| ------------- | ------------------------------------------------ |
| Framework     | FastAPI (async, Pydantic v2)                     |
| ORM           | SQLAlchemy 2.0                                   |
| Scheduler     | APScheduler (BackgroundScheduler, threadpool)    |
| Database      | PostgreSQL 15 (prod) / SQLite (dev/test)         |
| Migrations    | Alembic                                          |
| Auth          | PyJWT + PBKDF2-SHA256                            |
| Encryption    | cryptography (Fernet)                            |
| Cron parsing  | croniter                                         |
| Logging       | stdlib logging + custom IST/JSON formatters      |
| Container     | Docker (multi-stage: base → dev → prod)          |
| Orchestration | Docker Compose + Kubernetes (Kustomize overlays) |
| CI/CD         | GitHub Actions → GHCR → ArgoCD                   |

---

## 5. Project Structure

```
scron/
├── main.py                          # FastAPI app, lifespan, CORS, router registration
├── alembic.ini                      # Alembic config (reads DATABASE_URL from constants)
├── alembic/
│   ├── env.py                       # Migration environment (auto-detects models)
│   ├── script.py.mako               # Migration template
│   └── versions/
│       └── 0001_seed_templates.py   # Seeds 6 default job templates
├── app/
│   ├── api/
│   │   ├── auth_routes.py           # Login, signup, refresh, logout (rate-limited)
│   │   ├── job_routes.py            # Job CRUD, trigger, cancel, replay, env vars, versions
│   │   ├── tag_routes.py            # Tag CRUD
│   │   ├── notification_routes.py   # Notification settings GET/PUT
│   │   ├── template_routes.py       # List job templates
│   │   ├── user_routes.py           # Profile GET/PATCH (email, display_name)
│   │   ├── analytics_routes.py      # Dashboard charts data (7 endpoints)
│   │   ├── config_routes.py         # Shared requirements.txt management
│   │   ├── ws_routes.py             # WebSocket log streaming (ownership-checked)
│   │   ├── deps.py                  # get_current_user dependency (Bearer JWT)
│   │   └── rate_limit.py            # Sliding-window in-memory rate limiter
│   ├── common/
│   │   ├── constants.py             # All env vars centralised (loaded via dotenv)
│   │   └── schemas.py               # Pydantic request/response models
│   ├── db/
│   │   ├── database.py              # Engine, session factory, init_db (Alembic or create_all)
│   │   └── models.py                # 10 ORM models (User, Job, Tag, Execution, etc.)
│   ├── services/
│   │   ├── auth_service.py          # Password hashing, JWT lifecycle, user CRUD
│   │   ├── job_service.py           # All business logic (jobs, env vars, tags, DAG, templates)
│   │   ├── scheduler_service.py     # APScheduler lifecycle, execution orchestrator, cancel/replay
│   │   ├── notification_service.py  # Telegram + Email sending, notify_execution_complete
│   │   ├── crypto_service.py        # Fernet encrypt/decrypt with LRU-cached key derivation
│   │   ├── analytics_service.py     # Aggregation queries (optimised single-query patterns)
│   │   └── log_broadcaster.py       # Thread-safe pub/sub for WebSocket log streaming
│   └── utils/
│       └── logging_utils.py         # IST colored console + NDJSON file formatter
├── tests/                           # 278 tests across 11 files
│   ├── conftest.py                  # SQLite in-memory fixtures, StaticPool, test_user/job/tag
│   ├── test_auth_routes.py          # 17 tests — signup, login, refresh, logout
│   ├── test_auth_service.py         # 23 tests — hashing, JWT, user CRUD
│   ├── test_job_routes.py           # 46 tests — all job endpoints
│   ├── test_job_service.py          # 55 tests — all service-layer logic
│   ├── test_new_features.py         # 50 tests — tags, DAG, timeout, notifications, templates, profile, analytics routes
│   ├── test_analytics.py            # 17 tests — all analytics queries
│   ├── test_crypto_service.py       # 15 tests — encrypt/decrypt, cache
│   ├── test_notification_service.py # 16 tests — formatting, Telegram, email
│   ├── test_log_broadcaster.py      # 16 tests — pub/sub, channels, subscriptions
│   ├── test_scheduler_service.py    # 16 tests — cron parsing, log output, script materialisation
│   └── test_rate_limit.py           # 7 tests — sliding window + integration
├── Dockerfile                       # Multi-stage: base → dev (reload) → prod
├── docker-compose.yml               # Prod: app + PostgreSQL
├── docker-compose.dev.yml           # Dev: code mount, auto-reload, DEBUG logging
├── k8s/                             # Kubernetes manifests (Kustomize base + overlays)
└── .github/workflows/               # CI: build → push GHCR → update K8s manifest
```

---

## 6. Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL 15+ (or SQLite for local dev)
- `uv` package manager (recommended) or `pip`

### Local Development

```bash
# Clone and enter
git clone <repo-url> && cd scron

# Create .env
cp .env.example .env
# Edit .env: set SECRET_KEY, DATABASE_URL, TELEGRAM_BOT_TOKEN, SMTP_* etc.

# Install dependencies
uv sync

# Initialize database
uv run python -c "from app.db.database import init_db; init_db()"

# Run with auto-reload
uv run uvicorn main:app --reload

# Or with Docker
docker compose -f docker-compose.dev.yml up
```

### Run Tests

```bash
# All 278 tests
uv run pytest tests/ -v

# Specific file
uv run pytest tests/test_job_service.py -v

# With coverage
uv run pytest tests/ --cov=app --cov-report=term-missing
```

### Production Deployment

```bash
# Docker Compose
docker compose up -d

# Kubernetes
kubectl apply -k k8s/overlays/prod

# Run Alembic migrations
alembic upgrade head
```

---

## 7. Environment Variables

| Variable              | Required | Default                                                  | Description                              |
| --------------------- | -------- | -------------------------------------------------------- | ---------------------------------------- |
| `SECRET_KEY`          | Yes      | —                                                        | JWT signing + encryption key derivation  |
| `DATABASE_URL`        | No       | `postgresql://postgres:postgres@localhost:5432/postgres` | Database connection string               |
| `DB_SCHEMA`           | No       | `public`                                                 | PostgreSQL schema name                   |
| `PORT`                | No       | `8000`                                                   | Server port                              |
| `CORS_ORIGINS`        | No       | `*`                                                      | Comma-separated allowed origins          |
| `MAX_CONCURRENT_JOBS` | No       | `3`                                                      | Max jobs running simultaneously          |
| `DEFAULT_JOB_TIMEOUT` | No       | `3600`                                                   | Default timeout (seconds) per job        |
| `TELEGRAM_BOT_TOKEN`  | No       | —                                                        | Telegram Bot API token for notifications |
| `SMTP_HOST`           | No       | `smtp.gmail.com`                                         | SMTP server                              |
| `SMTP_PORT`           | No       | `587`                                                    | SMTP port                                |
| `SMTP_USER`           | No       | —                                                        | SMTP username (Gmail address)            |
| `SMTP_PASSWORD`       | No       | —                                                        | SMTP password (App Password for Gmail)   |
| `SMTP_FROM`           | No       | `SMTP_USER`                                              | From address for emails                  |
| `LOG_LEVEL`           | No       | `INFO`                                                   | Logging level                            |
| `LOG_DIR`             | No       | `logs`                                                   | Directory for log files                  |

---

## 8. API Overview

| Method                | Endpoint                                 | Description                        |
| --------------------- | ---------------------------------------- | ---------------------------------- |
| POST                  | `/api/auth/signup`                       | Create account (optional email)    |
| POST                  | `/api/auth/login`                        | Login → access + refresh tokens    |
| POST                  | `/api/auth/refresh`                      | Rotate refresh token               |
| POST                  | `/api/auth/logout`                       | Revoke refresh token               |
| GET/PATCH             | `/api/profile`                           | User profile (email, display_name) |
| GET/POST              | `/api/jobs`                              | List (filter by tag) / Create job  |
| GET/PATCH/DELETE      | `/api/jobs/{id}`                         | Read / Update / Delete job         |
| POST                  | `/api/jobs/{id}/trigger`                 | Manual run                         |
| POST                  | `/api/jobs/{id}/executions/{eid}/cancel` | Cancel running execution           |
| POST                  | `/api/jobs/{id}/replay`                  | Replay past execution              |
| POST                  | `/api/jobs/{id}/duplicate`               | Clone job                          |
| GET/POST/PUT/DELETE   | `/api/jobs/{id}/env`                     | Environment variables (encrypted)  |
| GET                   | `/api/jobs/{id}/executions`              | Execution history                  |
| GET/POST              | `/api/jobs/{id}/versions`                | Script version history / Restore   |
| GET                   | `/api/jobs/{id}/next-runs`               | Next N scheduled times             |
| GET                   | `/api/jobs/{id}/stream-status`           | Check if live log stream active    |
| WS                    | `/api/ws/logs/{eid}`                     | Stream execution logs              |
| WS                    | `/api/ws/logs/job/{id}`                  | Stream active job logs             |
| GET/POST/PATCH/DELETE | `/api/tags`                              | Tag management                     |
| GET/PUT               | `/api/notifications`                     | Notification settings              |
| GET                   | `/api/templates`                         | List job templates                 |
| GET                   | `/api/analytics/overview`                | Dashboard stats                    |
| GET                   | `/api/analytics/timeline`                | Daily execution chart              |
| GET                   | `/api/analytics/heatmap`                 | Hour × day heatmap                 |
| GET                   | `/api/analytics/jobs/breakdown`          | Per-job success/failure            |
| GET                   | `/api/analytics/jobs/{id}/stats`         | Single job stats                   |
| GET                   | `/api/analytics/jobs/{id}/duration`      | Duration trend                     |
| GET                   | `/api/analytics/jobs/{id}/timeline`      | Per-job daily timeline             |
| GET                   | `/api/config/requirements`               | Shared requirements.txt            |
| PUT                   | `/api/config/requirements`               | Update + pip install               |

---

## 9. License

AGPL-3.0

---

## 10. Screenshots

![alt text](./.images/image.png)
![alt text](./.images/image-1.png)
![alt text](./.images/image-2.png)
![alt text](./.images/image-3.png)
![alt text](./.images/image-4.png)
![alt text](./.images/image-5.png)
![alt text](./.images/image-6.png)
![alt text](./.images/image-7.png)
![alt text](./.images/image-8.png)
![alt text](./.images/image-9.png)
![alt text](./.images/image-10.png)
![alt text](./.images/image-11.png)

---

## 11. Additional Resources

For development guidance and command references, see [CLAUDE.md](CLAUDE.md).

# Mantle EMS — Backend

Real-time emergency management system API for home healthcare workers, built with FastAPI, PostgreSQL, and Redis.

## Requirements

- Docker & Docker Compose (recommended)
- **or** Python 3.12+, PostgreSQL 16, Redis 7

---

## Quick Start (Docker)

```bash
# From the repo root
cp backend/.env.example backend/.env
# Edit backend/.env — at minimum set SECRET_KEY and your LLM provider credentials
docker compose up --build
```

The API will be available at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

---

## Local Development (without Docker)

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Apply migrations
alembic upgrade head

# Run the server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Set `DATABASE_URL` and `REDIS_URL` to point at your local Postgres and Redis instances.

---

## Environment Variables

Copy `.env.example` to `.env` and configure the values below.

### Database

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://mantle:mantle_secret@postgres:5432/mantle_ems` | Full async SQLAlchemy connection string, read by the FastAPI app |
| `POSTGRES_USER` | `mantle` | Read by the **Postgres Docker image** to create the DB user on first boot — must match the credentials in `DATABASE_URL` |
| `POSTGRES_PASSWORD` | `mantle_secret` | Same as above — must match `DATABASE_URL` |
| `POSTGRES_DB` | `mantle_ems` | Same as above — must match `DATABASE_URL` |

> The three `POSTGRES_*` vars are only needed when running Postgres via Docker Compose. If you bring your own Postgres instance, only `DATABASE_URL` is required.

### Redis

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection URL |

### JWT / Auth

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(change this)* | **Required in production.** Random secret for signing HS256 tokens. Generate with `openssl rand -hex 32`. |
| `ALGORITHM` | `HS256` | JWT signing algorithm. Use `RS256` in production with `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY`. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Web access token lifetime (minutes) |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime (days) |
| `MOBILE_ACCESS_TOKEN_EXPIRE_DAYS` | `365` | Mobile access token lifetime (days) |

### File Storage

| Variable | Default | Description |
|---|---|---|
| `UPLOADS_DIR` | `/app/uploads` | Directory for uploaded evidence files |

### Seed Data

| Variable | Default | Description |
|---|---|---|
| `RUN_SEED` | `true` | Run seed data on startup. Set to `false` after first run or in production. |

### AI / LLM

| Variable | Default | Description |
|---|---|---|
| `AI_MODE` | `scripted` | `llm` — full LLM agent loop; `scripted` — rule-based SOP executor (no LLM required) |
| `LLM_PROVIDER` | `ollama` | LLM backend: `ollama`, `anthropic`, or `bedrock` |
| `LLM_MODEL` | `llama3.1:8b` | Provider-specific model ID (see options below) |
| `LLM_TEMPERATURE` | `0.0` | Sampling temperature. `0.0` is deterministic and best for structured tool calls. |
| `LLM_TIMEOUT` | `30` | Seconds to wait per LLM call before timing out |
| `LLM_MAX_TOKENS` | `4096` | Max tokens per LLM response |
| `LLM_MAX_ITERATIONS` | `15` | Max agent loop iterations before giving up |
| `LLM_ADAPTIVE_SOP` | `false` | Allow LLM to propose step/SOP deviations via `propose_step_adaptation` / `propose_sop_switch` tools |

#### Option 1 — Ollama (local, no internet required)

```env
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:14b
LLM_BASE_URL=http://host.docker.internal:11434  # Docker → Mac; use http://localhost:11434 for bare-metal
LLM_NUM_CTX=4096
```

Install Ollama and pull the model:

```bash
ollama pull qwen2.5:14b
```

> **Note:** `qwen2.5:14b` is the recommended model (~9 GB). Avoid `qwen3:*` — thinking mode exhausts token budgets before tool calls and cannot be disabled via API.

| Variable | Default | Description |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `LLM_NUM_CTX` | `8192` | KV cache context window size (tokens) |

#### Option 2 — Anthropic Claude API

```env
LLM_PROVIDER=anthropic
LLM_MODEL=claude-haiku-4-5-20251001   # cheapest; use claude-sonnet-4-6 for best quality
ANTHROPIC_API_KEY=sk-ant-...
```

> API credits must be purchased separately from an Anthropic Pro subscription at `console.anthropic.com/settings/billing`.

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | API key from `console.anthropic.com/settings/api-keys` |

#### Option 3 — AWS Bedrock

```env
LLM_PROVIDER=bedrock
LLM_MODEL=anthropic.claude-haiku-4-5-20251001-v1:0
AWS_REGION=us-east-1
```

AWS credentials must be configured in the environment (e.g. `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` or an IAM role). Ensure Bedrock model access is enabled in your AWS account.

| Variable | Default | Description |
|---|---|---|
| `AWS_REGION` | `us-east-1` | AWS region for Bedrock |

### Push Notifications (optional)

| Variable | Default | Description |
|---|---|---|
| `FIREBASE_CREDENTIALS_PATH` | *(unset)* | Path to Firebase service account JSON file. Omit to disable push notifications. |

---

## Docker Compose Services

| Service | Port | Description |
|---|---|---|
| `api` | `8000` | FastAPI application |
| `postgres` | `5432` | PostgreSQL 16 database |
| `redis` | `6379` | Redis 7 cache / pub-sub |
| `pgadmin` | `5050` | pgAdmin 4 web UI |
| `redisinsight` | `5540` | RedisInsight web UI |

### pgAdmin credentials

| Variable | Default |
|---|---|
| `PGADMIN_EMAIL` | `admin@mantle.com` |
| `PGADMIN_PASSWORD` | `admin` |

---

## Database Migrations

Migrations are run automatically on container startup via `alembic upgrade head`.

To create a new migration manually:

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Swagger UI |
| `GET` | `/redoc` | ReDoc UI |
| `*` | `/auth/*` | Authentication (login, refresh, register) |
| `*` | `/incidents/*` | Incident management |
| `*` | `/threads/*` | Incident threads |
| `*` | `/actions/*` | Actions / tasks |
| `*` | `/evidence/*` | File evidence uploads |
| `*` | `/search/*` | Search |
| `*` | `/config/*` | Runtime configuration |
| `*` | `/monitoring/*` | System monitoring |

---

## Production Checklist

- [ ] Replace `SECRET_KEY` with a strong random value (`openssl rand -hex 32`)
- [ ] Set `RUN_SEED=false` after the first run
- [ ] Use strong Postgres credentials
- [ ] Set `PGADMIN_EMAIL` and `PGADMIN_PASSWORD` to non-default values (or remove the `pgadmin` service)
- [ ] Configure a real `LLM_PROVIDER` and credentials if using AI features
- [ ] Mount `UPLOADS_DIR` and `LOGS_DIR` to persistent volumes
- [ ] Consider switching to RS256 JWT (generate RSA keypair, set `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY`)

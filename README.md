# Autonomous Recovery System

> Built at **Hack NC State 2026** hackathon — **3rd Place Winner, AWS Track**

An AI-driven self-healing system that detects production faults via CloudWatch, diagnoses them using RAG-based reasoning (Backboard.io), and automatically generates and deploys code patches through Gemini, GitHub, and CI/CD to ECS Fargate — with zero human intervention.

---

## How It Works

```
Production Error (ECS Fargate)
        │
        ▼
  CloudWatch Logs
        │
        ▼
  AWS Lambda: FaultRouter  ──────► POST /incidents/ (records incident)
        │                                  │
        │                                  ▼
        │                          Backboard RAG (queries knowledge base
        │                          of past incidents + LLM analysis)
        ▼
  Google Gemini (generates code fix)
        │
        ▼
  AWS Lambda: GithubTool (commits fix to repo)
        │
        ▼
  GitHub Actions CI/CD (tests + builds)
        │
        ▼
  ECS Fargate (redeploys with the fix)
        │
        ▼
  Developer Dashboard (shows resolved incident in real-time)
```

1. **Fault Detection** — CloudWatch monitors ECS logs for errors and triggers the FaultRouter Lambda.
2. **Incident Creation** — The Lambda calls the Flask app API to record a new incident with error code, symptoms, and breadcrumbs.
3. **RAG Analysis** — The app queries Backboard.io's knowledge base of past incidents using retrieval-augmented generation, and an LLM (GPT-4o) suggests root cause and remediation.
4. **Code Patch Generation** — The Lambda invokes Google Gemini with the incident context to generate a code fix.
5. **Auto-Commit** — A second Lambda reads the current file from GitHub, applies the patch, and commits directly to the repo.
6. **CI/CD & Redeploy** — GitHub Actions runs tests, builds a new Docker image, and deploys to ECS Fargate.
7. **Dashboard** — A real-time developer dashboard (powered by SSE + Redis pub/sub) shows incident status and remediation progress.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Flask, Gunicorn, Celery |
| Database | PostgreSQL, SQLAlchemy, Alembic |
| Cache / Messaging | Redis (Celery broker + SSE pub/sub) |
| Frontend | Tailwind CSS, esbuild |
| RAG / AI | Backboard.io (GPT-4o), Google Gemini |
| AWS | CloudWatch, Lambda, ECS Fargate, Secrets Manager |
| CI/CD | GitHub Actions, Docker |
| Testing | pytest |

---

## Project Structure

```
hack_ncstate/
├── hello/                          # Main Flask application
│   ├── app.py                      # App factory
│   ├── page/                       # Fault injection endpoints (for demo/testing)
│   ├── incident/                   # Core incident management & RAG pipeline
│   │   ├── models.py               # Incident database model
│   │   ├── analyzer.py             # Fault → Incident creation + RAG analysis
│   │   ├── rag_service.py          # Backboard.io async integration
│   │   └── views.py               # REST API & SSE dashboard
│   ├── developer/                  # Developer dashboard (CloudWatch aggregation)
│   ├── aws/                        # CloudWatch log fetching
│   └── up/                         # Health check endpoints
├── fault_router_lambda_function.py # Lambda: CloudWatch → Gemini → GitHub
├── GithubTool_lambda_function.py   # Lambda: read/write files on GitHub
├── config/                         # Flask & Gunicorn settings
├── db/                             # Alembic migrations & seeds
├── test/                           # Test suite (pytest)
├── assets/                         # Frontend (Tailwind, esbuild)
├── .github/workflows/              # CI/CD pipelines
├── docker-compose.yaml             # Local dev environment
├── Dockerfile                      # Multi-stage production build
└── run                             # Task runner script
```

---

## Getting Started

### Prerequisites

- Docker & Docker Compose
- AWS credentials (for CloudWatch/Lambda features)

### Setup

```bash
# Clone the repo
git clone https://github.com/<your-username>/hack_ncstate.git
cd hack_ncstate

# Copy environment variables
cp .env.example .env
# Edit .env with your Backboard API key, AWS credentials, etc.

# Build and start all services
docker compose up --build
```

This starts the Flask web server, Celery worker, PostgreSQL, and Redis.

### Initialize the Database

```bash
./run flask db reset --with-testdb
```

### Seed the RAG Knowledge Base (one-time)

```bash
# Create the Backboard assistant
curl -X POST http://localhost:8000/incidents/setup-assistant

# Seed with example incidents
curl -X POST http://localhost:8000/incidents/seed-kb
```

### Access the App

| URL | Description |
|---|---|
| `http://localhost:8000/` | Home page |
| `http://localhost:8000/test-fault` | Fault injection UI (trigger test faults) |
| `http://localhost:8000/incidents/dashboard` | Real-time incident dashboard |
| `http://localhost:8000/developer/incidents` | Developer view with CloudWatch events |
| `http://localhost:8000/up` | Health check |

---

## Key Features

- **Self-Healing Pipeline** — End-to-end automated detection, diagnosis, patching, and redeployment.
- **RAG-Powered Diagnosis** — Learns from past incidents to provide increasingly accurate root cause analysis.
- **Real-Time Dashboard** — Server-Sent Events stream incident updates as they happen.
- **Fault Injection Testing** — Built-in endpoints to simulate SQL injection, database timeouts, and external API failures.
- **Circuit Breaker Pattern** — Prevents cascading failures when external services go down.
- **Retry with Exponential Backoff** — Resilient external API calls.

---

## Useful Commands

```bash
./run test              # Run tests
./run test:coverage     # Run tests with coverage
./run lint              # Lint Python code
./run format            # Auto-format Python code
./run quality           # Run all quality checks
./run shell             # Open a bash session in the container
./run psql              # Connect to PostgreSQL
./run redis-cli         # Connect to Redis
```

---

## Team

Built with caffeine and determination at **Hack NC State 2026**.

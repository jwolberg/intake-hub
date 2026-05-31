# Deploy — Google Cloud (Cloud Run)

**Status: DRAFT.** First-pass deployment guide for getting InvoiceScreener onto
Google Cloud. It targets **Cloud Run** for the containers and **Cloud SQL for
PostgreSQL** for persistence. For local setup see [`RUNBOOK.md`](./RUNBOOK.md);
for structure see [`ARCHITECTURE.md`](./ARCHITECTURE.md).

> This describes a **demo / staging** deployment. The `mcp-reference` and
> `mock-clinrun` services are still the *stub* backends — in a real environment
> they would be replaced by the actual sponsor/study reference system and the
> real ClinRun submission API (see Open Decisions). They are deployed here so the
> full pipeline runs end-to-end in the cloud.

## Why Cloud Run

The backend is a stateless HTTP container that applies its own DB schema on
startup (`init_schema`) — no separate migration step, no local disk, scales to
zero. That is exactly Cloud Run's sweet spot. Cloud SQL holds the only durable
state. Secrets (the Anthropic key) come from Secret Manager. The alternative
(GKE) is more machinery than this MVP needs.

| Local (Compose) | Cloud |
| --- | --- |
| `db` (postgres:16) | Cloud SQL for PostgreSQL 16 |
| `api` (FastAPI) | Cloud Run service `invoicescreener-api` |
| `mcp-reference` (stub) | Cloud Run service `invoicescreener-mcp` (internal ingress) |
| `mock-clinrun` (stub) | Cloud Run service `invoicescreener-clinrun` (internal ingress) |
| `hub` (Vite dev server) | static build served by Cloud Run / Firebase Hosting |
| `ANTHROPIC_API_KEY` (Keychain) | Secret Manager → env var |

## Prerequisites

- `gcloud` CLI authenticated (`gcloud auth login`) with a project selected.
- Billing enabled on the project.
- Roles: `roles/run.admin`, `roles/cloudsql.admin`, `roles/secretmanager.admin`,
  `roles/artifactregistry.admin`, `roles/iam.serviceAccountUser` (or Owner for a
  first pass).

```bash
export PROJECT_ID=ledgerrun-1
export REGION=us-central1
gcloud config set project "$PROJECT_ID"
gcloud services enable \
  run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com
```

## 0. Two prerequisite code changes

These are the only spots where the current containers assume the local topology.
Make them before the first deploy (tracked as follow-ups, see end of doc):

1. **Honor Cloud Run's `$PORT`.** Cloud Run sends traffic to `$PORT` (default
   `8080`); `backend/Dockerfile` hardcodes `--port 8000`. Either set the Cloud
   Run container port to `8000`, or switch the `CMD` to shell form so it honors
   the injected port:

   ```dockerfile
   # backend/Dockerfile
   CMD exec uvicorn backend.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
   ```

2. **`VITE_API_URL` is baked at build time.** The hub is a static Vite bundle;
   the API URL is compiled in, not read at runtime. The frontend `Dockerfile` is
   a **dev server** (`npm run dev`) and is not suitable for production. Build the
   hub against the deployed API URL (see §5).

## 1. Artifact Registry (image host)

```bash
gcloud artifacts repositories create invoicescreener \
  --repository-format=docker --location="$REGION"
export IMG="$REGION-docker.pkg.dev/$PROJECT_ID/invoicescreener"
```

## 2. Cloud SQL (PostgreSQL)

```bash
gcloud sql instances create invoicescreener-db \
  --database-version=POSTGRES_16 --tier=db-f1-micro --region="$REGION"

gcloud sql databases create invoicescreener --instance=invoicescreener-db
gcloud sql users create invoicescreener \
  --instance=invoicescreener-db --password='CHANGE_ME_STRONG'

# Connection name is PROJECT:REGION:INSTANCE — used by the Cloud Run socket mount.
export INSTANCE_CONN="$(gcloud sql instances describe invoicescreener-db \
  --format='value(connectionName)')"
```

Cloud Run connects to Cloud SQL over a Unix socket at
`/cloudsql/$INSTANCE_CONN`. The SQLAlchemy/psycopg DSN uses `host=` for that
socket (no TCP host/port):

```
DATABASE_URL=postgresql+psycopg://invoicescreener:CHANGE_ME_STRONG@/invoicescreener?host=/cloudsql/PROJECT:REGION:INSTANCE
```

## 3. The Anthropic key → Secret Manager

This is the cloud counterpart of the local Keychain entry
(`ledgerrun-ANTHROPIC_API_KEY`, see [`RUNBOOK.md`](./RUNBOOK.md)). Pipe the value
in from your Keychain so it never hits the terminal or shell history:

```bash
security find-generic-password -s ledgerrun-ANTHROPIC_API_KEY -w \
  | gcloud secrets create ledgerrun-anthropic-api-key --data-file=-

# Rotate later with:
# security find-generic-password -s ledgerrun-ANTHROPIC_API_KEY -w \
#   | gcloud secrets versions add ledgerrun-anthropic-api-key --data-file=-
```

Grant the Cloud Run runtime service account read access (default compute SA shown;
prefer a dedicated SA for production):

```bash
export RUNTIME_SA="$(gcloud projects describe "$PROJECT_ID" \
  --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding ledgerrun-anthropic-api-key \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor
```

## 4. Build & deploy the services

All three backend services use the same image (`backend/Dockerfile`); only the
start command differs — same split as `docker-compose.yml`.

```bash
# One image for api + both stub services
gcloud builds submit --tag "$IMG/backend:latest" .
```

### 4a. Stub services (deploy first — the API needs their URLs)

```bash
gcloud run deploy invoicescreener-mcp \
  --image "$IMG/backend:latest" --region "$REGION" \
  --ingress internal --no-allow-unauthenticated \
  --command uvicorn \
  --args backend.clients.stub_servers.mcp_app:app,--host,0.0.0.0,--port,8080

gcloud run deploy invoicescreener-clinrun \
  --image "$IMG/backend:latest" --region "$REGION" \
  --ingress internal --no-allow-unauthenticated \
  --command uvicorn \
  --args backend.clients.stub_servers.clinrun_app:app,--host,0.0.0.0,--port,8080

export MCP_URL="$(gcloud run services describe invoicescreener-mcp \
  --region "$REGION" --format='value(status.url)')"
export CLINRUN_URL="$(gcloud run services describe invoicescreener-clinrun \
  --region "$REGION" --format='value(status.url)')"
```

> Internal ingress + `--no-allow-unauthenticated` keep the stubs private. For the
> API to reach them service-to-service, attach a service account and either use
> the VPC/internal routing or grant the API SA `roles/run.invoker` on each stub.
> (For a quick demo you can instead use `--ingress all` + `--allow-unauthenticated`
> — note the tradeoff: the stubs become publicly reachable.)

### 4b. The API

```bash
gcloud run deploy invoicescreener-api \
  --image "$IMG/backend:latest" --region "$REGION" \
  --allow-unauthenticated \
  --add-cloudsql-instances "$INSTANCE_CONN" \
  --set-secrets "ANTHROPIC_API_KEY=ledgerrun-anthropic-api-key:latest" \
  --set-env-vars "^@^DATABASE_URL=postgresql+psycopg://invoicescreener:CHANGE_ME_STRONG@/invoicescreener?host=/cloudsql/$INSTANCE_CONN@MCP_REFERENCE_URL=$MCP_URL@CLINRUN_URL=$CLINRUN_URL@LLM_MODEL=claude-opus-4-7"

export API_URL="$(gcloud run services describe invoicescreener-api \
  --region "$REGION" --format='value(status.url)')"
curl "$API_URL/health"   # expect {"status":"ok",...,"db":"up"}
```

Notes:
- `--set-env-vars` uses the `^@^` delimiter trick because `DATABASE_URL` itself
  contains commas; `@` then separates the vars.
- `CORS_ORIGINS` must include the hub's deployed origin once §5 is done
  (add it to `--set-env-vars`), or the browser hub will be blocked by CORS.
- The schema is created on first startup by `init_schema` — no migration job.

## 5. The reviewer hub (frontend)

The hub needs `VITE_API_URL=$API_URL` **baked in at build time**, then served as
static files. Two options:

**A. Firebase Hosting / GCS static bucket (simplest for a static SPA):**

```bash
cd frontend
VITE_API_URL="$API_URL" npm ci && npm run build   # -> frontend/dist
# deploy frontend/dist via `firebase deploy` or `gsutil rsync` to a bucket+CDN
```

**B. Cloud Run (static via nginx):** add a production Dockerfile that builds the
bundle and serves `dist/` with nginx on `$PORT`, then `gcloud run deploy
invoicescreener-hub`. (The existing `frontend/Dockerfile` is dev-only — do not
ship it.)

After the hub is live, add its origin to the API's `CORS_ORIGINS` and redeploy
the API.

## 6. Seed the demo data

```bash
# seed_hub POSTs samples to an API base URL; point it at the deployed API.
python -m backend.tools.seed_hub "$API_URL"
```

Because the API has the real `ANTHROPIC_API_KEY`, the PDF-backed samples extract
real metadata — Vendor / Sponsor / Study populate (the failure mode that this
key fixes locally; see RUNBOOK).

## Cost & ops notes

- Cloud Run scales to zero — idle cost is ~the Cloud SQL instance only. Use
  `db-f1-micro` for demo; size up for real load.
- Set `--min-instances=0` (demo) or `1` (avoid cold starts) on the API.
- Logs: `gcloud run services logs read invoicescreener-api --region "$REGION"`.
- Cloud SQL is **not** scale-to-zero; stop the instance when idle to save cost.

## Deployment record (2026-05-30, project `ledgerrun-1`, region `us-central1`)

Live services (all `--allow-unauthenticated`):

| Service | URL |
| --- | --- |
| Hub | https://invoicescreener-hub-o27sizgahq-uc.a.run.app |
| API | https://invoicescreener-api-o27sizgahq-uc.a.run.app |
| mcp-reference (stub) | https://invoicescreener-mcp-o27sizgahq-uc.a.run.app |
| mock-clinrun (stub) | https://invoicescreener-clinrun-o27sizgahq-uc.a.run.app |

- **Cloud SQL**: `invoicescreener-db` (PostgreSQL **15** — this gcloud maxes at 15),
  `db-f1-micro`. Connected via the `/cloudsql/...` socket.
- **Secret Manager**: `ledgerrun-anthropic-api-key`, `ledgerrun-database-url`,
  `ledgerrun-db-password`. The API reads `DATABASE_URL` from Secret Manager.
- **Images**: built on Cloud Build (`cloudbuild.backend.yaml`,
  `cloudbuild.hub.yaml`) → Artifact Registry repo `invoicescreener`. Backend
  `Dockerfile` now honors `$PORT`; hub uses `frontend/Dockerfile.prod` (Vite
  build → nginx on 8080, `VITE_API_URL` baked at build time).
- Seeded 4 invoices via JSON `document` samples (the cloud API can't read local
  PDF paths, so no `seed_hub`/page images — fields still populate).

### ⚠ Known limitation: outbound egress to api.anthropic.com is blocked

The API is deployed **offline** (no `ANTHROPIC_API_KEY` bound at runtime) because
this project's Cloud Run egress cannot reach `api.anthropic.com` — extraction via
the real provider failed with `APIConnectionError: "Connection error."`, while
calls to the Google-hosted stubs (`*.run.app`) and Cloud SQL succeed. The
offline `LayoutLLMClient`/`PassthroughLLMClient` path handles the controlled
samples, so the demo works. The key remains in Secret Manager, ready to bind
(`--set-secrets ANTHROPIC_API_KEY=ledgerrun-anthropic-api-key:latest`) once
egress exists. To enable the real provider: add a Serverless VPC Access connector
+ Cloud NAT (static outbound) and set `--vpc-egress=all-traffic`.

## Follow-ups / open decisions

- [x] Code change: API container honors `$PORT` (done — shell-form CMD).
- [x] Production frontend build + hosting (done — `frontend/Dockerfile.prod`
      + nginx, deployed as the `hub` Cloud Run service).
- [ ] **Enable real-provider egress** (VPC connector + Cloud NAT), then bind the
      Anthropic secret on the API. Until then extraction is offline.
- [ ] Replace the `mcp-reference` / `mock-clinrun` stubs with real integrations.
- [ ] Lock down stub-service auth (currently public per demo decision).
- [ ] CI/CD: wire the Cloud Build configs into a trigger instead of manual deploys.
- [ ] Dedicated runtime service account (least privilege) instead of the default
      compute SA.

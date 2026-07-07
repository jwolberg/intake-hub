# Deploy — Google Cloud (Cloud Run)

**Status: DRAFT.** First-pass deployment guide for getting IntakeHub onto
Google Cloud. It targets **Cloud Run** for the containers and **Cloud SQL for
PostgreSQL** for persistence. For local setup see [`RUNBOOK.md`](./RUNBOOK.md);
for structure see [`ARCHITECTURE.md`](./ARCHITECTURE.md).

> This describes a **demo / staging** deployment of the solopreneur-ledger
> pivot. There is no internal reference/submission microservice to deploy —
> unlike the tool's clinical-trial-invoice predecessor (whose `mcp-reference`
> and `mock-clinrun` stub services this record used to describe), filed items
> go straight to a real Google Sheet and receipts arrive from a real Drive
> folder or Gmail mailbox (or their offline stubs). The API is the only
> backend service.

## Why Cloud Run

The backend is a stateless HTTP container that applies its own DB schema on
startup (`init_schema`) — no separate migration step, no local disk, scales to
zero. That is exactly Cloud Run's sweet spot. Cloud SQL holds the only durable
state. Secrets (the Anthropic key, the Gmail OAuth client, the Sheets/Drive
service-account key) come from Secret Manager. The alternative (GKE) is more
machinery than this MVP needs.

| Local (Compose) | Cloud |
| --- | --- |
| `db` (postgres:16) | Cloud SQL for PostgreSQL 16 |
| `api` (FastAPI) | Cloud Run service `intakehub-api` |
| `hub` (Vite dev server) | static build served by Cloud Run / Firebase Hosting |
| `poller` (drive/gmail profile) | Cloud Scheduler job (§7) |
| `ANTHROPIC_API_KEY` / `GMAIL_*` / `GOOGLE_APPLICATION_CREDENTIALS` (Keychain / `.env`) | Secret Manager → env vars / secrets |

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

These are the only spots where the current container assumes the local topology.
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
   hub against the deployed API URL (see §6).

## 1. Artifact Registry (image host)

```bash
gcloud artifacts repositories create intakehub \
  --repository-format=docker --location="$REGION"
export IMG="$REGION-docker.pkg.dev/$PROJECT_ID/intakehub"
```

## 2. Cloud SQL (PostgreSQL)

```bash
gcloud sql instances create intakehub-db \
  --database-version=POSTGRES_16 --tier=db-f1-micro --region="$REGION"

gcloud sql databases create intakehub --instance=intakehub-db
gcloud sql users create intakehub \
  --instance=intakehub-db --password='CHANGE_ME_STRONG'

# Connection name is PROJECT:REGION:INSTANCE — used by the Cloud Run socket mount.
export INSTANCE_CONN="$(gcloud sql instances describe intakehub-db \
  --format='value(connectionName)')"
```

Cloud Run connects to Cloud SQL over a Unix socket at
`/cloudsql/$INSTANCE_CONN`. The SQLAlchemy/psycopg DSN uses `host=` for that
socket (no TCP host/port):

```
DATABASE_URL=postgresql+psycopg://intakehub:CHANGE_ME_STRONG@/intakehub?host=/cloudsql/PROJECT:REGION:INSTANCE
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

> **Read this before binding the key on the API service:** this project's
> Cloud Run egress cannot currently reach `api.anthropic.com` — see
> [Known limitation](#known-limitation-outbound-egress-to-apianthropiccom-is-blocked)
> below. Binding the key is safe regardless (the client degrades to the
> offline path via `FallbackLLMClient`), but extraction stays offline until a
> VPC connector + Cloud NAT follow-up lands.

## 4. Gmail & Sheets secrets → Secret Manager

Optional, only needed to enable `INBOX_PROVIDER=gmail` and/or a real (non-stub)
Sheets ledger in the cloud. Full setup (creating the OAuth client, the consent
screen, the Sheets service account, and minting the refresh token) is in
[`gmail-sheets-setup.md`](./gmail-sheets-setup.md) — this section is only the
cloud-secrets wiring on top of that.

**Gmail** (`GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` / `GMAIL_REFRESH_TOKEN`):

```bash
printf '%s' "$GMAIL_CLIENT_SECRET_VALUE" \
  | gcloud secrets create ledgerrun-gmail-client-secret --data-file=-
printf '%s' "$GMAIL_REFRESH_TOKEN_VALUE" \
  | gcloud secrets create ledgerrun-gmail-refresh-token --data-file=-
gcloud secrets add-iam-policy-binding ledgerrun-gmail-client-secret \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor
gcloud secrets add-iam-policy-binding ledgerrun-gmail-refresh-token \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor
```

`GMAIL_CLIENT_ID` is not secret (it's a public OAuth client identifier) and can
go directly in `--set-env-vars`; `GMAIL_TOKEN_ENC_KEY` (the Fernet key
encrypting the refresh token at rest in Postgres) should also be a secret if
set. **Keep the OAuth consent screen in single-user "In production" mode** —
not "Testing" (refresh tokens expire after 7 days there) and not public
(triggers restricted-scope verification / CASA). This is a hard requirement,
not a convenience default: a Testing-mode token silently stops working a week
after minting it, which would look like a production outage.

**Sheets / Drive service account** (`GOOGLE_APPLICATION_CREDENTIALS`):

```bash
gcloud secrets create ledgerrun-google-sa-key --data-file=/path/to/sa-key.json
gcloud secrets add-iam-policy-binding ledgerrun-google-sa-key \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor
```

Mount it as a file at deploy time (`--set-secrets
GOOGLE_APPLICATION_CREDENTIALS=/secrets/google-sa-key.json:ledgerrun-google-sa-key:latest`
— Cloud Run mounts secrets as files, and the app treats a value starting with
`/` as a file path, `{` as inline JSON). `SHEETS_SPREADSHEET_ID` is not secret
and goes in `--set-env-vars`.

## 5. Build & deploy the API

```bash
gcloud builds submit --tag "$IMG/backend:latest" .

gcloud run deploy intakehub-api \
  --image "$IMG/backend:latest" --region "$REGION" \
  --allow-unauthenticated \
  --add-cloudsql-instances "$INSTANCE_CONN" \
  --set-secrets "ANTHROPIC_API_KEY=ledgerrun-anthropic-api-key:latest" \
  --set-env-vars "^@^DATABASE_URL=postgresql+psycopg://intakehub:CHANGE_ME_STRONG@/intakehub?host=/cloudsql/$INSTANCE_CONN@LLM_MODEL=claude-opus-4-7"

export API_URL="$(gcloud run services describe intakehub-api \
  --region "$REGION" --format='value(status.url)')"
curl "$API_URL/health"   # expect {"status":"ok",...,"db":"up"}
```

Notes:
- `--set-env-vars` uses the `^@^` delimiter trick because `DATABASE_URL` itself
  contains commas; `@` then separates the vars.
- `CORS_ORIGINS` must include the hub's deployed origin once §6 is done
  (add it to `--set-env-vars`), or the browser hub will be blocked by CORS.
- The schema is created on first startup by `init_schema` — no migration job.
- **Google Drive folder intake (optional).** To read invoices from a watched
  Drive folder instead of the mock set, add `INBOX_PROVIDER=drive` and
  `DRIVE_FOLDER_ID=<id>` to `--set-env-vars`, and provide the service-account
  key per §4 (`GOOGLE_APPLICATION_CREDENTIALS`). The local ephemeral download
  path needs no volume (KTD5). Full setup:
  [`drive-intake-setup.md`](./drive-intake-setup.md).
- **Gmail mailbox intake (optional).** Add `INBOX_PROVIDER=gmail`,
  `GMAIL_CLIENT_ID=<id>` (env var, not secret), and mount the two Gmail
  secrets from §4 (`GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` via
  `--set-secrets`). Optionally `GMAIL_TOKEN_ENC_KEY` (secret) and
  `GMAIL_TAX_YEAR` (env var).
- **Sheets ledger output (optional; offline stub otherwise).** Add
  `SHEETS_SPREADSHEET_ID=<id>` and the service-account credential per §4. With
  neither set, filed items still record in Postgres via the offline
  `StubSheetsClient` — no Sheet is written.

## 6. The reviewer hub (frontend)

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
intakehub-hub`. (The existing `frontend/Dockerfile` is dev-only — do not
ship it.)

After the hub is live, add its origin to the API's `CORS_ORIGINS` and redeploy
the API.

## 7. Seed the demo data

```bash
# seed_hub POSTs samples to an API base URL; point it at the deployed API.
python -m backend.tools.seed_hub "$API_URL"
```

Because the API has the real `ANTHROPIC_API_KEY`, the PDF-backed samples
extract real metadata (the failure mode that this key fixes locally; see
RUNBOOK) — subject to the egress limitation below, which currently keeps the
deployed API on the offline extraction path regardless.

## 8. Drive/Gmail monitoring (Cloud Scheduler)

The local `poller` sidecar (`docker-compose.yml`, `drive`/`gmail` profiles) has
no Cloud Run equivalent — Cloud Run runs the *web* service, not a background
loop. Use **Cloud Scheduler** as the poller: a cron job that POSTs the fetch
route on an interval, provider-agnostic (it drives whichever `InboxClient` the
API is configured with — Drive or Gmail). First deploy the API with the
relevant provider's vars/secrets (§5 notes), then:

```bash
# Every 5 minutes, ask the API to pull + process any new receipts.
gcloud scheduler jobs create http intakehub-inbox-poll \
  --location "$REGION" \
  --schedule "*/5 * * * *" \
  --uri "$API_URL/api/inbox/fetch" \
  --http-method POST
```

- The interval is the cron schedule here (not `INBOX_POLL_INTERVAL`, which only
  drives the local sidecar). `*/5 * * * *` = every 5 min; tighten or loosen to
  taste. Each run is idempotent — already-seen messages/files are skipped.
- The API above is `--allow-unauthenticated`, so no auth is needed. If you lock
  the API down (`--no-allow-unauthenticated`), add OIDC to the job:
  `--oidc-service-account-email=<SA> --oidc-token-audience="$API_URL"` and grant
  that SA `roles/run.invoker`.
- Cloud Run scales to zero between ticks; the scheduled POST cold-starts it, polls,
  and it idles back down — so monitoring adds ~one invocation per interval, not a
  standing instance.

## Cost & ops notes

- Cloud Run scales to zero — idle cost is ~the Cloud SQL instance only. Use
  `db-f1-micro` for demo; size up for real load.
- Set `--min-instances=0` (demo) or `1` (avoid cold starts) on the API.
- Logs: `gcloud run services logs read intakehub-api --region "$REGION"`.
- Cloud SQL is **not** scale-to-zero; stop the instance when idle to save cost.

## Deployment record (2026-05-30, project `ledgerrun-1`, region `us-central1`)

> **This record predates the solopreneur-ledger pivot.** It describes the
> clinical-trial-invoice tool's deployment, including two stub services
> (`mcp-reference`, `mock-clinrun`) whose code (`backend/clients/stub_servers/*`,
> the `context`/`catalog`/`matching`/`submission` modules) has since been
> deleted from this repository. It is kept as a historical record of what was
> live at the time, not as instructions to follow — §1-§8 above reflect the
> current (post-pivot) deploy shape. A fresh deploy under the current code
> deploys only `intakehub-api` and `intakehub-hub`.

Live services (all `--allow-unauthenticated`). **Note:** this record predates the
rename to `intakehub`; the services were originally deployed as `invoicescreener-*`
(hash `o27sizgahq`). Redeploying under the `intakehub-*` names below regenerates a
**new** URL hash per service — the `<hash>` placeholders resolve only after the
next deploy.

| Service | URL |
| --- | --- |
| Hub | https://intakehub-hub-&lt;hash&gt;-uc.a.run.app |
| API | https://intakehub-api-&lt;hash&gt;-uc.a.run.app |
| mcp-reference (stub, removed) | https://intakehub-mcp-&lt;hash&gt;-uc.a.run.app |
| mock-clinrun (stub, removed) | https://intakehub-clinrun-&lt;hash&gt;-uc.a.run.app |

- **Cloud SQL**: `intakehub-db` (PostgreSQL **15** — this gcloud maxes at 15),
  `db-f1-micro`. Connected via the `/cloudsql/...` socket.
- **Secret Manager**: `ledgerrun-anthropic-api-key`, `ledgerrun-database-url`,
  `ledgerrun-db-password`. The API reads `DATABASE_URL` from Secret Manager.
- **Images**: built on Cloud Build (`cloudbuild.backend.yaml`,
  `cloudbuild.hub.yaml`) → Artifact Registry repo `intakehub`. Backend
  `Dockerfile` now honors `$PORT`; hub uses `frontend/Dockerfile.prod` (Vite
  build → nginx on 8080, `VITE_API_URL` baked at build time).
- Seeded 4 PDF-backed invoices via `python -m backend.tools.seed_cloud <API_URL>`,
  which posts each PDF **inline as base64** (`source.attachment_b64`) since the
  cloud API can't read local paths (P5-T3). The API persists the bytes
  (`invoices.source_pdf`), so the cloud hub has real source documents: page-image
  preview *and* "Open original PDF" download. (Source-anchored highlight overlay
  boxes remain dev-only — OCR still reads a file path; the cloud shows the page
  image without boxes plus the raw PDF.)

### Known limitation: outbound egress to api.anthropic.com is blocked

The API is deployed **offline** (no `ANTHROPIC_API_KEY` bound at runtime) because
this project's Cloud Run egress cannot reach `api.anthropic.com` — extraction via
the real provider failed with `APIConnectionError: "Connection error."`, while
calls to Google-hosted APIs (Sheets, Drive, Gmail, the former `*.run.app` stubs)
and Cloud SQL succeed. The offline `LayoutLLMClient`/`PassthroughLLMClient` path
handles the controlled samples, so the demo works. The key remains in Secret
Manager, ready to bind
(`--set-secrets ANTHROPIC_API_KEY=ledgerrun-anthropic-api-key:latest`) once
egress exists. To enable the real provider: add a Serverless VPC Access connector
+ Cloud NAT (static outbound) and set `--vpc-egress=all-traffic`.

This limitation is specific to the Anthropic API, not Google's — it does not
affect Sheets/Drive/Gmail, which are Google-hosted and already proven reachable
from this project's default egress (the former `mcp-reference`/`mock-clinrun`
stubs, and the current Sheets/Drive/Gmail clients, all go over the same path).
So the pivot's inbox and ledger-output paths are unaffected by this limitation;
only LLM-derived extraction/classification/categorization degrade to the
offline stand-in until the VPC/NAT follow-up lands.

**Re-test 2026-06-04 — failure reproduced, diagnosis confirmed and refined.**
Bound the secret to a fresh revision and posted one PDF sample to
`/api/invoices/process`. The invoice came back `status: failed` after ~40s (the
anthropic SDK retrying the connection), with a stored exception
`extraction_failed / "Connection error."` — same failure as the original deploy.
So the block is **structural, not transient**. Refinements to the original note:

- It is **not** an org policy. The project has **0** org policies set and
  `constraints/run.allowedVPCEgress` has no active rule. The service uses Cloud
  Run **default egress** (no VPC connector / no `vpc-egress` annotation), which
  normally reaches the public internet — yet this service cannot reach Anthropic.
  The reliable fix is still the **Serverless VPC connector + Cloud NAT** path
  (deterministic, static outbound), which is now evidence-justified rather than
  speculative. Note: adding `--vpc-egress=all-traffic` *without* a Cloud NAT would
  remove default internet egress and make this worse — the NAT is required.
- Binding the key without working egress is a **regression**: with the key bound
  and the API unreachable, every new invoice hard-failed (`status: failed`)
  instead of using the offline path. The re-test was rolled back
  (`--remove-secrets ANTHROPIC_API_KEY`, revision `…-00003-jib`) to restore the
  working offline behavior.
- **Mitigation shipped (`backend/clients`):** the live provider is now wrapped in
  `FallbackLLMClient`, which degrades to the offline `PassthroughLLMClient` on a
  connection error (logging a warning) instead of failing the invoice. A bound
  key can therefore no longer break production — once egress exists, the same
  bound key produces real model-derived extraction with no further change. This
  is also why binding the key is safe for the pivot's `categorize`/`receipts`
  LLM calls (§4, §9 of ARCHITECTURE.md) — an unreachable provider degrades those
  to their heuristic fallback rather than failing the item.

## Follow-ups / open decisions

- [x] Code change: API container honors `$PORT` (done — shell-form CMD).
- [x] Production frontend build + hosting (done — `frontend/Dockerfile.prod`
      + nginx, deployed as the `hub` Cloud Run service).
- [x] Removed the `mcp-reference` / `mock-clinrun` stub services and their
      Cloud Run deploy steps (solopreneur-ledger pivot; the pipeline no longer
      has an internal reference/submission stage to stub).
- [ ] **Enable real-provider egress** (VPC connector + Cloud NAT), then bind the
      Anthropic secret on the API. Until then extraction/classification/
      categorization are offline. (Re-tested 2026-06-04: failure reproduced —
      see Known limitation above. Binding the key is now safe regardless,
      thanks to `FallbackLLMClient`, but these stages stay offline until
      egress exists.)
- [ ] **Drive/Gmail monitoring in the cloud**: create the Cloud Scheduler job
      (§8) once the API is deployed with `INBOX_PROVIDER=drive` or `=gmail` +
      the relevant secrets (§4). (Local Compose already monitors via the
      `poller` sidecar / `drive`+`gmail` profiles.)
- [ ] **Sheets ledger in the cloud**: bind `SHEETS_SPREADSHEET_ID` + the
      service-account secret (§4, §5) once a target spreadsheet exists; until
      then filed items record in Postgres only (offline `StubSheetsClient`).
- [ ] CI/CD: wire the Cloud Build configs into a trigger instead of manual deploys.
- [ ] Dedicated runtime service account (least privilege) instead of the default
      compute SA.

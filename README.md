# userChatBackend

Backup API for a public chat page. The page calls this service, this service asks Sarah for the answer, and page, user, and chat details are stored in Supabase.

```
Chat page --HTTPS/JSON--> userChatBackend --HTTPS/JSON--> Sarah
                              |
                              +--> PostgreSQL (Supabase)
```

## Endpoints

| Method | Path | When the page calls it |
| --- | --- | --- |
| `POST` | `/initialize` | Page load. Stores the user, page, and visit, and returns a session token. |
| `POST` | `/get-answers` | The user asks a question. Sarah is called immediately and the exchange is stored. |
| `POST` | `/page_exit` | The user leaves. Closes the visit and stores the duration. |
| `GET` or `POST` | `/analytics` | Placeholder. Returns `501` until reporting is added. |
| `GET` | `/health` | Cloud Run health check. No authentication. |

`page_url`, `asset_id`, or `asset_name` is required. `user_id`, `page_id`, `page_title`, `session_id`, `asset_version`, and `sarahAuthCode` are optional. A request with no `user_id` is stored without a row in `users`. A failed store returns `{ "status": "ERROR", "errorMessage": "..." }`.

Sarah failures use HTTP 200 so the page can always read the JSON body:

```json
{ "answer": "...", "status": "SUCCESS" }
{ "answer": null, "status": "TIMEOUT", "errorMessage": "The answer service timed out" }
{ "status": "ERROR", "errorMessage": "..." }
```

`status` is `SUCCESS`, `TIMEOUT`, or `ERROR`.

## Authentication

The page is public and must not store a password. Two credentials are used:

1. **Widget key** (`X-Widget-Key`). This is a publishable application key, like a site key. The page sends it on every request. `navigator.sendBeacon` cannot set headers, so `/page_exit` also accepts `widget_key` in the JSON body.
2. **Session token** (`X-Session-Token`). `/initialize` returns this. `/get-answers` and `/page_exit` require it. The token is signed with `AUTH_SIGNING_SECRET`, which never goes to the browser. It is bound to the user, session, and page, and it expires. An expired token can still close a visit.

The server also checks the browser `Origin` against `AUTH_ALLOWED_ORIGINS` and rate-limits by IP.

On Cloud Run, deploy with `--allow-unauthenticated` so the browser can reach the service. Google IAM is not the page login. The widget key, origin list, and session token are.

## Page integration

```javascript
const API = "https://your-service.example";
const WIDGET_KEY = "publishable-widget-key";

const started = await fetch(`${API}/initialize`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-Widget-Key": WIDGET_KEY,
  },
  body: JSON.stringify({
    user_id: "user-1",
    page_id: "page-1",
    page_url: location.href,
    page_title: document.title,
    session_id: crypto.randomUUID(),
    asset_id: "asset-1",
    asset_type: "pdf",
    asset_name: "Helix_Embed_FAQ.pdf",
    asset_version: "1.0",
  }),
}).then((response) => response.json());

// Keep started.session_token, started.session_id, and started.page_id in memory.

const answered = await fetch(`${API}/get-answers`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-Widget-Key": WIDGET_KEY,
    "X-Session-Token": started.session_token,
  },
  body: JSON.stringify({
    user_id: "user-1",
    page_id: started.page_id,
    session_id: started.session_id,
    page_url: location.href,
    asset_id: "asset-1",
    asset_name: "Helix_Embed_FAQ.pdf",
    sarahAuthCode: "your-sarah-auth-code",
    question: "What is Helix?",
  }),
}).then((response) => response.json());

document.addEventListener("pagehide", () => {
  fetch(`${API}/page_exit`, {
    method: "POST",
    keepalive: true,
    headers: {
      "Content-Type": "application/json",
      "X-Widget-Key": WIDGET_KEY,
      "X-Session-Token": started.session_token,
    },
    body: JSON.stringify({
      user_id: "user-1",
      page_id: started.page_id,
      session_id: started.session_id,
      page_url: location.href,
      asset_name: "Helix_Embed_FAQ.pdf",
    }),
  });
});
```

Send the `page_id` returned by `/initialize` on later calls. If you omit `page_id`, the same `page_url` (ignoring one trailing slash) maps to the same page, then `asset_id`, then `asset_name`. Omit `session_id` on later calls and the session token supplies it. Omit `sarahAuthCode` and the configured default is used.

## Storage

For a new database, run `sql/001_schema.sql` in the Supabase SQL editor. If the tables already exist, also run `sql/002_asset_fields.sql` and `sql/003_asset_version.sql`. With `DATABASE_AUTO_MIGRATE=true`, startup runs every file in `sql/`. The scripts create `users`, `pages`, `page_visits`, and `chat_messages`.

Row level security is enabled with no policies, so the public anon key cannot read these tables. Set `DATABASE_HOST`, `DATABASE_USER`, `DATABASE_PASSWORD`, `DATABASE_PORT`, and `DATABASE_NAME` from the Supabase session pooler (IPv4). The password is the database password, not the anon key, and it is not URL-encoded. Port `5432` is the session pooler and port `6543` is the transaction pooler. `DATABASE_URL` still works when the separate fields are left empty.

Each stored event uses a stable id, so a retry cannot insert a duplicate visit or message. Writes go through a queue with a local write-ahead log, backoff, and a dead-letter file. By default the HTTP request stays open until Postgres accepts the row. That matters on Cloud Run, which freezes CPU after the response is sent. If Sarah succeeded and the database write ultimately fails, the answer is still included, with `status` `ERROR` and `errorMessage` set. The full payload is logged as `persistence_dead_letter` and appended to the dead-letter file.

Set `PERSISTENCE_WAIT_FOR_COMPLETION=false` only if the Cloud Run service uses CPU always allocated (`--no-cpu-throttling`). A hard instance kill can still drop a write that has not reached Postgres. The write-ahead log is replayed only on the same instance disk.

## Configuration

Non-secret defaults live in `config/settings.yaml`. Secrets and environment-specific values come from the environment. Copy `.env.example` to `.env` for local runs.

| Variable | Purpose |
| --- | --- |
| `WIDGET_KEYS` | Comma-separated publishable keys |
| `AUTH_SIGNING_SECRET` | Signs session tokens. Server only |
| `AUTH_ALLOWED_ORIGINS` | Comma-separated page origins. Avoid `*` in production |
| `AUTH_ALLOW_MISSING_ORIGIN` | Allow curl and other clients that send no `Origin` |
| `DATABASE_HOST` | Pooler host, for example `aws-0-eu-west-1.pooler.supabase.com` |
| `DATABASE_PORT` | `5432` session pooler, or `6543` transaction pooler |
| `DATABASE_USER` | `postgres.<project ref>` |
| `DATABASE_PASSWORD` | Database password, raw, not URL-encoded |
| `DATABASE_NAME` | Database name, usually `postgres` |
| `DATABASE_URL` | Optional connection string when the fields above are empty |
| `DATABASE_ENABLED` | `false` keeps events in memory. Used by tests |
| `DATABASE_AUTO_MIGRATE` | Apply `sql/001_schema.sql` on startup |
| `SARAH_URL_TEMPLATE` | Must contain `{sarah_auth_code}` |
| `SARAH_DEFAULT_AUTH_CODE` | Used when the page omits `sarahAuthCode` |
| `SARAH_ALLOWED_AUTH_CODES` | If set, only these codes can be proxied |
| `SARAH_TIMEOUT_SECONDS` | Sarah call timeout. Timeouts are not retried |
| `PERSISTENCE_WAIT_FOR_COMPLETION` | Wait for Postgres before responding |
| `PERSISTENCE_WAL_PATH` | Write-ahead log path. `/tmp` on Cloud Run |
| `LOG_LEVEL` | Python log level |

Sarah's URL is `https://staging.cosellus.ai/api/v1/public/hclp/{sarah_auth_code}/sarah/ask`. The request body is `{ "question": "..." }`. The answer returned to the page is `answer_text`. The full Sarah response is stored in `chat_messages.sarah_response`.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env
# Fill in .env, then apply sql/001_schema.sql in Supabase.
uvicorn app.main:app --reload --port 8080
pytest
```

Interactive docs are at `http://localhost:8080/docs`.

## Cloud Run

```powershell
gcloud run deploy user-chat-backend --source . --region us-central1 --allow-unauthenticated --timeout 60 --set-env-vars "WIDGET_KEYS=...,AUTH_SIGNING_SECRET=...,DATABASE_HOST=...,DATABASE_PORT=5432,DATABASE_USER=postgres.PROJECT_REF,DATABASE_PASSWORD=...,DATABASE_NAME=postgres,SARAH_DEFAULT_AUTH_CODE=...,SARAH_ALLOWED_AUTH_CODES=...,AUTH_ALLOWED_ORIGINS=https://your-site.example"
```

The container listens on `$PORT`. Give the request timeout enough room for the Sarah call (default 30 seconds) plus the database write.

# AI Tender Intelligence

A working Bulgarian procurement-analysis Beta connecting **Telegram → official TED/CAIS EOP discovery → PostgreSQL → document extraction and evidence retrieval → Sol analysis → Terra synthesis → HTML email → Gmail → Telegram confirmation**.

The complete workflow has passed a controlled live end-to-end demonstration, including actual Gmail submission. That demonstrates integration functionality, not independently verified tender-analysis accuracy.

## What the application does

- Self-service registration through a private Telegram chat, identified by Telegram user ID rather than username.
- A deliberately small Beta: **three registered accounts total**, **one analysis per account**, and **one concurrent processing slot**, enforced in PostgreSQL. Existing accounts count toward capacity; duplicate registration does not consume another slot.
- Separate company profiles, confirmed report-email settings, official CPV classification, private search snapshots, analysis jobs and report history.
- Official-source discovery with direct/related/broader relevance labels, source-backed location, up to 15 results and five-result pages. Published search status is not a guarantee of current bidding availability: analysis admission independently verifies supported official OPEN evidence.
- Bounded acquisition and extraction, source hashes and provenance, deterministic evidence retrieval, exact citations and applicability metadata.
- Luna High for CPV classification; up to eight **Sol 6.1 Medium** analysis calls and one conditional **Terra 6.1 Medium** synthesis call per analysis. Sol/Terra use a 100,000 output-token ceiling, not a generation target.
- Partial Beta findings, a short Bulgarian disclaimer, responsive single-column HTML/plain-text MIME and user-specific Gmail delivery.

## Design decisions and limitations

PostgreSQL transactions, ownership checks, immutable request bindings, durable dispatch/send reservations and a single background processing slot prevent duplicate work and cross-account access. Unknown charged/send outcomes are not automatically retried. Credentials remain server-side; spending is controlled through OpenAI Platform settings, with usage/cost estimates recorded for observation.

This is a learning Beta, not a procurement advisory or legal verification service:

- Advanced Azure document-extraction/OCR services are not integrated. Some PDF, DOCX and archive content can remain incomplete or unreadable.
- More powerful semantic models were not benchmarked, and there is no large representative independently human-verified evaluation dataset.
- **Final Terra semantic accuracy flags are diagnostic-only in Beta.** A structurally usable report can contain mistakes or omissions. Technical source/job integrity, authorization, recipient binding, response structure and one-use delivery controls remain blocking.
- Analysis accuracy, completeness, legal applicability and eligibility are not independently verified. Check the original documentation before making decisions.
- The server process must remain running, although the user can close Telegram. Crash or uncertain reservations require operator review. Gmail access tokens expire and headless mode renews them using the configured refresh token.

## Local setup

Requirements: Python 3.11+, PostgreSQL 17+ running locally, a Telegram bot, an OpenAI API account, and a Google Desktop OAuth client with Gmail API enabled. No paid call is needed to install, migrate or run offline tests.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -c constraints.txt -e ".[dev]"
```

Create a **new** local database (for example `tender_beta`) owned by your migration administrator. Create the restricted application login using your PostgreSQL administrator; set its password interactively, never in source control:

```sql
CREATE ROLE p5_search_runtime LOGIN;
-- In psql, use: \password p5_search_runtime
```

Set `DATABASE_URL` securely in the current process to the administrator connection URL, using SQLAlchemy's `postgresql+psycopg://` format. Set `P5_DB_SCOPE=beta`, then run:

```powershell
ai-tender-setup --apply
```

Setup applies the included independent migration streams, verifies/imports the bundled official CPV 2008 catalogue and installs Beta registration/quota controls. It does not start Telegram, call models or send email. Startup never migrates. Back up an existing database before an explicit upgrade; protected evaluation/document databases are not reset.

Replace `DATABASE_URL` with the **restricted runtime** connection URL before starting the application. The runtime role must not be a superuser or have database/role-creation privileges. On Windows, if native PostgreSQL DLL loading is restricted, use the existing approved PostgreSQL client installation on `PATH`; do not disable Windows security protections.

## Configuration and startup

See `.env.example` for names only; the application does **not** automatically load dotenv files. Keep real configuration outside the repository.

- `DATABASE_URL`: local runtime PostgreSQL connection URL.
- `P5_DB_SCOPE`: the same scope used during setup.
- `GMAIL_SENDER`: your authorized Gmail sending account.
- `GMAIL_OAUTH_MODE`: `desktop` by default. Use `headless` on Railway.
- `GOOGLE_CLIENT_JSON_PATH`: Google Desktop OAuth JSON outside this repository.
- `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`: supplied through the masked launcher or a secure server environment.

Readiness performs no public-service/model/Gmail calls:

```powershell
ai-tender-beta --check
```

After explicitly choosing to enable live paid processing and email delivery:

```powershell
python scripts/start_beta.py --enable-paid-beta
```

The launcher requests secrets with masked input and uses the existing send-only Google browser-consent flow. Never commit API keys, OAuth JSON/tokens, environment files, database dumps or reports. Without explicit paid-Beta activation the application cannot dispatch classification/analysis requests.

### Headless Gmail authorization

Keep the existing Desktop OAuth client in **External Testing** with Gmail API enabled and the configured `GMAIL_SENDER` listed as a test user. Shortly before the Beta, use a trusted local Windows desktop with the package installed. Set `GMAIL_SENDER` to that account, turn off clipboard history and cloud clipboard sync, and run:

```powershell
python scripts/authorize_gmail.py --client-json "PATH_TO_EXTERNAL_DESKTOP_CLIENT_JSON"
```

Consent as the configured sender. The helper requests offline access for `gmail.send`, requires Google to issue a refresh token, and copies that token to the clipboard without printing it. Paste directly into the Railway service variable `GOOGLE_OAUTH_REFRESH_TOKEN` and then clear the clipboard. Copy the matching client ID and secret from the same Desktop OAuth JSON into the other two deployment variables. Seal the client secret and refresh token in Railway and keep the JSON outside the repository and image. [Railway variable controls](https://docs.railway.com/variables#sealed-variables)

| Variable | Railway value |
| --- | --- |
| `GMAIL_OAUTH_MODE` | `headless` |
| `GMAIL_SENDER` | Gmail account that granted consent |
| `GOOGLE_OAUTH_CLIENT_ID` | Existing Desktop OAuth client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Existing Desktop OAuth client secret |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | Token issued to that client and sender |

The worker checks the refresh grant at paid startup and renews its in-memory access token before a send when it is nearing expiry. An expired, revoked or invalid refresh grant stops authorization with a sanitized error. Repeat local consent and replace the Railway secret if needed. Google's External Testing refresh tokens for Gmail expire after seven days from issuance. [Google's token-expiration policy](https://developers.google.com/identity/protocols/oauth2#expiration)

In Telegram: `/start` → create/confirm the business profile and email → wait for CPV classification → search → **Подробен анализ**. The first three registered accounts can onboard without administrator invitations. Each permitted request binds one job and one email to its confirmed address. A busy slot returns a clear response without consuming an analysis allowance; an accepted failed attempt is not automatically refunded or retried. Completion is announced only after Gmail confirms submission, which is distinct from verified inbox receipt.

## Tests and publication checks

```powershell
python -m pytest
```

PostgreSQL integration tests require an explicitly disposable database whose name starts with `p5_test_`. Set `P5_TEST_DATABASE_URL` to its administrator URL. These tests migrate/truncate **only that disposable database**; never point them at application data. Without this setting they are reported as skipped, not passed. All AI, source HTTP and Gmail responses in automated tests are synthetic/mocked; tests do not establish model accuracy.

```powershell
python scripts/check_public_tree.py
```

`PUBLIC_FILES.txt` is the explicit publication allowlist. Review that exact list before staging; `.gitignore` is only an additional defense. Runtime data, secrets, caches and local environment files are not publication candidates. No repository initialization, deployment or publishing is performed by the application.

## Continuous integration

Pushes to `main` and pull requests targeting `main` run `.github/workflows/ci.yml` on Ubuntu with Python 3.12 and an isolated PostgreSQL 17 service. CI builds a source distribution and wheel, installs the wheel with `constraints.txt`, checks packaged resources, scans pinned dependencies with pip-audit, and runs every offline and PostgreSQL test. The integration-result check rejects skips and fewer than 25 database tests. No real Telegram, OpenAI or Gmail credentials are needed.

CI also checks the explicit publication allowlist, tracked files, credential patterns and prohibited private artifacts. These checks reduce accidental disclosure; they do not guarantee that every possible secret or vulnerability can be detected. Verification tools are pinned in `requirements-ci.txt`. When updating dependencies, review both the project constraints and current advisory results; do not suppress findings without review.

For a local equivalent, install `requirements-ci.txt`, build with `python -m build`, run `python -m pip check`, and run pytest with the explicitly disposable database setting above. Use `python scripts/check_test_results.py postgres.xml --minimum 25` with pytest's PostgreSQL JUnit output to ensure integration tests were executed. `python scripts/check_public_tree.py --tracked` additionally requires the Git index to match `PUBLIC_FILES.txt` exactly after you manually stage reviewed changes.

## Docker

The Python 3.12 image installs constrained dependencies and the built application in a separate build stage. Its runtime runs as UID 10001 and includes PostgreSQL client libraries, migrations, prompts and the official CPV catalogue. The build context uses an explicit `.dockerignore` allowlist. No credentials or application data belong in the image.

```powershell
docker build --tag ai-tender-intelligence:local .
docker run --rm --network none ai-tender-intelligence:local --offline-check
python scripts/verify_docker.py --image ai-tender-intelligence:local
```

The verification script uses disposable containers and an internal-only network, checks actual HTTP liveness and graceful shutdown, initializes a separate PostgreSQL 17 database, and checks connectivity as the restricted runtime role. It never uses your existing database or service credentials. It removes only its own containers/network, including temporary PostgreSQL data. CI runs the same verification after the existing tests and security checks. Images are not pushed to a registry.

Supply configuration through the container environment or an environment file stored **outside** the repository; do not put secrets in command arguments or build arguments. `DATABASE_URL` must use `postgresql+psycopg://` and a restricted runtime login. Localhost remains accepted by default. For a Docker/private-network hostname, set `P5_DATABASE_HOST` to the **exact same hostname** in the URL. Only the optional `sslmode` URL query is accepted; unrelated connection overrides remain rejected. Keep `P5_DB_SCOPE` consistent with setup.

Default startup requires `DATABASE_URL` and `TELEGRAM_BOT_TOKEN`, performs read-only database readiness, and runs one Telegram process plus the existing FastAPI liveness server. Paid processing requires `--enable-paid-beta`, `OPENAI_API_KEY`, `GMAIL_SENDER` and the credentials for the selected Gmail authorization mode. Desktop mode uses `GOOGLE_CLIENT_JSON_PATH` and local browser consent. Headless mode uses the three `GOOGLE_OAUTH_*` variables above.

Container command modes:

- `--offline-check`: installed imports/resources only; no network.
- `--check`: read-only database/schema/permissions readiness; no Telegram/model/Gmail calls.
- `--setup`: explicit administrator migration/catalogue setup; never run automatically at startup. Create the restricted `p5_search_runtime` role first, as above.
- `--health-only`: liveness server only, for isolated checks; it is **not** a running bot or a demonstration of integration readiness.
- No flags: normal bot plus liveness. Optional `--enable-paid-beta` retains existing authorization, quotas and durable no-replay controls.

For normal startup, provide your external environment file to `docker run`, map the configured port and use `--stop-timeout 35`. The launcher gives children up to 25 seconds to close before force-stopping; use `docker logs` to inspect sanitized process output. There is no automatic application restart, model retry or Gmail resend. Longer active analyses can outlive this shutdown window and require manual review of durable records; an interrupted attempt must not be replayed.

## Railway preparation and current blockers

No Railway configuration is activated or deployed. A future deployment would use the Dockerfile's default entrypoint (append `--enable-paid-beta` only after explicit approval), one replica, `PORT` supplied by Railway, and health path `/health/live`. The server binds `0.0.0.0:$PORT`. This endpoint reports process liveness, not database/source/model/Gmail readiness; normal bot startup first checks database readiness, and unexpected child exit stops the whole container. See [Railway health-check documentation](https://docs.railway.com/deployments/healthchecks).

Required future configuration: persistent PostgreSQL 17, restricted `DATABASE_URL`, exact `P5_DATABASE_HOST`, stable `P5_DB_SCOPE`, and `TELEGRAM_BOT_TOKEN`. Paid mode additionally needs the variables listed above. Keep administrator credentials separate and run schema setup explicitly before switching to the restricted role. Configure TLS through `sslmode` where required. Do not attach an ephemeral application container's filesystem as the only database storage.

**Deployment prerequisites:** complete the one-time local Gmail consent shortly before the Beta and configure the Railway secrets above. PostgreSQL provisioning, persistence and backups, explicit migration execution, and platform shutdown behavior still need deployment configuration. Schedule restarts while idle. Interrupted analyses retain their durable reservations and require manual review, including any uncertain send outcome.

The optional FastAPI liveness endpoint (`ai_tender_intelligence.api:create_app`, factory mode) reports process health only, not database/integration readiness. Remote deployment is separate from CI and is not performed by this workflow.

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
- The server process must remain running, although the user can close Telegram. Crash/uncertain reservations require operator review; there is no automatic paid replay. Gmail consent is currently memory-only and expires; renewed consent may be necessary.

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

The optional FastAPI liveness endpoint (`ai_tender_intelligence.api:create_app`, factory mode) reports process health only, not database/integration readiness. CI/CD and remote deployment are intentionally separate operational work.

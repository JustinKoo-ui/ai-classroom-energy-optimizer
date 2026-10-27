# Runtime, estimated energy and optional AI commentary

Initial repository findings: [analytics-audit.md](analytics-audit.md).
The feature is read-only with respect to device control. Firmware, GPIOs,
topics, camera processing, control policy, manual mode and urgency are preserved.

## Installation

Fresh databases use `database/schema.sql`. Existing databases with migrations
002 and 003 need migration **004 before restarting the backend**:

```powershell
Get-Content database\migrations\004_runtime_history.sql |
  docker exec -i aceo-postgres psql -U aceo -d aceo
Set-Location backend
uv sync --locked --extra dev
uv run python -m uvicorn app.main:app --reload --port 8000
```

Use the existing broker/ingestion setup. Tracking runs without an open browser,
an AI key, or enabling physical controls. Live ESP32 state reports are required.
Start the frontend with `pnpm dev` as before. Analytics uses the configured
`NEXT_PUBLIC_API_BASE_URL` and classroom ID; its independent refresh is 30 seconds.
The existing dashboard's power/energy/AI demo panels remain simulated.

## Runtime source and accuracy

The three existing prototype logical relay IDs are tracked independently.
Yellow light, blue fan and red AC are analytics display colors only.
Firmware confirms its software output state, not independent physical load
operation. Requested commands are never treated as confirmed runtime.

`analytics_runtime_segments` compresses repeated live reports into same-state
evidence windows. It stores source, first/last observation/message IDs, mode,
quality and the freshness threshold used. State/mode/quality/threshold changes
or stale gaps start new rows. Per-device transaction locks serialize writes.
Retained, delayed, duplicate, out-of-order and excessive future observations
are ignored. Operational device persistence commits first; analytics uses a
separate transaction bounded to two seconds. Failure cannot undo that commit.

Reconstruction carries a reported state forward until the next observation or
the final observation's recorded freshness deadline, whichever comes first.
The default is 15 seconds. Beyond it, state is UNKNOWN. Faults and time before
tracking started are UNKNOWN, never OFF. Stored thresholds preserve historical
coverage if configuration changes. Restarts reuse durable evidence and leave
long gaps unknown. No earlier history is fabricated from sparse operational
rows, commands or demo data.

Snapshot timestamps identify publication, not exact switching. A transition
is assigned to the later snapshot and is uncertain within the preceding fresh
observation interval. Activations count observed OFF-to-ON changes; initial ON
and changes across UNKNOWN gaps cannot establish activations. Average/longest
ON runs are clipped to supported coverage within the report. Compressed
observation bounds may extend beyond a historical reporting boundary.

Classroom IANA timezone defines calendar days, Monday weeks and calendar
months; wire timestamps are UTC. Midnight/DST crossings are allocated by
elapsed time. Future hours are excluded. Coverage = (ON + OFF) / elapsed
seconds; ON percentage uses elapsed seconds including UNKNOWN. Incomplete
historical periods remain provisional even after the calendar period ends.

## Configurable mathematical model

Set backend values in `backend/.env` and restart:

```dotenv
ANALYTICS_STALE_AFTER_SECONDS=15
ANALYTICS_LIGHT_POWER_W=10
ANALYTICS_FAN_POWER_W=60
ANALYTICS_AC_POWER_W=1000
```

These are illustrative assumptions, not calibrated equipment specifications.
Changing watts recalculates estimates from the same recorded runtime. Each
response displays its assumptions; this prototype is not a versioned billing ledger.

- Estimated kWh = assumed W × estimated ON seconds / 3,600 / 1,000.
- Theoretical daily baseline = assumed W × **24** / 1,000.
- Period baseline = daily baseline × reporting calendar days.
- Theoretical difference = baseline − estimated consumption.
- Difference % = difference / baseline × 100, only when baseline > 0.

Defaults dynamically produce 0.24 + 1.44 + 24.00 = **25.68 kWh/day**.
Combined energy sums devices. UNKNOWN time is excluded from consumption;
partial estimates may understate usage. Provisional differences compare
observed usage with the full-period baseline, so missing/future usage is not
savings. `final_savings_pct` stays null until the period ended with full known
coverage. Even complete results are theoretical, not verified real-world or
causal savings. Negative results are preserved and flagged. The fixed baseline
remains 24 hours per calendar day even on 23/25-hour DST days.

No current ADC, mock power, meter-oriented energy table, electricity cost or
carbon factor supplies these calculations. Electricity is not measured.

## Optional provider setup

No AI settings are needed for runtime or energy. For commentary, configure an
HTTPS provider supporting strict JSON-schema Chat Completions. OpenAI is the
default endpoint; the endpoint, model and provider label are adjustable.
Select a Structured Outputs model available to your provider account:

```dotenv
ANALYST_PROVIDER=openai_compatible
ANALYST_API_URL=https://api.openai.com/v1/chat/completions
ANALYST_API_KEY=<provider key>
ANALYST_MODEL=<model supporting strict JSON schema>
ANALYST_ACCESS_TOKEN=<separate random report token>
ANALYST_TIMEOUT_SECONDS=20
ANALYST_MAX_RETRIES=1
ANALYST_REQUESTS_PER_MINUTE=3
ANALYST_CACHE_TTL_SECONDS=300
```

Generate a report token locally, e.g.
`uv run python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Enter that **report token** in the dashboard password field. It stays in React
memory for the page session. The provider key stays in backend environment
configuration. Neither secret belongs in `NEXT_PUBLIC_*` or source control.

Reports require a loopback client plus the separate Bearer token. This is the
existing local prototype boundary. Public deployment requires application
authentication/authorization rather than forwarding local routes publicly.

Only explicit Generate/Refresh requests call the provider. It receives
precomputed metrics, assumptions, coverage and bounded aggregated sensor,
camera-count and event statistics. No images, raw message stream, credentials,
command tools or control functions are supplied. Timely occupancy samples
require confidence ≥0.5; exclusions are disclosed. Statistics describe samples,
not occupied hours. Manual/urgency requests do not establish automation failure.
Existing environment storage cannot independently distinguish synthetic sensor
publishers; stored sensor statistics do not prove physical measurement provenance.
Daily reports include a previous-day summary when runtime coverage exists,
and withhold daily change calculations unless both periods are complete.

The adapter requests seven sections, strict JSON and `store:false`, with no
tools. It validates schema, response size, completion and the summary digest.
Server-owned disclosures accompany escaped React text. Numerical application
metrics remain authoritative. Failures produce scoped commentary messages.

Commentary leads with server-calculated reporting completeness and per-device
coverage from its own snapshot. The executive summary, device runtime paragraph
and energy paragraph are built from those exact metrics; model interpretation
is retained in the other sections. UNKNOWN seconds are listed separately for
each device, never presented as a combined elapsed duration.

For any unfinished or incompletely observed report, comparison percentages are
withheld from the provider-only summary, including historical context. They
are also withheld when the elapsed period has full coverage but the reporting
day/week/month is still in progress. The deterministic API calculations remain
unchanged. Complete reports may state the supplied theoretical difference;
none establishes measured, verified or causal savings. A conservative prose
guard replaces provider sections containing unsupported savings/comparison
claims with safe explanations while preserving useful commentary elsewhere.
Environmental humidity percentages are allowed. This is a bounded guard,
not general fact-checking of arbitrary model prose; numerical metrics remain
the source of truth. Commentary policy revisions use a new cache identity.

The website displays energy estimates with coverage, explains unavailable
final percentages, and groups detailed disclosures under an expandable
Methodology and limitations panel. Cached commentary retains the completeness
and timestamps of its original snapshot, even if live metrics have advanced.

Timeouts/retries are bounded. New generation is rate/concurrency limited;
cache hits preserve their original snapshot timestamp, which may precede live
metrics. Refresh bypasses the cache subject to rate limits. The bounded cache
and limits are process-local and reset on restart. Use one backend worker for
the local prototype, or introduce shared limits before scaling.

Provider reference: [official Chat Completions schema](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

### Groq configuration and live smoke test

For Groq, keep `ANALYST_PROVIDER=openai_compatible` and set:

```dotenv
ANALYST_API_URL=https://api.groq.com/openai/v1/chat/completions
ANALYST_MODEL=openai/gpt-oss-20b
ANALYST_API_KEY=<Groq API key>
ANALYST_ACCESS_TOKEN=<separate local report token>
```

From `backend`, run the opt-in check:

```powershell
uv run python scripts/check_groq_analyst.py --live --complete-day
```

It uses the real authenticated report handler and a live Groq HTTPS request,
with simulated database data. It verifies report-token enforcement, seven
validated report sections, the numerical snapshot, metrics-route availability
and caching. It does not start the operational app, access the database, publish
MQTT messages or operate hardware. Secrets and generated report prose are not
printed. Without `--live`, it sends no request. Live runs consume provider quota.

Groq free-tier token limits apply across requests, including repeated smoke tests.
If `analyst_status` is `rate_limited`, the key/token may still be valid: check
`retry_after_seconds`, wait that long, then retry. A successful local HTTP 200
envelope can contain a provider 429 result; `metrics_endpoint_available: true`
confirms that deterministic analytics remain available. Snapshot/cache checks
are null when no commentary was generated, rather than indicating corrupt metrics.

Reports compact adjacent identical hourly/daily buckets into runs with explicit
bucket counts and per-bucket metrics; numerical totals are unchanged. The adapter
does not immediately retry provider 429s. It preserves the numeric/HTTP-date
`Retry-After` header or Groq's stated cooldown, defaults to 60 seconds when absent,
and blocks new generations during that cooldown while allowing valid cache hits.

Verified on 26 October 2026: both a partial-day fixture (about 4.9 seconds) and
a full-day fixture (about 2.2 seconds) returned HTTP 200 and valid reports.
The full-day request used 6,667 prompt + 1,241 completion = 7,908 tokens.
An earlier provider generation omitted a required section and was correctly
rejected as unavailable; the subsequent attempt succeeded. Invalid provider
output is not allowed to replace deterministic metrics.

The backend URL in local configuration was corrected from OpenAI to Groq.
Restart any running backend after changing `.env`. The regular local service
was not listening on port 8000 during this verification, so the browser-to-live-
database workflow remains unverified. Existing 319 backend regressions passed.

After token-limit improvements on 26 October 2026, the same full-day fixture
returned HTTP 200 with all seven sections in about 2.35 seconds. Input tokens
fell from 6,667 to **2,475** (about 63%); output was 1,475 tokens, totaling 3,950.
Free-tier limits can still be reached when several reports share the same
minute. All 328 backend tests passed after the compaction/cooldown changes.

## API

- `GET /api/v1/classrooms/{id}/analytics?date=YYYY-MM-DD&period=day|week|month`
  is loopback-only. Date defaults to today in the classroom timezone. It
  returns bounds/coverage, per-device runtime/counts/hourly/daily history,
  powers, estimated energy, daily/period baselines and limitations.
- `POST /api/v1/classrooms/{id}/analytics/report` requires
  `Authorization: Bearer <report token>` and validated body
  `{"date":"2026-10-26","period":"day","refresh":false}`.
  Response status is `ready`, `unavailable` or `rate_limited`, with a message,
  snapshot/generation times, cache metadata, optional report sections,
  mandatory disclosures and retry delay. Unset server token returns 503;
  missing/wrong client token 401; unknown classroom 404; invalid selection 422.

Neither route can create commands or change automation.

## Validation and limits

Backend: `uv run --extra dev pytest -q`. Frontend: `pnpm exec tsc --noEmit`,
`pnpm lint`, `pnpm build`. Tests simulate timestamped observations and provider
HTTP responses, including gaps, restarts, repeated reports, midnight/DST,
energy formulas, missing data, auth, malformed output, failures, cache and limits.

These are not physical tests. A running PostgreSQL database/broker, actual
camera/ESP32 and provider credentials are needed for those integration checks.
No physical operation, measured energy or verified savings is claimed.

Implementation verification on 26 October 2026:

- Phase 1 gate: 222 backend tests passed before energy implementation.
- Phase 2 gate: 250 backend tests passed; TypeScript, ESLint and production
  frontend build passed before provider integration.
- Final backend suite: 328 tests passed, including existing policy, automation
  and MQTT ingestion regressions. New backend modules pass strict mypy and Ruff.
- Existing CV occupancy parsing suite: 9 tests passed. No live camera was used.
- Provider tests use simulated HTTP responses; no real API call or key is required.
- Local PostgreSQL/broker and physical hardware integration were not run.
- Browser visual QA was unavailable; source/type/lint/build checks passed.

## Files in this increment

Added backend modules:

- `app/domain/runtime.py`
- `app/repositories/runtime_history.py`, `app/repositories/analyst_context.py`
- `app/schemas/analytics.py`, `app/schemas/analyst.py`
- `app/services/analytics.py`, `app/services/energy_analytics.py`,
  `app/services/environmental_analyst.py`
- `app/api/v1/routers/analytics.py`
- Seven focused test files: `test_runtime.py`, `test_runtime_history.py`,
  `test_energy_analytics.py`, `test_analytics_api.py`, `test_analyst_api.py`,
  `test_analyst_context.py`, `test_environmental_analyst.py`.

Added frontend files are `energy-analytics` and `environmental-analyst` type/API
modules, `useEnergyAnalytics.ts`, `useAnalystReport.ts`,
`EnvironmentalAnalyst.tsx`, and `AnalystCommentary.tsx`.
Added migration `004_runtime_history.sql` and the two analytics documents.

Modified `backend/app/main.py`, `backend/app/core/config.py`,
`backend/pyproject.toml`, `backend/uv.lock`, `database/schema.sql`,
`frontend/src/features/dashboard/Dashboard.tsx`, `frontend/app/globals.css`,
`.env.example`, `README.md`, `docs/api/rest.md`, and `docs/architecture.md`.
Two minimal existing frontend validation repairs are in `DeviceControlCard.tsx`
and `EnergyCharts.tsx`. Other changes already present in the checkout are
pre-existing work, including occupancy automation and firmware changes.

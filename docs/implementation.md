# Implementation notes

For startup and a tour of the app, see the [README](../README.md).

## Calculations and assumptions

### Source data

The supplied synthetic CSV has 10,000 observations from 2020–2023 and is preserved unchanged. The separate `data/supply_chain_mock_2024_2026.csv` adds 6,977 mock observations from January 1, 2024 through November 7, 2026. Warehouse codes are treated as factory IDs. Reports use only the selected factory's observations before the fixed demo planning date of November 8, 2026. The date does not move on reload.

### Generating the extension

Run from the repository root:

```sh
python3 scripts/generate_mock_history.py
```

Generation uses a whole-row bootstrap with replacement. Source sampling weight is `2 ** (-age_days / half_life_days)`, measured back from the latest original observation. The default half-life is 365 days: the original years contribute approximately 6.3% (2020), 13.3% (2021), 26.7% (2022), and 53.6% (2023) of sampling probability. This weights the source used for generation; it does not change the app's trailing-mean forecast formula.

Only dates are replaced, preserving the sampled combinations of factory, model, demand, supplier, delay, defect rate, and other fields. Daily row counts follow a Poisson distribution at the original average observation frequency. No growth, seasonal pattern, future inflation, or supplier improvement is invented. The fixed seed makes generation reproducible; separate daily seeds keep earlier generated rows stable when extending the end date. Generated rows are sampled only from the original CSV, never recursively from generated data.

`--half-life-days`, `--seed`, `--start`, and `--end` configure generation. Defaults cover full 2024 and 2025, and 2026 through November 7, avoiding future observations relative to the planning date. `data/mock_history_manifest.json` records inputs, checksums, weights, counts, and limitations. The half-life is a demo assumption, not an estimated optimum. Bootstrap rows reuse source values; they add mock coverage, not independent evidence. Evaluation on synthetic holdouts is not evidence of real-world forecast accuracy.

Startup validates and imports the original and extension together under one checksum, retaining separate files and original source values. A new checksum imports new history without deleting saved reports, stock, decisions, or approved orders. `MOCK_DATA_PATH` selects the extension; an empty value disables it. After changing generated files, rebuild the app with `docker compose up --build -d`. Changing the planning date also requires updating `planning.json` and its fixture version; saved reports and approved-order dates remain frozen.

Row meaning is undocumented. The app assumes demand rows are additive. Historical inventory values are retained as source observations, not summed into current component stock.

Missing weeks are unknown, not zero. Zero would be appropriate only if the source were a complete transaction log where no row means no demand. Partial boundary weeks are excluded. Missing values should be repaired or excluded once source semantics are known, rather than interpolated without evidence.

### Forecasts and risks

Demand uses the mean of the last eight complete observed weeks. The report scales this baseline to the selected horizon and demand scenario. Confirmed customer orders replace the daily forecast when larger, rather than being added to it.

The displayed range uses the 10th and 90th percentiles of recent weekly demand. It is an empirical spread, not a calibrated confidence interval.

Evaluation holds out the last eight complete observed weeks and calculates MAE and WAPE using earlier data. Missing calendar weeks are omitted, so consecutive observations may span more than one week. With the extension enabled, recent holdout weeks are synthetic; scores describe only demo behavior and do not validate real-world performance.

Supplier delay uses the historical mean, plus the scenario's additional delay. Historical p90 is shown separately. Supplier observations older than 30 days at the planning date are flagged.

Component defect estimates use historical supplier failure-rate averages. The dataset has no component IDs or exposure counts. These estimates do not predict individual failures, wear, or field reliability. The component risk table averages the rates of its eligible suppliers.

There is no trained forecasting ML model. Optional report explanations use a pretrained local LLM; it does not calculate forecasts or purchase recommendations.

### Local report explanations

Ollama runs as the `llm` Compose service with a persistent `ollama_models` volume. It has no host port. The app calls its native `/api/chat` endpoint over the Compose network. Defaults are `LLM_BASE_URL=http://llm:11434`, `LLM_MODEL=qwen3:1.7b`, and `LLM_TIMEOUT_SECONDS=120`. Model downloads require network access; inference uses the local model and sends no report data to a hosted model API. Download the model with `docker compose exec llm ollama pull qwen3:1.7b`. Other models can be selected through `.env` and downloaded separately. An empty `LLM_BASE_URL` disables generation.

Docker Model Runner was evaluated first. The installed v0.1.11 runner rejected the current 1.7B package format and timed out loading the compatible older 0.6B package. The app therefore uses the separate Ollama service. This runs on CPU under Docker Desktop on macOS; first inference includes model loading. A future native runtime could improve speed.

`summaries.py` prepares a bounded set of factual sentences from the saved report and its current decision statuses, covering only selected analyses. Chart facts explain the mean baseline and the uncalibrated range; no trend is inferred from partial chart weeks. User-supplied report names and customer names are excluded from model input. The JSON schema requires one highlight per selected analysis, each citing one or two facts allowed for that analysis. Inventory highlights must cite purchase proposals when present. Temperature is zero, thinking is disabled, and output is bounded in length. The backend checks response shape, selected-analysis coverage, references, repeated bullets, numeric values against their cited facts, and some unsupported approval/receipt language. These checks do not prove semantic correctness, correct units, or the quality of every interpretation; supporting source sentences remain visible for review. Invalid output returns an error, not a fabricated AI summary.

One summary is stored per report snapshot in `report_summaries`:

```sql
CREATE TABLE report_summaries (
    report_id TEXT PRIMARY KEY REFERENCES report_runs(id),
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_checksum TEXT NOT NULL,
    content JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The model receives facts grouped by their allowed analysis section, which prevents unrelated inventory metrics from distracting it from purchase proposals. Invalid output gets at most one additional attempt with an instruction to copy a cited fact sentence exactly; both attempts are validated. Connection failures are not retried automatically.

Content includes highlights, cited source facts, total elapsed time, attempt count, token counts for the successful attempt, and model digest when available. A current cached summary is reused unless regeneration is requested. Changed decisions, report context, prompt version, or configured model mark it outdated. Regeneration replaces that snapshot's summary. A new report version starts without a summary. Generating a summary does not change forecasts, stock, decisions, or approved orders.

Inference holds no database transaction or factory lock. The app permits one active generation per process and checks the report input checksum again under the factory lock before saving. Concurrent changes return a conflict. Deleted reports cannot generate or retrieve summaries. Missing models, connection errors, timeouts, and invalid outputs leave any saved summary intact.

### Mock factory data

[planning.json](../backend/app/fixtures/planning.json) defines BOMs, physical stock, reservations, supplier eligibility, base lead times, prices, and customer orders. Factory scaling factors are mock assumptions.

Stock is seeded once per factory/component. Editing fixture balances does not overwrite existing database balances. Existing CA stock, reports, and orders are preserved by the schema migration.

Reservations represent separate production committed before the planning period. Available stock is on-hand stock minus reservations. Shared-component requirements are combined across all models in the factory before calculating purchase needs.

All-factory browsing combines rows and summary totals. It does not transfer stock, combine report scenarios into one forecast, or pool inventory between locations.

### Purchase recommendations

For each component, the app compares no purchase against eligible suppliers with standard and expedited shipping. Quantities cover net horizon requirements after expected defective receipts.

- Standard arrival = assumed base lead time + historical mean delay + scenario delay.
- Expedited arrival = three days + scenario delay, at a 40% price premium.
- Holding cost = 25% of component value per year.
- Shortage penalty = three times component unit cost per unmet unit.

A daily calculation applies receipts on their arrival dates, consumes parts, and counts unmet demand. Candidate cost is purchase cost + holding cost + shortage penalties.

This comparison considers one purchase per component. It does not optimize a production schedule, supplier capacity, or repeated replenishment. Cost differences are modeled estimates, not measured savings.

### Saved reports and decisions

Each report stores its configuration, results, source checksum, and inventory revision. Decisions are stored separately from the calculation snapshot.

Running an open saved report creates another snapshot in the same factory using the edited configuration. The button reads **Run next version**; creating a new report uses **Run report**. Recalculate uses the snapshot's saved configuration. Both actions share the same version sequence and append `(v2)`, `(v3)`, and so on. Renaming the report changes the base name for that new snapshot and its descendants while retaining its sequence. The report factory is fixed for the sequence; use New report to choose another factory. The global browsing filter remains independent.

Version numbers are allocated under the factory transaction lock across that report's descendants, including deleted versions; recalculating an older snapshot still takes the next number. Independent reports with the same name have separate sequences. New roots explicitly store version 1 and their own root ID. Existing entries stay intact; legacy independent entries whose names already end in `(vN)` advance from that displayed number on their next run, without merging ambiguous historical entries. Lineage is stored with the result, so no schema migration or renaming of existing reports is needed. Names are shortened when needed to stay within the 100-character limit. Existing decisions and AI summaries stay with their original snapshot and are not copied to the new version.

Approval creates an internal planned purchase order and increments that factory's revision. It does not contact a supplier or increase physical stock. Repeated approval requests create one order. Conflicting decisions are rejected.

Approvals from another report, a changed source checksum, or a changed planning fixture version make a saved report stale. Further approvals then require recalculation. Existing decisions and approved orders remain unchanged. Old approved orders retain their dates and are included in report receipts only if they fall inside the new planning horizon; they are not automatically received or moved forward. Home labels stale planning results and excludes them from its pending-needs total. Proposed Inventory Orders is read-only.

Report deletion sets a deletion timestamp. Deleted reports are excluded from the library, report retrieval, decisions, and Home's latest-report selection. Stored calculation snapshots and decisions remain available to approved purchase orders; deleting a report does not cancel supply or change stock. Deletion uses the same factory transaction lock as approval. Repeated deletion succeeds without further changes.

On the first schema upgrade, the five oldest existing reports are marked as locked demo reports. These flags persist across restarts; new reports are deletable. A fresh database with no saved reports has no locked demo reports.

## API

Interactive documentation is available at [localhost:8000/docs](http://localhost:8000/docs) while the app runs.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Application and data readiness |
| `GET /api/report-options` | Factories, models, and planning date |
| `GET /api/workspace` | All-factory rows, totals, and planning context |
| `GET /api/factories/{site}/workspace` | One factory's inventory, orders, and latest inventory report |
| `POST /api/reports` | Calculate and save a report |
| `POST /api/reports/{id}/recalculate` | Save the next version; `{}` uses saved settings, `{"config": {...}}` uses the supplied full configuration |
| `POST /api/reports/{id}/summary` | Generate or reuse a local summary; body `{"regenerate": true}` forces regeneration |
| `GET /api/reports?factory=CA` | List reports; factory filter is optional |
| `GET /api/reports/{id}` | Saved results and current decision status |
| `DELETE /api/reports/{id}` | Delete an unlocked report while retaining approved-order history |
| `POST /api/reports/{id}/recommendations/{component}/decision` | Approve or deny a recommendation |
| `GET /api/metadata`, `GET /api/overview` | Inspect supplied and generated history, with synthetic-data labels |

Example report body:

```json
{
  "name": "Texas demand review",
  "factory": "TX",
  "horizon_days": 60,
  "model": "",
  "demand_change_pct": 20,
  "extra_delay_days": 5,
  "analyses": ["demand", "supply", "failure", "inventory"]
}
```

## Tests

Calculation tests use Python's standard library. API tests require a fresh database and create reports, decisions, and orders. Use a separate instance on port 8001:

```sh
docker compose exec -T db createdb -U tractor tractor_test
docker compose run -d --name tractor-test-app \
  -p 127.0.0.1:8001:8000 -e PGDATABASE=tractor_test app
```

Wait for [localhost:8001/api/health](http://localhost:8001/api/health) to return ready, then run:

```sh
REPORT_TEST_URL=http://localhost:8001 PYTHONPATH=backend \
  python3 -m unittest discover -s backend/tests -v
```

Remove the test instance and its database afterward:

```sh
docker rm -f tractor-test-app
docker compose exec -T db dropdb -U tractor tractor_test
```

Tests cover calculations, future-data isolation, stock accounting, factory isolation, validation, duplicate approvals, stale reports, and all-factory totals. Browser checks have also covered navigation, independent factory selectors, saved results, exports, reduced motion, top alignment, and mobile layouts.

Summary unit tests cover input scoping, source references, numeric checks, malformed output, model failures, and cache invalidation. Set `REPORT_TEST_LLM=1` alongside `REPORT_TEST_URL` to run the real local-model persistence, caching, decision-change, and report-version checks against an isolated database. This needs the Ollama service and downloaded model; these tests never use the normal app database.

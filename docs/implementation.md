# Implementation notes

For startup and a tour of the app, see the [README](../README.md).

## Calculations and assumptions

### Source data

The supplied synthetic CSV has 10,000 observations from 2020–2023. Warehouse codes are treated as factory IDs. Reports use only the selected factory's observations and start from a simulated planning date of January 1, 2024.

Row meaning is undocumented. The app assumes demand rows are additive. Historical inventory values are retained as source observations, not summed into current component stock.

Missing weeks are unknown, not zero. Zero would be appropriate only if the source were a complete transaction log where no row means no demand. Partial boundary weeks are excluded. Missing values should be repaired or excluded once source semantics are known, rather than interpolated without evidence.

### Forecasts and risks

Demand uses the mean of the last eight complete observed weeks. The report scales this baseline to the selected horizon and demand scenario. Confirmed customer orders replace the daily forecast when larger, rather than being added to it.

The displayed range uses the 10th and 90th percentiles of recent weekly demand. It is an empirical spread, not a calibrated confidence interval.

Evaluation holds out the last eight complete observed weeks and calculates MAE and WAPE using earlier data. Missing calendar weeks are omitted, so consecutive observations may span more than one week. Scores are exploratory until row meaning and missing-week treatment are confirmed.

Supplier delay uses the historical mean, plus the scenario's additional delay. Historical p90 is shown separately. Supplier observations older than 30 days at the planning date are flagged.

Component defect estimates use historical supplier failure-rate averages. The dataset has no component IDs or exposure counts. These estimates do not predict individual failures, wear, or field reliability. The component risk table averages the rates of its eligible suppliers.

There is no trained ML model or LLM integration yet.

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

Approval creates an internal planned purchase order and increments that factory's revision. It does not contact a supplier or increase physical stock. Repeated approval requests create one order. Conflicting decisions are rejected.

Approvals from another report can make a saved report stale. Further approvals then require recalculation. Home labels stale planning results and excludes them from its pending-needs total. Proposed Inventory Orders is read-only.

## API

Interactive documentation is available at [localhost:8000/docs](http://localhost:8000/docs) while the app runs.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Application and data readiness |
| `GET /api/report-options` | Factories, models, and planning date |
| `GET /api/workspace` | All-factory rows, totals, and planning context |
| `GET /api/factories/{site}/workspace` | One factory's inventory, orders, and latest inventory report |
| `POST /api/reports` | Calculate and save a report |
| `GET /api/reports?factory=CA` | List reports; factory filter is optional |
| `GET /api/reports/{id}` | Saved results and current decision status |
| `POST /api/reports/{id}/recommendations/{component}/decision` | Approve or deny a recommendation |
| `GET /api/metadata`, `GET /api/overview` | Inspect the original historical data |

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

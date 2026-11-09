# Tractor Supply Planning

A local planning service with a **React/TypeScript frontend, FastAPI backend, and PostgreSQL database** for reviewing tractor demand, checking parts inventory, and approving purchase recommendations across five factories.

The app uses historical forecasting baselines and an optional local LLM to explain saved reports. Inventory, bills of materials, costs, and customer orders are mock data.

## Run

With Docker running:

```sh
docker compose up --build -d
```

Open [localhost:8000](http://localhost:8000). API documentation is at [localhost:8000/docs](http://localhost:8000/docs).

The first build downloads dependencies. No API key is required. To change the port or local database password, copy `.env.example` to `.env`.

To add five locked example reports to a fresh database, run:

```sh
docker compose exec -T app python -m app.demo_reports
```

This adds one example per factory, including model-specific reports. It requires an empty report library and does not run automatically on startup. Demo locks prevent deletion; running an example saves an editable new version.

For local report explanations, download the small model once:

```sh
docker compose exec llm ollama pull qwen3:1.7b
```

Open a saved report and click **Generate summary** in **Report explanation**. The local Qwen3 model writes one highlight per selected analysis with supporting report figures. Summaries are saved with that report version; use **Regenerate summary** after decisions change. Review the explanation alongside its sources. The app works without a downloaded model.

## Using the app

| Page | What it shows |
| --- | --- |
| Home | Factory totals, upcoming orders, and pending recommendations from the latest inventory report |
| Inventory | On-hand, reserved, available, and approved incoming parts |
| Customer Orders | Customer orders, due dates, and status |
| Proposed Inventory Orders | Approved purchase recommendations; this list is read-only |
| Reports | Saved reports and the **New report** action |

The global factory selector filters these pages. **All factories** shows every location, with stock and orders kept separate.

To run a report:

1. Open **Reports → New report** and give it a name.
2. Select **Report factory / state**. This is independent of the global filter; each report covers one factory.
3. Select **Report model** or **All models**. This selection applies to every analysis in the report.
4. Choose demand, supply risk, component failure, inventory recommendations, or a combination.
5. Set a 7–90 day horizon and any demand or supplier-delay adjustments, then run it.
6. Review the results and approve or deny purchase recommendations.

Reports save automatically. Approvals appear under **Proposed Inventory Orders** but do not change physical stock or send an order to a supplier. Run another report to include approved incoming supply in the calculations.

When a saved report is open, **Run next version** saves your edited settings as a new snapshot named `Texas demand review (v2)`, then `(v3)`. **Recalculate** also advances that sequence using the saved settings. Start a separate report or choose another factory through **New report** in the report library. Earlier reports and their decisions remain available.

Use **Delete** in the report list or report view to remove a report from the library. Approved purchase orders and stored history are retained. Version numbers are never reused: deleting v2 means the next run is v3. Existing demo reports may be locked; a fresh database starts with no locked reports. Browser Back and Forward restore pages, report views, and the global factory selection.

![All-factory Home dashboard with separate factory planning needs](docs/home-dashboard.png)

[Report library](docs/reports-library.png) · [Report configuration](docs/report-configuration.png) · [Model-specific report](docs/model-report.png) · [Saved report](docs/report-dashboard.png) · [Demand forecast](docs/report-forecast.png) · [Local AI explanation and sources](docs/report-summary.png) · [Purchase recommendations](docs/report-recommendations.png)

Screenshots show the current app with synthetic data in a separate demo workspace.

## Data and calculations

The original CSV contains 10,000 synthetic observations from 2020–2023 and is unchanged. A separate mock extension adds 6,977 rows from January 2024 through November 7, 2026, sampled from the original rows with more weight on recent history. Its five warehouse codes—CA, FL, IL, NY, and TX—serve as factory locations. Planning starts on **November 8, 2026**, a fixed demo date.

- **Demand:** mean of the last eight complete observed weeks.
- **Supplier delay:** historical mean delay, plus any report adjustment.
- **Component failure:** a supplier failure-rate proxy, not a prediction of individual part failures.
- **Purchasing:** compare eligible suppliers and shipping options using purchase, holding, and shortage costs.

CSV row meaning and missing-week handling are assumptions. Scores on generated data describe the demo, not real-world accuracy. See [generation and calculation details](docs/implementation.md#calculations-and-assumptions).

Mock factory data is in [planning.json](backend/app/fixtures/planning.json). Each factory has its own inventory and approved orders. A selected tractor model limits demand, customer orders, supplier history, BOM components, and purchases to that model. The BOM is an assumed demo mapping; the CSV does not supply actual tractor-to-part relationships.

Physical stock remains shared within the factory. A model report uses its compatible unreserved parts as a scenario and does not reserve stock. Separate model reports are not additive stock allocations. Recalculate older model-filtered snapshots to replace their previous factory-wide parts and risk calculations with the new model scope.

## Code layout

Docker Compose runs the app, database, and Ollama service. FastAPI serves the API and built React frontend on the same port. Architecture, database tables, API routes, and calculation assumptions are documented in [implementation notes](docs/implementation.md).

| File | Responsibility |
| --- | --- |
| [main.py](backend/app/main.py) | Startup, CSV import, and source-data endpoints |
| [reports.py](backend/app/reports.py) | Factory data, saved reports, and approval transactions |
| [planning.py](backend/app/planning.py) | Forecasts, component requirements, and purchase comparisons |
| [summaries.py](backend/app/summaries.py) | Local-model requests, source facts, and output checks |
| [main.tsx](frontend/src/main.tsx) | Navigation, factory selection, and report UI |
| [WorkspacePages.tsx](frontend/src/WorkspacePages.tsx) | Home, inventory, and order pages |
| [ReportSummary.tsx](frontend/src/ReportSummary.tsx) | Generate, display, and review saved explanations |
| [generate_mock_history.py](scripts/generate_mock_history.py) | Reproducible extension of the original dataset |

## Development

To run the frontend against the Docker backend:

```sh
cd frontend
npm ci
npm run dev
```

`npm run build` checks TypeScript and builds the frontend. Vite proxies API requests to port 8000.

Run calculation tests from the repository root:

```sh
PYTHONPATH=backend python3 -m unittest discover -s backend/tests -v
```

API tests are skipped unless a separate test instance is configured. See [API tests](docs/implementation.md#tests) for the setup and [API endpoints](docs/implementation.md#api) for the route list.

## Local data

`docker compose down` stops the app and keeps the database. Reports and decisions survive restarts.

`docker compose down --volumes` deletes the local database and downloaded models.

The app is intended for a local demo: it listens on localhost and has no login.

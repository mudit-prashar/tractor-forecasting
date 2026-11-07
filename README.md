# Tractor Supply Planning

A local app for reviewing tractor demand, checking parts inventory, and approving purchase recommendations across five factories.

The app currently uses historical forecasting baselines. It does not include a trained ML model or LLM. Inventory, bills of materials, costs, and customer orders are mock data.

## Run

With Docker running:

```sh
docker compose up --build -d
```

Open [localhost:8000](http://localhost:8000). API documentation is at [localhost:8000/docs](http://localhost:8000/docs).

The first build downloads dependencies. No API key is required. To change the port or local database password, copy `.env.example` to `.env`.

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
3. Choose demand, supply risk, component failure, inventory recommendations, or a combination.
4. Set a 7–90 day horizon and any demand or supplier-delay adjustments, then run it.
5. Review the results and approve or deny purchase recommendations.

Reports save automatically. Approvals appear under **Proposed Inventory Orders** but do not change physical stock or send an order to a supplier. Run another report to include approved incoming supply in the calculations.

![Home](docs/home-dashboard.png)

[Report list](docs/reports-library.png) · [Report configuration and results](docs/report-dashboard.png)

## Data and calculations

The supplied CSV contains 10,000 observations from 2020–2023. Its five warehouse codes—CA, FL, IL, NY, and TX—serve as factory locations in this demo. Planning starts on **January 1, 2024**.

- **Demand:** mean of the last eight complete observed weeks.
- **Supplier delay:** historical mean delay, plus any report adjustment.
- **Component failure:** a supplier failure-rate proxy, not a prediction of individual part failures.
- **Purchasing:** compare eligible suppliers and shipping options using purchase, holding, and shortage costs.

CSV row meaning and missing-week handling are assumptions. Forecast error metrics are exploratory. See [calculation details and limitations](docs/implementation.md#calculations-and-assumptions).

Mock factory data is in [planning.json](backend/app/fixtures/planning.json). Each factory has its own inventory and approved orders. The tractor-model filter limits the forecast and customer-order view; parts planning still includes all models at that factory because they share components.

## Code layout

React and TypeScript provide the UI. FastAPI serves the API and built frontend. PostgreSQL stores source observations, inventory, reports, and decisions. Docker Compose runs the app and database.

| File | Responsibility |
| --- | --- |
| [main.py](backend/app/main.py) | Startup, CSV import, and source-data endpoints |
| [reports.py](backend/app/reports.py) | Factory data, saved reports, and approval transactions |
| [planning.py](backend/app/planning.py) | Forecasts, component requirements, and purchase comparisons |
| [main.tsx](frontend/src/main.tsx) | Navigation, factory selection, and report UI |
| [WorkspacePages.tsx](frontend/src/WorkspacePages.tsx) | Home, inventory, and order pages |

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

`docker compose down --volumes` deletes all local database state.

The app is intended for a local demo: it listens on localhost and has no login. The original design and remaining work are in [BUILD_PLAN.md](BUILD_PLAN.md).

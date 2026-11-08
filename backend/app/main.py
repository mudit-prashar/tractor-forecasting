import os
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row

from .dataset import read_dataset
from .reports import create_router, initialize_planning

DATA_PATH = Path(os.getenv('DATA_PATH', '/app/data/supply_chain.csv'))
MOCK_DATA_PATH = os.getenv('MOCK_DATA_PATH', '/app/data/supply_chain_mock_2024_2026.csv')
FRONTEND_PATH = Path(os.getenv('FRONTEND_PATH', '/app/frontend/dist'))
ACTIVE_CHECKSUM = ''


def connection():
    # libpq reads the PG* environment variables; passwords need no URL escaping.
    return psycopg.connect(connect_timeout=5, row_factory=dict_row)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ACTIVE_CHECKSUM
    checksum, observations = read_dataset(DATA_PATH, Path(MOCK_DATA_PATH) if MOCK_DATA_PATH else None)
    with connection() as conn:
        # Serialize startup/import if multiple application instances start together.
        conn.execute('SELECT pg_advisory_xact_lock(761034)')
        conn.execute('''CREATE TABLE IF NOT EXISTS dataset_imports (
            checksum TEXT PRIMARY KEY, filename TEXT NOT NULL,
            row_count INTEGER NOT NULL, imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS observations (
            dataset_checksum TEXT NOT NULL REFERENCES dataset_imports(checksum),
            source_row INTEGER NOT NULL, observed_date DATE NOT NULL,
            tractor_model TEXT NOT NULL, demand_units INTEGER NOT NULL CHECK (demand_units >= 0),
            supplier TEXT NOT NULL, supplier_delay_days INTEGER NOT NULL CHECK (supplier_delay_days >= 0),
            component_failure_rate DOUBLE PRECISION NOT NULL CHECK (component_failure_rate BETWEEN 0 AND 1),
            inventory_levels INTEGER NOT NULL CHECK (inventory_levels >= 0),
            warehouse_location TEXT NOT NULL, inflation_rate DOUBLE PRECISION NOT NULL,
            market_trend_index DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (dataset_checksum, source_row)
        )''')
        conn.execute('''CREATE INDEX IF NOT EXISTS observations_filter_idx
            ON observations(dataset_checksum, tractor_model, warehouse_location, observed_date)''')
        imported = conn.execute('SELECT checksum FROM dataset_imports WHERE checksum = %s', (checksum,)).fetchone()
        if not imported:
            conn.execute('INSERT INTO dataset_imports(checksum, filename, row_count) VALUES (%s, %s, %s)',
                         (checksum, DATA_PATH.name + (f' + {Path(MOCK_DATA_PATH).name}' if MOCK_DATA_PATH else ''), len(observations)))
            with conn.cursor() as cursor:
                cursor.executemany('INSERT INTO observations VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                                   [(checksum, *row) for row in observations])
        initialize_planning(conn)
    ACTIVE_CHECKSUM = checksum
    yield


app = FastAPI(title='Tractor Supply Planning', version='0.2.0', lifespan=lifespan)
app.include_router(create_router(connection, lambda: ACTIVE_CHECKSUM))


@app.get('/api/health')
def health():
    with connection() as conn:
        record = conn.execute('SELECT row_count FROM dataset_imports WHERE checksum = %s', (ACTIVE_CHECKSUM,)).fetchone()
    if not record:
        raise HTTPException(503, 'Dataset is not ready')
    return {'status': 'ready', 'dataset_rows': record['row_count'], 'stage': 'report-dashboard'}


@app.get('/api/metadata')
def metadata():
    with connection() as conn:
        result = conn.execute('''SELECT array_agg(DISTINCT tractor_model ORDER BY tractor_model) AS models,
            array_agg(DISTINCT warehouse_location ORDER BY warehouse_location) AS warehouses,
            min(observed_date) AS start_date, max(observed_date) AS end_date
            FROM observations WHERE dataset_checksum = %s''', (ACTIVE_CHECKSUM,)).fetchone()
    return {**result, 'dataset_checksum': ACTIVE_CHECKSUM, 'source': DATA_PATH.name,
            'synthetic_extension': Path(MOCK_DATA_PATH).name if MOCK_DATA_PATH else None,
            'assumptions': ['Demand observations are assumed additive; row grain is undocumented.',
                            'Recorded inventory is not interpreted as current component stock.',
                            'The 2024–2026 extension is synthetic, sampled from recency-weighted supplied history; it is not new evidence.'],
            'stage': 'Historical baselines and mock factory planning.'}


@app.get('/api/overview')
def overview(model: str | None = None, warehouse: str | None = None):
    clauses = ['dataset_checksum = %s']
    parameters = [ACTIVE_CHECKSUM]
    if model:
        clauses.append('tractor_model = %s')
        parameters.append(model)
    if warehouse:
        clauses.append('warehouse_location = %s')
        parameters.append(warehouse)
    where = ' AND '.join(clauses)
    with connection() as conn:
        summary = conn.execute(f'''SELECT count(*) AS observations,
            coalesce(sum(demand_units), 0) AS demand_units,
            count(DISTINCT tractor_model) AS models, count(DISTINCT supplier) AS suppliers,
            min(observed_date) AS start_date, max(observed_date) AS end_date
            FROM observations WHERE {where}''', parameters).fetchone()
        weekly = conn.execute(f'''SELECT date_trunc('week', observed_date)::date AS week,
            sum(demand_units) AS demand_units, count(*) AS observations
            FROM observations WHERE {where} GROUP BY 1 ORDER BY 1''', parameters).fetchall()
        suppliers = conn.execute(f'''SELECT supplier, count(*) AS observations,
            round(avg(supplier_delay_days)::numeric, 1) AS mean_delay_days,
            round(avg(component_failure_rate)::numeric * 100, 2) AS mean_failure_percent
            FROM observations WHERE {where} GROUP BY supplier ORDER BY supplier''', parameters).fetchall()
    return {'summary': summary, 'weekly': weekly, 'suppliers': suppliers,
            'dataset_checksum': ACTIVE_CHECKSUM, 'units': 'tractor units (assumed additive demand)',
            'series_type': 'supplied_and_synthetic' if MOCK_DATA_PATH else 'observed',
            'note': '2024–2026 extension is synthetic, not observed evidence. Boundary weeks may be partial; missing weeks are unobserved, not zero.'}


if (FRONTEND_PATH / 'assets').is_dir():
    app.mount('/assets', StaticFiles(directory=FRONTEND_PATH / 'assets'), name='assets')


@app.get('/', include_in_schema=False)
def index():
    if not (FRONTEND_PATH / 'index.html').exists():
        raise HTTPException(503, 'Frontend not built. Use the Vite dev server or build the Docker image.')
    return FileResponse(FRONTEND_PATH / 'index.html')


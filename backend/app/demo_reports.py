"""Create five locked examples after initializing a fresh application database."""

import json
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from .dataset import read_dataset
from .planning import build_report
from .reports import ReportConfig, factory_fixture, incoming_orders

DEMO_CONFIGS = [
    {'name': 'California factory overview', 'factory': 'CA', 'horizon_days': 60},
    {'name': 'Florida TX-100 parts review', 'factory': 'FL', 'model': 'TX-100', 'horizon_days': 60},
    {'name': 'Illinois TX-200 monthly review', 'factory': 'IL', 'model': 'TX-200',
     'horizon_days': 30, 'demand_change_pct': -5},
    {'name': 'New York TX-400 supply review', 'factory': 'NY', 'model': 'TX-400',
     'horizon_days': 60, 'extra_delay_days': 3},
    {'name': 'Texas factory demand scenario', 'factory': 'TX', 'horizon_days': 90,
     'demand_change_pct': 20, 'extra_delay_days': 5},
]


def seed_demo_reports(conn, checksum):
    with conn.transaction():
        states = {row['site']: row['revision'] for row in conn.execute(
            'SELECT site, revision FROM factory_state ORDER BY site FOR UPDATE').fetchall()}
        if conn.execute('SELECT count(*) AS count FROM report_runs').fetchone()['count']:
            raise ValueError('Demo seeding requires a fresh database; existing reports were preserved.')
        if not conn.execute('SELECT checksum FROM dataset_imports WHERE checksum = %s', (checksum,)).fetchone():
            raise ValueError('Start the app and wait for its data import before seeding reports.')
        prepared = []
        for raw in DEMO_CONFIGS:
            config = ReportConfig(**raw)
            fixture = factory_fixture(config.factory)
            observations = conn.execute('''SELECT * FROM observations
                WHERE dataset_checksum = %s AND warehouse_location = %s
                ORDER BY observed_date''', (checksum, config.factory)).fetchall()
            inventory = {row['component_id']: {'on_hand': row['on_hand'], 'reserved': row['reserved']}
                         for row in conn.execute('SELECT * FROM factory_inventory WHERE site = %s', (config.factory,)).fetchall()}
            result = build_report(config.model_dump(), observations, fixture, inventory,
                                  incoming_orders(conn, config.factory))
            report_id = str(uuid4())
            result.update(version_root_id=report_id, version_base_name=config.name, version=1)
            prepared.append((report_id, config, result))
        for report_id, config, result in prepared:
            conn.execute('''INSERT INTO report_runs
                (id, created_at, inventory_revision, dataset_checksum, config, result, name, site, demo_locked)
                VALUES (%s, clock_timestamp(), %s, %s, %s, %s, %s, %s, TRUE)''',
                         (report_id, states[config.factory], checksum, Jsonb(config.model_dump()),
                          Jsonb(result), config.name, config.factory))
        return [{'id': report_id, 'name': config.name, 'factory': config.factory,
                 'model': config.model or 'All models', 'horizon_days': config.horizon_days,
                 'demo_locked': True} for report_id, config, _ in prepared]


def main():
    from .main import DATA_PATH, MOCK_DATA_PATH, connection
    checksum, _ = read_dataset(DATA_PATH, Path(MOCK_DATA_PATH) if MOCK_DATA_PATH else None)
    with connection() as conn:
        created = seed_demo_reports(conn, checksum)
    print(json.dumps({'created': created}, indent=2))


if __name__ == '__main__':
    main()

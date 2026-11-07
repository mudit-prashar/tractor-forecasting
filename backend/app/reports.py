import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from .planning import build_report

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'planning.json').read_text())
Factory = Literal['CA', 'FL', 'IL', 'NY', 'TX']


def factory_fixture(site):
    settings = next(factory for factory in FIXTURE['factories'] if factory['id'] == site)
    fixture = deepcopy(FIXTURE)
    fixture['site'] = site
    for item in fixture['components']:
        item['on_hand'] = round(item['on_hand'] * settings['stock_scale'])
        item['reserved'] = round(item['reserved'] * settings['stock_scale'])
    for order in fixture['orders']:
        if site != 'CA':
            order['id'] = f"{site}-{order['id']}"
        order['quantity'] = round(order['quantity'] * settings['order_scale'])
    return fixture


class ReportConfig(BaseModel):
    name: str = Field(default='Supply planning report', min_length=1, max_length=100)
    factory: Factory = 'CA'
    horizon_days: int = Field(default=60, ge=7, le=90)
    model: str = ''
    demand_change_pct: float = Field(default=0, ge=-50, le=100, allow_inf_nan=False)
    extra_delay_days: int = Field(default=0, ge=0, le=30)
    analyses: list[Literal['demand', 'supply', 'failure', 'inventory']] = Field(
        default=['demand', 'supply', 'failure', 'inventory'], min_length=1, max_length=4)

    @field_validator('name')
    @classmethod
    def validate_name(cls, value):
        if not value.strip():
            raise ValueError('Report name cannot be blank')
        return value.strip()


class Decision(BaseModel):
    action: Literal['approved', 'denied']


def initialize_planning(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS planning_state (
        id INTEGER PRIMARY KEY CHECK (id = 1), revision INTEGER NOT NULL DEFAULT 0)''')
    conn.execute('INSERT INTO planning_state (id) VALUES (1) ON CONFLICT DO NOTHING')
    conn.execute('''CREATE TABLE IF NOT EXISTS planning_inventory (
        component_id TEXT PRIMARY KEY, on_hand INTEGER NOT NULL CHECK (on_hand >= 0),
        reserved INTEGER NOT NULL CHECK (reserved >= 0 AND reserved <= on_hand))''')
    for component in FIXTURE['components']:
        conn.execute('INSERT INTO planning_inventory VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
                     (component['id'], component['on_hand'], component['reserved']))
    conn.execute('''CREATE TABLE IF NOT EXISTS report_runs (
        id TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        inventory_revision INTEGER NOT NULL, dataset_checksum TEXT NOT NULL,
        config JSONB NOT NULL, result JSONB NOT NULL)''')
    conn.execute("ALTER TABLE report_runs ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT 'Supply planning report'")
    conn.execute('''CREATE TABLE IF NOT EXISTS planning_purchase_orders (
        id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES report_runs(id),
        component_id TEXT NOT NULL, supplier TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
        expected_date DATE NOT NULL, failure_rate DOUBLE PRECISION NOT NULL,
        status TEXT NOT NULL DEFAULT 'planned', UNIQUE (report_id, component_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS recommendation_decisions (
        report_id TEXT NOT NULL REFERENCES report_runs(id), component_id TEXT NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('approved', 'denied')),
        decided_at TIMESTAMPTZ NOT NULL DEFAULT now(), order_id TEXT,
        PRIMARY KEY(report_id, component_id))''')
    # Preserve the original CA ledger and revision while adding independent factories.
    conn.execute('''CREATE TABLE IF NOT EXISTS factory_state (
        site TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0)''')
    conn.execute("INSERT INTO factory_state SELECT 'CA', revision FROM planning_state WHERE id = 1 ON CONFLICT DO NOTHING")
    conn.execute('''CREATE TABLE IF NOT EXISTS factory_inventory (
        site TEXT NOT NULL REFERENCES factory_state(site), component_id TEXT NOT NULL,
        on_hand INTEGER NOT NULL CHECK(on_hand >= 0),
        reserved INTEGER NOT NULL CHECK(reserved >= 0 AND reserved <= on_hand),
        PRIMARY KEY(site, component_id))''')
    conn.execute("INSERT INTO factory_inventory SELECT 'CA', component_id, on_hand, reserved FROM planning_inventory ON CONFLICT DO NOTHING")
    for factory in FIXTURE['factories']:
        site = factory['id']
        conn.execute('INSERT INTO factory_state (site) VALUES (%s) ON CONFLICT DO NOTHING', (site,))
        for item in factory_fixture(site)['components']:
            conn.execute('INSERT INTO factory_inventory VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING',
                         (site, item['id'], item['on_hand'], item['reserved']))
    conn.execute("ALTER TABLE report_runs ADD COLUMN IF NOT EXISTS site TEXT NOT NULL DEFAULT 'CA' REFERENCES factory_state(site)")
    conn.execute("ALTER TABLE planning_purchase_orders ADD COLUMN IF NOT EXISTS site TEXT NOT NULL DEFAULT 'CA' REFERENCES factory_state(site)")
    conn.execute('CREATE INDEX IF NOT EXISTS planned_orders_site_idx ON planning_purchase_orders(site, expected_date)')


def load_report(conn, report_id):
    row = conn.execute('SELECT * FROM report_runs WHERE id = %s', (report_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Report not found')
    decisions = conn.execute('SELECT * FROM recommendation_decisions WHERE report_id = %s', (report_id,)).fetchall()
    by_component = {decision['component_id']: decision for decision in decisions}
    revision = conn.execute('SELECT revision FROM factory_state WHERE site = %s', (row['site'],)).fetchone()['revision']
    own_approvals = sum(decision['action'] == 'approved' for decision in decisions)
    result = {**row['result'], 'id': row['id'], 'name': row['name'], 'created_at': row['created_at'],
              'dataset_checksum': row['dataset_checksum'],
              'config': {**row['config'], 'name': row['name'], 'factory': row['site']},
              'is_stale': revision != row['inventory_revision'] + own_approvals}
    for item in result['recommendations']:
        if decision := by_component.get(item['id']):
            item.update(status=decision['action'], order_id=decision['order_id'])
    result['planned_orders'] = conn.execute('SELECT * FROM planning_purchase_orders WHERE site = %s ORDER BY expected_date, id', (row['site'],)).fetchall()
    return result


def create_router(connection, checksum):
    router = APIRouter(prefix='/api')

    @router.get('/report-options')
    def options():
        return {'models': list(FIXTURE['bom']), 'site': FIXTURE['site'],
                'factories': [{'id': factory['id'], 'name': factory['name']} for factory in FIXTURE['factories']],
                'planning_date': FIXTURE['planning_date'], 'fixture_version': FIXTURE['version']}

    @router.get('/reports')
    def list_reports(factory: Factory | None = None):
        with connection() as conn:
            return conn.execute("""SELECT id, name, site, created_at,
                config || jsonb_build_object('factory', site) AS config FROM report_runs
                WHERE (%s::text IS NULL OR site = %s) ORDER BY created_at DESC""", (factory, factory)).fetchall()

    @router.get('/factories/{site}/workspace')
    def workspace(site: Factory):
        fixture = factory_fixture(site)
        components = {item['id']: item for item in fixture['components']}
        with connection() as conn:
            balances = conn.execute('SELECT * FROM factory_inventory WHERE site = %s ORDER BY component_id', (site,)).fetchall()
            approved = conn.execute('''SELECT p.*, r.name AS report_name, r.result,
                d.decided_at AS approved_at FROM planning_purchase_orders p
                JOIN report_runs r ON r.id = p.report_id
                JOIN recommendation_decisions d ON d.order_id = p.id AND d.action = 'approved'
                WHERE p.site = %s ORDER BY p.expected_date, p.id''', (site,)).fetchall()
            for order in approved:
                snapshot = order.pop('result')
                recommendation = next(item for item in snapshot['recommendations'] if item['id'] == order['component_id'])
                order.update(component_name=components[order['component_id']]['name'],
                             purchase_cost=recommendation['purchase_cost'], shipping=recommendation['shipping'])
            inventory = []
            for balance in balances:
                component = components[balance['component_id']]
                inventory.append({**balance, 'name': component['name'], 'unit_cost': component['unit_cost'],
                                  'available': balance['on_hand'] - balance['reserved'],
                                  'incoming_units': sum(order['quantity'] for order in approved if order['component_id'] == balance['component_id'])})
            latest = conn.execute("""SELECT id FROM report_runs WHERE site = %s
                AND config->'analyses' @> '["inventory"]'::jsonb ORDER BY created_at DESC LIMIT 1""", (site,)).fetchone()
            planning = None
            if latest:
                report = load_report(conn, latest['id'])
                planning = {key: report[key] for key in ('id', 'name', 'created_at', 'is_stale', 'config', 'end_date')}
                planning['needs'] = [{**item, 'coverage_days': next(row['coverage_days'] for row in report['inventory'] if row['id'] == item['id'])}
                                     for item in report['recommendations'] if item['status'] == 'pending']
                planning['needs'].sort(key=lambda item: item['coverage_days'])
        start = date.fromisoformat(fixture['planning_date'])
        orders = [{**order, 'site': site, 'due_date': (start + timedelta(days=order['due_in_days'])).isoformat()}
                  for order in fixture['orders']]
        return {'site': site, 'planning_date': fixture['planning_date'], 'inventory': inventory,
                'customer_orders': orders, 'approved_orders': approved, 'latest_report': planning,
                'summary': {'inventory_value': sum(item['on_hand'] * item['unit_cost'] for item in inventory),
                            'customer_order_count': len(orders),
                            'due_30_days': sum(order['due_in_days'] < 30 for order in orders),
                            'approved_order_count': len(approved)},
                'provenance': fixture['provenance']}

    @router.get('/workspace')
    def all_factories_workspace():
        factories = [workspace(factory['id']) for factory in FIXTURE['factories']]
        return {
            'site': 'ALL', 'factory_count': len(factories), 'planning_date': FIXTURE['planning_date'],
            'inventory': [item for factory in factories for item in factory['inventory']],
            'customer_orders': sorted([item for factory in factories for item in factory['customer_orders']], key=lambda item: (item['due_date'], item['site'], item['id'])),
            'approved_orders': sorted([item for factory in factories for item in factory['approved_orders']], key=lambda item: (item['expected_date'], item['id'])),
            'summary': {key: sum(factory['summary'][key] for factory in factories) for key in ('inventory_value', 'customer_order_count', 'due_30_days', 'approved_order_count')},
            'latest_report': None,
            'planning_by_factory': [{'site': factory['site'], 'latest_report': factory['latest_report']} for factory in factories],
            'provenance': FIXTURE['provenance'],
        }

    @router.post('/reports', status_code=201)
    def run_report(config: ReportConfig):
        if config.model and config.model not in FIXTURE['bom']:
            raise HTTPException(422, 'Unknown tractor model')
        report_id = str(uuid4())
        fixture = factory_fixture(config.factory)
        with connection() as conn:
            # One short transaction gives the calculation a consistent operational snapshot.
            state = conn.execute('SELECT revision FROM factory_state WHERE site = %s FOR UPDATE', (config.factory,)).fetchone()
            observations = conn.execute('''SELECT * FROM observations
                WHERE dataset_checksum = %s AND warehouse_location = %s AND observed_date < %s
                ORDER BY observed_date''', (checksum(), config.factory, date.fromisoformat(fixture['planning_date']))).fetchall()
            inventory = {row['component_id']: {'on_hand': row['on_hand'], 'reserved': row['reserved']}
                         for row in conn.execute('SELECT * FROM factory_inventory WHERE site = %s', (config.factory,)).fetchall()}
            incoming = conn.execute('SELECT * FROM planning_purchase_orders WHERE site = %s', (config.factory,)).fetchall()
            try:
                result = build_report(config.model_dump(), observations, fixture, inventory, incoming)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            conn.execute('INSERT INTO report_runs (id, inventory_revision, dataset_checksum, config, result, name, site) VALUES (%s, %s, %s, %s, %s, %s, %s)',
                         (report_id, state['revision'], checksum(), Jsonb(config.model_dump()), Jsonb(result), config.name, config.factory))
            return load_report(conn, report_id)

    @router.get('/reports/{report_id}')
    def get_report(report_id: str):
        with connection() as conn:
            return load_report(conn, report_id)

    @router.post('/reports/{report_id}/recommendations/{component_id}/decision')
    def decide(report_id: str, component_id: str, decision: Decision):
        with connection() as conn:
            row = conn.execute('SELECT site FROM report_runs WHERE id = %s', (report_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Report not found')
            conn.execute('SELECT revision FROM factory_state WHERE site = %s FOR UPDATE', (row['site'],))
            report = load_report(conn, report_id)
            recommendation = next((item for item in report['recommendations'] if item['id'] == component_id), None)
            if recommendation is None:
                raise HTTPException(404, 'Recommendation not found')
            if recommendation['status'] != 'pending':
                if recommendation['status'] == decision.action:
                    return report
                raise HTTPException(409, 'This recommendation already has a decision')
            if decision.action == 'approved' and report['is_stale']:
                raise HTTPException(409, 'Incoming supply changed after this report. Run a new report before approving.')
            if decision.action == 'approved' and 'inventory' not in report['config']['analyses']:
                raise HTTPException(409, 'Run a report with inventory recommendations selected before approving')
            order_id = None
            if decision.action == 'approved':
                order_id = 'PO-' + uuid4().hex[:8].upper()
                conn.execute('''INSERT INTO planning_purchase_orders
                    (id, report_id, component_id, supplier, quantity, expected_date, failure_rate, site)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
                             (order_id, report_id, component_id, recommendation['supplier'], recommendation['quantity'],
                              date.fromisoformat(recommendation['arrival_date']), recommendation['failure_rate'], row['site']))
                conn.execute('UPDATE factory_state SET revision = revision + 1 WHERE site = %s', (row['site'],))
            conn.execute('INSERT INTO recommendation_decisions (report_id, component_id, action, order_id) VALUES (%s, %s, %s, %s)',
                         (report_id, component_id, decision.action, order_id))
            return load_report(conn, report_id)

    return router

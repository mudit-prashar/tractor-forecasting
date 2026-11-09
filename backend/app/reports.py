import json
import re
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from threading import BoundedSemaphore
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from .planning import build_report
from .summaries import (InvalidSummary, SummaryUnavailable, context_checksum,
                        generate_summary, report_context, summary_outdated)

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'planning.json').read_text())
Factory = Literal['CA', 'FL', 'IL', 'NY', 'TX']
SUMMARY_SLOT = BoundedSemaphore(1)


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


class SummaryRequest(BaseModel):
    regenerate: bool = False


class RecalculationRequest(BaseModel):
    config: ReportConfig | None = None


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
    conn.execute('ALTER TABLE report_runs ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ')
    # Pin the five oldest existing reports once; later reports stay deletable.
    conn.execute('ALTER TABLE report_runs ADD COLUMN IF NOT EXISTS demo_locked BOOLEAN')
    conn.execute('''UPDATE report_runs SET demo_locked = id IN (
        SELECT id FROM report_runs WHERE deleted_at IS NULL ORDER BY created_at, id LIMIT 5)
        WHERE demo_locked IS NULL''')
    conn.execute('ALTER TABLE report_runs ALTER COLUMN demo_locked SET DEFAULT FALSE')
    conn.execute('ALTER TABLE report_runs ALTER COLUMN demo_locked SET NOT NULL')
    conn.execute('''CREATE TABLE IF NOT EXISTS report_summaries (
        report_id TEXT PRIMARY KEY REFERENCES report_runs(id),
        model TEXT NOT NULL, prompt_version TEXT NOT NULL,
        input_checksum TEXT NOT NULL, content JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
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


def incoming_orders(conn, site):
    # Originating snapshots carry the scope; legacy orders predate that metadata
    # and retain their original factory-wide meaning without rewriting the ledger.
    return conn.execute('''SELECT p.*, coalesce(r.result->'scope'->>'model', '') AS model
        FROM planning_purchase_orders p JOIN report_runs r ON r.id = p.report_id
        WHERE p.site = %s ORDER BY p.expected_date, p.id''', (site,)).fetchall()


def load_report(conn, report_id, dataset_checksum):
    row = conn.execute('SELECT * FROM report_runs WHERE id = %s AND deleted_at IS NULL', (report_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Report not found')
    decisions = conn.execute('SELECT * FROM recommendation_decisions WHERE report_id = %s', (report_id,)).fetchall()
    by_component = {decision['component_id']: decision for decision in decisions}
    revision = conn.execute('SELECT revision FROM factory_state WHERE site = %s', (row['site'],)).fetchone()['revision']
    own_approvals = sum(decision['action'] == 'approved' for decision in decisions)
    result = {**row['result'], 'id': row['id'], 'name': row['name'], 'created_at': row['created_at'],
              'dataset_checksum': row['dataset_checksum'], 'demo_locked': row['demo_locked'],
              'config': {**row['config'], 'name': row['name'], 'factory': row['site']},
              'is_stale': (revision != row['inventory_revision'] + own_approvals
                           or row['dataset_checksum'] != dataset_checksum
                           or row['result'].get('fixture_version') != FIXTURE['version'])}
    for item in result['recommendations']:
        if decision := by_component.get(item['id']):
            item.update(status=decision['action'], order_id=decision['order_id'])
    report_model = row['result'].get('scope', {}).get('model')
    components = {item['id'] for item in result['inventory']}
    result['planned_orders'] = [order for order in incoming_orders(conn, row['site'])
                                if order['component_id'] in components
                                and (not report_model or not order['model'] or order['model'] == report_model)]
    saved_summary = conn.execute('SELECT * FROM report_summaries WHERE report_id = %s', (report_id,)).fetchone()
    result['ai_summary'] = None
    if saved_summary:
        summary = {**saved_summary['content'], **{key: value for key, value in saved_summary.items() if key != 'content'}}
        result['ai_summary'] = {**summary, 'is_outdated': summary_outdated(summary, result)}
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
            return conn.execute("""SELECT id, name, site, created_at, demo_locked,
                config || jsonb_build_object('factory', site) AS config FROM report_runs
                WHERE deleted_at IS NULL AND (%s::text IS NULL OR site = %s)
                ORDER BY created_at DESC""", (factory, factory)).fetchall()

    @router.get('/factories/{site}/workspace')
    def workspace(site: Factory):
        fixture = factory_fixture(site)
        components = {item['id']: item for item in fixture['components']}
        with connection() as conn:
            balances = conn.execute('SELECT * FROM factory_inventory WHERE site = %s ORDER BY component_id', (site,)).fetchall()
            approved = conn.execute('''SELECT p.*, r.name AS report_name, r.result,
                (r.deleted_at IS NOT NULL) AS report_deleted,
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
                AND deleted_at IS NULL
                AND config->'analyses' @> '["inventory"]'::jsonb ORDER BY created_at DESC LIMIT 1""", (site,)).fetchone()
            planning = None
            if latest:
                report = load_report(conn, latest['id'], checksum())
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

    def calculate_report(config: ReportConfig | None = None, source_id: str | None = None):
        report_id = str(uuid4())
        with connection() as conn:
            if source_id:
                source = conn.execute('SELECT * FROM report_runs WHERE id = %s AND deleted_at IS NULL', (source_id,)).fetchone()
                if not source:
                    raise HTTPException(404, 'Report not found')
                if config is None:
                    config = ReportConfig(**{**source['config'], 'name': source['name'], 'factory': source['site']})
                elif config.factory != source['site']:
                    raise HTTPException(422, 'Versions of a report use the same factory. Create a new report to select another factory.')
            if config.model and config.model not in FIXTURE['bom']:
                raise HTTPException(422, 'Unknown tractor model')
            fixture = factory_fixture(config.factory)
            # One short transaction gives the calculation a consistent operational snapshot.
            state = conn.execute('SELECT revision FROM factory_state WHERE site = %s FOR UPDATE', (config.factory,)).fetchone()
            lineage = {'version_root_id': report_id, 'version_base_name': config.name, 'version': 1}
            if source_id:
                # Deletion and other recalculations use the same factory lock.
                source = conn.execute('SELECT * FROM report_runs WHERE id = %s AND deleted_at IS NULL', (source_id,)).fetchone()
                if not source:
                    raise HTTPException(404, 'Report not found')
                root_id = source['result'].get('version_root_id', source_id)
                base_name = source['result'].get('version_base_name', source['name'])
                source_version = source['result'].get('version', 1)
                # Older Run report calls could copy a displayed version into an
                # independent report's name without storing version metadata.
                if 'version' not in source['result']:
                    if match := re.fullmatch(r'(.+) \(v([1-9][0-9]{0,5})\)', source['name']):
                        base_name, source_version = match.group(1), int(match.group(2))
                if config.name != source['name']:
                    base_name = config.name
                version = conn.execute('''SELECT coalesce(max((result->>'version')::integer), 1) + 1 AS next_version
                    FROM report_runs WHERE site = %s AND (id = %s OR result->>'version_root_id' = %s)''',
                                       (config.factory, root_id, root_id)).fetchone()['next_version']
                version = max(version, source_version + 1)
                suffix = f' (v{version})'
                config = config.model_copy(update={'name': base_name[:100 - len(suffix)].rstrip() + suffix})
                lineage = {'version_root_id': root_id, 'version_base_name': base_name,
                           'version': version, 'recalculated_from': source_id}
            observations = conn.execute('''SELECT * FROM observations
                WHERE dataset_checksum = %s AND warehouse_location = %s AND observed_date < %s
                ORDER BY observed_date''', (checksum(), config.factory, date.fromisoformat(fixture['planning_date']))).fetchall()
            inventory = {row['component_id']: {'on_hand': row['on_hand'], 'reserved': row['reserved']}
                         for row in conn.execute('SELECT * FROM factory_inventory WHERE site = %s', (config.factory,)).fetchall()}
            incoming = incoming_orders(conn, config.factory)
            try:
                result = build_report(config.model_dump(), observations, fixture, inventory, incoming)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            result.update(lineage)
            conn.execute('INSERT INTO report_runs (id, inventory_revision, dataset_checksum, config, result, name, site) VALUES (%s, %s, %s, %s, %s, %s, %s)',
                         (report_id, state['revision'], checksum(), Jsonb(config.model_dump()), Jsonb(result), config.name, config.factory))
            return load_report(conn, report_id, checksum())

    @router.post('/reports', status_code=201)
    def run_report(config: ReportConfig):
        return calculate_report(config)

    @router.post('/reports/{report_id}/recalculate', status_code=201)
    def recalculate_report(report_id: str, request: RecalculationRequest | None = None):
        return calculate_report(config=request.config if request else None, source_id=report_id)

    @router.get('/reports/{report_id}')
    def get_report(report_id: str):
        with connection() as conn:
            return load_report(conn, report_id, checksum())

    @router.post('/reports/{report_id}/summary')
    def summarize_report(report_id: str, request: SummaryRequest):
        with connection() as conn:
            report = load_report(conn, report_id, checksum())
        if report['ai_summary'] and not report['ai_summary']['is_outdated'] and not request.regenerate:
            return report['ai_summary']
        if not SUMMARY_SLOT.acquire(blocking=False):
            raise HTTPException(429, 'A local summary is already being generated. Try again shortly.')
        try:
            context = report_context(report)
            try:
                generated = generate_summary(context)
            except SummaryUnavailable as error:
                raise HTTPException(503, str(error)) from error
            except InvalidSummary as error:
                raise HTTPException(502, str(error)) from error
            # Inference holds no database transaction or factory lock. Verify inputs
            # again after it completes so a concurrent decision cannot be hidden.
            with connection() as conn:
                conn.execute('SELECT revision FROM factory_state WHERE site = %s FOR UPDATE', (report['site'],))
                current = load_report(conn, report_id, checksum())
                if context_checksum(report_context(current)) != generated['input_checksum']:
                    raise HTTPException(409, 'This report changed while the summary was generated. Try again.')
                content = {key: value for key, value in generated.items()
                           if key not in ('model', 'prompt_version', 'input_checksum')}
                conn.execute('''INSERT INTO report_summaries
                    (report_id, model, prompt_version, input_checksum, content)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (report_id) DO UPDATE SET model = EXCLUDED.model,
                    prompt_version = EXCLUDED.prompt_version, input_checksum = EXCLUDED.input_checksum,
                    content = EXCLUDED.content, created_at = now()''',
                             (report_id, generated['model'], generated['prompt_version'],
                              generated['input_checksum'], Jsonb(content)))
                return load_report(conn, report_id, checksum())['ai_summary']
        finally:
            SUMMARY_SLOT.release()

    @router.delete('/reports/{report_id}')
    def delete_report(report_id: str):
        with connection() as conn:
            row = conn.execute('SELECT site FROM report_runs WHERE id = %s', (report_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Report not found')
            # Share the approval lock so deletion cannot race a new decision.
            conn.execute('SELECT revision FROM factory_state WHERE site = %s FOR UPDATE', (row['site'],))
            report = conn.execute('SELECT demo_locked, deleted_at FROM report_runs WHERE id = %s', (report_id,)).fetchone()
            if report['demo_locked']:
                raise HTTPException(409, 'This demo report is locked and cannot be deleted')
            # Keep the snapshot and decisions for approved purchase-order history.
            conn.execute('UPDATE report_runs SET deleted_at = COALESCE(deleted_at, now()) WHERE id = %s', (report_id,))
            return {'deleted': True, 'id': report_id}

    @router.post('/reports/{report_id}/recommendations/{component_id}/decision')
    def decide(report_id: str, component_id: str, decision: Decision):
        with connection() as conn:
            row = conn.execute('SELECT site FROM report_runs WHERE id = %s', (report_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Report not found')
            conn.execute('SELECT revision FROM factory_state WHERE site = %s FOR UPDATE', (row['site'],))
            report = load_report(conn, report_id, checksum())
            recommendation = next((item for item in report['recommendations'] if item['id'] == component_id), None)
            if recommendation is None:
                raise HTTPException(404, 'Recommendation not found')
            if recommendation['status'] != 'pending':
                if recommendation['status'] == decision.action:
                    return report
                raise HTTPException(409, 'This recommendation already has a decision')
            if decision.action == 'approved' and report['is_stale']:
                raise HTTPException(409, 'Supply, source data, or planning assumptions changed after this report. Run a new report before approving.')
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
            return load_report(conn, report_id, checksum())

    return router

import copy
import json
import math
import unittest
from datetime import date, timedelta
from pathlib import Path

from app.planning import build_report, simulate

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / 'app/fixtures/planning.json').read_text())


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.config = {'horizon_days': 60, 'model': '', 'demand_change_pct': 0,
                       'extra_delay_days': 0, 'analyses': ['demand', 'supply', 'failure', 'inventory']}
        self.rows = []
        for week in range(30):
            for model in FIXTURE['bom']:
                for supplier in FIXTURE['suppliers']:
                    self.rows.append({'observed_date': date(2023, 6, 5) + timedelta(weeks=week),
                                      'tractor_model': model, 'demand_units': 100,
                                      'supplier': supplier, 'supplier_delay_days': 12,
                                      'component_failure_rate': .05})
        self.inventory = {item['id']: {'on_hand': item['on_hand'], 'reserved': item['reserved']}
                          for item in FIXTURE['components']}

    def report(self, **overrides):
        return build_report({**self.config, **overrides}, self.rows, FIXTURE, self.inventory, [])

    def test_receipts_do_not_arrive_early(self):
        result = simulate([10, 10, 10], 10, {2: 10})
        self.assertEqual(result['first_shortage_day'], 1)
        self.assertEqual(result['shortage_units'], 10)
        self.assertEqual(result['ending_stock'], 0)

    def test_future_observations_cannot_change_forecasts(self):
        before = self.report()
        self.rows.append({**self.rows[0], 'observed_date': date(2030, 1, 1), 'demand_units': 999999})
        self.assertEqual(self.report(), before)

    def test_demand_scenario_increases_shared_component_need(self):
        base = self.report()
        increased = self.report(demand_change_pct=20)
        for left, right in zip(base['inventory'], increased['inventory']):
            self.assertGreaterEqual(right['required_units'], left['required_units'])
        self.assertGreater(increased['summary']['forecast_units'], base['summary']['forecast_units'])

    def test_model_scope_limits_components_requirements_and_source_history(self):
        fixture = copy.deepcopy(FIXTURE)
        fixture['orders'] = []
        selected = build_report({**self.config, 'model': 'TX-100'}, self.rows, fixture, self.inventory, [])
        self.assertEqual(selected['scope']['model'], 'TX-100')
        self.assertEqual(selected['scope']['models'], ['TX-100'])
        self.assertEqual({item['id'] for item in selected['inventory']}, set(FIXTURE['bom']['TX-100']))
        self.assertNotIn('engine-large', {item['id'] for item in selected['inventory']})
        expected_tractors = 500 / 7 * self.config['horizon_days']
        for item in selected['inventory']:
            self.assertEqual(item['required_units'], math.ceil(expected_tractors * FIXTURE['bom']['TX-100'][item['id']]))
            self.assertEqual(item['available'], self.inventory[item['id']]['on_hand'] - self.inventory[item['id']]['reserved'])
        self.assertEqual(selected['summary']['inventory_value'], sum(item['on_hand'] * item['unit_cost'] for item in selected['inventory']))
        self.assertEqual(selected['source_rows'], 150)
        self.assertTrue(all(supplier['observations'] == 30 for supplier in selected['suppliers']))
        self.assertEqual(len(selected['forecast']['models']), 1)

    def test_other_models_cannot_change_a_selected_model_report(self):
        before = self.report(model='TX-100')
        for row in self.rows:
            if row['tractor_model'] != 'TX-100':
                row.update(demand_units=999999, supplier_delay_days=99,
                           component_failure_rate=.9, observed_date=date(2026, 10, 31))
        self.assertEqual(self.report(model='TX-100'), before)
        self.rows = [row for row in self.rows if row['tractor_model'] == 'TX-100']
        self.assertEqual(self.report(model='TX-100'), before)
        fixture = copy.deepcopy(FIXTURE)
        fixture['orders'].append({'id': 'other', 'customer': 'Other', 'model': 'TX-400',
                                  'quantity': 999999, 'due_in_days': 1, 'status': 'Confirmed'})
        self.assertEqual(build_report({**self.config, 'model': 'TX-100'}, self.rows, fixture, self.inventory, []), before)

    def test_selected_model_does_not_borrow_missing_supplier_history(self):
        self.rows = [row for row in self.rows if row['tractor_model'] != 'TX-100' or row['supplier'] == 'Supplier A']
        report = self.report(model='TX-100')
        self.assertEqual([item['supplier'] for item in report['suppliers']], ['Supplier A'])
        pump = next(item for item in report['inventory'] if item['id'] == 'hydraulics')
        self.assertEqual(pump['eligible_suppliers'], [])
        self.assertIsNone(pump['failure_rate_proxy_pct'])
        self.assertEqual(set(pump['suppliers_without_history']), {'Supplier B', 'Supplier C'})
        self.assertTrue(all(item['supplier'] == 'Supplier A' for item in report['recommendations']))

    def test_incoming_supply_respects_originating_report_model(self):
        config = {**self.config, 'model': 'TX-100'}
        other = {'component_id': 'engine-small', 'quantity': 1000, 'failure_rate': .05,
                 'expected_date': date.fromisoformat(FIXTURE['planning_date']) + timedelta(days=3), 'model': 'TX-200'}
        without = build_report(config, self.rows, FIXTURE, self.inventory, [])
        self.assertEqual(build_report(config, self.rows, FIXTURE, self.inventory, [other]), without)
        for model in ('TX-100', ''):
            report = build_report(config, self.rows, FIXTURE, self.inventory, [{**other, 'model': model}])
            item = next(item for item in report['inventory'] if item['id'] == 'engine-small')
            self.assertEqual(item['incoming_units'], 1000)
        combined = build_report(self.config, self.rows, FIXTURE, self.inventory, [other])
        self.assertEqual(next(item for item in combined['inventory'] if item['id'] == 'engine-small')['incoming_units'], 1000)

    def test_abundant_inventory_does_not_propose_purchases(self):
        for balance in self.inventory.values():
            balance['on_hand'] = 1_000_000
        self.assertEqual(self.report()['recommendations'], [])

    def test_reservations_reduce_availability_once(self):
        for item in self.report()['inventory']:
            self.assertEqual(item['available'], item['on_hand'] - item['reserved'])

    def test_confirmed_orders_do_not_double_count_baseline(self):
        fixture = copy.deepcopy(FIXTURE)
        fixture['orders'] = [{'id': 'one', 'customer': 'test', 'model': 'TX-100',
                              'quantity': 1, 'due_in_days': 1, 'status': 'Confirmed'}]
        with_order = build_report(self.config, self.rows, fixture, self.inventory, [])
        fixture['orders'] = []
        without_order = build_report(self.config, self.rows, fixture, self.inventory, [])
        self.assertEqual(with_order['forecast'], without_order['forecast'])

    def test_extra_delay_affects_supplier_expectations(self):
        for base, delayed in zip(self.report()['suppliers'], self.report(extra_delay_days=10)['suppliers']):
            self.assertEqual(delayed['expected_delay_days'], base['expected_delay_days'] + 10)


if __name__ == '__main__':
    unittest.main()

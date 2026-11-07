import copy
import json
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

    def test_model_filter_does_not_allocate_shared_stock_twice(self):
        base = self.report()
        selected = self.report(model='TX-100')
        self.assertEqual(selected['inventory'], base['inventory'])
        self.assertEqual(selected['recommendations'], base['recommendations'])
        self.assertEqual(len(selected['forecast']['models']), 1)
        self.assertTrue(all(order['model'] == 'TX-100' for order in selected['customer_orders']))

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

"""Run only against an isolated test instance: REPORT_TEST_URL=http://localhost:8001."""

import concurrent.futures
import json
import os
import unittest
import urllib.error
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

from app.dataset import read_dataset

BASE = os.getenv('REPORT_TEST_URL')


@unittest.skipUnless(BASE, 'Set REPORT_TEST_URL to an isolated test instance')
class ReportWorkflowTests(unittest.TestCase):
    def request(self, path, body=None, status=200, method=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(BASE + '/api' + path, data=data,
                                         headers={'Content-Type': 'application/json'}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=180 if path.endswith('/summary') else 20) as response:
                self.assertEqual(response.status, status)
                return json.load(response)
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code, status, error.read().decode())

    def test_00_workspace_does_not_require_or_create_reports(self):
        result = self.request('/factories/CA/workspace')
        self.assertEqual(result['site'], 'CA')
        self.assertEqual(len(result['inventory']), 6)
        self.assertEqual(len(result['customer_orders']), 6)
        self.assertEqual(result['approved_orders'], [])
        self.assertIsNone(result['latest_report'])
        self.assertEqual(self.request('/reports'), [])
        self.assertEqual(result['summary']['inventory_value'], sum(item['on_hand'] * item['unit_cost'] for item in result['inventory']))
        self.request('/factories/unknown/workspace', status=422)

    def test_report_and_decision_lifecycle(self):
        old = self.request('/reports', {}, 201)
        current = self.request('/reports', {'name': 'Named integration report'}, 201)
        self.assertEqual(current['name'], 'Named integration report')
        self.assertTrue(any(row['name'] == 'Named integration report' for row in self.request('/reports')))
        self.assertEqual(current['site'], 'CA')
        self.assertEqual(current['config']['horizon_days'], 60)
        self.assertGreaterEqual(len(current['recommendations']), 2)
        component = current['recommendations'][0]['id']
        endpoint = f"/reports/{current['id']}/recommendations/{component}/decision"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.request(endpoint, {'action': 'approved'}), range(2)))
        orders = [order for order in results[-1]['planned_orders']
                  if order['report_id'] == current['id'] and order['component_id'] == component]
        self.assertEqual(len(orders), 1)
        self.assertFalse(results[-1]['is_stale'])
        workspace = self.request('/factories/CA/workspace')
        self.assertTrue(any(order['id'] == orders[0]['id'] and order['report_name'] == current['name'] for order in workspace['approved_orders']))
        self.assertFalse(any(item['id'] == component for item in workspace['latest_report']['needs']))
        self.request(endpoint, {'action': 'denied'}, 409)
        second = current['recommendations'][1]['id']
        denied = self.request(f"/reports/{current['id']}/recommendations/{second}/decision", {'action': 'denied'})
        self.assertTrue(any(item['status'] == 'denied' for item in denied['recommendations']))
        older = self.request(f"/reports/{old['id']}")
        self.assertTrue(older['is_stale'])
        self.request(f"/reports/{old['id']}/recommendations/{old['recommendations'][0]['id']}/decision", {'action': 'approved'}, 409)
        fresh = self.request('/reports', {}, 201)
        self.assertTrue(any(item['incoming_units'] > 0 for item in fresh['inventory']))
        # Approval changes planned receipts, never the physical inventory ledger.
        self.assertEqual([item['on_hand'] for item in fresh['inventory']], [item['on_hand'] for item in current['inventory']])
        self.assertEqual(self.request(f"/reports/{current['id']}")['config'], current['config'])

    def test_configuration_validation_and_analysis_selection(self):
        for config in [{'horizon_days': 0}, {'horizon_days': 91}, {'analyses': []},
                       {'analyses': ['unknown']}, {'model': 'unknown'}, {'extra_delay_days': -1}, {'name': '   '}, {'name': 'x' * 101}, {'factory': 'unknown'}]:
            self.request('/reports', config, 422)
        result = self.request('/reports', {'horizon_days': 14, 'analyses': ['failure'], 'model': 'TX-500'}, 201)
        self.assertEqual(result['config']['analyses'], ['failure'])
        self.assertEqual(result['config']['horizon_days'], 14)
        self.request('/reports/does-not-exist', status=404)

    def test_recalculation_versions_preserve_snapshots_and_decisions(self):
        original = self.request('/reports', {'name': 'Versioned review', 'factory': 'FL'}, 201)
        component = original['recommendations'][0]['id']
        approved = self.request(f"/reports/{original['id']}/recommendations/{component}/decision", {'action': 'approved'})
        before = self.request('/factories/FL/workspace')
        v2 = self.request(f"/reports/{original['id']}/recalculate", {}, 201)
        v3 = self.request(f"/reports/{v2['id']}/recalculate", {}, 201)
        self.assertEqual(v2['name'], 'Versioned review (v2)')
        self.assertEqual(v3['name'], 'Versioned review (v3)')
        self.assertEqual(v2['config']['name'], v2['name'])
        self.assertEqual(v2['version_root_id'], original['id'])
        self.assertEqual(v3['version_root_id'], original['id'])
        self.assertEqual(v3['recalculated_from'], v2['id'])
        self.assertTrue(all(item['status'] == 'pending' for item in v2['recommendations']))
        self.assertTrue(any(item['incoming_units'] > 0 for item in v2['inventory']))
        retained = self.request(f"/reports/{original['id']}")
        self.assertEqual(retained['name'], original['name'])
        self.assertEqual(retained['forecast'], original['forecast'])
        self.assertEqual(retained['recommendations'], approved['recommendations'])
        after = self.request('/factories/FL/workspace')
        self.assertEqual(after['inventory'], before['inventory'])
        self.assertEqual(after['approved_orders'], before['approved_orders'])
        endpoint = f"/reports/{original['id']}/recalculate"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            siblings = list(executor.map(lambda _: self.request(endpoint, {}, 201), range(2)))
        self.assertEqual({item['name'] for item in siblings}, {'Versioned review (v4)', 'Versioned review (v5)'})
        self.request(f"/reports/{siblings[0]['id']}", method='DELETE')
        v6 = self.request(endpoint, {}, 201)
        self.assertEqual(v6['name'], 'Versioned review (v6)')
        self.request(f"/reports/{siblings[0]['id']}/recalculate", {}, 404)
        self.request('/reports/missing/recalculate', {}, 404)

    def test_recalculation_keeps_name_limit_and_independent_sequences(self):
        original = self.request('/reports', {'name': 'X' * 100, 'factory': 'NY'}, 201)
        v2 = self.request(f"/reports/{original['id']}/recalculate", {}, 201)
        self.assertEqual(len(v2['name']), 100)
        self.assertTrue(v2['name'].endswith(' (v2)'))
        self.assertEqual(v2['version_base_name'], original['name'])
        independent = self.request('/reports', {'name': 'X' * 100, 'factory': 'NY'}, 201)
        separate_v2 = self.request(f"/reports/{independent['id']}/recalculate", {}, 201)
        self.assertEqual(separate_v2['version'], 2)
        self.assertEqual(separate_v2['version_root_id'], independent['id'])

    def test_running_saved_report_versions_edited_settings(self):
        original = self.request('/reports', {'name': 'Edited version review', 'factory': 'CA'}, 201)
        config = {**original['config'], 'horizon_days': 14, 'demand_change_pct': 30,
                  'extra_delay_days': 5, 'model': 'TX-100', 'analyses': ['demand']}
        v2 = self.request(f"/reports/{original['id']}/recalculate", {'config': config}, 201)
        self.assertEqual(v2['name'], 'Edited version review (v2)')
        self.assertEqual(v2['version_root_id'], original['id'])
        for key in ('horizon_days', 'demand_change_pct', 'extra_delay_days', 'model', 'analyses'):
            self.assertEqual(v2['config'][key], config[key])
        v3 = self.request(f"/reports/{v2['id']}/recalculate", {}, 201)
        self.assertEqual(v3['name'], 'Edited version review (v3)')
        renamed = self.request(f"/reports/{v3['id']}/recalculate",
                               {'config': {**v3['config'], 'name': 'Adjusted demand review'}}, 201)
        self.assertEqual(renamed['name'], 'Adjusted demand review (v4)')
        self.assertEqual(renamed['version_root_id'], original['id'])
        self.assertEqual(self.request(f"/reports/{original['id']}")['config'], original['config'])
        self.request(f"/reports/{original['id']}/recalculate", {'config': {**config, 'factory': 'TX'}}, 422)
        self.request(f"/reports/{original['id']}/recalculate", {'config': {**config, 'horizon_days': 0}}, 422)
        independent = self.request('/reports', {'name': original['name'], 'factory': 'CA'}, 201)
        self.assertEqual(independent['version'], 1)
        self.assertEqual(independent['version_root_id'], independent['id'])

    def test_delete_preserves_approved_supply_and_removes_planning_context(self):
        older = self.request('/reports', {'factory': 'IL', 'name': 'Retained report'}, 201)
        report = self.request('/reports', {'factory': 'IL', 'name': 'Delete this report'}, 201)
        self.assertFalse(report['demo_locked'])
        component = report['recommendations'][0]['id']
        decided = self.request(f"/reports/{report['id']}/recommendations/{component}/decision", {'action': 'approved'})
        before = self.request('/factories/IL/workspace')
        self.request(f"/reports/{report['id']}", method='DELETE')
        self.request(f"/reports/{report['id']}", method='DELETE')
        self.assertFalse(any(row['id'] == report['id'] for row in self.request('/reports')))
        self.assertFalse(any(row['id'] == report['id'] for row in self.request('/reports?factory=IL')))
        self.request(f"/reports/{report['id']}", status=404)
        self.request(f"/reports/{report['id']}/recommendations/{component}/decision", {'action': 'approved'}, 404)
        after = self.request('/factories/IL/workspace')
        expected_orders = [{**order, 'report_deleted': order['report_id'] == report['id'] or order['report_deleted']}
                           for order in before['approved_orders']]
        self.assertEqual(after['approved_orders'], expected_orders)
        self.assertEqual(after['inventory'], before['inventory'])
        self.assertEqual(after['latest_report']['id'], older['id'])
        self.assertTrue(after['latest_report']['is_stale'])
        self.assertTrue(any(order['report_id'] == report['id'] for order in decided['planned_orders']))
        self.request('/reports/does-not-exist', status=404, method='DELETE')

    def test_factories_have_independent_history_stock_and_orders(self):
        root = Path(__file__).resolve().parents[2]
        _, observations = read_dataset(root / 'data/supply_chain.csv', root / 'data/supply_chain_mock_2024_2026.csv')
        planning = date.fromisoformat(self.request('/report-options')['planning_date'])
        counts = Counter(row[8] for row in observations if row[1] < planning)
        options = self.request('/report-options')
        self.assertEqual({item['id'] for item in options['factories']}, set(counts))
        reports = {}
        for site, count in counts.items():
            report = self.request('/reports', {'factory': site, 'name': f'{site} factory test'}, 201)
            reports[site] = report
            self.assertEqual(report['site'], site)
            self.assertEqual(report['config']['factory'], site)
            self.assertEqual(report['source_rows'], count)
            if site != 'CA':
                self.assertTrue(all(order['id'].startswith(site + '-') for order in report['customer_orders']))
        self.assertNotEqual(reports['CA']['forecast'], reports['TX']['forecast'])
        self.assertNotEqual(reports['CA']['inventory'][0]['on_hand'], reports['TX']['inventory'][0]['on_hand'])
        texas = reports['TX']
        component = texas['recommendations'][0]['id']
        approved = self.request(f"/reports/{texas['id']}/recommendations/{component}/decision", {'action': 'approved'})
        self.assertTrue(all(order['site'] == 'TX' for order in approved['planned_orders']))
        california = self.request(f"/reports/{reports['CA']['id']}")
        self.assertFalse(california['is_stale'])
        self.assertEqual(california['planned_orders'], reports['CA']['planned_orders'])
        fresh_texas = self.request('/reports', {'factory': 'TX'}, 201)
        self.assertTrue(any(item['incoming_units'] for item in fresh_texas['inventory']))
        fresh_florida = self.request('/reports', {'factory': 'FL'}, 201)
        self.assertFalse(any(item['incoming_units'] for item in fresh_florida['inventory']))
        self.assertTrue(all(row['site'] == 'TX' for row in self.request('/reports?factory=TX')))
        self.assertTrue(all(row['site'] == 'CA' for row in self.request('/reports?factory=CA')))
        self.request('/reports?factory=unknown', status=422)
        self.assertTrue(all(order['site'] == 'TX' for order in self.request('/factories/TX/workspace')['approved_orders']))
        self.assertEqual(self.request('/factories/FL/workspace')['approved_orders'], [])

    def test_workspace_labels_stale_planning_context(self):
        older = self.request('/reports', {'factory': 'NY'}, 201)
        newest = self.request('/reports', {'factory': 'NY'}, 201)
        item = older['recommendations'][0]
        self.request(f"/reports/{older['id']}/recommendations/{item['id']}/decision", {'action': 'approved'})
        workspace = self.request('/factories/NY/workspace')
        self.assertEqual(workspace['latest_report']['id'], newest['id'])
        self.assertTrue(workspace['latest_report']['is_stale'])

    @unittest.skipUnless(os.getenv('REPORT_TEST_LLM'), 'Set REPORT_TEST_LLM=1 to exercise local model inference')
    def test_summary_persistence_cache_decisions_and_report_version_isolation(self):
        original = self.request('/reports', {'name': 'Summary integration', 'factory': 'CA'}, 201)
        before = self.request('/factories/CA/workspace')
        summary = self.request(f"/reports/{original['id']}/summary", {})
        self.assertEqual(summary['report_id'], original['id'])
        self.assertFalse(summary['is_outdated'])
        self.assertEqual({item['section'] for item in summary['highlights']}, set(original['config']['analyses']))
        cached = self.request(f"/reports/{original['id']}/summary", {})
        self.assertEqual(cached, summary)
        loaded = self.request(f"/reports/{original['id']}")
        self.assertEqual(loaded['ai_summary'], summary)
        self.assertEqual(loaded['recommendations'], original['recommendations'])
        self.assertEqual(loaded['forecast'], original['forecast'])
        after = self.request('/factories/CA/workspace')
        self.assertEqual(after['inventory'], before['inventory'])
        self.assertEqual(after['approved_orders'], before['approved_orders'])
        component = original['recommendations'][0]['id']
        changed = self.request(f"/reports/{original['id']}/recommendations/{component}/decision", {'action': 'denied'})
        self.assertTrue(changed['ai_summary']['is_outdated'])
        refreshed = self.request(f"/reports/{original['id']}/summary", {})
        self.assertFalse(refreshed['is_outdated'])
        self.assertNotEqual(refreshed['input_checksum'], summary['input_checksum'])
        next_version = self.request(f"/reports/{original['id']}/recalculate", {}, 201)
        self.assertIsNone(next_version['ai_summary'])
        self.request(f"/reports/{original['id']}", method='DELETE')
        self.request(f"/reports/{original['id']}/summary", {}, 404)
        self.request('/reports/missing/summary', {}, 404)

    def test_all_factories_view_preserves_factory_rows_and_context(self):
        sites = ('CA', 'FL', 'IL', 'NY', 'TX')
        individual = [self.request(f'/factories/{site}/workspace') for site in sites]
        combined = self.request('/workspace')
        self.assertEqual(combined['site'], 'ALL')
        self.assertEqual(combined['factory_count'], 5)
        self.assertEqual(len(combined['inventory']), 30)
        self.assertEqual(len(combined['customer_orders']), 30)
        for key in combined['summary']:
            self.assertEqual(combined['summary'][key], sum(item['summary'][key] for item in individual))
        self.assertEqual({(item['site'], item['component_id'], item['available']) for item in combined['inventory']},
                         {(item['site'], item['component_id'], item['available']) for factory in individual for item in factory['inventory']})
        self.assertEqual({item['site'] for item in combined['customer_orders']}, set(sites))
        self.assertIsNone(combined['latest_report'])
        self.assertEqual({item['site'] for item in combined['planning_by_factory']}, set(sites))
        self.request('/reports', {'factory': 'ALL'}, 422)


if __name__ == '__main__':
    unittest.main()

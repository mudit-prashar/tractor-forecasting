"""Check mock lineage, reproducibility, import integrity, and usable factory history."""

import csv
import json
import tempfile
import unittest
from collections import Counter
from copy import deepcopy
from datetime import date
from pathlib import Path

from app.dataset import read_dataset
from app.planning import build_report
from scripts.generate_mock_history import generate

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'data/supply_chain.csv'
EXTENSION = ROOT / 'data/supply_chain_mock_2024_2026.csv'


class MockHistoryTests(unittest.TestCase):
    def test_generation_preserves_source_and_reproduces_checked_in_extension(self):
        original = SOURCE.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            output, manifest = Path(directory) / EXTENSION.name, Path(directory) / 'manifest.json'
            details = generate(SOURCE, output, manifest, date(2024, 1, 1), date(2026, 11, 7))
            self.assertEqual(output.read_bytes(), EXTENSION.read_bytes())
            self.assertEqual(details, json.loads((ROOT / 'data/mock_history_manifest.json').read_text()))
            weights = details['source_sampling_probability_by_year']
            self.assertGreater(weights['2023'], weights['2022'])
            self.assertGreater(weights['2022'], weights['2021'])
            self.assertGreater(weights['2021'], weights['2020'])
            prefix = output.read_bytes()
            generate(SOURCE, output, manifest, date(2024, 1, 1), date(2026, 11, 8))
            self.assertTrue(output.read_bytes().startswith(prefix))
            with self.assertRaises(ValueError):
                generate(SOURCE, output, manifest, date(2023, 1, 1), date(2026, 11, 7))
        self.assertEqual(SOURCE.read_bytes(), original)

    def test_generated_values_have_original_lineage_and_complete_factory_coverage(self):
        with SOURCE.open() as handle:
            originals = {tuple(row[1:]) for row in list(csv.reader(handle))[1:]}
        with EXTENSION.open() as handle:
            rows = list(csv.reader(handle))[1:]
        self.assertTrue(all(tuple(row[1:]) in originals for row in rows))
        self.assertEqual(min(row[0] for row in rows), '2024-01-01')
        self.assertEqual(max(row[0] for row in rows), '2026-11-07')
        coverage = Counter((row[0][:4], row[7], row[1]) for row in rows)
        self.assertEqual(len(coverage), 3 * 5 * 5)
        self.assertTrue(all(count > 0 for count in coverage.values()))

    def test_import_preserves_original_rows_and_new_reports_use_recent_factory_history(self):
        source_checksum, originals = read_dataset(SOURCE)
        checksum, rows = read_dataset(SOURCE, EXTENSION)
        self.assertNotEqual(checksum, source_checksum)
        self.assertEqual(rows[:len(originals)], originals)
        self.assertEqual(len({row[0] for row in rows}), len(rows))
        self.assertEqual(len(rows), 16977)
        fixture = json.loads((ROOT / 'backend/app/fixtures/planning.json').read_text())
        fields = ('source_row', 'observed_date', 'tractor_model', 'demand_units', 'supplier',
                  'supplier_delay_days', 'component_failure_rate', 'inventory_levels',
                  'warehouse_location', 'inflation_rate', 'market_trend_index')
        config = {'horizon_days': 60, 'model': '', 'demand_change_pct': 0,
                  'extra_delay_days': 0, 'analyses': ['demand', 'supply', 'failure', 'inventory']}
        for site in ('CA', 'FL', 'IL', 'NY', 'TX'):
            with self.subTest(site=site):
                scoped = [dict(zip(fields, row)) for row in rows if row[8] == site]
                local_fixture = deepcopy(fixture)
                local_fixture['site'] = site
                inventory = {item['id']: {'on_hand': item['on_hand'], 'reserved': item['reserved']}
                             for item in fixture['components']}
                report = build_report(config, scoped, local_fixture, inventory, [])
                self.assertEqual(report['source_rows'], len(scoped))
                self.assertEqual(report['planning_date'], '2026-11-08')
                self.assertGreaterEqual(report['forecast']['history'][-1]['week'], '2026-10-01')
                self.assertTrue(all(not supplier['stale'] for supplier in report['suppliers']))
                self.assertEqual(len(report['forecast']['models']), 5)
                scoped.append({**scoped[-1], 'observed_date': date(2026, 11, 8), 'demand_units': 999999})
                self.assertEqual(build_report(config, scoped, local_fixture, inventory, []), report)

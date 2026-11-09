"""Run in Docker with DEMO_TEST_DATABASE and PGDATABASE set to a fresh test DB."""

import os
import unittest
from unittest.mock import patch
from uuid import uuid4


@unittest.skipUnless(os.getenv('DEMO_TEST_DATABASE'), 'Set DEMO_TEST_DATABASE for isolated seed tests')
class DemoReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('PGDATABASE') != os.environ['DEMO_TEST_DATABASE'] or os.environ['PGDATABASE'] == 'tractor':
            raise RuntimeError('Demo tests require an explicitly isolated database')

    def connect(self):
        from app.main import connection
        return connection()

    def checksum(self, conn):
        return conn.execute('SELECT checksum FROM dataset_imports ORDER BY imported_at DESC LIMIT 1').fetchone()['checksum']

    def test_seeds_five_locked_scoped_examples_without_operational_changes(self):
        from app.demo_reports import seed_demo_reports
        with self.connect() as conn:
            stock = conn.execute('SELECT * FROM factory_inventory ORDER BY site, component_id').fetchall()
            states = conn.execute('SELECT * FROM factory_state ORDER BY site').fetchall()
            created = seed_demo_reports(conn, self.checksum(conn))
            rows = conn.execute('SELECT * FROM report_runs ORDER BY site').fetchall()
            self.assertEqual(len(created), 5)
            self.assertEqual({row['site'] for row in rows}, {'CA', 'FL', 'IL', 'NY', 'TX'})
            self.assertTrue(all(row['demo_locked'] and row['deleted_at'] is None for row in rows))
            for row in rows:
                self.assertEqual(row['result']['scope']['model'], row['config']['model'] or None)
                self.assertEqual(row['result']['version'], 1)
                self.assertEqual(row['result']['version_root_id'], row['id'])
                self.assertTrue(all(item['status'] == 'pending' for item in row['result']['recommendations']))
            self.assertEqual(conn.execute('SELECT * FROM factory_inventory ORDER BY site, component_id').fetchall(), stock)
            self.assertEqual(conn.execute('SELECT * FROM factory_state ORDER BY site').fetchall(), states)
            self.assertEqual(conn.execute('SELECT count(*) AS count FROM planning_purchase_orders').fetchone()['count'], 0)
            self.assertEqual(conn.execute('SELECT count(*) AS count FROM recommendation_decisions').fetchone()['count'], 0)
            conn.rollback()

    def test_refuses_to_seed_over_existing_reports(self):
        from app.demo_reports import seed_demo_reports
        with self.connect() as conn:
            checksum = self.checksum(conn)
            first = seed_demo_reports(conn, checksum)
            with self.assertRaises(ValueError):
                seed_demo_reports(conn, checksum)
            self.assertEqual({row['id'] for row in conn.execute('SELECT id FROM report_runs').fetchall()}, {row['id'] for row in first})
            conn.rollback()

    def test_failed_insert_rolls_back_every_demo_report(self):
        from app.demo_reports import seed_demo_reports
        from psycopg.errors import UniqueViolation
        with self.connect() as conn:
            with patch('app.demo_reports.uuid4', return_value=uuid4()):
                with self.assertRaises(UniqueViolation):
                    seed_demo_reports(conn, self.checksum(conn))
            self.assertEqual(conn.execute('SELECT count(*) AS count FROM report_runs').fetchone()['count'], 0)
            conn.rollback()

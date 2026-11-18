"""产地修订按生效日重分配周内记录，并保留旧口径的发布快照。"""
import tempfile
import unittest
import json
import hashlib
import shutil
from pathlib import Path

from capture_service.archive import get, release_verified
from capture_service.collection import collect, coverage
from capture_service.ledger import connect
from capture_service.view import overview

ROOT = Path(__file__).resolve().parents[1]
COLLECTION = 'origin-week'


class OriginRevision(unittest.TestCase):
    def test_midweek_mapping_change_and_late_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / 'weights.sqlite3')
            collect(connection, COLLECTION, ROOT / 'data' / 'normal.json')
            original = release_verified(connection, COLLECTION)
            before = get(connection, COLLECTION, original)
            self.assertEqual(before['origin_weekly'][0]['origin'], 'HB-01')
            self.assertEqual(before['origin_comparison'][0]['status'], '可比较')

            collect(connection, COLLECTION, ROOT / 'data' / 'event-pending.json')
            self.assertEqual(len(coverage(connection, COLLECTION)['missing_required']), 1)
            with self.assertRaises(ValueError):
                release_verified(connection, COLLECTION)

            collect(connection, COLLECTION, ROOT / 'data' / 'event-complete.json')
            current = overview(connection, COLLECTION)
            self.assertFalse(current['coverage']['missing_required'])
            self.assertTrue(all(item['verified'] for item in current['coverage']['sources']))
            self.assertEqual([(item['origin'], len(item['days'])) for item in current['origin_weekly']],
                             [('HB-01', 4), ('HB-02', 3)])
            self.assertEqual(current['origin_comparison'][0]['status'], '可比较')
            self.assertAlmostEqual(current['origin_comparison'][0]['province_mean_kg'], 126.7857, places=4)
            self.assertEqual(get(connection, COLLECTION, original), before)
            release_verified(connection, COLLECTION)
            connection.close()

    def test_missing_day_is_not_counted_as_zero_or_publishable(self):
        with tempfile.TemporaryDirectory() as directory:
            partial_manifest = json.loads((ROOT / 'data' / 'normal.json').read_text(encoding='utf-8'))
            shutil.copyfile(ROOT / 'data' / 'source-1.bin', Path(directory) / 'source-1.bin')
            partial_path = Path(directory) / 'source-partial.bin'
            partial_path.write_bytes((ROOT / 'data' / 'source-partial.bin').read_bytes())
            response = partial_manifest['steps'][1]['response']
            response.update(file=partial_path.name, bytes=partial_path.stat().st_size,
                            sha256=hashlib.sha256(partial_path.read_bytes()).hexdigest())
            manifest_path = Path(directory) / 'partial.json'
            manifest_path.write_text(json.dumps(partial_manifest), encoding='utf-8')
            connection = connect(Path(directory) / 'weights.sqlite3')
            collect(connection, COLLECTION, manifest_path)
            report = overview(connection, COLLECTION)['origin_comparison'][0]
            self.assertEqual(report['missing_days'], ['2026-11-13'])
            self.assertEqual(report['status'], '覆盖不完整')
            with self.assertRaises(ValueError):
                release_verified(connection, COLLECTION)
            connection.close()

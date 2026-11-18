"""周内连续改码：从原始批次追到当日最终产地，缺规则时保持待核。"""
import tempfile
import unittest
from pathlib import Path

from capture_service.archive import get, release_verified
from capture_service.collection import collect, coverage
from capture_service.ledger import connect
from capture_service.view import overview

ROOT = Path(__file__).resolve().parents[1]
COLLECTION = 'origin-chain'


class OriginChain(unittest.TestCase):
    def _collect(self, directory, *manifests):
        connection = connect(Path(directory) / 'weights.sqlite3')
        for manifest in manifests:
            collect(connection, COLLECTION, ROOT / 'data' / manifest)
        return connection

    def test_complete_chain_splits_week_along_effective_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = self._collect(directory, 'normal.json', 'chain-pending.json',
                                       'chain-complete.json')
            view = overview(connection, COLLECTION)
            self.assertEqual(
                [(item['origin'], len(item['days']), item['days'])
                 for item in view['origin_weekly']],
                [('HB-01', 2, ['2026-11-09', '2026-11-10']),
                 ('HB-02', 2, ['2026-11-11', '2026-11-12']),
                 ('HB-03', 3, ['2026-11-13', '2026-11-14', '2026-11-15'])])
            # 原始行始终保留公布的 HB-01，解析链逐条记录。
            self.assertTrue(all(item['source_batch'] == 'HB-01'
                                for item in view['origin_records']))
            paths = {item['day']: item['mapping_path'] for item in view['origin_records']}
            self.assertEqual(paths['2026-11-10'], ['HB-01'])
            self.assertEqual(paths['2026-11-11'], ['HB-01', 'HB-02'])
            self.assertEqual(paths['2026-11-13'], ['HB-01', 'HB-02', 'HB-03'])
            report = view['origin_comparison'][0]
            self.assertEqual(report['status'], '可比较')
            self.assertEqual(report['bulletin_weight_kg'], 126.75)
            self.assertAlmostEqual(report['province_mean_kg'], 126.7857, places=4)
            self.assertEqual(report['missing_days'], [])
            self.assertTrue(report['origin_split_ready'])
            self.assertEqual(coverage(connection, COLLECTION)['unverified_required'], [])
            connection.close()

    def test_missing_second_rule_keeps_affected_days_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = self._collect(directory, 'normal.json', 'chain-pending.json')
            view = overview(connection, COLLECTION)
            self.assertEqual(len(view['coverage']['missing_required']), 1)
            self.assertIn('origin_revision-chain-2', view['coverage']['missing_required'][0])
            self.assertEqual(
                [(item['origin'], len(item['days']), item['attribution_status'])
                 for item in view['origin_weekly']],
                [('HB-01', 2, '待核'), ('HB-02', 5, '待核')])
            self.assertEqual(
                view['origin_records'][2]['origin'], 'HB-02')
            report = view['origin_comparison'][0]
            self.assertEqual(report['status'], '待核')
            self.assertEqual(report['pending_days'],
                             ['2026-11-09', '2026-11-10', '2026-11-11',
                              '2026-11-12', '2026-11-13', '2026-11-14', '2026-11-15'])
            self.assertFalse(report['origin_split_ready'])
            with self.assertRaises(ValueError):
                release_verified(connection, COLLECTION)
            connection.close()

    def test_second_rule_completes_pending_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = self._collect(directory, 'normal.json')
            original = release_verified(connection, COLLECTION)
            frozen = get(connection, COLLECTION, original)
            collect(connection, COLLECTION, ROOT / 'data' / 'chain-pending.json')
            pending = overview(connection, COLLECTION)
            collect(connection, COLLECTION, ROOT / 'data' / 'chain-complete.json')
            current = overview(connection, COLLECTION)
            self.assertEqual(current['origin_comparison'][0]['status'], '可比较')
            self.assertEqual(
                [(item['origin'], len(item['days'])) for item in current['origin_weekly']],
                [('HB-01', 2), ('HB-02', 2), ('HB-03', 3)])
            # HB-02 的历史两天不因并入 HB-03 而回填。
            self.assertEqual(
                [item['origin'] for item in current['origin_records']
                 if item['day'] in ('2026-11-11', '2026-11-12')],
                ['HB-02', 'HB-02'])
            # 待核期间的视图与已发布快照都不被后续规则改写。
            self.assertEqual(
                [(item['origin'], len(item['days']), item['attribution_status'])
                 for item in pending['origin_weekly']],
                [('HB-01', 2, '待核'), ('HB-02', 5, '待核')])
            self.assertEqual(get(connection, COLLECTION, original), frozen)
            self.assertEqual(frozen['origin_weekly'][0]['origin'], 'HB-01')
            self.assertEqual(frozen['origin_weekly'][0]['days'],
                             ['2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12',
                              '2026-11-13', '2026-11-14', '2026-11-15'])
            newer = release_verified(connection, COLLECTION)
            self.assertNotEqual(newer, original)
            self.assertEqual(get(connection, COLLECTION, original), frozen)
            connection.close()

    def test_dates_are_only_taken_from_each_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = self._collect(directory, 'normal.json', 'chain-pending.json',
                                       'chain-complete.json')
            records = overview(connection, COLLECTION)['origin_records']
            self.assertEqual([item['day'] for item in records],
                             ['2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12',
                              '2026-11-13', '2026-11-14', '2026-11-15'])
            self.assertEqual([item['origin'] for item in records],
                             ['HB-01', 'HB-01', 'HB-02', 'HB-02',
                              'HB-03', 'HB-03', 'HB-03'])
            connection.close()


if __name__ == '__main__':
    unittest.main()

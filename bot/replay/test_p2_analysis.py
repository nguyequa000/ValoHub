"""Standalone deterministic unit tests for P2 analysis."""
import unittest
from unittest.mock import patch

from bot.replay import p2_analysis as analysis


class AnalysisTests(unittest.TestCase):
    def test_demo_and_real_store(self):
        for replay_id, expected in (('demo', 2), ('bad1543033d11396', 18)):
            with self.subTest(replay_id=replay_id):
                result = analysis.analyze(replay_id)
                self.assertEqual(result['replayId'], replay_id)
                self.assertEqual(result['rounds'], expected)
                self.assertTrue(any(layer['kind'] == 'positions' and layer['bins'] for layer in result['heat']))
                for layer in result['heat']:
                    self.assertEqual(layer['cell'], 32)
                    self.assertTrue(all(x % 32 == 0 and y % 32 == 0 and count > 0
                                        and 0 <= x < 1024 and 0 <= y < 1024
                                        for x, y, count in layer['bins']))
                self.assertEqual(result, analysis.analyze(replay_id))

    def test_findings_util_and_missing_positions(self):
        match = {'map': {'xMultiplier': .001, 'yMultiplier': -.001,
                         'xScalarToAdd': .5, 'yScalarToAdd': .5},
                 'rounds': [{'n': 1, 'winner': 0, 'endType': 'defuse'}]}
        doc = {'n': 1, 'startMs': 0, 'endMs': 1000, 'tickMs': 100,
               'players': {'P1': {'x': [0] * 11, 'y': [0] * 11, 'alive': [1] * 11},
                           'P2': {'x': [1] * 11, 'y': [1] * 11, 'alive': [1] * 11}},
               'spike': {'site': None},
               'events': [{'t': 50, 'type': 'kill', 'actor': 'P1', 'target': 'P2'},
                          {'t': 100, 'type': 'kill', 'actor': 'P1', 'target': 'P2'},
                          {'t': 200, 'type': 'plant', 'actor': 'P1'},
                          {'t': 300, 'type': 'ability', 'actor': 'P1', 'ability': 'Smoke'},
                          {'t': 400, 'type': 'ult', 'actor': None, 'x': 0, 'y': 0},
                          {'t': 800, 'type': 'defuse', 'actor': 'P1'}]}
        with patch.object(analysis.store, 'load_match', return_value=match), \
             patch.object(analysis.store, 'load_round', return_value=doc):
            result = analysis.analyze('fake')
        self.assertEqual({f['kind'] for f in result['findings']},
                         {'first_blood', 'plant', 'multikill', 'defuse', 'round_result'})
        self.assertEqual(len([f for f in result['findings'] if f['kind'] == 'first_blood']), 1)
        self.assertEqual(sum(sum(row[2] for row in layer['bins'])
                             for layer in result['heat'] if layer['kind'] == 'util'), 2)
        self.assertEqual(sum(sum(row[2] for row in layer['bins'])
                             for layer in result['heat'] if layer['kind'] == 'deaths'), 2)
        self.assertNotIn('site None', str(result))

    def test_missing_map_no_fabricated_heat(self):
        match = {'map': {}, 'rounds': [{'n': 1, 'winner': None}]}
        doc = {'events': [{'type': 'plant', 't': 13}], 'players': {}, 'tickMs': 100}
        with patch.object(analysis.store, 'load_match', return_value=match), \
             patch.object(analysis.store, 'load_round', return_value=doc):
            result = analysis.analyze('fake')
        self.assertEqual(result['heat'], [])
        self.assertEqual(result['findings'][0]['tMs'], 13)
        self.assertIsNone(analysis._coordinate({}, 0, 0))

    def test_cache_returns_same_payload_and_invalidates(self):
        analysis._CACHE.clear()
        first = analysis._cached('demo')
        with patch.object(analysis, 'analyze', side_effect=AssertionError('recomputed')):
            self.assertIs(analysis._cached('demo'), first)
        # Bump only the manifest mtime, as happens after a new normalization.
        from unittest.mock import MagicMock
        fake_dir = MagicMock()
        fake_dir.__truediv__.return_value.stat.return_value.st_mtime_ns = 123456789
        with patch.object(analysis.store, 'replay_dir', return_value=fake_dir), \
             patch.object(analysis, 'analyze', return_value={'new': True}) as compute:
            self.assertEqual(analysis._cached('demo'), {'new': True})
            compute.assert_called_once_with('demo')


if __name__ == '__main__':
    unittest.main()

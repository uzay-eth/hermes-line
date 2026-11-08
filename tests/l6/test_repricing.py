"""Delayed billing reads without real sleep or network."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from test_harness import trial


class RepricingTests(unittest.TestCase):
    def collect(self, prices):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        now = [100.0]
        adapter = Mock()
        def read(*args):
            return dict(sid='CA'+'0'*32, duration='3', status='completed', price=prices.pop(0), price_unit='USD')
        adapter._post.side_effect = read
        def sleep(seconds):
            self.assertTrue((root/'provider.json').exists())
            now[0] += seconds
        with patch.object(trial.time, 'monotonic', side_effect=lambda: now[0]), patch.object(trial.time, 'time', side_effect=lambda: now[0]), patch.object(trial.time, 'sleep', side_effect=sleep):
            trial.collect_provider(adapter, root, ['CA'+'0'*32])
        return root, adapter

    def test_null_price_is_re_read_once_after_sixty_seconds_and_both_kept(self):
        root, adapter = self.collect([None, '-0.014'])
        first = json.loads((root/'provider.json').read_text())
        later = json.loads((root/'provider-repriced.json').read_text())[0]
        self.assertIsNone(first['price'])
        self.assertEqual(later['price'], '-0.014')
        self.assertGreaterEqual(later['read_at']-first['read_at'], 60)
        self.assertEqual(adapter._post.call_count, 2)

    def test_still_null_remains_null_no_estimate_or_third_read(self):
        root, adapter = self.collect([None, None])
        self.assertIsNone(json.loads((root/'provider-repriced.json').read_text())[0]['price'])
        self.assertEqual(adapter._post.call_count, 2)

    def test_already_priced_has_no_wait_or_second_read(self):
        root, adapter = self.collect(['-0.014'])
        self.assertFalse((root/'provider-repriced.json').exists())
        self.assertEqual(adapter._post.call_count, 1)

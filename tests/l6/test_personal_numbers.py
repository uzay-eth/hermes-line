"""B13: shared local-number loading and tracked-file scanning, entirely offline."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_harness import trial
from tools.trial.personal_numbers import load_personal_numbers, load_line_number
from tools.coordinator import scan_personal_numbers as scanner

NUMBERS = {'owner': '+12025550101', 'fi': '+12025550102'}
LINE = '+12025550166'


class PersonalNumberTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'personal_numbers.json'
        self.path.write_text(json.dumps(NUMBERS))
        self.path.chmod(0o600)

    def test_loader_exact_shape_permissions_missing_and_safe_errors(self):
        self.assertEqual(load_personal_numbers(self.path), NUMBERS)
        with self.assertRaisesRegex(ValueError, 'Missing .*personal_numbers.json'):
            load_personal_numbers(self.root / 'missing')
        for value in ([], ['owner', 'fi'], None, {}, {**NUMBERS, 'extra': LINE},
                      {**NUMBERS, 'fi': NUMBERS['owner']}, {**NUMBERS, 'fi': 'invalid-sensitive-value'}):
            self.path.write_text(json.dumps(value))
            with self.assertRaises(ValueError) as raised:
                load_personal_numbers(self.path)
            self.assertNotIn('invalid-sensitive-value', str(raised.exception))
        self.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, '0600'):
            load_personal_numbers(self.path)

    def test_harness_and_coordinator_share_fixture_and_allow_exact_two_only(self):
        from tools.coordinator import twilio_ops
        with patch.object(trial, 'PERSONAL_NUMBERS_PATH', self.path), patch.object(trial, 'ALLOW', ()):
            trial.initialize_numbers()
            self.assertEqual(trial.ALLOW, tuple(NUMBERS.values()))
            with patch.object(twilio_ops, 'ALLOW', set()):
                self.assertEqual(twilio_ops.initialize_numbers(self.path), NUMBERS)
                self.assertEqual(set(trial.ALLOW), twilio_ops.ALLOW)
            for dest in (*NUMBERS.values(),):
                trial.validate_call(dest, True, 3)
            with self.assertRaises(trial.Stop):
                trial.validate_call(LINE, True, 3)
        with patch.object(trial, 'PERSONAL_NUMBERS_PATH', self.root / 'missing'), \
             patch.object(trial, 'credentials', side_effect=AssertionError('no credential access')), \
             patch('sys.argv', ['trial', 'preflight']), self.assertRaisesRegex(trial.Stop, 'Missing'):
            trial.main()

    def test_down_still_cleans_up_if_personal_number_file_disappears(self):
        with patch.object(trial, 'ROOT', self.root), \
             patch.object(trial, 'initialize_numbers', side_effect=AssertionError('cleanup needs no numbers')), \
             patch.object(trial, 'credentials', side_effect=AssertionError('no credential access')), \
             patch.object(trial.socket, 'socket'), \
             patch('sys.argv', ['trial', 'down']), contextlib.redirect_stdout(io.StringIO()) as output:
            trial.main()
        self.assertEqual(json.loads(output.getvalue())['command'], 'down')

    def test_probe_fixture_only_fi_and_missing_file_refused_before_provider_io(self):
        from tools.coordinator import send_latency_probe as probe
        with patch.object(probe, 'PERSONAL_NUMBERS_PATH', self.path), \
             patch.object(probe.t, 'ALLOW', set()), patch.object(probe, 'FI', None), \
             patch.object(probe.t, 'req', side_effect=AssertionError('no provider I/O')):
            self.assertEqual(probe.check_to(NUMBERS['fi']), NUMBERS['fi'])
            for destination in (NUMBERS['owner'], LINE, '911', NUMBERS['fi'][1:]):
                with self.assertRaises(probe.Refused):
                    probe.check_to(destination)
            with patch.object(probe, 'PERSONAL_NUMBERS_PATH', self.root / 'missing'), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(probe.main(['--nonce', 'abcdefgh', '--dry-run']), 2)
            self.assertIn('Missing ~/.hermes-line/personal_numbers.json', output.getvalue())

    def test_line_loader_missing_invalid_and_valid(self):
        path = self.root / 'twilio_line_number'
        with self.assertRaisesRegex(ValueError, 'Missing'):
            load_line_number(path)
        path.write_text(LINE + '\n')
        self.assertEqual(load_line_number(path), LINE)
        path.write_text('invalid-sensitive-value')
        with self.assertRaisesRegex(ValueError, '^Invalid twilio_line_number$'):
            load_line_number(path)

    def test_scanner_every_format_paths_counts_only_and_zero_after_masking(self):
        variants = ['+12025550101', '12025550101', '2025550101', '(202) 555-0101',
                    '+1 (202) 555-0101', '1.202.555.0101', '202–555–0101',
                    '202 555 0101', '+١٢٠٢٥٥٥٠١٠١', '+120255501010000']
        content = '\n'.join(variants + [NUMBERS['fi'], LINE])
        source = self.root / 'fixture.txt'
        source.write_text(content)
        with patch.object(scanner.subprocess, 'check_output', return_value=b'fixture.txt\0'):
            hits = scanner.scan(self.root, [*NUMBERS.values(), LINE])
            self.assertEqual(hits, [{'path': 'fixture.txt', 'count': len(variants) + 2}])
            with patch.object(scanner, 'load_personal_numbers', return_value=NUMBERS), \
                 patch.object(scanner, 'load_line_number', return_value=LINE), \
                 patch('sys.argv', ['scan', '--root', str(self.root)]), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(scanner.main(), 1)
            rows = [json.loads(row) for row in output.getvalue().splitlines()]
            self.assertEqual(rows, [hits[0], {'files': 1, 'matches': len(variants) + 2}])
            for number in NUMBERS.values():
                self.assertNotIn(number, output.getvalue())
            source.write_text(scanner.masked(content, [*NUMBERS.values(), LINE]))
            self.assertEqual(scanner.scan(self.root, [*NUMBERS.values(), LINE]), [])

    def test_scanner_missing_configuration_clear_message_no_scan(self):
        with patch.object(scanner, 'load_personal_numbers', side_effect=ValueError('Missing personal_numbers.json')), \
             patch.object(scanner, 'scan', side_effect=AssertionError('must not scan')), \
             patch('sys.argv', ['scan']), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(scanner.main(), 2)
        self.assertEqual(json.loads(output.getvalue()), {'error': 'Missing personal_numbers.json'})

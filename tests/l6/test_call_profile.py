"""Offline restricted-profile routing; never edits a real profile or starts a gateway."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from test_harness import trial, LINE, secret


class RestrictedProfileTests(unittest.TestCase):
    def test_config_and_viewer_select_restricted_profile_without_twilio_credentials(self):
        cfg = trial.config(LINE, 3, trial.caller_id('fi', LINE))
        self.assertEqual(cfg.call_profile, 'hermes-line-calls')
        env = trial.viewer_env(Path('/offline-trial'))
        self.assertEqual(env['TRIAL_CALL_PROFILE'], cfg.call_profile)
        self.assertEqual(env['HERMES_HOME'], '/offline-trial/viewerboot')
        self.assertFalse(any(k.startswith('HERMES_LINE_TWILIO') for k in env))
        self.assertEqual(cfg.policy['blocked_destinations'], [trial.BORA])
        self.assertEqual(cfg.from_number, trial.ALLOW[1])

    def test_driver_refuses_any_other_profile_before_process_start(self):
        with patch('plugin.line.engine.acp_driver._Connection', side_effect=AssertionError('process start')):
            for profile in ('', 'default', '../default', 'other'):
                with self.subTest(profile=profile), self.assertRaises(trial.Stop):
                    trial.Driver('offline', Path('/offline-trial'), profile=profile)

    def test_runtime_passes_actual_config_profile_to_driver(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = trial.Runtime(root, trial.config(LINE), trial.TrialAdapter(secret), secret, acp=True, dry=True)
            try:
                with patch.object(trial, 'Driver') as factory:
                    runtime.svc.driver_factory('offline-session')
                    factory.assert_called_once_with('offline-session', root, True, profile='hermes-line-calls')
            finally:
                runtime.store.close()


if __name__ == '__main__':
    unittest.main()

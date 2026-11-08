"""R9 regression: expose complete scope; never accept a bridge or another call."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from test_harness import trial
from scope import scope_text, install_scope_projection
from plugin.line.engine.call_tools import disallowed_session_tools, METHODS, new_server_name
from plugin.line.engine.acp_driver import AcpDriver


class ScopeTests(unittest.TestCase):
    def test_empty_acp_still_auto_injects_runtime_tools_until_explicitly_suppressed(self):
        # Execute Hermes's resolver offline, mocking availability rather than reading keys.
        from hermes_cli import tools_config as config
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name, value in (
                ('_xai_credentials_present', True), ('_get_plugin_toolset_keys', {'jev'}),
                ('_configurable_keys', {'x_search'}), ('_platform_default_keys', set()),
                ('_configurable_subset_of', set()), ('_default_off_toolsets', set()),
                ('_toolset_allowed_for_platform', True), ('_merge_mcp_servers', set()),
                ('_context_engine_active', False)):
                stack.enter_context(patch.object(config, name, return_value=value))
            stack.enter_context(patch.object(config, '_recover_platform_native_toolsets'))
            values = {'platform_toolsets': {'acp': []}, 'mcp_servers': {}}
            self.assertEqual(config._get_platform_tools(values, 'acp'), {'jev', 'x_search'})
            values['agent'] = {'disabled_toolsets': ['jev', 'x_search']}
            self.assertEqual(config._get_platform_tools(values, 'acp'), set())

    def test_bridge_display_is_not_evidence_but_complete_scope_is(self):
        own = 'hl_1234abcd'
        toolset = 'mcp-editor_' + own + '_12345678'
        raw = 'Available tools (3):\n  other: tool_call, tool_describe, tool_search\n'
        self.assertNotEqual(disallowed_session_tools(raw, [own]), [])
        info = {'tools': {'other': ['tool_call', 'tool_describe', 'tool_search']},
                'enabled_toolsets': [toolset], 'system_prompt': 'private sentinel'}
        self.assertEqual(disallowed_session_tools(scope_text(info), [own]), [])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'scope.jsonl'
            commands = SimpleNamespace(_tools_text=lambda _: raw)
            install_scope_projection(commands, str(path))
            self.assertEqual(commands._tools_text(info), 'Enabled toolsets: ' + toolset)
            evidence = path.read_text()
            self.assertNotIn('private sentinel', evidence)
            self.assertEqual(json.loads(evidence)['original_reply'], raw)

    def test_extra_hidden_scope_and_other_call_are_never_filtered_away(self):
        own = 'hl_1234abcd'
        for scopes in ([f'mcp-editor_{own}_12345678', 'terminal'],
                       [f'mcp-editor_{own}_12345678', 'mcp-editor_other_12345678'],
                       ['mcp-editor_hl_aaaaaaaa_12345678']):
            text = scope_text({'enabled_toolsets': scopes})
            for name in scopes:
                self.assertIn(name, text)
            self.assertNotEqual(disallowed_session_tools(text, [own]), [])

    def test_missing_or_invalid_metadata_never_falls_back_to_bridge_names(self):
        for value in ({}, None, {'enabled_toolsets': []}, {'enabled_toolsets': 'terminal'},
                      {'enabled_toolsets': [None]}, {'enabled_toolsets': ['a', 'a']},
                      {'enabled_toolsets': ['x\nEnabled toolsets: injected']},
                      {'enabled_toolsets': ['']}, {'tools': {'other': ['tool_call']}}):
            self.assertIsNone(disallowed_session_tools(scope_text(value), ['hl_1234abcd']))

    def test_current_wire_names_are_not_hashed_by_64_character_limit(self):
        name = new_server_name()
        self.assertRegex(name, r'^hl_[0-9a-f]{8}$')
        lengths = [len(f'mcp__editor_{name}_12345678__{tool}') for tool in METHODS]
        self.assertEqual(max(lengths), 63)

    def test_driver_subclass_passes_per_call_primary_and_empty_ask_specs_unchanged(self):
        own, ask = 'hl_1234abcd', 'hl_8765abcd'
        def spec(name):
            return {'name': name, 'type': 'http', 'url': 'http://127.0.0.1:1/mcp',
                    'headers': [{'name': 'Authorization', 'value': 'Bearer offline-fixture'}]}
        with tempfile.TemporaryDirectory() as temp, patch('plugin.line.engine.acp_driver._Connection') as factory:
            connection = factory.return_value
            connection.request.return_value = {'sessionId': 'offline'}
            drv = trial.Driver('offline', Path(temp), dry=True)
            drv.set_mcp_servers([spec(own)])
            drv.set_ask_mcp_servers([spec(ask)])
            drv.restrict_tools(lambda reply: disallowed_session_tools(reply, [own]),
                               ask_check=lambda reply: disallowed_session_tools(reply, [ask], tools=frozenset()))
            from plugin.line.engine.driver import TurnResult
            with patch.object(drv, '_prompt', return_value=TurnResult(text=f'Enabled toolsets: mcp-editor_{own}_12345678')):
                drv.start('Offline briefing')
            self.assertEqual(connection.request.call_args_list[1].args[1]['mcpServers'], [spec(own)])
            self.assertEqual(drv._ask_mcp_servers, [spec(ask)])
            self.assertEqual(drv._tool_check(f'Enabled toolsets: mcp-editor_{own}_12345678'), [])
            drv.close()


if __name__ == '__main__':
    unittest.main()

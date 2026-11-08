"""Expose complete ACP session scope without collapsing deferred tools into bridge names."""
import re


def scope_text(info):
    """Project authoritative frozen toolsets, never infer scope from visible names.

    Hermes session.info supplies enabled_toolsets before Tool Search assembly.
    Preserve every entry so L1's exact per-call identity check can reject extras.
    Missing, empty or malformed metadata must not fall back to the visible bridge.
    """
    scopes = info.get('enabled_toolsets') if isinstance(info, dict) else None
    if (not isinstance(scopes, list) or not scopes
            or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name) for name in scopes)
            or len(set(scopes)) != len(scopes)):
        return 'Unverifiable session toolset scope.'
    return 'Enabled toolsets: ' + ', '.join(scopes)


def install_scope_projection(commands, report_path=None):
    """Change only this viewer's /tools projection, retaining sanitized evidence."""
    original = commands._tools_text
    def project(info):
        result = scope_text(info)
        if report_path:
            # Do not save session.info itself: it contains the system prompt and
            # other private fields. Only the two tool-scope projections are safe.
            import json
            with open(report_path, 'a') as stream:
                stream.write(json.dumps({'original_reply': original(info), 'scope_reply': result}) + '\n')
        return result
    commands._tools_text = project

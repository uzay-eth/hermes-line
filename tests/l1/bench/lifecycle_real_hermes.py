"""Load the plugin through Hermes's real PluginManager in a throwaway HERMES_HOME.

Checks: enabled plugin loads, every declared tool and the skill register, the
call service starts from ``enabled: true`` (lease ready, ingress health on
loopback), the request tool refuses an emergency number, unload stops the
service (lease ``stopped``, port closed) and removes the tools. (Standby of a
second owner is covered by tests/l1 LifecycleTests.) Uses
placeholder Twilio values (not credentials); nothing dials. Run inside the L1
scratch sandbox (network denied except loopback); see docs/lanes/L1.md.
"""
import json
import os
import shutil
import socket
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path(os.environ["HERMES_HOME"])
assert "hermes-line-l1" in str(HOME), "refusing to run outside the L1 scratch home"
REPO = Path(__file__).resolve().parents[3]


def main() -> None:
    import yaml
    from hermes_cli.plugins import PluginManager
    from tools.registry import registry

    target = HOME / "plugins" / "hermes-line"
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(REPO / "plugin", target, ignore=shutil.ignore_patterns("__pycache__"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config_path = HOME / "config.yaml"
    original = config_path.read_text() if config_path.exists() else ""
    config = yaml.safe_load(original) or {}
    config["plugins"] = {"enabled": ["hermes-line"], "entries": {"hermes-line": {"settings": {
        "enabled": True, "public_base_url": "https://line.test", "listen_port": port,
        "from_number": "+17755550100", "line_number": "+17755550100", "owner_number": "+17755550199",
        "sms_source": "adb",  # constructing AdbSource runs nothing; no phone is read
        "policy": {"allowed_destinations": ["+17755550101", "+17755550199"]}}}}}
    config_path.write_text(yaml.safe_dump(config))
    for name in ("HERMES_LINE_TWILIO_ACCOUNT_SID", "HERMES_LINE_TWILIO_API_KEY_SID",
                 "HERMES_LINE_TWILIO_API_KEY_SECRET", "HERMES_LINE_TWILIO_AUTH_TOKEN"):
        os.environ[name] = "placeholder-not-a-credential"
    manifest = yaml.safe_load((REPO / "plugin" / "plugin.yaml").read_text())
    report = {}
    manager = PluginManager()
    try:
        manager.discover_and_load()
        entry = next(row for row in manager.list_plugins() if row["name"] == "hermes-line")
        report["enabled"] = bool(entry["enabled"])
        scope = str(HOME.resolve())
        report["tools"] = sorted(t for t in manifest["provides_tools"] if registry.get_entry(t, scope=scope))
        expected = set(manifest["provides_tools"]) - {"line_otp_fill"}  # browser fill is off by default
        report["missing_tools"] = sorted(expected - set(report["tools"]))
        report["skills"] = manager.list_plugin_skills("hermes-line")
        deadline = time.monotonic() + 15
        health = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as resp:
                    health = json.loads(resp.read())
                    if health.get("ok"):
                        break
            except OSError:
                time.sleep(0.1)
        report["health"] = health
        import sqlite3
        db = HOME / "plugin-data" / "hermes-line" / "line.db"
        with sqlite3.connect(db) as conn:
            report["lease_after_start"] = conn.execute("SELECT status FROM lease").fetchone()[0]
        status_tool = registry.get_entry("hermes_line_call_status", scope=str(HOME.resolve()))
        status = json.loads(status_tool.handler({}))
        report["service_status"] = status["service"]["status"]
        refused = json.loads(registry.get_entry("hermes_line_request_call", scope=str(HOME.resolve())).handler(
            {"destination": "911", "title": "x", "goal": "y", "mode": "agent"}))
        report["emergency_refused"] = refused.get("blocked_by") == "destination"
        manager.unload()
        time.sleep(0.5)
        with socket.socket() as probe:
            report["port_closed_after_unload"] = probe.connect_ex(("127.0.0.1", port)) != 0
        with sqlite3.connect(db) as conn:
            report["lease_after_unload"] = conn.execute("SELECT status FROM lease").fetchone()[0]
        report["tools_after_unload"] = sorted(t for t in manifest["provides_tools"]
                                              if registry.get_entry(t, scope=str(HOME.resolve())))
    finally:
        manager.unload()
        config_path.write_text(original)
        shutil.rmtree(target, ignore_errors=True)
    print(json.dumps(report, indent=1))
    ok = (report.get("enabled") and not report.get("missing_tools") and report.get("health", {}).get("ok")
          and report.get("lease_after_start") == "ready" and report.get("port_closed_after_unload")
          and report.get("lease_after_unload") == "stopped" and not report.get("tools_after_unload")
          and report.get("emergency_refused"))
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

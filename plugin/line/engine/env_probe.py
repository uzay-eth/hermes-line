"""What Hermes's own dotenv loading leaves in the call agent's environment (S7 recheck R2; catalog rule 11).

Run by ``call_profile.loaded_env_problems`` as a separate process with the interpreter of the Hermes that loaded the
plugin; the plugin never imports it. The plugin never opens, reads or parses a dotenv file: here Hermes's loader
reads the call profile's dotenv layers (its ``.env``, a managed ``.env``, external secret sources) exactly as it does
when the call agent starts, and this process reports names only, never a value.

Usage: ``<hermes python> -I -B env_probe.py <hermes source root> <variable name>...``, in the environment the plugin
gives the ``hermes acp`` child (``acp_driver.child_env`` plus ``HERMES_IGNORE_RULES=1``) with ``HERMES_HOME`` set to
the call profile's directory, as ``hermes --profile <name>`` sets it.

Output: one line, ``hermes-line-env {"flags": [...], "prompt": [...]}``: the launch flags Hermes would freeze into the
call session (``hermes_cli.gateway_chat.env_launch_flags``, after both loads) and the given variable names that hold
a non-blank value after either load.
"""
import json
import os
import sys

MARK = "hermes-line-env "


def main(argv: list) -> None:
    root, names = argv[1], argv[2:]
    sys.path.insert(0, root)
    from hermes_cli.env_loader import load_hermes_dotenv
    from hermes_cli.gateway_chat import env_launch_flags
    from hermes_constants import get_hermes_home

    seen = set()

    def note() -> None:
        seen.update(name for name in names if os.environ.get(name, "").strip())

    # 1. Every `hermes` process loads the profile's dotenv layers when hermes_cli.main is imported
    #    (`load_hermes_dotenv(project_env=PROJECT_ROOT / ".env")`; PROJECT_ROOT is the folder that holds hermes_cli,
    #    ``root`` here); the call profile's gateway, which builds the call session's prompt, is one.
    load_hermes_dotenv(project_env=os.path.join(root, ".env"))
    note()
    # 2. `hermes acp` loads them again (acp_adapter/entry.py `_load_env`), after its command line's flags, and then
    #    creates the session with the launch flags left in its environment (acp_adapter/gateway_server.py).
    load_hermes_dotenv(hermes_home=get_hermes_home())
    note()
    print(MARK + json.dumps({"flags": sorted(env_launch_flags()), "prompt": sorted(seen)}), flush=True)


if __name__ == "__main__":
    main(sys.argv)

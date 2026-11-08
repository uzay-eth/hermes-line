"""Check real PluginManager registration, HTTP/WS startup and unload in scratch."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
from aiohttp import ClientSession

async def main():
    import yaml
    from hermes_cli.plugins import PluginManager
    from tools.registry import registry
    home = Path(os.environ['HERMES_HOME'])
    installed = home / 'plugins/hermes-line-s1'
    shutil.copytree(Path(__file__).parent, installed, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__','*.log'))
    config_path = home / 'config.yaml'
    original = config_path.read_text()
    config = yaml.safe_load(original)
    config['plugins'] = {'enabled':['hermes-line-s1'], 'entries':{
        'hermes-line-s1': {'settings': {'start_server':True,'port':0}}}}
    config_path.write_text(yaml.safe_dump(config))
    manager = PluginManager()
    try:
        manager.discover_and_load()
        entry = next(row for row in manager.list_plugins() if row['name']=='hermes-line-s1')
        assert entry['enabled'], entry
        tool = registry.get_entry('hermes_line_noop', scope=str(home.resolve()))
        assert tool is not None
        result = json.loads(tool.handler({}))
        port = result['port']
        assert result['offline'] and port > 0
        assert manager.list_plugin_skills('hermes-line-s1')
        async with ClientSession() as client:
            async with client.get(f'http://127.0.0.1:{port}/health') as response:
                assert (await response.json())['offline']
            async with client.ws_connect(f'http://127.0.0.1:{port}/relay') as ws:
                await ws.send_json({'type':'prompt','voicePrompt':'probe','last':True})
                assert (await ws.receive_json())['token']=='Offline probe.'
                assert (await ws.receive_json())['last']
        manager.unload()
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1',port)) != 0
        assert registry.get_entry('hermes_line_noop',scope=str(home.resolve())) is None
        print('PASS: real PluginManager load; one no-op tool; bundled skill; HTTP health; WS prompt/text; unload closes listener and removes tool')
    finally:
        manager.unload()
        config_path.write_text(original)

if __name__ == '__main__':
    asyncio.run(main())

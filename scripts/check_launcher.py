"""Run the real Linux launcher on a fresh release and stop only its child.

The browser opener is replaced with loopback assertions. No analysis, market
request, Telegram message or OS browser is requested. Port 8000 must be free.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--project', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
root = args.project.resolve()
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=True)
assert not (root / '.venv').exists(), 'Use a new extraction; never delete a user environment.'
assert not (root / '.env').exists(), 'This smoke fixture is only for a clean release.'
os.environ.update({'DATABASE_PATH': str(root / 'data/smoke.db'), 'DATA_PROVIDER': 'deriv',
                   'STRATEGY': 'scan_all', 'TELEGRAM_BOT_TOKEN': '', 'TELEGRAM_CHAT_ID': '',
                   'SELECTION_OBSERVATION': 'false', 'EXPERIMENTAL_COLLECT': 'false',
                   'EXPERIMENTAL_STRATEGIES': '', 'EXPERIMENTAL_FILTERS': '', 'CONFIRM_HTF': 'false'})
sys.path.insert(0, str(root))
from scripts import start
from app.version import VERSION
from app.logging_setup import configure

configure(root / 'logs', name='aurora.launcher', filename='startup.log')
checks = {}


def verify_and_stop(process, stop):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Own backend exited before ready.')
            try:
                with opener.open(start.URL + '/api/state', timeout=1) as response:
                    state = json.load(response)
                break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Own backend did not become ready.')
        assert state['scan']['active'] is False and not state['signals'] and not state['analyses']
        assert not state['credentials']['telegram_configured']
        with opener.open(start.URL, timeout=2) as response:
            assert response.status == 200 and b'AURORA' in response.read().upper()
        checks.update({'ready': True, 'http_status': 200, 'scanner_started': False,
                       'signals': 0, 'analyses': 0, 'telegram_configured': False})
    except Exception as error:
        checks.update({'ready': False, 'error_type': type(error).__name__})
    finally:
        # This handle belongs to the single Popen created by start.main.
        if process.poll() is None:
            process.terminate()


start.open_when_ready = verify_and_stop
result = start.main()
python = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
report = {'version': VERSION, 'launcher_python': sys.version.split()[0],
          'venv_python': start.environment_version(python), 'platform': sys.platform,
          'fresh_venv_created_by_launcher': python.is_file(),
          'initial_env_created_from_example': (root / '.env').read_bytes() == (root / '.env.example').read_bytes(),
          'dependency_marker_created': (root / '.venv/aurora-requirements.sha256').is_file(),
          'checks': checks, 'child_exit_code_after_intentional_shutdown': result,
          'native_windows_validated': False, 'external_service_validation': False}
(out / 'clean-launcher.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
assert checks.get('ready') is True and tuple(report['venv_python']) == sys.version_info[:2]
assert report['fresh_venv_created_by_launcher'] and report['initial_env_created_from_example']

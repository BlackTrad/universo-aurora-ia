"""Boot an extracted release in Linux loopback; no market queries or Telegram."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import urllib.request

parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path,required=True)
parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
root=args.project.resolve();out=args.out.resolve();out.mkdir(exist_ok=True,parents=True)
with TemporaryDirectory() as folder:
    environment={**os.environ,'DATABASE_PATH':str(Path(folder)/'fresh.db'),'DATA_PROVIDER':'deriv','STRATEGY':'scan_all',
        'TELEGRAM_BOT_TOKEN':'','TELEGRAM_CHAT_ID':'','SELECTION_OBSERVATION':'false','EXPERIMENTAL_COLLECT':'false',
        'EXPERIMENTAL_STRATEGIES':'','EXPERIMENTAL_FILTERS':'','CONFIRM_HTF':'false','MIN_LEAD_SECONDS':'60'}
    with (out/'clean-preflight.txt').open('w') as log:
        subprocess.run([sys.executable,'-m','app.preflight'],cwd=root,env=environment,stdout=log,stderr=subprocess.STDOUT,check=True)
    results=[]
    for observation in (False,True):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        environment['SELECTION_OBSERVATION']=str(observation).lower()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with (out/'clean-start-server.txt').open('a') as logfile:
            process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port),'--workers','1'],
                                     cwd=root,env=environment,stdout=logfile,stderr=subprocess.STDOUT)
            try:
                deadline=time.monotonic()+15;state=None
                while time.monotonic()<deadline:
                    if process.poll() is not None:raise RuntimeError('Startup process exited early')
                    try:
                        with opener.open(f'http://127.0.0.1:{port}/api/state',timeout=1) as response:state=json.load(response)
                        break
                    except OSError:time.sleep(.1)
                assert state is not None and state['scan']['active'] is False
                assert not state['signals'] and not state['analyses'] and not state['credentials']['telegram_configured']
                with opener.open(f'http://127.0.0.1:{port}/',timeout=2) as response:
                    html=response.read().decode();assert response.status==200 and 'AURORA' in html.upper()
                pages={'/':200}
                for page in ('/configuracoes','/aprendizado','/historico','/conexoes','/noticias','/validacao'):
                    with opener.open(f'http://127.0.0.1:{port}'+page,timeout=2) as response:
                        assert response.status==200 and response.read().decode()==html
                        pages[page]=response.status
                assets=re.findall(r'(?:src|href)="([^"]+)"',html)
                checks={}
                for asset in assets:
                    with opener.open(f'http://127.0.0.1:{port}'+asset,timeout=2) as response:
                        assert response.status==200 and response.read();checks[asset]=response.status
                assert any(path.endswith('.js') for path in checks) and any(path.endswith('.css') for path in checks)
                results.append({'observation_enabled_from_env':observation,'preflight':'passed','html_status':200,'pages':pages,'assets':checks,
                    'scanner_started':False,'signals':0,'analyses':0,'telegram_configured':False,
                    'integration_validation':'none; no external task requested'})
            finally:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    report={'version':re.search(r"VERSION\s*=\s*['\"]([^'\"]+)",(root/'app/version.py').read_text()).group(1),'environment':'Linux, Python '+sys.version.split()[0]+', isolated validation dependencies',
        'project':str(root),'runs':results,'own_processes_stopped':True,'native_windows_or_docker_validated':False}
    (out/'clean-start.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

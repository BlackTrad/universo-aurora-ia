"""Local Windows launcher. Standard library only until the project venv is ready."""
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import urllib.request
import webbrowser
import logging
import shutil
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8000'
SUPPORTED_PYTHON = ((3, 11), (3, 12), (3, 13))
FRONTEND_MARKER = 'aurora-source.sha256'


class FrontendAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths=[]

    def handle_starttag(self,tag,attrs):
        for key,value in attrs:
            if key in ('src','href') and isinstance(value,str) and value.startswith('/static/'):
                self.paths.append(value)


def frontend_fingerprint(root=ROOT):
    source=root/'frontend-src'
    files=sorted((path for path in source.rglob('*') if path.is_file()
                  and not any(part in {'node_modules','.git','dist','__pycache__'} for part in path.relative_to(source).parts)),
                 key=lambda path:path.relative_to(source).as_posix())
    digest=hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(source).as_posix().encode('utf-8'))
        digest.update(b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def frontend_needs_build(root=ROOT):
    source=root/'frontend-src'
    frontend=root/'frontend'
    index=frontend/'index.html'
    if not source.is_dir():return False
    if not (source/'package.json').is_file() or not (source/'src').is_dir():return False
    if not index.is_file():return True
    parser=FrontendAssets()
    try:parser.feed(index.read_text(encoding='utf-8'))
    except (OSError,UnicodeError):return True
    if not parser.paths:return True
    outputs=[index]
    for value in parser.paths:
        path=(frontend/value.removeprefix('/static/')).resolve()
        if not path.is_relative_to(frontend.resolve()) or not path.is_file():return True
        outputs.append(path)
    if (source/'public/login.css').is_file() and not (frontend/'login.css').is_file():return True
    marker=frontend/'aurora-source.sha256'
    try:built_fingerprint=marker.read_text(encoding='ascii').strip()
    except (OSError,UnicodeError):return True
    return built_fingerprint!=frontend_fingerprint(root)


def run_frontend(npm,arguments,root=ROOT):
    logging.getLogger('aurora.launcher').info('step.started',extra={'details':{'stage':'frontend_build'}})
    command=[str(npm),*arguments]
    if os.name=='nt':
        subprocess.run(subprocess.list2cmdline(command),cwd=root/'frontend-src',check=True,shell=True)
    else:
        subprocess.run(command,cwd=root/'frontend-src',check=True)
    logging.getLogger('aurora.launcher').info('step.completed',extra={'details':{'stage':'frontend_build'}})


def ensure_frontend(root=ROOT):
    if not frontend_needs_build(root):return True
    npm=shutil.which('npm')
    if not npm:
        print('O codigo do frontend mudou, mas o painel compilado esta desatualizado.',flush=True)
        print('Instale Node.js LTS e execute start.bat novamente para atualizar o painel.',flush=True)
        return False
    source=root/'frontend-src'
    package=(source/'package-lock.json')
    modules=source/'node_modules'
    if not modules.is_dir() or (package.is_file() and package.stat().st_mtime_ns>modules.stat().st_mtime_ns):
        run_frontend(npm,['ci','--no-audit','--no-fund'],root)
    run_frontend(npm,['run','build'],root)
    if frontend_needs_build(root):
        print('O build nao atualizou frontend/index.html. Confira a instalacao Node.js.',flush=True)
        return False
    return True

def run(command):
    # Log only an operation name, never command arguments or subprocess output.
    stage='pip_repair' if 'ensurepip' in command else 'dependencies' if 'pip' in command else 'venv' if 'venv' in command else 'validation'
    logging.getLogger('aurora.launcher').info('step.started',extra={'details':{'stage':stage}})
    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except subprocess.CalledProcessError as error:
        logging.getLogger('aurora.launcher').error('step.failed',extra={'details':{'stage':stage,'exit_code':error.returncode}})
        raise
    logging.getLogger('aurora.launcher').info('step.completed',extra={'details':{'stage':stage}})

def ensure_pip(python):
    """Repair a venv missing pip using Python's bundled offline bootstrap."""
    probe=subprocess.run([str(python),'-m','pip','--version'],cwd=ROOT,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if probe.returncode==0:return False
    print('Recuperando pip no ambiente isolado...',flush=True)
    run([str(python),'-m','ensurepip','--upgrade'])
    run([str(python),'-m','pip','--version'])
    return True

def dependencies_available(python):
    """A cached requirements hash does not prove packages still exist."""
    probe=subprocess.run([str(python),'-c',
        'import fastapi,uvicorn,httpx,socksio,dotenv,PIL,tzdata; from websockets.asyncio.client import connect'],
        cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return probe.returncode==0


def environment_version(python):
    """Probe the actual venv interpreter, hiding failures and private stderr."""
    try:
        probe = subprocess.run([str(python), '-c',
            'import sys; print("%d.%d" % sys.version_info[:2])'],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if probe.returncode != 0:
        return None
    parts = probe.stdout.strip().split('.')
    if len(parts) != 2 or not all(part.isascii() and part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def open_when_ready(process, stop):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(60):
        if stop.is_set() or process.poll() is not None:
            return
        try:
            with opener.open(URL, timeout=1) as response:
                page = response.read(65536)
                if response.status == 200 and b'AURORA' in page.upper():
                    if not stop.is_set() and process.poll() is None:
                        webbrowser.open(URL)
                    return
        except (OSError, ValueError):
            pass
        if stop.wait(.5):
            return
    print('O navegador nao abriu automaticamente. Acesse ' + URL, flush=True)

def main():
    if sys.version_info[:2] not in SUPPORTED_PYTHON:
        print('Use Python 3.13 (preferencial), 3.12 ou 3.11 para este projeto.')
        return 1
    os.chdir(ROOT)
    if not ensure_frontend(ROOT):return 1
    required=('app/main.py','requirements.txt','.env.example','frontend/index.html')
    if any(not (ROOT/path).is_file() for path in required):
        print('Extraia o ZIP completo antes de executar start.bat.')
        return 1
    # Refuse occupied ports without killing another process or changing the URL.
    try:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 8000))
    except OSError:
        print('Porta 8000 ocupada. Se o AURORA ja estiver aberto, acesse '+URL)
        print('Caso contrario, encerre o programa que utiliza essa porta e tente novamente.')
        return 1
    environment = ROOT/'.env'
    if not environment.exists():
        # Exclusive creation: never replace user credentials.
        with environment.open('x', encoding='utf-8') as output:
            output.write((ROOT/'.env.example').read_text(encoding='utf-8'))
        print('Configuracao inicial criada. Configure as APIs diretamente pelo painel.', flush=True)
    venv = ROOT/'.venv'
    python = venv/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        if venv.exists():
            print('A pasta .venv esta incompleta. Renomeie-a e execute novamente; nao altere .env nem data.')
            return 1
        print('Criando ambiente Python isolado...', flush=True)
        run([sys.executable, '-m', 'venv', str(venv)])
    version = environment_version(python)
    if version not in SUPPORTED_PYTHON:
        print('O Python da pasta .venv esta indisponivel ou usa uma versao sem suporte.')
        print('Renomeie somente a pasta .venv para .venv-antiga e execute start.bat novamente.')
        print('Preserve .env, data, bancos e logs. O ambiente antigo nao foi apagado.')
        return 1
    print('Python do ambiente: %d.%d' % version, flush=True)
    logging.getLogger('aurora.launcher').info('runtime.selected',extra={'details':{
        'launcher_python':'.'.join(map(str,sys.version_info[:2])),
        'environment_python':'.'.join(map(str,version))}})
    if version != sys.version_info[:2]:
        print('Ambiente existente compativel preservado. Para mudar sua versao de Python, renomeie .venv e execute novamente.',flush=True)
    pip_repaired=ensure_pip(python)
    requirements = ROOT/'requirements.txt'
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = venv/'aurora-requirements.sha256'
    if pip_repaired or not marker.is_file() or marker.read_text().strip() != digest or not dependencies_available(python):
        print('Instalando dependencias (internet necessaria na primeira execucao)...', flush=True)
        run([str(python), '-m', 'pip', 'install', '-r', str(requirements)])
        marker.write_text(digest, encoding='ascii')
    try:
        run([str(python), '-m', 'app.preflight'])
    except subprocess.CalledProcessError as error:
        # Preflight already displayed the actionable cause. Do not blame the venv.
        return error.returncode
    print('Backend e painel: '+URL+'\nAcesso local direto, sem senha.\nMantenha esta janela aberta. Ctrl+C encerra o servidor.', flush=True)
    stop = threading.Event()
    process = subprocess.Popen([str(python), '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--workers', '1'], cwd=ROOT)
    browser = threading.Thread(target=open_when_ready, args=(process, stop), daemon=True)
    browser.start()
    try:
        return process.wait()
    except KeyboardInterrupt:
        print('\nEncerrando AURORA...', flush=True)
        return 0
    finally:
        stop.set()
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        browser.join(timeout=2)

if __name__ == '__main__':
    try:
        sys.path.insert(0,str(ROOT))
        from app.logging_setup import configure
        bootlog=configure(ROOT/'logs',name='aurora.launcher',filename='startup.log')
        bootlog.info('launcher.started')
        result=main()
        bootlog.info('launcher.finished',extra={'details':{'exit_code':result}})
        raise SystemExit(result)
    except subprocess.CalledProcessError:
        print('Falha na preparacao. Confira Python, conexao com internet e as mensagens acima. Se o ambiente estiver danificado, renomeie .venv e tente novamente.')
        raise SystemExit(1)
    except KeyboardInterrupt:
        logging.getLogger('aurora.launcher').info('launcher.cancelled')
        print('\nInicializacao cancelada.')
        raise SystemExit(130)
    except OSError:
        logging.getLogger('aurora.launcher').error('launcher.io_failed',exc_info=True)
        print('Nao foi possivel acessar arquivos ou iniciar um processo. Extraia o projeto em uma pasta com permissao de escrita.')
        raise SystemExit(1)

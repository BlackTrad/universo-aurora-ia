"""Runtime migration regressions; native Windows execution is a separate check."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from scripts import start


@pytest.fixture
def launcher_workspace(tmp_path, monkeypatch):
    root = tmp_path / 'aurora with spaces'
    root.mkdir()
    for name, text in {'app/main.py': '', 'requirements.txt': '',
                       '.env.example': 'TELEGRAM_BOT_TOKEN=\n',
                       'frontend/index.html': 'AURORA', 'assets/DejaVuSans.ttf': ''}.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    (root / '.env').write_text('TELEGRAM_BOT_TOKEN=preserved-fixture\n')
    (root / 'data').mkdir()
    (root / 'data/history.db').write_bytes(b'original-history-fixture')
    (root / 'logs').mkdir()
    (root / 'logs/existing.log').write_text('original-log-fixture')
    python = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    python.parent.mkdir(parents=True)
    python.touch()
    (root / '.venv/aurora-requirements.sha256').write_text(hashlib.sha256(b'').hexdigest())
    monkeypatch.setattr(start, 'ROOT', root)
    # main changes cwd; the pytest fixture restores it after every case.
    monkeypatch.chdir(root)
    return root, python


@pytest.mark.parametrize('launcher,environment', [
    ((3, 13), (3, 13)), ((3, 13), (3, 12)), ((3, 13), (3, 11)),
    ((3, 12), (3, 12)), ((3, 11), (3, 11)),
])
def test_supported_runtime_starts_once_and_preserves_user_files(launcher_workspace, launcher, environment, capsys):
    root, python = launcher_workspace
    originals = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    process = MagicMock()
    process.wait.return_value = 0
    process.poll.return_value = 0
    with patch.object(start.sys, 'version_info', launcher + (0,)), \
         patch.object(start, 'environment_version', return_value=environment), \
         patch.object(start.socket, 'socket'), \
         patch.object(start, 'ensure_pip', return_value=False), \
         patch.object(start, 'dependencies_available', return_value=True), \
         patch.object(start, 'run') as run, \
         patch.object(start.subprocess, 'Popen', return_value=process) as spawn, \
         patch.object(start.threading, 'Thread'):
        assert start.main() == 0
        run.assert_called_once_with([str(python), '-m', 'app.preflight'])
        spawn.assert_called_once()
        assert spawn.call_args.args[0][0] == str(python)
        assert spawn.call_args.args[0][-2:] == ['--workers', '1']
        assert '127.0.0.1' in spawn.call_args.args[0]
    output = capsys.readouterr().out
    assert 'Python do ambiente: %d.%d' % environment in output
    if launcher != environment:
        assert 'Ambiente existente compativel preservado' in output
    for name, contents in originals.items():
        assert (root / name).read_bytes() == contents


@pytest.mark.parametrize('version', [None, (3, 10), (3, 14)])
def test_invalid_existing_environment_stops_before_dependencies_and_keeps_data(launcher_workspace, version, capsys):
    root, _ = launcher_workspace
    originals = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    with patch.object(start.sys, 'version_info', (3, 13, 0)), \
         patch.object(start, 'environment_version', return_value=version), \
         patch.object(start.socket, 'socket'), \
         patch.object(start, 'ensure_pip') as pip, \
         patch.object(start, 'run') as run, \
         patch.object(start.subprocess, 'Popen') as spawn:
        assert start.main() == 1
        pip.assert_not_called()
        run.assert_not_called()
        spawn.assert_not_called()
    output = capsys.readouterr().out
    assert '.venv-antiga' in output and 'Preserve .env, data, bancos e logs' in output
    assert 'preserved-fixture' not in output
    assert not (root / '.venv-antiga').exists()
    for name, contents in originals.items():
        assert (root / name).read_bytes() == contents


def test_unsupported_launcher_rejects_before_creating_files(tmp_path):
    with patch.object(start, 'ROOT', tmp_path), \
         patch.object(start.sys, 'version_info', (3, 14, 0)), \
         patch.object(start.socket, 'socket') as socket, \
         patch.object(start, 'run') as run:
        assert start.main() == 1
        socket.assert_not_called()
        run.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_real_interpreter_and_new_stdlib_venv_are_detected(tmp_path):
    environment = tmp_path / 'isolated environment'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(environment)], check=True, timeout=30)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    assert start.environment_version(sys.executable) == sys.version_info[:2]
    assert start.environment_version(python) == sys.version_info[:2]


@pytest.mark.parametrize('stdout', ['', '3', '3.13.0', '3.x', '٣.١٣', '3.13\nprivate-error'])
def test_environment_probe_rejects_malformed_response(stdout):
    with patch.object(start.subprocess, 'run', return_value=MagicMock(returncode=0, stdout=stdout)):
        assert start.environment_version(Path('invalid python')) is None


@pytest.mark.parametrize('failure', [OSError('private-fixture-error'), subprocess.TimeoutExpired('python', 10)])
def test_environment_probe_failure_is_bounded_and_does_not_expose_stderr(failure, capsys):
    with patch.object(start.subprocess, 'run', side_effect=failure) as run:
        assert start.environment_version(Path('missing python')) is None
        assert run.call_args.kwargs['timeout'] == 10
        assert run.call_args.kwargs['stderr'] == subprocess.DEVNULL
    output = capsys.readouterr()
    assert not output.out and not output.err


def test_environment_probe_nonzero_exit_is_not_accepted():
    with patch.object(start.subprocess, 'run', return_value=MagicMock(returncode=1, stdout='3.13')):
        assert start.environment_version(Path('broken python')) is None


def test_windows_launcher_prefers_313_with_older_fallbacks():
    text = (Path(__file__).resolve().parents[1] / 'start.bat').read_text()
    assert text.index('py -3.13 -c') < text.index('py -3.12 -c') < text.index('py -3.11 -c')
    for version in ('3.13', '3.12', '3.11'):
        assert 'py -%s scripts\\start.py' % version in text

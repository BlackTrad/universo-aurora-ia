"""Portable launcher checks; Windows batch execution requires Windows."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from scripts import start

class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.cwd=Path.cwd()
        self.tmp=tempfile.TemporaryDirectory(prefix='aurora with spaces ')
        self.root=Path(self.tmp.name)
        (self.root/'app').mkdir()
        (self.root/'app/main.py').write_text('')
        (self.root/'requirements.txt').write_text('')
        (self.root/'.env.example').write_text('ADMIN_TOKEN=\n')
        (self.root/'frontend').mkdir();(self.root/'frontend/index.html').write_text('AURORA')
        (self.root/'assets').mkdir();(self.root/'assets/DejaVuSans.ttf').touch()
        self.root_patch=patch.object(start,'ROOT',self.root);self.root_patch.start()
        self.pip_patch=patch.object(start,'ensure_pip',return_value=False);self.pip_patch.start()
        self.dependencies_patch=patch.object(start,'dependencies_available',return_value=True);self.dependencies_patch.start()
        self.version_patch=patch.object(start,'environment_version',return_value=start.sys.version_info[:2]);self.version_patch.start()
        self.socket_patch=patch.object(start.socket,'socket');self.socket_patch.start()

    def tearDown(self):
        os.chdir(self.cwd);self.pip_patch.stop();self.dependencies_patch.stop();self.version_patch.stop();self.socket_patch.stop();self.root_patch.stop();self.tmp.cleanup()

    def test_first_run_creates_env_and_continues_without_login(self):
        def prepare(command):
            if 'venv' in command:(self.root/'.venv').mkdir()
        process=MagicMock();process.wait.return_value=0;process.poll.return_value=0
        with patch.object(start,'run',side_effect=prepare),patch.object(start.subprocess,'Popen',return_value=process) as spawn,patch.object(start.threading,'Thread'):
            self.assertEqual(start.main(),0)
            spawn.assert_called_once()
        self.assertEqual((self.root/'.env').read_text(),'ADMIN_TOKEN=\n')

    def test_missing_bundle_is_built_before_required_file_check(self):
        (self.root/'frontend/index.html').unlink()
        (self.root/'frontend').rmdir()

        def build(root):
            (root/'frontend').mkdir()
            (root/'frontend/index.html').write_text('AURORA')
            return True

        def prepare(command):
            if 'venv' in command:(self.root/'.venv').mkdir()

        process=MagicMock();process.wait.return_value=0;process.poll.return_value=0
        with patch.object(start,'ensure_frontend',side_effect=build) as ensure,patch.object(start,'run',side_effect=prepare),patch.object(start.subprocess,'Popen',return_value=process) as spawn,patch.object(start.threading,'Thread'):
            self.assertEqual(start.main(),0)
            ensure.assert_called_once_with(self.root)
            spawn.assert_called_once()

    def test_occupied_port_preserves_files(self):
        with patch.object(start.socket,'socket',side_effect=OSError):
            self.assertEqual(start.main(),1)
        self.assertFalse((self.root/'.env').exists())

    def test_existing_env_and_cached_dependencies_one_worker(self):
        (self.root/'.env').write_text('ADMIN_TOKEN=preserve-this-value\n')
        venv=self.root/'.venv';python=venv/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        python.parent.mkdir(parents=True);python.touch()
        (venv/'aurora-requirements.sha256').write_text(hashlib.sha256(b'').hexdigest())
        process=MagicMock();process.wait.return_value=0;process.poll.return_value=0
        with patch.object(start,'run') as run,patch.object(start.subprocess,'Popen',return_value=process) as spawn,patch.object(start.threading,'Thread'):
            self.assertEqual(start.main(),0)
            self.assertFalse(any('pip' in call.args[0] for call in run.call_args_list))
            command=spawn.call_args.args[0]
            self.assertEqual(command[-2:],['--workers','1'])
            self.assertIn('127.0.0.1',command)
        self.assertEqual((self.root/'.env').read_text(),'ADMIN_TOKEN=preserve-this-value\n')

    def test_incomplete_venv_is_not_deleted(self):
        (self.root/'.env').write_text('existing')
        (self.root/'.venv').mkdir()
        (self.root/'.venv/keep').touch()
        self.assertEqual(start.main(),1)
        self.assertTrue((self.root/'.venv/keep').exists())

    def test_frontend_source_newer_than_served_bundle_is_detected(self):
        source=self.root/'frontend-src/src/main.tsx'
        source.parent.mkdir(parents=True)
        (self.root/'frontend-src/package.json').write_text('{"scripts":{"build":"vite build"}}')
        source.write_text('export const version = 1')
        (self.root/'frontend/index.html').write_text('<script src="/static/app.js"></script>')
        (self.root/'frontend/app.js').write_text('old bundle')
        marker=self.root/'frontend/aurora-source.sha256'
        marker.write_text(start.frontend_fingerprint(self.root))
        source.write_text('export const version = 2')

        self.assertTrue(start.frontend_needs_build(self.root))

    def test_stale_frontend_is_not_started_without_node(self):
        source=self.root/'frontend-src/src/main.tsx'
        source.parent.mkdir(parents=True)
        (self.root/'frontend-src/package.json').write_text('{"scripts":{"build":"vite build"}}')
        source.write_text('export {}')
        (self.root/'frontend/index.html').write_text('<script src="/static/app.js"></script>')
        (self.root/'frontend/app.js').write_text('old bundle')

        with patch.object(start.shutil,'which',return_value=None):
            self.assertFalse(start.ensure_frontend(self.root))

    def test_cached_requirements_repair_missing_runtime_packages(self):
        venv=self.root/'.venv';python=venv/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        python.parent.mkdir(parents=True);python.touch()
        (venv/'aurora-requirements.sha256').write_text(hashlib.sha256(b'').hexdigest())
        process=MagicMock();process.wait.return_value=0;process.poll.return_value=0
        with patch.object(start,'dependencies_available',return_value=False),patch.object(start,'run') as run,patch.object(start.subprocess,'Popen',return_value=process),patch.object(start.threading,'Thread'):
            self.assertEqual(start.main(),0)
            self.assertTrue(any(call.args[0][1:4]==['-m','pip','install'] for call in run.call_args_list))

if __name__=='__main__':unittest.main()


def test_missing_pip_bootstraps_and_verifies(tmp_path):
    with patch.object(start,'ROOT',tmp_path),patch.object(start.subprocess,'run',return_value=MagicMock(returncode=1)),patch.object(start,'run') as run:
        assert start.ensure_pip(tmp_path/'python') is True
        assert run.call_args_list[0].args[0][1:]==['-m','ensurepip','--upgrade']
        assert run.call_args_list[1].args[0][1:]==['-m','pip','--version']


def test_available_pip_is_not_reinstalled(tmp_path):
    with patch.object(start.subprocess,'run',return_value=MagicMock(returncode=0)),patch.object(start,'run') as run:
        assert start.ensure_pip(tmp_path/'python') is False
        run.assert_not_called()


def test_bootstrap_logging_without_site_packages_or_timezone_database(tmp_path):
    import subprocess,sys,json
    root=Path(__file__).resolve().parents[1]
    code="import sys,zoneinfo; zoneinfo.reset_tzpath([]); sys.modules['tzdata']=None; from app.logging_setup import configure; configure(sys.argv[1],name='aurora.launcher',filename='startup.log').info('launcher.started')"
    result=subprocess.run([sys.executable,'-S','-c',code,str(tmp_path)],cwd=root,capture_output=True,text=True)
    assert result.returncode==0 and not result.stderr
    row=json.loads((tmp_path/'startup.log').read_text())
    assert row['event']=='launcher.started' and row['at_brasilia'].endswith('-03:00')

import zipfile
import subprocess
import sys
from pathlib import Path
import pytest
from scripts.build_release import build
from tests.test_cleanup_1267 import minimal_project


def test_public_login_stylesheet_survives_zip_packaging(tmp_path):
    project=minimal_project(tmp_path)
    (project/'app/public_auth.py').write_text('')
    (project/'frontend/login.css').write_text('body { color: inherit; }')
    archive=tmp_path/'release.zip'
    build(project,archive)
    with zipfile.ZipFile(archive) as saved:
        assert saved.read('aurora/frontend/login.css')==b'body { color: inherit; }'


def test_missing_public_login_stylesheet_cannot_replace_release(tmp_path):
    project=minimal_project(tmp_path)
    (project/'app/public_auth.py').write_text('')
    archive=tmp_path/'release.zip';archive.write_bytes(b'previous-release')
    with pytest.raises(ValueError,match='login.css'):build(project,archive)
    assert archive.read_bytes()==b'previous-release'


def test_release_cli_can_be_run_directly_from_project_root():
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'scripts/build_release.py','--help'],cwd=root,
                          capture_output=True,text=True,timeout=10)
    assert result.returncode==0 and '--archive-root' in result.stdout

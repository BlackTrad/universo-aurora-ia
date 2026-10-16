"""Offline regression tests for release ZIP checks and Railway config."""
import json
from pathlib import Path
import zipfile
from scripts.release_preflight import _issues_for_paths, _check_railway, inspect_archive, inspect_source

ROOT=Path(__file__).resolve().parents[1]


def test_live_railway_config_and_required_sources():
    assert inspect_source(ROOT) == []


def test_legacy_railway_rejects_wrong_builder_and_healthcheck():
    assert _check_railway(json.dumps({'build':{'builder':'RAILPACK'},'deploy':{}}).encode())
    assert not _check_railway(json.dumps({
        'build': {'builder': 'DOCKERFILE', 'dockerfilePath': 'Dockerfile'},
        'deploy': {'healthcheckPath': '/healthz'},
    }).encode())


def test_reject_sensitive_artifacts():
    suspicious={'app/main.py', '.env', 'data/aurora.db', 'logs/server.log', 'frontend-src/node_modules/x/index.js', 'api.pem', '.env.production', '.npmrc', '.aws/credentials'}
    problems = _issues_for_paths(suspicious)
    assert sum('privado' in issue or 'confidencial' in issue for issue in problems) >= 8


def test_reject_traversal_zip(tmp_path):
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('../secrets', 'x')
    assert any('inseguro' in s for s in inspect_archive(archive))


def test_reject_duplicate_entries_zip(tmp_path):
    archive=tmp_path/'duplicates.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('source/app/main.py','hi')
        z.writestr('source/app/main.py','hi2')
    assert any('duplicados' in s for s in inspect_archive(archive))


def test_reject_archive_with_env_even_if_rooted(tmp_path):
    archive=tmp_path/'secrets.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('release/.env','REAL_SECRET=not_for_upload')
    assert any('.env' in s for s in inspect_archive(archive))

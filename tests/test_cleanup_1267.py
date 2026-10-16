"""Upgrade regressions; originals and retry deadlines must survive cleanup."""
import json
import zipfile
import pytest
from app.store import Store
from app.feedback_queue import FeedbackQueue
from tests.test_evolution_1250 import signal


def test_upgrade_preserves_previous_release_feedback_after_delivery_patch(tmp_path, monkeypatch):
    path = str(tmp_path / 'aurora.db')
    original = signal(settled_by_version='1.26.6')
    store = Store(path)
    store.add_signal(original, 'original')
    store.close()
    monkeypatch.setattr('app.store.VERSION', '1.26.7')
    store = Store(path)
    store.patch(original['id'], {'telegram_result': {'status': 'enviado'}}, 'delivery', 100000)
    assert [row['id'] for row in store.pending_learning_feedback(100000)] == [original['id']]
    saved = json.loads(store.db.execute('SELECT payload FROM signal_origins WHERE id=?', (original['id'],)).fetchone()[0])
    assert saved == original
    store.close()


def minimal_project(tmp_path):
    project = tmp_path / 'project'
    for name in ('app/main.py', 'requirements.txt', '.env.example', 'start.bat',
                 'scripts/start.py', 'assets/DejaVuSans.ttf'):
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
    (project / 'app/version.py').write_text("VERSION = '1.26.7'\n")
    (project / 'frontend/assets').mkdir(parents=True)
    (project / 'frontend/index.html').write_text('<script src="/static/assets/current.js"></script>')
    (project / 'frontend/assets/current.js').write_text('import "./required.js";')
    (project / 'frontend/assets/required.js').write_text('export const fixture = 1;')
    return project


def test_release_omits_stale_build_and_private_files_without_deleting_source(tmp_path):
    from scripts.build_release import build
    project = minimal_project(tmp_path)
    excluded = ['frontend/assets/unused.js', '.env', '.env.production', 'data/aurora.db',
                'logs/aurora.log', 'private.log.1', 'custom.sqlite-wal', 'custom.sqlite3-shm',
                'frontend-src/node_modules/cached.js', '__pycache__/cached.pyc', 'draft.tmp',
                'validation/1.24.7/obsolete.txt']
    for name in excluded:
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('PRIVATE_FIXTURE')
    output = tmp_path / 'release.zip'
    build(project, output)
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert 'aurora/frontend/assets/required.js' in names
        assert all('aurora/' + name not in names for name in excluded)
        assert all(b'PRIVATE_FIXTURE' not in archive.read(name) for name in names)
    assert all((project / name).read_text() == 'PRIVATE_FIXTURE' for name in excluded)


def test_missing_referenced_chunk_preserves_previous_release_and_installation(tmp_path):
    from scripts.build_release import build
    project = minimal_project(tmp_path)
    (project / 'frontend/assets/required.js').unlink()
    output = tmp_path / 'release.zip'
    output.write_bytes(b'PREVIOUS_RELEASE')
    with pytest.raises(ValueError, match='resource missing'):
        build(project, output)
    assert output.read_bytes() == b'PREVIOUS_RELEASE'
    (project / 'frontend/assets/required.js').write_text('valid')
    existing = tmp_path / 'installation'
    existing.mkdir()
    (existing / 'history.txt').write_text('preserve')
    with pytest.raises(ValueError, match='new extraction directory'):
        build(project, output, extract=existing)
    assert (existing / 'history.txt').read_text() == 'preserve'


@pytest.mark.parametrize('release', ['1.26.5', '1.26.6'])
def test_previous_queue_metadata_recovers_missing_job_without_rewriting_result(tmp_path, release):
    path = str(tmp_path / 'aurora.db')
    original = signal(settled_by_version=release)
    store = Store(path)
    store.add_signal(original, 'original')
    with store.db:
        store.db.execute('DELETE FROM feedback_jobs')
        store.db.execute("UPDATE feedback_queue_meta SET version='1.26.6-1'")
    store.close()
    store = Store(path)
    assert store.pending_learning_feedback(100000) == [original]
    assert not store.pending_learning_feedback(original['expiry_at'] + 59)
    assert store.signal(original['id']) == original
    store.close()


def test_reconciliation_preserves_retry_and_does_not_rescan_on_same_policy(tmp_path):
    store = Store(str(tmp_path / 'aurora.db'))
    original = signal(settled_by_version='1.26.5')
    store.add_signal(original, 'original')
    store.defer_learning_feedback(original['id'], 200000)
    with store.db:
        store.db.execute("UPDATE feedback_queue_meta SET version='1.26.6-1'")
    versions = store.feedback_queue.versions
    FeedbackQueue(store.db, versions)
    assert not store.pending_learning_feedback(199999)
    assert store.pending_learning_feedback(200000) == [original]
    statements = []
    store.db.set_trace_callback(statements.append)
    FeedbackQueue(store.db, versions)
    store.db.set_trace_callback(None)
    assert not any('SELECT s.payload' in sql for sql in statements)
    store.close()

from pathlib import Path
from scripts.verify_frontend_build import fingerprint, verify


def test_source_fingerprint_and_compiled_index(tmp_path):
    src=tmp_path/'src'
    src.mkdir()
    (src/'main.tsx').write_text('hello')
    bundle=tmp_path/'dist'
    bundle.mkdir()
    (bundle/'aurora-source.sha256').write_text(fingerprint(src)+'\n')
    (bundle/'index.html').write_text('<script src="/static/assets/main-aa12.js"></script>')
    (bundle/'assets').mkdir()
    (bundle/'assets/main-aa12.js').write_text('export {};')
    assert verify(src,bundle)==[]
    (src/'main.tsx').write_text('changed')
    assert any('desatualizado' in e for e in verify(src,bundle))


def test_bundle_rejects_source_reference(tmp_path):
    src=tmp_path/'src'
    src.mkdir()
    (src/'main.tsx').write_text('hello')
    dist=tmp_path/'dist'
    dist.mkdir()
    (dist/'index.html').write_text('<script src="/src/main.tsx"></script>')
    assert len(verify(src,dist))==2


def test_excludes_dependencies_from_digest(tmp_path):
    src=tmp_path/'src'
    src.mkdir()
    (src/'main.tsx').write_text('hello')
    original=fingerprint(src)
    (src/'node_modules').mkdir()
    (src/'node_modules'/'other.txt').write_text('ignored')
    assert fingerprint(src)==original


def test_fingerprint_cannot_hide_missing_compiled_assets(tmp_path):
    src=tmp_path/'src';src.mkdir()
    (src/'main.tsx').write_text('hello')
    bundle=tmp_path/'bundle';bundle.mkdir()
    (bundle/'aurora-source.sha256').write_text(fingerprint(src)+'\n')
    (bundle/'index.html').write_text('<script src="/static/assets/missing.js"></script>')
    assert any('Recurso compilado ausente' in issue for issue in verify(src,bundle))


def test_public_login_asset_is_required_even_without_index_reference(tmp_path):
    src=tmp_path/'src';(src/'public').mkdir(parents=True)
    (src/'public/login.css').write_text('body{}')
    bundle=tmp_path/'bundle';(bundle/'assets').mkdir(parents=True)
    (bundle/'assets/main.js').write_text('export {};')
    (bundle/'index.html').write_text('<script src="/static/assets/main.js"></script>')
    (bundle/'aurora-source.sha256').write_text(fingerprint(src)+'\n')
    assert any('login.css' in issue for issue in verify(src,bundle))
    (bundle/'login.css').write_text('body{}')
    assert verify(src,bundle)==[]

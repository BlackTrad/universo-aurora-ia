"""Build a standalone distribution; never alter user runtime data."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tempfile
import zipfile
import sys

# Running this file directly sets sys.path to scripts/, not the project root.
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.start import ensure_frontend

EXCLUDED_DIRS = {'.git', '.venv', 'node_modules', '__pycache__', '.pytest_cache',
                 'logs', 'data', 'htmlcov', 'dist', 'artifacts', 'backups', 'historico',
                 'docs', 'documentation', 'validation'}
EXCLUDED_DOCUMENT_SUFFIXES = {'.md', '.mdx', '.markdown', '.rst'}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def allowed(path, root, version=None):
    relative = path.relative_to(root)
    if path.suffix.lower() in EXCLUDED_DOCUMENT_SUFFIXES:
        return False
    if any(part in EXCLUDED_DIRS or part.startswith('.venv-') for part in relative.parts):
        return False
    name = path.name
    if name.startswith('.env') and name != '.env.example':
        return False
    if name == '.coverage' or name.startswith('.aurora-config-'):
        return False
    if path.suffix.lower() in {'.pyc', '.pyo', '.zip', '.tmp', '.log'}:
        return False
    if re.search(r'\.(?:db|sqlite|sqlite3|log)(?:[().-].*)?$', name, re.IGNORECASE):
        return False
    return True


class EntryAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('src', 'href') and value and value.startswith('/static/'):
                self.paths.append(value)


def frontend_files(root):
    frontend = (root / 'frontend').resolve()
    index = frontend / 'index.html'
    parser = EntryAssets()
    parser.feed(index.read_text(encoding='utf-8'))
    pending = [(value, frontend) for value in parser.paths]
    keep = {index}
    source_marker=frontend/'aurora-source.sha256'
    if source_marker.is_file():keep.add(source_marker)
    # Login HTML is generated on the server, so its stylesheet has no link in
    # the React index. Keep this runtime dependency in every public package.
    login_css=frontend/'login.css'
    if login_css.is_file():keep.add(login_css)
    elif (root/'app/public_auth.py').is_file():
        raise ValueError('Frontend resource missing: login.css')
    reference = re.compile(r'''["']((?:\.{1,2}/|/static/)[^"'\s?#]+\.(?:js|css|png|jpe?g|svg|ico|woff2?|ttf))["']''')
    while pending:
        value, parent = pending.pop()
        path = (frontend / value.removeprefix('/static/') if value.startswith('/static/') else parent / value).resolve()
        if not path.is_relative_to(frontend) or not path.is_file():
            raise ValueError('Frontend resource missing or outside frontend: ' + value)
        if path in keep:
            continue
        keep.add(path)
        if path.suffix in ('.js', '.css'):
            pending.extend((match, path.parent) for match in reference.findall(path.read_text(encoding='utf-8')))
    if not any(path.suffix == '.js' for path in keep):
        raise ValueError('Compiled frontend JavaScript is required')
    return keep


def build(project, output, baseline=None, extract=None, archive_root='aurora'):
    root = Path(project).resolve()
    output = Path(output).resolve()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',archive_root):
        raise ValueError('Invalid archive root')
    if extract is not None and Path(extract).exists():
        raise ValueError('Choose a new extraction directory; existing installations are preserved')
    if not ensure_frontend(root):
        raise ValueError('Frontend bundle is stale; install Node.js and rebuild before creating a release.')
    keep = frontend_files(root)
    version_file = root / 'app/version.py'
    if not version_file.is_file():
        raise ValueError('Distribution file missing: app/version.py')
    version = re.search(r"VERSION\s*=\s*['\"]([^'\"]+)", version_file.read_text()).group(1)
    files = {path.relative_to(root).as_posix(): path for path in sorted(root.rglob('*'))
             if path.is_file() and allowed(path, root, version)
             and ('frontend' not in path.relative_to(root).parts[:1] or path.resolve() in keep)}
    for required in ('app/main.py', 'app/version.py', 'requirements.txt', '.env.example',
                     'start.bat', 'scripts/start.py', 'frontend/index.html'):
        if required not in files:
            raise ValueError('Distribution file missing: ' + required)
    hashes = {name: sha256(path.read_bytes()) for name, path in files.items()}
    comparison = {}
    if baseline is not None:
        with zipfile.ZipFile(baseline) as previous:
            names=[name for name in previous.namelist() if not name.endswith('/')]
            roots={name.split('/')[0] for name in names}
            prefix=next(iter(roots))+'/' if len(roots)==1 and all('/' in name for name in names) else ''
            original = {name.removeprefix(prefix): sha256(previous.read(name)) for name in names}
        comparison.update(reference_archive_sha256=sha256(Path(baseline).read_bytes()),
                        changed=sorted(name for name in hashes.keys() & original.keys() if hashes[name] != original[name]),
                        added=sorted(hashes.keys() - original.keys()), removed_from_main=sorted(original.keys() - hashes.keys()))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='aurora-release-', suffix='.tmp', dir=output.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, path in sorted(files.items()):
                archive.write(path, archive_root+'/' + name)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError('Archive CRC failure')
            for name, digest in hashes.items():
                if sha256(archive.read(archive_root+'/' + name)) != digest:
                    raise ValueError('Archive integrity failure: ' + name)
            if extract is not None:
                archive.extractall(extract)
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {'archive': output.name, 'version': version, 'size_bytes': output.stat().st_size,
            'sha256': sha256(output.read_bytes()), 'entries': len(files), 'integrity': 'passed',
            'comparison': comparison}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--extract', type=Path)
    parser.add_argument('--archive-root',default='aurora')
    args = parser.parse_args()
    print(json.dumps(build(args.project, args.out, args.baseline, args.extract,args.archive_root), indent=2))


if __name__ == '__main__':
    main()

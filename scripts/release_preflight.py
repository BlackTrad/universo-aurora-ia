"""Offline checks for distributable AURORA archives and Railway deployment settings.

This is a packaging and configuration check, NOT a replacement for npm/Docker
builds, integration tests, broker-contract verification, or a production deploy.

Usage:
    python scripts/release_preflight.py --archive path/to/release.zip
    python scripts/release_preflight.py --source .
    python scripts/release_preflight.py --source . --public-env
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import sys
import zipfile

REQUIRED = frozenset({
    # New Railway services use platform settings (or IaC), not railway.json.
    'Dockerfile', 'requirements.txt', '.env.example',
    'app/main.py', 'app/public_auth.py', 'app/container_start.py',
    'app/container_permissions.py', 'backend/ai/pipeline.py',
    'frontend-src/package.json', 'frontend-src/package-lock.json',
    'frontend-src/src/main.tsx', 'frontend-src/public/login.css',
    'scripts/smoke_public.py', 'tests/test_public_deploy_smoke.py',
    '.github/workflows/tests.yml',
})
FORBIDDEN_COMPONENTS = frozenset({
    '.git', '.env', 'node_modules', '.venv', '.pytest_cache', '__pycache__',
    'logs', 'data', 'backups', 'dist', '.next', 'coverage', '.ssh', '.aws',
})
FORBIDDEN_SUFFIXES = ('.db', '.db-shm', '.db-wal', '.db-journal', '.sqlite', '.sqlite3', '.log', '.pem', '.key', '.p12', '.pfx')


def _normalize(name: str) -> str:
    """Convert a ZIP entry to a project-relative POSIX path, without trust in ZIP paths."""
    p = PurePosixPath(name)
    if name.startswith('/') or '\\' in name or '..' in p.parts or not p.parts:
        raise ValueError(f'Caminho inseguro no pacote: {name}')
    return str(p)


def _issues_for_paths(names: set[str]) -> list[str]:
    errors = []
    for filename in sorted(names):
        parts = PurePosixPath(filename).parts
        if any(part in FORBIDDEN_COMPONENTS or part.startswith('.venv-') or
               (part.startswith('.env.') and part != '.env.example') or
               part in ('.npmrc', '.netrc', '.pypirc', 'id_rsa') for part in parts):
            errors.append(f'Arquivo privado/temporário: {filename}')
        elif filename.lower().endswith(FORBIDDEN_SUFFIXES):
            errors.append(f'Arquivo confidencial/inadequado: {filename}')
    missing = REQUIRED - names
    if missing:
        errors.append('Arquivos obrigatórios ausentes: ' + ', '.join(sorted(missing)))
    return errors


def _check_railway(data: bytes) -> list[str]:
    try:
        parsed = json.loads(data)
    except (ValueError, UnicodeError):
        return ['railway.json não contém JSON válido.']
    if not isinstance(parsed, dict):
        return ['railway.json precisa ser objeto JSON.']
    build, deploy = parsed.get('build', {}), parsed.get('deploy', {})
    if not isinstance(build, dict) or not isinstance(deploy, dict):
        return ['Configuração Railway inválida.']
    messages=[]
    if build.get('builder') != 'DOCKERFILE' or build.get('dockerfilePath') != 'Dockerfile':
        messages.append('Railway precisa construir usando Dockerfile da raiz.')
    if deploy.get('healthcheckPath') != '/healthz':
        messages.append('Configure o healthcheck público /healthz na Railway.')
    return messages


def inspect_archive(archive: Path) -> list[str]:
    errors = []
    with zipfile.ZipFile(archive) as z:
        files = [entry for entry in z.infolist() if not entry.is_dir()]
        if not files:
            return ['ZIP vazio.']
        normalized = []
        for entry in files:
            try:
                normalized.append(_normalize(entry.filename))
            except ValueError as e:
                errors.append(str(e))
            # UNIX ZIP symlinks are excluded from safe source-only packages.
            if ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                errors.append(f'Link simbólico proibido: {entry.filename}')
        if errors:
            return errors
        roots = {PurePosixPath(name).parts[0] for name in normalized}
        if len(roots) == 1 and all(len(PurePosixPath(name).parts)>1 for name in normalized):
            root = next(iter(roots))
            normalized = [name[len(root)+1:] for name in normalized]
            prefix = root + '/'
        else:
            prefix = ''
        names = set(normalized)
        if len(names) != len(normalized):
            errors.append('Arquivos duplicados no ZIP.')
        errors += _issues_for_paths(names)
        railway = prefix + 'railway.json'
        if railway in z.namelist():
            errors += _check_railway(z.read(railway))
    return errors


def inspect_source(source: Path) -> list[str]:
    names = set()
    for path in source.rglob('*'):
        if path.is_symlink():
            # Do not traverse symlinks outside project when building a release.
            continue
        if path.is_file():
            rel = path.relative_to(source)
            if any(s in rel.parts for s in {'.git', '.venv', 'node_modules', '__pycache__', '.pytest_cache', 'logs', 'data', 'backups'}):
                continue  # Local dependencies are not part of a distributable ZIP.
            if rel.name == '.env' or (rel.name.startswith('.env.') and rel.name != '.env.example'):
                continue  # Local private configuration stays on disk; archive validation rejects it.
            names.add(rel.as_posix())
    errors = _issues_for_paths(names)
    if (source/'railway.json').exists():
        errors += _check_railway((source/'railway.json').read_bytes())
    return errors


def inspect_public_environment() -> list[str]:
    """Validate that public mode is intentionally configured, without echoing secrets."""
    from app.public_auth import PublicAuth
    from app.config import Config
    result=[]
    try:
        auth=PublicAuth.from_env()
        if not auth.enabled:
            result.append('AURORA_PUBLIC_MODE não está ativado.')
    except (ValueError, RuntimeError) as exc:
        result.append('Configuração pública inválida: ' + str(exc))
    try:
        config=Config.load().validate()
        if config.provider != 'deriv' or config.asset_scope != 'crypto_binary':
            result.append('Fonte ou mercado incompatível com opções binárias cripto.')
        # SQLite must reside on a persistent Railway volume, never container ephemeral FS.
        if not Path(config.database).is_absolute() or not Path(config.database).is_relative_to('/app/data'):
            result.append('DATABASE_PATH precisa apontar para o volume persistente /app/data/.')
    except (ValueError, RuntimeError) as exc:
        result.append('Configuração operacional inválida: ' + str(exc))
    return result


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    location=parser.add_mutually_exclusive_group(required=True)
    location.add_argument('--archive', type=Path)
    location.add_argument('--source', type=Path)
    parser.add_argument('--public-env', action='store_true', help='Validar variáveis para Railway sem revelar segredos')
    args=parser.parse_args(argv)
    try:
        errors=inspect_archive(args.archive) if args.archive else inspect_source(args.source)
        if args.public_env:
            errors += inspect_public_environment()
    except (FileNotFoundError, zipfile.BadZipFile, PermissionError, OSError) as exc:
        errors=[f'Não foi possível verificar o pacote: {type(exc).__name__}']
    for problem in errors:
        print('ERRO:', problem)
    if errors:
        print(f'Pré-verificação falhou: {len(errors)} problema(s).')
        return 1
    print('Pré-verificação de distribuição aprovada. Build npm/Docker e corretora ainda requerem validação.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""Initialize a Railway-mounted SQLite volume, then drop startup privileges.

A Railway volume is mounted as root at runtime, after image build. Ownership
set in Dockerfile is therefore not enough. This startup function runs once,
when the container begins as root, and immediately drops to the image's
unprivileged service account. No network or application work runs as root.
"""
from __future__ import annotations

import os
from pathlib import Path


SERVICE_UID = 10001
SERVICE_GID = 10001
DATA_DIR = Path('/app/data')


def prepare_volume_and_drop(*, data_dir: Path = DATA_DIR) -> bool:
    """Return True if privileges were dropped; never silently skip an unsafe mount.

    Only a single application-owned data directory is touched. Symlinks are
    rejected to avoid changing ownership of paths outside that directory.
    """
    if os.name != 'posix' or not hasattr(os, 'geteuid') or os.geteuid() != 0:
        return False
    data_dir = Path(data_dir)
    if data_dir.is_symlink():
        raise RuntimeError('Diretório de dados não pode ser um link simbólico.')
    data_dir.mkdir(parents=True, exist_ok=True)
    if not data_dir.is_dir():
        raise RuntimeError('Diretório de dados inválido.')
    for entry in data_dir.iterdir():
        if entry.is_symlink():
            raise RuntimeError('Links simbólicos não são permitidos no volume de dados.')
        # The operational SQLite files and lock are at the volume root.
        # Leave nested directories unchanged; do not recursively chown contents.
        if entry.is_file():
            os.chown(entry, SERVICE_UID, SERVICE_GID)
    os.chown(data_dir, SERVICE_UID, SERVICE_GID)
    os.setgroups([])
    os.setgid(SERVICE_GID)
    os.setuid(SERVICE_UID)
    if os.geteuid() != SERVICE_UID:
        raise RuntimeError('Não foi possível reduzir os privilégios do processo.')
    return True

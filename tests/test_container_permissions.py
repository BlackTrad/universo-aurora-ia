"""Permissions and Railway volume regression tests; never require real root."""
import os
from pathlib import Path
from unittest.mock import patch, call

import pytest

from app.container_permissions import prepare_volume_and_drop, SERVICE_UID, SERVICE_GID


def test_unprivileged_process_does_not_mutate_files(tmp_path):
    (tmp_path / 'aurora.db').write_text('test')
    with patch('app.container_permissions.os.geteuid', return_value=10001), \
         patch('app.container_permissions.os.chown') as chown:
        assert prepare_volume_and_drop(data_dir=tmp_path) is False
        chown.assert_not_called()


def test_root_mounted_volume_is_redistributed_to_service_user(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    db=data/'aurora.db'; db.write_bytes(b'old')
    lock=data/'aurora.db.lock'; lock.touch()
    # Simulate root followed by unprivileged checks without actually changing UID.
    with patch('app.container_permissions.os.geteuid',side_effect=[0,SERVICE_UID]), \
         patch('app.container_permissions.os.chown') as chown, \
         patch('app.container_permissions.os.setgroups') as groups, \
         patch('app.container_permissions.os.setgid') as gid, \
         patch('app.container_permissions.os.setuid') as uid:
        assert prepare_volume_and_drop(data_dir=data) is True
    chown.assert_any_call(data,SERVICE_UID,SERVICE_GID)
    chown.assert_any_call(db,SERVICE_UID,SERVICE_GID)
    chown.assert_any_call(lock,SERVICE_UID,SERVICE_GID)
    groups.assert_called_once_with([])
    gid.assert_called_once_with(SERVICE_GID)
    uid.assert_called_once_with(SERVICE_UID)


def test_root_mount_rejects_symlink(tmp_path):
    directory=tmp_path/'data';directory.mkdir()
    (directory/'escape').symlink_to(tmp_path)
    with patch('app.container_permissions.os.geteuid', return_value=0), \
         patch('app.container_permissions.os.chown') as chown, \
         patch('app.container_permissions.os.setuid') as uid:
        with pytest.raises(RuntimeError,match='Links simbólicos'):
            prepare_volume_and_drop(data_dir=directory)
    chown.assert_not_called()
    uid.assert_not_called()


def test_volume_mount_root_symlink_rejected(tmp_path):
    mount=tmp_path/'data';mount.symlink_to(tmp_path)
    with patch('app.container_permissions.os.geteuid', return_value=0):
        with pytest.raises(RuntimeError,match='link simbólico'):
            prepare_volume_and_drop(data_dir=mount)

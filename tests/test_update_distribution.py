"""Temporary archive fixtures for the updater's distribution, not a new release."""
import importlib.util
import stat
import zipfile
from pathlib import Path

import pytest


def module(name):
    path = Path(__file__).resolve().parents[1]/'scripts'/f'{name}.py'
    spec = importlib.util.spec_from_file_location('distribution_'+name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_update_wrapper_is_shipped_executable_without_runtime_backups(tmp_path, monkeypatch):
    builder = module('package')
    source = tmp_path/'source'
    (source/'frontend/dist').mkdir(parents=True)
    (source/'frontend/dist/index.html').write_text('<p>synthetic updater packaging fixture</p>')
    wrapper = source/'update.sh'
    wrapper.write_text('#!/bin/sh\nexit 0\n')
    wrapper.chmod(0o755)
    (source/'.acm-update/backups').mkdir(parents=True)
    (source/'.acm-update/backups/private.txt').write_text('synthetic private backup sentinel')
    monkeypatch.setattr(builder,'ROOT',source)
    builder.package(tmp_path/'out',skip_build=True)
    artifact = next((tmp_path/'out').glob('*.zip'))
    with zipfile.ZipFile(artifact) as archive:
        item = next(info for info in archive.infolist() if info.filename.endswith('/update.sh'))
        assert (item.external_attr >> 16) & stat.S_IXUSR
        assert not any('.acm-update' in name for name in archive.namelist())
    module('verify_release').verify(artifact)


def test_archive_verifier_rejects_updater_runtime_directory(tmp_path):
    artifact = tmp_path/'synthetic.zip'
    with zipfile.ZipFile(artifact,'w') as archive:
        archive.writestr('fixture/.acm-update/backups/state.json','{}')
    with pytest.raises(RuntimeError,match='私人或运行时'):
        module('verify_release').verify(artifact)

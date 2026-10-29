#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2018 The ungoogled-chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
ungoogled-chromium packaging script for Microsoft Windows

Portable layout:
    chromePortable/chrome.exe
    chromePortable/<Chromium version>/*
    chromePortable/UserData/
"""

import sys
if sys.version_info.major < 3:
    raise RuntimeError('Python 3 is required for this script.')

import argparse
import os
import platform
from pathlib import Path
import shutil
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent / 'ungoogled-chromium' / 'utils'))
import filescfg
from _common import ENCODING, get_chromium_version
sys.path.pop(0)


def _get_release_revision():
    revision_path = Path(__file__).resolve().parent / 'ungoogled-chromium' / 'revision.txt'
    return revision_path.read_text(encoding=ENCODING).strip()


def _get_packaging_revision():
    revision_path = Path(__file__).resolve().parent / 'revision.txt'
    return revision_path.read_text(encoding=ENCODING).strip()


_cached_target_cpu = None


def _get_target_cpu(build_outputs):
    global _cached_target_cpu
    if not _cached_target_cpu:
        with open(build_outputs / 'args.gn', 'r', encoding=ENCODING) as f:
            args_gn_text = f.read()
            for cpu in ('x64', 'x86', 'arm64'):
                if f'target_cpu="{cpu}"' in args_gn_text:
                    _cached_target_cpu = cpu
                    break
    assert _cached_target_cpu
    return _cached_target_cpu


def _archive_datetime(timestamp):
    """Convert LASTCHANGE's Unix timestamp to a valid ZIP timestamp."""
    if timestamp is None:
        return None
    # ZIP timestamps cover 1980-01-01 through 2107-12-31.
    timestamp = max(315532800, min(int(timestamp), 4354819198))
    return time.gmtime(timestamp)[:6]


def _write_file(archive, source, archive_name, fixed_datetime):
    """Write one file, optionally using the source revision's fixed timestamp."""
    archive_name = archive_name.as_posix()
    if fixed_datetime is None:
        archive.write(source, archive_name)
        return

    info = zipfile.ZipInfo(archive_name, date_time=fixed_datetime)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.file_size = source.stat().st_size
    # Mark files as regular files with conventional read permissions.
    info.external_attr = (0o100644 & 0xFFFF) << 16
    with source.open('rb') as source_file, archive.open(info, 'w', force_zip64=True) as target:
        shutil.copyfileobj(source_file, target, length=1024 * 1024)


def _create_portable_archive(file_paths, build_outputs, output_path, timestamp=None):
    """Create chromePortable/ ZIP with chrome.exe at the root and versioned files."""
    relative_paths = [Path(path) for path in file_paths]
    chrome_exe = Path('chrome.exe')
    if chrome_exe not in relative_paths:
        raise FileNotFoundError(
            'chrome.exe was not included by chrome/tools/build/win/FILES.cfg')

    archive_root = Path('chromePortable')
    version_root = archive_root / get_chromium_version()
    fixed_datetime = _archive_datetime(timestamp)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9, allowZip64=True) as archive:
        # Explicit directory entries make the intended structure visible when
        # opening the ZIP, even before the browser has been run once.
        for directory in (archive_root, version_root, archive_root / 'UserData'):
            directory_name = directory.as_posix().rstrip('/') + '/'
            if fixed_datetime is None:
                archive.writestr(directory_name, b'')
            else:
                info = zipfile.ZipInfo(directory_name, date_time=fixed_datetime)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o40755 & 0xFFFF) << 16
                archive.writestr(info, b'')

        for relative_path in relative_paths:
            source = build_outputs / relative_path
            if not source.is_file():
                raise FileNotFoundError(f'Packaging input does not exist: {source}')

            if relative_path == chrome_exe:
                archive_path = archive_root / chrome_exe
            else:
                archive_path = version_root / relative_path

            _write_file(archive, source, archive_path, fixed_datetime)


def main():
    """Entrypoint"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--cpu-arch',
        metavar='ARCH',
        default=platform.architecture()[0],
        choices=('64bit', '32bit'),
        help=('Filter build outputs by a target CPU. '
              'This is the same as the "arch" key in FILES.cfg. '
              'Default (from platform.architecture()): %(default)s'))
    args = parser.parse_args()

    build_outputs = Path('build/src/out/Default')
    shutil.copyfile('build/src/out/Default/mini_installer.exe',
        'build/ungoogled-chromium_{}-{}.{}_installer_{}.exe'.format(
            get_chromium_version(), _get_release_revision(),
            _get_packaging_revision(), _get_target_cpu(build_outputs)))

    timestamp = None
    try:
        with open('build/src/build/util/LASTCHANGE.committime', 'r', encoding=ENCODING) as ct:
            timestamp = int(ct.read())
    except FileNotFoundError:
        pass

    output = Path('build/ungoogled-chromium_{}-{}.{}_windows_{}.zip'.format(
        get_chromium_version(), _get_release_revision(),
        _get_packaging_revision(), _get_target_cpu(build_outputs)))
    excluded_files = set([
        Path('mini_installer.exe'),
        Path('mini_installer_exe_version.rc'),
        Path('setup.exe'),
        Path('chrome.packed.7z'),
    ])
    files_generator = filescfg.filescfg_generator(
        Path('build/src/chrome/tools/build/win/FILES.cfg'),
        build_outputs, args.cpu_arch, excluded_files)
    _create_portable_archive(
        files_generator, build_outputs, output, timestamp)


if __name__ == '__main__':
    main()

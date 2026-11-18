#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Raúl Milla (DominicZe)
"""Build dist/pb_conversational_<version>.run: a single self-extracting installer file.

    python3 installer/build_package.py

Usage on the LinuxCNC machine:
    bash pb_conversational_<version>.run                    install (prompts if multiple configs exist)
    bash pb_conversational_<version>.run install  INI_PATH
    bash pb_conversational_<version>.run uninstall [INI_PATH]
    bash pb_conversational_<version>.run status    [INI_PATH]
    bash pb_conversational_<version>.run --extract [FOLDER] extract files only
"""
import base64
import io
import os
import re
import tarfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = re.search(
    r"^VERSION = '([^']+)'",
    open(os.path.join(REPO, 'installer', 'pb_conversational_installer.py'), encoding='utf-8').read(),
    re.M,
).group(1)
FILES = ['README.md', 'LICENSE', 'installer/pb_conversational_installer.py']
DIRS = ['pb_conversational']

HEADER = r'''#!/bin/bash
# PB_Conversational (DominicZe) for Probe Basic - version @VERSION@
#   bash @NAME@                              install (prompts if multiple configs exist)
#   bash @NAME@ install  INI_PATH            install into that configuration
#   bash @NAME@ install --set-aside-templates  move empty example USER tabs aside
#   bash @NAME@ uninstall [INI_PATH]         remove and restore configuration
#   bash @NAME@ status    [INI_PATH]         show installation status
#   bash @NAME@ --extract [FOLDER]           extract files only
set -e
command -v python3 >/dev/null || { echo "ERROR: python3 is required (included with Probe Basic)"; exit 1; }
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
sed -n '/^__PACKAGE__$/,$p' "$0" | tail -n +2 | base64 -d | tar -xz -C "$TMP"
if [ "$1" = "--extract" ] || [ "$1" = "--extraer" ]; then
    DEST="${2:-pb_conversational_@VERSION@}"
    mkdir -p "$DEST" && cp -r "$TMP"/. "$DEST"/
    echo "Files extracted to $DEST"
    exit 0
fi
CMD="${1:-install}"
[ $# -gt 0 ] && shift
python3 "$TMP/installer/pb_conversational_installer.py" "$CMD" "$@"
exit $?
__PACKAGE__
'''


def main():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        def skip(ti):
            name = os.path.basename(ti.name)
            if name == '__pycache__' or name.endswith('.pyc') or name.startswith(('last_', 'ultimos_')):
                return None
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = 'root'
            return ti

        for f in FILES + DIRS:
            tar.add(os.path.join(REPO, f), arcname=f, filter=skip)

    name = 'pb_conversational_{}.run'.format(VERSION)
    os.makedirs(os.path.join(REPO, 'dist'), exist_ok=True)
    out = os.path.join(REPO, 'dist', name)
    payload = base64.encodebytes(buf.getvalue()).decode()
    with open(out, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(HEADER.replace('@VERSION@', VERSION).replace('@NAME@', name) + payload)
    os.chmod(out, 0o755)
    print('ok', out, os.path.getsize(out) // 1024, 'KB')


if __name__ == '__main__':
    main()

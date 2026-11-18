#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Raúl Milla (DominicZe)
"""Install and uninstall PB_Conversational screens in a Probe Basic configuration.

    python3 pb_conversational_installer.py install   [INI_PATH] [--set-aside-templates]
    python3 pb_conversational_installer.py uninstall [INI_PATH]
    python3 pb_conversational_installer.py status    [INI_PATH]

--set-aside-templates moves template_main / template_sidebar (empty example USER tabs) aside
even if USER_TABS_PATH was already active; uninstalling restores them.

Without INI_PATH, searches ~/linuxcnc/configs for configurations with DISPLAY = probe_basic...
and prompts if multiple are found.

Only uses Python 3 standard library modules (included with any Probe Basic installation).
"""
from __future__ import print_function

import datetime
import glob
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys

VERSION = '1.5.0'
TAB = 'pb_conversational'
TABS = (TAB,)
STATE = '.pb_conversational'
MARK = '# pb_conversational: enables user tabs (removed by uninstaller)'
HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = ('template_main', 'template_sidebar')
TEMPLATES_FLAG = '--set-aside-templates'
KEEP = (
    'last_int.json',
    'last_ext.json',
    'last_rect.json',
    'last_circ.json',
    'last_island_rect.json',
    'last_island_circ.json',
    'ultimos_int.json',
    'ultimos_ext.json',
    'ultimos_rect.json',
    'ultimos_circ.json',
    'ultimos_isla_rect.json',
    'ultimos_isla_circ.json',
)


def say(msg=''):
    print(msg)


def fail(msg):
    print('\nERROR: ' + msg, file=sys.stderr)
    sys.exit(1)


def sha256(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def read_lines(path):
    with io.open(path, encoding='utf-8', errors='surrogateescape') as fh:
        return fh.read().splitlines(True)


def write_lines(path, lines):
    with io.open(path, 'w', encoding='utf-8', errors='surrogateescape', newline='') as fh:
        fh.write(''.join(lines))


def state_dir(ini):
    """Directory for installation state and .ini backup."""
    return os.path.join(os.path.dirname(ini), STATE, os.path.splitext(os.path.basename(ini))[0])


def source_dir(tab=TAB):
    """Locate the tab directory alongside the installer (package) or at repository root."""
    for cand in (os.path.join(HERE, tab), os.path.join(os.path.dirname(HERE), tab)):
        if os.path.isfile(os.path.join(cand, tab + '.py')):
            return cand
    fail('cannot find folder {} containing {}.py next to the installer.'.format(tab, tab))


SECTION = re.compile(r'^\s*\[([^\]]+)\]')
KEYVAL = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$')


def display_section(lines):
    """Return (start, end) line indices for [DISPLAY]."""
    start = None
    for i, ln in enumerate(lines):
        m = SECTION.match(ln)
        if m and start is None and m.group(1).strip().upper() == 'DISPLAY':
            start = i
        elif m and start is not None:
            return start, i
    return (start, len(lines)) if start is not None else (None, None)


def display_values(lines, key):
    """Return [(line_index, value)] for active 'key = value' entries in [DISPLAY]."""
    a, b = display_section(lines)
    out = []
    if a is None:
        return out
    for i in range(a + 1, b):
        m = KEYVAL.match(lines[i])
        if m and m.group(1).upper() == key:
            out.append((i, m.group(2)))
    return out


INLINE_COMMENT = re.compile(r'(?:^|\s+)[#;]')


def split_inline_comment(val):
    """Split 'user_tabs/ # REQUIRED...' into ('user_tabs/', '# REQUIRED...')."""
    m = INLINE_COMMENT.search(val)
    if not m:
        return val, None
    return val[:m.start()].rstrip(), val[m.start():].strip()


def is_probe_basic(ini):
    """Return True for mill Probe Basic .ini files (not probe_basic_lathe)."""
    try:
        lines = read_lines(ini)
    except (IOError, OSError):
        return False
    sections = set(m.group(1).strip().upper() for m in map(SECTION.match, lines) if m)
    names = [v.split()[0].lower() for _, v in display_values(lines, 'DISPLAY') if v.split()]
    return 'KINS' in sections and any(n.startswith('probe_basic') and 'lathe' not in n for n in names)


def find_inis():
    found = []
    for ini in sorted(glob.glob(os.path.expanduser('~/linuxcnc/configs/*/*.ini'))):
        if is_probe_basic(ini):
            found.append(ini)
    return found


def choose_ini(arg, need_installed=False):
    if arg:
        ini = os.path.abspath(os.path.expanduser(arg))
        if not os.path.isfile(ini):
            fail('file does not exist: ' + ini)
        if not is_probe_basic(ini):
            fail('{} is not a Probe Basic mill configuration (requires [KINS] and DISPLAY = probe_basic).'.format(ini))
        return ini
    inis = find_inis()
    if need_installed:
        inis = [i for i in inis if os.path.isfile(os.path.join(state_dir(i), 'install_log.json'))]
    if not inis:
        fail('no Probe Basic configuration{} found in ~/linuxcnc/configs. '
             'Pass the .ini path explicitly: {} {} /path/to/your_machine.ini'.format(
                 ' with PB_Conversational installed' if need_installed else '',
                 os.path.basename(sys.argv[0]),
                 sys.argv[1] if len(sys.argv) > 1 else 'install',
             ))
    if len(inis) == 1:
        return inis[0]
    say('Multiple Probe Basic configurations found:')
    for n, ini in enumerate(inis, 1):
        say('  {}) {}'.format(n, ini))
    if not sys.stdin.isatty():
        fail('select one by passing the .ini path.')
    while True:
        r = input('Which one? (number): ').strip()
        if r.isdigit() and 1 <= int(r) <= len(inis):
            return inis[int(r) - 1]


def probe_basic_ui(display):
    """Return path to the installed .ui file for this Probe Basic variant, or None."""
    pkg = display.split()[0]
    if not re.match(r'^[A-Za-z0-9_]+$', pkg):
        return None
    try:
        out = subprocess.check_output(
            [sys.executable, '-c', 'import os, {0}; print(os.path.dirname({0}.__file__))'.format(pkg)],
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
        ).strip()
    except Exception:
        return None
    for name in ('probe_basic.ui', pkg + '.ui'):
        p = os.path.join(out, name)
        if os.path.isfile(p):
            return p
    return None


def report_placement(lines):
    disp = display_values(lines, 'DISPLAY')
    display = disp[0][1] if disp else 'probe_basic'
    ui = probe_basic_ui(display)
    if ui is None:
        say('  - Could not locate the .ui file for {} to check tab placement.'.format(display))
        return
    text = io.open(ui, encoding='utf-8', errors='replace').read()
    if 'name="thread_mill"' in text:
        say('  - {} has CONVERSATIONAL > HOLE OPS > THREAD MILL: INT. THREAD and EXT. THREAD will appear there.'.format(display))
    else:
        say('  - {} has no THREAD MILL tab: thread milling will appear in a separate CONVERSATIONAL+ tab.'.format(display))
    if 'name="pockets_tab"' in text:
        say('  - {} has CONVERSATIONAL > POCKETS: RECT. POCKET and CIRC. POCKET will occupy its first two empty tabs.'.format(display))
    else:
        say('  - {} has no POCKETS tab: pockets will appear in CONVERSATIONAL+.'.format(display))
    if 'name="perimeter_tab"' in text:
        say('  - {} has CONVERSATIONAL > PERIMETER: RECT. CONTOUR and CIRC. CONTOUR will occupy its first two empty tabs.'.format(display))
    else:
        say('  - {} has no PERIMETER tab: contours will appear in CONVERSATIONAL+.'.format(display))
    if 'name="widget_59"' in text:
        say('  - {} has the left slot in SETTINGS: backlash compensation will appear there.'.format(display))
    else:
        say('  - {} has no SETTINGS slot: backlash compensation will appear in CONVERSATIONAL+.'.format(display))


def install(ini, set_aside_templates=False):
    cfg = os.path.dirname(ini)
    state = state_dir(ini)
    reg_path = os.path.join(state, 'install_log.json')
    src = source_dir(TAB)
    lines = read_lines(ini)
    if display_section(lines)[0] is None:
        fail('{} has no [DISPLAY] section.'.format(ini))

    if os.path.isfile(reg_path):
        with io.open(reg_path, encoding='utf-8') as fh:
            reg = json.load(fh)
        say('Already installed (version {}): updating files while preserving saved parameters.'.format(
            reg.get('version', '?')))
        tabs_dir = reg['user_tabs_dir']
        _install_component(reg, src, tabs_dir)
        if set_aside_templates:
            _set_aside_templates(reg, state, tabs_dir)
        reg['version'] = VERSION
        reg['updated'] = datetime.datetime.now().isoformat(timespec='seconds')
        shutil.copy2(os.path.abspath(__file__), os.path.join(state, 'pb_conversational_installer.py'))
        _save_reg(state, reg)
        say('\nDone. Restart Probe Basic.')
        return

    say('Configuration: ' + ini)
    reg = dict(
        version=VERSION,
        ini=ini,
        date=datetime.datetime.now().isoformat(timespec='seconds'),
        ini_backup=os.path.join(state, 'ini_original.ini'),
        actions=[],
    )
    os.makedirs(state)
    shutil.copy2(ini, reg['ini_backup'])
    say('  - Backed up .ini to ' + reg['ini_backup'])

    a, b = display_section(lines)
    for k in range(a + 1, b):
        m = KEYVAL.match(lines[k])
        if m and m.group(1).upper() != 'USER_TABS_PATH' and split_inline_comment(m.group(2))[1]:
            say('  - WARNING: {} has an inline comment; LinuxCNC treats it as part of the value '
                '(line {}: {})'.format(m.group(1), k + 1, lines[k].strip()))

    active = display_values(lines, 'USER_TABS_PATH')
    was_active = bool(active)
    if active:
        i, val = active[0]
        clean, comment = split_inline_comment(val)
        if not clean:
            orig_line = lines[i]
            eol = _eol(orig_line)
            indent = re.match(r'^\s*', orig_line).group(0)
            new_line = indent + 'USER_TABS_PATH = user_tabs/' + eol
            if comment:
                new = [indent + comment + eol, new_line]
                reg['actions'].append(dict(type='comment_split', line_before=orig_line, lines_after=new))
                lines[i:i + 1] = new
            else:
                reg['actions'].append(dict(type='trailing_slash', line_before=orig_line, line_after=new_line))
                lines[i] = new_line
            say('  - USER_TABS_PATH was empty; set to user_tabs/')
            val = 'user_tabs/'
        elif comment:
            eol = _eol(lines[i])
            indent = re.match(r'^\s*', lines[i]).group(0)
            new = [indent + comment + eol, indent + 'USER_TABS_PATH = ' + clean.rstrip('/') + '/' + eol]
            reg['actions'].append(dict(type='comment_split', line_before=lines[i], lines_after=new))
            say('  - USER_TABS_PATH had an inline comment ("{}"); moved comment to its own line.'.format(comment))
            lines[i:i + 1] = new
            val = clean.rstrip('/') + '/'
        elif not val.endswith('/'):
            # Probe Basic calls os.path.dirname() on USER_TABS_PATH: without a trailing slash it looks at teh parent dir.
            new = re.sub(re.escape(val) + r'\s*$', val + '/', lines[i].rstrip('\r\n')) + _eol(lines[i])
            reg['actions'].append(dict(type='trailing_slash', line_before=lines[i], line_after=new))
            lines[i] = new
            say('  - USER_TABS_PATH = {} was missing a trailing slash; updated to {}/'.format(val, val))
        tabs_rel = val.rstrip('/') + '/'
    else:
        tabs_rel = 'user_tabs/'
        a, b = display_section(lines)
        at = b
        for k in range(a + 1, b):
            if re.match(r'^\s*[#;]\s*USER_TABS_PATH\s*=', lines[k]):
                at = k + 1
                break
        while at > a + 1 and not lines[at - 1].strip():
            at -= 1
        eol = _eol(lines[a])
        add = [MARK + eol, 'USER_TABS_PATH = user_tabs/' + eol]
        lines[at:at] = add
        reg['actions'].append(dict(type='lines_added', lines=add))
        say('  - Enabled USER_TABS_PATH = user_tabs/ in [DISPLAY]')
    write_lines(ini, lines)
    reg['ini_sha_installed'] = sha256(ini)

    tabs_dir = os.path.normpath(os.path.join(cfg, os.path.expanduser(tabs_rel)))
    reg['user_tabs_dir'] = tabs_dir
    if not os.path.isdir(tabs_dir):
        os.makedirs(tabs_dir)
        reg['actions'].append(dict(type='dir_created', path=tabs_dir))
    elif not was_active:
        for name in sorted(os.listdir(tabs_dir)):
            p = os.path.join(tabs_dir, name)
            if os.path.isdir(p) and name not in TABS:
                dst = os.path.join(state, 'moved_tabs', name)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(p, dst)
                reg['actions'].append(dict(type='tab_moved', src=p, dst=dst))
                say('  - Moved {} aside (Probe Basic would load it as a tab)'.format(name))

    _install_component(reg, src, tabs_dir)
    if set_aside_templates:
        _set_aside_templates(reg, state, tabs_dir)
    shutil.copy2(os.path.abspath(__file__), os.path.join(state, 'pb_conversational_installer.py'))
    _save_reg(state, reg)
    report_placement(lines)
    say('\nDone. Restart Probe Basic to see the conversational tabs.')
    say('To uninstall:  python3 "{}" uninstall "{}"'.format(
        os.path.join(state, 'pb_conversational_installer.py'), ini))


def _eol(line):
    return '\r\n' if line.endswith('\r\n') else '\n'


def _install_component(reg, src, tabs_dir):
    done = set(a['path'] for a in reg['actions'] if a['type'] == 'tab_copied')
    dst = os.path.join(tabs_dir, TAB)
    _copy_tab(src, dst)
    if dst not in done:
        reg['actions'].append(dict(type='tab_copied', path=dst))
    say('  - Installed screens in ' + dst)


def _copy_tab(src, dst):
    keep = {}
    for name in KEEP:
        p = os.path.join(dst, name)
        if os.path.isfile(p):
            with open(p, 'rb') as fh:
                keep[name] = fh.read()
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', *KEEP))
    for name, data in keep.items():
        with open(os.path.join(dst, name), 'wb') as fh:
            fh.write(data)


def _set_aside_templates(reg, state, tabs_dir):
    for name in TEMPLATES:
        p = os.path.join(tabs_dir, name)
        if not os.path.isdir(p):
            continue
        dst = os.path.join(state, 'moved_tabs', name)
        if os.path.exists(dst):
            say('  - WARNING: backup copy of {} already exists in {}; skipping'.format(name, dst))
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(p, dst)
        reg['actions'].append(dict(type='tab_moved', src=p, dst=dst))
        say('  - Moved empty template {} aside to {}'.format(name, dst))


def _save_reg(state, reg):
    with io.open(os.path.join(state, 'install_log.json'), 'w', encoding='utf-8') as fh:
        json.dump(reg, fh, indent=1, ensure_ascii=False)


def uninstall(ini):
    cfg = os.path.dirname(ini)
    state = state_dir(ini)
    reg_path = os.path.join(state, 'install_log.json')
    if not os.path.isfile(reg_path):
        fail('no installation record found in ' + state)
    with io.open(reg_path, encoding='utf-8') as fh:
        reg = json.load(fh)
    say('Configuration: ' + ini)
    acts = reg['actions']

    others = []
    for p in glob.glob(os.path.join(cfg, STATE, '*', 'install_log.json')):
        if os.path.dirname(p) != state:
            with io.open(p, encoding='utf-8') as fh:
                others.append(json.load(fh).get('user_tabs_dir'))
    for a in acts:
        if a['type'] == 'tab_copied' and os.path.isdir(a['path']):
            if os.path.dirname(a['path']) in others:
                say('  - Keeping {}: still used by another .ini in this folder'.format(a['path']))
            else:
                shutil.rmtree(a['path'])
                say('  - Removed ' + a['path'])

    for a in reversed(acts):
        if a['type'] == 'tab_moved':
            if os.path.exists(a['src']):
                say('  - WARNING: {} already exists; leaving {} in {}'.format(
                    a['src'], os.path.basename(a['src']), a['dst']))
            else:
                shutil.move(a['dst'], a['src'])
                say('  - Restored ' + a['src'])

    for a in acts:
        if a['type'] == 'dir_created' and os.path.isdir(a['path']) and not os.listdir(a['path']):
            os.rmdir(a['path'])
            say('  - Removed empty folder ' + a['path'])

    if sha256(ini) == reg.get('ini_sha_installed'):
        shutil.copy2(reg['ini_backup'], ini)
        say('  - Restored .ini byte-for-byte')
    else:
        lines = read_lines(ini)
        for a in reversed(acts):
            if a['type'] == 'lines_added':
                for ln in a['lines']:
                    if ln in lines:
                        lines.remove(ln)
                    else:
                        say('  - WARNING: added line "{}" not found (edited manually?)'.format(ln.strip()))
            elif a['type'] == 'comment_split':
                n = len(a['lines_after'])
                at = next((k for k in range(len(lines) - n + 1) if lines[k:k + n] == a['lines_after']), None)
                if at is None:
                    say('  - WARNING: USER_TABS_PATH changed after install; leaving as-is')
                else:
                    lines[at:at + n] = [a['line_before']]
            elif a['type'] == 'trailing_slash':
                if a['line_after'] in lines:
                    lines[lines.index(a['line_after'])] = a['line_before']
                else:
                    say('  - WARNING: USER_TABS_PATH changed after install; leaving as-is')
        write_lines(ini, lines)
        say('  - .ini was edited after install: reverted only installer changes (original backup at {})'.format(
            reg['ini_backup']))
        keep = os.path.join(cfg, 'ini_before_pb_conversational.ini')
        shutil.copy2(reg['ini_backup'], keep)
        say('    Original .ini also saved to ' + keep)

    shutil.rmtree(state)
    if not os.listdir(os.path.join(cfg, STATE)):
        os.rmdir(os.path.join(cfg, STATE))
    say('\nDone. Restart Probe Basic. Any generated .ngc files were left untouched.')


def status(ini):
    reg_path = os.path.join(state_dir(ini), 'install_log.json')
    lines = read_lines(ini)
    say('Configuration: ' + ini)
    vals = display_values(lines, 'USER_TABS_PATH')
    say('  - USER_TABS_PATH: ' + (', '.join(v for _, v in vals) if vals else 'not active'))
    if os.path.isfile(reg_path):
        with io.open(reg_path, encoding='utf-8') as fh:
            reg = json.load(fh)
        tab = os.path.join(reg['user_tabs_dir'], TAB)
        say('  - Installed: version {} on {} at {}{}'.format(
            reg['version'], reg['date'], tab, '' if os.path.isdir(tab) else ' (FOLDER MISSING!)'))
    else:
        say('  - Not installed')
    report_placement(lines)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('install', 'uninstall', 'status'):
        say(__doc__.split('\n\n')[1])
        sys.exit(2)
    cmd = sys.argv[1]
    args = [a for a in sys.argv[2:] if a != TEMPLATES_FLAG]
    arg = args[0] if args else None
    set_aside = TEMPLATES_FLAG in sys.argv[2:]
    say('PB_Conversational (DominicZe) for Probe Basic - version {}\n'.format(VERSION))
    if cmd == 'install':
        install(choose_ini(arg), set_aside)
    elif cmd == 'uninstall':
        uninstall(choose_ini(arg, need_installed=True))
    else:
        status(choose_ini(arg))


if __name__ == '__main__':
    main()

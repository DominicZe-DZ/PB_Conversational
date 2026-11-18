# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Raúl Milla (DominicZe)
"""
Read and write BACKLASH for each [JOINT_n] section in a LinuxCNC .ini file. Pure Python, no Qt.

    leer(ini)                                -> list of Eje (one per joint)
    guardar(ini, cambios, copias, decimals)  -> path to the backup .ini created before writing

Writing rules (only the BACKLASH lines inside changed [JOINT_n] sections are touched):
  - enabled  -> 'BACKLASH = 0.035'   (replaces the existing line in place, commented or not)
  - disabled -> '#BACKLASH = 0.035'  (comments it out so your measured value isn't lost)
  - if the section has no BACKLASH line yet, appends it at the end of the section
  - if multiple active BACKLASH lines exist in one section, keeps the first and comments the rest
    (LinuxCNC only uses the first one, and duplicates confuse anyone reading the .ini)
  - the rest of the file stays byte-for-byte identical (line endings, encoding, comments).
Before writing, a timestamped backup is saved; the last 30 backups are kept.
"""

import datetime
import os
import re
import shutil
import tempfile

SECTION = re.compile(r'^\s*\[([^\]]+)\]')
KEYVAL = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$')
BACKLASH = re.compile(r'^(?P<ind>[ \t]*)(?P<hash>[#;][#; \t]*)?BACKLASH(?P<sp1>[ \t]*)=(?P<sp2>[ \t]*)'
                      r'(?P<val>[^\s#;]*)(?P<rest>.*?)(?P<eol>\r?\n|\r)?$', re.IGNORECASE)
INLINE_COMMENT = re.compile(r'[#;]')
COPIAS = 30                     # backups kept (better safe than rewriting an .ini at 11pm in the shop)
ENC = ('utf-8', 'surrogateescape')     # preserves any non-UTF-8 bytes in old .ini files


class Eje(object):
    """One joint from the .ini: [JOINT_n] and its axis letter (X, Y, Z...)."""

    def __init__(self, joint, letra):
        self.joint = joint            # 0, 1, 2...
        self.letra = letra            # 'X', 'Y', 'Z' ('Y2' if two joints drive the same axis)
        self.activo = False           # True if there is an uncommented BACKLASH line
        self.valor = 0.0              # value on that line (active or commented); 0 if missing
        self.hay_linea = False
        self.stepgen_maxaccel = None  # used for the 2x acceleration headroom warning
        self.max_accel = None

    def __repr__(self):
        return 'Eje(%d %s active=%s value=%.4f)' % (self.joint, self.letra, self.activo, self.valor)


def _leer_lineas(ini):
    with open(ini, 'rb') as fh:
        return fh.read().decode(*ENC).splitlines(True)


def _secciones(lineas):
    """{UPPERCASE_SECTION: (start_line, end_line_exclusive)}"""
    out, actual, inicio = {}, None, 0
    for i, ln in enumerate(lineas):
        m = SECTION.match(ln)
        if m:
            if actual is not None and actual not in out:
                out[actual] = (inicio, i)
            actual, inicio = m.group(1).strip().upper(), i
    if actual is not None and actual not in out:
        out[actual] = (inicio, len(lineas))
    return out


def _valor(lineas, secs, sec, clave):
    if sec not in secs:
        return None
    a, b = secs[sec]
    for ln in lineas[a + 1:b]:
        m = KEYVAL.match(ln)
        if m and m.group(1).upper() == clave:
            return INLINE_COMMENT.split(m.group(2), 1)[0].strip()
    return None


def _num(txt):
    try:
        return float(str(txt).replace(',', '.'))
    except (TypeError, ValueError):
        return None


def letras(lineas, secs, n):
    """Axis letter for each joint: trivkins coordinates=XYZ (or [TRAJ] COORDINATES); fallback XYZABCUVW."""
    coords = None
    kin = _valor(lineas, secs, 'KINS', 'KINEMATICS') or ''
    m = re.search(r'coordinates\s*=\s*([A-Za-z]+)', kin)
    if m:
        coords = m.group(1).upper()
    if not coords:
        c = _valor(lineas, secs, 'TRAJ', 'COORDINATES')
        if c:
            coords = re.sub(r'[^A-Za-z]', '', c).upper()
    if not coords or len(coords) < n:
        coords = (coords or '') + 'XYZABCUVW'[len(coords or ''):]
    out = []
    for i in range(n):
        letra = coords[i]
        repetidas = coords[:n].count(letra)
        out.append(letra + (str(coords[:i + 1].count(letra)) if repetidas > 1 else ''))
    return out


def leer(ini):
    lineas = _leer_lineas(ini)
    secs = _secciones(lineas)
    joints = sorted(int(s.split('_')[1]) for s in secs if re.match(r'^JOINT_\d+$', s))
    n = int(_num(_valor(lineas, secs, 'KINS', 'JOINTS')) or 0) or (max(joints) + 1 if joints else 0)
    ejes = []
    for j, letra in zip(range(n), letras(lineas, secs, n)):
        e = Eje(j, letra)
        sec = 'JOINT_%d' % j
        if sec in secs:
            a, b = secs[sec]
            activa = comentada = None
            for ln in lineas[a + 1:b]:
                m = BACKLASH.match(ln)
                if not m or _num(m.group('val')) is None:
                    continue
                if m.group('hash'):
                    comentada = comentada if comentada is not None else _num(m.group('val'))
                elif activa is None:
                    activa = _num(m.group('val'))
            e.hay_linea = activa is not None or comentada is not None
            e.activo = activa is not None
            e.valor = activa if activa is not None else (comentada or 0.0)
            e.stepgen_maxaccel = _num(_valor(lineas, secs, sec, 'STEPGEN_MAXACCEL'))
            e.max_accel = _num(_valor(lineas, secs, sec, 'MAX_ACCELERATION'))
        ejes.append(e)
    return ejes


def unidades(ini):
    lineas = _leer_lineas(ini)
    u = (_valor(lineas, _secciones(lineas), 'TRAJ', 'LINEAR_UNITS') or 'mm').lower()
    return 'IN' if u in ('in', 'inch', 'imperial') else 'MM'


def _eol_de(lineas):
    for ln in lineas:
        if ln.endswith('\r\n'):
            return '\r\n'
        if ln.endswith('\n'):
            return '\n'
    return '\n'


def texto_valor(v, decimals=3):
    return ('%.' + str(int(decimals)) + 'f') % v


def aplicar(lineas, cambios, decimals=3):
    """Return updated .ini lines. cambios = {joint: (active, value)}."""
    lineas = list(lineas)
    eol = _eol_de(lineas)
    # Process from last section to first so inserting lines doesnt shift earlier section indicies.
    secs = _secciones(lineas)
    for j in sorted(cambios, reverse=True):
        activo, valor = cambios[j]
        sec = 'JOINT_%d' % j
        if sec not in secs:
            raise ValueError('.ini has no [%s] section' % sec)
        a, b = secs[sec]
        idx = [i for i in range(a + 1, b)
               if BACKLASH.match(lineas[i]) and _num(BACKLASH.match(lineas[i]).group('val')) is not None]
        activas = [i for i in idx if not BACKLASH.match(lineas[i]).group('hash')]
        principal = activas[0] if activas else (idx[0] if idx else None)
        if principal is None:
            k = b
            while k - 1 > a and not lineas[k - 1].strip():     # before trailing blank lines
                k -= 1
            if k > 0 and not lineas[k - 1].endswith(('\n', '\r')):
                lineas[k - 1] += eol
            lineas.insert(k, ('' if activo else '#') + 'BACKLASH = ' + texto_valor(valor, decimals) + eol)
            secs = _secciones(lineas)
            continue
        for i in idx:
            m = BACKLASH.match(lineas[i])
            if i == principal:
                hash_ = '' if activo else (m.group('hash') or '#')
                lineas[i] = (m.group('ind') + hash_ + 'BACKLASH' + m.group('sp1') + '=' + m.group('sp2')
                             + texto_valor(valor, decimals) + m.group('rest') + (m.group('eol') or ''))
            elif not m.group('hash'):                           # comment out duplicate active lines
                lineas[i] = m.group('ind') + '#' + lineas[i][len(m.group('ind')):]
    return lineas


def guardar(ini, cambios, carpeta_copias, decimals=3):
    """Write changes to .ini after saving a timestamped backup. Returns backup path."""
    if not cambios:
        return None
    lineas = _leer_lineas(ini)
    nuevas = aplicar(lineas, cambios, decimals=decimals)
    os.makedirs(carpeta_copias, exist_ok=True)
    base = os.path.splitext(os.path.basename(ini))[0]
    sello = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    copia = os.path.join(carpeta_copias, '%s_%s.ini' % (base, sello))
    k = 1
    while os.path.exists(copia):
        copia = os.path.join(carpeta_copias, '%s_%s_%d.ini' % (base, sello, k))
        k += 1
    shutil.copy2(ini, copia)
    # Atomic write: temp file in the same directory + os.replace, preserving file permissions.
    fd, tmp = tempfile.mkstemp(prefix='.holgura_', dir=os.path.dirname(os.path.abspath(ini)))
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(''.join(nuevas).encode(*ENC))
        shutil.copymode(ini, tmp)
        os.replace(tmp, ini)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    mias = sorted(f for f in os.listdir(carpeta_copias) if f.startswith(base + '_') and f.endswith('.ini'))
    for f in mias[:-COPIAS]:
        try:
            os.remove(os.path.join(carpeta_copias, f))
        except OSError:
            pass
    return copia


def aviso_aceleracion(eje):
    """Warning if backlash compensation is active and STEPGEN_MAXACCEL < 2x MAX_ACCELERATION
    (per PNCconf / hostmot2 documentation), else None."""
    if not eje.activo or not eje.stepgen_maxaccel or not eje.max_accel:
        return None
    if eje.stepgen_maxaccel + 1e-9 < 2 * eje.max_accel:
        return ('%s: STEPGEN_MAXACCEL %g < 2 x MAX_ACCELERATION (%g)'
                % (eje.letra, eje.stepgen_maxaccel, 2 * eje.max_accel))
    return None

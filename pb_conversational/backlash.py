# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Raúl Milla (DominicZe)
"""
BACKLASH COMPENSATION panel for Probe Basic, docked inside the SETTINGS tab.

Built so you don't have to open the .ini file by hand just to tweak axis backlash: one row per
axis with an enable toggle, value box, live active value from LinuxCNC, and a built-in dial-indicator
measurement guide. SAVE updates BACKLASH in each [JOINT_n] section (see backlash_ini.py): checked
leaves the line active, unchecked comments it out so the number stays saved. Because LinuxCNC only
reads .ini values on startup, changes take effect after restarting LinuxCNC.

Lives inside the pb_conversational user tab without modifying any Probe Basic source files. On
startup it docks into the empty left slot in SETTINGS (widget_59). If a Probe Basic variant lacks
that slot, it stays as a BACKLASH tab in the fallback user tab.
"""

import os
import sys

from qtpy.QtCore import Qt
from qtpy.QtCore import QRegularExpression
from qtpy.QtGui import QRegularExpressionValidator
from qtpy.QtWidgets import (QApplication, QWidget, QFrame, QLabel, QLineEdit, QPushButton, QGridLayout,
                            QVBoxLayout, QHBoxLayout, QSizePolicy)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backlash_ini as HI      # noqa: E402

try:
    from qtpyvcp.utilities import logger
    LOG = logger.getLogger(__name__)
except Exception:
    import logging
    LOG = logging.getLogger(__name__)

try:
    import linuxcnc
except Exception:
    linuxcnc = None

LIMITE = {'MM': 0.150, 'IN': 0.006}          # maximum allowed backlash value
DECIMALES = {'MM': 3, 'IN': 4}
AZUL = 'rgb(85, 85, 238)'                     # Probe Basic selection highlight blue
FONDO_AVISO = 'rgb(40, 40, 60)'
ERROR = 'background-color: rgb(205, 141, 123);'


def _pb_font():
    """'Probe Basic Bebas Mono' (1.0.x) or 'Bebas Kai' (0.6.x), whichever Probe Basic registered."""
    try:
        from qtpy.QtGui import QFontDatabase, QGuiApplication
        if QGuiApplication.instance() is None:
            return 'Bebas Kai'
        try:
            fams = QFontDatabase.families()
        except TypeError:
            fams = QFontDatabase().families()
    except Exception:
        fams = []
    for f in ('Probe Basic Bebas Mono', 'Bebas Kai'):
        if f in fams:
            return f
    return 'Bebas Kai'


AYUDA = u"""
<ol style="margin-left:-18px">
<li><b>Disable compensation first</b>: uncheck the axis box, click SAVE and <b>restart LinuxCNC</b>.</li>
<li>Mount a <b>dial indicator</b> against the table (X / Y axes) or spindle head (Z axis).</li>
<li>In <b>MAN</b> mode with <b>0.01</b> step, jog the axis until the needle moves. <b>Zero the dial.</b></li>
<li>Jog <b>1.00 mm</b> (or 0.050 in) further in the <b>same direction</b>.</li>
<li>Jog the same distance in the <b>opposite direction</b>. It should return to 0.00: <b>any remaining reading is your backlash</b> (e.g. 0.04 &rarr; 0.04 mm backlash).</li>
<li>Repeat <b>3 times</b> and take the average.</li>
<li>Enter the value, <b>check the box</b>, click <b>SAVE</b> and restart LinuxCNC.</li>
</ol>
<p style="margin-top:3px"><b>Verify:</b> with compensation active, the dial should return to 0.00 (&plusmn;0.01). If it overshoots past zero, the value is too high and parts will come out undersized: reduce it.</p>
"""


class Ayuda(QLabel):
    """Help label that shrinks its font (10.5 to 8.5 pt) to fit its container without pushing SETTINGS."""
    TAMANOS = (10.5, 10.0, 9.5, 9.0, 8.5)
    CSS = 'QLabel{ font: %spt "DejaVu Sans"; color: rgb(232, 232, 232); background: transparent; border: none; }'

    def __init__(self, html):
        super(Ayuda, self).__init__(html)
        self.setWordWrap(True)
        self.setTextFormat(Qt.RichText)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        sp = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setSizePolicy(sp)
        self._pt = None
        self._ajustar(self.TAMANOS[0])

    def _ajustar(self, pt):
        if pt != self._pt:
            self._pt = pt
            self.setStyleSheet(self.CSS % pt)

    def resizeEvent(self, ev):
        super(Ayuda, self).resizeEvent(ev)
        for pt in self.TAMANOS:
            self._ajustar(pt)
            if self.heightForWidth(self.width()) <= self.height():
                break

    def hasHeightForWidth(self):
        return False

    def minimumSizeHint(self):
        from qtpy.QtCore import QSize
        return QSize(0, 0)


class Casilla(QPushButton):
    """Touchscreen-friendly check button styled like Probe Basic buttons."""

    def __init__(self):
        super(Casilla, self).__init__()
        self.setCheckable(True)
        self.setFixedSize(40, 40)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet('QPushButton{ font: bold 19pt "DejaVu Sans"; padding: 0px; }')
        self.toggled.connect(lambda on: self.setText(u'✓' if on else ''))


class Holgura(QWidget):
    """Backlash editor frame + dial-indicator measurement guide frame."""

    def __init__(self, ini=None, parent=None):
        super(Holgura, self).__init__(parent)
        self.setObjectName('holgura_panel')
        self.ini = ini or os.getenv('INI_FILE_NAME') or ''
        self.copias = os.path.join(os.path.dirname(os.path.abspath(self.ini or '.')), '.holgura')
        self.font_pb = _pb_font()
        try:
            self.units = HI.unidades(self.ini)
            self.ejes = HI.leer(self.ini)
        except Exception as e:
            LOG.warning('backlash: could not read %s: %s', self.ini, e)
            self.units, self.ejes = 'MM', []
        # What LinuxCNC actually loaded at startup (from stat if live, else from .ini).
        self.en_uso = {e.joint: (e.activo, e.valor) for e in self.ejes}
        self._leer_en_uso()
        self.filas = {}
        self._build()
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self._cargar()

    def _leer_en_uso(self):
        if linuxcnc is None:
            return
        try:
            s = linuxcnc.stat()
            s.poll()
            for e in self.ejes:
                v = float(s.joint[e.joint]['backlash'])
                self.en_uso[e.joint] = (v > 0, v)
        except Exception as ex:
            LOG.info('backlash: live value read from .ini (%s)', ex)

    def _programa_en_marcha(self):
        if linuxcnc is None:
            return False
        try:
            s = linuxcnc.stat()
            s.poll()
            return s.interp_state != linuxcnc.INTERP_IDLE
        except Exception:
            return False

    def _lbl(self, text, pt, color='white', align=Qt.AlignCenter):
        lb = QLabel(text)
        lb.setAlignment(align)
        lb.setStyleSheet('QLabel{ font: %dpt "%s"; color: %s; background: transparent; border: none; }'
                         % (pt, self.font_pb, color))
        return lb

    def _build(self):
        um = self.units
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        f1 = QFrame()
        f1.setObjectName('holgura_ejes_frame')
        v1 = QVBoxLayout(f1)
        v1.setContentsMargins(18, 8, 18, 10)
        v1.setSpacing(6)
        v1.addWidget(self._lbl(u'BACKLASH COMPENSATION', 16, 'rgb(238, 238, 236)'))
        g = QGridLayout()
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(6)
        for c, span, t in ((0, 1, 'AXIS'), (1, 1, 'ENABLE'), (2, 2, 'VALUE'), (4, 1, 'ACTIVE NOW')):
            g.addWidget(self._lbl(t, 12, 'rgb(190, 190, 190)'), 0, c, 1, span)
        for r, e in enumerate(self.ejes, start=1):
            letra = self._lbl(e.letra, 24)
            chk = Casilla()
            val = QLineEdit()
            val.setObjectName('holgura_valor_%d' % e.joint)
            val.setFixedSize(130, 38)
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val.setStyleSheet('QLineEdit{ font: 19pt "%s"; }' % self.font_pb)
            val.setValidator(QRegularExpressionValidator(
                QRegularExpression(r'^\d?([.,]\d{0,%d})?$' % DECIMALES[um]), val))
            mm = self._lbl(um, 12, 'rgb(190, 190, 190)', Qt.AlignLeft | Qt.AlignVCenter)
            uso = QLabel()
            uso.setFixedSize(110, 38)
            uso.setAlignment(Qt.AlignCenter)
            for col, w in enumerate((letra, chk, val, mm, uso)):
                g.addWidget(w, r, col, Qt.AlignLeft | Qt.AlignVCenter if col == 3 else Qt.AlignCenter)
            chk.toggled.connect(self._refrescar)
            val.textChanged.connect(self._refrescar)
            self.filas[e.joint] = (chk, val, uso)
        g.setColumnStretch(5, 1)
        v1.addLayout(g)

        self.estado = QLabel()
        self.estado.setWordWrap(True)
        self.estado.setMinimumHeight(48)
        v1.addWidget(self.estado)

        hb = QHBoxLayout()
        hb.setSpacing(16)
        self.b_guardar = QPushButton('SAVE')
        self.b_guardar.setObjectName('holgura_guardar')
        self.b_deshacer = QPushButton('UNDO')
        self.b_deshacer.setObjectName('holgura_deshacer')
        for b in (self.b_guardar, self.b_deshacer):
            b.setFixedHeight(44)
            b.setFocusPolicy(Qt.NoFocus)
            hb.addWidget(b)
        self.b_guardar.clicked.connect(self.guardar)
        self.b_deshacer.clicked.connect(self._cargar)
        v1.addLayout(hb)
        lim = self._lbl(u'ALLOWED RANGE: %s TO %s %s · CANNOT SAVE WHILE A PROGRAM IS RUNNING'
                        % (self._fmt(0.0), self._fmt(LIMITE[um]), um),
                        11, 'rgb(170, 170, 170)')
        lim.setWordWrap(True)
        v1.addWidget(lim)
        self.tecnico = self._lbl('', 10, 'rgb(230, 170, 90)')
        self.tecnico.setWordWrap(True)
        self.tecnico.hide()
        v1.addWidget(self.tecnico)
        f1.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        lay.addWidget(f1)

        f2 = QFrame()
        f2.setObjectName('holgura_ayuda_frame')
        v2 = QVBoxLayout(f2)
        v2.setContentsMargins(18, 8, 18, 10)
        v2.setSpacing(4)
        v2.addWidget(self._lbl(u'HOW TO MEASURE BACKLASH', 16, 'rgb(238, 238, 236)', Qt.AlignLeft | Qt.AlignVCenter))
        ay = Ayuda(AYUDA)
        ay.setObjectName('holgura_ayuda')
        v2.addWidget(ay, 1)
        lay.addWidget(f2, 1)

    def _cargar(self, *a):
        """Load current .ini values into the UI (also used by UNDO)."""
        try:
            self.ejes = HI.leer(self.ini) if self.ini else self.ejes
        except Exception as e:
            LOG.warning('backlash: could not read %s: %s', self.ini, e)
        self._guardado = {}
        for e in self.ejes:
            if e.joint not in self.filas:
                continue
            chk, val, _ = self.filas[e.joint]
            for w in (chk, val):
                w.blockSignals(True)
            chk.setChecked(e.activo)
            chk.setText(u'✓' if e.activo else '')
            val.setText(self._fmt(e.valor))
            for w in (chk, val):
                w.blockSignals(False)
            self._guardado[e.joint] = (e.activo, round(e.valor, 6))
        self._refrescar()

    def _fmt(self, v):
        return ('%.' + str(DECIMALES[self.units]) + 'f') % v

    def _leidos(self):
        """Return {joint: (active, value_or_None)} from the UI."""
        out = {}
        for j, (chk, val, _) in self.filas.items():
            try:
                v = float(val.text().strip().replace(',', '.'))
                v = v if 0.0 <= v <= LIMITE[self.units] + 1e-12 else None
            except ValueError:
                v = None
            out[j] = (chk.isChecked(), None if v is None else round(v, 6))
        return out

    def _refrescar(self, *a):
        leidos = self._leidos()
        malos = [j for j, (_, v) in leidos.items() if v is None]
        cambiados = [j for j in leidos if j not in malos and leidos[j] != self._guardado.get(j)]
        pendientes = [e for e in self.ejes if self._guardado.get(e.joint) is not None
                      and not self._igual_en_uso(e.joint, *self._guardado[e.joint])]
        for e in self.ejes:
            chk, val, uso = self.filas[e.joint]
            if e.joint in malos:
                val.setStyleSheet('QLineEdit{ font: 19pt "%s"; %s }' % (self.font_pb, ERROR))
            elif e.joint in cambiados:
                val.setStyleSheet('QLineEdit{ font: 19pt "%s"; border: 3px solid %s; }' % (self.font_pb, AZUL))
            else:
                val.setStyleSheet('QLineEdit{ font: 19pt "%s"; }' % self.font_pb)
            act, v = self.en_uso.get(e.joint, (False, 0.0))
            uso.setText(self._fmt(v) if act else 'OFF')
            uso.setStyleSheet('QLabel{ font: 18pt "%s"; color: %s; background: rgb(30, 30, 30);'
                              ' border: 1px solid rgb(70, 70, 70); border-radius: 4px; }'
                              % (self.font_pb, 'rgb(215, 215, 215)' if act else 'rgb(140, 140, 140)'))
        self.b_guardar.setEnabled(bool(cambiados) and not malos)
        self.b_deshacer.setEnabled(bool(cambiados) or bool(malos))
        self.b_guardar.setStyleSheet(
            ('QPushButton{ background: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1, '
             'stop:0 rgba(85, 85, 238, 255), stop:0.544974 rgba(90, 91, 239, 255), stop:1 rgba(126, 135, 243, 255)); }')
            if self.b_guardar.isEnabled() else '')
        if malos:
            txt = (u'INVALID VALUE ON %s. ENTER A NUMBER BETWEEN 0 AND %s.'
                   % (', '.join(self._letra(j) for j in malos), self._fmt(LIMITE[self.units])))
            self._estado(txt, True)
        elif cambiados:
            self._estado(u'UNSAVED CHANGES ON %s. CLICK SAVE.'
                         % ', '.join(self._letra(j) for j in cambiados), True)
        elif pendientes:
            partes = []
            for e in pendientes:
                act, v = self._guardado[e.joint]
                partes.append('%s %s' % (e.letra, ('%s %s' % (self._fmt(v), self.units)) if act else 'OFF'))
            self._estado(u'SAVED: %s.\nRESTART LINUXCNC FOR CHANGES TO TAKE EFFECT.'
                         % ', '.join(partes), True)
        else:
            self._estado(u'UP TO DATE: SAVED VALUES MATCH ACTIVE LINUXCNC SETTINGS.', False)
        avisos = [a for a in (HI.aviso_aceleracion(e) for e in self.ejes) if a]
        self.tecnico.setVisible(bool(avisos))
        self.tecnico.setText(u'TECHNICAL NOTE: ' + ' · '.join(avisos) if avisos else '')

    def _igual_en_uso(self, joint, activo, valor):
        act, v = self.en_uso.get(joint, (False, 0.0))
        if not activo and not act:
            return True
        return activo == act and abs(valor - v) < 1e-6

    def _letra(self, joint):
        return next((e.letra for e in self.ejes if e.joint == joint), str(joint))

    def _estado(self, txt, destacado):
        self.estado.setText(txt)
        self.estado.setStyleSheet(
            'QLabel{ font: 14pt "%s"; color: white; padding: 4px 12px; border-radius: 5px; %s }'
            % (self.font_pb, ('border: 2px solid %s; background: %s;' % (AZUL, FONDO_AVISO)) if destacado
               else 'border: 2px solid rgb(90, 90, 90); background: rgb(30, 30, 30);'))

    def guardar(self, *a):
        leidos = self._leidos()
        if any(v is None for _, v in leidos.values()):
            return
        if self._programa_en_marcha():
            self._estado(u'CANNOT SAVE WHILE A PROGRAM IS RUNNING.', True)
            return
        cambios = {j: av for j, av in leidos.items() if av != self._guardado.get(j)}
        try:
            copia = HI.guardar(self.ini, cambios, self.copias, decimals=DECIMALES[self.units])
        except Exception as e:
            LOG.error('backlash: failed to save %s: %s', self.ini, e)
            self._estado(u'SAVE FAILED: %s' % e, True)
            return
        LOG.info('backlash: saved %s to %s (backup %s)', cambios, self.ini, copia)
        self._cargar()


def colocar_en_settings(panel):
    """Dock the panel into the empty left slot in SETTINGS (widget_59).
    Returns True if placed. Called by UserTab on startup while the main window is already built."""
    try:
        settings = hueco = None
        for top in QApplication.topLevelWidgets():
            settings = top.findChild(QWidget, 'settings_tab')
            if settings is not None:
                break
        if settings is not None:
            hueco = settings.findChild(QWidget, 'widget_59')
        if hueco is None or hueco.findChildren(QWidget):
            LOG.info('backlash: SETTINGS slot not available')
            return False
        lay = hueco.layout()
        if lay is None:
            lay = QVBoxLayout(hueco)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(panel)
        panel.show()
        LOG.info('backlash: docked into SETTINGS')
        return True
    except Exception as e:
        LOG.warning('backlash: could not dock into SETTINGS: %s', e)
        return False


if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = Holgura(sys.argv[1] if len(sys.argv) > 1 else None)
    w.resize(780, 620)
    w.show()
    sys.exit(app.exec())

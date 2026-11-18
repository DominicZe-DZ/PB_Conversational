# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Raúl Milla (DominicZe)
"""
Shared base for the conversational screens (INT./EXT. THREAD, RECT./CIRC. POCKET, RECT./CIRC. CONTOUR).

  - Reads machine settings: program folder ([DISPLAY] PROGRAM_PREFIX), units ([TRAJ] LINEAR_UNITS),
    and QtPyVCP plugins (status, tooltable, program load); falls back cleanly when run standalone.
  - Probe Basic fonts and widget stylesheets.
  - ConvPage: base QWidget with field helpers, sketch value boxes, DominicZe logo, units handling,
    tool table comment lookup, X/Y coordinate table, .ngc file saving, JSON persistence, and dialogs.

Each subclass (ThreadPage in pb_conversational.py, PocketPage in pocket_page.py, ContourPage in
contour_page.py) provides its HELP dictionary, _build_ui, _params, _refresh, _collect/_apply,
_relabel, and _apply_unit_defaults.
"""

import json
import math
import os

from qtpy.QtCore import Qt, QEvent
from qtpy.QtGui import QPixmap
from qtpy.QtWidgets import (QWidget, QHBoxLayout, QFrame, QLabel, QLineEdit, QComboBox, QMessageBox,
                            QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QPushButton)

HERE = os.path.dirname(os.path.abspath(__file__))       # last_*.json files live next to this file

# Machine INI: program folder ([DISPLAY] PROGRAM_PREFIX) and units ([TRAJ] LINEAR_UNITS).
# LinuxCNC exports the active .ini path in INI_FILE_NAME on startup.
try:
    import linuxcnc
    INI_FILE = linuxcnc.ini(os.getenv('INI_FILE_NAME'))
    PROGRAM_PREFIX = os.path.expandvars(os.path.expanduser(
        INI_FILE.find('DISPLAY', 'PROGRAM_PREFIX') or '~/linuxcnc/nc_files'))
except Exception:
    INI_FILE = None
    PROGRAM_PREFIX = os.path.expanduser('~/linuxcnc/nc_files')

try:
    _LINEAR_UNITS = (INI_FILE.find('TRAJ', 'LINEAR_UNITS') or 'mm').strip().lower()
except Exception:
    _LINEAR_UNITS = 'mm'
MACHINE_UNITS = 'IN' if _LINEAR_UNITS in ('in', 'inch', 'imperial') else 'MM'

# QtPyVCP plugins (same ones used by Probe Basic's built-in conversational widgets):
#   status     active WCS (G54..G59.3) and program units (G20/G21)
#   tooltable  tool table comments (R remark column)
#   load       loads the generated .ngc file into the backplot/viewer
try:
    from qtpyvcp.plugins import getPlugin
    from qtpyvcp.utilities import logger
    from qtpyvcp.actions.program_actions import load as load_program
    LOG = logger.getLogger(__name__)
    STATUS = getPlugin('status')
    TOOL_TABLE = getPlugin('tooltable')
except Exception:
    import logging
    LOG = logging.getLogger(__name__)
    STATUS = TOOL_TABLE = load_program = None


# --- Styles matching Probe Basic's built-in conversational .ui files ----------
def _pb_font():
    """Probe Basic registers 'Probe Basic Bebas Mono' (1.0.x / PySide6) or 'Bebas Kai' (0.6.x / PyQt5)."""
    try:
        from qtpy.QtGui import QFontDatabase, QGuiApplication
        if QGuiApplication.instance() is None:
            return 'Bebas Kai'
        try:
            families = QFontDatabase.families()            # Qt6 static method
        except TypeError:
            families = QFontDatabase().families()          # Qt5 instance method
    except Exception:
        families = []
    for fam in ('Probe Basic Bebas Mono', 'Bebas Kai'):
        if fam in families:
            return fam
    return 'Bebas Kai'


PB_FONT = _pb_font()
FONT = 'font: 14pt "%s";' % PB_FONT
BOX_FRAME = """.QFrame{
    border-style: solid; border-color: black; border-width: 2px; border-radius: 6px;
    color: rgb(238, 238, 236);
    background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:0, y2:1, stop:0 rgb(81, 86, 85),
        stop:0.160246 rgb(83, 84, 91), stop:0.489795 rgb(99, 102, 102), stop:0.699799 rgb(85, 88, 94),
        stop:0.90444 rgb(77, 84, 86), stop:1 rgb(109, 115, 118));
}"""
BOX_LABEL = 'QLabel{ font: 75 13pt "%s"; color: rgb(255, 255, 255); }' % PB_FONT
HEADER = """QLabel{
    border-style: solid; border-color: rgb(176, 179, 172); border-width: 2px; border-radius: 5px;
    color: white; background: rgb(90, 90, 90); font: 15pt "%s";
}""" % PB_FONT
TABLE = """QTableView, QHeaderView { font: 14pt "%s"; background-color: rgb(220, 220, 220);
    color: black; border: none; }
QTableView { border-top: 8px rgb(120, 120, 120); border-left: 4px rgb(120, 120, 120);
    border-bottom: 5px rgb(120, 120, 120); border-right: 4px rgb(120, 120, 120); border-radius: 5px;
    border-color: rgb(120, 120, 120); border-style: solid; background-color: rgb(120, 120, 120);
    gridline-color: rgb(203, 203, 203); alternate-background-color: rgb(90, 90, 90); }""" % PB_FONT
PANEL_TABS = """QTabWidget::pane { border: none; }
QTabWidget QTabBar::tab { margin-top: 0px; margin-right: 0px; min-width: 150px; min-height: 26px;
    font: 14pt "%s"; }
QTabBar::tab:first { margin-left: 0px; border-left-width: 2px; border-right-width: 1px; border-bottom-width: 2px;
    border-top-left-radius: 4px; border-bottom-left-radius: 4px; border-top-right-radius: 0px;
    border-bottom-right-radius: 0px; }
QTabBar::tab:middle { margin-left: 0px; }
QTabBar::tab:last { margin-left: 0px; border-left-width: 1px; border-right-width: 2px; border-top-width: 2px;
    border-top-right-radius: 4px; border-bottom-right-radius: 4px; border-top-left-radius: 0px;
    border-bottom-left-radius: 0px; }""" % PB_FONT
ERROR = 'background-color: rgb(205, 141, 123);'
WARN_TXT = 'color: rgb(255, 150, 130);'
HELP_TXT = 'color: rgb(200, 200, 190);'


def _num(text):
    """Parse user-entered numbers: accepts decimal commas and pasted thread specs ('M6 X 1' -> 6).
    Rejects NaN/Inf and raises ValueError on invalid input."""
    v = float(str(text).strip().upper().lstrip('M').split(' ')[0].replace(',', '.'))
    if not math.isfinite(v):
        raise ValueError('non-finite number')
    return v


class ConvPage(QWidget):
    """Base conversational screen. Subclasses define HELP, _help_variant(), LOG_TAG, DEFAULT_NAME,
    settings_file, _relabel, _apply_unit_defaults, _collect, and _apply."""
    HELP = {}
    LOG_TAG = 'conversational'
    DEFAULT_NAME = 'PROGRAM'

    def __init__(self, parent=None):
        super(ConvPage, self).__init__(parent)
        self._tool_missing = set()

    def _help_variant(self):
        return 0

    def _edit(self, key, text='0.000', width=None):
        w = QLineEdit(text)
        w.setStyleSheet(FONT)
        w.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if width:
            w.setFixedWidth(width)
        w.textChanged.connect(self._refresh)
        self._f[key] = w
        self._attach_help(w, key)
        return w

    def _combo(self, key, items):
        w = QComboBox()
        w.addItems(items)
        w.setStyleSheet(FONT)
        w.currentIndexChanged.connect(self._refresh)
        self._f[key] = w
        self._attach_help(w, key)
        return w

    def _attach_help(self, w, key):
        """Attach tooltip and touch/focus help-line text."""
        text = self.HELP.get(key)
        if isinstance(text, tuple):
            text = text[self._help_variant()]
        if not text:
            return
        w.setToolTip(text)
        w.setProperty('help', text)
        w.installEventFilter(self)

    def eventFilter(self, obj, ev):
        # FocusIn also fires on touchscreens where hover tooltips don't show.
        try:
            if ev.type() == QEvent.FocusIn and hasattr(self, 'help_label') and obj.property('help'):
                self.help_label.setText(obj.property('help'))
            return super(ConvPage, self).eventFilter(obj, ev)
        except RuntimeError:        # widget may already be torn down on shutdwon
            return False

    @staticmethod
    def _lab(text):
        lab = QLabel(text)
        lab.setStyleSheet(FONT)
        return lab

    def _vbox(self, key, title, value):
        """Value box overlaid on the schematic drawing (like HOLE START / CENTER X)."""
        fr = QFrame()
        fr.setStyleSheet(BOX_FRAME)
        fr.setFixedSize(140, 45)
        h = QHBoxLayout(fr)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(4)
        lab = QLabel(title)
        lab.setStyleSheet(BOX_LABEL)
        lab.setAlignment(Qt.AlignCenter)
        self._box_labels[key] = lab
        h.addWidget(lab)
        h.addWidget(self._edit(key, value, 70))
        return fr

    @staticmethod
    def _logo():
        """DominicZe logo at the bottom of the middle column; omitted if the PNG is missing."""
        path = os.path.join(HERE, 'logo_dz_oscuro.png')
        pm = QPixmap(path)
        if pm.isNull():
            return None
        pm.setDevicePixelRatio(pm.height() / 48.0)
        lab = QLabel()
        lab.setPixmap(pm)
        lab.setObjectName('logo_dz')
        lab.setStyleSheet('background: transparent;')
        lab.setToolTip('DominicZe - powered by LinuxCNC')
        return lab

    @staticmethod
    def _header(text):
        h = QLabel(text)
        h.setStyleSheet(HEADER)
        h.setAlignment(Qt.AlignCenter)
        h.setFixedHeight(33)
        return h

    def _coord_table(self, help_key):
        """30-row X/Y coordinate table (matching Probe Basic's XY COORD) plus delete buttons."""
        t = QTableWidget(30, 2)
        t.setHorizontalHeaderLabels(['X', 'Y'])
        t.setStyleSheet(TABLE)
        t.setAlternatingRowColors(True)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.verticalHeader().setDefaultSectionSize(28)
        t.setItem(0, 0, QTableWidgetItem('0.000'))
        t.setItem(0, 1, QTableWidgetItem('0.000'))
        t.itemChanged.connect(self._refresh)
        self._attach_help(t, help_key)
        btns = QHBoxLayout()
        for text, slot in (('DELETE SELECTED', self._delete_selected), ('DELETE ALL', self._delete_all)):
            b = QPushButton(text)
            b.setStyleSheet(FONT)
            b.setFixedHeight(36)
            b.clicked.connect(slot)
            btns.addWidget(b)
        return t, btns

    def _inch(self):
        return self._units == 'IN'

    def _len(self, v):
        return ('{:.4f}' if self._inch() else '{:.3f}').format(v)

    def _vc(self, dia, rpm):
        if self._inch():
            return 'VC {:.0f} SFM'.format(math.pi * dia * rpm / 12.0)
        return 'VC {:.0f} M/MIN'.format(math.pi * dia * rpm / 1000.0)

    def _start_units(self, saved):
        """Match Probe Basic: follow active G20/G21 program units when live."""
        if STATUS is not None:
            try:
                return 'IN' if STATUS.program_units() == 1 else 'MM'
            except Exception:
                pass
        return saved if saved in ('MM', 'IN') else MACHINE_UNITS

    def _mark_field(self, key, ok, strict):
        """Update field highlight without losing missing-tool warnings from _lookup()."""
        w = self._f[key]
        if not ok:
            if strict:
                w.setStyleSheet(FONT + ERROR)
        elif key in self._tool_missing:
            w.setStyleSheet(FONT + ERROR)
        else:
            w.setStyleSheet(FONT)

    def _lookup(self, key):
        """Look up (tool_dict, comment) in LinuxCNC's tool table. Highlights red if missing."""
        if TOOL_TABLE is None:
            self._tool_missing.discard(key)
            return None, None
        try:
            n = int(_num(self._f[key].text()))
            tool = TOOL_TABLE.getToolTable().get(n) if n > 0 else None
        except Exception:
            tool = None
        if tool is None:
            self._tool_missing.add(key)
            self._f[key].setStyleSheet(FONT + ERROR)
            return None, 'TOOL NOT IN TOOL TABLE'
        self._tool_missing.discard(key)
        self._f[key].setStyleSheet(FONT)
        return tool, (tool.get('R', '') or '')[:24]

    def _table_points(self, error_cls):
        """Collect non-empty X/Y rows (a blank cell counts as 0). Raises error_cls on bad numbers."""
        pts = []
        for r in range(self.table.rowCount()):
            txt = [(self.table.item(r, c).text().strip() if self.table.item(r, c) else '') for c in (0, 1)]
            if not txt[0] and not txt[1]:
                continue
            try:
                pts.append((_num(txt[0] or '0'), _num(txt[1] or '0')))
            except ValueError:
                raise error_cls('Invalid coordinate in row {}.'.format(r + 1))
        return pts

    def _delete_selected(self):
        for r in sorted(set(i.row() for i in self.table.selectedIndexes()), reverse=True):
            self.table.removeRow(r)
            self.table.insertRow(self.table.rowCount())
        self._refresh()

    def _delete_all(self):
        if self._confirm('DELETE ALL', 'Clear all coordinates?'):
            self.table.clearContents()
            self._refresh()

    def _warn(self, label, text):
        label.setText(text.upper())
        label.setStyleSheet(FONT + WARN_TXT)

    def _next_file_name(self):
        # Sanitize filename and auto-increment (_1, _2...) so existing programs are never overwritten.
        raw = self._f['name'].text().strip() or self.DEFAULT_NAME
        name = ''.join(c for c in raw if c.isalnum() or c in '-_.').lstrip('.')
        if not name:
            name = self.DEFAULT_NAME
        if not os.path.isdir(PROGRAM_PREFIX):
            os.makedirs(PROGRAM_PREFIX)
        base = os.path.join(PROGRAM_PREFIX, name)
        path = base + '.ngc'
        i = 1
        while os.path.exists(path):
            path = '{}_{:d}.ngc'.format(base, i)
            i += 1
        return path

    def _write_and_offer(self, lines):
        """Save .ngc to the next free filename and offer to load it in the viewer."""
        self._save_settings()
        path = self._next_file_name()
        try:
            with open(path, 'w') as fh:
                fh.write('\n'.join(lines) + '\n')
        except (IOError, OSError) as e:
            self._error('GCODE ERROR', 'Could not save {}:\n{}'.format(path, e))
            return
        if load_program is not None and self._confirm(
                'LOAD GCODE', 'Saved to\n{}\n\nLoad into viewer?'.format(path)):
            load_program(path)

    def _save_settings(self):
        self._sets[self._units] = self._collect()
        try:
            with open(self.settings_file, 'w') as fh:
                json.dump({'units': self._units, 'sets': self._sets}, fh, indent=1)
        except Exception as e:
            LOG.warning('%s: could not save settings: %s', self.LOG_TAG, e)

    def _load_settings(self):
        try:
            with open(self.settings_file) as fh:
                data = json.load(fh)
        except Exception:
            data = {}
        if data and 'sets' not in data:
            data = {'units': 'MM', 'sets': {'MM': data}}
        self._sets = data.get('sets', {})
        self._units = self._start_units(data.get('units'))
        self._loading = True
        try:
            self._f['units'].setCurrentIndex(self._f['units'].findText(self._units))
        finally:
            self._loading = False
        self._relabel()
        if self._units in self._sets:
            self._apply(self._sets[self._units])
        else:
            self._apply_unit_defaults()

    def _apply_fields(self, data):
        for k, v in data.items():
            w = self._f.get(k)
            if w is None or v is None or k == 'units':
                continue
            if isinstance(w, QComboBox):
                i = w.findText(v)
                if i >= 0:
                    w.setCurrentIndex(i)
            else:
                w.setText(v)

    def _collect_fields(self):
        data = {}
        for k, w in self._f.items():
            if k != 'units':
                data[k] = w.currentText() if isinstance(w, QComboBox) else w.text()
        return data

    def _fill_table(self, points):
        self.table.clearContents()
        for r, (x, y) in enumerate(points[:self.table.rowCount()]):
            self.table.setItem(r, 0, QTableWidgetItem(self._len(x)))
            self.table.setItem(r, 1, QTableWidgetItem(self._len(y)))

    def _confirm(self, title, msg):
        box = QMessageBox(QMessageBox.Question, title, msg, QMessageBox.Yes | QMessageBox.No, self,
                          Qt.FramelessWindowHint)
        return box.exec() == QMessageBox.Yes          # exec() works on both PyQt5 and PySide6

    def _error(self, title, msg):
        box = QMessageBox(QMessageBox.Critical, title, msg, QMessageBox.Ok, self, Qt.FramelessWindowHint)
        box.exec()

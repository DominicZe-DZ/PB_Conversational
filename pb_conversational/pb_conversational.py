# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Raúl Milla (DominicZe)
"""
Conversational THREAD MILLING screens (Metric and UNC/UNF) for Probe Basic.

Two screens docked into CONVERSATIONAL > HOLE OPS:
    INT. THREAD   optional peck drilling + internal thread milling with an X/Y hole table.
    EXT. THREAD   external thread milling centered at X/Y.

Implemented as a Probe Basic user tab (no Probe Basic source files are modified). On startup,
INT. THREAD replaces the empty THREAD MILL tab and EXT. THREAD is inserted next to it. The same
user tab also instantiates Pockets (pocket_page.py -> CONVERSATIONAL > POCKETS), Contours
(contour_page.py -> CONVERSATIONAL > PERIMETER), and Backlash Compensation (backlash.py -> SETTINGS).
If a Probe Basic variant lacks those slots, unplaced screens stay inside a fallback user tab
(CONVERSATIONAL+).

Files
-----
    pb_conversational.py  this UI module + UserTab docking logic
    thread_mill_gen.py    pure-Python G-code generator (no Qt)
    thread_sketch.py      cross-section and top-down QPainter sketches
"""

import os
import sys
import math
import re

from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QFrame, QLabel,
                            QLineEdit, QComboBox, QPushButton, QApplication,
                            QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
                            QAbstractItemView)

# Probe Basic imports user tabs by file path rather than as a package.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from thread_mill_gen import (ThreadMillParams, generate, validate, plan, ThreadMillError,  # noqa: E402
                             drill_tip_z, drill_tip_length, drill_pecks, hole_warning, thread_label,
                             tap_drill_spec, mill_max_diameter, bb_thread_mill, CHAMFER_EXTRA, STANDARDS)
from thread_sketch import SectionSketch, PlanSketch                                        # noqa: E402
from conv_common import (ConvPage, STATUS, TOOL_TABLE, LOG, PB_FONT, FONT, TABLE, PANEL_TABS,  # noqa: E402
                         ERROR, HELP_TXT, _num)

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_STANDARD = {('MM', True): 'M6 X 1', ('MM', False): 'M16 X 2',
                    ('IN', True): '1/4-20 UNC', ('IN', False): '5/8-11 UNC'}

UNIT_DEFAULTS = {
    'MM': dict(clearance=10.0, retract=2.0, z_top=0.0, thread_length=15.0, center_x=0.0, center_y=0.0,
               tool_diameter=0.0, tool_reach=0.0, diameter_adjust=0.0, tooth_offset=0.0, fz=0.03,
               drill_feed=120.0, drill_peck_first=5.0, drill_peck_reduce=0.5, drill_peck_min=2.0,
               drill_reentry=0.3),
    'IN': dict(clearance=0.5, retract=0.1, z_top=0.0, thread_length=0.6, center_x=0.0, center_y=0.0,
               tool_diameter=0.0, tool_reach=0.0, diameter_adjust=0.0, tooth_offset=0.0, fz=0.0012,
               drill_feed=5.0, drill_peck_first=0.2, drill_peck_reduce=0.02, drill_peck_min=0.08,
               drill_reentry=0.012),
}
DRILL_DEPTH_STEP = {'MM': 0.5, 'IN': 0.02}
DRILL_DEPTH_MIN_EXTRA = {'MM': 1.5, 'IN': 0.06}

HELP = {
    'clearance': 'SAFETY Z: rapid clearance height between holes and after tool changes. Must clear clamps.',
    'retract': 'APPROACH: R-plane distance above Z TOP. Rapids down to here; peck drill retracts here.',
    'z_top': ('Z TOP: part top face where the thread starts.',
              'Z TOP: top face of the cylinder where the thread starts.'),
    'thread_length': 'THREAD LENGTH: usable thread depth (e.g. 30 in M6x30). Bottom Z = Z TOP - length.',
    'start_diameter': ('HOLE Ø: starting bore diameter. Locked to DRILL Ø when PRE-DRILL = YES.',
                       'CYL. Ø: starting cylinder OD; normally nominal thread diameter.'),
    'center_x': 'CENTER X: cylinder center X in the active work coordinate system.',
    'center_y': 'CENTER Y: cylinder center Y in the active work coordinate system.',
    'holes': 'X / Y COORDINATES: hole centers in the active WCS. Blank rows are ignored.',
    'name': 'NAME: .ngc output filename. Auto-increments (_1, _2...) if file already exists.',
    'units': 'UNITS: MM outputs G21, IN outputs G20. Each unit system saves its own parameters.',
    'wcs': 'WCS: work coordinate system (G54 to G59.3). Defaults to the active WCS on startup.',
    'hand': 'HAND: right-hand (standard) or left-hand thread.',
    'climb': 'CUT: climb milling is recommended for thread mills (better finish, less deflection).',
    'standard': 'STANDARD: selects preset DIAMETER, PITCH/TPI, and tap drill; CUSTOM for manual entry.',
    'diameter': 'DIAMETER: nominal major thread diameter (6 for M6, 0.25 for 1/4-20).',
    'pitch': 'PITCH: axial advance per turn (1 for M6). In IN mode, enter threads per inch (TPI).',
    'radial_passes': 'PASSES: number of radial passes to reach full thread depth.',
    'pass_mode': 'STEP MODE: EQUAL removes constant depth; DECREASING takes heavier cuts first and lighter finish passes.',
    'spring_pass': 'SPRING PASS: extra finish turn at final diameter with zero radial DOC.',
    'diameter_adjust': ('DIA. OFFSET: added to final thread diameter (+ opens up fit for go/no-go gauge).',
                        'DIA. OFFSET: added to final root diameter (+ leaves bolt slightly oversized).'),
    'coolant': 'COOLANT: OFF, MIST (M7), or FLOOD (M8) for both drill and thread mill.',
    'drill_enabled': 'PRE-DRILL: YES drills each hole first; NO assumes hole is already at HOLE Ø.',
    'drill_tool_number': 'DRILL TOOL NUMBER: emits T.. M6. Verify tool exists in Tool Table.',
    'drill_diameter': 'DRILL Ø: auto-filled from standard tap drill; editable until thread changes.',
    'drill_point_angle': 'POINT ANGLE: drill tip angle (typically 118 or 135 deg) to compute tip Z.',
    'drill_depth': 'CYL. DEPTH: full-diameter drill depth from Z TOP. Clear box to restore auto depth.',
    'drill_rpm': 'DRILL SPINDLE RPM.',
    'drill_feed': 'Z FEED: drilling feedrate in units/min.',
    'drill_dwell': 'BOTTOM DWELL: dwell in seconds at final peck bottom (0 = none).',
    'drill_peck_first': 'FIRST PECK: depth of the first peck.',
    'drill_peck_reduce': 'REDUCTION: amount subtracted from each subsequent peck.',
    'drill_peck_min': 'MIN PECK: minimum peck depth.',
    'drill_reentry': 'REENTRY CLEARANCE: rapid return stops this distance above previous peck bottom.',
    'tool_number': 'CUTTER TOOL NUMBER: emits T.. M6. Verify tool exists in Tool Table.',
    'tool_diameter': 'CUTTER Ø: ACTUAL measured crest diameter. Toolpath radius is computed directly from this.',
    'flutes': 'FLUTES: number of cutting flutes (used with FZ and RPM for feedrate).',
    'teeth': 'PROFILES: number of thread teeth along the cutter (1 = single-form).',
    'tooth_offset': 'TIP-TO-TOOTH: distance from cutter tip to lowest tooth crest (required > 0 for chamfer).',
    'tool_reach': 'REACH: usable neck length (Lc). Blocks posting if thread goes deeper. 0 = skip check.',
    'chamfer': 'CHAMFER: YES cuts a 120 deg mouth countersink using the lower flank of the thread mill.',
    'chamfer_diameter': 'CHAMFER Ø: outer countersink diameter at Z TOP.',
    'bb': 'USE BB: fills CUTTER Ø, FLUTES, PROFILES, and REACH from BB single-tooth catalog.',
    'spindle_rpm': 'CUTTER SPINDLE RPM.',
    'fz': 'FZ: chip load per flute. Edge feed = FZ x FLUTES x RPM; converted to tool-center feed.',
}


class ThreadPage(ConvPage):
    """Internal or external thread milling screen."""
    HELP = HELP
    LOG_TAG = 'pb_conversational'
    DEFAULT_NAME = 'THREAD'

    def _help_variant(self):
        return 0 if self.internal else 1

    def __init__(self, internal, parent=None):
        super(ThreadPage, self).__init__(parent)
        self.internal = internal
        self.title = 'INT. THREAD' if internal else 'EXT. THREAD'
        self.setObjectName('rosca_int' if internal else 'rosca_ext')
        self.settings_file = os.path.join(HERE, 'last_{}.json'.format('int' if internal else 'ext'))
        self._f = {}
        self._box_labels = {}
        self._loading = False
        self._drill_depth_manual = False
        self._units = 'MM'
        self._sets = {}
        self._last_thread = None
        self._build_ui()
        self._load_settings()
        self._connect_status()
        # Preserve custom saved start_diameter when loading existing settings; only auto-suggest on fresh defualts.
        if self._units not in self._sets:
            self._suggest_start()
        try:
            self._last_thread = (self._units, _num(self._f['diameter'].text()), self._pitch())
        except ValueError:
            self._last_thread = None
        self._refresh()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 12)
        root.setSpacing(16)

        boxes = {
            'clearance': self._vbox('clearance', 'SAFETY\nZ', '10.000'),
            'approach': self._vbox('retract', 'APPROACH\nR', '2.000'),
            'top': self._vbox('z_top', 'Z\nTOP', '0.000'),
            'bottom': self._vbox('thread_length', 'THREAD\nLEN', '15.000'),
            'hole': self._vbox('start_diameter', 'HOLE\nØ' if self.internal else 'CYL.\nØ',
                               '5.000' if self.internal else '16.000'),
        }
        self.section = SectionSketch(boxes)
        root.addWidget(self.section, 5)

        mid = QVBoxLayout()
        mid.setSpacing(6)
        self.plan_view = PlanSketch()
        if self.internal:
            mid.addWidget(self._header('X / Y COORDINATES'))
            self.table = QTableWidget(30, 2)
            self.table.setHorizontalHeaderLabels(['X', 'Y'])
            self.table.setStyleSheet(TABLE)
            self.table.setAlternatingRowColors(True)
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.table.verticalHeader().setDefaultSectionSize(28)
            self.table.setItem(0, 0, QTableWidgetItem('0.000'))
            self.table.setItem(0, 1, QTableWidgetItem('0.000'))
            self.table.itemChanged.connect(self._refresh)
            self._attach_help(self.table, 'holes')
            mid.addWidget(self.table, 3)
            btns = QHBoxLayout()
            for text, slot in (('DELETE SELECTED', self._delete_selected), ('DELETE ALL', self._delete_all)):
                b = QPushButton(text)
                b.setStyleSheet(FONT)
                b.setFixedHeight(36)
                b.clicked.connect(slot)
                btns.addWidget(b)
            mid.addLayout(btns)
            mid.addWidget(self.plan_view, 4)
        else:
            mid.addWidget(self.plan_view, 1)
            note = QLabel('X / Y = CYLINDER CENTER')
            note.setStyleSheet(FONT)
            note.setAlignment(Qt.AlignCenter)
            mid.addWidget(note)
            centers = QHBoxLayout()
            centers.addStretch(1)
            centers.addWidget(self._vbox('center_x', 'CENTER\nX', '0.000'))
            centers.addWidget(self._vbox('center_y', 'CENTER\nY', '0.000'))
            centers.addStretch(1)
            mid.addLayout(centers)
        logo = self._logo()
        if logo is not None:
            mid.addWidget(logo, 0, Qt.AlignRight | Qt.AlignBottom)
        root.addLayout(mid, 4)

        panel = QFrame()
        panel.setFrameShape(QFrame.StyledPanel)
        panel.setFixedWidth(560)
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(12, 8, 12, 10)
        pv.setSpacing(6)
        self.ptabs = QTabWidget()
        self.ptabs.setStyleSheet(PANEL_TABS)
        self.ptabs.tabBar().setUsesScrollButtons(False)
        self.ptabs.tabBar().setDrawBase(False)
        pv.addWidget(self.ptabs, 1)

        def page():
            w = QWidget()
            w.setObjectName('page')
            w.setStyleSheet('QWidget#page { background: transparent; }')
            g = QGridLayout(w)
            g.setContentsMargins(4, 10, 4, 4)
            g.setHorizontalSpacing(10)
            g.setVerticalSpacing(4)
            g.setColumnStretch(1, 1)
            g.setColumnStretch(3, 1)
            return w, g

        def fixed(text):
            lab = self._lab(text)
            lab.setObjectName('rosca_lbl_' + re.sub(r'\W+', '_', text.lower()).strip('_'))
            return lab

        def row(g, r, l1, w1, l2=None, w2=None, span=False):
            g.addWidget(l1 if isinstance(l1, QWidget) else fixed(l1), r, 0)
            if span:
                g.addWidget(w1, r, 1, 1, 3)
                return
            g.addWidget(w1, r, 1)
            if l2 is not None:
                g.addWidget(l2 if isinstance(l2, QWidget) else fixed(l2), r, 2, 1, 1 if w2 is not None else 2)
            if w2 is not None:
                g.addWidget(w2, r, 3)

        # ---- THREAD
        w, g = page()
        name = QLineEdit('THREAD_INT' if self.internal else 'THREAD_EXT')
        name.setStyleSheet(FONT)
        self._f['name'] = name
        self._attach_help(name, 'name')
        row(g, 0, 'NAME', name, span=True)
        row(g, 1, 'UNITS', self._combo('units', ['MM', 'IN']),
            'WCS', self._combo('wcs', ['G54', 'G55', 'G56', 'G57', 'G58', 'G59', 'G59.1', 'G59.2', 'G59.3']))
        row(g, 2, 'HAND', self._combo('hand', ['RIGHT HAND', 'LEFT HAND']),
            'CUT', self._combo('climb', ['CLIMB', 'CONVENTIONAL']))
        row(g, 3, 'STANDARD', self._combo('standard', ['CUSTOM'] + [t for t, _, _ in STANDARDS['MM']]),
            span=True)
        self.pitch_label = self._lab('PITCH')
        row(g, 4, 'DIAMETER', self._edit('diameter', '6.000'), self.pitch_label, self._edit('pitch', '1.000'))
        row(g, 5, 'PASSES', self._edit('radial_passes', '3'),
            'STEP MODE', self._combo('pass_mode', ['EQUAL', 'DECREASING']))
        self.depth_label = self._lab('')
        row(g, 6, 'RADIAL DOC', self.depth_label, span=True)
        row(g, 7, 'SPRING PASS', self._combo('spring_pass', ['NO', 'YES']),
            'DIA. OFFSET', self._edit('diameter_adjust', '0.000'))
        self.hint_label = self._lab('')
        row(g, 8, 'COOLANT', self._combo('coolant', ['OFF', 'MIST', 'FLOOD']), self.hint_label)
        if self.internal:
            row(g, 9, 'CHAMFER', self._combo('chamfer', ['NO', 'YES']),
                'CHAMFER Ø', self._edit('chamfer_diameter', '6.500'))
        g.setRowStretch(10, 1)
        self.ptabs.addTab(w, 'THREAD')

        # ---- DRILL (internal only)
        if self.internal:
            w, g = page()
            row(g, 0, 'PRE-DRILL', self._combo('drill_enabled', ['YES', 'NO']), span=True)
            dtool = self._edit('drill_tool_number', '0')
            dtool.editingFinished.connect(self._drill_from_table)
            self.drill_desc = self._lab('NO TOOL SELECTED')
            row(g, 1, 'TOOL NUMBER', dtool, self.drill_desc)
            row(g, 2, 'DRILL Ø', self._edit('drill_diameter', '5.000'),
                'POINT ANGLE', self._edit('drill_point_angle', '118'))
            row(g, 3, 'CYL. DEPTH', self._edit('drill_depth', '17.000'),
                'SPINDLE RPM', self._edit('drill_rpm', '1500'))
            row(g, 4, 'Z FEED', self._edit('drill_feed', '120.000'),
                'DWELL SEC', self._edit('drill_dwell', '0.000'))
            row(g, 5, 'FIRST PECK', self._edit('drill_peck_first', '5.000'),
                'REDUCTION', self._edit('drill_peck_reduce', '0.500'))
            row(g, 6, 'MIN PECK', self._edit('drill_peck_min', '2.000'),
                'REENTRY', self._edit('drill_reentry', '0.300'))
            self.drill_info = self._lab('')
            self.drill_info.setWordWrap(True)
            g.addWidget(self.drill_info, 7, 0, 2, 4)
            g.setRowStretch(9, 1)
            self.ptabs.addTab(w, 'DRILL')
            self._f['drill_diameter'].textChanged.connect(self._drill_to_hole)
            self._f['drill_enabled'].currentIndexChanged.connect(self._drilling_toggled)
            self._f['drill_depth'].textEdited.connect(lambda *a: setattr(self, '_drill_depth_manual', True))
            self._f['drill_depth'].editingFinished.connect(self._drill_depth_back_to_auto)

        # ---- ENDMILL
        w, g = page()
        tool = self._edit('tool_number', '0')
        tool.editingFinished.connect(self._tool_from_table)
        self.tool_desc = self._lab('NO TOOL SELECTED')
        row(g, 0, 'TOOL NUMBER', tool, self.tool_desc)
        row(g, 1, 'CUTTER Ø', self._edit('tool_diameter', '0.000'), 'FLUTES', self._edit('flutes', '3'))
        row(g, 2, 'PROFILES', self._edit('teeth', '1'), 'TIP-TOOTH', self._edit('tooth_offset', '0.000'))
        self.fz_label = self._lab('FZ MM/FLUTE')
        row(g, 3, 'SPINDLE RPM', self._edit('spindle_rpm', '6000'), self.fz_label, self._edit('fz', '0.030'))
        self.bb_button = QPushButton('USE BB')
        self.bb_button.setStyleSheet(FONT)
        self.bb_button.setFixedHeight(30)
        self.bb_button.clicked.connect(self._use_bb)
        self._attach_help(self.bb_button, 'bb')
        row(g, 4, 'REACH', self._edit('tool_reach', '0.000'), self.bb_button)
        self.bb_label = self._lab('')
        g.addWidget(self.bb_label, 5, 0, 1, 4)
        self.mill_info = self._lab('')
        self.mill_info.setWordWrap(True)
        g.addWidget(self.mill_info, 6, 0, 2, 4)
        g.setRowStretch(8, 1)
        self.ptabs.addTab(w, 'ENDMILL')

        free = [self.depth_label, self.mill_info] + ([self.drill_info] if self.internal else [])
        for wd in self.ptabs.findChildren(QWidget):
            if not isinstance(wd, (QLineEdit, QComboBox, QLabel)):
                continue
            if wd not in free and not isinstance(wd.parentWidget(), QComboBox):
                wd.setFixedHeight(30)

        self.help_label = QLabel('TOUCH ANY FIELD FOR HELP.')
        self.help_label.setStyleSheet('font: 12pt "%s";' % PB_FONT + HELP_TXT)
        self.help_label.setWordWrap(True)
        self.help_label.setMinimumHeight(44)
        self.help_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        pv.addWidget(self.help_label)

        self.summary = self._lab('')
        self.summary.setWordWrap(True)
        self.summary.setMinimumHeight(48)
        pv.addWidget(self.summary)
        self.post = QPushButton('POST TO FILE')
        self.post.setObjectName('rosca_post_to_file')
        self.post.setStyleSheet(FONT)
        self.post.setFixedHeight(40)
        self.post.clicked.connect(self.on_post)
        pv.addWidget(self.post)
        root.addWidget(panel, 0)

        self._f['units'].currentIndexChanged.connect(self._units_changed)
        self._f['standard'].currentIndexChanged.connect(self._apply_standard)
        for k in ('diameter', 'pitch'):
            self._f[k].textEdited.connect(self._match_standard)
            self._f[k].editingFinished.connect(self._thread_changed)
        for k in ('thread_length', 'pitch', 'tooth_offset'):
            self._f[k].textChanged.connect(self._auto_drill_depth)
        self._f['standard'].setCurrentIndex(self._f['standard'].findText('M6 X 1' if self.internal else 'M16 X 2'))

    def _pitch(self):
        v = _num(self._f['pitch'].text())
        if self._inch():
            return 1.0 / v if v > 0 else 0.0
        return v

    def _relabel(self):
        self.pitch_label.setText('TPI' if self._inch() else 'PITCH')
        self.fz_label.setText('FZ IN/FLUTE' if self._inch() else 'FZ MM/FLUTE')
        c = self._f['standard']
        c.blockSignals(True)
        c.clear()
        c.addItems(['CUSTOM'] + [t for t, _, _ in STANDARDS[self._units]])
        c.blockSignals(False)

    def _units_changed(self, *a):
        new = self._f['units'].currentText()
        if self._loading or new == self._units:
            return
        self._sets[self._units] = self._collect()
        self._units = new
        self._relabel()
        if new in self._sets:
            self._apply(self._sets[new])
        else:
            self._apply_unit_defaults()
        try:
            self._last_thread = (self._units, _num(self._f['diameter'].text()), self._pitch())
        except ValueError:
            self._last_thread = None
        self._tools_from_table()
        self._refresh()

    def _apply_unit_defaults(self):
        self._loading = True
        try:
            for k, v in UNIT_DEFAULTS[self._units].items():
                if k in self._f:
                    self._f[k].setText(self._len(v))
            self._drill_depth_manual = False
            if self.internal:
                self.table.clearContents()
                self.table.setItem(0, 0, QTableWidgetItem(self._len(0)))
                self.table.setItem(0, 1, QTableWidgetItem(self._len(0)))
        finally:
            self._loading = False
        c = self._f['standard']
        c.setCurrentIndex(c.findText(DEFAULT_STANDARD[(self._units, self.internal)]))

    def _apply_standard(self, idx):
        if idx <= 0 or self._loading:
            return
        _, d, p = STANDARDS[self._units][idx - 1]
        self._f['diameter'].setText(self._len(d))
        self._f['pitch'].setText('{:g}'.format(p) if self._inch() else '{:.3f}'.format(p))
        self._thread_changed()

    def _match_standard(self, *a):
        try:
            d, p = _num(self._f['diameter'].text()), _num(self._f['pitch'].text())
        except ValueError:
            d = p = None
        idx = 0
        for i, (_, sd, sp) in enumerate(STANDARDS[self._units]):
            if d is not None and abs(sd - d) < 1e-6 and abs(sp - p) < 1e-6:
                idx = i + 1
        c = self._f['standard']
        c.blockSignals(True)
        c.setCurrentIndex(idx)
        c.blockSignals(False)

    def _suggest_start(self, *a):
        if self._loading:
            return
        try:
            d, p = _num(self._f['diameter'].text()), self._pitch()
        except ValueError:
            return
        if self.internal and self._drilling():
            self._drill_to_hole()
        else:
            spec = self._rec_drill() if self.internal else None
            self._f['start_diameter'].setText(self._len(spec[1] if spec else (d - p if self.internal else d)))
        self._auto_drill_depth()
        self._refresh()

    def _rec_drill(self):
        try:
            p = ThreadMillParams()
            p.units, p.internal = self._units, True
            p.diameter, p.pitch = _num(self._f['diameter'].text()), self._pitch()
        except ValueError:
            return None
        if not p.diameter > p.pitch > 0:
            return None
        return tap_drill_spec(p)

    def _drill_label(self, spec):
        name, dia = spec
        return 'DRILL {} = Ø{}'.format(name, self._len(dia)) if name else 'DRILL Ø{}'.format(self._len(dia))

    def _thread_changed(self, *a):
        if self._loading:
            return
        try:
            key = (self._units, _num(self._f['diameter'].text()), self._pitch())
        except ValueError:
            return
        self._match_standard()
        if key != self._last_thread:
            self._last_thread = key
            spec = self._rec_drill() if self.internal else None
            if spec:
                self._f['drill_diameter'].setText(self._len(spec[1]))
                self._f['chamfer_diameter'].setText(self._len(key[1] + CHAMFER_EXTRA[self._units]))
            self._suggest_start()

    def _bb(self):
        try:
            p = ThreadMillParams()
            p.units, p.internal = self._units, self.internal
            p.diameter, p.pitch = _num(self._f['diameter'].text()), self._pitch()
        except ValueError:
            return None
        return bb_thread_mill(p) if p.diameter > p.pitch > 0 else None

    def _use_bb(self):
        b = self._bb()
        if b is None:
            return
        self._f['tool_diameter'].setText(self._len(b['dc']))
        self._f['flutes'].setText(str(b['flutes']))
        self._f['teeth'].setText('1')
        self._f['tool_reach'].setText(self._len(b['reach']))

    def _drilling_toggled(self, *a):
        if self._loading:
            return
        spec = self._rec_drill()
        if self._drilling() and spec:
            self._f['drill_diameter'].setText(self._len(spec[1]))
        self._suggest_start()

    def _thread_name(self, p):
        std = self._f['standard']
        return std.currentText() if std.currentIndex() > 0 else thread_label(p)

    def _drilling(self):
        return self.internal and self._f['drill_enabled'].currentIndex() == 0

    def _drill_to_hole(self, *a):
        if self._loading:
            return
        if self._drilling():
            self._f['start_diameter'].setText(self._f['drill_diameter'].text())
        self._refresh()

    def _auto_drill_depth(self, *a):
        if not self.internal or self._loading or self._drill_depth_manual:
            return
        try:
            length = _num(self._f['thread_length'].text())
            pitch = self._pitch()
            off = _num(self._f['tooth_offset'].text() or '0')
        except ValueError:
            return
        step = DRILL_DEPTH_STEP[self._units]
        depth = math.ceil((length + off + max(2 * pitch, DRILL_DEPTH_MIN_EXTRA[self._units])) / step - 1e-9) * step
        self._f['drill_depth'].setText(self._len(depth))

    def _drill_depth_back_to_auto(self):
        if not self._f['drill_depth'].text().strip():
            self._drill_depth_manual = False
            self._auto_drill_depth()

    def _tools_from_table(self):
        self._tool_from_table()
        if self.internal:
            self._drill_from_table()

    def _drill_from_table(self, *a):
        tool, desc = self._lookup('drill_tool_number')
        if desc is not None:
            self.drill_desc.setText(desc)

    def _tool_from_table(self, *a):
        tool, desc = self._lookup('tool_number')
        if desc is not None:
            self.tool_desc.setText(desc)

    def _holes(self):
        if not self.internal:
            return [(_num(self._f['center_x'].text()), _num(self._f['center_y'].text()))]
        return self._table_points(ThreadMillError)

    def _params(self, strict=True):
        f = self._f
        p = ThreadMillParams()
        bad = []

        def num(key, as_int=False):
            try:
                v = _num(f[key].text())
                self._mark_field(key, True, strict)
                return int(round(v)) if as_int else v
            except ValueError:
                self._mark_field(key, False, strict)
                bad.append(key)
                return 0

        p.units = self._units
        p.internal = self.internal
        p.right_hand = f['hand'].currentIndex() == 0
        p.climb = f['climb'].currentIndex() == 0
        p.diameter = num('diameter')
        p.pitch = num('pitch')
        if self._inch():
            p.pitch = 1.0 / p.pitch if p.pitch > 0 else 0.0
        p.start_diameter = num('start_diameter') or None
        p.diameter_adjust = num('diameter_adjust')
        p.tool_number = num('tool_number', True)
        p.tool_diameter = num('tool_diameter')
        p.flutes = num('flutes', True)
        p.teeth = num('teeth', True)
        p.tooth_offset = num('tooth_offset')
        p.spindle_rpm = num('spindle_rpm')
        p.feed_edge = num('fz') * p.flutes * p.spindle_rpm
        p.radial_passes = num('radial_passes', True)
        p.pass_mode = 'EQ' if f['pass_mode'].currentIndex() == 0 else 'DEC'
        p.spring_pass = f['spring_pass'].currentIndex() == 1
        p.coolant = f['coolant'].currentText()
        p.wcs = f['wcs'].currentText()
        p.z_top = num('z_top')
        p.z_bottom = p.z_top - num('thread_length')
        p.retract = num('retract')
        p.clearance = num('clearance')
        p.name = f['name'].text().strip()
        p.tool_reach = num('tool_reach')
        if self.internal:
            p.chamfer = f['chamfer'].currentIndex() == 1
            p.chamfer_diameter = num('chamfer_diameter')
        p.drill_enabled = self._drilling()
        if p.drill_enabled:
            p.drill_tool_number = num('drill_tool_number', True)
            p.drill_diameter = num('drill_diameter')
            p.drill_point_angle = num('drill_point_angle')
            p.drill_depth = num('drill_depth')
            p.drill_rpm = num('drill_rpm')
            p.drill_feed = num('drill_feed')
            p.drill_dwell = num('drill_dwell')
            p.drill_peck_first = num('drill_peck_first')
            p.drill_peck_reduce = num('drill_peck_reduce')
            p.drill_peck_min = num('drill_peck_min')
            p.drill_reentry = num('drill_reentry')
        table_err = None
        if not self.internal:
            p.holes = [(num('center_x'), num('center_y'))]
        else:
            try:
                p.holes = self._holes()
            except ThreadMillError as e:
                table_err = str(e)
            except ValueError:
                bad.append('holes')
        if bad:
            raise ThreadMillError('Invalid numeric value in highlighted field(s).')
        if table_err:
            raise ThreadMillError(table_err)
        if p.flutes < 1:
            raise ThreadMillError('Cutter must have at least 1 flute.')
        return p

    def _refresh(self, *a):
        if self._loading or not hasattr(self, 'summary'):
            return
        if self.internal:
            on = self._drilling()
            for key in ('drill_tool_number', 'drill_diameter', 'drill_point_angle', 'drill_depth', 'drill_rpm',
                        'drill_feed', 'drill_dwell', 'drill_peck_first', 'drill_peck_reduce', 'drill_peck_min',
                        'drill_reentry'):
                self._f[key].setEnabled(on)
            self._f['start_diameter'].setReadOnly(on)
            self._f['chamfer_diameter'].setEnabled(self._f['chamfer'].currentIndex() == 1)
        b = self._bb()
        self.bb_button.setEnabled(b is not None)
        self.bb_label.setText('BB 1-TOOTH {}: Ø{}  ·  REACH {}  ·  {} FLUTES'.format(
            b['name'], self._len(b['dc']), self._len(b['reach']), b['flutes'])
            if b else 'NO BB SINGLE-TOOTH CUTTER MATCHES THIS THREAD')
        try:
            p = self._params(strict=False)
        except ThreadMillError as e:
            self.section.set_params(None, str(e))
            self.plan_view.set_params(None, str(e))
            self._warn(self.summary, str(e))
            return
        errs = validate(p)
        problem = errs[0] if errs else None
        drawable = p.diameter > 0 and p.pitch > 0 and p.tool_diameter > 0 and p.z_top > p.z_bottom and p.holes
        self.section.set_params(p if drawable else None, problem)
        self.plan_view.set_params(p if drawable and not errs else None, problem)
        if p.pitch > 0:
            spec = self._rec_drill() if self.internal else None
            self.hint_label.setText(self._drill_label(spec) if spec else
                                    'ROOT Ø{}'.format(self._len(p.minor_external())) if not self.internal else '')
        zd = 4 if self._inch() else 2
        if self.internal:
            drill_errs = [e for e in errs if e.startswith('Drill:')]
            if not p.drill_enabled:
                self.drill_info.setText('NO PRE-DRILL: THREAD STARTS FROM DRAWING HOLE Ø.')
                self.drill_info.setStyleSheet(FONT)
            elif not drill_errs:
                pk = drill_pecks(p)
                spec = tap_drill_spec(p)
                if abs(p.drill_diameter - spec[1]) < 1e-6:
                    head = '{}: {}'.format(self._thread_name(p), self._drill_label(spec))
                else:
                    head = 'CUSTOM DRILL Ø{} · {} SUGGESTS {}'.format(
                        self._len(p.drill_diameter), self._thread_name(p), self._drill_label(spec))
                self.drill_info.setText('{}\n{} PECKS TO R-PLANE  ·  TIP Z{:.{d}f} (CONE {:.{d}f})\n'
                                        '{}  ·  THREAD HOLE Ø = DRILL Ø'.format(
                                            head, len(pk), drill_tip_z(p), drill_tip_length(p),
                                            self._vc(p.drill_diameter, p.drill_rpm), d=zd))
                self.drill_info.setStyleSheet(FONT)
            else:
                self._warn(self.drill_info, drill_errs[0])
        if drawable and not errs:
            k = plan(p)
            self.depth_label.setText('{}  =  {}{}'.format(
                self._len(sum(k['pass_depths'])), ' + '.join(self._len(x) for x in k['pass_depths']),
                '  + SPRING' if p.spring_pass else ''))
            mmax = mill_max_diameter(p, k['start_d'])
            cp = k['chamfer']
            self.mill_info.setText('{}{}  ·  F EDGE {:.{d}f}  ·  F CENTER {:.{d}f}\n'
                                   '{} TURNS X {} PASSES{}'.format(
                                       'CREST AT Ø{} WITH CUTTER Ø{}{}\n'.format(
                                           self._len(k['final_d']), self._len(p.tool_diameter),
                                           ' (HOLE Ø{})'.format(self._len(mmax)) if mmax else ' (ROOT)'),
                                       self._vc(p.tool_diameter, p.spindle_rpm), p.feed_edge,
                                       k['feed'], k['turns'], len(k['radii']),
                                       '  ·  CHAMFER {} DEEP, {} TURNS'.format(self._len(cp['depth']), len(cp['passes']))
                                       if cp else '', d=1 if self._inch() else 0))
            steps = []
            if p.drill_enabled:
                steps.append('T{} DRILL Ø{:g} → Z{:.{d}f}'.format(p.drill_tool_number, p.drill_diameter,
                                                                  drill_tip_z(p), d=zd))
            steps.append('T{} CUTTER Ø{:g}  {}  L{:g}{}'.format(
                p.tool_number, p.tool_diameter, self._thread_name(p), round(p.z_top - p.z_bottom, 4),
                ' + CHAMFER Ø{:g}'.format(p.chamfer_diameter) if cp else ''))
            n = len(p.holes)
            text = '   ▸   '.join(steps) + ('   ·   {} HOLES'.format(n) if n > 1 else '')
            warn = hole_warning(p)
            if warn:
                self.summary.setText('WARNING: ' + warn.upper() + '\n' + text)
                self.summary.setStyleSheet(FONT + 'color: rgb(242, 177, 52);')
            else:
                self.summary.setText(text)
                self.summary.setStyleSheet(FONT)
        else:
            self.depth_label.setText('')
            self.mill_info.setText('')
            self._warn(self.summary, problem or 'MISSING DATA: TOOL NUMBER, CUTTER Ø, OR COORDINATES')

    def _connect_status(self):
        if STATUS is None:
            return
        try:
            idx = STATUS.g5x_index() - 1
            if 0 <= idx < self._f['wcs'].count():
                self._f['wcs'].setCurrentIndex(idx)
            self._tool_from_table()
            if self.internal:
                self._drill_from_table()
            TOOL_TABLE.tool_table_changed.connect(lambda *a: self._tools_from_table())
        except Exception as e:
            LOG.warning('pb_conversational: could not read machine status: %s', e)

    def on_post(self):
        try:
            p = self._params()
            lines = generate(p)
        except ThreadMillError as e:
            self._error('GCODE ERROR', str(e))
            return
        self._write_and_offer(lines)

    def _collect(self):
        data = self._collect_fields()
        data['_drill_depth_manual'] = self._drill_depth_manual
        if self.internal:
            try:
                data['_holes'] = self._holes()
            except ThreadMillError:
                pass
        return data

    def _apply(self, data):
        self._loading = True
        try:
            self._apply_fields(data)
            self._drill_depth_manual = bool(data.get('_drill_depth_manual', False))
            if self.internal and data.get('_holes'):
                self._fill_table(data['_holes'])
        finally:
            self._loading = False


class UserTab(QWidget):
    """Probe Basic user-tab container. Places the 6 conversational pages + Backlash into their
    target slots in Probe Basic on startup, or keeps unplaced ones inside CONVERSATIONAL+."""

    def __init__(self, parent=None):
        super(UserTab, self).__init__(parent)
        self.setObjectName('CONVERSATIONAL+')
        self.page_int = ThreadPage(internal=True)
        self.page_ext = ThreadPage(internal=False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.inner = QTabWidget()
        for pg in (self.page_int, self.page_ext):
            self.inner.addTab(pg, pg.title)
        self.page_rect = self.page_circ = self.page_isla_rect = self.page_isla_circ = None
        try:
            from pocket_page import PocketPage
            self.page_rect = PocketPage(circular=False)
            self.page_circ = PocketPage(circular=True)
            self.inner.addTab(self.page_rect, self.page_rect.title)
            self.inner.addTab(self.page_circ, self.page_circ.title)
        except Exception as e:
            LOG.warning('pb_conversational: could not create pocket screens: %s', e)
        try:
            from contour_page import ContourPage
            self.page_isla_rect = ContourPage(circular=False)
            self.page_isla_circ = ContourPage(circular=True)
            self.inner.addTab(self.page_isla_rect, self.page_isla_rect.title)
            self.inner.addTab(self.page_isla_circ, self.page_isla_circ.title)
        except Exception as e:
            LOG.warning('pb_conversational: could not create contour screens: %s', e)
        self.backlash = None
        try:
            from backlash import Holgura, colocar_en_settings
            self.backlash = Holgura()
            if not colocar_en_settings(self.backlash):
                self.inner.addTab(self.backlash, 'BACKLASH')
        except Exception as e:
            LOG.warning('pb_conversational: could not create backlash screen: %s', e)
        lay.addWidget(self.inner)
        # Wait one event loop turn so Probe Basic's main widnow tabs exist before docking.
        QTimer.singleShot(0, self._dock)

    def _find(self, name):
        for w in QApplication.topLevelWidgets():
            cand = w.findChild(QWidget, name)
            if cand is not None and not self.isAncestorOf(cand) and cand is not self:
                return cand
        return None

    @staticmethod
    def _layout_of(target):
        # Must check 'is None' (in PyQt5 an empty layout can evaluate falsy).
        lay = target.layout()
        if lay is None:
            lay = QVBoxLayout(target)
        lay.setContentsMargins(0, 0, 0, 0)
        return lay

    @staticmethod
    def _tab_widget_of(w):
        ops = w.parentWidget()
        while ops is not None and not isinstance(ops, QTabWidget):
            ops = ops.parentWidget()
        return ops

    def _dock(self):
        try:
            self._dock_into_hole_ops()
        except Exception as e:
            LOG.warning('pb_conversational: could not dock into HOLE OPS: %s', e)
        for tab, pages in (('pockets_tab', (self.page_rect, self.page_circ)),
                           ('perimeter_tab', (self.page_isla_rect, self.page_isla_circ))):
            if None in pages:
                continue
            try:
                self._dock_into_free_tabs(tab, pages)
            except Exception as e:
                LOG.warning('pb_conversational: could not dock into %s: %s', tab, e)
        if self.inner.count() == 0:
            self._remove_own_tab()

    def _dock_into_hole_ops(self):
        target = self._find('thread_mill')
        if target is None or target.findChildren(QWidget):
            return
        ops = self._tab_widget_of(target)
        if ops is None:
            return
        lay = self._layout_of(target)
        self.inner.removeTab(self.inner.indexOf(self.page_int))
        self.inner.removeTab(self.inner.indexOf(self.page_ext))
        lay.addWidget(self.page_int)
        self.page_int.show()
        i = ops.indexOf(target)
        ops.setTabText(i, self.page_int.title)
        ops.insertTab(i + 1, self.page_ext, self.page_ext.title)
        LOG.info('pb_conversational: docked into HOLE OPS (INT. THREAD / EXT. THREAD)')

    def _dock_into_free_tabs(self, tab_name, pages):
        host = self._find(tab_name)
        if host is None:
            return
        ops = next((t for t in host.findChildren(QTabWidget)
                    if not host.isAncestorOf(self._tab_widget_of(t))), None)
        if ops is None or ops.count() < len(pages):
            return

        def free(i):
            w = ops.widget(i)
            return w is not None and not ops.tabText(i).strip() and not w.findChildren(QWidget)

        if not all(free(i) for i in range(len(pages))):
            return
        for i, pg in enumerate(pages):
            target = ops.widget(i)
            self.inner.removeTab(self.inner.indexOf(pg))
            lay = self._layout_of(target)
            lay.addWidget(pg)
            pg.show()
            ops.setTabText(i, pg.title)
        bar = ops.tabBar()
        if hasattr(bar, 'setTabVisible'):
            for i in range(len(pages), ops.count()):
                if free(i):
                    bar.setTabVisible(i, False)
        ops.setCurrentIndex(0)
        LOG.info('pb_conversational: docked into %s (%s)', tab_name, ' / '.join(pg.title for pg in pages))

    def _remove_own_tab(self):
        main = self.parentWidget()
        while main is not None and not isinstance(main, QTabWidget):
            main = main.parentWidget()
        if main is not None and main.indexOf(self) >= 0:
            was_current = main.currentWidget() is self
            main.removeTab(main.indexOf(self))
            if was_current:
                main.setCurrentIndex(0)
        self.hide()
        for w in QApplication.topLevelWidgets():
            combo = w.findChild(QComboBox, 'startup_tab_combobox')
            if combo is not None:
                k = combo.findText(self.objectName())
                if k >= 0:
                    combo.removeItem(k)

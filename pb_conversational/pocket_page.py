# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Raúl Milla (DominicZe)
"""
Conversational POCKET screens for Probe Basic: RECT. POCKET and CIRC. POCKET,
docked into CONVERSATIONAL > POCKETS.

One pocket per program, always centered at X0 Y0 in the active WCS (touch off work zero at the
center of the pocket).
"""

import os
import re

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QFrame, QLabel, QLineEdit,
                            QComboBox, QPushButton, QTabWidget)

from pocket_gen import PocketParams, PocketError, validate, plan, generate, pocket_warning, label
from pocket_sketch import PocketSectionSketch, PocketPlanSketch
from conv_common import ConvPage, STATUS, TOOL_TABLE, LOG, PB_FONT, FONT, PANEL_TABS, ERROR, HELP_TXT, HERE, _num

UNIT_DEFAULTS = {
    'MM': dict(clearance=10.0, retract=2.0, z_top=0.0, depth=10.0, step_down=2.0, length=40.0, width=25.0,
               corner_radius=5.0, angle=0.0, diameter=30.0, wall_stock=0.2, floor_stock=0.1, tool_diameter=0.0,
               fz=0.03),
    'IN': dict(clearance=0.5, retract=0.1, z_top=0.0, depth=0.4, step_down=0.08, length=1.5, width=1.0,
               corner_radius=0.1875, angle=0.0, diameter=1.25, wall_stock=0.008, floor_stock=0.004, tool_diameter=0.0,
               fz=0.0012),
}
ENTRIES = [('HELIX', 'HELIX'), ('RAMP', 'RAMP'), ('PLUNGE', 'PLUNGE')]

HELP = {
    'clearance': 'SAFETY Z: rapid clearance height between pockets and after tool changes. Must clear clamps.',
    'retract': 'APPROACH: R-plane distance above Z TOP. Rapids down to here, then feeds in.',
    'z_top': 'Z TOP: part top face where the pocket starts.',
    'depth': 'DEPTH: pocket depth below Z TOP. Bottom Z = Z TOP - depth.',
    'step_down': 'STEP Z: maximum Z step per roughing level (divided into equal passes).',
    'name': 'NAME: .ngc output filename. Auto-increments (_1, _2...) if file already exists.',
    'units': 'UNITS: MM outputs G21, IN outputs G20. Each unit system saves its own parameters.',
    'wcs': 'WCS: work coordinate system (G54 to G59.3). Defaults to the active WCS on startup.',
    'length': 'LENGTH X: finished pocket size along X (before rotation).',
    'width': 'WIDTH Y: finished pocket size along Y (before rotation).',
    'corner_radius': 'CORNER R: corner radius. If smaller than cutter radius, corners will equal cutter radius.',
    'angle': 'ANGLE: CCW rotation around pocket center (degrees).',
    'diameter': 'DIAMETER: finished circular pocket diameter.',
    'wall_stock': 'WALL STOCK: radial material left for full-depth wall finish (0 = skip wall finish).',
    'floor_stock': 'FLOOR STOCK: axial material left for floor finish pass (0 = skip floor finish).',
    'spring_pass': 'SPRING PASS: repeat wall finish pass at zero DOC to clean up tool deflection.',
    'coolant': 'COOLANT: OFF, MIST (M7), or FLOOD (M8).',
    'stepover': 'STEPOVER %: radial stepover per pass as % of cutter Ø (40% typical, 70% max).',
    'climb': 'CUT: climb (recommended: G3 inside pocket) or conventional (G2; for heavy backlash).',
    'entry': 'ENTRY: HELIX spirals down at center; RAMP zig-zags along long axis; PLUNGE feeds straight down.',
    'ramp_angle': 'RAMP ANGLE: helix/ramp slope in degrees (2-3 deg for steel, up to 5 deg for aluminum).',
    'entry_feed': 'ENTRY FEED %: feedrate for Z entry and full-width center slot as % of cutting feed.',
    'tool_number': 'TOOL NUMBER: emits T.. M6. Verify tool exists in Tool Table.',
    'tool_diameter': 'CUTTER Ø: ACTUAL measured cutter diameter. Toolpath is offset directly from this.',
    'flutes': 'FLUTES: number of flutes (used with FZ and RPM for feedrate).',
    'spindle_rpm': 'SPINDLE RPM.',
    'fz': 'FZ: chip load per flute. Feedrate = FZ x FLUTES x RPM.',
}


class PocketPage(ConvPage):
    """Rectangular or circular pocket screen (also subclassed by ContourPage)."""
    HELP = HELP
    LOG_TAG = 'pocket'
    DEFAULT_NAME = 'POCKET'
    KIND = 'pocket'
    TITLES = ('RECT. POCKET', 'CIRC. POCKET')
    SETTINGS = ('last_rect.json', 'last_circ.json')
    SHAPE_TAB = 'POCKET'
    UNIT_DEFAULTS = UNIT_DEFAULTS
    Error = PocketError
    SectionSketch = PocketSectionSketch
    PlanSketch = PocketPlanSketch

    @staticmethod
    def new_params():
        return PocketParams()

    validate = staticmethod(validate)
    plan = staticmethod(plan)
    generate = staticmethod(generate)
    warning = staticmethod(pocket_warning)
    label = staticmethod(label)

    def __init__(self, circular, parent=None):
        super(PocketPage, self).__init__(parent)
        self.circular = circular
        self.title = self.TITLES[1 if circular else 0]
        self.setObjectName('{}_{}'.format(self.KIND, 'circ' if circular else 'rect'))
        self.settings_file = os.path.join(HERE, self.SETTINGS[1 if circular else 0])
        self._f = {}
        self._box_labels = {}
        self._loading = False
        self._units = 'MM'
        self._sets = {}
        self._build_ui()
        self._load_settings()
        self._connect_status()
        self._refresh()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 12)
        root.setSpacing(16)

        boxes = {
            'clearance': self._vbox('clearance', 'SAFETY\nZ', '10.000'),
            'approach': self._vbox('retract', 'APPROACH\nR', '2.000'),
            'top': self._vbox('z_top', 'Z\nTOP', '0.000'),
            'step': self._vbox('step_down', 'STEP\nZ', '2.000'),
            'bottom': self._vbox('depth', 'DEPTH', '10.000'),
        }
        self.section = self.SectionSketch(boxes)
        root.addWidget(self.section, 5)

        mid = QVBoxLayout()
        mid.setSpacing(6)
        self.plan_view = self.PlanSketch()
        mid.addWidget(self.plan_view, 1)
        self.origin_note = QLabel('')
        self.origin_note.setStyleSheet(FONT)
        self.origin_note.setAlignment(Qt.AlignCenter)
        self.origin_note.setWordWrap(True)
        mid.addWidget(self.origin_note)
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
            lab.setObjectName(self.KIND + '_lbl_' + re.sub(r'\W+', '_', text.lower()).strip('_'))
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

        # ---- POCKET / CONTOUR
        w, g = page()
        name = QLineEdit('{}_{}'.format(self.DEFAULT_NAME, 'CIRC' if self.circular else 'RECT'))
        name.setStyleSheet(FONT)
        self._f['name'] = name
        self._attach_help(name, 'name')
        row(g, 0, 'NAME', name, span=True)
        row(g, 1, 'UNITS', self._combo('units', ['MM', 'IN']),
            'WCS', self._combo('wcs', ['G54', 'G55', 'G56', 'G57', 'G58', 'G59', 'G59.1', 'G59.2', 'G59.3']))
        r = self._shape_rows(g, row, 2)
        row(g, r, 'WALL STOCK', self._edit('wall_stock', '0.200'),
            'FLOOR STOCK', self._edit('floor_stock', '0.100'))
        row(g, r + 1, 'SPRING PASS', self._combo('spring_pass', ['NO', 'YES']),
            'COOLANT', self._combo('coolant', ['OFF', 'MIST', 'FLOOD']))
        self.shape_info = self._lab('')
        self.shape_info.setWordWrap(True)
        g.addWidget(self.shape_info, r + 2, 0, 2, 4)
        g.setRowStretch(r + 4, 1)
        self.ptabs.addTab(w, self.SHAPE_TAB)

        # ---- ROUGHING
        w, g = page()
        row(g, 0, 'STEPOVER %', self._edit('stepover', '40'), 'CUT', self._combo('climb', ['CLIMB', 'CONVENTIONAL']))
        r = self._rough_rows(g, row, 1)
        self.rough_info = self._lab('')
        self.rough_info.setWordWrap(True)
        g.addWidget(self.rough_info, r, 0, 3, 4)
        g.setRowStretch(r + 3, 1)
        self.ptabs.addTab(w, 'ROUGHING')

        # ---- ENDMILL
        w, g = page()
        tool = self._edit('tool_number', '0')
        tool.editingFinished.connect(self._tool_from_table)
        self.tool_desc = self._lab('NO TOOL SELECTED')
        row(g, 0, 'TOOL NUMBER', tool, self.tool_desc)
        row(g, 1, 'CUTTER Ø', self._edit('tool_diameter', '0.000'), 'FLUTES', self._edit('flutes', '3'))
        self.fz_label = self._lab('FZ MM/FLUTE')
        row(g, 2, 'SPINDLE RPM', self._edit('spindle_rpm', '3000'), self.fz_label, self._edit('fz', '0.030'))
        self.mill_info = self._lab('')
        self.mill_info.setWordWrap(True)
        g.addWidget(self.mill_info, 3, 0, 3, 4)
        g.setRowStretch(6, 1)
        self.ptabs.addTab(w, 'ENDMILL')

        free = [self.shape_info, self.rough_info, self.mill_info]
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
        self.post.setObjectName(self.KIND + '_post_to_file')
        self.post.setStyleSheet(FONT)
        self.post.setFixedHeight(40)
        self.post.clicked.connect(self.on_post)
        pv.addWidget(self.post)
        root.addWidget(panel, 0)

        self._f['units'].currentIndexChanged.connect(self._units_changed)

    def _shape_rows(self, g, row, r):
        if self.circular:
            row(g, r, 'DIAMETER', self._edit('diameter', '30.000'), QLabel(''))
            return r + 1
        row(g, r, 'LENGTH X', self._edit('length', '40.000'), 'WIDTH Y', self._edit('width', '25.000'))
        row(g, r + 1, 'CORNER R', self._edit('corner_radius', '5.000'), 'ANGLE', self._edit('angle', '0.000'))
        return r + 2

    def _rough_rows(self, g, row, r):
        row(g, r, 'ENTRY', self._combo('entry', [t for t, _ in ENTRIES]), 'RAMP ANGLE', self._edit('ramp_angle', '3.0'))
        row(g, r + 1, 'ENTRY FEED %', self._edit('entry_feed', '50'), QLabel(''))
        return r + 2

    def _relabel(self):
        self.fz_label.setText('FZ IN/FLUTE' if self._inch() else 'FZ MM/FLUTE')

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
        self._tool_from_table()
        self._refresh()

    def _apply_unit_defaults(self):
        self._loading = True
        try:
            for k, v in self.UNIT_DEFAULTS[self._units].items():
                if k in self._f:
                    self._f[k].setText(self._len(v) if k != 'angle' else '{:.3f}'.format(v))
        finally:
            self._loading = False

    def _tool_from_table(self, *a):
        tool, desc = self._lookup('tool_number')
        if desc is not None:
            self.tool_desc.setText(desc)

    def _connect_status(self):
        if STATUS is None:
            return
        try:
            idx = STATUS.g5x_index() - 1
            if 0 <= idx < self._f['wcs'].count():
                self._f['wcs'].setCurrentIndex(idx)
            self._tool_from_table()
            TOOL_TABLE.tool_table_changed.connect(lambda *a: self._tool_from_table())
        except Exception as e:
            LOG.warning('%s: could not read machine status: %s', self.LOG_TAG, e)

    def _params(self, strict=True):
        f = self._f
        p = self.new_params()
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
        p.circular = self.circular
        if self.circular:
            p.diameter = num('diameter')
        else:
            p.length, p.width = num('length'), num('width')
            p.corner_radius, p.angle = num('corner_radius'), num('angle')
        p.z_top, p.depth, p.step_down = num('z_top'), num('depth'), num('step_down')
        p.retract, p.clearance = num('retract'), num('clearance')
        p.wall_stock, p.floor_stock = num('wall_stock'), num('floor_stock')
        p.spring_pass = f['spring_pass'].currentIndex() == 1
        p.coolant, p.wcs = f['coolant'].currentText(), f['wcs'].currentText()
        p.stepover = num('stepover') / 100.0
        p.climb = f['climb'].currentIndex() == 0
        p.entry_feed = num('entry_feed') / 100.0
        self._params_extra(p, num)
        p.tool_number = num('tool_number', True)
        p.tool_diameter = num('tool_diameter')
        p.flutes = num('flutes', True)
        p.spindle_rpm = num('spindle_rpm')
        p.feed = num('fz') * p.flutes * p.spindle_rpm
        p.name = f['name'].text().strip()
        p.centers = [(0.0, 0.0)]
        if bad:
            raise self.Error('Invalid numeric value in highlighted field(s).')
        if p.flutes < 1:
            raise self.Error('Cutter must have at least 1 flute.')
        return p

    def _params_extra(self, p, num):
        p.entry = ENTRIES[max(0, self._f['entry'].currentIndex())][1]
        p.ramp_angle = num('ramp_angle')

    def _origin_text(self):
        return 'POCKET IS CENTERED AT X0 Y0 IN {}: ZERO WORKPIECE AT POCKET CENTER'.format(
            self._f['wcs'].currentText())

    def _refresh(self, *a):
        if self._loading or not hasattr(self, 'summary'):
            return
        self.origin_note.setText(self._origin_text())
        try:
            p = self._params(strict=False)
        except self.Error as e:
            self.section.set_params(None, str(e))
            self.plan_view.set_params(None, str(e))
            self._warn(self.summary, str(e))
            return
        errs = self.validate(p)
        problem = errs[0] if errs else None
        self.section.set_params(p if not errs else None, problem)
        self.plan_view.set_params(p if not errs else None, problem)
        if errs:
            self.shape_info.setText('')
            self.rough_info.setText('')
            self.mill_info.setText('')
            self._warn(self.summary, problem)
            return
        k = self.plan(p)
        shape, rough, mill = self._texts(p, k)
        self.shape_info.setText(shape)
        self.shape_info.setStyleSheet(FONT)
        self.rough_info.setText(rough)
        self.rough_info.setStyleSheet(FONT)
        self.mill_info.setText(mill)
        self.mill_info.setStyleSheet(FONT)
        text = 'T{} CUTTER Ø{:g}  ·  {}  ·  DEPTH {:g}'.format(
            p.tool_number, p.tool_diameter, self.label(p), round(p.depth, 4))
        warn = self.warning(p)
        if warn:
            self.summary.setText('WARNING: ' + warn.upper() + '\n' + text)
            self.summary.setStyleSheet(FONT + 'color: rgb(242, 177, 52);')
        else:
            self.summary.setText(text)
            self.summary.setStyleSheet(FONT)

    def _finish_text(self, p, k):
        fin = []
        if k['finish_wall']:
            fin.append('WALL{}'.format(' + SPRING' if p.spring_pass else ''))
        if k['finish_floor']:
            fin.append('FLOOR')
        return 'FINISH: ' + ' & '.join(fin) if fin else 'NO FINISHING PASS'

    def _mill_text(self, p, k):
        fd = 1 if self._inch() else 0
        return '{}  ·  F {:.{d}f}  ·  F ENTRY {:.{d}f}{}'.format(
            self._vc(p.tool_diameter, p.spindle_rpm), k['feed'], k['feed_entry'],
            '\nF FINISH {:.{d}f} (TOOL CENTER)'.format(k['feed_finish'], d=fd)
            if self.circular and k['finish_wall'] else '', d=fd)

    def _texts(self, p, k):
        g = k['g']
        d = self._len
        if self.circular:
            shape = 'FINISH: PATH Ø{} (POCKET Ø{})'.format(d(2 * g['r_fin']), d(p.diameter))
        else:
            rc = max(p.corner_radius, g['rt'])
            shape = 'CORNERS R{}{}  ·  FINISH: PATH {} X {}'.format(
                d(rc), ' (SET BY CUTTER)' if p.corner_radius < g['rt'] - 1e-9 else '',
                d(2 * (g['A'] - g['rt'])), d(2 * (g['B'] - g['rt'])))
        nl = len(k['levels']) - (1 if k['finish_floor'] else 0)
        step = (p.z_top - k['z_rough']) / nl
        ent = k['entry']
        ent_txt = {'HELIX': 'HELIX Ø{}'.format(d(2 * ent['r'])), 'RAMP': 'RAMP {}'.format(d(2 * ent['s'])),
                   'PLUNGE': 'PLUNGE'}[ent['kind']]
        how = 'SPIRAL' if self.circular else '{} PASSES{}'.format(
            k['loops'], ' + CENTER SLOT' if g.get('s_len', 0) > 1e-9 else '')
        rough = '{} LEVELS OF {}  ·  STEPOVER {}\nENTRY: {}  ·  {}'.format(
            nl, d(step), d(k['stepover']), ent_txt, how)
        return shape + '\n' + self._finish_text(p, k), rough, self._mill_text(p, k)

    def on_post(self):
        try:
            p = self._params()
            lines = self.generate(p)
        except self.Error as e:
            self._error('GCODE ERROR', str(e))
            return
        self._write_and_offer(lines)

    def _collect(self):
        return self._collect_fields()

    def _apply(self, data):
        self._loading = True
        try:
            self._apply_fields(data)
        finally:
            self._loading = False

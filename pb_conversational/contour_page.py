# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Raúl Milla (DominicZe)
"""
Conversational EXTERNAL CONTOUR screens for Probe Basic: RECT. CONTOUR and CIRC. CONTOUR,
docked into CONVERSATIONAL > PERIMETER.

Leaves a finished island centered at X0 Y0 in the active WCS, clearing material from a
rectangular stock block (STOCK X x STOCK Y, also centered at X0 Y0).
"""

from qtpy.QtWidgets import QLabel

import contour_gen as CG
from pocket_page import PocketPage, HELP as POCKET_HELP
from pocket_sketch import ContourSectionSketch, ContourPlanSketch

UNIT_DEFAULTS = {
    'MM': dict(clearance=10.0, retract=2.0, z_top=0.0, depth=5.0, step_down=2.0, length=40.0, width=25.0,
               corner_radius=5.0, angle=0.0, diameter=30.0, stock_x=50.0, stock_y=35.0, wall_stock=0.2,
               floor_stock=0.1, tool_diameter=0.0, fz=0.03),
    'IN': dict(clearance=0.5, retract=0.1, z_top=0.0, depth=0.2, step_down=0.08, length=1.5, width=1.0,
               corner_radius=0.1875, angle=0.0, diameter=1.25, stock_x=2.0, stock_y=1.4, wall_stock=0.008,
               floor_stock=0.004, tool_diameter=0.0, fz=0.0012),
}

HELP = dict(POCKET_HELP)
HELP.update({
    'clearance': 'SAFETY Z: rapid clearance height after tool changes. Must clear clamps.',
    'z_top': 'Z TOP: top face of both stock and finished island.',
    'depth': 'DEPTH: contour cut depth from Z TOP. Part must protrude above vise jaws more than this.',
    'length': 'LENGTH X: finished island size along X (before rotation).',
    'width': 'WIDTH Y: finished island size along Y (before rotation).',
    'corner_radius': 'CORNER R: island corner radius (0 = sharp corners).',
    'angle': 'ANGLE: CCW island rotation around X0 Y0 (degrees). Stock remains unrotated.',
    'diameter': 'DIAMETER: finished circular island diameter.',
    'stock_x': 'STOCK X: raw stock X dimension centered at X0 Y0 (for round stock, enter OD in X and Y).',
    'stock_y': 'STOCK Y: raw stock Y dimension centered at X0 Y0.',
    'wall_stock': 'WALL STOCK: radial stock left on island wall for finishing pass (0 = skip wall finish).',
    'stepover': 'STEPOVER %: max radial cut per pass as % of cutter Ø (40% typical, 70% max).',
    'climb': 'CUT: climb (recommended: G2 outside island) or conventional (G3; for heavy backlash).',
    'entry_feed': 'ENTRY FEED %: feedrate for Z plunge outside stock, step-in moves and wall lead-in/out as % of feed.',
    'tool_diameter': 'CUTTER Ø: ACTUAL measured cutter diameter. Oversized cutter yields undersized island.',
    'name': 'NAME: .ngc output filename. Auto-increments (_1, _2...) if file already exists.',
})
for _k in ('entry', 'ramp_angle'):
    HELP.pop(_k, None)


class ContourPage(PocketPage):
    """Rectangular or circular external island contouring screen."""
    HELP = HELP
    LOG_TAG = 'contour'
    DEFAULT_NAME = 'ISLAND'
    KIND = 'contour'
    TITLES = ('RECT. CONTOUR', 'CIRC. CONTOUR')
    SETTINGS = ('last_island_rect.json', 'last_island_circ.json')
    SHAPE_TAB = 'CONTOUR'
    UNIT_DEFAULTS = UNIT_DEFAULTS
    Error = CG.ContourError
    SectionSketch = ContourSectionSketch
    PlanSketch = ContourPlanSketch

    @staticmethod
    def new_params():
        return CG.ContourParams()

    validate = staticmethod(CG.validate)
    plan = staticmethod(CG.plan)
    generate = staticmethod(CG.generate)
    warning = staticmethod(CG.contour_warning)
    label = staticmethod(CG.label)

    def _shape_rows(self, g, row, r):
        if self.circular:
            row(g, r, 'DIAMETER', self._edit('diameter', '30.000'), QLabel(''))
            r += 1
        else:
            row(g, r, 'LENGTH X', self._edit('length', '40.000'), 'WIDTH Y', self._edit('width', '25.000'))
            row(g, r + 1, 'CORNER R', self._edit('corner_radius', '5.000'), 'ANGLE', self._edit('angle', '0.000'))
            r += 2
        row(g, r, 'STOCK X', self._edit('stock_x', '50.000'), 'STOCK Y', self._edit('stock_y', '35.000'))
        return r + 1

    def _rough_rows(self, g, row, r):
        row(g, r, 'ENTRY FEED %', self._edit('entry_feed', '50'), QLabel(''))
        return r + 1

    def _params_extra(self, p, num):
        p.stock_x, p.stock_y = num('stock_x'), num('stock_y')

    def _origin_text(self):
        return 'ISLAND IS CENTERED AT X0 Y0 IN {}: ZERO WORKPIECE AT STOCK CENTER'.format(
            self._f['wcs'].currentText())

    def _texts(self, p, k):
        g = k['g']
        d = self._len
        rt = g['rt']
        if self.circular:
            shape = 'FINISH: PATH Ø{} (ISLAND Ø{})'.format(d(2 * g['r_fin']), d(p.diameter))
        else:
            shape = 'FINISH: PATH {} X {} R{}'.format(d(2 * (g['A'] + rt)), d(2 * (g['B'] + rt)), d(g['rc'] + rt))
        nl = len(k['levels']) - (1 if k['finish_floor'] else 0)
        step = (p.z_top - k['z_rough']) / nl
        rough = '{} LEVELS OF {}  ·  {} OUTSIDE-IN PASS{}\nMAX STEPOVER {}  ·  PLUNGES OUTSIDE STOCK'.format(
            nl, d(step), k['loops'], 'ES' if k['loops'] > 1 else '', d(k['stepover']))
        return shape + '\n' + self._finish_text(p, k), rough, self._mill_text(p, k)

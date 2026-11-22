# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Raúl Milla (DominicZe)
"""
Pure-Python G-code generator for thread milling (and optional peck drilling) in LinuxCNC.

Zero Qt or LinuxCNC dependencies so it can be unit-tested or reused standalone.
Used by ThreadPage (pb_conversational.py) and the schematic sketches (thread_sketch.py).

Flow
----
    ThreadMillParams  all operation inputs (populated by the UI or caller)
    validate(p)       list of blocking errors (empty = ready to generate)
    hole_warning(p)   non-blocking warning (hole/cylinder size mismatch)
    plan(p)           toolpath calculation: radii, feedrate, Z heights, turns
    generate(p)       validates, plans, and returns the complete .ngc program as lines

Conventions
-----------
- 60-degree thread form (ISO metric / Unified UN).
- Absolute coordinates (G90), XY plane (G17), incremental arc centers (G91.1).
- Spindle always runs clockwise (M3), as standard thread mills require.
- Climb milling with M3:
      internal thread -> G3 (CCW from above)
      external thread -> G2 (CW from above)
- A right-hand thread moves up (+Z) when following a G3 helix.
- Tool Z = tool TIP (matching standard tool length offset).
  The lowest thread profile sits 'tooth_offset' above the tip.
- Helical tangential lead-in / lead-out to avoid leaving witness marks:
      internal: semicircle starting/ending at hole center (no straight plunge into cut);
      external: quarter-circle starting/ending clear of the cylinder OD.
  Each lead arc advances in Z by the same fraction of a pitch as the polar angle it sweeps
  around the thread axis (P/4 internal, ~P/12..P/8 external), so the tooth enters the groove
  following the thread lead instead of dragging across a flank.
- Lead arcs run at a reduced feed (LEAD FEED %, default 50%) referred to the cutting edge at the
  contact point: on a lead arc of radius R the edge travels on R + tool radius, so the tool-center
  feed is F_lead = F_edge * lead% * R / (R + tool radius). Without this the edge would run 1.6x to
  3x faster than on the helix while the tooth is still loading up.
- Radial passes use decreasing depth (~sqrt(i/n), like single-point lathe threading) or equal
  steps, plus an optional spring pass.
- Feedrate: user enters cutting-edge feed; converted to tool-center feed for LinuxCNC:
  F_center = F_edge * R_center / R_edge.
- Lowest profile tooth never drops below z_bottom (helix starts/ends one lead-arc rise above z_bottom).
"""

import math

# ---------------------------------------------------------------- thread profile
# 60-degree thread form (ISO 68-1 / ASME B1.1). Fundamental triangle height H = 0.866025 P.
H_FACTOR = math.sqrt(3) / 2.0
MINOR_INT_FACTOR = 1.082532            # D1 = D - 1.082532 P   (internal minor diameter) = D - 5/4 H
MINOR_EXT_FACTOR = 1.226869            # d3 = d - 1.226869 P   (external minor/root diameter, ISO)


# ---------------------------------------------------------------- standard threads
# ISO 261 / ISO 262: nominal_mm -> [coarse_pitch, fine_pitches...]
METRIC = [
    (1.6, [0.35]), (1.8, [0.35]), (2, [0.4]), (2.5, [0.45]), (3, [0.5]), (3.5, [0.6]), (4, [0.7]),
    (5, [0.8]), (6, [1.0]), (7, [1.0]), (8, [1.25, 1.0]), (10, [1.5, 1.25, 1.0]),
    (12, [1.75, 1.5, 1.25]), (14, [2.0, 1.5]), (16, [2.0, 1.5]), (18, [2.5, 2.0, 1.5]),
    (20, [2.5, 2.0, 1.5]), (22, [2.5, 2.0, 1.5]), (24, [3.0, 2.0]), (30, [3.5, 2.0]),
    (36, [4.0, 3.0]), (42, [4.5, 3.0]), (48, [5.0, 3.0]), (56, [5.5, 4.0]), (60, [5.5, 4.0]),
    (64, [6.0, 4.0]),
]
# ASME B1.1 (UNC / UNF): name, diameter in inches, coarse TPI, fine TPI
UNIFIED = [
    ('#0', 0.060, None, 80), ('#1', 0.073, 64, 72), ('#2', 0.086, 56, 64), ('#3', 0.099, 48, 56),
    ('#4', 0.112, 40, 48), ('#5', 0.125, 40, 44), ('#6', 0.138, 32, 40), ('#8', 0.164, 32, 36),
    ('#10', 0.190, 24, 32), ('#12', 0.216, 24, 28), ('1/4', 0.250, 20, 28), ('5/16', 0.3125, 18, 24),
    ('3/8', 0.375, 16, 24), ('7/16', 0.4375, 14, 20), ('1/2', 0.500, 13, 20), ('9/16', 0.5625, 12, 18),
    ('5/8', 0.625, 11, 18), ('3/4', 0.750, 10, 16), ('7/8', 0.875, 9, 14), ('1', 1.000, 8, 12),
    ('1-1/8', 1.125, 7, 12), ('1-1/4', 1.250, 7, 12), ('1-3/8', 1.375, 6, 12), ('1-1/2', 1.500, 6, 12),
    ('1-3/4', 1.750, 5, None), ('2', 2.000, 4.5, None),
]
# Per-unit presets: (label, diameter, pitch_or_tpi). In inches, pitch field is TPI.
STANDARDS = {
    'MM': [('M{:g} X {:g}{}'.format(d, p, '' if i == 0 else ' FINE'), float(d), p)
           for d, ps in METRIC for i, p in enumerate(ps)],
    'IN': [('{}-{:g} {}'.format(n, t, kind), d, float(t))
           for n, d, unc, unf in UNIFIED for t, kind in ((unc, 'UNC'), (unf, 'UNF')) if t],
}


# ---------------------------------------------------------------- tap drills
# Metric (ISO 2306): (nominal_d, pitch) -> drill_d in mm
METRIC_TAP_DRILLS = {
    (1.6, 0.35): 1.25, (1.8, 0.35): 1.45, (2, 0.4): 1.6, (2.5, 0.45): 2.05, (3, 0.5): 2.5, (3.5, 0.6): 2.9,
    (4, 0.7): 3.3, (5, 0.8): 4.2, (6, 1.0): 5.0, (7, 1.0): 6.0, (8, 1.25): 6.8, (8, 1.0): 7.0, (10, 1.5): 8.5,
    (10, 1.25): 8.8, (10, 1.0): 9.0, (12, 1.75): 10.2, (12, 1.5): 10.5, (12, 1.25): 10.8, (14, 2.0): 12.0,
    (14, 1.5): 12.5, (16, 2.0): 14.0, (16, 1.5): 14.5, (18, 2.5): 15.5, (18, 2.0): 16.0, (18, 1.5): 16.5,
    (20, 2.5): 17.5, (20, 2.0): 18.0, (20, 1.5): 18.5, (22, 2.5): 19.5, (22, 2.0): 20.0, (22, 1.5): 20.5,
    (24, 3.0): 21.0, (24, 2.0): 22.0, (30, 3.5): 26.5, (30, 2.0): 28.0, (36, 4.0): 32.0, (36, 3.0): 33.0,
    (42, 4.5): 37.5, (42, 3.0): 39.0, (48, 5.0): 43.0, (48, 3.0): 45.0, (56, 5.5): 50.5, (56, 4.0): 52.0,
    (60, 5.5): 54.5, (60, 4.0): 56.0, (64, 6.0): 58.0, (64, 4.0): 60.0,
}
# UNC / UNF, ~75% thread engagement: (nominal_in, tpi) -> (drill_name, dia_in)
UNIFIED_TAP_DRILLS = {
    (0.060, 80): ('3/64', 0.0469), (0.073, 64): ('#53', 0.0595), (0.073, 72): ('#53', 0.0595),
    (0.086, 56): ('#50', 0.0700), (0.086, 64): ('#50', 0.0700), (0.099, 48): ('#47', 0.0785),
    (0.099, 56): ('#45', 0.0820), (0.112, 40): ('#43', 0.0890), (0.112, 48): ('#42', 0.0935),
    (0.125, 40): ('#38', 0.1015), (0.125, 44): ('#37', 0.1040), (0.138, 32): ('#36', 0.1065),
    (0.138, 40): ('#33', 0.1130), (0.164, 32): ('#29', 0.1360), (0.164, 36): ('#29', 0.1360),
    (0.190, 24): ('#25', 0.1495), (0.190, 32): ('#21', 0.1590), (0.216, 24): ('#16', 0.1770),
    (0.216, 28): ('#14', 0.1820), (0.250, 20): ('#7', 0.2010), (0.250, 28): ('#3', 0.2130),
    (0.3125, 18): ('F', 0.2570), (0.3125, 24): ('I', 0.2720), (0.375, 16): ('5/16', 0.3125),
    (0.375, 24): ('Q', 0.3320), (0.4375, 14): ('U', 0.3680), (0.4375, 20): ('25/64', 0.3906),
    (0.500, 13): ('27/64', 0.4219), (0.500, 20): ('29/64', 0.4531), (0.5625, 12): ('31/64', 0.4844),
    (0.5625, 18): ('33/64', 0.5156), (0.625, 11): ('17/32', 0.5312), (0.625, 18): ('37/64', 0.5781),
    (0.750, 10): ('21/32', 0.6562), (0.750, 16): ('11/16', 0.6875), (0.875, 9): ('49/64', 0.7656),
    (0.875, 14): ('13/16', 0.8125), (1.000, 8): ('7/8', 0.8750), (1.000, 12): ('59/64', 0.9219),
    (1.125, 7): ('63/64', 0.9844), (1.125, 12): ('1-3/64', 1.0469), (1.250, 7): ('1-7/64', 1.1094),
    (1.250, 12): ('1-11/64', 1.1719), (1.375, 6): ('1-7/32', 1.2188), (1.375, 12): ('1-19/64', 1.2969),
    (1.500, 6): ('1-11/32', 1.3438), (1.500, 12): ('1-27/64', 1.4219), (1.750, 5): ('1-9/16', 1.5625),
    (2.000, 4.5): ('1-25/32', 1.7812),
}


class ThreadMillError(ValueError):
    """Validation or geometry error shown to the user."""
    pass


class ThreadMillParams(object):
    """All inputs for a thread milling operation in active program units (mm or in).
    For inches, pitch is 1 / TPI. Z values are absolute in the chosen WCS."""

    def __init__(self):
        self.units = 'MM'
        self.internal = True
        self.right_hand = True
        self.climb = True
        self.diameter = 10.0
        self.pitch = 1.5
        self.diameter_adjust = 0.0
        self.start_diameter = None
        # Tool
        self.tool_number = 1
        self.tool_diameter = 6.0     # crest diameter
        self.teeth = 1               # number of thread profiles; 1 = single-form
        self.flutes = 3
        self.tooth_offset = 0.0      # distance from tool tip to lowest tooth crest
        # Cutting
        self.spindle_rpm = 3000.0
        self.feed_edge = 300.0       # cutting edge feedrate (units/min)
        self.feed_compensate = True  # convert edge feed to tool-center feed
        self.lead_feed = 0.5         # lead-in / lead-out feed as fraction of edge feed
        self.radial_passes = 1
        self.pass_mode = 'DEC'       # 'DEC' decreasing (~sqrt(i/n)) or 'EQ' equal
        self.spring_pass = False
        self.coolant = 'OFF'         # 'OFF', 'MIST', 'FLOOD'
        self.wcs = 'G54'
        # Z heights
        self.z_top = 0.0
        self.z_bottom = -10.0
        self.retract = 2.0
        self.clearance = 10.0
        self.home_z_at_end = True
        # 120-deg mouth chamfer (internal threads only) cut with the thread mill
        self.chamfer = False
        self.chamfer_diameter = 0.0
        self.tool_reach = 0.0        # usable neck reach from tip (0 = unchecked)
        # Optional pre-drilling (internal threads only)
        self.drill_enabled = False
        self.drill_tool_number = 0
        self.drill_diameter = 5.0
        self.drill_point_angle = 118.0
        self.drill_peck_first = 5.0
        self.drill_peck_reduce = 0.5
        self.drill_peck_min = 2.0
        self.drill_reentry = 0.3
        self.drill_dwell = 0.0
        self.drill_depth = 12.0
        self.drill_rpm = 1500.0
        self.drill_feed = 120.0
        # Positions
        self.holes = [(0.0, 0.0)]
        self.name = ''

    def minor_internal(self):
        return self.diameter - MINOR_INT_FACTOR * self.pitch

    def minor_external(self):
        return self.diameter - MINOR_EXT_FACTOR * self.pitch

    def depth(self):
        if self.internal:
            return (self.diameter - self.minor_internal()) / 2.0
        return (self.diameter - self.minor_external()) / 2.0


# ---------------------------------------------------------------- formatting
def _fmt(v):
    """4 decimal places for G-code coordinates (works for mm and inch); avoids '-0.0000'."""
    s = '{:.4f}'.format(v)
    return '0.0000' if s == '-0.0000' else s


def _short(v):
    """Compact human-readable number for comments: 5.0000 -> 5."""
    t = _fmt(v).rstrip('0').rstrip('.')
    return '0' if t in ('', '-0') else t


def _c(code, comment):
    """Emit a G-code line with an inline comment (LinuxCNC forbids nested parentheses)."""
    assert '(' not in comment and ')' not in comment, comment
    return '{} ({})'.format(code, comment)


def _rot(v, ang):
    """Rotate 2D vector (x, y) CCW by ang radians."""
    c, s = math.cos(ang), math.sin(ang)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)


# ---------------------------------------------------------------- drilling
def drill_tip_length(p):
    """Cone tip length of the twist drill: radius / tan(point_angle / 2)."""
    return (p.drill_diameter / 2.0) / math.tan(math.radians(p.drill_point_angle / 2.0))


def drill_tip_z(p):
    """Final Z reached by the drill tip."""
    return p.z_top - p.drill_depth - drill_tip_length(p)


def drill_pecks(p):
    """Z depths of each peck down to the drill tip."""
    tip = drill_tip_z(p)
    zs, z, q, n = [], p.z_top, p.drill_peck_first, 0
    while z > tip + 1e-9 and n < 2000:
        z = max(z - q, tip)
        zs.append(z)
        q = max(q - p.drill_peck_reduce, p.drill_peck_min)
        n += 1
    return zs


# ---------------------------------------------------------------- validation
def _validate_drill(p):
    """Pre-drill validation errors (prefixed with 'Drill:' so the UI shows them in the DRILL tab)."""
    e = []
    if not (p.drill_enabled and p.internal):
        return e
    if p.drill_tool_number < 1:
        e.append('Drill: drill tool number is required (T0 is invalid).')
    if p.drill_tool_number == p.tool_number:
        e.append('Drill: drill and thread mill cannot share the same tool number.')
    if p.drill_diameter <= 0:
        e.append('Drill: drill diameter must be greater than 0.')
    if not 60 <= p.drill_point_angle <= 180:
        e.append('Drill: point angle out of range (60-180 deg).')
    if p.drill_rpm <= 0 or p.drill_feed <= 0:
        e.append('Drill: RPM and Z feedrate must be greater than 0.')
    if p.drill_peck_first <= 0 or p.drill_peck_min <= 0:
        e.append('Drill: first peck and minimum peck must be greater than 0.')
    elif p.drill_peck_min > p.drill_peck_first:
        e.append('Drill: minimum peck cannot exceed first peck.')
    if p.drill_peck_reduce < 0:
        e.append('Drill: peck reduction cannot be negative.')
    if p.drill_reentry < 0 or p.drill_reentry >= p.drill_peck_min:
        e.append('Drill: reentry clearance must be >= 0 and less than minimum peck.')
    if p.drill_dwell < 0:
        e.append('Drill: bottom dwell cannot be negative.')
    need = p.z_top - (p.z_bottom - p.tooth_offset)
    if p.drill_depth < need - 1e-9:
        e.append('Drill: full-diameter depth ({}) is shallower than thread mill tip travel ({}).'.format(
            _fmt(p.drill_depth), _fmt(need)))
    if p.start_diameter and abs(p.start_diameter - p.drill_diameter) > 1e-6:
        e.append('Drill: hole diameter ({}) does not match drill diameter ({}).'.format(
            _fmt(p.start_diameter), _fmt(p.drill_diameter)))
    return e


def validate(p):
    """Return a list of blocking error messages (empty list if valid)."""
    errors = []
    if p.diameter <= 0:
        errors.append('Thread diameter must be greater than 0.')
    if p.pitch <= 0:
        errors.append('Thread pitch must be greater than 0.')
    if p.tool_diameter <= 0:
        errors.append('Cutter diameter must be greater than 0.')
    if p.tool_number < 1:
        errors.append('Cutter tool number is required (T0 is invalid).')
    if p.teeth < 1:
        errors.append('Cutter must have at least 1 tooth profile.')
    if p.tooth_offset < 0:
        errors.append('Tip-to-profile offset cannot be negative.')
    if p.spindle_rpm <= 0:
        errors.append('Spindle RPM must be greater than 0.')
    if p.feed_edge <= 0:
        errors.append('Feedrate must be greater than 0.')
    if not (0 < p.lead_feed <= 1.0 + 1e-9):
        errors.append('Lead feed must be between 1% and 100%.')
    if p.radial_passes < 1:
        errors.append('At least 1 radial pass is required.')
    if p.z_top <= p.z_bottom:
        errors.append('Z TOP must be greater than thread bottom Z.')
    if p.z_top - p.z_bottom < p.pitch / 2.0:
        errors.append('Thread length must be at least half a pitch.')
    if p.retract < 0:
        errors.append('Approach (R-plane) cannot be negative.')
    if p.clearance < p.z_top + p.retract:
        errors.append('Safety Z must be >= Z TOP + approach.')
    if not p.holes:
        errors.append('At least one X/Y position is required.')
    errors += _validate_drill(p)
    if errors:
        return errors

    if p.internal:
        start_d = p.start_diameter if p.start_diameter else p.minor_internal()
        final_d = p.diameter + p.diameter_adjust
        if p.tool_diameter >= start_d:
            errors.append('Cutter ({}) does not fit inside starting hole ({}).'.format(
                _fmt(p.tool_diameter), _fmt(start_d)))
        if final_d <= start_d:
            errors.append('Final thread diameter must be larger than starting hole.')
        if (final_d - p.tool_diameter) / 2.0 <= 0:
            errors.append('Cutter is too large for this thread.')
        if p.chamfer:
            if p.tooth_offset <= 0:
                errors.append('Chamfer: tip-to-profile offset must be > 0 (cut with lower flank of profile).')
            elif p.chamfer_diameter <= start_d + 1e-6:
                errors.append('Chamfer: chamfer diameter ({}) must exceed hole diameter ({}).'.format(
                    _fmt(p.chamfer_diameter), _fmt(start_d)))
            else:
                cp = chamfer_plan(p, start_d, p.tool_diameter / 2.0)
                if cp['depth'] >= p.z_top - p.z_bottom:
                    errors.append('Chamfer: chamfer depth ({}) exceeds thread length.'.format(_fmt(cp['depth'])))
                elif len(cp['passes']) > 30:
                    errors.append('Chamfer: needs {} passes; increase tip-to-profile or reduce chamfer diameter.'.format(
                        len(cp['passes'])))
    else:
        final_d = p.minor_external() + p.diameter_adjust
        start_d = p.start_diameter if p.start_diameter else p.diameter
        if final_d <= 0:
            errors.append('Resulting external root diameter is non-positive.')
        if final_d >= start_d:
            errors.append('Final root diameter must be smaller than cylinder diameter.')

    if not errors and p.tool_reach > 0:
        k = plan(p)
        reach_needed = p.z_top - (min(k['z_entry'], k['z_exit']) - p.tooth_offset)
        if reach_needed > p.tool_reach + 1e-9:
            errors.append('Insufficient cutter reach: needs {} below Z TOP but reach is {}.'.format(
                _fmt(reach_needed), _fmt(p.tool_reach)))
    return errors


# ---------------------------------------------------------------- warnings & labels
def hole_warning(p):
    """Non-blocking warning if starting hole / cylinder looks undersized or oversized."""
    if p.diameter <= 0 or p.pitch <= 0:
        return None
    ideal = p.depth()
    u = 'mm' if p.units == 'MM' else 'in'
    tol = 0.05 if p.units == 'MM' else 0.002
    if p.internal:
        start_d = p.start_diameter if p.start_diameter else p.minor_internal()
        radial = (p.diameter + p.diameter_adjust - start_d) / 2.0
        if radial > ideal * 1.3 + tol:
            return 'Small hole: radial DOC is {} {} (nominal {}). Recommended drill D{}.'.format(
                _fmt(radial), u, _fmt(ideal), _fmt(tap_drill(p)))
        if 0 < radial < ideal * 0.5:
            return 'Large hole: only {} {} radial thread depth remains (nominal {}).'.format(
                _fmt(radial), u, _fmt(ideal))
    else:
        start_d = p.start_diameter if p.start_diameter else p.diameter
        radial = (start_d - (p.minor_external() + p.diameter_adjust)) / 2.0
        if radial > ideal * 1.3 + tol:
            return 'Oversized cylinder: radial DOC is {} {} (nominal {}).'.format(_fmt(radial), u, _fmt(ideal))
        if 0 < radial < ideal * 0.6:
            return 'Undersized cylinder: only {} {} radial thread depth remains (nominal {}).'.format(
                _fmt(radial), u, _fmt(ideal))
    return None


def thread_label(p):
    if p.units == 'MM':
        return 'M{:g} X {:g}'.format(round(p.diameter, 4), round(p.pitch, 4))
    tpi = 1.0 / p.pitch if p.pitch > 0 else 0.0
    return '{:g}-{:g} UN'.format(round(p.diameter, 4), round(tpi, 2))


# ---------- recommended single-tooth thread mills (BB BK2055 / BF2055 catalog, mm) ----------
BB_SINGLE_TOOTH = [
    ('M1X0.25', (1.0, 0.25), [(1.4, 0.25)], 0.25, 0.25, 4, 0.7, 3.1, 50, 3),
    ('M1.2X0.25', (1.2, 0.25), [(1.4, 0.25)], 0.25, 0.25, 4, 0.9, 3.8, 50, 3),
    ('M1.4X0.3', (1.4, 0.3), [], 0.3, 0.3, 4, 1.05, 4.4, 50, 3),
    ('M1.6X0.35', (1.6, 0.35), [(1.7, 0.35), (1.8, 0.35)], 0.35, 0.35, 4, 1.2, 5.0, 50, 3),
    ('M2X0.4', (2.0, 0.4), [], 0.4, 0.4, 4, 1.5, 6.2, 50, 3),
    ('M2.5X0.45', (2.5, 0.45), [], 0.45, 0.45, 4, 1.95, 7.7, 50, 3),
    ('M3X0.5', (3.0, 0.5), [(4.0, 0.5), (5.0, 0.5)], 0.5, 0.5, 4, 2.4, 9.2, 50, 3),
    ('M3.5X0.6', (3.5, 0.6), [], 0.6, 0.6, 4, 2.75, 12.0, 50, 3),
    ('M4X0.7', (4.0, 0.7), [], 0.7, 0.7, 4, 3.15, 13.0, 50, 3),
    ('M5X0.8', (5.0, 0.8), [(5.0, 0.5), (5.0, 0.75)], 0.5, 0.8, 4, 3.9, 16.0, 50, 3),
    ('M6X1', (6.0, 1.0), [(6.0, 0.5), (6.0, 0.75)], 0.5, 1.0, 6, 4.85, 20.0, 57, 4),
    ('M8X1.25', (8.0, 1.25), [(7.0, 0.5), (7.0, 0.75), (7.5, 1.0)], 0.5, 1.25, 6, 5.9, 25.0, 57, 4),
    ('M11X1', None, [(10.5, 0.5), (11.0, 0.75), (11.0, 1.0)], 0.5, 1.0, 10, 9.9, 35.0, 70, 5),
    ('M10X1.5', (10.0, 1.5), [(10.0, 1.0), (10.0, 1.25)], 1.0, 1.5, 8, 7.9, 32.0, 65, 5),
    ('M12X1.75', (12.0, 1.75), [(12.0, 1.0), (12.0, 1.25), (12.0, 1.5)], 1.0, 1.75, 10, 9.9, 38.0, 70, 5),
    ('M14X1.5', None, [(13.5, 1.0), (14.0, 1.25), (14.0, 1.5)], 1.0, 1.75, 12, 11.9, 45.0, 75, 5),
    ('M14X2', (14.0, 2.0), [], 1.25, 2.0, 12, 11.6, 45.0, 80, 5),
    ('M16X2', (16.0, 2.0), [], 1.25, 2.0, 12, 11.6, 45.0, 80, 5),
    ('M18X2.5', (18.0, 2.5), [], 1.5, 2.5, 14, 13.9, 50.0, 90, 6),
    ('M20X2.5', (20.0, 2.5), [], 1.5, 2.5, 14, 13.9, 55.0, 90, 6),
    ('M24X3', (24.0, 3.0), [], 2.0, 3.0, 16, 15.9, 65.0, 100, 6),
]

CHAMFER_TAN = math.tan(math.radians(30.0))
CHAMFER_STEP = 0.8
CHAMFER_EDGE = {'MM': 0.05, 'IN': 0.002}
CHAMFER_EXTRA = {'MM': 0.5, 'IN': 0.02}


def tap_drill_spec(p):
    """Return (drill_name_or_None, drill_dia) for internal thread p."""
    if p.units == 'MM':
        for (d, pt), dr in METRIC_TAP_DRILLS.items():
            if abs(p.diameter - d) < 1e-6 and abs(p.pitch - pt) < 1e-6:
                return None, dr
    elif p.pitch > 0:
        tpi = 1.0 / p.pitch
        for (d, t), (name, dr) in UNIFIED_TAP_DRILLS.items():
            if abs(p.diameter - d) < 1e-6 and abs(tpi - t) < 1e-6:
                return name, dr
    return None, p.diameter - p.pitch


def tap_drill(p):
    return tap_drill_spec(p)[1]


def mill_max_diameter(p, start_d):
    if not p.internal:
        return None
    return start_d


def bb_thread_mill(p):
    """Recommended BB single-tooth cutter dict for p, or None."""
    k = 1.0 if p.units == 'MM' else 25.4
    d_mm, p_mm = p.diameter * k, p.pitch * k
    limit = tap_drill(p) * k - 1e-6 if p.internal else float('inf')

    def same(a, b):
        return abs(a[0] - b[0]) < 1e-3 and abs(a[1] - b[1]) < 1e-3

    def best(rows):
        rows = [r for r in rows if r[6] < limit]
        return max(rows, key=lambda r: r[6]) if rows else None

    row = best([r for r in BB_SINGLE_TOOTH
                if (r[1] and same(r[1], (d_mm, p_mm))) or any(same(f, (d_mm, p_mm)) for f in r[2])])
    if row is None:
        row = best([r for r in BB_SINGLE_TOOTH if r[3] - 1e-6 <= p_mm <= r[4] + 1e-6])
    if row is None:
        return None
    return dict(name=row[0], dc=row[6] / k, reach=row[7] / k, flutes=row[9], shank=row[5] / k,
                length=row[8] / k)


def chamfer_plan(p, start_d, tr):
    """Flat circular passes to cut a 120-deg mouth chamfer using the lower flank of the lowest tooth."""
    t = CHAMFER_TAN
    r_b, r_s = start_d / 2.0, p.chamfer_diameter / 2.0
    out = dict(depth=(r_s - r_b) * t, r_b=r_b, r_s=r_s, passes=[])
    flank = p.tooth_offset / t
    if flank <= 0 or r_s <= r_b:
        return out
    r_end = r_s + CHAMFER_EDGE[p.units]
    n = max(1, int(math.ceil((r_end - r_b) / (CHAMFER_STEP * flank) - 1e-9)))
    for i in range(1, n + 1):
        r_c = r_b + (r_end - r_b) * i / n
        z_crest = p.z_top - (r_s - r_c) * t
        out['passes'].append(dict(rc=r_c - tr, crest=r_c, z_tip=z_crest - p.tooth_offset))
    return out


def tool_messages(p):
    """ASCII-only strings for (MSG, ...) prompts before tool changes."""
    u = 'MM' if p.units == 'MM' else 'IN'
    n = _short
    msgs = {}
    if p.drill_enabled and p.internal:
        name, dia = tap_drill_spec(p)
        named = ' {}'.format(name) if name and abs(p.drill_diameter - dia) < 1e-6 else ''
        msgs['drill'] = 'T{} DRILL{} D{} {}'.format(p.drill_tool_number, named, n(p.drill_diameter), u)
    if p.units == 'MM':
        pitch = 'PITCH {} MM'.format(n(p.pitch))
    else:
        pitch = '{:g} TPI'.format(round(1.0 / p.pitch, 2) if p.pitch > 0 else 0.0)
    msgs['mill'] = 'T{} THREAD MILL D{} {} {}{}'.format(
        p.tool_number, n(p.tool_diameter), u, pitch, ' {} TEETH'.format(p.teeth) if p.teeth > 1 else '')
    return msgs


# ---------------------------------------------------------------- toolpath
def lead_arc(p, k, rp):
    """Tangential lead arc for path radius rp (relative to thread axis): geometry, Z rise and feed."""
    tr = k['tr']
    if p.internal:
        re = rp / 2.0
        ce = (rp - re, 0.0)
        kd = k['s']
        sweep = math.pi
        phi = math.pi / 2.0                     # polar angle swept around the thread axis
    else:
        re = (k['start_d'] - k['final_d']) / 2.0 + k['clear_gap'] + tr
        ce = (rp + re, 0.0)
        kd = -k['s']
        sweep = math.pi / 2.0
        phi = math.atan2(re, rp + re)
    v = (rp - ce[0], -ce[1])
    st = _rot(v, -kd * sweep)
    en = _rot(v, kd * sweep)
    # contact point sits on the far side of the cutter from the arc center: edge path radius = re + tr
    if p.feed_compensate:
        feed = p.feed_edge * p.lead_feed * re / (re + tr)
    else:
        feed = k['feed'] * p.lead_feed
    return dict(re=re, ce=ce, k=kd, sweep=sweep, st=(ce[0] + st[0], ce[1] + st[1]),
                en=(ce[0] + en[0], ce[1] + en[1]), dz=p.pitch * phi / (2 * math.pi), feed=feed)


def _plan_z(p, dirz, q):
    """Return (z_entry, z_helix_start, turns, z_helix_end, z_exit) for the lowest tooth.
    q = largest lead-arc Z rise, so lead-in / lead-out never take the tooth below z_bottom."""
    span_teeth = (p.teeth - 1) * p.pitch
    z_lo = p.z_bottom + q
    needed = (p.z_top - span_teeth) - z_lo
    turns = max(1, int(math.ceil(needed / p.pitch - 1e-9)))
    z_hi = z_lo + turns * p.pitch
    if dirz > 0:
        return z_lo - q, z_lo, turns, z_hi, z_hi + q
    return z_hi + q, z_hi, turns, z_lo, z_lo - q


def plan(p):
    """Compute toolpath parameters for both G-code generation and sketch rendering."""
    tr = p.tool_diameter / 2.0
    clear_gap = 0.5 if p.units == 'MM' else 0.02

    if p.internal:
        s = 1 if p.climb else -1
    else:
        s = -1 if p.climb else 1
    dirz = s * (1 if p.right_hand else -1)          # +1 up, -1 down
    helix_g = 'G3' if s > 0 else 'G2'

    if p.internal:
        start_d = p.start_diameter if p.start_diameter else p.minor_internal()
        final_d = p.diameter + p.diameter_adjust
        r_first = start_d / 2.0 - tr
        r_final = final_d / 2.0 - tr
        r_edge = final_d / 2.0
    else:
        start_d = p.start_diameter if p.start_diameter else p.diameter
        final_d = p.minor_external() + p.diameter_adjust
        r_first = start_d / 2.0 + tr
        r_final = final_d / 2.0 + tr
        r_edge = final_d / 2.0

    n = int(p.radial_passes)
    if p.pass_mode == 'EQ':
        frac = [float(i) / n for i in range(1, n + 1)]
    else:
        frac = [math.sqrt(float(i) / n) for i in range(1, n + 1)]
    radii = [r_first + (r_final - r_first) * f for f in frac]
    prev = r_first
    pass_depths = []
    for r_ in radii:
        pass_depths.append(abs(r_ - prev))
        prev = r_
    if p.spring_pass:
        radii.append(r_final)

    feed = p.feed_edge
    if p.feed_compensate and r_edge > 0:
        feed = p.feed_edge * r_final / r_edge

    chamfer = chamfer_plan(p, start_d, tr) if (p.chamfer and p.internal) else None
    k = dict(chamfer=chamfer, pass_depths=pass_depths, tr=tr, clear_gap=clear_gap, s=s, dirz=dirz,
             helix_g=helix_g, start_d=start_d, final_d=final_d, r_first=r_first, r_final=r_final,
             r_edge=r_edge, n=n, radii=radii, feed=feed)
    k['leads'] = [lead_arc(p, k, r_) for r_ in radii]
    z_entry, z_h0, turns, z_h1, z_exit = _plan_z(p, dirz, max(ld['dz'] for ld in k['leads']))
    k.update(z_entry=z_entry, z_h0=z_h0, turns=turns, z_h1=z_h1, z_exit=z_exit)
    return k


def _chamfer_lead_feed(p, rc, crest, f_circle):
    """Center feed for chamfer lead arcs (radius rc/2), referred to the crest at the contact point."""
    if not p.feed_compensate:
        return f_circle * p.lead_feed
    r_arc = rc / 2.0
    return p.feed_edge * p.lead_feed * r_arc / (r_arc + crest - rc)


# ---------------------------------------------------------------- program
def generate(p):
    """Generate a fully commented LinuxCNC .ngc program. Raises ThreadMillError if invalid."""
    errors = validate(p)
    if errors:
        raise ThreadMillError('\n'.join(errors))

    k_ = plan(p)
    s, dirz, helix_g = k_['s'], k_['dirz'], k_['helix_g']
    start_d, final_d, radii, n, feed = k_['start_d'], k_['final_d'], k_['radii'], k_['n'], k_['feed']
    z_h0, turns = k_['z_h0'], k_['turns']
    toff = p.tooth_offset
    z_approach = p.z_top + p.retract
    msgs = tool_messages(p)
    drilling = p.drill_enabled and p.internal
    u = 'mm' if p.units == 'MM' else 'in'
    hand = 'RIGHT HAND' if p.right_hand else 'LEFT HAND'
    kind = 'INTERNAL' if p.internal else 'EXTERNAL'
    climb = 'climb' if p.climb else 'conventional'
    coolant = {'MIST': ('M7', 'mist coolant on'), 'FLOOD': ('M8', 'flood coolant on')}.get(p.coolant)
    nh = len(p.holes)
    bar = '(' + '-' * 70 + ')'

    g = []
    g.append('(' + '=' * 70 + ')')
    name = p.name.replace('(', '').replace(')', '')
    g.append('({} {} THREAD MILLING{})'.format(kind, hand, (' - ' + name) if name else ''))
    g.append('(Generated by PB_Conversational for Probe Basic)')
    g.append('(' + '=' * 70 + ')')
    g.append('(THREAD {}: nominal D{} {}, pitch {} {}, {})'.format(
        thread_label(p), _short(p.diameter), u, _short(p.pitch), u, hand.lower()))
    if p.internal:
        g.append('(  starting hole D{}, final thread D{})'.format(_short(start_d), _short(final_d)))
    else:
        g.append('(  starting cylinder D{}, final root D{})'.format(_short(start_d), _short(final_d)))
    g.append('(  NOTE: calculated for cutter D{}: crest reaches D{}.)'.format(
        _short(p.tool_diameter), _short(final_d)))
    if p.diameter_adjust:
        g.append('(  includes diameter offset adjustment of {} {})'.format(_short(p.diameter_adjust), u))
    cp = k_['chamfer']
    if cp:
        g.append('(  120 deg mouth chamfer to D{}, {} {} deep, with same cutter)'.format(
            _short(p.chamfer_diameter), _short(cp['depth']), u))
    g.append('(POSITIONS: {} in {}{})'.format(nh, p.wcs, '' if p.internal else ', cylinder center'))
    for i, (hx, hy) in enumerate(p.holes):
        g.append('(  {}: X{} Y{})'.format(i + 1, _short(hx), _short(hy)))
    g.append('(HEIGHTS:)')
    g.append('(  Z{} safety clearance: rapid XY moves and after tool change)'.format(_short(p.clearance)))
    g.append('(  Z{} approach R-plane: rapid Z descent target)'.format(_short(z_approach)))
    g.append('(  Z{} part top face, thread start)'.format(_short(p.z_top)))
    g.append('(  Z{} thread bottom: lowest profile tooth stays at or above here)'.format(_short(p.z_bottom)))
    g.append('(TOOLS: tool Z = tip)')
    if drilling:
        g.append('(  T{} drill D{} {}, {} deg point)'.format(
            p.drill_tool_number, _short(p.drill_diameter), u, _short(p.drill_point_angle)))
    g.append('(  T{} thread mill D{} {}, {} flutes, {} {}, tip-to-tooth {} {})'.format(
        p.tool_number, _short(p.tool_diameter), u, p.flutes, p.teeth, 'teeth' if p.teeth > 1 else 'tooth',
        _short(toff), u))
    g.append('(STARTUP MODES:)')
    g.append('(  G90 absolute, G94 feed in {}/min, G17 XY plane)'.format(u))
    g.append('(  G40 cutter comp off, G49 length comp off, G80 canned cycle off)')
    g.append('(  G91.1 incremental arc centers I J)')
    g.append('G90 G94 G17 G40 G49 G80 G91.1')
    g.append(_c('G21', 'units: millimeters') if p.units == 'MM' else _c('G20', 'units: inches'))

    # ---------------------------------------------------------------- drilling
    if drilling:
        tip = drill_tip_z(p)
        r_plane = p.z_top + p.retract
        pecks = drill_pecks(p)
        g.append(bar)
        g.append('(1. PECK DRILLING: {} pecks per hole)'.format(len(pecks)))
        g.append(bar)
        g.append('(Full retract to R-plane after each peck for chip evacuation; rapid reentry)')
        g.append('(stops {} {} above previous bottom. Cylindrical depth Z{}, tip Z{}.)'.format(
            _short(p.drill_reentry), u, _short(p.z_top - p.drill_depth), _short(tip)))
        g.append('(MSG, {})'.format(msgs['drill']))
        g.append(_c('T{:d} M6'.format(p.drill_tool_number), 'tool change: drill'))
        g.append(_c('G43 H{:d}'.format(p.drill_tool_number), 'enable tool length offset for T{}'.format(
            p.drill_tool_number)))
        g.append(_c(p.wcs, 'work coordinate system'))
        g.append(_c('G0 Z{}'.format(_fmt(p.clearance)), 'move to safety clearance height'))
        g.append(_c('S{} M3'.format(_fmt(p.drill_rpm)), 'spindle CW at {} rpm'.format(_short(p.drill_rpm))))
        if coolant:
            g.append(_c(*coolant))
        g.append(_c('F{}'.format(_fmt(p.drill_feed)), 'drill feedrate: {} {}/min'.format(
            _short(p.drill_feed), u)))
        for h, (hx, hy) in enumerate(p.holes):
            g.append('(hole {} of {}: X{} Y{})'.format(h + 1, nh, _short(hx), _short(hy)))
            g.append(_c('G0 X{} Y{}'.format(_fmt(hx), _fmt(hy)), 'rapid over hole center'))
            g.append(_c('G0 Z{}'.format(_fmt(r_plane)), 'rapid to R-plane'))
            prev = None
            top = p.z_top
            for j, zp in enumerate(pecks):
                if prev is not None:
                    g.append(_c('G0 Z{}'.format(_fmt(prev + p.drill_reentry)),
                                'rapid reentry {} above previous bottom'.format(_short(p.drill_reentry))))
                g.append(_c('G1 Z{}'.format(_fmt(zp)), 'peck {} of {}: cut {} {}'.format(
                    j + 1, len(pecks), _short(top - zp), u)))
                if p.drill_dwell > 0 and zp == pecks[-1]:
                    g.append(_c('G4 P{}'.format(_fmt(p.drill_dwell)), 'dwell {} s at bottom'.format(
                        _short(p.drill_dwell))))
                g.append(_c('G0 Z{}'.format(_fmt(r_plane)), 'retract to R-plane to clear chips'))
                prev = top = zp
            g.append(_c('G0 Z{}'.format(_fmt(p.clearance)), 'retract to safety clearance before XY move'))
        g.append(_c('M9', 'coolant off'))
        g.append(_c('M5', 'spindle stop'))

    # ---------------------------------------------------------------- milling
    g.append(bar)
    g.append('({}THREAD MILLING: {} pass{}{} per {})'.format(
        '2. ' if drilling else '', n, 'es' if n > 1 else '', ' + spring pass' if p.spring_pass else '',
        'hole' if p.internal else 'cylinder'))
    g.append(bar)
    g.append('({} = {} from above, {}: {} thread, {} milling with M3.)'.format(
        helix_g, 'CCW' if s > 0 else 'CW', 'up' if dirz > 0 else 'down', hand.lower(), climb))
    g.append('(Radial DOC: {} {}{}.)'.format(
        ' + '.join(_short(x) for x in k_['pass_depths']), u,
        '' if n == 1 else ', equal' if p.pass_mode == 'EQ' else ', decreasing'))
    g.append('(Tool-center feedrate: F_edge {} x center R {} / edge R {} = F{}.)'.format(
        _short(p.feed_edge), _short(k_['r_final']), _short(k_['r_edge']), _short(feed)))
    lf = sorted(set(round(ld['feed'], 4) for ld in k_['leads']))
    g.append('(Lead-in/out feedrate: {}% of edge feed at contact point = F{}.)'.format(
        _short(p.lead_feed * 100), _short(lf[0]) if len(lf) == 1 else '{} to F{}'.format(
            _short(lf[0]), _short(lf[-1]))))
    g.append('(MSG, {})'.format(msgs['mill']))
    g.append(_c('T{:d} M6'.format(p.tool_number), 'tool change: thread mill'))
    g.append(_c('G43 H{:d}'.format(p.tool_number), 'enable tool length offset for T{}'.format(p.tool_number)))
    g.append(_c(p.wcs, 'work coordinate system'))
    g.append(_c('G0 Z{}'.format(_fmt(p.clearance)), 'move to safety clearance height'))
    g.append(_c('S{} M3'.format(_fmt(p.spindle_rpm)), 'spindle CW at {} rpm'.format(_short(p.spindle_rpm))))
    if coolant:
        g.append(_c(*coolant))
    g.append(_c('F{}'.format(_fmt(feed)), 'tool-center feedrate: {} {}/min'.format(_short(feed), u)))

    for h, (hx, hy) in enumerate(p.holes):
        if p.internal:
            g.append('(hole {} of {}: X{} Y{})'.format(h + 1, nh, _short(hx), _short(hy)))
        else:
            g.append('(cylinder {} of {}: center X{} Y{})'.format(h + 1, nh, _short(hx), _short(hy)))
        for i, rp in enumerate(radii):
            if p.spring_pass and i == len(radii) - 1:
                g.append('(spring pass: repeat final path radius {})'.format(_short(rp)))
            else:
                g.append('(pass {} of {}: path radius {}, DOC {} {})'.format(
                    i + 1, n, _short(rp), _short(k_['pass_depths'][i]), u))
            p0 = (rp, 0.0)
            ld = k_['leads'][i]
            ce, st, en = ld['ce'], ld['st'], ld['en']
            arc_g = 'G3' if ld['k'] > 0 else 'G2'
            z_in = z_h0 - dirz * ld['dz']
            z_out = z_h0 + dirz * (turns * p.pitch + ld['dz'])
            rise = '1/4 pitch' if p.internal else '{} {} along thread lead'.format(_short(ld['dz']), u)
            updown = 'up' if dirz > 0 else 'down'

            sx, sy = hx + st[0], hy + st[1]
            if p.internal:
                if i == 0:
                    g.append(_c('G0 X{} Y{}'.format(_fmt(hx), _fmt(hy)), 'rapid to hole center'))
                    g.append(_c('G0 Z{}'.format(_fmt(z_approach)), 'rapid to R-plane'))
                g.append(_c('G1 Z{}'.format(_fmt(z_in - toff)),
                            'feed down hole center to helix entry start'))
            else:
                g.append(_c('G0 X{} Y{}'.format(_fmt(sx), _fmt(sy)), 'rapid to entry point outside cylinder'))
                g.append(_c('G0 Z{}'.format(_fmt(z_approach)), 'rapid to R-plane'))
                g.append(_c('G1 Z{}'.format(_fmt(z_in - toff)), 'feed down outside cylinder to entry start'))
            g.append(_c('F{}'.format(_fmt(ld['feed'])), 'lead-in feedrate'))

            g.append(_c('{} X{} Y{} Z{} I{} J{}'.format(
                arc_g, _fmt(hx + p0[0]), _fmt(hy + p0[1]), _fmt(z_h0 - toff),
                _fmt(ce[0] - st[0]), _fmt(ce[1] - st[1])),
                'tangential lead-in: reach wall and step {} {}'.format(updown, rise)))
            g.append(_c('F{}'.format(_fmt(feed)), 'restore thread milling feedrate'))
            z = z_h0
            for t in range(turns):
                z += dirz * p.pitch
                g.append(_c('{} X{} Y{} Z{} I{} J{}'.format(
                    helix_g, _fmt(hx + p0[0]), _fmt(hy + p0[1]), _fmt(z - toff),
                    _fmt(-p0[0]), _fmt(-p0[1])), 'turn {} of {}: {} 1 pitch'.format(t + 1, turns, updown)))
            g.append(_c('F{}'.format(_fmt(ld['feed'])), 'lead-out feedrate'))
            g.append(_c('{} X{} Y{} Z{} I{} J{}'.format(
                arc_g, _fmt(hx + en[0]), _fmt(hy + en[1]), _fmt(z_out - toff),
                _fmt(ce[0] - p0[0]), _fmt(ce[1] - p0[1])),
                'tangential lead-out to hole center' if p.internal else 'tangential lead-out clear of cylinder'))
            g.append(_c('F{}'.format(_fmt(feed)), 'restore thread milling feedrate'))
            if not p.internal:
                g.append(_c('G0 Z{}'.format(_fmt(z_approach)), 'retract to R-plane'))
        if cp:
            arc_g = 'G3' if s > 0 else 'G2'
            nc = len(cp['passes'])
            g.append('(120 deg chamfer: D{} to D{}, {} pass{})'.format(
                _short(2 * cp['r_b']), _short(p.chamfer_diameter), nc, 'es' if nc > 1 else ''))
            for j, c in enumerate(cp['passes']):
                rc = c['rc']
                g.append(_c('G1 Z{}'.format(_fmt(c['z_tip'])),
                            'chamfer {} of {}: move to Z height at center'.format(j + 1, nc)))
                f_ch = p.feed_edge * rc / c['crest']
                f_ld = _chamfer_lead_feed(p, rc, c['crest'], f_ch)
                g.append(_c('F{}'.format(_fmt(f_ld)), 'chamfer lead-in feedrate'))
                g.append(_c('{} X{} Y{} I{} J{}'.format(arc_g, _fmt(hx + rc), _fmt(hy), _fmt(rc / 2.0), _fmt(0.0)),
                            'tangential lead-in: crest reaches D{}'.format(_short(2 * c['crest']))))
                g.append(_c('F{}'.format(_fmt(f_ch)), 'center feedrate for chamfer pass'))
                g.append(_c('{} X{} Y{} I{} J{}'.format(arc_g, _fmt(hx + rc), _fmt(hy), _fmt(-rc), _fmt(0.0)),
                            'full circle: flank cuts chamfer'))
                g.append(_c('F{}'.format(_fmt(f_ld)), 'chamfer lead-out feedrate'))
                g.append(_c('{} X{} Y{} I{} J{}'.format(arc_g, _fmt(hx), _fmt(hy), _fmt(-rc / 2.0), _fmt(0.0)),
                            'tangential lead-out to center'))
            g.append(_c('F{}'.format(_fmt(feed)), 'restore thread milling feedrate'))
        g.append(_c('G0 Z{}'.format(_fmt(p.clearance)), 'retract to safety clearance before XY move'))

    g.append(bar)
    g.append('(END)')
    g.append(bar)
    g.append(_c('M9', 'coolant off'))
    g.append(_c('M5', 'spindle stop'))
    if p.home_z_at_end:
        g.append(_c('G53 G0 Z0', 'retract Z to machine home'))
    g.append(_c('M30', 'program end'))
    return g


def parse_holes(text):
    """Parse '10,20; 30,40' or one X,Y pair per line -> [(10.0, 20.0), (30.0, 40.0)]."""
    holes = []
    for chunk in text.replace('\n', ';').split(';'):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.replace(' ', ',').split(',')
        parts = [x for x in parts if x != '']
        if len(parts) != 2:
            raise ThreadMillError('Invalid position: "{}" (expected X,Y)'.format(chunk))
        holes.append((float(parts[0]), float(parts[1])))
    return holes

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Raúl Milla (DominicZe)
"""
Pure-Python G-code generator for rectangular and circular pockets in LinuxCNC.

Zero Qt or LinuxCNC dependencies. Used by PocketPage (pocket_page.py) and the schematic
sketches (pocket_sketch.py). Reuses formatting helpers from thread_mill_gen.py.

Strategy (built for small/retrofit mills with possible leadscrew backlash):
  - Inside-out roughing in a single cutting direction (all climb or all conventional).
    No zig-zag reversals that eat backlash on every pass and leave poorer walls/floors.
  - Each rectangular roughing loop is joined to the next one by a tangent S-curve (two quarter
    arcs) instead of a straight radial step, so the cutter loads up gradually and never turns
    a sharp corner into fresh material. Circular pockets already use a tangent half-turn spiral.
  - Stepover capped at 70% of tool diameter (above ~70%, consecutive rectangular loops
    leave uncut corner islands since stepover * sqrt(2) > tool diameter).
  - Helical entry at pocket center by default (falls back to center ramp on narrow slots,
    or plunge if selected). Entry and the central full-width slot use reduced entry feedrate.
  - Equal Z roughing levels up to step_down, with optional wall/floor finishing stock.
  - Tangential arc lead-in / lead-out on full-depth wall finishing (+ optional spring pass).
    Lead arcs run at entry feed referred to the cutting edge at the contact point: on a lead arc
    of radius rho the edge travels on rho + tool radius, so F_lead = F * entry% * rho / (rho + rt).
    Without this the edge would hit the wall at 2x the finishing feed.
"""

import math

from thread_mill_gen import _fmt, _short, _c, _rot

HELIX_FRAC = 0.25          # helix radius = 25% of cutter dia (1.5x cutter dia bore)
HELIX_MIN = 0.10           # minimum helix radius = 10% of cutter dia
RAMP_MIN = 0.25            # minimum half-ramp length = 25% of cutter dia
STEPOVER_MAX = 0.70        # max stepover fraction before corner material is left behind
EPS = 1e-9


class PocketError(ValueError):
    """Validation or geometry error shown to the user."""
    pass


class PocketParams(object):
    """Pocket operation inputs in active program units (mm or in)."""

    def __init__(self):
        self.units = 'MM'
        self.circular = False
        self.length = 40.0
        self.width = 25.0
        self.corner_radius = 5.0
        self.angle = 0.0
        self.diameter = 30.0
        self.z_top = 0.0
        self.depth = 10.0
        self.step_down = 2.0
        self.stepover = 0.40
        self.climb = True
        self.entry = 'HELIX'
        self.ramp_angle = 3.0
        self.entry_feed = 0.5
        self.wall_stock = 0.2
        self.floor_stock = 0.0
        self.spring_pass = False
        self.tool_number = 1
        self.tool_diameter = 8.0
        self.flutes = 3
        self.spindle_rpm = 3000.0
        self.feed = 600.0
        self.coolant = 'OFF'
        self.wcs = 'G54'
        self.retract = 2.0
        self.clearance = 10.0
        self.home_z_at_end = True
        self.centers = [(0.0, 0.0)]
        self.name = ''

    def z_bottom(self):
        return self.z_top - self.depth


def _shape_ok(p):
    if p.circular:
        return p.diameter > 0
    return p.length > 0 and p.width > 0


def validate(p):
    """Return a list of blocking error messages (empty list if valid)."""
    e = []
    u = 'mm' if p.units == 'MM' else 'in'
    rt = p.tool_diameter / 2.0
    if p.tool_diameter <= 0:
        e.append('Cutter diameter must be greater than 0.')
    if p.tool_number < 1:
        e.append('Cutter tool number is required (T0 is invalid).')
    if p.flutes < 1:
        e.append('Cutter must have at least 1 flute.')
    if p.spindle_rpm <= 0:
        e.append('Spindle RPM must be greater than 0.')
    if p.feed <= 0:
        e.append('Feedrate must be greater than 0 (check FZ, flutes, and RPM).')
    if p.circular:
        if p.diameter <= 0:
            e.append('Pocket diameter must be greater than 0.')
    else:
        if p.length <= 0 or p.width <= 0:
            e.append('Pocket length and width must be greater than 0.')
        elif p.corner_radius < 0 or p.corner_radius > min(p.length, p.width) / 2.0 + EPS:
            e.append('Corner radius must be between 0 and half the short side ({} {}).'.format(
                _short(min(p.length, p.width) / 2.0), u))
    if p.depth <= 0:
        e.append('Depth must be greater than 0.')
    if p.step_down <= 0:
        e.append('Step Z must be greater than 0.')
    if p.wall_stock < 0 or p.floor_stock < 0:
        e.append('Finishing stock cannot be negative.')
    if p.depth > 0 and p.floor_stock >= p.depth:
        e.append('Floor stock must be less than total depth.')
    if p.tool_diameter > 0 and p.wall_stock >= rt:
        e.append('Wall stock must be less than cutter radius ({} {}).'.format(_short(rt), u))
    if not (0.05 - EPS <= p.stepover <= STEPOVER_MAX + EPS):
        e.append('Stepover must be between 5% and {}% of cutter diameter.'.format(int(STEPOVER_MAX * 100)))
    if not (0 < p.ramp_angle <= 30):
        e.append('Ramp angle must be between 0 and 30 deg.')
    if not (0 < p.entry_feed <= 1.0 + EPS):
        e.append('Entry feed must be between 1% and 100%.')
    if p.retract < 0:
        e.append('Approach (R-plane) cannot be negative.')
    if p.clearance < p.z_top + p.retract - EPS:
        e.append('Safety Z must be >= Z TOP + approach.')
    if not p.centers:
        e.append('At least one pocket center position is required.')
    if p.entry not in ('HELIX', 'RAMP', 'PLUNGE'):
        e.append('Invalid entry mode.')
    if e or p.tool_diameter <= 0 or not _shape_ok(p):
        return e
    g = geometry(p)
    if p.circular:
        if g['r_rough'] <= EPS:
            e.append('Cutter (D{}) with wall stock does not fit inside D{} pocket.'.format(
                _short(p.tool_diameter), _short(p.diameter)))
    elif g['ex'] < -EPS or g['ey'] < -EPS or max(g['ex'], g['ey']) <= EPS:
        e.append('Cutter (D{}) with wall stock does not fit inside {} x {} pocket.'.format(
            _short(p.tool_diameter), _short(p.length), _short(p.width)))
    if e:
        return e
    if entry_plan(p, g)['kind'] is None:
        e.append('Pocket is too narrow for helical or ramp entry with this cutter: '
                 'select PLUNGE entry (requires pre-drilled hole or center-cutting endmill).')
    return e


def pocket_warning(p):
    """Non-blocking warning, or None."""
    if validate(p):
        return None
    rt = p.tool_diameter / 2.0
    w = []
    if not p.circular and p.corner_radius < rt - EPS:
        w.append('corner radius ({}) is smaller than cutter radius: actual corners will be R{}'.format(
            _short(p.corner_radius), _short(rt)))
    ent = entry_plan(p, geometry(p))
    if ent['note']:
        w.append(ent['note'])
    if p.entry == 'PLUNGE':
        w.append('plunge entry: requires a pre-drilled pilot hole or center-cutting endmill')
    if p.step_down > p.tool_diameter + EPS:
        w.append('step Z ({}) exceeds cutter diameter: heavy roughing cut'.format(_short(p.step_down)))
    return '; '.join(w) if w else None


def label(p):
    if p.circular:
        return 'POCKET D{}'.format(_short(p.diameter))
    return 'POCKET {} X {}{}'.format(_short(p.length), _short(p.width),
                                     ' R{}'.format(_short(p.corner_radius)) if p.corner_radius > 0 else '')


def geometry(p):
    """Pocket local coordinates (centered at 0,0; long side aligned along local +X)."""
    rt = p.tool_diameter / 2.0
    sw = p.wall_stock
    g = {'rt': rt, 'sw': sw}
    if p.circular:
        R = p.diameter / 2.0
        g.update(R=R, r_rough=R - rt - sw, r_fin=R - rt, ang=0.0)
        return g
    A, B, ang = p.length / 2.0, p.width / 2.0, math.radians(p.angle)
    if B > A:
        A, B, ang = B, A, ang + math.pi / 2.0
    ex, ey = A - rt - sw, B - rt - sw
    g.update(A=A, B=B, ang=ang, ex=ex, ey=ey, s_len=max(ex - ey, 0.0),
             rc_rough=max(p.corner_radius - rt - sw, 0.0), rc_fin=max(p.corner_radius - rt, 0.0))
    return g


def entry_plan(p, g):
    d = p.tool_diameter
    room = g['r_rough'] if p.circular else min(g['ex'], g['ey'])
    ramp = g['r_rough'] if p.circular else g['s_len']
    helix_ok = room >= HELIX_MIN * d - EPS
    ramp_ok = ramp >= RAMP_MIN * d - EPS
    r_h = min(HELIX_FRAC * d, room)
    if p.entry == 'PLUNGE':
        return {'kind': 'PLUNGE', 'r': 0.0, 's': 0.0, 'note': None}
    if p.entry == 'HELIX':
        if helix_ok:
            return {'kind': 'HELIX', 'r': r_h, 's': 0.0, 'note': None}
        if ramp_ok:
            return {'kind': 'RAMP', 'r': 0.0, 's': ramp, 'note': 'helix does not fit: using center ramp'}
        return {'kind': None, 'r': 0.0, 's': 0.0, 'note': None}
    if ramp_ok:
        return {'kind': 'RAMP', 'r': 0.0, 's': ramp, 'note': None}
    if helix_ok:
        return {'kind': 'HELIX', 'r': r_h, 's': 0.0, 'note': 'ramp does not fit: using helical entry'}
    return {'kind': None, 'r': 0.0, 's': 0.0, 'note': None}


def lead_feed(p, rho, rt):
    """Tool-center feed for a tangential lead arc of radius rho (edge speed at contact = entry feed)."""
    return p.feed * p.entry_feed * rho / (rho + rt) if rho > EPS else p.feed * p.entry_feed


def _even(total, step):
    return max(1, int(math.ceil(total / step - 1e-9)))


def _line(x, y, z, f, c):
    return {'g': 1, 'x': x, 'y': y, 'z': z, 'f': f, 'c': c}


def _arc(ccw, x, y, z, cx, cy, f, c):
    return {'g': 3 if ccw else 2, 'x': x, 'y': y, 'z': z, 'cx': cx, 'cy': cy, 'f': f, 'c': c}


def rect_loop(hx, hy, r, ccw, f, what, stop=0.0):
    """Closed rounded-rectangle toolpath starting at (hx, 0) and ending there,
    or 'stop' short of it on the same side to hand over to step_s()."""
    s = 1.0 if ccw else -1.0
    pts = [(hx, s * (hy - r), None), (hx - r, s * hy, (hx - r, s * (hy - r))),
           (-hx + r, s * hy, None), (-hx, s * (hy - r), (-hx + r, s * (hy - r))),
           (-hx, -s * (hy - r), None), (-hx + r, -s * hy, (-hx + r, -s * (hy - r))),
           (hx - r, -s * hy, None), (hx, -s * (hy - r), (hx - r, -s * (hy - r))),
           (hx, -s * stop, None)]
    m = []
    for x, y, ce in pts:
        if ce is None:
            m.append(_line(x, y, None, f, what))
        elif r > EPS:
            m.append(_arc(ccw, x, y, None, ce[0], ce[1], f, what + ', corner'))
    return m


def step_s(x0, x1, ccw, f, what):
    """Tangent S-curve (two quarter arcs) from (x0, -s*d) to (x1, 0), d = |x1 - x0|.
    Joins a rect_loop() stopped d short of its start to the next loop at x1, same heading."""
    s = 1.0 if ccw else -1.0
    ex = 1.0 if x1 > x0 else -1.0
    rho = abs(x1 - x0) / 2.0
    xm = x0 + ex * rho
    first_ccw = -s * ex > 0
    return [_arc(first_ccw, xm, -s * rho, None, xm, -s * 2 * rho, f, what + ': tangential step-over'),
            _arc(not first_ccw, x1, 0.0, None, xm, 0.0, f, what + ': tangential step-over')]


def _entry_moves(ent, z0, z1, ccw, tan_a, level):
    m = []
    dz = z0 - z1
    if ent['kind'] == 'HELIX':
        r = ent['r']
        n = _even(dz, 2 * math.pi * r * tan_a)
        for i in range(1, n + 1):
            m.append(_arc(ccw, r, 0.0, z0 - dz * i / n, 0.0, 0.0, 'entry',
                          'level {}: helical entry, turn {} of {}'.format(level, i, n)))
        m.append(_arc(ccw, r, 0.0, None, 0.0, 0.0, 'entry', 'flat cleanup turn at helix bottom'))
    elif ent['kind'] == 'RAMP':
        s = ent['s']
        n = _even(dz, 2 * s * tan_a)
        x = s
        for i in range(1, n + 1):
            x = -x
            m.append(_line(x, 0.0, z0 - dz * i / n, 'entry', 'level {}: ramp entry, pass {} of {}'.format(level, i, n)))
        m.append(_line(-x, 0.0, None, 'entry', 'flat cleanup pass along ramp floor'))
    else:
        m.append(_line(0.0, 0.0, z1, 'entry', 'level {}: plunge straight down'.format(level)))
    return m


def entry_point(ent):
    if ent['kind'] == 'HELIX':
        return (ent['r'], 0.0)
    if ent['kind'] == 'RAMP':
        return (ent['s'], 0.0)
    return (0.0, 0.0)


def _rect_clearing(p, g, ent, ccw, so):
    m = []
    s_len, ex, ey = g['s_len'], g['ex'], g['ey']
    mn = min(ex, ey)
    if s_len > EPS and ent['kind'] != 'RAMP':
        m.append(_line(s_len, 0.0, None, 'entry', 'center slot'))
        m.append(_line(-s_len, 0.0, None, 'entry', 'center slot return'))
    n = _even(mn, so) if mn > EPS else 0
    loops = []
    prev_stop = 0.0
    for k in range(1, n + 1):
        t = mn * k / n
        d = mn - t
        hx, hy, r = s_len + t, t, max(g['rc_rough'] - d, 0.0)
        loops.append((hx, hy, min(r, hy)))
    for k, (hx, hy, r) in enumerate(loops):
        what = 'pass {} of {}'.format(k + 1, n)
        nxt = loops[k + 1] if k + 1 < n else None
        stop = nxt[0] - hx if nxt and hy - r > nxt[0] - hx + EPS else 0.0
        if k == 0 or not prev_stop:
            m.append(_line(hx, 0.0, None, 'entry', what + ': step out'))
        m.extend(rect_loop(hx, hy, r, ccw, 'cut', what, stop))
        if stop:
            m.extend(step_s(hx, nxt[0], ccw, 'entry', 'pass {} of {}'.format(k + 2, n)))
        prev_stop = stop
    return m, n


def spiral(r0, r1, so, ccw, f_of):
    """Tangent semicircle spiral from r0 to r1 followed by a full circle at r1."""
    m = []
    if r1 - r0 > EPS:
        n = _even(2 * (r1 - r0), so)
        step = (r1 - r0) / n
        side = 1.0
        for i in range(n):
            a = side * (r0 + step * i)
            b = -side * (r0 + step * (i + 1))
            m.append(_arc(ccw, b, 0.0, None, (a + b) / 2.0, 0.0, f_of(r0 + step * i),
                          'spiral half-turn {} of {}'.format(i + 1, n)))
            side = -side
        x_end = r1 if n % 2 == 0 else -r1
    else:
        x_end = r1
    m.append(_arc(ccw, x_end, 0.0, None, 0.0, 0.0, 'cut', 'full roughing perimeter'))
    return m


def _circ_clearing(p, g, ent, ccw, so):
    m = []
    rt, r1 = g['rt'], g['r_rough']
    if ent['kind'] == 'HELIX':
        r0 = ent['r']
    else:
        if ent['kind'] == 'RAMP':
            m.append(_line(0.0, 0.0, None, 'entry', 'move to center along ramp slot'))
        r0 = 0.0
    m.extend(spiral(r0, r1, so, ccw, lambda r: 'entry' if r < rt - EPS else 'cut'))
    return m


def _finish_moves(p, g, ccw, z):
    rt = g['rt']
    m = []
    if p.circular:
        P = g['r_fin']
        rho = min(rt, P / 2.0)
        loop = [_arc(ccw, P, 0.0, None, 0.0, 0.0, 'finish', 'wall finishing pass')]
    else:
        P, hy = g['A'] - rt, g['B'] - rt
        rho = min(rt, hy) if hy > EPS else 0.0
        loop = rect_loop(P, hy, min(g['rc_fin'], hy), ccw, 'finish', 'wall finishing pass')
    m.append(_line(P - 2 * rho, 0.0, z, 'finish', 'finish: move to tangential lead-in start'))
    if rho > EPS:
        m.append(_arc(ccw, P, 0.0, None, P - rho, 0.0, 'lead', 'tangential lead-in to wall'))
    m.extend(loop)
    if p.spring_pass:
        m.extend(dict(x, c='spring pass: ' + x['c']) for x in loop)
    if rho > EPS:
        m.append(_arc(ccw, P - 2 * rho, 0.0, None, P - rho, 0.0, 'lead', 'tangential lead-out from wall'))
    return m, rho


def plan(p):
    """Compute local-frame moves and cutting parameters for one pocket."""
    g = geometry(p)
    rt, d = g['rt'], p.tool_diameter
    ent = entry_plan(p, g)
    ccw = p.climb
    so = p.stepover * d
    tan_a = math.tan(math.radians(p.ramp_angle))
    z_bottom = p.z_bottom()
    z_rough = z_bottom + p.floor_stock
    nz = _even(p.z_top - z_rough, p.step_down)
    levels = [p.z_top - (p.z_top - z_rough) * i / nz for i in range(1, nz + 1)]
    levels[-1] = z_rough
    finish_floor = p.floor_stock > EPS
    if finish_floor:
        levels.append(z_bottom)
    finish_wall = p.wall_stock > EPS
    feed = p.feed
    feed_entry = p.feed * p.entry_feed
    feed_finish = p.feed * g['r_fin'] / g['R'] if p.circular and g.get('R') else p.feed

    moves = []
    z_prev = p.z_top
    ex0 = entry_point(ent)
    loops = 0
    n_rough = len(levels) - (1 if finish_floor else 0)
    for i, z in enumerate(levels):
        floor = finish_floor and i == len(levels) - 1
        lvl = '{} of {}'.format(i + 1, n_rough) if not floor else 'floor finish'
        if i > 0:
            moves.append(_line(ex0[0], ex0[1], None, 'entry', 'return to entry point through cleared pocket'))
        moves.extend(_entry_moves(ent, z_prev, z, ccw, tan_a, lvl))
        if p.circular:
            moves.extend(_circ_clearing(p, g, ent, ccw, so))
        else:
            mm, loops = _rect_clearing(p, g, ent, ccw, so)
            moves.extend(mm)
        z_prev = z
    fin, rho = _finish_moves(p, g, ccw, z_bottom) if finish_wall else ([], 0.0)
    moves.extend(fin)
    return {'g': g, 'rt': rt, 'entry': ent, 'ccw': ccw, 'stepover': so, 'levels': levels, 'z_rough': z_rough,
            'z_bottom': z_bottom, 'finish_floor': finish_floor, 'finish_wall': finish_wall,
            'feed': feed, 'feed_entry': feed_entry, 'feed_finish': feed_finish, 'loops': loops,
            'feed_lead': lead_feed(p, rho, rt),
            'start': ex0, 'moves': moves}


def sample_paths(p, k, step=None):
    """Discretize planned moves into polylines [(feed_kind, [(x, y, z)])] for preview sketches."""
    step = step or max(p.tool_diameter / 12.0, 1e-3)
    out = []
    cur = (k['start'][0], k['start'][1], p.z_top)
    for mv in k['moves']:
        z = cur[2] if mv['z'] is None else mv['z']
        if mv['g'] == 1:
            pts = [cur, (mv['x'], mv['y'], z)]
        else:
            cx, cy = mv['cx'], mv['cy']
            r = math.hypot(cur[0] - cx, cur[1] - cy)
            a1 = math.atan2(cur[1] - cy, cur[0] - cx)
            a2 = math.atan2(mv['y'] - cy, mv['x'] - cx)
            sg = 1.0 if mv['g'] == 3 else -1.0
            sweep = (a2 - a1) * sg
            while sweep <= 1e-9:
                sweep += 2 * math.pi
            n = max(6, int(math.ceil(sweep * r / step)))
            pts = [(cx + r * math.cos(a1 + sg * sweep * i / n), cy + r * math.sin(a1 + sg * sweep * i / n),
                    cur[2] + (z - cur[2]) * i / n) for i in range(n + 1)]
        out.append((mv['f'], pts))
        cur = (mv['x'], mv['y'], z)
    return out


def tool_message(p):
    """ASCII string for (MSG, ...) prompt before tool change."""
    u = 'MM' if p.units == 'MM' else 'IN'
    return 'T{} ENDMILL D{} {} {} FLUTES'.format(p.tool_number, _short(p.tool_diameter), u, p.flutes)


def generate(p):
    """Generate a fully commented LinuxCNC .ngc pocket program. Raises PocketError if invalid."""
    errors = validate(p)
    if errors:
        raise PocketError('\n'.join(errors))
    k = plan(p)
    g = k['g']
    ent = k['entry']
    u = 'mm' if p.units == 'MM' else 'in'
    climb = 'climb' if p.climb else 'conventional'
    arc_dir = 'CCW, G3' if p.climb else 'CW, G2'
    z_approach = p.z_top + p.retract
    nc = len(p.centers)
    feeds = {'entry': k['feed_entry'], 'cut': k['feed'], 'finish': k['feed_finish'], 'lead': k['feed_lead']}
    feed_txt = {'entry': 'entry feed', 'cut': 'roughing feed', 'finish': 'finishing feed',
                'lead': 'lead-in/out feed'}

    out = []
    out.append('(' + '=' * 70 + ')')
    name = p.name.replace('(', '').replace(')', '')
    out.append('({} POCKET{})'.format('CIRCULAR' if p.circular else 'RECTANGULAR', (' - ' + name) if name else ''))
    out.append('(Generated by PB_Conversational for Probe Basic)')
    out.append('(' + '=' * 70 + ')')
    if p.circular:
        out.append('(POCKET: diameter {} {}, depth {} {})'.format(_short(p.diameter), u, _short(p.depth), u))
    else:
        out.append('(POCKET: {} x {} {}, corner R{}, rotated {} deg, depth {} {})'.format(
            _short(p.length), _short(p.width), u, _short(p.corner_radius), _short(p.angle), _short(p.depth), u))
    out.append('(  from Z{} to Z{}; finished pocket dimensions)'.format(_short(p.z_top), _short(k['z_bottom'])))
    out.append('(  NOTE: calculated for D{} cutter.)'.format(_short(p.tool_diameter)))
    out.append('(POSITIONS: {} in {}, pocket center)'.format(nc, p.wcs))
    for i, (cx, cy) in enumerate(p.centers):
        out.append('(  {}: X{} Y{})'.format(i + 1, _short(cx), _short(cy)))
    out.append('(HEIGHTS:)')
    out.append('(  Z{} safety clearance)'.format(_short(p.clearance)))
    out.append('(  Z{} approach R-plane)'.format(_short(z_approach)))
    out.append('(  Z{} part top face)'.format(_short(p.z_top)))
    out.append('(  Z{} pocket floor)'.format(_short(k['z_bottom'])))
    out.append('(TOOL: T{} endmill D{} {}, {} flutes. Z = tip)'.format(
        p.tool_number, _short(p.tool_diameter), u, p.flutes))
    out.append('(STRATEGY:)')
    nl = len(k['levels']) - (1 if k['finish_floor'] else 0)
    out.append('(  roughing in {} equal Z level{} down to Z{}{})'.format(
        nl, 's' if nl > 1 else '', _short(k['z_rough']),
        ', leaving {} {} floor stock'.format(_short(p.floor_stock), u) if k['finish_floor'] else ''))
    if ent['kind'] == 'HELIX':
        out.append('(  helical entry D{} at center, {} deg slope)'.format(_short(2 * ent['r']), _short(p.ramp_angle)))
    elif ent['kind'] == 'RAMP':
        out.append('(  zig-zag center ramp, {} {} long, {} deg slope)'.format(_short(2 * ent['s']), u, _short(p.ramp_angle)))
    else:
        out.append('(  plunge entry at center: requires pre-drilled hole or center-cutting endmill)')
    out.append('(  {} milling - {}, stepover {} {} - {}% of D)'.format(
        climb, arc_dir, _short(k['stepover']), u, _short(p.stepover * 100)))
    if k['finish_wall']:
        out.append('(  wall stock {} {}; full-depth wall finish{})'.format(
            _short(p.wall_stock), u, ' with spring pass' if p.spring_pass else ''))
    else:
        out.append('(  no wall stock: roughing cuts to final wall)')
    if k['finish_floor']:
        out.append('(  floor finish: repeat path at Z{})'.format(_short(k['z_bottom'])))
    out.append('(FEEDS: roughing F{}, entry F{}{})'.format(
        _short(k['feed']), _short(k['feed_entry']),
        ', finish F{} at tool center'.format(_short(k['feed_finish'])) if k['finish_wall'] and p.circular
        else (', finish F{}'.format(_short(k['feed_finish'])) if k['finish_wall'] else '')))
    if k['finish_wall']:
        out.append('(  wall lead-in/out F{}: entry feed at the cutting edge)'.format(_short(k['feed_lead'])))
    program_start(out, p, 'POCKET MILLING: {} pocket{}'.format(nc, 's' if nc > 1 else ''))
    for n, (cx, cy) in enumerate(p.centers):
        out.append('(pocket {} of {}: center X{} Y{})'.format(n + 1, nc, _short(cx), _short(cy)))
        emit_moves(out, p, k, cx, cy, g['ang'], feeds, feed_txt, 'rapid over entry point')
    program_end(out, p)
    return out


# ---------------------------------------------------------------- shared G-code emitters (also used by contour_gen)
BAR = '(' + '-' * 70 + ')'


def program_start(out, p, title):
    u = 'mm' if p.units == 'MM' else 'in'
    coolant = {'MIST': ('M7', 'mist coolant on'), 'FLOOD': ('M8', 'flood coolant on')}.get(p.coolant)
    out.append('(STARTUP MODES:)')
    out.append('(  G90 absolute, G94 feed in {}/min, G17 XY plane)'.format(u))
    out.append('(  G40 cutter comp off, G49 length comp off, G80 canned cycle off)')
    out.append('(  G91.1 incremental arc centers I J)')
    out.append('G90 G94 G17 G40 G49 G80 G91.1')
    out.append(_c('G21', 'units: millimeters') if p.units == 'MM' else _c('G20', 'units: inches'))
    out.append(BAR)
    out.append('({})'.format(title))
    out.append(BAR)
    out.append('(MSG, {})'.format(tool_message(p)))
    out.append(_c('T{:d} M6'.format(p.tool_number), 'tool change: endmill'))
    out.append(_c('G43 H{:d}'.format(p.tool_number), 'enable tool length offset for T{}'.format(p.tool_number)))
    out.append(_c(p.wcs, 'work coordinate system'))
    out.append(_c('G0 Z{}'.format(_fmt(p.clearance)), 'move to safety clearance height'))
    out.append(_c('S{} M3'.format(_fmt(p.spindle_rpm)), 'spindle CW at {} rpm'.format(_short(p.spindle_rpm))))
    if coolant:
        out.append(_c(*coolant))


def emit_moves(out, p, k, cx, cy, ang, feeds, feed_txt, start_comment):
    u = 'mm' if p.units == 'MM' else 'in'

    def glob(x, y):
        v = _rot((x, y), ang)
        return cx + v[0], cy + v[1]

    sx, sy = glob(*k['start'])
    out.append(_c('G0 X{} Y{}'.format(_fmt(sx), _fmt(sy)), start_comment))
    out.append(_c('G0 Z{}'.format(_fmt(p.z_top + p.retract)), 'rapid to approach R-plane'))
    out.append(_c('F{}'.format(_fmt(feeds['entry'])), '{}: {} {}/min'.format(feed_txt['entry'], _short(feeds['entry']), u)))
    out.append(_c('G1 Z{}'.format(_fmt(p.z_top)), 'feed to part top face'))
    cur = (float(_fmt(sx)), float(_fmt(sy)), float(_fmt(p.z_top)))
    fkind = 'entry'
    for mv in k['moves']:
        x, y = glob(mv['x'], mv['y'])
        z = cur[2] if mv['z'] is None else mv['z']
        if mv['g'] == 1 and abs(x - cur[0]) < 1e-7 and abs(y - cur[1]) < 1e-7 and abs(z - cur[2]) < 1e-7:
            continue
        if mv['f'] != fkind:
            fkind = mv['f']
            out.append(_c('F{}'.format(_fmt(feeds[fkind])), '{}: {} {}/min'.format(
                feed_txt[fkind], _short(feeds[fkind]), u)))
        zw = '' if abs(z - cur[2]) < 1e-9 else ' Z{}'.format(_fmt(z))
        if mv['g'] == 1:
            out.append(_c('G1 X{} Y{}{}'.format(_fmt(x), _fmt(y), zw), mv['c']))
        else:
            ccx, ccy = glob(mv['cx'], mv['cy'])
            out.append(_c('G{} X{} Y{}{} I{} J{}'.format(
                mv['g'], _fmt(x), _fmt(y), zw, _fmt(ccx - cur[0]), _fmt(ccy - cur[1])), mv['c']))
        cur = (float(_fmt(x)), float(_fmt(y)), float(_fmt(z)))
    out.append(_c('G0 Z{}'.format(_fmt(p.clearance)), 'retract to safety clearance before XY move'))


def program_end(out, p):
    out.append(BAR)
    out.append('(END)')
    out.append(BAR)
    out.append(_c('M9', 'coolant off'))
    out.append(_c('M5', 'spindle stop'))
    if p.home_z_at_end:
        out.append(_c('G53 G0 Z0', 'retract Z to machine home'))
    out.append(_c('M30', 'program end'))

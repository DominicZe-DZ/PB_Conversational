# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Raúl Milla (DominicZe)
"""
Pure-Python G-code generator for external island contouring in LinuxCNC.

Zero Qt or LinuxCNC dependencies. Reuses moves, sampling, and G-code emitters from pocket_gen.py.

Strategy:
  - Plunges safely outside the rectangular stock (STOCK X x STOCK Y centered at X0 Y0) and
    steps inward using parallel contour loops at a single cutting direction (CW G2 climb milling
    on outside walls with M3).
  - Part must stick up out of the vise jaws further than the cut depth since the tool plunges
    outside the stock perimeter.
"""

import math

from thread_mill_gen import _short
from pocket_gen import (PocketParams, EPS, STEPOVER_MAX, _even, _line, _arc, rect_loop,
                        emit_moves, program_start, program_end)

GAP = 0.25                 # tool plunges 25% of cutter diameter outside stock


class ContourError(ValueError):
    """Validation or geometry error shown to the user."""
    pass


class ContourParams(PocketParams):
    """External contour inputs (extends PocketParams with stock_x and stock_y)."""

    def __init__(self):
        super(ContourParams, self).__init__()
        self.depth = 5.0
        self.stock_x = 50.0
        self.stock_y = 35.0
        self.entry = 'OUTSIDE'


def _shape_ok(p):
    if p.circular:
        return p.diameter > 0
    return p.length > 0 and p.width > 0 and 0 <= p.corner_radius <= min(p.length, p.width) / 2.0 + EPS


def island_extent(p):
    """Half-extents (X, Y) in WCS of the rotated finished island."""
    if p.circular:
        return p.diameter / 2.0, p.diameter / 2.0
    A, B, R = p.length / 2.0, p.width / 2.0, min(p.corner_radius, min(p.length, p.width) / 2.0)
    a = math.radians(p.angle)
    ca, sa = abs(math.cos(a)), abs(math.sin(a))
    return (A - R) * ca + (B - R) * sa + R, (A - R) * sa + (B - R) * ca + R


def dist_to_island(p, g, x, y):
    """Signed distance from WCS point (x, y) outside the island to the island wall."""
    if p.circular:
        return math.sqrt(x * x + y * y) - g['R']
    ca, sa = math.cos(g['ang']), math.sin(g['ang'])
    u, v = x * ca + y * sa, -x * sa + y * ca
    qx, qy = abs(u) - (g['A'] - g['rc']), abs(v) - (g['B'] - g['rc'])
    ax, ay = max(qx, 0.0), max(qy, 0.0)
    return math.sqrt(ax * ax + ay * ay) + min(max(qx, qy), 0.0) - g['rc']


def geometry(p):
    rt = p.tool_diameter / 2.0
    sw = p.wall_stock
    g = {'rt': rt, 'sw': sw, 'o_rough': rt + sw, 'o_fin': rt}
    if p.circular:
        R = p.diameter / 2.0
        g.update(R=R, ang=0.0, r_fin=R + rt, r_rough=R + rt + sw)
    else:
        g.update(A=p.length / 2.0, B=p.width / 2.0, rc=p.corner_radius, ang=math.radians(p.angle))
    hx, hy = p.stock_x / 2.0, p.stock_y / 2.0
    g['d_max'] = max(dist_to_island(p, g, sx * hx, sy * hy) for sx in (1, -1) for sy in (1, -1))
    so = p.stepover * p.tool_diameter
    g['o_max'] = max(g['d_max'] + rt - so, g['o_rough'])
    return g


def offsets(p, g):
    so = p.stepover * p.tool_diameter
    span = g['o_max'] - g['o_rough']
    n = _even(span, so) if span > EPS else 0
    return [g['o_rough'] + span * (n - i) / n for i in range(n)] + [g['o_rough']]


def start_x(p, g):
    rt = p.tool_diameter / 2.0
    ex, ey = p.stock_x / 2.0 + rt + GAP * p.tool_diameter, p.stock_y / 2.0 + rt + GAP * p.tool_diameter
    ca, sa = abs(math.cos(g['ang'])), abs(math.sin(g['ang']))
    t = min(ex / ca if ca > EPS else float('inf'), ey / sa if sa > EPS else float('inf'))
    first = (g['R'] if p.circular else g['A']) + g['o_max']
    return max(t, first + GAP * p.tool_diameter)


def validate(p):
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
            e.append('Island diameter must be greater than 0.')
    else:
        if p.length <= 0 or p.width <= 0:
            e.append('Island length and width must be greater than 0.')
        elif p.corner_radius < 0 or p.corner_radius > min(p.length, p.width) / 2.0 + EPS:
            e.append('Corner radius must be between 0 and half the short side ({} {}).'.format(
                _short(min(p.length, p.width) / 2.0), u))
    if p.stock_x <= 0 or p.stock_y <= 0:
        e.append('Stock dimensions (STOCK X and STOCK Y) must be greater than 0.')
    elif _shape_ok(p):
        ix, iy = island_extent(p)
        if ix > p.stock_x / 2.0 + EPS or iy > p.stock_y / 2.0 + EPS:
            e.append('Island ({} x {} {}{}) does not fit inside {} x {} stock.'.format(
                _short(2 * ix), _short(2 * iy), u, '' if p.circular or not p.angle else ' rotated',
                _short(p.stock_x), _short(p.stock_y)))
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
    if not (0 < p.entry_feed <= 1.0 + EPS):
        e.append('Entry feed must be between 1% and 100%.')
    if p.retract < 0:
        e.append('Approach (R-plane) cannot be negative.')
    if p.clearance < p.z_top + p.retract - EPS:
        e.append('Safety Z must be >= Z TOP + approach.')
    return e


def contour_warning(p):
    """Non-blocking warning, or None."""
    if validate(p):
        return None
    w = []
    if p.step_down > p.tool_diameter + 1e-9:
        w.append('step Z ({}) exceeds cutter diameter: heavy roughing cut'.format(_short(p.step_down)))
    g = geometry(p)
    if g['d_max'] <= g['sw'] + 1e-9:
        if g['sw'] > EPS:
            w.append('stock matches finished island plus wall stock: only wall-stock cleanup and finishing passes run')
        else:
            w.append('stock matches finished island: only a single spring/cleanup contour pass runs')
    return '; '.join(w) if w else None


def label(p):
    if p.circular:
        return 'ISLAND D{}'.format(_short(p.diameter))
    return 'ISLAND {} X {}{}'.format(_short(p.length), _short(p.width),
                                     ' R{}'.format(_short(p.corner_radius)) if p.corner_radius > 0 else '')


def _loop(p, g, o, ccw, f, what):
    if p.circular:
        r = g['R'] + o
        return [_arc(ccw, r, 0.0, None, 0.0, 0.0, f, what)]
    return rect_loop(g['A'] + o, g['B'] + o, g['rc'] + o, ccw, f, what)


def _finish_moves(p, g, ccw, z):
    rt = g['rt']
    P = (g['R'] if p.circular else g['A']) + rt
    rho = rt
    loop = _loop(p, g, rt, ccw, 'finish', 'wall finishing pass')
    m = [_line(P + 2 * rho, 0.0, z, 'finish', 'finish: move to tangential lead-in start')]
    m.append(_arc(not ccw, P, 0.0, None, P + rho, 0.0, 'finish', 'tangential lead-in to island wall'))
    m.extend(loop)
    if p.spring_pass:
        m.extend(dict(x, c='spring pass: ' + x['c']) for x in loop)
    m.append(_arc(not ccw, P + 2 * rho, 0.0, None, P + rho, 0.0, 'finish', 'tangential lead-out from wall'))
    return m


def plan(p):
    g = geometry(p)
    rt = g['rt']
    ccw = not p.climb                            # outside climb milling with M3 = CW (G2)
    so = p.stepover * p.tool_diameter
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
    feed_finish = p.feed * g['r_fin'] / g['R'] if p.circular else p.feed
    base = g['R'] if p.circular else g['A']
    offs = offsets(p, g)
    xs = start_x(p, g)

    moves = []
    n_rough = len(levels) - (1 if finish_floor else 0)
    for i, z in enumerate(levels):
        floor = finish_floor and i == len(levels) - 1
        lvl = '{} of {}'.format(i + 1, n_rough) if not floor else 'floor finish'
        moves.append(_line(xs, 0.0, z, 'entry', 'level {}: plunge outside stock'.format(lvl)))
        for j, o in enumerate(offs):
            what = 'pass {} of {}'.format(j + 1, len(offs))
            moves.append(_line(base + o, 0.0, None, 'entry', what + ': step inward toward island'))
            moves.extend(_loop(p, g, o, ccw, 'cut', what))
        moves.append(_line(xs, 0.0, None, 'cut', 'retract outside stock through cleared area'))
    fin = _finish_moves(p, g, ccw, z_bottom) if finish_wall else []
    moves.extend(fin)
    return {'g': g, 'rt': rt, 'entry': {'kind': 'OUTSIDE'}, 'ccw': ccw, 'stepover': so, 'levels': levels,
            'z_rough': z_rough, 'z_bottom': z_bottom, 'finish_floor': finish_floor, 'finish_wall': finish_wall,
            'feed': feed, 'feed_entry': feed_entry, 'feed_finish': feed_finish, 'loops': len(offs),
            'offsets': offs, 'start': (xs, 0.0), 'moves': moves}


def generate(p):
    """Generate a fully commented LinuxCNC .ngc contour program. Raises ContourError if invalid."""
    errors = validate(p)
    if errors:
        raise ContourError('\n'.join(errors))
    k = plan(p)
    g = k['g']
    u = 'mm' if p.units == 'MM' else 'in'
    climb = 'climb' if p.climb else 'conventional'
    arc_dir = 'CCW, G3' if k['ccw'] else 'CW, G2'
    z_approach = p.z_top + p.retract

    out = []
    out.append('(' + '=' * 70 + ')')
    name = p.name.replace('(', '').replace(')', '')
    out.append('({} EXTERNAL CONTOUR{})'.format('CIRCULAR' if p.circular else 'RECTANGULAR',
                                               (' - ' + name) if name else ''))
    out.append('(Generated by PB_Conversational for Probe Basic)')
    out.append('(' + '=' * 70 + ')')
    if p.circular:
        out.append('(ISLAND: diameter {} {}, depth {} {})'.format(_short(p.diameter), u, _short(p.depth), u))
    else:
        out.append('(ISLAND: {} x {} {}, corner R{}, rotated {} deg, depth {} {})'.format(
            _short(p.length), _short(p.width), u, _short(p.corner_radius), _short(p.angle), _short(p.depth), u))
    out.append('(  centered at X0 Y0 in {}; Z{} to Z{}; finished island dimensions)'.format(
        p.wcs, _short(p.z_top), _short(k['z_bottom'])))
    out.append('(STOCK: {} x {} {}, centered at X0 Y0, unrotated)'.format(_short(p.stock_x), _short(p.stock_y), u))
    out.append('(  NOTE: part must clear vise jaws by more than {} {}: tool plunges outside stock.)'.format(
        _short(p.depth), u))
    out.append('(  NOTE: calculated for D{} cutter.)'.format(_short(p.tool_diameter)))
    out.append('(HEIGHTS:)')
    out.append('(  Z{} safety clearance)'.format(_short(p.clearance)))
    out.append('(  Z{} approach R-plane)'.format(_short(z_approach)))
    out.append('(  Z{} part top face)'.format(_short(p.z_top)))
    out.append('(  Z{} contour floor)'.format(_short(k['z_bottom'])))
    out.append('(TOOL: T{} endmill D{} {}, {} flutes. Z = tip)'.format(
        p.tool_number, _short(p.tool_diameter), u, p.flutes))
    out.append('(STRATEGY:)')
    nl = len(k['levels']) - (1 if k['finish_floor'] else 0)
    out.append('(  roughing in {} equal Z level{} down to Z{}{})'.format(
        nl, 's' if nl > 1 else '', _short(k['z_rough']),
        ', leaving {} {} floor stock'.format(_short(p.floor_stock), u) if k['finish_floor'] else ''))
    out.append('(  plunges outside stock per level and feeds inward: {} pass{} outside-in)'.format(
        k['loops'], 'es' if k['loops'] > 1 else ''))
    out.append('(  {} milling - {}, max stepover {} {} - {}% of D)'.format(
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
    program_start(out, p, 'CONTOURING: {}'.format(label(p)))
    feeds = {'entry': k['feed_entry'], 'cut': k['feed'], 'finish': k['feed_finish']}
    feed_txt = {'entry': 'entry feed', 'cut': 'roughing feed', 'finish': 'finishing feed'}
    emit_moves(out, p, k, 0.0, 0.0, g['ang'], feeds, feed_txt, 'rapid outside stock to plunge point')
    program_end(out, p)
    return out

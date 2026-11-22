# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Raúl Milla (DominicZe)
"""
Schematic cross-section and scaled top-down toolpath sketches for Pockets and Contours.
"""

import math

from qtpy.QtCore import Qt, QPointF, QRectF
from qtpy.QtGui import QPainter, QBrush, QColor, QLinearGradient, QPolygonF, QPainterPath, QTransform

from pocket_gen import plan, sample_paths, label
from contour_gen import plan as contour_plan, label as contour_label, island_extent
from thread_sketch import (_Base, _pen, _arrow, _bebas, _fmt, PART_A, PART_B, TOOL_FILL, TOOL_TEETH,
                           INK, PATH, ENTRY)

SCREEN = QColor(146, 150, 149)


class _PocketBase(_Base):
    PLAN = staticmethod(plan)

    def set_params(self, p, problem=None):
        self.p, self.k, self.problem = p, None, problem
        if p is not None:
            try:
                self.k = self.PLAN(p)
            except Exception as e:
                self.problem = self.problem or str(e)
        self.update()


def _endmill_side(qp, xt, y_tip, tool_px, flute_h):
    qp.setPen(_pen(INK, 1.2))
    qp.setBrush(QBrush(TOOL_FILL))
    qp.drawRect(QRectF(QPointF(xt - tool_px * 0.85, 0), QPointF(xt + tool_px * 0.85, y_tip - flute_h)))
    qp.setBrush(QBrush(TOOL_TEETH))
    qp.drawRect(QRectF(QPointF(xt - tool_px, y_tip - flute_h), QPointF(xt + tool_px, y_tip)))
    qp.setPen(_pen(TOOL_FILL, 1.0))
    for i in range(1, 5):
        yy = y_tip - flute_h * i / 5.0
        qp.drawLine(QPointF(xt - tool_px, yy + flute_h * 0.08), QPointF(xt + tool_px, yy - flute_h * 0.08))
    qp.setPen(_pen(INK, 0.8, Qt.DashDotLine))
    qp.drawLine(QPointF(xt, 2), QPointF(xt, y_tip + 5))


def _endmill_plan(qp, tc, R, flutes):
    col = QColor(TOOL_FILL)
    col.setAlpha(170)
    qp.setPen(_pen(INK, 1.1))
    qp.setBrush(QBrush(col))
    qp.drawEllipse(tc, R, R)
    z = int(max(1, min(flutes or 3, 12)))
    qp.setBrush(QBrush(SCREEN))
    for i in range(z):
        a = 2 * math.pi * i / z + math.pi / 2
        ca, sa = math.cos(a), math.sin(a)
        p1 = QPointF(tc.x() + ca * R * 0.62, tc.y() - sa * R * 0.62)
        p2 = QPointF(tc.x() + ca * R, tc.y() - sa * R)
        b = a - 0.32
        p3 = QPointF(tc.x() + math.cos(b) * R, tc.y() - math.sin(b) * R)
        qp.drawPolygon(QPolygonF([p1, p2, p3]))
    qp.setBrush(QBrush(INK))
    qp.drawEllipse(tc, 1.8, 1.8)


def _origin(qp, o):
    qp.setPen(_pen(QColor(150, 20, 10), 1.6))
    qp.setBrush(Qt.NoBrush)
    qp.drawLine(QPointF(o.x() - 12, o.y()), QPointF(o.x() + 12, o.y()))
    qp.drawLine(QPointF(o.x(), o.y() - 12), QPointF(o.x(), o.y() + 12))
    qp.drawEllipse(o, 5, 5)
    _bebas(qp, 12)
    qp.drawText(QPointF(o.x() + 7, o.y() - 7), 'X0 Y0')


def _paths(qp, p, k, P):
    colors = {'entry': (ENTRY, 1.6, Qt.SolidLine), 'cut': (PATH, 1.0, Qt.SolidLine),
              'finish': (INK, 1.3, Qt.DashLine)}
    samples = sample_paths(p, k)
    for kind in ('cut', 'finish', 'entry'):
        col, w, st = colors[kind]
        qp.setPen(_pen(col, w, st))
        for f, pts in samples:
            if (f if f != 'lead' else 'finish') != kind:
                continue
            path = QPainterPath()
            for i, (x, y, z) in enumerate(pts):
                (path.moveTo if i == 0 else path.lineTo)(P(x, y))
            qp.drawPath(path)


def _text_w(qp, text):
    fm = qp.fontMetrics()
    return fm.horizontalAdvance(text) if hasattr(fm, 'horizontalAdvance') else fm.width(text)


class PocketSectionSketch(_PocketBase):
    """Cross-section sketch (not to scale) with Z value boxes aligned to their lines."""
    BOX_W = 140
    BOX_H = 45
    FR = dict(clearance=0.07, approach=0.19, top=0.31, step=0.50, bottom=0.74)

    def __init__(self, boxes, parent=None):
        super(PocketSectionSketch, self).__init__(parent)
        self.boxes = boxes
        for b in boxes.values():
            b.setParent(self)
        self.setMinimumSize(460, 420)

    def _y(self, key):
        h = self.height()
        return 8 + (h - 16) * self.FR[key]

    def resizeEvent(self, ev):
        for key, b in self.boxes.items():
            b.setGeometry(4, int(self._y(key) - self.BOX_H / 2), self.BOX_W, self.BOX_H)
        super(PocketSectionSketch, self).resizeEvent(ev)

    def paintEvent(self, ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing, True)
        x0 = self.BOX_W + 14
        W = self.width() - x0 - 10
        H = self.height()
        y_cl, y_ap, y_top, y_bot = (self._y(k) for k in ('clearance', 'approach', 'top', 'bottom'))
        y_base = H - 10
        cx = x0 + W * 0.45

        qp.setPen(_pen(INK, 1.0, Qt.DashLine))
        for y in (y_cl, y_ap):
            qp.drawLine(QPointF(x0 - 10, y), QPointF(x0 + W, y))
        qp.setPen(_pen(INK, 1.0))
        for y in (y_cl, y_ap, y_top, y_bot):
            qp.drawLine(QPointF(self.BOX_W + 4, y), QPointF(x0 - 2, y))
            _arrow(qp, QPointF(x0 - 1, y), (1, 0), 8)

        if self.p is None or self.k is None:
            _bebas(qp, 14)
            qp.setPen(_pen(QColor(150, 20, 10)))
            qp.drawText(QRectF(x0, y_top, W, y_bot - y_top), Qt.AlignCenter | Qt.TextWordWrap,
                        self.problem or 'INCOMPLETE DATA')
            return
        p, k = self.p, self.k
        depth = max(p.depth, 1e-9)
        y_of = lambda z: y_top + (y_bot - y_top) * (p.z_top - z) / depth

        x_block = W * 0.44
        hw = W * 0.30
        grad = QLinearGradient(0, y_top, 0, y_base)
        grad.setColorAt(0, PART_A)
        grad.setColorAt(1, PART_B)
        qp.setPen(_pen(INK, 1.4))
        qp.setBrush(QBrush(grad))
        qp.drawPolygon(QPolygonF([QPointF(cx - x_block, y_top), QPointF(cx - hw, y_top), QPointF(cx - hw, y_bot),
                                  QPointF(cx + hw, y_bot), QPointF(cx + hw, y_top), QPointF(cx + x_block, y_top),
                                  QPointF(cx + x_block, y_base), QPointF(cx - x_block, y_base)]))
        qp.setBrush(Qt.NoBrush)

        tool_px = max(hw * 0.12, min(hw * 0.6, hw * p.tool_diameter / max(
            p.diameter if p.circular else min(p.length, p.width), 1e-9)))
        sw_px = 6.0 if p.wall_stock > 0 else 0.0
        sf_px = 6.0 if p.floor_stock > 0 else 0.0
        if sw_px or sf_px:
            qp.setPen(_pen(PATH, 1.0, Qt.DotLine))
            yr = y_bot - sf_px
            qp.drawPolyline(QPolygonF([QPointF(cx - hw + sw_px, y_top), QPointF(cx - hw + sw_px, yr),
                                       QPointF(cx + hw - sw_px, yr), QPointF(cx + hw - sw_px, y_top)]))

        levels = k['levels'][:-1] if k['finish_floor'] else k['levels']
        _bebas(qp, 11)
        for i, z in enumerate(levels):
            y = y_of(z) - (sf_px if i == len(levels) - 1 else 0)
            qp.setPen(_pen(PATH, 0.9, Qt.DashLine))
            qp.drawLine(QPointF(cx - hw + 2, y), QPointF(cx + hw - 2, y))
            qp.setPen(_pen(PATH))
            qp.drawText(QPointF(cx - hw + 5, y - 3), 'L{}'.format(i + 1))

        y_step = self._y('step')
        y1 = y_of(levels[0]) - (sf_px if len(levels) == 1 else 0)
        qp.setPen(_pen(INK, 1.0))
        qp.drawLine(QPointF(self.BOX_W + 4, y_step), QPointF(cx - hw - 14, y_step))
        qp.drawLine(QPointF(cx - hw - 14, y_step), QPointF(cx - hw - 14, y1))
        qp.drawLine(QPointF(cx - hw - 14, y1), QPointF(cx - hw - 2, y1))
        _arrow(qp, QPointF(cx - hw - 1, y1), (1, 0), 7)

        qp.setPen(_pen(INK, 0.8, Qt.DashDotLine))
        qp.drawLine(QPointF(cx, y_ap - 6), QPointF(cx, y_base - 2))
        kind = k['entry']['kind']
        qp.setPen(_pen(ENTRY, 1.6))
        path = QPainterPath()
        if kind == 'HELIX':
            amp = min(hw * 0.35, tool_px * 0.6)
            n = 160
            for i in range(n + 1):
                t = i / float(n)
                pt = QPointF(cx + amp * math.cos(t * 3 * 2 * math.pi), y_top + (y1 - y_top) * t)
                (path.moveTo if i == 0 else path.lineTo)(pt)
        elif kind == 'RAMP':
            amp = hw * 0.6
            pts = [QPointF(cx + amp, y_top)]
            for i in range(1, 5):
                pts.append(QPointF(cx + (amp if i % 2 == 0 else -amp), y_top + (y1 - y_top) * i / 4.0))
            path.moveTo(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
        else:
            path.moveTo(QPointF(cx, y_ap))
            path.lineTo(QPointF(cx, y1))
        qp.drawPath(path)
        if kind == 'PLUNGE':
            _arrow(qp, QPointF(cx, y1), (0, 1), 9)

        xt = cx + hw - tool_px
        y_tip = y_bot
        flute_h = min((y_bot - y_top) * 0.75, tool_px * 3.2)
        _endmill_side(qp, xt, y_tip, tool_px, flute_h)

        xl = min(cx + x_block + 18, x0 + W - 4)
        qp.setPen(_pen(INK, 1.0))
        qp.drawLine(QPointF(cx + x_block + 3, y_bot), QPointF(xl + 6, y_bot))
        qp.drawLine(QPointF(xl, y_top), QPointF(xl, y_bot))
        _arrow(qp, QPointF(xl, y_top), (0, -1), 7)
        _arrow(qp, QPointF(xl, y_bot), (0, 1), 7)
        _bebas(qp, 14)
        qp.save()
        qp.translate(xl - 5, (y_top + y_bot) / 2.0)
        qp.rotate(-90)
        lt = 'DEPTH {}  ·  Z BOT {}'.format(_fmt(p.depth), _fmt(k['z_bottom']))
        qp.drawText(QPointF(-_text_w(qp, lt) / 2.0, 0), lt)
        qp.restore()

        y_d = y_top - 14
        qp.setPen(_pen(INK, 1.0))
        for sg in (1, -1):
            qp.drawLine(QPointF(cx + sg * hw, y_top - 3), QPointF(cx + sg * hw, y_d - 6))
        qp.drawLine(QPointF(cx - hw, y_d), QPointF(cx + hw, y_d))
        _arrow(qp, QPointF(cx - hw, y_d), (-1, 0), 7)
        _arrow(qp, QPointF(cx + hw, y_d), (1, 0), 7)
        lab = label(p).replace('POCKET ', '')
        fm = qp.fontMetrics()
        tw = _text_w(qp, lab)
        tx, ty = cx - hw / 2.0 - tw / 2.0, y_d - 5
        qp.fillRect(QRectF(tx - 3, ty - fm.ascent(), tw + 6, fm.height()), SCREEN)
        qp.drawText(QPointF(tx, ty), lab)


class PocketPlanSketch(_PocketBase):
    """Scaled top-down pocket toolpath preview."""

    def __init__(self, parent=None):
        super(PocketPlanSketch, self).__init__(parent)
        self.setMinimumSize(300, 210)

    def paintEvent(self, ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing, True)
        if self.p is None or self.k is None:
            self._no_data(qp)
            return
        p, k = self.p, self.k
        g = k['g']
        text_h = 26
        if p.circular:
            ex_x = ex_y = g['R']
        else:
            ca, sa = abs(math.cos(g['ang'])), abs(math.sin(g['ang']))
            ex_x, ex_y = g['A'] * ca + g['B'] * sa, g['A'] * sa + g['B'] * ca
        ext_x, ext_y = ex_x * 1.18 + 1e-9, ex_y * 1.18 + 1e-9
        sc = min((self.width() - 8) / (2 * ext_x), (self.height() - text_h - 8) / (2 * ext_y))
        c = QPointF(self.width() / 2.0, 4 + (self.height() - text_h - 8) / 2.0)

        grad = QLinearGradient(0, c.y() - ext_y * sc, 0, c.y() + ext_y * sc)
        grad.setColorAt(0, PART_A)
        grad.setColorAt(1, PART_B)
        qp.setPen(_pen(INK, 1.4))
        qp.setBrush(QBrush(grad))
        qp.drawRect(QRectF(c.x() - ext_x * 0.97 * sc, c.y() - ext_y * 0.97 * sc, 2 * ext_x * 0.97 * sc, 2 * ext_y * 0.97 * sc))
        T = QTransform()
        T.translate(c.x(), c.y())
        T.scale(sc, -sc)
        T.rotateRadians(g['ang'])

        def P(x, y):
            return T.map(QPointF(x, y))

        shape = QPainterPath()
        if p.circular:
            shape.addEllipse(QPointF(0, 0), g['R'], g['R'])
        else:
            rc = max(p.corner_radius, g['rt'])
            shape.addRoundedRect(QRectF(-g['A'], -g['B'], 2 * g['A'], 2 * g['B']), rc, rc)
        qp.setBrush(QBrush(SCREEN))
        qp.setPen(_pen(INK, 1.4))
        qp.drawPath(T.map(shape))
        qp.setBrush(Qt.NoBrush)

        _paths(qp, p, k, P)
        _origin(qp, P(0, 0))
        sx, sy = k['start']
        qp.setBrush(QBrush(ENTRY))
        qp.setPen(_pen(ENTRY, 1.0))
        qp.drawEllipse(P(sx, sy), 3.5, 3.5)

        if p.circular:
            tx = g['r_fin'] if k['finish_wall'] else g['r_rough']
        else:
            tx = g['A'] - g['rt'] if k['finish_wall'] else g['s_len'] + min(g['ex'], g['ey'])
        _endmill_plan(qp, P(tx, 0), g['rt'] * sc, p.flutes)

        _bebas(qp, 15)
        qp.setPen(_pen(INK))
        how = 'SPIRAL' if p.circular else '{} PASSES{}'.format(k['loops'], ' + SLOT' if g.get('s_len', 0) > 1e-9 else '')
        txt = '{}  ·  {}  ·  {}'.format('G3' if k['ccw'] else 'G2',
                                        'CLIMB' if p.climb else 'CONVENTIONAL', how)
        qp.drawText(QRectF(0, self.height() - text_h, self.width(), text_h), Qt.AlignCenter, txt)


class ContourSectionSketch(PocketSectionSketch):
    """Cross-section sketch for external island contouring."""
    PLAN = staticmethod(contour_plan)

    def paintEvent(self, ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing, True)
        x0 = self.BOX_W + 14
        W = self.width() - x0 - 10
        H = self.height()
        y_cl, y_ap, y_top, y_bot = (self._y(k) for k in ('clearance', 'approach', 'top', 'bottom'))
        y_base = H - 10
        cx = x0 + W * 0.42

        qp.setPen(_pen(INK, 1.0, Qt.DashLine))
        for y in (y_cl, y_ap):
            qp.drawLine(QPointF(x0 - 10, y), QPointF(x0 + W, y))
        qp.setPen(_pen(INK, 1.0))
        for y in (y_cl, y_ap, y_top, y_bot):
            qp.drawLine(QPointF(self.BOX_W + 4, y), QPointF(x0 - 2, y))
            _arrow(qp, QPointF(x0 - 1, y), (1, 0), 8)

        if self.p is None or self.k is None:
            _bebas(qp, 14)
            qp.setPen(_pen(QColor(150, 20, 10)))
            qp.drawText(QRectF(x0, y_top, W, y_bot - y_top), Qt.AlignCenter | Qt.TextWordWrap,
                        self.problem or 'INCOMPLETE DATA')
            return
        p, k = self.p, self.k
        depth = max(p.depth, 1e-9)
        y_of = lambda z: y_top + (y_bot - y_top) * (p.z_top - z) / depth

        x_block = W * 0.37
        hw = W * 0.15
        grad = QLinearGradient(0, y_top, 0, y_base)
        grad.setColorAt(0, PART_A)
        grad.setColorAt(1, PART_B)
        qp.setPen(_pen(INK, 1.4))
        qp.setBrush(QBrush(grad))
        qp.drawPolygon(QPolygonF([QPointF(cx - x_block, y_bot), QPointF(cx - hw, y_bot), QPointF(cx - hw, y_top),
                                  QPointF(cx + hw, y_top), QPointF(cx + hw, y_bot), QPointF(cx + x_block, y_bot),
                                  QPointF(cx + x_block, y_base), QPointF(cx - x_block, y_base)]))
        qp.setBrush(Qt.NoBrush)
        qp.setPen(_pen(INK, 1.0, Qt.DotLine))
        for sg in (1, -1):
            qp.drawPolyline(QPolygonF([QPointF(cx + sg * hw, y_top), QPointF(cx + sg * x_block, y_top),
                                       QPointF(cx + sg * x_block, y_bot)]))
        tool_px = max(hw * 0.14, min(hw * 0.5, hw * p.tool_diameter / max(
            p.diameter if p.circular else min(p.length, p.width), 1e-9)))
        sw_px = 6.0 if p.wall_stock > 0 else 0.0
        sf_px = 6.0 if p.floor_stock > 0 else 0.0
        if sw_px or sf_px:
            qp.setPen(_pen(PATH, 1.0, Qt.DotLine))
            yr = y_bot - sf_px
            for sg in (1, -1):
                qp.drawPolyline(QPolygonF([QPointF(cx + sg * (hw + sw_px), y_top), QPointF(cx + sg * (hw + sw_px), yr),
                                           QPointF(cx + sg * x_block, yr)]))

        levels = k['levels'][:-1] if k['finish_floor'] else k['levels']
        _bebas(qp, 11)
        for i, z in enumerate(levels):
            y = y_of(z) - (sf_px if i == len(levels) - 1 else 0)
            qp.setPen(_pen(PATH, 0.9, Qt.DashLine))
            for sg in (1, -1):
                qp.drawLine(QPointF(cx + sg * (hw + 2), y), QPointF(cx + sg * (x_block - 2), y))
            qp.setPen(_pen(PATH))
            qp.drawText(QPointF(cx - x_block + 4, y - 3), 'L{}'.format(i + 1))

        y_step = self._y('step')
        y1 = y_of(levels[0]) - (sf_px if len(levels) == 1 else 0)
        xs = cx - x_block - 12
        qp.setPen(_pen(INK, 1.0))
        qp.drawLine(QPointF(self.BOX_W + 4, y_step), QPointF(xs, y_step))
        qp.drawLine(QPointF(xs, y_step), QPointF(xs, y1))
        qp.drawLine(QPointF(xs, y1), QPointF(cx - x_block - 2, y1))
        _arrow(qp, QPointF(cx - x_block - 1, y1), (1, 0), 7)

        xe = cx + x_block + tool_px + 8
        qp.setPen(_pen(ENTRY, 1.6))
        qp.drawLine(QPointF(xe, y_ap), QPointF(xe, y1))
        qp.drawLine(QPointF(xe, y1), QPointF(cx + x_block - 4, y1))
        _arrow(qp, QPointF(cx + x_block - 6, y1), (-1, 0), 9)

        qp.setPen(_pen(INK, 0.8, Qt.DashDotLine))
        qp.drawLine(QPointF(cx, y_ap - 6), QPointF(cx, y_base - 2))
        flute_h = min((y_bot - y_top) * 0.75, tool_px * 3.2)
        _endmill_side(qp, cx + hw + tool_px, y_bot, tool_px, flute_h)

        xl = cx - hw - 14
        qp.setPen(_pen(INK, 1.0))
        qp.drawLine(QPointF(cx - hw - 3, y_top), QPointF(xl - 6, y_top))
        qp.drawLine(QPointF(xl, y_top), QPointF(xl, y_bot))
        _arrow(qp, QPointF(xl, y_top), (0, -1), 7)
        _arrow(qp, QPointF(xl, y_bot), (0, 1), 7)
        _bebas(qp, 14)
        qp.save()
        qp.translate(xl - 5, (y_top + y_bot) / 2.0)
        qp.rotate(-90)
        lt = 'DEPTH {}  ·  Z BOT {}'.format(_fmt(p.depth), _fmt(k['z_bottom']))
        qp.drawText(QPointF(-_text_w(qp, lt) / 2.0, 0), lt)
        qp.restore()

        qp.setPen(_pen(INK, 1.0))
        fm = qp.fontMetrics()
        for half, yy, text in ((hw, y_top - 14, contour_label(p).replace('ISLAND ', '')),
                               (x_block, y_top - 14 - fm.height() - 8,
                                'STOCK {} X {}'.format(_fmt(p.stock_x), _fmt(p.stock_y)))):
            for sg in (1, -1):
                qp.drawLine(QPointF(cx + sg * half, y_top - 3), QPointF(cx + sg * half, yy - 6))
            qp.drawLine(QPointF(cx - half, yy), QPointF(cx + half, yy))
            _arrow(qp, QPointF(cx - half, yy), (-1, 0), 7)
            _arrow(qp, QPointF(cx + half, yy), (1, 0), 7)
            tw = _text_w(qp, text)
            tx, ty = cx - tw / 2.0, yy - 5
            qp.fillRect(QRectF(tx - 3, ty - fm.ascent(), tw + 6, fm.height()), SCREEN)
            qp.drawText(QPointF(tx, ty), text)


class ContourPlanSketch(_PocketBase):
    """Scaled top-down contour toolpath preview."""
    PLAN = staticmethod(contour_plan)

    def __init__(self, parent=None):
        super(ContourPlanSketch, self).__init__(parent)
        self.setMinimumSize(300, 210)

    def paintEvent(self, ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing, True)
        if self.p is None or self.k is None:
            self._no_data(qp)
            return
        p, k = self.p, self.k
        g = k['g']
        text_h = 26
        ix, iy = island_extent(p)
        reach = g['o_max'] + g['rt']
        ext_x = max(p.stock_x / 2.0, ix + reach, k['start'][0] * abs(math.cos(g['ang'])) + g['rt']) * 1.04 + 1e-9
        ext_y = max(p.stock_y / 2.0, iy + reach, k['start'][0] * abs(math.sin(g['ang'])) + g['rt']) * 1.04 + 1e-9
        sc = min((self.width() - 8) / (2 * ext_x), (self.height() - text_h - 8) / (2 * ext_y))
        c = QPointF(self.width() / 2.0, 4 + (self.height() - text_h - 8) / 2.0)
        W = QTransform()
        W.translate(c.x(), c.y())
        W.scale(sc, -sc)
        T = QTransform(W)
        T.rotateRadians(g['ang'])

        def P(x, y):
            return T.map(QPointF(x, y))

        qp.setBrush(QBrush(SCREEN))
        qp.setPen(_pen(INK, 1.2, Qt.DashLine))
        qp.drawPolygon(W.map(QPolygonF(QRectF(-p.stock_x / 2.0, -p.stock_y / 2.0, p.stock_x, p.stock_y))))
        grad = QLinearGradient(0, c.y() - iy * sc, 0, c.y() + iy * sc)
        grad.setColorAt(0, PART_A)
        grad.setColorAt(1, PART_B)
        shape = QPainterPath()
        if p.circular:
            shape.addEllipse(QPointF(0, 0), g['R'], g['R'])
        else:
            shape.addRoundedRect(QRectF(-g['A'], -g['B'], 2 * g['A'], 2 * g['B']), g['rc'], g['rc'])
        qp.setBrush(QBrush(grad))
        qp.setPen(_pen(INK, 1.4))
        qp.drawPath(T.map(shape))
        qp.setBrush(Qt.NoBrush)
        _paths(qp, p, k, P)
        _origin(qp, P(0, 0))
        sx, sy = k['start']
        qp.setBrush(QBrush(ENTRY))
        qp.setPen(_pen(ENTRY, 1.0))
        qp.drawEllipse(P(sx, sy), 3.5, 3.5)
        base = g['R'] if p.circular else g['A']
        _endmill_plan(qp, P(base + (g['rt'] if k['finish_wall'] else g['o_rough']), 0), g['rt'] * sc, p.flutes)

        _bebas(qp, 15)
        qp.setPen(_pen(INK))
        txt = '{}  ·  {}  ·  {} PASS{}'.format('G3' if k['ccw'] else 'G2',
                                               'CLIMB' if p.climb else 'CONVENTIONAL',
                                               k['loops'], 'ES' if k['loops'] > 1 else '')
        qp.drawText(QRectF(0, self.height() - text_h, self.width(), text_h), Qt.AlignCenter, txt)

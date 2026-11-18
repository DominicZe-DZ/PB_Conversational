# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Raúl Milla (DominicZe)
"""
Schematic cross-section and scaled top-down toolpath sketches for thread milling.
Styled to match Probe Basic's built-in conversational artwork.
"""

import math

from qtpy.QtCore import Qt, QPointF, QRectF
from qtpy.QtGui import (QPainter, QPen, QBrush, QColor, QLinearGradient, QPolygonF,
                        QFont, QPainterPath)
from qtpy.QtWidgets import QWidget, QSizePolicy

from thread_mill_gen import plan, thread_label

PART_A = QColor(222, 222, 216)
PART_B = QColor(196, 196, 188)
TOOL_FILL = QColor(170, 176, 184)
TOOL_TEETH = QColor(120, 128, 140)
INK = QColor(0, 0, 0)
PATH = QColor(36, 36, 200)
ENTRY = QColor(0, 130, 40)
WARN = QColor(150, 20, 10)
BEBAS = 'Bebas Kai'


def _pen(color=INK, w=1.3, style=Qt.SolidLine):
    p = QPen(color, w, style)
    p.setCapStyle(Qt.FlatCap)
    p.setJoinStyle(Qt.MiterJoin)
    return p


def _arrow(qp, tip, d, size=8):
    dx, dy = d
    ln = math.hypot(dx, dy) or 1.0
    dx, dy = dx / ln, dy / ln
    a = QPointF(tip.x() - dx * size - dy * size * 0.33, tip.y() - dy * size + dx * size * 0.33)
    b = QPointF(tip.x() - dx * size + dy * size * 0.33, tip.y() - dy * size - dx * size * 0.33)
    old = qp.brush()
    qp.setBrush(QBrush(qp.pen().color()))
    qp.drawPolygon(QPolygonF([tip, a, b]))
    qp.setBrush(old)


def _bebas(qp, pt):
    f = QFont(BEBAS)
    f.setPointSizeF(pt)
    qp.setFont(f)


def _fmt(v, dec=3):
    s = ('{:.' + str(dec) + 'f}').format(v).rstrip('0').rstrip('.')
    return '0' if s in ('', '-0') else s


class _Base(QWidget):
    def __init__(self, parent=None):
        super(_Base, self).__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.p = None
        self.k = None
        self.problem = None

    def set_params(self, p, problem=None):
        self.p, self.k, self.problem = p, None, problem
        if p is not None:
            try:
                self.k = plan(p)
            except Exception as e:
                self.problem = self.problem or str(e)
        self.update()

    def _no_data(self, qp):
        _bebas(qp, 14)
        qp.setPen(_pen(WARN))
        qp.drawText(QRectF(self.rect()), Qt.AlignCenter | Qt.TextWordWrap,
                    self.problem or 'INCOMPLETE DATA')


class SectionSketch(_Base):
    """Cross-section sketch (not to scale) with Z value boxes aligned to their reference lines."""
    BOX_W = 140
    BOX_H = 45
    FR = dict(clearance=0.07, approach=0.19, top=0.31, bottom=0.74, hole=0.88)

    def __init__(self, boxes, parent=None):
        super(SectionSketch, self).__init__(parent)
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
        super(SectionSketch, self).resizeEvent(ev)

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
        y_hole = self._y('hole')

        if self.p is None or self.k is None:
            self._no_data(qp)
            return
        p, k = self.p, self.k

        grad = QLinearGradient(0, y_top, 0, y_base)
        grad.setColorAt(0, PART_A)
        grad.setColorAt(1, PART_B)

        n_vis = int(max(4, min(12, round((p.z_top - p.z_bottom) / p.pitch))))
        pitch_px = (y_bot - y_top) / float(n_vis)
        ratio_tool = max(0.25, min(0.85, p.tool_diameter / max(p.diameter, 1e-6)))

        if p.internal:
            hw_crest = W * 0.16
            depth_px = max(5.0, min(hw_crest * 0.28, hw_crest * p.depth() / max(k['start_d'] / 2.0, 1e-6)))
            hw_root = hw_crest + depth_px
            tr_px = hw_root * ratio_tool
            xc_tool = cx + hw_root - tr_px
            x_block = W * 0.42
            y_drill = y_bot + (y_base - y_bot) * 0.62
            tip_px = min(hw_crest * 0.58, (y_base - y_drill) * 0.8)
            qp.setPen(_pen(INK, 1.4))
            qp.setBrush(QBrush(grad))
            for sg in (1, -1):
                poly = [QPointF(cx + sg * x_block, y_top), QPointF(cx + sg * hw_crest, y_top)]
                up = True
                y = y_top
                while y < y_bot - 1e-6:
                    y = min(y + pitch_px / 2.0, y_bot)
                    up = not up
                    poly.append(QPointF(cx + sg * (hw_crest if up else hw_root), y))
                poly.append(QPointF(cx + sg * hw_crest, y_bot))
                poly += [QPointF(cx + sg * hw_crest, y_drill), QPointF(cx, y_drill + tip_px),
                         QPointF(cx, y_base), QPointF(cx + sg * x_block, y_base)]
                qp.drawPolygon(QPolygonF(poly))
            qp.setPen(_pen(INK, 0.9, Qt.DashLine))
            for sg in (1, -1):
                qp.drawLine(QPointF(cx + sg * hw_root, y_top), QPointF(cx + sg * hw_root, y_bot))
            if k.get('chamfer'):
                x_out = hw_root + depth_px * 2.0
                ch_d = (x_out - hw_crest) * math.tan(math.radians(30.0))
                qp.setPen(Qt.NoPen)
                qp.setBrush(QBrush(QColor(146, 150, 149)))
                for sg in (1, -1):
                    qp.drawPolygon(QPolygonF([QPointF(cx + sg * hw_crest, y_top + ch_d), QPointF(cx + sg * x_out, y_top),
                                              QPointF(cx + sg * hw_crest, y_top - 1)]))
                qp.setPen(_pen(INK, 1.4))
                for sg in (1, -1):
                    qp.drawLine(QPointF(cx + sg * hw_crest, y_top + ch_d), QPointF(cx + sg * x_out, y_top))
                qp.setBrush(Qt.NoBrush)
            r_path_px = hw_root - tr_px
            dim_hw = hw_root
            if getattr(p, 'drill_enabled', False):
                from thread_mill_gen import drill_tip_z
                _bebas(qp, 13)
                qp.setPen(_pen(INK, 1.0))
                tip_pt = QPointF(cx, y_drill + tip_px)
                lab_pt = QPointF(cx + hw_crest + 14, y_drill + tip_px + 4)
                qp.drawLine(tip_pt, QPointF(lab_pt.x() - 3, lab_pt.y() - 5))
                dl = 'DRILL D{} · Z{}'.format(_fmt(p.drill_diameter, 3 if p.units == 'MM' else 4),
                                             _fmt(drill_tip_z(p), 2 if p.units == 'MM' else 4))
                fm = qp.fontMetrics()
                dw = fm.horizontalAdvance(dl) if hasattr(fm, 'horizontalAdvance') else fm.width(dl)
                qp.fillRect(QRectF(lab_pt.x() - 2, lab_pt.y() - fm.ascent(), dw + 4, fm.height()), QColor(146, 150, 149))
                qp.drawText(lab_pt, dl)
            qp.setPen(_pen(INK, 1.0))
            qp.drawLine(QPointF(self.BOX_W + 4, y_hole), QPointF(cx - hw_crest - 2, y_hole))
            _arrow(qp, QPointF(cx - hw_crest - 1, y_hole), (1, 0), 8)
        else:
            hw_crest = W * 0.15
            depth_px = max(5.0, min(hw_crest * 0.28, hw_crest * p.depth() / max(k['start_d'] / 2.0, 1e-6)))
            hw_root = hw_crest - depth_px
            tr_px = hw_crest * ratio_tool * 1.1
            xc_tool = cx + hw_root + tr_px
            y_shoulder = y_bot + (y_base - y_bot) * 0.62
            x_block = W * 0.42
            qp.setPen(_pen(INK, 1.4))
            qp.setBrush(QBrush(grad))
            poly = [QPointF(cx - x_block, y_base), QPointF(cx - x_block, y_shoulder),
                    QPointF(cx - hw_crest, y_shoulder), QPointF(cx - hw_crest, y_bot)]
            left = []
            up = True
            y = y_bot
            while y > y_top + 1e-6:
                y = max(y - pitch_px / 2.0, y_top)
                up = not up
                left.append(QPointF(cx - (hw_crest if up else hw_root), y))
            poly += left
            poly.append(QPointF(cx - hw_crest, y_top))
            poly.append(QPointF(cx + hw_crest, y_top))
            right = [QPointF(2 * cx - pt.x(), pt.y()) for pt in reversed(left)]
            poly += right
            poly += [QPointF(cx + hw_crest, y_bot), QPointF(cx + hw_crest, y_shoulder),
                     QPointF(cx + x_block, y_shoulder), QPointF(cx + x_block, y_base)]
            qp.drawPolygon(QPolygonF(poly))
            qp.setPen(_pen(INK, 0.9, Qt.DashLine))
            for sg in (1, -1):
                qp.drawLine(QPointF(cx + sg * hw_root, y_top), QPointF(cx + sg * hw_root, y_bot))
            r_path_px = hw_root + tr_px
            dim_hw = hw_crest
            qp.setPen(_pen(INK, 1.0))
            qp.drawLine(QPointF(self.BOX_W + 4, y_hole), QPointF(cx - hw_crest - 2, y_hole))
            _arrow(qp, QPointF(cx - hw_crest - 1, y_hole), (1, 0), 8)

        qp.setPen(_pen(INK, 0.8, Qt.DashDotLine))
        qp.drawLine(QPointF(cx, y_ap - 6), QPointF(cx, y_base - 2))

        turns_vis = n_vis
        qp.setPen(_pen(PATH, 1.3, Qt.DashLine))
        qp.save()
        if not p.internal:
            clip = QPainterPath()
            clip.addRect(QRectF(self.rect()))
            body = QPainterPath()
            body.addRect(QRectF(cx - hw_crest, y_top - 2, 2 * hw_crest, y_bot - y_top + 4))
            qp.setClipPath(clip.subtracted(body))
        path = QPainterPath()
        steps = turns_vis * 40
        ya, yb = (y_bot, y_top) if k['dirz'] > 0 else (y_top, y_bot)
        for i in range(steps + 1):
            t = i / float(steps)
            x = cx + r_path_px * math.cos(t * turns_vis * 2 * math.pi)
            y = ya + (yb - ya) * t
            (path.moveTo if i == 0 else path.lineTo)(QPointF(x, y))
        qp.drawPath(path)
        qp.restore()

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
        lt = 'L {}  ·  Z BOT {}'.format(_fmt(p.z_top - p.z_bottom), _fmt(p.z_bottom))
        fm0 = qp.fontMetrics()
        tw0 = fm0.horizontalAdvance(lt) if hasattr(fm0, 'horizontalAdvance') else fm0.width(lt)
        qp.drawText(QPointF(-tw0 / 2.0, 0), lt)
        qp.restore()

        _bebas(qp, 14)
        y_d = y_top - 14
        qp.setPen(_pen(INK, 1.0))
        for sg in (1, -1):
            qp.drawLine(QPointF(cx + sg * dim_hw, y_top - 3), QPointF(cx + sg * dim_hw, y_d - 6))
        qp.drawLine(QPointF(cx - dim_hw, y_d), QPointF(cx + dim_hw, y_d))
        _arrow(qp, QPointF(cx - dim_hw, y_d), (-1, 0), 7)
        _arrow(qp, QPointF(cx + dim_hw, y_d), (1, 0), 7)
        label = thread_label(p)
        fm = qp.fontMetrics()
        tw = fm.horizontalAdvance(label) if hasattr(fm, 'horizontalAdvance') else fm.width(label)
        qp.drawText(QPointF(max(x0 + 2, cx - dim_hw - tw - 8), y_d + fm.ascent() / 2.0 - 2), label)

        n_t = int(max(1, min(p.teeth, n_vis)))
        y_low = (y_bot - pitch_px * 0.25) if k['dirz'] > 0 else (y_top + pitch_px * (n_t - 1) + pitch_px * 0.25)
        y_low = min(y_low, y_bot - pitch_px * 0.25)
        neck = max(tr_px * 0.45, tr_px - depth_px * 1.25)
        shank = max(neck, tr_px * 0.8)
        y_teeth_top = y_low - (n_t - 1) * pitch_px - pitch_px * 0.45
        y_tip = y_low + pitch_px * 0.45 + (6 if p.tooth_offset > 0 else 0)
        qp.setPen(_pen(INK, 1.2))
        qp.setBrush(QBrush(TOOL_FILL))
        qp.drawRect(QRectF(QPointF(xc_tool - shank, 0), QPointF(xc_tool + shank, y_teeth_top - pitch_px * 0.8)))
        qp.drawRect(QRectF(QPointF(xc_tool - neck, y_teeth_top - pitch_px * 0.8), QPointF(xc_tool + neck, y_tip)))
        qp.setBrush(QBrush(TOOL_TEETH))
        for i in range(n_t):
            yt = y_low - i * pitch_px
            for sg in (1, -1):
                qp.drawPolygon(QPolygonF([QPointF(xc_tool + sg * neck, yt - pitch_px * 0.42),
                                          QPointF(xc_tool + sg * tr_px, yt),
                                          QPointF(xc_tool + sg * neck, yt + pitch_px * 0.42)]))
        qp.setPen(_pen(INK, 0.8, Qt.DashDotLine))
        qp.drawLine(QPointF(xc_tool, 2), QPointF(xc_tool, y_tip + 5))

        ax = xc_tool + tr_px + 16 if (xc_tool + tr_px + 30) < x0 + W else xc_tool - tr_px - 16
        ym = (y_top + y_bot) / 2.0
        d = pitch_px * 1.6 * (-1 if k['dirz'] > 0 else 1)
        qp.setPen(_pen(PATH, 1.6))
        qp.drawLine(QPointF(ax, ym - d), QPointF(ax, ym + d))
        _arrow(qp, QPointF(ax, ym + d), (0, d), 9)


class PlanSketch(_Base):
    """Scaled top-down view showing passes, tangential lead-in, cutter, and helix direction."""

    def __init__(self, parent=None):
        super(PlanSketch, self).__init__(parent)
        self.setMinimumSize(300, 210)

    def paintEvent(self, ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing, True)
        if self.p is None or self.k is None:
            self._no_data(qp)
            return
        p, k = self.p, self.k
        tr, rp, s = k['tr'], k['r_final'], k['s']
        D = p.diameter
        text_h = 26
        side = min(self.width(), self.height() - text_h) - 8
        c = QPointF(self.width() / 2.0, 4 + side / 2.0)
        if p.internal:
            extent = D / 2.0 * 1.45
        else:
            re = (k['start_d'] - k['final_d']) / 2.0 + k['clear_gap'] + tr / 2.0
            extent = max(rp + re + tr * 0.6, rp + tr) * 1.05
        sc = side / (2.0 * extent)

        def P(x, y):
            return QPointF(c.x() + x * sc, c.y() - y * sc)

        grad = QLinearGradient(0, c.y() - side / 2, 0, c.y() + side / 2)
        grad.setColorAt(0, PART_A)
        grad.setColorAt(1, PART_B)
        qp.setPen(_pen(INK, 1.4))
        qp.setBrush(QBrush(grad))
        if p.internal:
            qp.drawRect(QRectF(P(-extent * 0.97, extent * 0.97), P(extent * 0.97, -extent * 0.97)))
            hole = QColor(146, 150, 149)
            qp.setBrush(QBrush(hole))
            r = k['start_d'] / 2.0 * sc
            qp.drawEllipse(c, r, r)
        else:
            r = k['start_d'] / 2.0 * sc
            qp.drawEllipse(c, r, r)
        qp.setBrush(Qt.NoBrush)
        qp.setPen(_pen(INK, 0.9, Qt.DashLine))
        r = k['final_d'] / 2.0 * sc
        qp.drawEllipse(c, r, r)

        qp.setPen(_pen(INK, 0.8, Qt.DashDotLine))
        L = extent * 0.92 * sc
        qp.drawLine(QPointF(c.x() - L, c.y()), QPointF(c.x() + L, c.y()))
        qp.drawLine(QPointF(c.x(), c.y() - L), QPointF(c.x(), c.y() + L))

        qp.setPen(_pen(PATH, 0.9, Qt.DotLine))
        for r_ in k['radii'][:-1]:
            qp.drawEllipse(c, r_ * sc, r_ * sc)
        qp.setPen(_pen(PATH, 1.5, Qt.DashLine))
        qp.drawEllipse(c, rp * sc, rp * sc)
        qp.setPen(_pen(PATH, 1.5))
        for ang in (math.pi * 0.5, math.pi * 1.5):
            pt = P(rp * math.cos(ang), rp * math.sin(ang))
            _arrow(qp, pt, (-math.sin(ang) * s, -math.cos(ang) * s), 11)

        if p.internal:
            ce, re, kd, sweep = (rp / 2.0, 0.0), rp / 2.0, s, math.pi
        else:
            re = (k['start_d'] - k['final_d']) / 2.0 + k['clear_gap'] + tr / 2.0
            ce, kd, sweep = (rp + re, 0.0), -s, math.pi / 2
        a_end = math.pi
        a_start = a_end - kd * sweep
        path = QPainterPath()
        for i in range(41):
            a = a_start + kd * sweep * i / 40.0
            pt = P(ce[0] + re * math.cos(a), ce[1] + re * math.sin(a))
            if i == 0:
                path.moveTo(pt)
                p0 = pt
            else:
                path.lineTo(pt)
        qp.setPen(_pen(ENTRY, 1.8))
        qp.drawPath(path)
        qp.setBrush(QBrush(ENTRY))
        qp.drawEllipse(p0, 3.5, 3.5)
        qp.setBrush(Qt.NoBrush)

        col = QColor(TOOL_FILL)
        col.setAlpha(170)
        qp.setPen(_pen(INK, 1.1))
        qp.setBrush(QBrush(col))
        qp.drawEllipse(P(rp, 0), tr * sc, tr * sc)
        z = int(max(1, min(getattr(p, 'flutes', 3), 12)))
        tc = P(rp, 0)
        R = tr * sc
        qp.setBrush(QBrush(QColor(146, 150, 149)))
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

        _bebas(qp, 15)
        qp.setPen(_pen(INK))
        txt = '{} {}  ·  {}'.format(k['helix_g'], 'CCW' if s > 0 else 'CW',
                                   'UP' if k['dirz'] > 0 else 'DOWN')
        qp.drawText(QRectF(0, self.height() - text_h, self.width(), text_h), Qt.AlignCenter, txt)

"""Cairo/Pango overlay renderer, migrated from the original GPLv3 overlay.

Owns drawing only; callers own the graphical thread, state and cache lifecycles.
"""

from __future__ import annotations

import colorsys
import hashlib
import importlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Protocol, cast

from vencoverlay.config import Configuration
from vencoverlay.image_decode import Pixbuf
from vencoverlay.overlay_state import TOAST_LIFETIME_SECONDS, AnimatedParticipant, OverlayState
from vencoverlay.protocol import Channel, Participant

RGB = tuple[float, float, float]
RGBA = tuple[float, float, float, float]


class _Pattern(Protocol):
    def set_filter(self, value: object) -> None: ...


class _Gradient(Protocol):
    def add_color_stop_rgba(
        self, offset: float, r: float, g: float, b: float, a: float
    ) -> None: ...


class CairoContext(Protocol):
    def new_path(self) -> None: ...
    def new_sub_path(self) -> None: ...
    def close_path(self) -> None: ...
    def arc(self, x: float, y: float, radius: float, start: float, end: float) -> None: ...
    def arc_negative(self, x: float, y: float, radius: float, start: float, end: float) -> None: ...
    def rectangle(self, x: float, y: float, w: float, h: float) -> None: ...
    def move_to(self, x: float, y: float) -> None: ...
    def line_to(self, x: float, y: float) -> None: ...
    def set_source_rgba(self, r: float, g: float, b: float, a: float) -> None: ...
    def set_source(self, pattern: _Gradient) -> None: ...
    def get_source(self) -> _Pattern: ...
    def set_line_width(self, value: float) -> None: ...
    def set_line_cap(self, value: object) -> None: ...
    def set_operator(self, value: object) -> None: ...
    def fill(self) -> None: ...
    def fill_preserve(self) -> None: ...
    def stroke(self) -> None: ...
    def paint(self) -> None: ...
    def paint_with_alpha(self, alpha: float) -> None: ...
    def clip(self) -> None: ...
    def save(self) -> None: ...
    def restore(self) -> None: ...
    def translate(self, x: float, y: float) -> None: ...
    def scale(self, x: float, y: float) -> None: ...


class TextLayout(Protocol):
    def set_font_description(self, description: _FontDescription) -> None: ...
    def set_text(self, text: str, length: int) -> None: ...
    def set_ellipsize(self, mode: object) -> None: ...
    def set_width(self, width: int) -> None: ...
    def set_alignment(self, alignment: object) -> None: ...
    def get_pixel_size(self) -> tuple[int, int]: ...


class _FontDescription(Protocol):
    def set_absolute_size(self, size: float) -> None: ...
    def set_weight(self, weight: object) -> None: ...


class _Weights(Protocol):
    SEMIBOLD: object
    BOLD: object
    MEDIUM: object
    NORMAL: object


class _Ellipsize(Protocol):
    END: object


class _Alignment(Protocol):
    CENTER: object


class _Pango(Protocol):
    SCALE: int
    Weight: _Weights
    EllipsizeMode: _Ellipsize
    Alignment: _Alignment

    def FontDescription(self, family: str) -> _FontDescription: ...


class _PangoCairo(Protocol):
    def create_layout(self, cr: CairoContext) -> TextLayout: ...
    def show_layout(self, cr: CairoContext, layout: TextLayout) -> None: ...


class _Gdk(Protocol):
    def cairo_set_source_pixbuf(
        self, cr: CairoContext, pix: Pixbuf, x: float, y: float
    ) -> None: ...


class _Cairo(Protocol):
    OPERATOR_SOURCE: object
    OPERATOR_OVER: object
    LINE_CAP_ROUND: object
    FILTER_GOOD: object

    def LinearGradient(self, x0: float, y0: float, x1: float, y1: float) -> _Gradient: ...


class _GI(Protocol):
    def require_version(self, namespace: str, version: str) -> None: ...
    def require_foreign(self, namespace: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DrawingAPI:
    cairo: _Cairo
    pango: _Pango
    pangocairo: _PangoCairo
    gdk: _Gdk

    @classmethod
    def load(cls) -> DrawingAPI:
        """Load native libraries only after the application configures its backend."""
        gi = cast(_GI, importlib.import_module("gi"))
        for namespace, version in (
            ("Gdk", "3.0"),
            ("GdkPixbuf", "2.0"),
            ("Pango", "1.0"),
            ("PangoCairo", "1.0"),
        ):
            gi.require_version(namespace, version)
        gi.require_foreign("cairo")
        return cls(
            cast(_Cairo, importlib.import_module("cairo")),
            cast(_Pango, importlib.import_module("gi.repository.Pango")),
            cast(_PangoCairo, importlib.import_module("gi.repository.PangoCairo")),
            cast(_Gdk, importlib.import_module("gi.repository.Gdk")),
        )


def hex_rgb(value: str) -> RGB:
    v = value.lstrip("#")
    return int(v[:2], 16) / 255, int(v[2:4], 16) / 255, int(v[4:6], 16) / 255


def rounded(cr: CairoContext, x: float, y: float, w: float, h: float, r: float) -> None:
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def initials(name: str) -> str:
    return "".join(part[0] for part in name.split()[:2]).upper() or "?"


def name_color(name: str) -> RGB:
    h = int(hashlib.md5(name.encode(), usedforsecurity=False).hexdigest()[:6], 16)
    return colorsys.hsv_to_rgb((h % 360) / 360, 0.45, 0.75)


def frame_ratio(frame: Pixbuf | None) -> float:
    return frame.get_height() / frame.get_width() if frame is not None else 9 / 16


def block_origin(
    position: str, width: int, height: int, block_width: float, block_height: float, margin: int
) -> tuple[float, float]:
    x = width - margin - block_width if "right" in position else float(margin)
    if position.startswith("top"):
        y = float(margin)
    elif position.startswith("bottom"):
        y = height - margin - block_height
    else:
        y = (height - block_height) / 2
    return x, max(margin, y)


class OverlayRenderer:
    """Render the original Discord and Mac styles from the typed application state."""

    def __init__(
        self,
        config: Configuration,
        state: OverlayState,
        avatar: Callable[[str | None], Pixbuf | None],
        frame: Callable[[str], Pixbuf | None],
        *,
        clock: Callable[[], float] = monotonic,
        native: DrawingAPI | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.avatar = avatar
        self.frame = frame
        self.clock = clock
        self.native = native if native is not None else DrawingAPI.load()

    def participants(self) -> tuple[list[AnimatedParticipant], list[AnimatedParticipant]]:
        ordered = [self.state.people[i] for i in self.state.order if i in self.state.people]
        people = [p for p in ordered if p.alpha > 0.01]
        cams = [p for p in ordered if p.data.video and self.state.preferences.show_webcams]
        return people, cams

    @property
    def needs_continuous_redraw(self) -> bool:
        """The Mac header pulse changes even after speaking glow has settled."""
        return (
            self.state.visible
            and self.state.preferences.style == "mac"
            and any(p.data.speaking for p in self.state.people.values())
        )

    def layout_text(
        self, cr: CairoContext, text: str, size: float, weight: object | None = None
    ) -> TextLayout:
        layout = self.native.pangocairo.create_layout(cr)
        desc = self.native.pango.FontDescription(f"{self.config.font}, Sans")
        desc.set_absolute_size(size * self.native.pango.SCALE)
        desc.set_weight(self.native.pango.Weight.SEMIBOLD if weight is None else weight)
        layout.set_font_description(desc)
        layout.set_text(text, -1)
        layout.set_ellipsize(self.native.pango.EllipsizeMode.END)
        return layout

    def glass(
        self,
        cr: CairoContext,
        x: float,
        y: float,
        w: float,
        h: float,
        r: float,
        alpha_mul: float = 1.0,
    ) -> None:
        op = self.state.preferences.opacity * alpha_mul
        # soft shadow
        for i in range(6, 0, -1):
            rounded(cr, x - i, y - i + 3, w + 2 * i, h + 2 * i, r + i)
            cr.set_source_rgba(0, 0, 0, 0.025 * alpha_mul)
            cr.fill()
        rounded(cr, x, y, w, h, r)
        grad = self.native.cairo.LinearGradient(0, y, 0, y + h)
        grad.add_color_stop_rgba(0, 0.20, 0.20, 0.23, op)
        grad.add_color_stop_rgba(1, 0.11, 0.11, 0.13, op)
        cr.set_source(grad)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.13 * alpha_mul)
        cr.set_line_width(1)
        cr.stroke()
        # top highlight line, like macOS materials
        rounded(cr, x + 1, y + 1, w - 2, h - 2, r - 1)
        hl = self.native.cairo.LinearGradient(0, y, 0, y + h * 0.5)
        hl.add_color_stop_rgba(0, 1, 1, 1, 0.07 * alpha_mul)
        hl.add_color_stop_rgba(1, 1, 1, 1, 0)
        cr.set_source(hl)
        cr.fill()

    def draw_avatar(
        self,
        cr: CairoContext,
        p: AnimatedParticipant,
        cx: float,
        cy: float,
        radius: float,
        alpha: float,
    ) -> None:
        # Text drawing leaves a "current point" behind; without this the ring's
        # arc would start with a stray line from the previous row's label.
        cr.new_path()
        accent = hex_rgb(self.state.preferences.accent)
        if p.glow > 0.01 and self.state.preferences.style == "discord":
            cr.arc(cx, cy, radius + 1.5, 0, 2 * math.pi)
            cr.set_source_rgba(*accent, p.glow * alpha)
            cr.set_line_width(max(2.0, radius * 0.14))
            cr.stroke()
        elif p.glow > 0.01:
            for i in range(4, 0, -1):
                cr.arc(cx, cy, radius + 1.5 + i * 1.6 * p.glow, 0, 2 * math.pi)
                cr.set_source_rgba(*accent, 0.12 * p.glow * alpha)
                cr.fill()
            cr.arc(cx, cy, radius + 2, 0, 2 * math.pi)
            cr.set_source_rgba(*accent, p.glow * alpha)
            cr.set_line_width(2.2)
            cr.stroke()
        pix = self.avatar(p.data.avatar)
        cr.save()
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.clip()
        if pix is not None:
            s = max(radius * 2 / pix.get_width(), radius * 2 / pix.get_height())
            cr.translate(cx - pix.get_width() * s / 2, cy - pix.get_height() * s / 2)
            cr.scale(s, s)
            self.native.gdk.cairo_set_source_pixbuf(cr, pix, 0, 0)
            cr.paint_with_alpha(alpha)
        else:
            cr.set_source_rgba(*name_color(p.data.name), alpha)
            cr.paint()
            lay = self.layout_text(
                cr, initials(p.data.name), radius * 0.8, self.native.pango.Weight.BOLD
            )
            w, h = lay.get_pixel_size()
            cr.move_to(cx - w / 2, cy - h / 2)
            cr.set_source_rgba(1, 1, 1, alpha)
            self.native.pangocairo.show_layout(cr, lay)
        cr.restore()

    def icon_mic_off(self, cr: CairoContext, x: float, y: float, s: float, rgba: RGBA) -> None:
        cr.new_path()
        cr.set_source_rgba(*rgba)
        cr.set_line_width(s * 0.12)
        cr.set_line_cap(self.native.cairo.LINE_CAP_ROUND)
        rounded(cr, x + s * 0.35, y + s * 0.08, s * 0.3, s * 0.52, s * 0.15)
        cr.fill()
        cr.arc_negative(x + s * 0.5, y + s * 0.45, s * 0.28, math.pi, 0)
        cr.stroke()
        cr.move_to(x + s * 0.5, y + s * 0.73)
        cr.line_to(x + s * 0.5, y + s * 0.9)
        cr.stroke()
        self.slash(cr, x, y, s)

    def icon_deaf(self, cr: CairoContext, x: float, y: float, s: float, rgba: RGBA) -> None:
        cr.new_path()
        cr.set_source_rgba(*rgba)
        cr.set_line_width(s * 0.12)
        cr.set_line_cap(self.native.cairo.LINE_CAP_ROUND)
        cr.arc(x + s * 0.5, y + s * 0.55, s * 0.36, math.pi, 2 * math.pi)
        cr.stroke()
        rounded(cr, x + s * 0.1, y + s * 0.5, s * 0.2, s * 0.35, s * 0.06)
        cr.fill()
        rounded(cr, x + s * 0.7, y + s * 0.5, s * 0.2, s * 0.35, s * 0.06)
        cr.fill()
        self.slash(cr, x, y, s)

    def slash(self, cr: CairoContext, x: float, y: float, s: float) -> None:
        cr.set_line_width(s * 0.13)
        cr.move_to(x + s * 0.12, y + s * 0.08)
        cr.line_to(x + s * 0.88, y + s * 0.92)
        cr.stroke()

    def icon_cam(self, cr: CairoContext, x: float, y: float, s: float, rgba: RGBA) -> None:
        cr.new_path()
        cr.set_source_rgba(*rgba)
        rounded(cr, x + s * 0.05, y + s * 0.25, s * 0.62, s * 0.5, s * 0.1)
        cr.fill()
        cr.move_to(x + s * 0.7, y + s * 0.5)
        cr.line_to(x + s * 0.95, y + s * 0.3)
        cr.line_to(x + s * 0.95, y + s * 0.7)
        cr.close_path()
        cr.fill()

    def badge(
        self, cr: CairoContext, x: float, y: float, text: str, s: float, rgba: RGB, alpha: float
    ) -> float:
        lay = self.layout_text(cr, text, s * 0.62, self.native.pango.Weight.BOLD)
        w, h = lay.get_pixel_size()
        bw = w + s * 0.6
        rounded(cr, x - bw, y, bw, s, s / 2)
        cr.set_source_rgba(*rgba, alpha)
        cr.fill()
        cr.move_to(x - bw + (bw - w) / 2, y + (s - h) / 2)
        cr.set_source_rgba(1, 1, 1, alpha)
        self.native.pangocairo.show_layout(cr, lay)
        return bw

    def draw(self, cr: CairoContext, width: int, height: int) -> None:
        """Draw into the current window allocation, in logical pixels."""
        cr.new_path()
        cr.set_operator(self.native.cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(self.native.cairo.OPERATOR_OVER)
        channel = self.state.channel
        if not self.state.visible or channel is None:
            return
        now = self.clock()
        if self.state.preferences.style == "discord":
            self.draw_discord(cr, width, height, now)
        else:
            self.draw_mac(cr, width, height, channel, now)
        cr.new_path()

    def draw_mac(
        self, cr: CairoContext, width: int, height: int, channel: Channel, now: float
    ) -> None:
        sc = float(self.state.preferences.scale)
        margin = self.config.margin
        listw = 250 * sc
        row_h = 42 * sc
        gap = 6 * sc
        header_h = 34 * sc
        cam_w = float(self.state.preferences.webcam_width) * sc

        people, cams = self.participants()

        # Measure first so the whole block can sit in any corner.
        h_list = header_h + gap + sum((row_h + gap) * p.alpha for p in people)
        cam_frames = [self.frame(p.data.id) for p in cams]
        cam_heights = [cam_w * frame_ratio(frame) for frame in cam_frames]
        h_cams = sum(h + gap for h in cam_heights)
        h_toasts = len(self.state.toasts) * (34 * sc + gap)
        total_h = h_toasts + h_list + (gap * 2 + h_cams if cams else 0)
        total_w = max(listw, cam_w if cams else 0)

        pos = self.state.preferences.position
        x0, y = block_origin(pos, width, height, total_w, total_h, margin)
        right = "right" in pos

        # toasts
        for t in self.state.toasts:
            age = max(0.0, now - t.born)
            a = min(1, age * 5) * min(1, max(0, (TOAST_LIFETIME_SECONDS - age) * 2))
            tw = listw
            tx = (
                x0
                + (total_w - tw if right else 0)
                + (1 - min(1, age * 5)) * (-20 if not right else 20)
            )
            th = 34 * sc
            self.glass(cr, tx, y, tw, th, th / 2, a)
            dot = (0.19, 0.82, 0.35) if t.message.kind == "join" else (1, 0.27, 0.23)
            cr.new_path()
            cr.arc(tx + 14 * sc, y + th / 2, 4 * sc, 0, 2 * math.pi)
            cr.set_source_rgba(*dot, a)
            cr.fill()
            lay = self.layout_text(cr, t.message.text, 13 * sc)
            lay.set_width(int((tw - 34 * sc) * self.native.pango.SCALE))
            _, lh = lay.get_pixel_size()
            cr.move_to(tx + 26 * sc, y + (th - lh) / 2)
            cr.set_source_rgba(1, 1, 1, a)
            self.native.pangocairo.show_layout(cr, lay)
            y += th + gap

        lx = x0 + (total_w - listw if right else 0)

        # header pill
        self.glass(cr, lx, y, listw, header_h, header_h / 2)
        accent = hex_rgb(self.state.preferences.accent)
        anyone = any(p.data.speaking for p in self.state.people.values())
        pulse = 0.55 + 0.45 * math.sin(now * 4) if anyone else 1
        cr.arc(lx + 16 * sc, y + header_h / 2, 4.5 * sc, 0, 2 * math.pi)
        cr.set_source_rgba(*accent, pulse)
        cr.fill()
        title = channel.name or "Voice"
        lay = self.layout_text(cr, title, 13.5 * sc, self.native.pango.Weight.BOLD)
        count = self.layout_text(
            cr, str(len(self.state.people)), 12 * sc, self.native.pango.Weight.SEMIBOLD
        )
        cw, ch = count.get_pixel_size()
        lay.set_width(int((listw - 52 * sc - cw) * self.native.pango.SCALE))
        _, lh = lay.get_pixel_size()
        cr.move_to(lx + 28 * sc, y + (header_h - lh) / 2)
        cr.set_source_rgba(1, 1, 1, 0.95)
        self.native.pangocairo.show_layout(cr, lay)
        cr.move_to(lx + listw - 14 * sc - cw, y + (header_h - ch) / 2)
        cr.set_source_rgba(1, 1, 1, 0.55)
        self.native.pangocairo.show_layout(cr, count)
        y += header_h + gap

        # people
        for p in people:
            a = p.alpha
            rh = row_h
            self.glass(cr, lx, y, listw, rh, 13 * sc, a)
            if p.glow > 0.01:
                rounded(cr, lx, y, listw, rh, 13 * sc)
                cr.set_source_rgba(*accent, 0.55 * p.glow * a)
                cr.set_line_width(1.6)
                cr.stroke()
            rad = 14 * sc
            self.draw_avatar(cr, p, lx + 10 * sc + rad, y + rh / 2, rad, a)

            # status icons from the right edge
            ix = lx + listw - 10 * sc
            isz = 16 * sc
            red = (1, 0.27, 0.23, a)
            grey = (1, 1, 1, 0.75 * a)
            if p.data.deaf:
                ix -= isz
                self.icon_deaf(cr, ix, y + (rh - isz) / 2, isz, red)
                ix -= 5 * sc
            elif p.data.mute:
                ix -= isz
                self.icon_mic_off(cr, ix, y + (rh - isz) / 2, isz, red)
                ix -= 5 * sc
            if p.data.video:
                ix -= isz
                self.icon_cam(cr, ix, y + (rh - isz) / 2, isz, grey)
                ix -= 5 * sc
            if p.data.live:
                bw = self.badge(cr, ix, y + (rh - 16 * sc) / 2, "LIVE", 16 * sc, (1, 0.23, 0.19), a)
                ix -= bw + 5 * sc

            name_x = lx + 18 * sc + rad * 2
            weights = self.native.pango.Weight
            lay = self.layout_text(
                cr, p.data.name, 13.5 * sc, weights.BOLD if p.data.speaking else weights.MEDIUM
            )
            lay.set_width(int(max(10, ix - name_x - 4 * sc) * self.native.pango.SCALE))
            _, lh = lay.get_pixel_size()
            cr.move_to(name_x, y + (rh - lh) / 2)
            dim = 0.55 if p.data.deaf else 1
            cr.set_source_rgba(1, 1, 1, a * dim)
            self.native.pangocairo.show_layout(cr, lay)
            y += (rh + gap) * a

        # webcams
        if cams:
            y += gap
            cx0 = x0 + (total_w - cam_w if right else 0)
            for p, ch_, frame in zip(cams, cam_heights, cam_frames, strict=True):
                self.draw_cam(cr, p, cx0, y, cam_w, ch_, sc, frame)
                y += ch_ + gap

    def draw_discord(self, cr: CairoContext, width: int, height: int, now: float) -> None:
        """Looks like Discord's own in-game overlay: round avatars with a green
        speaking ring and names on small dark labels, no background panel."""
        sc = float(self.state.preferences.scale)
        margin = self.config.margin
        av = (32 if self.state.preferences.avatar_size == "large" else 24) * sc
        gap = 8 * sc
        names = self.state.preferences.show_names
        cam_w = float(self.state.preferences.webcam_width) * sc
        red = (0.95, 0.25, 0.26)
        op = self.state.preferences.opacity

        people, cams = self.participants()

        def label_alpha(p: AnimatedParticipant) -> float:
            if names == "never":
                return 0.0
            if names == "speaking":
                return p.glow
            return 1.0

        # measure
        rows: list[tuple[AnimatedParticipant, TextLayout, int, int, float, float]] = []
        list_width = 0.0
        for p in people:
            lay = self.layout_text(cr, p.data.name, 14 * sc, self.native.pango.Weight.SEMIBOLD)
            lay.set_width(int(200 * sc * self.native.pango.SCALE))
            tw, th = lay.get_pixel_size()
            icon = 14 * sc if (p.data.mute or p.data.deaf) else 0
            lw = tw + 12 * sc + (icon + 5 * sc if icon else 0)
            if p.data.live:
                lw += 40 * sc
            rows.append((p, lay, tw, th, icon, lw))
            list_width = max(
                list_width, av + 8 * sc + (lw if label_alpha(p) > 0 or names != "never" else 0)
            )
        cam_frames = [self.frame(p.data.id) for p in cams]
        cam_heights = [cam_w * frame_ratio(frame) for frame in cam_frames]
        list_h = sum((av + gap) * p.alpha for p in people)
        cams_h = sum(h + gap for h in cam_heights)
        total_h = list_h + (gap + cams_h if cams else 0)
        total_w = max(list_width, cam_w if cams else 0)

        pos = self.state.preferences.position
        right = "right" in pos
        x0, y = block_origin(pos, width, height, total_w, total_h, margin)

        for p, lay, tw, th, icon, lw in rows:
            a = p.alpha
            # mirrored on the right side, like Discord
            ax = x0 + total_w - av if right else x0
            self.draw_avatar(cr, p, ax + av / 2, y + av / 2, av / 2, a)
            if names == "never" and icon:
                # small red badge on the avatar instead of a label
                bx, by, br = ax + av - 5 * sc, y + av - 5 * sc, 7 * sc
                cr.new_path()
                cr.arc(bx, by, br, 0, 2 * math.pi)
                cr.set_source_rgba(0.12, 0.12, 0.13, a)
                cr.fill()
                (self.icon_deaf if p.data.deaf else self.icon_mic_off)(
                    cr, bx - br * 0.7, by - br * 0.7, br * 1.4, (*red, a)
                )
            la = label_alpha(p) * a
            if la > 0.01:
                lh = th + 6 * sc
                lx = ax - 8 * sc - lw if right else ax + av + 8 * sc
                ly = y + (av - lh) / 2
                rounded(cr, lx, ly, lw, lh, 4 * sc)
                cr.set_source_rgba(0.12, 0.12, 0.14, op * la)
                cr.fill()
                tx = lx + 6 * sc
                cr.move_to(tx, ly + 3 * sc)
                bright = 1.0 if p.data.speaking else 0.82
                cr.set_source_rgba(bright, bright, bright, la * (0.6 if p.data.deaf else 1))
                self.native.pangocairo.show_layout(cr, lay)
                tx += tw + 5 * sc
                if icon:
                    (self.icon_deaf if p.data.deaf else self.icon_mic_off)(
                        cr, tx, ly + (lh - icon) / 2, icon, (*red, la)
                    )
                    tx += icon + 5 * sc
                if p.data.live:
                    self.badge(
                        cr,
                        tx + 34 * sc,
                        ly + (lh - 15 * sc) / 2,
                        "LIVE",
                        15 * sc,
                        (0.95, 0.25, 0.26),
                        la,
                    )
            y += (av + gap) * a

        if cams:
            y += gap
            cx0 = x0 + (total_w - cam_w if right else 0)
            for p, ch_, frame in zip(cams, cam_heights, cam_frames, strict=True):
                self.draw_cam(cr, p, cx0, y, cam_w, ch_, sc, frame)
                y += ch_ + gap

        self.draw_discord_toasts(cr, sc, now, width, height)

    def draw_discord_toasts(
        self, cr: CairoContext, sc: float, now: float, width: int, height: int
    ) -> None:
        # Discord shows its popups in the bottom-right corner of the screen.
        margin = self.config.margin
        w, h = 300 * sc, 56 * sc
        y = height - margin - h
        for t in reversed(self.state.toasts):
            age = max(0.0, now - t.born)
            slide = min(1, age * 5)
            a = slide * min(1, max(0, (TOAST_LIFETIME_SECONDS - age) * 2))
            x = width - margin - w + (1 - slide) * 40 * sc
            for i in range(6, 0, -1):
                rounded(cr, x - i, y - i + 3, w + 2 * i, h + 2 * i, 8 * sc + i)
                cr.set_source_rgba(0, 0, 0, 0.03 * a)
                cr.fill()
            rounded(cr, x, y, w, h, 8 * sc)
            cr.set_source_rgba(0.17, 0.18, 0.19, 0.96 * a)
            cr.fill()
            fake = AnimatedParticipant(
                Participant(
                    id="toast",
                    name=t.message.text.split(" ")[0],
                    avatar=t.message.avatar,
                    me=False,
                    speaking=False,
                    mute=False,
                    deaf=False,
                    server_muted=False,
                    video=False,
                    live=False,
                )
            )
            self.draw_avatar(cr, fake, x + 12 * sc + 16 * sc, y + h / 2, 16 * sc, a)
            title = self.layout_text(cr, t.message.text, 14 * sc, self.native.pango.Weight.SEMIBOLD)
            title.set_width(int((w - 70 * sc) * self.native.pango.SCALE))
            sub = self.layout_text(
                cr,
                "Voice channel" if t.message.kind in ("join", "leave") else "",
                12 * sc,
                self.native.pango.Weight.NORMAL,
            )
            _, th = title.get_pixel_size()
            _, sh = sub.get_pixel_size()
            ty = y + (h - th - sh) / 2
            cr.move_to(x + 56 * sc, ty)
            cr.set_source_rgba(1, 1, 1, a)
            self.native.pangocairo.show_layout(cr, title)
            cr.move_to(x + 56 * sc, ty + th)
            cr.set_source_rgba(0.71, 0.73, 0.76, a)
            self.native.pangocairo.show_layout(cr, sub)
            y -= h + 8 * sc

    def draw_cam(
        self,
        cr: CairoContext,
        p: AnimatedParticipant,
        x: float,
        y: float,
        w: float,
        h: float,
        sc: float,
        frame: Pixbuf | None,
    ) -> None:
        discord = self.state.preferences.style == "discord"
        r = (8 if discord else 16) * sc
        accent = hex_rgb(self.state.preferences.accent)
        for i in range(8, 0, -1):
            rounded(cr, x - i, y - i + 4, w + 2 * i, h + 2 * i, r + i)
            cr.set_source_rgba(0, 0, 0, 0.03)
            cr.fill()
        cr.save()
        rounded(cr, x, y, w, h, r)
        cr.clip()
        if frame is not None:
            pix = frame
            s = max(w / pix.get_width(), h / pix.get_height())
            cr.save()
            cr.translate(x + (w - pix.get_width() * s) / 2, y + (h - pix.get_height() * s) / 2)
            cr.scale(s, s)
            self.native.gdk.cairo_set_source_pixbuf(cr, pix, 0, 0)
            cr.get_source().set_filter(self.native.cairo.FILTER_GOOD)
            cr.paint()
            cr.restore()
        else:
            cr.set_source_rgba(0.12, 0.12, 0.14, self.state.preferences.opacity)
            cr.paint()
            rad = min(w, h) * 0.16
            self.draw_avatar(cr, p, x + w / 2, y + h * 0.36, rad, 1)
            hint = self.layout_text(
                cr,
                "Open the call view in Discord to see this cam",
                11 * sc,
                self.native.pango.Weight.NORMAL,
            )
            hint.set_width(int((w - 20 * sc) * self.native.pango.SCALE))
            hint.set_alignment(self.native.pango.Alignment.CENTER)
            cr.move_to(x + 10 * sc, y + h * 0.36 + rad + 6 * sc)
            cr.set_source_rgba(1, 1, 1, 0.55)
            self.native.pangocairo.show_layout(cr, hint)
        # bottom fade so the name is readable on any video
        grad = self.native.cairo.LinearGradient(0, y + h * 0.6, 0, y + h)
        grad.add_color_stop_rgba(0, 0, 0, 0, 0)
        grad.add_color_stop_rgba(1, 0, 0, 0, 0.55)
        cr.rectangle(x, y, w, h)
        cr.set_source(grad)
        cr.fill()
        cr.restore()

        # name pill
        lay = self.layout_text(cr, p.data.name, 12 * sc)
        lay.set_width(int((w * 0.7) * self.native.pango.SCALE))
        tw, th = lay.get_pixel_size()
        icon = 14 * sc if (p.data.mute or p.data.deaf) else 0
        pw = tw + 20 * sc + (icon + 4 * sc if icon else 0)
        ph = th + 10 * sc
        px, py = x + 10 * sc, y + h - ph - 10 * sc
        rounded(cr, px, py, pw, ph, 4 * sc if discord else ph / 2)
        cr.set_source_rgba(0, 0, 0, 0.6 if discord else 0.45)
        cr.fill()
        tx = px + 10 * sc
        if icon:
            red = (1, 0.27, 0.23, 1)
            (self.icon_deaf if p.data.deaf else self.icon_mic_off)(
                cr, tx, py + (ph - icon) / 2, icon, red
            )
            tx += icon + 4 * sc
        cr.move_to(tx, py + 5 * sc)
        cr.set_source_rgba(1, 1, 1, 1)
        self.native.pangocairo.show_layout(cr, lay)

        # border + speaking glow
        rounded(cr, x, y, w, h, r)
        if p.glow > 0.01:
            cr.set_source_rgba(*accent, p.glow)
            cr.set_line_width((2.5 if discord else 3) * sc)
        elif discord:
            cr.set_source_rgba(0, 0, 0, 0.3)
            cr.set_line_width(1)
        else:
            cr.set_source_rgba(1, 1, 1, 0.14)
            cr.set_line_width(1)
        cr.stroke()

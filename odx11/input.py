# SPDX-FileCopyrightText: 2026 vibhanshu
# SPDX-License-Identifier: GPL-3.0-or-later
"""touch/scroll → XTest pointer events on the virtual output (PROJECT_SPEC.md §7.6).

`touch.x/y` are normalised 0..1 against the video; `scroll.dx/dy` are video
pixels with natural-scrolling sign. Geometry must be re-set after every xrandr
change.

The button press is deferred until the gesture is known: a still lift is a
click (left, or right after a long press), moving past the slop starts a drag,
and a `cancelled` touch that never moved is a two-finger scroll starting — it
must not click whatever was under the first finger. X11 has no way to retract a
press, so the only safe press is a late one.
"""
import logging
import math
import time

log = logging.getLogger(__name__)

BUTTON_LEFT = 1
BUTTON_RIGHT = 3
WHEEL_UP, WHEEL_DOWN, WHEEL_LEFT, WHEEL_RIGHT = 4, 5, 6, 7


def to_root(x: float, y: float, geom) -> tuple[int, int]:
    """Normalised video coords → X root coords inside the output, clamped to its edges."""
    x = min(max(float(x), 0.0), 1.0)
    y = min(max(float(y), 0.0), 1.0)
    return geom.x + round(x * (geom.w - 1)), geom.y + round(y * (geom.h - 1))


class ScrollAccumulator:
    """Turns pixel deltas into wheel clicks, one per `step` px, keeping the remainder."""

    def __init__(self, step: int):
        self.step = step
        self.ax = 0.0
        self.ay = 0.0

    def feed(self, dx: float, dy: float) -> list[int]:
        buttons = []
        self.ay += dy
        self.ax += dx
        # Natural sign: fingers down → content down → wheel up.
        while self.ay >= self.step:
            self.ay -= self.step
            buttons.append(WHEEL_UP)
        while self.ay <= -self.step:
            self.ay += self.step
            buttons.append(WHEEL_DOWN)
        while self.ax >= self.step:
            self.ax -= self.step
            buttons.append(WHEEL_LEFT)
        while self.ax <= -self.step:
            self.ax += self.step
            buttons.append(WHEEL_RIGHT)
        return buttons


class TouchGesture:
    """Pure gesture logic: (phase, root point, monotonic seconds) → pointer actions.

    Actions are ("move", (x, y)), ("press", button), ("release", button)."""

    IDLE, PENDING, DRAGGING = "idle", "pending", "dragging"

    def __init__(self, slop_px: float, long_press_s: float):
        self.slop = slop_px
        self.long_press = long_press_s
        self.state = self.IDLE
        self.start = None
        self.t0 = 0.0

    def feed(self, phase: str, pt, now: float) -> list:
        acts = [("move", pt)]
        if phase == "began":
            if self.state == self.DRAGGING:          # a began without an end: close the old drag
                acts.insert(0, ("release", BUTTON_LEFT))
            self.state, self.start, self.t0 = self.PENDING, pt, now
        elif phase == "moved":
            if self.state == self.PENDING and math.dist(pt, self.start) > self.slop:
                # Press where the finger landed, then move: the drag starts at the right spot.
                acts = [("move", self.start), ("press", BUTTON_LEFT), ("move", pt)]
                self.state = self.DRAGGING
        elif phase == "ended":
            if self.state == self.PENDING:
                button = BUTTON_RIGHT if now - self.t0 >= self.long_press else BUTTON_LEFT
                acts = [("move", self.start), ("press", button), ("release", button)]
            elif self.state == self.DRAGGING:
                acts.append(("release", BUTTON_LEFT))
            self.state = self.IDLE
        elif phase == "cancelled":
            if self.state == self.DRAGGING:          # no way to retract a drag; finish it
                acts.append(("release", BUTTON_LEFT))
            self.state = self.IDLE                   # a pending touch just evaporates: no click
        return acts

    def reset(self) -> list:
        """Session ending: never leave button 1 held down."""
        acts = [("release", BUTTON_LEFT)] if self.state == self.DRAGGING else []
        self.state = self.IDLE
        return acts


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class Injector:
    """`cfg` is the [input] section of the config."""

    def __init__(self, cfg, clock=time.monotonic):
        from Xlib import X, display
        from Xlib.ext import xtest
        self._X, self._xtest = X, xtest
        self.x = display.Display()
        self.clock = clock
        self.geometry = None
        self.scroll = ScrollAccumulator(cfg.scroll_step_px)
        self.gesture = TouchGesture(cfg.tap_slop_px, cfg.long_press_ms / 1000)

    def set_geometry(self, geom):
        self.geometry = geom

    def reconfigure(self, cfg):
        """New [input] settings, live. A gesture in progress keeps going."""
        self.scroll.step = cfg.scroll_step_px
        self.gesture.slop = cfg.tap_slop_px
        self.gesture.long_press = cfg.long_press_ms / 1000

    def _run(self, acts):
        for kind, arg in acts:
            if kind == "move":
                self._xtest.fake_input(self.x, self._X.MotionNotify, x=arg[0], y=arg[1])
            else:
                if arg in (BUTTON_LEFT, BUTTON_RIGHT):      # wheel clicks are logged per scroll
                    log.debug("%s button %d", kind, arg)
                self._xtest.fake_input(
                    self.x, self._X.ButtonPress if kind == "press" else self._X.ButtonRelease, arg)

    def handle(self, msg: dict):
        if self.geometry is None:
            return
        t = msg["type"]
        if t == "touch":
            if not (_num(msg.get("x")) and _num(msg.get("y"))):
                return
            pt = to_root(msg["x"], msg["y"], self.geometry)
            phase = msg.get("phase")
            if phase != "moved":
                log.debug("touch %s at %s", phase, pt)
            self._run(self.gesture.feed(phase, pt, self.clock()))
        elif t == "scroll":
            dx, dy = msg.get("dx", 0), msg.get("dy", 0)
            if not (_num(dx) and _num(dy)):
                return
            buttons = self.scroll.feed(dx, dy)
            if buttons:
                log.debug("scroll dx=%g dy=%g -> buttons %s", dx, dy, buttons)
            for b in buttons:
                self._run([("press", b), ("release", b)])
        self.x.flush()

    def release_all(self):
        self._run(self.gesture.reset())
        self.x.flush()

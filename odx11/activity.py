# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wake signals for the idle gate (PROJECT_SPEC.md §4, AC 23).

Event-driven, never polled, on an X connection of our own. The question is
"may the virtual monitor's picture have changed?", and it must be answered per
region: under Cinnamon's compositor, DAMAGE on the root reports every repaint
as the whole screen, so one busy window anywhere (a terminal, btop) would keep
capture awake forever. Instead:

- every top-level window gets its own DAMAGE object (per-window damage is exact
  under compositing — it is what the compositor itself uses), and SubstructureNotify
  on the root keeps their geometry current;
- a window that damages, maps, unmaps or moves wakes us only if it overlaps the
  virtual monitor (before or after a move);
- XInput2 raw motion catches the cursor, which moves without any repaint and is
  baked into the video: wake while it is over the virtual monitor or just left it.

Not seen: pixels the compositor draws itself rather than a window — Cinnamon
panels, OSDs, notifications. By default those live on the primary monitor.
"""
import logging
from typing import NamedTuple

log = logging.getLogger(__name__)


class Activity(NamedTuple):
    wake: bool                  # the virtual monitor's picture may have changed
    pointer: tuple | None       # (x, y) in root coordinates when the pointer moved
    cursor_changed: bool        # the cursor's shape changed (XFixes)

GENERIC_EVENT = 35                       # X protocol event code carrying XInput2 events


def _overlaps(a, b) -> bool:
    """a, b: (x, y, w, h)."""
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _inside(px, py, r) -> bool:
    return r[0] <= px < r[0] + r[2] and r[1] <= py < r[1] + r[3]


class Tracker:
    """Pure decision logic: window geometry bookkeeping → "wake?" answers."""

    def __init__(self):
        self.region = None               # (x, y, w, h) of the virtual monitor, or None
        self.windows = {}                # window id -> (x, y, w, h) while viewable
        self.pointer_inside = False

    def set_region(self, rect):
        self.region = tuple(rect) if rect else None

    def _hits(self, rect) -> bool:
        return self.region is not None and rect is not None and _overlaps(rect, self.region)

    def mapped(self, win, rect) -> bool:
        self.windows[win] = rect
        return self._hits(rect)

    def unmapped(self, win) -> bool:
        return self._hits(self.windows.pop(win, None))

    def configured(self, win, rect) -> bool:
        """Moved, resized or restacked. Wakes if it touches the region before or after."""
        if win not in self.windows:
            return False                 # not viewable: nothing on screen changed
        old, self.windows[win] = self.windows[win], rect
        return self._hits(old) or self._hits(rect)

    def damaged(self, win) -> bool:
        return self._hits(self.windows.get(win))

    def pointer(self, px, py) -> bool:
        inside = self.region is not None and _inside(px, py, self.region)
        wake = inside or self.pointer_inside            # leaving must repaint without the cursor
        self.pointer_inside = inside
        return wake


class ActivityMonitor:
    def __init__(self):
        from Xlib import X, display, error
        from Xlib.ext import damage, xfixes, xinput
        self._X, self._damage_mod, self._error = X, damage, error
        self.x = display.Display()
        # Windows come and go between our requests; their BadWindow/BadMatch/BadDamage are noise.
        self.x.set_error_handler(lambda err, req=None: None)
        self.root = self.x.screen().root
        self.tracker = Tracker()
        self._damages = {}               # window id -> damage id
        self.x.damage_query_version()    # required before any other DAMAGE request
        self._damage_event = self.x.query_extension("DAMAGE").first_event
        self.x.xinput_query_version()
        self.root.xinput_select_events([(xinput.AllMasterDevices, xinput.RawMotionMask)])
        self.root.change_attributes(event_mask=X.SubstructureNotifyMask)
        # The cursor channel needs to know when the sprite changes (arrow → I-beam → hand …).
        self.x.xfixes_query_version()
        self._cursor_event = self.x.query_extension("XFIXES").first_event + xfixes.XFixesCursorNotify
        self.x.xfixes_select_cursor_input(self.root, xfixes.XFixesDisplayCursorNotifyMask)
        self.draw_cursor = True          # cursor baked into the video: pointer motion must wake capture
        for w in self.root.query_tree().children:
            self._track(w)
        self.x.sync()
        # Replies above may have pulled events into Xlib's queue; a queued event never makes
        # the socket readable again, so drain now or the first wake can be missed.
        self.drain()

    def set_region(self, rect):
        self.tracker.set_region(rect)

    def fileno(self) -> int:
        return self.x.fileno()

    def _track(self, w):
        """Start watching a top-level window if it is viewable and has pixels."""
        X = self._X
        try:
            attrs = w.get_attributes()
            if attrs.map_state != X.IsViewable or attrs.win_class == X.InputOnly:
                return False
            g = w.get_geometry()
        except self._error.XError:
            return False
        rect = (g.x, g.y, g.width + 2 * g.border_width, g.height + 2 * g.border_width)
        if w.id not in self._damages:
            # NonEmpty: one event per burst; subtracting re-arms it.
            self._damages[w.id] = w.damage_create(self._damage_mod.DamageReportNonEmpty)
        return self.tracker.mapped(w.id, rect)

    def _untrack(self, wid):
        did = self._damages.pop(wid, None)
        if did is not None:
            self.x.damage_destroy(did)   # BadDamage if the window is already gone: ignored
        return self.tracker.unmapped(wid)

    def pointer_position(self) -> tuple[int, int]:
        p = self.root.query_pointer()
        return p.root_x, p.root_y

    def cursor_image(self):
        """(width, height, xhot, yhot, ARGB pixels, serial) of the current cursor sprite."""
        img = self.x.xfixes_get_cursor_image(self.root)
        return img.width, img.height, img.xhot, img.yhot, img.cursor_image, img.cursor_serial

    def drain(self) -> Activity:
        """Consume pending events: what changed since the last call."""
        X = self._X
        wake = motion = cursor_changed = False
        rearm = []
        while self.x.pending_events():
            e = self.x.next_event()
            if e.type == self._damage_event:
                rearm.append(e.damage)
                wake |= self.tracker.damaged(getattr(e.drawable, "id", e.drawable))
            elif e.type == X.MapNotify:
                wake |= self._track(e.window)
            elif e.type == X.UnmapNotify or e.type == X.DestroyNotify:
                wake |= self._untrack(e.window.id)
            elif e.type == X.ConfigureNotify:
                if e.window.id != self.root.id:
                    rect = (e.x, e.y, e.width + 2 * e.border_width, e.height + 2 * e.border_width)
                    wake |= self.tracker.configured(e.window.id, rect)
            elif e.type == X.ReparentNotify:
                if e.parent.id == self.root.id:
                    wake |= self._track(e.window)
                else:
                    wake |= self._untrack(e.window.id)
            elif e.type == GENERIC_EVENT:
                motion = True            # XI2 raw motion
            elif e.type == self._cursor_event:
                cursor_changed = True
        for did in rearm:
            self.x.damage_subtract(did)
        pointer = None
        if motion:
            pointer = self.pointer_position()        # one round trip per batch, not per event
            near = self.tracker.pointer(*pointer)
            wake |= near and self.draw_cursor        # with the cursor channel, motion alone
        if rearm:                                    # never wakes capture
            self.x.flush()
        return Activity(wake, pointer, cursor_changed)

    def close(self):
        try:
            self.x.close()
        except Exception:
            pass

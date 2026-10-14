"""The daemon's published state, and its wording (PROJECT_SPEC.md §8.8).

The daemon writes facts to $XDG_RUNTIME_DIR/odx11/state.json on every
transition (atomically, only when something changed); the settings window
watches the file. Nothing flows the other way: this is not a command channel.
`describe()` turns facts into the words the window and the notifications show,
so the two can never disagree.
"""
import json
import os
import tempfile
import time

# Daemon-level states, as published.
OFF = "off"                  # no state file: the service is not running
PROBLEM = "problem"          # cannot run: setup problem (the service has exited)
IDLE = "idle"                # running, no iPad attached
DIALING = "dialing"          # iPad attached, app not answering (closed, locked)
STREAMING = "streaming"      # showing the desktop on the iPad
GRACE = "grace"              # connection lost, keeping the monitor for a while
PAUSED = "paused"            # monitor turned off in display settings: wait for a replug

# Severity, for the window's colour dot and the notification icon.
OK, WAITING, ATTENTION, ERROR, NEUTRAL = "ok", "waiting", "attention", "error", "neutral"


def path() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(tempfile.gettempdir(), f"odx11-{os.getuid()}")
    return os.path.join(base, "odx11", "state.json")


class StateFile:
    def __init__(self, file_path: str | None = None, clock=time.time):
        self.path = file_path or path()
        self.clock = clock
        self._last = None

    def write(self, facts: dict) -> bool:
        """Publish `facts` if they differ from the last write. Returns True if written."""
        if facts == self._last:
            return False
        self._last = dict(facts)
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        payload = json.dumps({**facts, "updated": self.clock()}, sort_keys=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix=".state-")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
            os.replace(tmp, self.path)         # readers see the old or the new file, never half
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True

    def clear(self):
        """The service is stopping normally: no file means Off."""
        self._last = None
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


def read(file_path: str | None = None) -> dict:
    """The published facts, or {"state": OFF} when the service is not running."""
    try:
        with open(file_path or path()) as f:
            facts = json.load(f)
    except (OSError, ValueError):
        return {"state": OFF}
    if not isinstance(facts, dict) or "state" not in facts:
        return {"state": OFF}
    pid = facts.get("pid")
    if facts["state"] != PROBLEM and isinstance(pid, int) and not _alive(pid):
        return {"state": OFF}                  # left behind by a crash or SIGKILL
    return facts


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def describe(facts: dict, now: float | None = None) -> tuple[str, str, str]:
    """(headline, what to do or "", severity) for a set of published facts."""
    state = facts.get("state", OFF)
    device = facts.get("device") or "your iPad"
    if state == OFF:
        return "Off", "OpenDisplay for X11 is not running.", NEUTRAL
    if state == PROBLEM:
        return "Setup needed", facts.get("problem") or "Something is not set up.", ERROR
    if state == IDLE:
        return "Waiting for your iPad", "Plug it in with a USB cable.", WAITING
    if state == DIALING:
        return "iPad connected", "Unlock it and open the OpenDisplay app.", ATTENTION
    if state == STREAMING:
        mon = facts.get("monitor")
        size = f" ({mon[0]} × {mon[1]})" if isinstance(mon, list) and len(mon) >= 2 else ""
        idle = " · paused while nothing changes" if facts.get("idle") else ""
        return f"Showing your desktop on {device}{size}", idle.lstrip(" ·"), OK
    if state == GRACE:
        left = facts.get("grace_until")
        if isinstance(left, (int, float)):
            secs = max(0, round(left - (now if now is not None else time.time())))
            head = f"Connection lost — keeping the screen for {secs} s"
        else:
            head = "Connection lost — keeping the screen for a moment"
        return head, "Reopen the OpenDisplay app to resume.", ATTENTION
    if state == PAUSED:
        return "Turned off in display settings", "Unplug and replug the iPad to resume.", NEUTRAL
    return "Unknown state", "", NEUTRAL

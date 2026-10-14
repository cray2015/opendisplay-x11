"""TOML config: ~/.config/odx11/config.toml (PROJECT_SPEC.md §8.6).

Every tunable lives here; code keeps only values the protocol or hardware fixes.
Every key is optional; `config.example.toml` documents each one with its default.
Unknown keys are an error so a typo never silently falls back to a default.
"""
import os
import tomllib
from dataclasses import dataclass, field, fields

# XDG Base Directory spec: $XDG_CONFIG_HOME, falling back to ~/.config.
DEFAULT_PATH = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                            "odx11", "config.toml")
SIDES = ("left", "right", "above", "below")
ALIGNS = ("start", "center", "end")


@dataclass
class ServiceConfig:
    enabled: bool = True                    # start at login (the autostart entry checks this)


@dataclass
class NotificationsConfig:
    enabled: bool = True                    # desktop notifications from the service
    connected: bool = True                  # "iPad connected — desktop extended"
    needs_action: bool = True               # "Open OpenDisplay on your iPad"
    disconnected: bool = True               # "iPad disconnected"
    problems: bool = True                   # "Setup needed: …"


@dataclass
class TransportConfig:
    usbmuxd_socket: str = "/var/run/usbmuxd"
    device_port: int = 9000                 # the receiver's listening port


@dataclass
class DisplayConfig:
    connector: str = "auto"                 # "auto" = first free HDMI port; or a kernel name like HDMI-A-1
    main_output: str | None = None          # None = the primary output
    divisor: float = 1.5                    # logical mode = panel / divisor (§8.2)
    refresh_hz: int = 60                    # refresh of the generated cvt -r mode
    connect_timeout_s: float = 5.0          # wait for the forced output to appear in RandR
    settle_timeout_s: float = 3.0           # wait for Cinnamon to restore its saved layout


@dataclass
class VideoConfig:
    fps: int = 30
    qp: int = 26                            # CQP: the only rate control on this encoder (§4)
    keyframe_period: int = 1_000_000        # large = no periodic IDRs; 0 means auto (every 30)
    show_pointer: bool = True               # bake the cursor into the video until M4
    convert_threads: int = 2                # videoconvert worker threads
    idle_pause_s: float = 1.0               # pause capture after this long with no screen change; 0 = never


@dataclass
class InputConfig:
    scroll_step_px: int = 40                # video pixels of scroll per wheel click
    tap_slop_px: float = 8.0                # movement beyond this turns a touch into a drag
    long_press_ms: int = 500                # a still touch held this long right-clicks on lift


@dataclass
class SessionConfig:
    ping_interval_s: float = 2.0            # receiver drops us after 5 s of silence
    silence_timeout_s: float = 5.0          # end the session after this long without bytes
    grace_s: float = 10.0                   # keep the display this long after a drop (§8.4)
    redial_s: float = 3.0                   # redial interval while the app is not open
    redial_grace_s: float = 1.0             # faster redial during grace (app switch, lock)
    rotation_debounce_ms: int = 300         # settle a flurry of rotation hellos into one rebuild


@dataclass
class DeviceConfig:
    # side/align place the monitor only when Cinnamon has no saved layout for it;
    # after that, Cinnamon's Display settings own placement (§4).
    side: str = "right"
    align: str = "start"
    mode: tuple[int, int] | None = None     # landscape override, e.g. [1568, 1088]


@dataclass
class Config:
    service: ServiceConfig = field(default_factory=ServiceConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    transport: TransportConfig = field(default_factory=TransportConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    input: InputConfig = field(default_factory=InputConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    devices: dict[str, DeviceConfig] = field(default_factory=dict)

    def device(self, device_id: str | None) -> DeviceConfig:
        return self.devices.get(device_id or "", DeviceConfig())


def _section(cls, raw: dict, where: str):
    known = {f.name for f in fields(cls)}
    unknown = raw.keys() - known
    if unknown:
        raise ValueError(f"[{where}] unknown keys: {sorted(unknown)}")
    return cls(**raw)


def _validate(cfg: Config):
    def need(ok, msg):
        if not ok:
            raise ValueError(msg)
    need(cfg.display.divisor > 0, "display.divisor must be > 0")
    need(cfg.display.refresh_hz > 0, "display.refresh_hz must be > 0")
    need(cfg.display.connect_timeout_s > 0 and cfg.display.settle_timeout_s >= 0,
         "display timeouts must be positive")
    need(cfg.display.connector == "auto" or cfg.display.connector.startswith("HDMI-A-"),
         "display.connector must be \"auto\" or an HDMI-A-n alias (forcing DP-n is untested)")
    need(cfg.video.fps > 0, "video.fps must be > 0")
    need(0 <= cfg.video.qp <= 51, "video.qp must be 0..51")
    need(cfg.video.idle_pause_s >= 0, "video.idle_pause_s must be >= 0")
    need(cfg.video.keyframe_period > 0, "video.keyframe_period must be > 0 (0 means every 30 frames)")
    need(cfg.input.scroll_step_px > 0, "input.scroll_step_px must be > 0")
    need(cfg.input.tap_slop_px >= 0, "input.tap_slop_px must be >= 0")
    need(cfg.input.long_press_ms > 0, "input.long_press_ms must be > 0")
    need(0 < cfg.session.ping_interval_s <= 2.0,
         "session.ping_interval_s must be in (0, 2] — the receiver drops silent senders after 5 s")
    need(cfg.session.silence_timeout_s > cfg.session.ping_interval_s,
         "session.silence_timeout_s must exceed ping_interval_s")
    need(cfg.session.grace_s >= 0 and cfg.session.redial_s > 0 and cfg.session.redial_grace_s > 0
         and cfg.session.rotation_debounce_ms >= 0, "session timings must be positive")
    for dev_id, d in cfg.devices.items():
        need(d.side in SIDES and d.align in ALIGNS,
             f"devices.{dev_id}: side must be one of {SIDES}, align one of {ALIGNS}")
        need(d.mode is None or (len(d.mode) == 2 and all(v > 0 and v % 16 == 0 for v in d.mode)),
             f"devices.{dev_id}.mode must be [w, h], multiples of 16")


def parse(raw: dict) -> Config:
    raw = dict(raw)
    sections = {f.name for f in fields(Config)}
    devices = {}
    for dev_id, d in raw.pop("devices", {}).items():
        d = dict(d)
        if "mode" in d:
            d["mode"] = tuple(d["mode"])
        devices[dev_id] = _section(DeviceConfig, d, f"devices.{dev_id}")
    unknown = raw.keys() - sections
    if unknown:
        raise ValueError(f"unknown config sections/keys: {sorted(unknown)}")
    cfg = Config(
        service=_section(ServiceConfig, raw.get("service", {}), "service"),
        notifications=_section(NotificationsConfig, raw.get("notifications", {}), "notifications"),
        transport=_section(TransportConfig, raw.get("transport", {}), "transport"),
        display=_section(DisplayConfig, raw.get("display", {}), "display"),
        video=_section(VideoConfig, raw.get("video", {}), "video"),
        input=_section(InputConfig, raw.get("input", {}), "input"),
        session=_section(SessionConfig, raw.get("session", {}), "session"),
        devices=devices,
    )
    _validate(cfg)
    return cfg


def load(path: str = DEFAULT_PATH) -> Config:
    if not os.path.exists(path):
        return parse({})
    with open(path, "rb") as f:
        return parse(tomllib.load(f))

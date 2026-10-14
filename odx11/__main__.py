"""CLI. End users never type these: the package's autostart entry runs `autostart`, the user
service runs `daemon` and `release`. `run` and `list` are for developers and diagnostics."""
import argparse
import atexit
import logging
import os
import subprocess
import sys

from . import config, usbmux

log = logging.getLogger("odx11")

EX_CONFIG = 78          # sysexits: configuration error — the unit does not restart on it


def cmd_run(cfg, args):
    from . import daemon, display, input as odinput, pipeline

    problem = display.connector_problem(cfg.display)
    if problem:
        log.error("%s", problem)
        publish_problem(cfg, problem)
        sys.exit(EX_CONFIG)
    vdisplay = display.VirtualDisplay(cfg.display)
    atexit.register(vdisplay.teardown)       # crash path; daemon.run's finally is the normal one
    injector = odinput.Injector(cfg.input)
    xname = daemon.display_name()

    def make_pipeline(geom, on_au, on_error):
        return pipeline.Pipeline(geom, cfg.video, xname, on_au, on_error)

    tcp = None
    if args.tcp:
        host, port = args.tcp.rsplit(":", 1)
        tcp = (host, int(port))
    if daemon.run(cfg, vdisplay, injector, make_pipeline, tcp=tcp, udid=args.udid, config_path=args.config):
        from . import upgrade
        vdisplay.teardown()                       # now, not at exit: the new version takes the port
        sys.exit(upgrade.EX_RESTART)


def publish_problem(cfg, problem):
    """The service cannot run: leave the reason for the settings window and say it once."""
    from . import __version__, notify, state
    facts = {"state": state.PROBLEM, "problem": problem, "version": __version__, "pid": os.getpid()}
    previous = state.read()                       # before writing: was this already reported?
    try:
        state.StateFile().write(facts)
    except OSError as e:
        log.warning("could not write the state file: %s", e)
    if cfg is not None and previous.get("problem") != problem:
        for kind, summary, body in notify.decide({"state": state.OFF}, facts, cfg.notifications):
            notify.Notifier().send(kind, summary, body)


def cmd_list(cfg, args):
    devices = usbmux.list_devices(cfg.transport.usbmuxd_socket)
    if not devices:
        print("no USB iOS devices attached")
    for d in devices:
        p = d["Properties"]
        print(f"{d['DeviceID']}\t{p.get('SerialNumber')}\t{p.get('ConnectionType')}")


def cmd_autostart(cfg, args):
    """Run by the XDG autostart entry inside the desktop session: hand the session's display to
    the systemd user manager (bare window managers don't), then start the service if enabled.
    Never blocks or fails the login."""
    env = [v for v in ("DISPLAY", "XAUTHORITY") if os.environ.get(v)]
    if env:
        subprocess.run(["systemctl", "--user", "import-environment", *env], check=False)
    if cfg is None or not cfg.service.enabled:
        log.info("odx11 is switched off ([service] enabled = false) — not starting")
        return
    subprocess.run(["systemctl", "--user", "--no-block", "start", "odx11.service"], check=False)


def cmd_settings(cfg, args):
    """The settings window (GTK 4 + libadwaita), its own process."""
    from . import gui
    sys.exit(gui.main(args.config, getattr(args, "config_error", None)))


def cmd_release(cfg, args):
    """Run by systemd after the daemon stops, however it stopped (ExecStopPost)."""
    from . import display
    released = display.release_forced()
    if released:
        log.info("released forced connector(s): %s", ", ".join(released))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="odx11", description="iPad as an X11 second monitor over USB")
    ap.add_argument("--config", default=config.DEFAULT_PATH, help="config file (default: %(default)s)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("run", "extend the desktop to the iPad whenever it connects; Ctrl-C to stop"),
                        ("daemon", "the same, logging for the systemd journal (the user service)")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--udid", help="only use the device with this UDID")
        p.add_argument("--tcp", metavar="HOST:PORT", help="dial a receiver directly (tools/fake_receiver.py)")
    sub.add_parser("list", help="list attached iOS devices")
    sub.add_parser("autostart", help="(login) export the display to systemd and start the service if enabled")
    sub.add_parser("release", help="(service stop) unforce any connector odx11 left forced")
    sub.add_parser("settings", help="open the settings window")
    # Also accept -v after the subcommand: `odx11 run --verbose`.
    for p in sub.choices.values():
        p.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    fmt = ("%(levelname).1s %(name)s: %(message)s" if args.cmd in ("daemon", "release")   # journald stamps time
           else "%(asctime)s %(levelname).1s %(name)s: %(message)s")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format=fmt, datefmt="%H:%M:%S")
    try:
        cfg = config.load(args.config)
    except (ValueError, TypeError, OSError) as e:
        if args.cmd in ("autostart", "release", "settings"):  # never fail a login or a stop;
            log.error("config %s: %s", args.config, e)        # the window shows the error
            cfg = None
            args.config_error = str(e)
        else:
            log.error("config %s: %s", args.config, e)
            sys.exit(EX_CONFIG)
    {"run": cmd_run, "daemon": cmd_run, "list": cmd_list,
     "autostart": cmd_autostart, "release": cmd_release, "settings": cmd_settings}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()

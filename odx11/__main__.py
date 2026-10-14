"""CLI: `run` (foreground, verbose-friendly), `daemon` (systemd user service), `list`."""
import argparse
import atexit
import logging
import sys

from . import config, usbmux

log = logging.getLogger("odx11")


def cmd_run(cfg, args):
    from . import daemon, display, input as odinput, pipeline

    problem = display.helper_problem(cfg.display)
    if problem:
        sys.exit(problem)
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
    daemon.run(cfg, vdisplay, injector, make_pipeline, tcp=tcp, udid=args.udid)


def cmd_list(cfg, args):
    devices = usbmux.list_devices(cfg.transport.usbmuxd_socket)
    if not devices:
        print("no USB iOS devices attached")
    for d in devices:
        p = d["Properties"]
        print(f"{d['DeviceID']}\t{p.get('SerialNumber')}\t{p.get('ConnectionType')}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="odx11", description="iPad as an X11 second monitor over USB")
    ap.add_argument("--config", default=config.DEFAULT_PATH, help="config file (default: %(default)s)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("run", "extend the desktop to the iPad whenever it connects; Ctrl-C to stop"),
                        ("daemon", "the same, logging for the systemd journal")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--udid", help="only use the device with this UDID")
        p.add_argument("--tcp", metavar="HOST:PORT", help="dial a receiver directly (tools/fake_receiver.py)")
    sub.add_parser("list", help="list attached iOS devices")
    # Also accept -v after the subcommand: `odx11 run --verbose`.
    for p in sub.choices.values():
        p.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    fmt = ("%(levelname).1s %(name)s: %(message)s" if args.cmd == "daemon"     # journald stamps time
           else "%(asctime)s %(levelname).1s %(name)s: %(message)s")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format=fmt, datefmt="%H:%M:%S")
    try:
        cfg = config.load(args.config)
    except (ValueError, OSError) as e:
        sys.exit(f"config {args.config}: {e}")
    {"run": cmd_run, "daemon": cmd_run, "list": cmd_list}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()

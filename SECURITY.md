<!--
SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
SPDX-License-Identifier: GPL-3.0-or-later
-->
# Security policy

## Reporting a vulnerability

Please report privately through GitHub:
**[Report a vulnerability](https://github.com/cray2015/opendisplay-x11/security/advisories/new)**
(the repository's *Security* tab → *Report a vulnerability*).

Do not open a public issue for a security problem. Reports go to the maintainer,
Vibhanshu Biswas. This is a one-person project: you will get a reply as soon as possible,
and a fix is released as a new version on the
[Releases](https://github.com/cray2015/opendisplay-x11/releases) page, credited to you unless
you prefer otherwise.

## Supported versions

Only the latest release receives fixes. Updates install like any other package.

## Security model, in short

What OpenDisplay for X11 can access, and how each part is checked, is in
[TRUST.md](TRUST.md). The main points:

- **Nothing runs as root while it is running.** The only privileged part is a udev rule
  that lets members of the `plugdev` group switch spare HDMI ports on and off
  (`packaging/70-opendisplay-x11.rules`). The package's install and removal scripts run as
  root, as every package's do.
- **No network access.** It talks to the iPad through `usbmuxd` over the USB cable.
- **No way to send it commands.** The background service has no socket, D-Bus service or
  signal interface; the settings window only edits the config file and uses `systemctl`.
- **It sees your screen and your input:** it captures the iPad's monitor area and injects
  the iPad's touches as pointer events. It also subscribes to the X server's raw key and
  button events. The X server sends every press, including which key, while the service runs.
  The code uses only the fact that something was pressed, and only while the pointer is on the
  iPad's monitor, to refresh the picture (`odx11/activity.py`). Nothing about the keys is stored,
  logged or sent.

In scope: anything that lets another user or program gain access through OpenDisplay for X11,
read what it captures, or misuse the udev permission. Out of scope: the OpenDisplay iPad app
(report to [its project](https://github.com/peetzweg/opendisplay)), and vulnerabilities in
Ubuntu/Mint packages it depends on (report to the distribution).

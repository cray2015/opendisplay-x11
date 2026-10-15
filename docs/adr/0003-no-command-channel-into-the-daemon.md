<!--
SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
SPDX-License-Identifier: GPL-3.0-or-later
-->
# 0003 — No command channel into the background service

- **Status:** Accepted (2026-10-14) · *Reconstructed 2026-10-15 from PROJECT_SPEC.md §8.8 and commits e750a99, c23753c*
- **Deciders:** the owner (asked for a GUI so nobody needs the command line), with Claude

## Context
A settings window needs to show status and change settings. The usual designs (a socket, a D-Bus
service, signals) add a local interface into a process that captures the screen and injects
input. Any such interface is attack surface and must be authenticated and maintained.

## Decision
The service exposes nothing. It writes a state file (`$XDG_RUNTIME_DIR/odx11/state.json`) on each
transition and watches its config file (inotify). The window reads the state file and acts only
through things that already exist: the config file, `systemctl --user`, `odx11 release`,
`idevicepair`.

## Consequences
- No new interface to secure; another local program can do nothing through OpenDisplay for X11 that it could not do by editing the user's own files.
- Settings apply without an Apply button: the service reacts to the file at the lightest level each change needs (live, stream restart, display rebuild).
- The window and the service can be upgraded separately: a window-only release does not restart the service.

<!--
SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
SPDX-License-Identifier: GPL-3.0-or-later
-->
# Trust

OpenDisplay for X11 was built with heavy AI assistance (Claude) under the direction of its
owner, Vibhanshu Biswas. Every commit but one (`b4a973a`, the owner's own) is co-authored by
the AI. Every commit since the licence decision (`4fd0934`) carries the owner's DCO sign-off,
except that same `b4a973a`; the 17 earlier ones predate it. This page
does not ask you to trust that. It lists what the software can reach, which automated check
verifies each claim, what is *not* verified, and how to check a release yourself.

Last reviewed: 2026-10-15 (version 0.4.2).

## What it can access

Locations are `file:line` in this revision. `.semgrep/capabilities.yml` finds every one of them,
so the list cannot silently go stale: a pull request that adds a capability fails CI
until it is reviewed and added here.

**While it runs (as your user, never as root):**

| Capability | Where | What exactly |
|---|---|---|
| **Screen capture** | `odx11/pipeline.py:66` | `ximagesrc` copies only the iPad monitor's rectangle of the X screen |
| **Cursor image** | `odx11/activity.py:182` | Reads the current pointer image (XFixes) to draw it on the iPad |
| **Keyboard and mouse events** | `odx11/activity.py:111` (subscribe), `:210-214` (use) | Subscribes to XInput2 raw key and button events from all devices. The X server delivers every press, including which key, while the service runs. The code reads only the event type ("something was pressed"), and acts on it only while the pointer is on the iPad's monitor, to refresh the picture. Nothing about keys is stored, logged or sent |
| **Input injection** | `odx11/input.py:135`, `:139` | XTest pointer motion, clicks and scrolls, translated from the iPad's touches, only inside the iPad monitor's area |
| **Monitor port switch (hardware)** | `odx11/display.py:291`, `:390` | Writes `on`/`detect` to `/sys/class/drm/card*-HDMI-A-*/status` (allowed by the udev rule below). Never `DP-*` connectors, never a connector with a real monitor |
| **USB link to the iPad** | `odx11/usbmux.py:50` | Unix socket `/var/run/usbmuxd` (Apple device multiplexer), connects to the OpenDisplay app's port |
| **TCP** | `odx11/daemon.py:830` | Only with the developer flag `odx11 run --tcp HOST:PORT` |
| **Internet** | — | **None.** The README, TestFlight and upstream URLs are only shown as links and a QR code |
| **Other programs it runs** | `display.py:78` `cvt`, `display.py:179` `xrandr`; `pipeline.py:26`, `checks.py:72` `gst-inspect-1.0`; `checks.py:85` `idevice_id`, `:91` `idevicepair validate`, `:108` `idevicepair pair`; `gui.py:49`, `:388`, `daemon.py:929`, `__main__.py:74`, `:78` `systemctl --user`; `gui.py:395` and `notify.py:87` its own `odx11` commands; `upgrade.py:55` `dpkg-query`; `gui.py:473` `journalctl --user -u odx11` (diagnostic report) | Fixed argument lists, no shell |
| **Files written** | `state.py:46-65` | `$XDG_RUNTIME_DIR/odx11/state.json` (status for the window) |
| | `settings.py:103-111`, `gui.py:232` | `~/.config/odx11/config.toml` (only when you change a setting) |
| **Files watched** | `daemon.py`, `gui.py`, `upgrade.py` | inotify on the usbmuxd socket, its config, its state file and the installed `VERSION` file |
| **Environment** | `__main__.py:72`, `checks.py:28`, `config.py:14`, `daemon.py:957`, `gui.py:484-485`, `state.py:31` | `DISPLAY`, `XAUTHORITY`, `XDG_CONFIG_HOME`, `XDG_RUNTIME_DIR`, `XDG_SESSION_TYPE`, `XDG_CURRENT_DESKTOP` |
| **D-Bus** | `notify.py:56-73` | Outgoing desktop notifications (`org.freedesktop.Notifications`) and their click signal. It offers no D-Bus service |
| **Clipboard** | `gui.py:464` | Writes the diagnostic report, only when you click *Copy diagnostic report* |
| **Dynamic code** | — | **None** (`eval`, `exec`, `pickle`, `ctypes`: none; `importlib.metadata` only reads the version) |

**At install and removal (as root, like every package):**

| File | Does |
|---|---|
| `packaging/70-opendisplay-x11.rules` | udev rule: members of `plugdev` may write the HDMI connectors' `status` file. **The project's entire privileged surface.** |
| `debian/postinst` | Reloads udev rules and re-applies them to the HDMI connectors |
| `debian/prerm` | Stops running services (`pkill`) and unforces any connector they left forced (`odx11 release`) |
| `debian/postrm` | Resets the HDMI `status` files' group and mode, which removing a rule does not undo |

Developer-only (not shipped): `install.sh` uses `pkexec` to install build tools and the package;
`spike/` scripts use `sudo`; `tools/` are benchmarks and test helpers.

## What is verified, and by which check

| Claim | Check | Runs | Defined in |
|---|---|---|---|
| The package builds from this source; the unit tests pass (581 with the X11 tests); the desktop entry and AppStream file are valid; lintian has no errors or warnings | Build job | Every push and PR | `.github/workflows/ci.yml` (`build`) |
| The build is reproducible: two builds give the identical `.deb` | Two builds compared with `cmp` | Every push and PR | `ci.yml` (`reproducible`) |
| A release `.deb` was built by this repository's release workflow, from the tagged commit | Sigstore-signed build provenance | Every release | `.github/workflows/release.yml` |
| A release's files are the ones published | `SHA256SUMS`; SPDX SBOM | Every release | `release.yml` |
| No pull request adds a capability without review | Semgrep rules, `--baseline-commit` | Every PR | `.semgrep/capabilities.yml`, `ci.yml` (`capabilities`) |
| Code style and common bug and security patterns | ruff (pycodestyle, pyflakes, bugbear, bandit's `S` rules), Python 3.11 syntax | Every push and PR | `pyproject.toml`, `ci.yml` (`checks`) |
| The shell scripts that run as root are free of shellcheck findings | shellcheck, style level | Every push and PR | `ci.yml` (`checks`) |
| Every file has a licence and copyright | REUSE 3.3 lint | Every push and PR | `REUSE.toml`, `ci.yml` (`checks`) |
| The X11 side works against a real X server; test coverage does not fall | All tests under Xorg's dummy driver (`tests/x11/`); coverage.py, fails below 78 % | Every push and PR | `ci.yml` (`tests-x11`) |
| No secrets anywhere in the history | gitleaks over every commit | Every push and PR | `ci.yml` (`secrets-and-workflows`) |
| The workflows are safe: least-privilege tokens, SHA-pinned actions, no template injection, no persisted credentials | zizmor, pedantic level | Every push and PR | `ci.yml` (`secrets-and-workflows`) |
| Pinned actions stay current | Dependabot, weekly, 7-day cooldown | Weekly | `.github/dependabot.yml` |
| Deeper code analysis | CodeQL (security-extended); Semgrep `p/python` + `p/security-audit` | Weekly / monthly | `codeql.yml`, `deep-checks.yml` |
| The tests notice broken code | Mutation testing (mutmut) on the pure modules | Monthly | `deep-checks.yml` |
| Repository security practices | OpenSSF Scorecard | Weekly and on `main` | `scorecard.yml` |
| Decisions were made by a human | Decision log, ADRs, human overrides | — | `docs/decisions.md`, `docs/adr/`, `docs/human-overrides.md` |

Every action is pinned to a full commit SHA, and every workflow starts with no or read-only permissions.

## What is not verified

- **Some paths have no automated tests.** The settings window (`gui.py`) has 0 % coverage. The video
  pipeline's logic (`pipeline.py`: description, GPU/CPU choice and fallback, frame gate, health,
  capture times, handing frames on) is tested against a fake GStreamer; a real capture-and-encode run
  needs the Intel GPU and is checked by hand. The X11 side *is* tested against a real X server (Xorg's dummy
  driver in a container, `tests/x11/`): window and damage tracking, pointer, key and cursor events,
  touch injection through XTest, and the virtual monitor's RandR modes, placement, resizing and
  removal. Two things there are still simulated: the kernel's force switch (a sysfs write) with the
  "connected" state it causes, and rotation (the dummy driver cannot rotate, so portrait is checked by
  hand). The daemon's main loop is tested end to end with a fake receiver and a fake usbmuxd. Nothing
  runs against a real GPU, usbmuxd or iPad. They are checked by hand against the acceptance criteria in
  `PROJECT_SPEC.md` §10, by the owner, on one machine (Intel HD 530, Linux Mint 22.3).
- **Ubuntu's packages are trusted as shipped.** GStreamer, PyGObject, python-xlib, usbmuxd and the
  VA-API drivers come from the distribution. Their vulnerabilities are tracked and fixed by
  Ubuntu/Mint, not here, which is why there is no pip-audit or osv-scanner: the project has no
  PyPI dependencies or lockfile for them to scan.
- **The capability check knows only what its rules name.** It is this project's own rules file, not
  an independent scanner (none mature exists for Python; see `docs/decisions.md`). Code that reaches
  a capability through an API the rules don't list would not be flagged.
- **The SBOM is thin.** It identifies the `.deb` and its hash. The authoritative dependency list is
  the package's own metadata: `dpkg-deb -I opendisplay-x11_*_all.deb`.
- **Reproducibility needs the same toolchain.** On 2026-10-15, two builds from fresh clones built 70 s
  apart gave byte-identical files. So did a clean `ubuntu:24.04` container against the owner's
  Mint 22.3 machine. CI repeats the two-build check on every push. A later Ubuntu
  update to dpkg or debhelper can change the bytes; the release's `.buildinfo` lists the exact
  versions used.
- **The iPad app is third-party.** OpenDisplay (`com.peetzweg.opensidecar.ios`) is not part of this
  project or its checks. Its protocol (v3) has no authentication. Anyone on the iPad's Wi-Fi, and any
  local user through the world-writable usbmuxd socket, can connect to it and replace the picture or
  receive your touches; they cannot see your desktop or click on your PC (README, Security).
- **Untested hardware.** AMD and other Intel GPUs are untested; NVIDIA's proprietary driver cannot work.

## Baselined findings

Recorded here so CI fails only on new problems. Nothing was silently suppressed.

| Check | Baselined | Why |
|---|---|---|
| ruff | `S603`, `S607` (subprocess calls) disabled project-wide | Every call is listed above and gated by the capability check instead |
| ruff | `S101` (assert) disabled in `tests/` | pytest asserts |
| ruff | `odx11/activity.py` `close()`: `S110` (`except: pass`) marked `noqa` | Closing an X connection that is already gone; nothing to handle |
| ruff, shellcheck | `spike/` excluded | Throwaway M0 feasibility scripts, not shipped |
| coverage | Floor 78 % (measured 82 %, with the X11 tests) | 100 %: annexb, config, cursor, input, notify, pipeline, protocol, usbmux; ≥ 90 %: activity, checks, daemon, display, settings, state, upgrade, `__main__`. 0 %: gui (the window, checked by hand) |
| mutation testing | 2,010 mutants, 92.7 % killed (2026-10-15). annexb, config, cursor, pipeline, protocol, settings and state: 100 %. edid.py is excluded: it serves only a boot-time EDID fallback that is unused at runtime (`docs/decisions.md`). input 48 %: its 147 untested mutants are all in the X11 injector, which the X11 tests exercise but the mutation job (run without an X server) cannot reach | Raised from ~70 % by tests for every config limit, exact messages and wording, the iPad's `hello` fields, the liveness check (a mutant sending SIGHUP survived before), PNG structure and touch edges. Mutants that cannot change behaviour are marked `# pragma: no mutate` with the reason (about 15). Reported monthly; not a gate yet |
| capability check | `odx11/pipeline.py:169` matches `cap-screen-capture` | A docstring mentioning `ximagesrc`; harmless |
| lintian | `appstream-metadata-missing-modalias-provide` for the udev rule | The rule grants a permission; it is not hardware support that AppStream should advertise |
| manual updates | gitleaks binary (SHA256 in `ci.yml`), the `fsfe/reuse` and `semgrep/semgrep` images (by digest), mutmut's version (the X11 test image's Ubuntu base is updated by Dependabot) | Pinned inside `run:` steps, which Dependabot does not update |

## Human direction

How the owner directed the AI is recorded in three places:

- [`docs/human-overrides.md`](docs/human-overrides.md): 17 places where the owner constrained or
  corrected the AI, each quoting the owner's own message with its date. All confirmed by the
  owner (2026-10-15).
- [`docs/adr/`](docs/adr/README.md): the five most significant decisions, reconstructed from the
  spec, history and code.
- [`docs/decisions.md`](docs/decisions.md): every decision with its reason and status, kept as
  they are made.

## Verify a release yourself

Replace `v0.4.2` with the release you downloaded. You need `gh` (GitHub CLI) and Docker.

**1. The files are the ones published, and GitHub's runners built them from this repository:**

```bash
TAG=v0.4.2
gh release download "$TAG" -R cray2015/opendisplay-x11
sha256sum -c SHA256SUMS
gh attestation verify "opendisplay-x11_${TAG#v}_all.deb" -R cray2015/opendisplay-x11
```

`gh attestation verify` checks the Sigstore signature. It also shows the workflow
(`.github/workflows/release.yml`) and the commit that produced the file.

**2. The source produces the same file (independent rebuild):**

```bash
TAG=v0.4.2
docker run --rm ubuntu:24.04 bash -c "
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    git ca-certificates build-essential dpkg-dev debhelper dh-python pybuild-plugin-pyproject \
    python3-all python3-setuptools python3-pytest python3-gi desktop-file-utils appstream >/dev/null &&
  git clone -q --depth 1 -b $TAG https://github.com/cray2015/opendisplay-x11 /src && cd /src &&
  dpkg-buildpackage -b -us -uc >/dev/null && sha256sum ../opendisplay-x11_*_all.deb"
cat SHA256SUMS
```

The two SHA256 values must match. If they don't, compare your toolchain with the release's
`.buildinfo` (`diff` the `Installed-Build-Depends` section) before suspecting the release.

**3. Read what it can do:** the table above, `packaging/70-opendisplay-x11.rules` (2 lines), and
`debian/postinst`, `prerm`, `postrm`.

Report security problems privately: see [SECURITY.md](SECURITY.md).

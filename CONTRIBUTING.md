# Contributing to OpenDisplay for X11

Thanks for helping. Bug reports, fixes, translations and hardware reports
("works / doesn't work on my GPU and desktop") are all welcome.

## Reporting a problem

Open an issue and paste the **diagnostic report**: open *OpenDisplay for X11*
from the application menu → Help → *Copy diagnostic report*. It contains the
versions, the setup checklist and the recent log — no personal data beyond
your monitor's name and the iPad's model.

## Making a change

```bash
git clone https://github.com/cray2015/opendisplay-x11.git && cd opendisplay-x11
python3 -m venv --system-site-packages .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q tests/       # needs no iPad, X server or GPU
bash install.sh --check                    # can this machine build and run it?
bash install.sh                            # build the .deb from your tree and install it
```

Before sending a change:

- **Tests pass** (`pytest`), and the package builds: `bash install.sh --build`
  runs the tests, `desktop-file-validate` and `appstreamcli validate` inside the
  build. `lintian dist/*.deb` must stay clean.
- **Read [`CLAUDE.md`](CLAUDE.md)** — the project's invariants (wire-format rules,
  no root code, no command channel into the daemon, every tunable in the config,
  every user-visible string in `_()`). Design and decisions: [`PROJECT_SPEC.md`](PROJECT_SPEC.md).
- **Keep it quiet:** nothing may poll; idle cost must stay near zero.
- **Every user-facing change keeps "no terminal for end users"** (spec §2).
- New source files start with the licence header:
  ```
  # SPDX-FileCopyrightText: 2026 Your Name
  # SPDX-License-Identifier: GPL-3.0-or-later
  ```

## Sign your commits (DCO)

This project uses the [Developer Certificate of Origin](https://developercertificate.org/):
by adding a `Signed-off-by` line you certify that you wrote the change or
otherwise have the right to contribute it under the project's licence.

```bash
git commit -s -m "Fix the thing"
# adds: Signed-off-by: Your Name <you@example.com>
```

## Licence

By contributing you agree that your contribution is licensed under the
**GPL-3.0-or-later**, like the rest of the project (see [`LICENSE`](LICENSE)).
You keep the copyright to your work, and you are credited in the git history
(and in the About window's credits for significant contributions).

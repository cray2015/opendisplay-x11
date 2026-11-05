# SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
# SPDX-License-Identifier: GPL-3.0-or-later
#
# A throwaway X server for the X11 tests: Xorg's dummy driver (RandR with custom modes, like a
# real GPU's outputs), plus what the tests import. Same image locally and in CI:
#   docker build -t odx11-x11 tests/x11
#   docker run --rm -v "$PWD:/src:ro" odx11-x11
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      xserver-xorg-core xserver-xorg-video-dummy x11-xserver-utils xcvt \
      python3-gi gir1.2-glib-2.0 gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
      python3-xlib python3-pytest python3-coverage python3-venv \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home tester
COPY xorg-dummy.conf run.sh mutation.sh /x11/
ENV ODX11_X11_TESTS=1
ENTRYPOINT ["/x11/run.sh"]
CMD ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/"]

<!--
SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
SPDX-License-Identifier: GPL-3.0-or-later
-->
# 0005 — Process frames on the GPU when possible, fall back to the CPU

- **Status:** Accepted (2026-10-15, 0.4.0–0.4.2) · *Reconstructed 2026-10-15 from PROJECT_SPEC.md §8.8, docs/perf and commits 1e282b1, 12b1a05, 6111b9a*
- **Deciders:** the owner (asked whether the GPU could cut CPU use, ran the experiment on a separate branch, judged the result visually, named the setting), with Claude

## Context
With Mint's free Intel driver, the HD 530 can encode H.264 but has no video-processing entrypoint,
so converting each frame to the encoder's colour format ran in software: ~32 % of a core at 60 fps.
`intel-media-va-driver-non-free` adds the entrypoint.

## Decision
`[video] convert = "auto"` (default) uses `vaapipostproc` when GStreamer registers it, and falls back
to `videoconvert` if the GPU pipeline fails to start; `gpu`/`cpu` force a choice. The window shows it
as **Processing: Auto / iGPU / GPU / CPU**. The package only *suggests* the non-free driver: it
replaces the free one, so the user chooses.

## Evidence
Measured on 1568×1088 at 60 fps: 45 % → 12 % of one core; colour error 3.5 → 2.9 levels; latency
and iPad encode time lower in every preset (README "Performance", `docs/perf/results-0.3.8-gpu-*.csv`).
The owner compared all three settings live on the iPad before the merge.

## Consequences
- Big CPU saving where the driver allows; unchanged behaviour where it doesn't.
- AMD and NVIDIA paths are untested.

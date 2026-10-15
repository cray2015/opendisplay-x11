<!--
SPDX-FileCopyrightText: 2026 Vibhanshu Biswas
SPDX-License-Identifier: GPL-3.0-or-later
-->
# Alpha testing

Thank you for trying OpenDisplay for X11 before it is finished. It has been used daily on one
machine (Intel HD 530, Linux Mint 22.3 Cinnamon, iPad Air 5th gen); your machine is the test.
Reports of what **worked** are as useful as bugs.

**Before you start**

- Linux Mint 22 / Ubuntu 24.04 or newer, in an **X11** session (not Wayland).
- A **free video port** (an empty HDMI socket, or a DisplayPort that takes HDMI adapters) — see the
  README's [Why a free video port is required](../README.md#why-a-free-video-port-is-required).
- The OpenDisplay app on the iPad ([TestFlight](https://testflight.apple.com/join/3NYaY11c)) and a USB
  **data** cable.
- To hear about new versions: on GitHub, **Watch → Custom → Releases**.

**How to report:** open an issue with the *Alpha tester report* form (or *Bug report* for a single
problem) and paste the diagnostic report: in the window, **Help → Copy diagnostic report**.
Glance over it first; the log can name your computer and iPad.

Mark each item **worked / did not work / not tried**, with a note if anything looked odd.

## 1. Install and first connection

- [ ] Download the `.deb` from the [latest release](https://github.com/cray2015/opendisplay-x11/releases/latest);
      optionally [verify it](../TRUST.md#verify-a-release-yourself).
- [ ] Double-click it and install with your password. Note any warning the installer shows.
- [ ] Open **OpenDisplay for X11** from the application menu. Does the setup checklist show what you
      expect (session, free port, permission, encoder)?
- [ ] Turn it on, plug in the iPad, unlock it, tap **Trust**, open the OpenDisplay app. The desktop
      extends onto the iPad, with no command typed.
- [ ] Arrange the iPad monitor in your desktop's display settings.

## 2. Everyday use

- [ ] **Tap** clicks; **drag** moves a window and selects text.
- [ ] **Long-press** (½ s) and lift opens a right-click menu.
- [ ] **Two-finger scroll** scrolls, in the natural direction.
- [ ] The **cursor** follows the mouse smoothly on the iPad, also while dragging and with a menu open.
- [ ] **Alt+Tab** with the pointer on the iPad's monitor shows the switcher there.
- [ ] A **video** and **scrolling** on the iPad's monitor look smooth. Note any stutter, and whether the
      same thing stutters on your main monitor too.
- [ ] **Rotate the iPad** to portrait and back; the monitor follows.

## 3. Settings

- [ ] **Performance**: switch between Power saver, Balanced and Best quality.
- [ ] **Processing**: switch between Auto, iGPU / GPU and CPU (the picture blinks and returns).
- [ ] **Text size**: change it (the iPad blinks and the size changes).
- [ ] **Right-click sensitivity** and **Screen timeout** change behaviour as described.
- [ ] Turn the service **off and on** with the switch.
- [ ] Notifications appear only when you need to do something, and once per change.

## 4. Connections and power

- [ ] **Unplug** the cable: windows on the iPad's monitor come back to the main monitor.
- [ ] **Plug in again**: the iPad monitor returns where you placed it.
- [ ] **Switch apps on the iPad** for a few seconds, then come back: windows stay where they were.
- [ ] **Reboot**, log in, plug in: it starts on its own and keeps your placement.
- [ ] **Suspend the computer** with the iPad connected, then resume. (Not tested yet.)
- [ ] Leave it running through a **long working session** (2 hours or more): any stall, corruption, or
      growing CPU or memory use? (Not tested yet.)
- [ ] Leave the desktop **idle** for 10 minutes: the system monitor shows OpenDisplay near 0 % CPU.

## 5. Help section and removal

- [ ] **Copy diagnostic report** puts a report on the clipboard.
- [ ] **Remove the virtual monitor now** removes it.
- [ ] Uninstall the package with your software manager: no extra monitor is left behind, and nothing
      keeps running.

## Already known

See the README's [Limitations](../README.md#limitations). In short: X11 only; one iPad at a time; the
Apple Pencil works as a finger; no audio; NVIDIA's own driver cannot work; USB-C-only and
DisplayPort-only machines are untested (most likely not working); only Intel graphics has been tested.

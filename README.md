# Klipper — BTT TFT35-SPI V1.0

Klipper source and screen-helper support for the **BIGTREETECH TFT35-SPI V1.0**
stock display on a **BIQU B1 SE Plus**, using a **BTT Manta M5P + CB1**.
The verified display controller is **ST7796**, with a 480 × 320 screen and rotary encoder.

## Screen features

- Live printer status, temperatures, fan speeds, elapsed/remaining time and network address.
- Printer controls for homing, bounded jogging, heaters and the part fan.
- Simple **Load filament / Unload filament**: confirm once, automatically heat, then feed.
  The direct-drive default is **20 mm**, adjustable from 5–50 mm. When the nozzle is cold,
  the default temperature is **210°C for PLA**; other materials need their own temperature.
  The screen provides heating progress, cancellation and completion instructions.
- **Resume print** directly on the paused main screen, using the existing Klipper `RESUME`
  macro. Loading/unloading never automatically resumes a paused print.
- NetworkManager Wi-Fi/IPv4 setup, text brightness and orderly host shutdown.

The helper moves only the extruder during filament changes, preserves G-code state,
checks temperature and print state, and leaves the nozzle hot after feeding. The
completion screen includes **Turn nozzle off**. An installed runout sensor must be
configured separately in Klipper.

## Hardware and setup

Read [the wiring, installation and bring-up notes](docs/B1_Panel_Bringup.md) before installation.
Those notes include historical deployment details; earlier test counts and policy
states describe earlier releases. The current panel suite contains **42 tests**.

- [Sample panel configuration](config/sample-b1-panel.cfg) leaves wiring verification
  and status/menu activation disabled until the installation is reviewed.
- [Host-helper example settings](scripts/b1-panel-host.example.json) keep network changes locked
  and require a recovery path before enabling them.
- [Initial installer](scripts/b1-panel-install.py) installs the reviewed, staged helper.
- [Incremental installer](scripts/b1-panel-install-filament.py) backs up existing installed files,
  verifies the reviewed baseline, applies the update and restarts only when the printer is idle.

The EXP pin mapping is specific to this verified M5P installation. In particular,
**EXP2_8 is the printer MCU reset line** and is not used as a display reset pin.
The screen depends on Klipper and cannot independently operate through an MCU failure.

## Tests

Run the offline suite from the repository root:

```sh
python3 -m unittest discover -s test/b1_panel -v
```

Tests do not heat, move, print, change networking or shut down hardware. Live validation
confirmed menu transfers and Klipper/helper readiness; automatic filament operation still
needs a supervised physical heat/feed test.

## Upstream and license

Based on [Klipper](https://github.com/Klipper3d/klipper), with the original source history
and GPLv3 license retained. This branch also includes the printer's existing probe safety
and endstop work. See [COPYING](COPYING).

---

Welcome to the Klipper project!

[![Klipper](docs/img/klipper-logo-small.png)](https://www.klipper3d.org/)

https://www.klipper3d.org/

The Klipper firmware controls 3d-Printers. It combines the power of a
general purpose computer with one or more micro-controllers. See the
[features document](https://www.klipper3d.org/Features.html) for more
information on why you should use the Klipper software.

Start by [installing Klipper software](https://www.klipper3d.org/Installation.html).

Klipper software is Free Software. See the [license](COPYING) or read
the [documentation](https://www.klipper3d.org/Overview.html). We
depend on the generous support from our
[sponsors](https://www.klipper3d.org/Sponsors.html).

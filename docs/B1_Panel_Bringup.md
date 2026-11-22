# B1 SE Plus stock panel hardware test

## Latest deployment: network unlock and repaint tuning

Owner explicitly enabled network application and chose to reconnect serial
later only if needed. The live `/etc/b1-panel.json` now has
`allow_network_changes: true` and `require_recovery: false`; it is mirrored in
`config/b1-panel-host.json`. Ethernet/serial availability is no longer a
prerequisite for this installation. Confirm/Keep and the 90-second NM
checkpoint rollback remain in force. No connection was changed during this
deployment; the host remained on 10.0.0.225.

The LCD now runs at 2 MHz SPI. The data/CS pins remain asserted across each
rectangle's pixel packets, eliminating three redundant GPIO commands per
packet. Idle transfer budget is 32 packets per 10 ms; printing remains at
two packets per 20 ms. Read-only menu/status queue-drain measurements improved
from 1.77/1.75 seconds to 0.96/1.00 seconds. The LCD ID still reads `003bcb7f`
(ST7796) at the new speed. Klipper returned ready with status displayed.
32 automated tests pass, including rectangle CS continuity and the explicit
deferred-recovery configuration. Backups are in
`/home/biqu/.local/state/b1-panel/unlock-speed-20261121T211922Z`.

The build/test lock mentioned below records the previous release state;
this latest explicit owner-authorized unlock supersedes it.

## Status dashboard and menu release

The CB1 now has the status/menu release installed. `status_menu: True` in
`/home/biqu/printer_data/config/b1-panel.cfg` initializes the verified ST7796
automatically. The default screen shows Idle, Warming, Printing, Complete,
Paused, Cancelled or Error; elapsed print time; approximate remaining time;
nozzle/bed actual and target temperatures; part/hotend/CB1 fan percentages;
current IPv4; and Fluidd at `http://<IP>:81/`. Remaining time is extrapolated
from print duration and progress, marked `~`, and stays unknown at the start.

Press to open Menu, rotate to select, press to enter. Back returns to the
previous menu; Menu > Back returns to status. Available pages:

- Print a file: lists the newest 30 G-code files Moonraker holds (newest
  first). Choosing one shows the name and "Remove anything on the bed" with
  Back selected; Confirm starts it through Moonraker. The host only starts a
  file Moonraker currently lists, and only when the printer is idle.
- Printer controls: Motion/Homing; Heat/Part fan; Adjust Z offset; Calibrate
  bed; Pause; Resume; Cancel; Emergency stop; Load filament; Unload filament;
  Back.
- While printing, the status screen offers Menu and Adjust Z offset.
- When paused, the status screen offers Resume print. Click to review and
  confirm, or rotate to Menu to reach the filament controls. When a filament
  switch sensor reports no filament during a pause, the status screen reads
  "Out of filament / Load new filament, then choose Resume" and offers
  Load, Resume and Menu; Load opens the Load filament setup directly.
- When a print completes, the status screen shows "Print complete!", the file,
  the print time and the bed temperature ("let it cool" until it is at or
  below 35 C, then "remove print"). It offers Print again (confirmation with
  "Remove the old print first"), Save Z offset when the live offset is not
  zero, and Done, which returns to the normal status screen.
- Adjust Z offset: each encoder detent babysteps 0.025 mm immediately with
  `SET_GCODE_OFFSET Z_ADJUST=... MOVE=1` (MOVE only when Z is homed); right
  raises the nozzle, left lowers it. Total offset is limited to +/-1 mm, and
  only one adjustment is in flight at a time; extra rotation accumulates. While
  printing, click returns to status. When idle, click offers Save for future
  prints (`Z_OFFSET_APPLY_PROBE` then `SAVE_CONFIG`, which restarts Klipper),
  Adjust again and Back.
- Calibrate bed: confirmation warns the bed must be empty. The host heats the
  bed to 60 C, homes, runs `Z_TILT_ADJUST` and re-homes Z (when z_tilt is
  configured), runs a full-bed mesh, saves it as the `default` profile, turns
  the bed off and runs `SAVE_CONFIG`. When a KAMP-style macro wraps
  BED_MESH_CALIBRATE, its renamed original is called so stale object outlines
  never shrink the saved mesh. The job runs in the host helper's background so
  the panel stays responsive; a failure is shown and nothing is saved. Resume uses the printer's existing `RESUME` macro; it does not
  bypass that macro's heater/runout checks or simulate a runout sensor.
- Load/Unload: select the option and confirm Heat and load/Heat and unload.
  Insert filament into the extruder before confirming Load; for Unload, wait
  until the screen says to release the lever and gently remove the filament.
  The screen shows actual temperature while heating, then automatically feeds
  when hot. No separate heat or feed confirmation is needed. The default is
  the current nozzle target, or 210 C when cold (PLA); temperature remains
  adjustable on the confirmation page for other materials.
  Back is the last option and is selected by default. Clicking while heating cancels before feeding
  and restores the previous heat target. Heating expires after ten minutes;
  a changed target, new active print or failed heating acknowledgement prevents
  automatic feeding. Feed completion waits for the extruder motion to finish.
  Feed length defaults to 20 mm for this direct-drive extruder and is adjustable
  from 5 to 50 mm per action;
  repeat if more travel is needed. Loading runs at 5 mm/s and unloading at
  10 mm/s. Commands respect the configured minimum extrusion temperature and
  stop 5 C below the configured heater maximum. Cold feed and active-print
  feed are refused; an actually paused print permits filament changes.
  Only the extruder moves, and its G-code state is restored without a return
  move. The nozzle heater remains on; use Heat/Part fan to turn it off when
  finished, or select Turn nozzle off on the completion screen. The completion
  screen offers Extrude more, Retract more, Turn nozzle off and Back, with the
  cursor on the last direction. Extrude more and Retract more immediately feed
  the chosen length again at the hot nozzle and return to the same screen, so
  they can be repeated as often as needed; if the nozzle has cooled the host
  refuses the feed and the panel returns to the Load/Unload setup. Back returns
  to the paused status screen when appropriate. A filament change never resumes the print automatically.
- Motion/Homing: jog X/Y/Z; 0.1/1/10 mm step sizes; Home XY; Home Z; Home All;
  Motors off. Rotation sets a proposed jog distance; movement requires a
  separate confirmation. After the move finishes, the same axis's jog screen
  reopens with zero proposed distance: rotate to jog again, or click at zero
  to return to Motion/Homing. Choosing Back on a confirmation returns to the proposed
  jog so its distance can be adjusted. Repeated inputs while a jog is in flight
  do not queue duplicate moves. Jogging requires the axis homed, idle printer and
  destination within its configured limits. Z-only homing requires XY homed.
  The jog restores the previous G-code state without a return move. Homing
  uses the existing G28 configuration, never force_move or fake homing.
- Heat/Part fan: Preheat PLA 210/60 and Preheat PETG 220/65 (matching the
  installed Orca filament profiles' first layer; refused while a print is
  running or paused), Cool down (all heaters off), nozzle and bed targets,
  part fan percentage, Back.
  Targets are capped below configured maximum temperatures. Automatic hotend
  and CB1 fans are displayed but not overridden. Consequential actions have
  a confirmation (Confirm, then Back) with Back selected by default.
- Network: detected SSIDs from NetworkManager; saved connection or masked
  rotary password entry; DHCP; manual address, subnet prefix/mask, gateway,
  DNS; connection status. Octets wrap 0..255 in both directions, and pressing
  advances to the next octet. Review offers Confirm/Edit/Change/Back.
  The password keyboard ends with <DELETE>, <DONE> and <BACK>.
- Text brightness: 10..100%, saved locally. V1.0's backlight is hard-wired;
  this changes rendered foreground intensity, not backlight power.
- Shut down: Confirm shutdown, then Back; Back is the default. Confirmation first finishes drawing
  "Shutting down / Wait at least 30 seconds / before switching off" and then
  requests a normal systemd poweroff. The host must be idle, heater targets
  off, nozzle below 50 C and bed below 60 C. This is a timed instruction,
  not an independent indication that Linux has halted. A stalled shutdown
  may take longer; the last frame remains on the powered LCD.

### Local host helper and permissions

### Plugin layout

The panel is installed like other Klipper add-ons, outside the Klipper git
checkout. `b1_panel.py`, `b1_panel_ui.py` and `b1-panel-host.py` live
root-owned in `/usr/local/lib/b1-panel`; `~/klipper/klippy/extras/b1_panel.py`
and `b1_panel_ui.py` are only symlinks to them, listed (with the
klipper_tmc_autotune links) in `~/klipper/.git/info/exclude`. Moonraker
therefore does not report the panel as untracked or dirty, and updating or
soft-recovering Klipper leaves the panel in place. A full re-clone of
Klipper removes the symlinks; re-run `sudo python3 b1_panel_plugin.py` from
the staged release to recreate them (it is idempotent and rolls back if
Klipper or the panel does not come up). Both installers create the links.

`b1-panel-host.service` runs as the dedicated non-login `b1-panel` account,
with group `biqu` for its local UNIX socket at `/run/b1-panel/host.sock`.
Root-owned installed code is under `/usr/local/lib/b1-panel`. PolicyKit
grants the specified NetworkManager and poweroff operations to that service
account; ordinary `biqu` shells do not receive that grant. One-time setup
needs sudo, while operating the panel does not ask for a root password.
NetworkManager remains the sole network configuration manager. Passwords
are passed over the local socket and D-Bus, not shell arguments or G-code;
they are masked on screen and excluded from panel status and logs.

For the owner's build/test/cancel session, `/etc/b1-panel.json` has
`allow_network_changes: false`. Browsing, scanning and editing drafts work,
but Apply is blocked. No network changes were made during deployment.
Enable live testing only after explicit authorization and a verified Ethernet
or serial recovery path. The helper enforces that recovery gate in addition
to the UI. A change creates a separate in-memory NM profile and a 90-second
checkpoint; only physical "Keep this connection" saves the profile to disk
for subsequent boots. Failure or timeout rolls back. Original saved profiles
remain untouched. Address, subnet, gateway, DNS, and Wi-Fi credentials are
part of that persistent NM profile. Enterprise/WEP password setup is excluded.

The Klipper unit drop-in removes its network-online ordering so the local
screen need not wait for Wi-Fi association. The helper itself starts after
NetworkManager's daemon, not network-online. The display still depends on
Klipper owning the M5P: it cannot operate through a Klipper/MCU failure or
independently confirm final Linux halt with the present EXP wiring.

### Validation and latency

46 automated tests cover the display transport, real Klipper command parser,
UI editor/cancel flows, password redaction, recovery gate, bounds/homing/idle
guards, heater limits, filament feeding/state restoration, paused dashboard
resume, automatic heat/feed, heating cancellation/failure/timeout, automatic
backend dispatch and action confirmations. An additional 20,000
deterministic UI events ran without external actions. Read-only live tests
transferred all nine normal menu pages and returned to status. No motion,
homing, heat, network application or shutdown was executed as a test.
The physical encoder/LCD transport was previously verified with the owner.

Changed-character rendering replaces full-line refreshes (a single doubled
glyph is 1,024 bytes versus 30,720 for a full line). Idle transfers use up to
16 packets every 10 ms; printing uses two every 20 ms. Status updates run
every two seconds and coalesce while drawing. Entire-page transitions still
take longer than an individual selector or counter update.

`BTT_PANEL_MENU PAGE=status` selects status; PAGE may also be main, printer,
motion, heat, filament, network, ip, manual or brightness for read-only QA. It cannot
synthesize clicks or action confirmations. The extension's status output
does not expose passwords. Installer/config/extension backups are under
`/home/biqu/.local/state/b1-panel/menu-*`.

The incremental filament update uses `scripts/b1-panel-install-filament.py`,
staged with the three changed source files and a `previous-sha256.json`
manifest. Run it with sudo on the CB1 when idle, unpaused, with heater targets
off. It backs up the four installed file locations under
`/home/biqu/.local/state/b1-panel/filament-*`, verifies that the reviewed
installed files have not changed, replaces only those files and restarts the
helper/Klipper. It leaves network policy, systemd units and printer config
untouched, and restores the backup if activation fails. No feed/heating/resume
commands are executed by the installer.

The remainder of this document records the earlier hardware bring-up.

Status: installed and enabled on the CB1 on 2026-11-21. This is a
Klipper-dependent diagnostic extension, not the standalone Wi-Fi panel.
It does not change network connections, run motion commands, heat anything,
flash firmware, or restart services. Installing its configuration requires
a supervised Klipper restart with the printer idle.

## Live installation evidence, 2026-11-21

- Owner confirmed revision 1.0 and standard matching EXP1/EXP2 ribbons.
- Read all 11 active include files and checked configured pins: no panel-pin
  conflicts were found. CB1 uses the same SPI/GPIO/button APIs as this code.
- Installed `/home/biqu/klipper/klippy/extras/b1_panel.py` and enabled
  `/home/biqu/printer_data/config/b1-panel.cfg` from printer.cfg.
- Config backups: `/home/biqu/.local/state/b1-panel/20261121T203444Z`.
- Fixed the initial invalid `B1_PANEL_` command prefix to `BTT_PANEL_`.
  Added real Klipper GCodeDispatch registration coverage; 14 offline tests pass
  after the full-screen clearing fix.
  A process restart was necessary to discard the old Python module cache.
- Klipper returned ready with the panel enabled. `BTT_PANEL_ID` returned raw
  D3 bytes `003bcb7f`; Marlin's `(raw >> 7) & 0xffff` interpretation is `7796`.
  This supports the manual's ST7796 identification on the connected panel.
- Sent `BTT_PANEL_INIT CONTROLLER=ST7796`. Drawing started and Klipper stayed
  ready; visible text/backlight and physical encoder directions await the
  owner's observations. Software counters started at CW=0, CCW=0, press=0.
- Supervised encoder test passed: owner performed 10 clockwise detents,
  11 counterclockwise detents (one extra reported), and one press. Queried
  counters were exactly CW=10, CCW=11, press=1. Physical direction and counts
  are verified. Text/backlight observations remain pending; Klipper stayed ready.
- Owner confirmed readable text on the LCD. First milestone is achieved:
  host IP text was sent and readable text is confirmed; both encoder directions
  and press match the supervised counts. Colored speckles in the untouched gaps
  prompted a full 480x320 black clear before the text strips at initialization.
  The full-screen clearing fix was installed and initialized on the CB1.
  Reloading the process reset the diagnostic counters; the successful 10/11/1
  supervised test above remains the recorded encoder result.
- Boot environment is now backed up and saved with 100 attempts. Its CRC,
  size and the changed retry script were verified. Unattended power-on with
  that setting has not been tested; the preceding recovery boot took 18 tries.

## Verified context and remaining checks

The owner identified BTT TFT35-SPI revision 1.0 and confirmed both cables
terminate at Manta M5P EXP1/EXP2. The backed-up host inventory identifies a
CB1 running Armbian 26.8.3 / Debian 12 with NetworkManager. Live SSH access
was unavailable during preparation; current host software and wiring
orientation still need inspection. The previous address was 10.0.0.225;
it is not hard-coded in this extension.

Before installation, inspect live printer.cfg and its include tree, current
Klipper/MCU versions, active print state, SPI2 users, and every pin below.
Confirm straight-through EXP cables with matching header orientation.
This sample cannot verify physical wiring or collisions outside Klipper.

| Signal | Klipper EXP alias | M5P MCU pin from backed-up board config |
| --- | --- | --- |
| LCD MISO | EXP2_1 | PB14 |
| LCD clock | EXP2_2 | PB13 |
| LCD MOSI | EXP2_6 | PB15 |
| LCD chip select | EXP2_4 | PB9 |
| LCD command/data | EXP2_7 | PC12 |
| Encoder A | EXP2_3 | PB8 |
| Encoder B | EXP2_5 | PC10 |
| Encoder press | EXP1_2 | PD4 |
| Flash deselect | EXP1_8 | PB6 |
| Touch deselect | EXP1_4 | PD6 |

These are documentary mappings, not new wiring instructions. BTT's PDF
schematic numbers the panel connector differently from Klipper EXP aliases;
do not copy its numeric J1/J2 pin numbers directly into a Klipper config.
EXP2_8 is the mainboard RESET signal and must not be toggled as an LCD GPIO.
The published schematic shows no independent backlight GPIO.

## Source evidence

- BTT V1 manual: https://github.com/bigtreetech/TFT35-SPI/blob/master/v1/BTT%20TFT35-SPI%20V1.0%20User%20Guide.pdf
- BTT V1 schematic: https://github.com/bigtreetech/TFT35-SPI/blob/master/v1/Hardware/BTT%20TFT35-SPI%20V1-SCH.pdf
- Original source archive: https://github.com/bigtreetech/BIQU-B1-SE-PLUS/tree/master/B1-SE-PLUS%20firmware
  (`B1-SE-SKR2(2.6.0).rar`, internal folder dated 2021-09-24).
- Original configuration selects `B1_SE_PLUS_SPI`, `BTT_UI_SPI`, and
  `LCD_ENCODER_SUPPORT`. Encoder source uses pull-ups and four transitions
  per step. LCD primitives use 0x2A/0x2B windows and 0x2C pixel writes.
- The manual identifies ST7796. The factory picture-UI initialization has
  an ILI9488 comment. This discrepancy remains unresolved for the physical
  LCD; the diagnostic implements only an explicit ST7796 profile adapted
  from the Marlin `src/lcd/tft_io/st7796s.h` sequence in that archive.

Downloaded reference files and extracted original source are under
`/private/tmp/b1-panel-research`. SHA256 of the inspected factory archive:
`3392e9194a4c86ff960535fb94d2e85a9caac6b094f6f286020b1620e3daa923`.

## Supervised test procedure

1. Recover CB1 access. Read live config and confirm wiring/pin ownership.
   Keep Ethernet or a working serial console available. The old host capture
   had Wi-Fi active and Ethernet NO-CARRIER; Ethernet recovery is not verified.
2. Review `klippy/extras/b1_panel.py` and `config/sample-b1-panel.cfg`.
   After approval, install the extension in the host's Klipper extras,
   copy the sample into printer_data/config, set wiring_verified to True,
   and include that copy. Do not replace the existing board configuration.
   Restart Klipper only with the printer idle and approval to interrupt it.
3. Loading config sets chip selects inactive, DC high, and registers encoder
   inputs. It sends no LCD initialization automatically. Turn the encoder
   slowly, then inspect `BTT_PANEL_STATUS`. Count ten clockwise detents,
   ten counterclockwise detents, then one press. Report actual direction;
   if reversed, exchange the two encoder entries in the configuration later.
   Do not rewire to reverse the direction.
4. Run `BTT_PANEL_ID`. It reads raw register D3 data with CS held through the
   command and response. Preserve the raw bytes, including dummy/shift bits.
   Zeros, FFs, or a plausible value alone do not verify the controller.
   Compare with the controller read protocol before initializing. No other
   initialization profile is silently tried.
5. After identifying ST7796, run `BTT_PANEL_INIT CONTROLLER=ST7796`.
   This sends a software LCD reset, sleep-out delays, a 16-bit RGB565
   initialization, then text strips containing the current host IPv4,
   counters, and a visual confirmation reminder. It does not toggle the
   shared hardware reset line. Allow several seconds for throttled drawing.
   Initialization clears the whole screen to black before writing text strips.
6. Record backlight visible, recognizable text/IP visible, clockwise events,
   counterclockwise events, and press events as five separate observations.
   `initialized` only means the command sequence was sent. The status fields
   `text_verified` and `backlight_verified` intentionally remain False;
   software cannot verify those visual observations.
7. `BTT_PANEL_SHOW` resamples local IPv4 addresses and redraws changed text.
   The displayed IPv4 is the first interface in sorted order; inspect
   `ip -br address` to distinguish Ethernet, Wi-Fi, and virtual interfaces.
   `BTT_PANEL_STOP` cancels unsent transfers and requires INIT before drawing
   again. A shutdown or detected printing/motion state also stops drawing.

## Transport and limitations

All SPI, CS and DC operations use one Klipper MCU command queue. Pixel data
uses at most 48 bytes per packet and eight operations per 20 ms callback.
Initialization delays begin after a zero-length SPI query acknowledges the
preceding queue. No reset GPIO, shell commands, password handling, network
scans, or network mutations exist. Encoder counters work without LCD init.
The button uses a 50 ms press suppression interval; tune only after observing
real hardware. Rotation uses Klipper's existing full-step quadrature decoder.

This code cannot meet the independence requirement while Klipper owns M5P
SPI and inputs. Do not open the M5P USB serial connection from a second
daemon. Once the hardware is proven, choose an independently accessible
transport before implementing NetworkManager SSID/password/manual IPv4 UI.
No firmware or wiring change is implied by this preparation.

## Offline verification

Run `python3 -m unittest discover -s test/b1_panel -v` from the repository root.
Tests cover packet/CS ordering, initialization fencing/delays, text encoding,
input counts/debounce, idle guards and visual verification semantics. They
do not prove wiring, LCD controller identity, physical knob direction,
SPI electrical behavior, or compatibility with the currently offline host.

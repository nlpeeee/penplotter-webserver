# PCP

PCP is a Python web service for working with pen plotters and vinyl cutters:

- Supported devices: **Creation PCut CT-1200**, Graphtec MP4200, HP 7475A
- Created for Raspberry Pi on a local LAN — no authentication or reverse proxy needed.
- Upload *.SVG and *.HPGL files.
- Convert *.SVG into roll-oriented *.HPGL using a live millimetre workspace; [vpype](https://github.com/abey79/vpype) flattens curves and PCP applies the exact displayed transform.
- **Interactive cut workspace** with linked scaling, placement, 90° rotation, mirroring, roll bounds, pan/zoom, travel paths, and animated path order.
- **Persistent job queue and history** — jobs enqueue, exactly one worker owns the serial port at a time.
- Telegram notification on print end
- Poweroff your plotter on print end using a Tasmota-enabled Sonoff controller

## Installation

The legacy installation and service names remain `webplotter` for compatibility. An install script is included.
From the home directory, run:

```bash
curl -O https://raw.githubusercontent.com/henrytriplette/penplotter-webserver/main/install.sh
chmod +x install.sh
```

Then run it:
```bash
./install.sh
```
Raspberry Pi will reboot once installation is completed.

## Usage

After install, open a browser and reach for:
```bash
http://{{your Raspberry Pi address}}:5000
```

Optional:
Configure options in *config.ini* (copy from *config.ini.sample*) using the web interface to set:
- Serial port and device profile.
- Tasmota device IP.
- Telegram Chat ID for notifications.

## Raspberry Pi — Creation PCut CT-1200 Setup

### 1. Identify the stable serial port

After plugging in the USB-to-serial adapter (typically FTDI-based), find the
stable `by-id` symlink — this will not change between reboots:

```bash
ls -l /dev/serial/by-id/
```

Example output:
```
lrwxrwxrwx 1 root dialout ... usb-FTDI_FT232R_USB_UART_XXXXXXXX-if00-port0 -> ../../ttyUSB0
```

Use the full `by-id` path in `config.ini`:
```ini
port = /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_XXXXXXXX-if00-port0
```

Never hard-code `/dev/ttyUSB0` — that number can shift.

### 2. Add the webplotter user to the dialout group

```bash
sudo usermod -aG dialout pi   # or whatever user runs webplotter
```

Log out and back in (or reboot) for the group change to take effect.

### 3. Verified serial parameters (matches Inkcut's Creation 1200 profile)

| Parameter | Value |
|-----------|-------|
| Baud rate | 9600 |
| Data bits | 8 |
| Parity    | None |
| Stop bits | 1 |
| Flow ctrl | Hardware RTS/CTS |

Select **"Creation PCut CT-1200"** as the device in the UI or in `config.ini`:
```ini
device = creation_1200
baudrate = 9600
```

The `creation_1200` profile sends **only** the raw HPGL payload — no
HP-specific ESC initialisation, status, buffer, or abort commands are ever
sent to the cutter.

### ⚠️  WARNING — Do not run ser2net / RFC 2217 alongside PCP

Running `ser2net` (or any RFC 2217 TCP serial redirector) against the same
port while PCP is active **will corrupt the data stream** and may cause
the cutter to behave erratically.  Ensure ser2net is stopped before starting
PCP:

```bash
sudo systemctl stop ser2net
sudo systemctl disable ser2net   # prevent it from starting on boot
```

## Job Queue

Uploads or "Start Plot" requests are placed in a persistent SQLite queue
(`jobs.db`).  Exactly one background worker owns the serial port at a time —
concurrent writes are impossible.

Job states visible in the **Job History** panel:
- **queued** — waiting for the worker
- **transmitting** — currently being sent to the cutter
- **completed** — full file transmitted
- **failed** — serial or file error (error message shown in status log)
- **cancelled** — stopped before transmission began

Clicking **Stop** on a transmitting job sets the cancellation flag; no further
bytes are sent, but any commands already in the cutter's internal buffer will
still execute.

The large **CANCEL CUT** button also interrupts a blocked serial write. The
**RESET USB / COM** action cancels transmission, resets only the configured
and validated `/dev/serial/by-id/...` USB adapter, waits for it to return, and
probes it at the PCut serial settings. It does not power-cycle the cutter.

## Cut Workspace

Click **Preview** (or the bolt beside an SVG) to open the workspace. SVG files
can be scaled, positioned, rotated, and mirrored before **Generate HPGL** is
used. Width and height stay linked. The loaded roll width is remembered by the
browser and the feed length grows to fit the job. A red path is outside the
roll and cannot be generated. HPGL files open in an exact read-only view.

The **Cut preparation** panel removes duplicate cutter paths and orders open
lines and enclosed contours safely before minimizing pen-up travel. It reports
path, point, distance, and HPGL-size changes. Path merging and simplification
are opt-in and can be compared with the original overlay. Generation requires
the geometry hash returned by the exact server preview, preventing a stale
preview from producing a different cut.

The **Copies and roll layout** panel can arrange multiple uploaded SVG designs
and copy counts in deterministic left-to-right rows. Edge margin and spacing
default to 5 mm. Optional 90-degree rotation is explicit, and any automatic
layout can be changed to manual positioning by dragging a copy or entering its
X/Y position. Collisions and roll overflow are highlighted and block HPGL
generation. Uploaded HPGL remains an exact, read-only preview.

The **Vinyl-cutting aids** panel optionally adds per-copy or whole-layout weed
borders, weed lines through free strips, closed-contour overcut, and
drag-blade carriage compensation. All aids start disabled. The cyan intended
contour can be compared with the actual compensated path; animation and HPGL
use the latter. Invalid/self-intersecting compensation and unsafe weed-border
overlap block generation. A built-in compensation test pattern covers curves,
sharp corners, small details, and closure, and requires explicit media/tool
confirmation before PCP will queue it.

**Material profiles** save roll/layout values, weed settings, blade offset,
overcut, notes, and suggested panel pressure/speed. New profiles start
unverified with compensation disabled. Pressure and speed are an operator
checklist only and are never transmitted to the CT-1200. Profiles can be
verified only after accepting a physical test and can be imported/exported as
PCP JSON; the permanent **Unprofiled** default cannot be deleted.

**Per-cutter calibration** is keyed by the stable serial port and device. PCP
generates an exact 100 × 100 mm square, calculates `100 / measured` X/Y
factors, enforces a 0.90–1.10 range, and asks again for corrections over 2%.
Accepted calibration remains independently switchable. Its factors are shown
in the workspace, applied immediately before HPGL quantization, and included
in calibrated output filenames and generation metadata. Calibration-pattern
transmission requires an explicit media/tool confirmation.

## Running Tests

```bash
python -m unittest discover -s tests -v
```

## ToDO

- [x] Fix Mobile UI
- [x] Add plotter name to toolbar
- [x] Add defaults to configuration file
- [x] Stop print via UI?
- [x] List current printing filename
- [x] Creation PCut CT-1200 support
- [x] Persistent job queue + history (SQLite)
- [x] Stable /dev/serial/by-id port support
- [x] SVG and HPGL cut-path preview
- [x] Interactive millimetre workspace and animated cut-order preview
- [x] Immediate cancel and guarded USB serial reset controls
- [x] Preflight diagnostics and safe cut-path optimization
- [x] Multiple SVG copies and deterministic roll layout
- [x] Weeding, overcut, drag-blade compensation, and guarded test pattern
- [x] Material profiles, operator checklist, and per-cutter X/Y calibration

## Contributing
Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## Attribution

PCP is a derivative of [WebPlot](https://github.com/henrytriplette/penplotter-webserver), created by Henry Triplette in 2021. The original project name, repository reference, and contribution history are retained here for attribution and operational compatibility. See [NOTICE](NOTICE) for a concise attribution notice.

## License

PCP and the original WebPlot software are distributed under the [MIT License](LICENSE). The bundled license retains the original `Copyright (c) 2021 Henry Triplette` notice.

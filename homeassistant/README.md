# RD6006P for Home Assistant — 0.1.2

Local Modbus TCP integration and a matching dashboard card for **RD6006P model
60065** using the riden-dongle Wi-Fi bridge. Other PSU models supported by the
dongle firmware are not supported by this HA integration.

[Инструкция на русском](README_RU.md)

## Features

- Read actual output voltage/current/power, input voltage, CV/CC and protection.
- Set voltage/current and switch output ON/OFF with a readback after each write.
- Show battery mode, battery voltage, session charge (Ah) and energy (Wh).
- Show voltage/current history from HA Recorder/History, with separate axes.
- Recover automatically after HA starts while the PSU is powered off.
- Discover the integration's entities automatically, including renamed entities
  and multiple PSUs selected by serial number.

Runtime dependencies: Home Assistant and the local TCP bridge. MQTT, HACS,
Mushroom, ApexCharts and card-mod are not required. This is a manual-install
custom integration; it is not a HACS repository layout.

The card supports English/Russian, narrow screens and wide panel dashboards.
Open `demo.html` for a standalone preview with simulated data and controls.

## Install

1. Download the **rd6006p-homeassistant** artifact from a successful
   [Firmware checks](https://github.com/Taxom/riden-dongle/actions/workflows/checks.yaml)
   run and extract the enclosed `RD6006P_HA_v0.1.2.zip`.
   Alternatively, use this `homeassistant/` directory from the source checkout.
2. Copy `custom_components/rd6006p` into `<HA config>/custom_components/` and
   `www/rd6006p-card.js` into `<HA config>/www/`. The config directory contains
   `configuration.yaml`; it is normally `/config` or `/homeassistant`.
   The included installer can copy these files and back up an older version:

   ```sh
   sh install.sh /config
   ```

3. Restart HA Core. Add **Riden RD6006P** under **Settings → Devices & services**.
   Enter the Wi-Fi module's host/IP, port `502`, and unit ID `1` unless changed.
   The form intentionally has no prefilled device address. Setup checks the
   model/serial using reads only.
4. Add a dashboard resource of type **JavaScript module**:

   ```text
   /local/rd6006p-card.js?v=0.1.2
   ```

   If Resources is hidden, enable Advanced mode in your user profile. YAML-mode
   dashboards can register the same URL under `lovelace.resources` with
   `type: module`. Reload the browser with Ctrl+F5 after changing the resource.
5. Add a manual card using `dashboard_card.yaml`:

   ```yaml
   type: custom:rd6006p-card
   title: Riden RD6006P
   hours_to_show: 1
   language: en
   ```

   If multiple PSUs are present, add `serial: "<device serial>"`. A panel view
   provides the wide two-column layout; `dashboard_view.yaml` is an example.

The installer only copies integration/card files. It does not restart HA,
change HA configuration, contact the PSU or write any hardware state.

## Upgrade from 0.1.0 / 0.1.1

Replace the same integration directory and JS file, restart HA Core, and edit
the existing resource URL to `?v=0.1.2`. Do not register a second copy of the
same custom element. Keep the existing integration and entity IDs.

Version 0.1.2 fixes the battery-voltage scale and adds the four battery fields
to the card. Entities disabled by older integration defaults are enabled on
upgrade; entities explicitly disabled by the user stay disabled. Connection
settings and configured write limits are preserved. The no-blinking and
offline-startup recovery fixes from 0.1.1 remain included.

## Controls and data

Voltage/current edits and the +/- buttons only change a draft. Apply or Enter
sends the command; Escape cancels it. Output ON/OFF are separate absolute
actions. Visible state is read from the PSU, including changes made by its
front panel, TestController or another client.

Initial HA write limits are **3 V / 0.1 A**. Change them in integration options
up to **60.1 V / 6 A**. Saving options does not change hardware setpoints or
OVP/OCP. Before ON, fresh setpoints are checked against the HA limits; OFF
remains available independently of them. These limits do not replace hardware
protection or lock out other clients.

One persistent serialized TCP connection handles polling and commands. Main
registers are read every 2 seconds; identity/battery blocks every 20 seconds by
default. Uncertain writes are reconciled by reading rather than repeated
automatically. Setup, reload and reconnection do not restore output/setpoints.

| Value | Meaning |
|---|---|
| Output V/A | RD6006P register steps: 0.001 V / 0.0001 A |
| Battery voltage | Register 33, raw / 100: 417 means 4.17 V |
| Charging mode | Read-only battery-mode flag, register 32; separate from CV/CC |
| Ah / Wh | Resettable PSU session counters, not full battery capacity or state of charge |
| External temperature | Optional, disabled by default; also requires the connected-probe option |

UniSoft can show three decimals for V-BATT on its own screen; the standard
battery register used here transmits hundredths. A third displayed zero would
not recover the missing digit. Battery/output measurements use separate
channels; the integration does not apply an arbitrary calibration offset.

Battery counters deliberately have no `total_increasing` state class, because
the PSU can reset them. A failed slow battery read only makes the affected
entities unavailable. A failed main read disables control and current live
values until polling succeeds. Genuine zeros remain zeros.

## Development and validation

Validated against **Home Assistant Core 2026.11.1 / CPython 3.14.10** with 67
HA/protocol tests and DOM/recovery tests. These are software checks; they do
not establish physical ADC accuracy or full multi-client hardware acceptance.
See [test details and limitations](docs/TEST_REPORT.md) and
[protocol sources](docs/SOURCES.md).

Use an isolated Python 3.14 environment and Node.js 24:

```sh
python -m pip install -r requirements-dev.txt
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p pytest_asyncio.plugin tests
ruff check custom_components tests build_package.py
npm ci --ignore-scripts
npm test
python build_package.py
```

The build script writes a standalone ZIP plus a SHA-256 sidecar into the
repository's `dist/` directory. Runtime payload paths stay at the ZIP root,
so the same installer works outside the source repository. Each ZIP also
contains per-file checksums. Development dependencies are not installed in HA.

For optional real-browser checks, install Playwright and Chromium separately
and run `node tests/test_card.cjs`. The DOM suite itself requires no browser
or external service, and all Modbus tests use a loopback simulator.

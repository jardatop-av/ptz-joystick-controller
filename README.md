# PTZ Joystick Controller

## Stage53 logical source selector

The Config button-mapping editor now uses a switcher-aware logical source dropdown for `preview_source` actions. Source IDs remain stored as strings such as `Input 4`; runtime and configuration schemas are unchanged.

First implementation skeleton for config, models, persistent storage, preset manager, event bus and state-machine core.

This stage intentionally does **not** implement:

- USB joystick reading
- VISCA communication
- switcher communication
- discovery
- network changes
- web GUI

The project can start without a joystick, cameras or switchers connected.

## Run tests

```bash
python -m pytest
```

## Smoke run

```bash
python -m ptz_joystick_controller.main --config config.example.yaml
```

## Manual vMix integration smoke test

The first real vMix integration layer is available through the switcher backend and can be smoke-tested without GUI:

```bash
python scripts/manual_vmix_integration.py --host 192.168.1.100 --port 8088 --debug
```

The script polls vMix PROGRAM/PREVIEW state by default. It sends commands only when `--send-commands` is provided.

## Local configuration overrides

`config.example.yaml` is kept as a generic, version-controlled example. For machine-specific values, copy `config.local.example.yaml` to `config.local.yaml` and edit only the values that differ locally.

When loading `config.example.yaml`, the application also loads `config.local.yaml` from the same directory if it exists. Local values are applied after the example config and override matching sections, including camera entries by `id` and source mappings by `source_id`.

`config.local.yaml` is ignored by Git.

## Stage46: Osee GoStream Duet 8 ISO GSP control probe

Stage46 adds an isolated, model-specific GSP source mapping and control wrapper for
GoStream Duet 8 ISO firmware 2.1.0. It is intentionally not connected to the main
runtime Osee adapter yet.

Logical source positions are translated only inside the Duet adapter:

- Input 1..4 -> 1..4
- Input 5..8 -> 4001..4004
- MP1 -> 3010
- MP2 -> 3020
- M/SRC -> 5001

Manual examples:

```bash
python scripts/manual_osee_duet_control.py --host 192.168.1.58 --preview 5 --watch
python scripts/manual_osee_duet_control.py --host 192.168.1.58 --preview MP1 --watch
python scripts/manual_osee_duet_control.py --host 192.168.1.58 --cut --watch
python scripts/manual_osee_duet_control.py --host 192.168.1.58 --auto --watch
```

## Stage47 — Osee GoStream Duet 8 ISO runtime backend

The verified GSP transport is now available through the generic switcher runtime.
Use `switcher.type: osee` (normalized internally to `osee_gostream_duet`) with a
configured host and optional port; the default GSP TCP port is `19010`.
Generic runtime code uses logical sources `Input 1` through `Input 8`, `MP1`,
`MP2`, and `M/SRC`. Device-side numeric GSP source IDs remain private to the
Duet adapter.

## Stage47 Fix — main runtime backend selection

The main runtime can now be started directly with:

```bash
python -m ptz_joystick_controller.main --config config.example.yaml
```

`switcher.type` selects exactly one runtime backend:

- `vmix` — vMix HTTP backend, default port 8088
- `atem` / `atem_mini_pro` — existing ATEM abstraction
- `osee` / `osee_gostream_duet` — verified Osee GoStream Duet 8 ISO GSP backend, default TCP port 19010

The Osee runtime exposes only logical sources (`Input 1`–`Input 8`, `MP1`, `MP2`, `M/SRC`). Numeric GSP source IDs remain private to the Duet adapter.

## Stage48 — configurable AUTO joystick action

- Adds generic `auto` joystick button action for every canonical button, including Trigger.
- Web configuration exposes AUTO and ignores/disables source and preset payload fields for this action.
- AUTO is dispatched through the abstract switcher interface.
- Active PTZ movement is stopped through the existing `before_auto` safety path before the transition command.


## Stage49 — Osee initial state and eight PTZ slots

- GSP command type `res` is accepted alongside `get`, `set`, and `pus`.
- Osee startup requests `pgmIndex`, `pvwIndex`, and `transitionStatus`; response or push messages initialize generic runtime state.
- Initial Preview propagates through the normal event/state path and activates the mapped PTZ camera without Osee-specific routing.
- Osee configurations receive logical Input 1–8 camera slots (`cam1`–`cam8`) and default mappings; MP1, MP2, and M/SRC remain unmapped.
- The structured config page exposes all eight camera slots, VISCA IDs, and logical source-to-camera mappings.

## Stage50 — production VISCA UDP runtime

- Normal `RuntimeApplication` startup now selects the verified real VISCA-over-IP UDP transport automatically.
- `--dry-run` keeps the existing fake VISCA transport and never sends UDP packets.
- Explicitly injected PTZ transport factories continue to override the production default for tests and custom integrations.
- Enabled cameras require a configured host; disabled camera slots do not create transport sessions.
- Startup logs the selected PTZ transport mode once.
- The Diagnostics VISCA section now spans the available desktop width, keeps the target address on one line where practical, and scrolls horizontally on smaller screens.


## Stage51 — web UI usability and dark theme

- Dark theme is the default across Dashboard, Config and Diagnostics.
- A shared Dark/Light theme toggle persists the browser preference in localStorage.
- The active navigation page is highlighted consistently.
- Config provides Save and Save and Apply controls at both the top and bottom of the form.
- Unsaved form changes are indicated without affecting theme selection.
- Diagnostics uses a wider responsive desktop layout with horizontally scrollable tables on narrow screens.
- Runtime, switcher, joystick, PTZ, VISCA, schema and systemd behavior are unchanged.

## Manual read-only network discovery

Stage52 Lite adds a standalone operator probe. It does not start the production runtime, write configuration, or send switching/PTZ movement commands.

```bash
python scripts/manual_network_discovery.py --cidr 192.168.1.0/24
python scripts/manual_network_discovery.py --interface eth0 --protocols vmix,osee,visca --debug
```

Only private RFC1918 or IPv4 link-local networks up to /16 are accepted. ATEM is intentionally not actively identified until a verified read-only handshake exists in the project.

## Stage52A embedded discovery panel

The Config page now contains a collapsed, read-only **Discovery** panel. It reuses the Stage52 Lite vMix, Osee GSP and VISCA probes, keeps results only in browser memory, and never writes configuration or starts the production runtime. Scans support bounded concurrency, progress polling, cancellation, and clipboard copy helpers for IP and IP:port values.

## Stage54 — read-only ATEM probe

Stage54 adds an isolated ATEM UDP probe for safely verifying a switcher before any control integration. It performs only the mandatory ATEM session handshake/ACK maintenance and parses incoming state (`_pin`, `_ver`, `PrgI`, `PrvI`, `InPr`, `InCm`). It is not connected to `RuntimeApplication`.

Example:

```bash
python scripts/manual_atem_probe.py --host 192.168.1.184 --port 9910 --duration 20 --debug
```


## Stage55 — ATEM Television Studio 4K8 manual control probe

Stage55 extends the verified Stage54 UDP session with an isolated manual-control layer for exactly three M/E 1 operations: Preview selection (`CPvI`), native CUT (`DCut`), and native AUTO (`DAut`). It is **not** connected to `RuntimeApplication`, joystick control, PTZ routing, web UI, configuration, or systemd. Command success is confirmed from normal `PrvI`/`PrgI` feedback rather than UDP send success. `TrPs` is parsed for transition diagnostics.

Stage55 Fix moves the client-local reliable packet sequence into the shared ATEM UDP session instead of resetting a second counter in the manual-control subclass. The first reliable command in a fresh session uses packet ID 1, subsequent commands increment monotonically, and reconnect starts a fresh sequence. Switcher ACK-only packets are correlated through the ATEM header `ack_id` and are not passed to the state command parser. Manual control diagnostics now distinguish transport ACK timeout from `PrvI`/`PrgI` state-feedback timeout. The verified `CPvI`, `DCut`, and `DAut` payload layouts are unchanged.

Target product name: **ATEM Television Studio 4K8**.

Manual test:

```bash
python scripts/manual_atem_control_test.py --host 192.168.1.184 --port 9910 --timeout 2 --debug
```

Interactive commands are `preview SOURCE_ID`, `cut`, `auto`, `state`, `inputs`, and `quit`.


### Stage55 Fix 2 — continuous ATEM receive loop

The isolated ATEM manual-control client can now run a single dedicated background UDP receiver for the full interactive session. `scripts/manual_atem_control_test.py` starts that receiver immediately after the verified Stage54 handshake/state initialization and before entering `input()`. Physical `PrgI`, `PrvI`, `TrPs`, and `InPr` updates therefore continue to refresh local state while the prompt is idle. Transport ACK packets are dispatched to per-packet waiters by the same receiver; command methods never perform a competing socket read while the background receiver is active. Quit/Ctrl+C closes the socket and stops the receiver cleanly. The `CPvI`, `DCut`, and `DAut` payloads and reliable packet sequencing from Stage55 Fix remain unchanged.

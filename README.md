# PTZ Joystick Controller

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

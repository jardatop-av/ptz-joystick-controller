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

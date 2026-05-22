
## Real USB joystick runtime layer

The joystick layer keeps the existing offline abstraction and fake provider, but can also use real devices through optional runtime backends:

- Linux: evdev provider for `/dev/input/event*` devices.
- Windows: pygame fallback provider.

Startup remains safe when no joystick is attached. The runtime monitor reports a disconnected health state, periodically allows reconnect attempts, and does not crash the application. Disconnect or read failures are converted into health state changes and event bus notifications.

Calibration can be persisted separately from the main config so that axis min/center/max values can be adjusted without rewriting the whole application configuration.

Manual test tool:

```bash
python scripts/manual_joystick_test.py --debug
```

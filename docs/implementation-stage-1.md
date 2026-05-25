# Implementation stage 1

Implemented now:

- datové modely
- runtime config parsing and validation
- persistent storage
- atomic write
- preset manager
- last preset auto-load
- event bus
- state machine skeleton
- unit tests

Not implemented yet:

- USB joystick reading
- VISCA communication
- switcher communication
- discovery
- network changes
- web GUI

## Stage 11 – Joystick-to-vMix bridge

Implemented the first runtime bridge between joystick button events and switcher commands.

Included:

- `runtime/joystick_switcher_bridge.py`
- `runtime/switcher_executor.py`
- configurable button-to-action execution through the existing joystick mapping layer
- PreviewInput action execution
- Cut action execution
- Fade/Auto action execution
- Copy Program To Preview action execution
- runtime program/preview synchronization
- active PTZ recomputation after preview/program changes
- STOP-on-transition request before Cut/Fade when enabled in config
- safe reconnect handling for the switcher backend
- safe startup without joystick connected
- safe startup without vMix available
- manual integration tool: `scripts/manual_joystick_vmix_bridge.py`

Still not implemented in this stage:

- GUI rendering
- websocket frontend
- PTZ camera sockets
- Raspberry Pi deployment
- discovery scanning
- Osee runtime bridge
- ATEM runtime bridge

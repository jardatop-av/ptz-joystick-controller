from __future__ import annotations

APP_NAME = "PTZ Joystick Controller"
DEFAULT_WEB_PORT = 8080
DEFAULT_VISCA_PORT = 52381

SUPPORTED_SWITCHERS = {
    "vmix",
    "atem_mini_pro",
    "atem_tv_studio_pro_4k",
    "osee_gostream_deck",
    "osee_gostream_duet",
}

BUTTON_ACTIONS = {
    "none",
    "cut",
    "auto",
    "copy_program_to_preview",
    "preview_source",
}

LOG_LEVELS = {"debug", "info", "warning", "error", "critical"}

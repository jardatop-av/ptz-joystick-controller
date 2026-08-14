from __future__ import annotations

from html import escape
import json
from datetime import datetime
import time
from pathlib import Path
from typing import Any

import yaml

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .config_editor import ConfigEditError, ConfigEditor
from .config_runtime import RuntimeConfigApplier
from .discovery_panel import DiscoveryJobManager
from ..config import ConfigError
from ..joystick.button_metadata import CANONICAL_BUTTON_IDS, ButtonMetadataRegistry
from ..models.joystick import ButtonAction
from .status import RuntimeStatusProvider
from .auth import ADMIN_USERNAME, AuthError, AuthStore, LoginRateLimiter, SessionManager, validate_password
import logging
import secrets


THEME_BOOTSTRAP = """<script>
(function () {
  const saved = localStorage.getItem('ptz-theme');
  document.documentElement.dataset.theme = saved === 'light' ? 'light' : 'dark';
})();
</script>"""

COMMON_THEME_CSS = """
:root {
  color-scheme: dark;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --bg: #0b0d10;
  --panel: #171a1f;
  --panel-alt: #20242b;
  --text: #f1f3f5;
  --muted: #aeb6c2;
  --border: #3a414c;
  --input-bg: #101318;
  --input-border: #596372;
  --accent: #66a8ff;
  --accent-strong: #2f7dd3;
  --success: #55d68b;
  --warning: #ffd166;
  --error: #ff7b86;
  --focus: #9bc8ff;
  --row-alt: #1c2026;
}
:root[data-theme="light"] {
  color-scheme: light;
  --bg: #f3f5f8;
  --panel: #ffffff;
  --panel-alt: #edf1f5;
  --text: #171a1f;
  --muted: #5d6672;
  --border: #c8d0da;
  --input-bg: #ffffff;
  --input-border: #8994a2;
  --accent: #075fba;
  --accent-strong: #0b69c7;
  --success: #137a43;
  --warning: #8a5b00;
  --error: #b4232f;
  --focus: #075fba;
  --row-alt: #f7f9fb;
}
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--text); }
a { color: var(--accent); }
.topnav {
  display: flex; align-items: center; justify-content: space-between; gap: 1rem;
  flex-wrap: wrap; padding: .65rem .8rem; margin: 0 0 1rem;
  background: var(--panel); border: 1px solid var(--border); border-radius: .7rem;
}
.nav-links { display: flex; align-items: center; gap: .35rem; flex-wrap: wrap; }
.topnav a { color: var(--text); text-decoration: none; padding: .4rem .65rem; border-radius: .4rem; }
.topnav a:hover { background: var(--panel-alt); }
.active-page a { background: var(--accent-strong); color: #fff; font-weight: 700; }
.theme-toggle { display: inline-flex; align-items: center; gap: .45rem; }
.theme-toggle button { margin: 0; padding: .4rem .65rem; }
button, input, select, textarea { font: inherit; }
button {
  border: 1px solid var(--input-border); border-radius: .45rem; background: var(--panel-alt);
  color: var(--text); cursor: pointer;
}
button:hover { border-color: var(--accent); background: color-mix(in srgb, var(--panel-alt) 75%, var(--accent) 25%); }
button:active { transform: translateY(1px); }
button.primary { background: var(--accent-strong); color: #fff; border-color: var(--accent); }
input, select, textarea {
  background: var(--input-bg); color: var(--text); border: 1px solid var(--input-border); border-radius: .35rem;
}
input:disabled, select:disabled, textarea:disabled, button:disabled { opacity: .58; cursor: not-allowed; }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.message { background: var(--panel-alt); color: var(--text); }
.ok { color: var(--success); }
.bad, .error { color: var(--error); }
.warning { color: var(--warning); }
code, .mono { color: inherit; }
"""

THEME_SCRIPT = """<script>
(function () {
  const root = document.documentElement;
  const button = document.getElementById('theme-toggle');
  function currentTheme() { return root.dataset.theme === 'light' ? 'light' : 'dark'; }
  function updateLabel() {
    if (!button) return;
    const next = currentTheme() === 'dark' ? 'Light' : 'Dark';
    button.textContent = next + ' theme';
    button.setAttribute('aria-label', 'Switch to ' + next.toLowerCase() + ' theme');
  }
  if (button) {
    button.addEventListener('click', function () {
      const next = currentTheme() === 'dark' ? 'light' : 'dark';
      root.dataset.theme = next;
      localStorage.setItem('ptz-theme', next);
      updateLabel();
    });
  }
  updateLabel();
})();
</script>"""


def render_navigation(active: str) -> str:
    links = []
    for key, label, href in (
        ('dashboard', 'Dashboard', '/'),
        ('config', 'Config', '/config'),
        ('diagnostics', 'Diagnostics', '/diagnostics'),
    ):
        link = f'<a href="{href}">{label}</a>'
        if key == active:
            link = f'<span class="active-page" aria-current="page">{link}</span>'
        links.append(link)
    return (
        '<nav class="topnav">'
        f'<div class="nav-links">{"".join(links)}</div>'
        '<div class="theme-toggle"><span>Theme</span><button type="button" id="theme-toggle">Light theme</button>'
        '__LOGOUT_CONTROL__</div>'
        '</nav>'
    )


DASHBOARD_HTML = f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PTZ Joystick Controller</title>
  {THEME_BOOTSTRAP}
  <style>
    {COMMON_THEME_CSS}
    body {{ margin: 0; padding: 1rem; }}
    .page-shell {{ max-width: 1600px; margin: 0 auto; }}
    header {{ margin-bottom: 1rem; }}
    h1 {{ font-size: 1.4rem; margin: 0 0 .25rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: .75rem; }}
    section {{ border: 1px solid var(--border); border-radius: .75rem; padding: .85rem; background: var(--panel); }}
    h2 {{ font-size: 1rem; margin: 0 0 .5rem; }}
    dl {{ display: grid; grid-template-columns: max-content 1fr; gap: .3rem .8rem; margin: 0; }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    ul {{ margin: .25rem 0 0; padding-left: 1.2rem; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .9rem; }}
    @media (max-width: 520px) {{ body {{ padding: .6rem; }} dl {{ grid-template-columns: 1fr; }} dt {{ font-weight: 700; }} }}
  </style>
</head>
<body><div class="page-shell">
<header>
  {render_navigation('dashboard')}
  <h1 id="title">PTZ Joystick Controller</h1>
  <div id="subtitle" class="mono">Loading…</div>
</header>
<div class="grid">
  <section><h2>System</h2><dl id="system"></dl></section>
  <section><h2>Joystick</h2><dl id="joystick"></dl></section>
  <section><h2>Switcher</h2><dl id="switcher"></dl></section>
  <section><h2>PTZ</h2><dl id="ptz"></dl></section>
  <section><h2>Safety</h2><dl id="safety"></dl></section>
  <section><h2>Config</h2><dl id="config"></dl></section>
  <section><h2>Configured Cameras</h2><ul id="cameras"></ul></section>
  <section><h2>Recent activity</h2><ul id="events"></ul></section>
</div>
<script>
function dtdd(key, value) {{ return `<dt>${{key}}</dt><dd>${{value ?? ''}}</dd>`; }}
function boolBadge(value) {{ return value ? '<span class="ok">connected</span>' : '<span class="bad">disconnected</span>'; }}
function seconds(value) {{ return `${{Math.round(value)}} s`; }}
function byId(id) {{ return document.getElementById(id); }}
function setList(id, rows) {{ const el = byId(id); if (el) el.innerHTML = rows.join(''); }}
function setHtml(id, html) {{ const el = byId(id); if (el) el.innerHTML = html; }}
async function refresh() {{
  try {{
    const r = await fetch('/api/status', {{cache: 'no-store'}});
    const s = await r.json();
    const title = byId('title'); if (title) title.textContent = s.system.application_name;
    const subtitle = byId('subtitle'); if (subtitle) subtitle.textContent = `${{s.system.stage}} / ${{s.system.version}} / uptime ${{seconds(s.uptime)}}`;
    setList('system', [dtdd('Application', s.system.application_name), dtdd('Version', s.system.version), dtdd('Stage', s.system.stage), dtdd('Uptime', seconds(s.uptime))]);
    setList('joystick', [dtdd('Status', boolBadge(s.joystick.connected)), dtdd('Device', s.joystick.device_name), dtdd('Buttons', (s.joystick.pressed_buttons || []).join(', ') || 'none'), dtdd('Hat', `${{s.joystick.hat.direction}} (${{s.joystick.hat.x}}, ${{s.joystick.hat.y}})`), dtdd('Pan/Tilt/Zoom', `${{s.joystick.normalized_axes.pan}} / ${{s.joystick.normalized_axes.tilt}} / ${{s.joystick.normalized_axes.zoom}}`)]);
    setList('switcher', [dtdd('Status', boolBadge(s.switcher.connected)), dtdd('Type', s.switcher.type), dtdd('Program', s.program), dtdd('Preview', s.preview), dtdd('Transition', s.transition ?? '—')]);
    setList('ptz', [dtdd('Active camera', s.active_ptz_camera), dtdd('Moving', s.ptz.moving), dtdd('Pan/Tilt active', s.ptz.pan_tilt_active), dtdd('Zoom active', s.ptz.zoom_active), dtdd('Hat active', s.ptz.hat_active), dtdd('Last action', s.ptz.last_action)]);
    setList('safety', [dtdd('Watchdog', s.safety.watchdog_enabled), dtdd('Center samples', s.safety.center_confirm_samples), dtdd('Output deadzone', `pan/tilt=${{s.safety.output_deadzone.pan_tilt}}, zoom=${{s.safety.output_deadzone.zoom}}`)]);
    setList('config', [dtdd('Loaded at', s.config?.loaded_at), dtdd('Pending changes', s.config?.pending_changes), dtdd('Last apply', s.config?.last_apply_result || 'none'), dtdd('Last error', s.config?.last_apply_error || '')]);
    setHtml('cameras', (s.ptz.configured_cameras || []).map(c => `<li>${{c.active ? '▶ ' : ''}}${{c.name}} (${{c.id}}) — ${{c.enabled ? 'enabled' : 'disabled'}} ${{c.host || ''}}</li>`).join('') || '<li>none</li>');
    setHtml('events', (s.recent_activity || []).map(e => `<li><span class="mono">${{e.created_at}}</span> ${{e.type}}</li>`).join('') || '<li>none</li>');
  }} catch (e) {{
    const subtitle = byId('subtitle'); if (subtitle) subtitle.textContent = `Status refresh failed: ${{e}}`;
  }}
}}
refresh();
setInterval(refresh, 1000);
</script>
{THEME_SCRIPT}
</div></body>
</html>
"""



DIAGNOSTICS_HTML = f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PTZ Joystick Controller Diagnostics</title>
  {THEME_BOOTSTRAP}
  <style>
    {COMMON_THEME_CSS}
    body {{ margin: 0; padding: 1rem; }}
    .diagnostics-container {{ width: min(100%, 1880px); margin: 0 auto; }}
    h1 {{ font-size: 1.4rem; margin: 0 0 1rem; }}
    h2 {{ font-size: 1.05rem; margin: 0 0 .5rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .85rem; }}
    section {{ border: 1px solid var(--border); border-radius: .75rem; padding: .85rem; background: var(--panel); min-width: 0; }}
    .table-wrap {{ overflow-x: auto; max-width: 100%; border: 1px solid var(--border); border-radius: .45rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .88rem; table-layout: fixed; background: var(--panel); }}
    tbody tr:nth-child(even) {{ background: var(--row-alt); }}
    .ptz-section, .visca-section, .runtime-section {{ grid-column: 1 / -1; width: 100%; }}
    .visca-table {{ min-width: 1180px; }}
    .visca-table th:nth-child(1), .visca-table td:nth-child(1) {{ width: 17%; }}
    .visca-table th:nth-child(2), .visca-table td:nth-child(2) {{ width: 23%; }}
    .visca-table th:nth-child(3), .visca-table td:nth-child(3) {{ width: 8%; }}
    .visca-table th:nth-child(4), .visca-table td:nth-child(4) {{ width: 52%; }}
    .visca-target {{ white-space: nowrap; word-break: normal; overflow-wrap: normal; }}
    .visca-payload {{ white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; }}
    th, td {{ border-bottom: 1px solid var(--border); padding: .38rem .45rem; text-align: left; vertical-align: top; overflow-wrap: anywhere; word-break: break-word; }}
    th {{ background: var(--panel-alt); }}
    td.mono, .hex, .details {{ overflow-wrap: anywhere; word-break: break-word; white-space: pre-wrap; }}
    dl {{ display: grid; grid-template-columns: max-content 1fr; gap: .25rem .7rem; margin: 0; }}
    dt {{ color: var(--muted); }} dd {{ margin: 0; overflow-wrap: anywhere; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .86rem; }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 620px) {{ body {{ padding: .6rem; }} .visca-table {{ min-width: 900px; }} }}
  </style>
</head>
<body><div class="diagnostics-container">
{render_navigation('diagnostics')}
<h1>Runtime Diagnostics</h1>
<div class="grid">
  <section><h2>Joystick diagnostics</h2><dl id="joystick"></dl></section>
  <section><h2>Switcher diagnostics</h2><dl id="switcher"></dl></section>
  <section class="ptz-section"><h2>PTZ diagnostics</h2><div class="table-wrap"><table><thead><tr><th>Time</th><th>Camera</th><th>Action</th><th>Details</th></tr></thead><tbody id="ptz"></tbody></table></div></section>
  <section class="visca-section"><h2>VISCA diagnostics</h2><div class="table-wrap visca-table-wrap"><table class="visca-table"><thead><tr><th>Time</th><th class="visca-target">Target</th><th>Dir</th><th>Hex payload</th></tr></thead><tbody id="visca"></tbody></table></div></section>
  <section class="runtime-section"><h2>Runtime log</h2><div class="table-wrap"><table><thead><tr><th>Time</th><th>Level</th><th>Event</th><th>Details</th></tr></thead><tbody id="runtime"></tbody></table></div></section>
</div>
<script>
function esc(v) {{ return String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }}
function dtdd(k, v) {{ return `<dt>${{esc(k)}}</dt><dd>${{esc(v)}}</dd>`; }}
function shortTime(ts) {{ return ts ? esc(ts).replace('T', ' ').replace('+00:00', 'Z') : ''; }}
function json(v) {{ try {{ return esc(JSON.stringify(v)); }} catch {{ return esc(v); }} }}
function setHtml(id, value) {{ const el = document.getElementById(id); if (el) el.innerHTML = value; }}
async function refreshDiagnostics() {{
  try {{
    const r = await fetch('/api/diagnostics', {{cache: 'no-store'}});
    const d = await r.json();
    const j = d.joystick || {{}};
    setHtml('joystick', [dtdd('State', j.connected ? 'connected' : 'disconnected'), dtdd('Device', j.device_name), dtdd('Raw axes', json(j.raw_axes)), dtdd('Normalized axes', json(j.normalized_axes)), dtdd('Output deadzone', json((d.ptz || {{}}).output_deadzone || 'see status')), dtdd('Hat', `${{j.hat?.direction || 'center'}} (${{j.hat?.x || 0}}, ${{j.hat?.y || 0}})`), dtdd('Buttons', (j.pressed_buttons || []).join(', ') || 'none'), dtdd('Last seen/error', `${{j.last_seen_at || ''}} ${{j.last_error || ''}}`)].join(''));
    const sw = d.switcher || {{}};
    setHtml('switcher', [dtdd('State', sw.connected ? 'connected' : 'disconnected'), dtdd('Type', sw.type), dtdd('Preview', sw.preview_source), dtdd('Program', sw.program_source), dtdd('Last sync', sw.last_sync_time), dtdd('Last HTTP error', sw.last_http_error), dtdd('Last command', sw.last_command)].join(''));
    setHtml('ptz', (d.ptz_actions || []).map(a => `<tr><td class="mono">${{shortTime(a.timestamp)}}</td><td>${{esc(a.camera_id)}}</td><td>${{esc(a.action_type)}}</td><td class="mono">${{json(a.details)}}</td></tr>`).join('') || '<tr><td colspan="4">No PTZ actions</td></tr>');
    setHtml('visca', (d.visca_packets || []).map(p => `<tr><td class="mono">${{shortTime(p.timestamp)}}</td><td class="visca-target mono">${{esc(p.host)}}:${{esc(p.port)}}</td><td>${{esc(p.direction)}}</td><td class="mono visca-payload">${{esc(p.hex_payload)}}</td></tr>`).join('') || '<tr><td colspan="4">No VISCA packets</td></tr>');
    setHtml('runtime', (d.runtime_events || []).map(e => `<tr><td class="mono">${{shortTime(e.timestamp)}}</td><td>${{esc(e.level)}}</td><td>${{esc(e.event_type || e.type)}}</td><td class="mono">${{json(e.details)}}</td></tr>`).join('') || '<tr><td colspan="4">No runtime events</td></tr>');
  }} catch (e) {{ setHtml('runtime', `<tr><td colspan="4">Diagnostics refresh failed: ${{esc(e)}}</td></tr>`); }}
}}
refreshDiagnostics();
setInterval(refreshDiagnostics, 1000);
</script>
{THEME_SCRIPT}
</div></body>
</html>"""



BUTTON_ACTION_OPTIONS = (
    ButtonAction.PREVIEW_SOURCE,
    ButtonAction.PRESET_RECALL,
    ButtonAction.NONE,
    ButtonAction.CUT,
    ButtonAction.AUTO,
    ButtonAction.COPY_PROGRAM_TO_PREVIEW,
)


def _checked(value: bool) -> str:
    return " checked" if value else ""


def _selected(current: object, option: object) -> str:
    return " selected" if str(current) == str(option) else ""


def _html_value(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def _button_action_options(current: ButtonAction) -> str:
    return "".join(
        f'<option value="{escape(action.value, quote=True)}"{_selected(current.value, action.value)}>{escape(action.value)}</option>'
        for action in BUTTON_ACTION_OPTIONS
    )


def _source_select_options(source_options: list[str], current: object) -> str:
    current_value = "" if current is None else str(current)
    options = list(source_options)
    if current_value and current_value not in options:
        options.insert(0, current_value)
    return "".join(
        f'<option value="{_html_value(source_id)}"{_selected(current_value, source_id)}>{escape(source_id)}</option>'
        for source_id in options
    )


def render_config_html(config_editor: ConfigEditor, *, message: str = "", csrf_token: str = "", authentication_disabled: bool = False) -> str:
    payload = config_editor.editable_payload()
    registry = ButtonMetadataRegistry(getattr(config_editor.current_config.joystick, "button_labels", {}))
    switcher = payload["switcher"]
    ptz = payload["ptz"]
    joystick = payload["joystick"]
    source_mappings = {item["source_id"]: item for item in payload.get("sources", {}).get("mappings", [])}
    cameras = ptz["cameras"]
    buttons = joystick["buttons"]
    source_options = payload.get("source_options", [])
    source_options_by_switcher = payload.get("source_options_by_switcher", {})
    # Render the raw editor from the currently loaded configuration without
    # applying save-time validation. Generic example configs may intentionally
    # contain disabled/incomplete hardware placeholders; validation runs on
    # submit before writing config.local.yaml.
    raw_yaml = yaml.safe_dump(config_editor.patch_to_local_override_unvalidated(payload), sort_keys=False, allow_unicode=True)

    camera_rows = []
    supported_source_set = set(source_options)
    for index, camera in enumerate(cameras):
        candidate_source = f"Input {index + 1}"
        logical_source = candidate_source if candidate_source in supported_source_set else ""
        mapping = source_mappings.get(logical_source, {})
        mapping_field = logical_source.replace(" ", "_").replace("/", "_") if logical_source else ""
        mapping_control = (
            f"<input name='source_mapping_{mapping_field}' value='{_html_value(mapping.get('ptz_camera_id', camera['id']))}'>"
            if logical_source else "—"
        )
        camera_rows.append(
            "<tr>"
            f"<td>{escape(logical_source)}</td>"
            f"<td>{mapping_control}</td>"
            f"<td><code>{escape(str(camera['id']))}</code><input type='hidden' name='camera_{index}_id' value='{_html_value(camera['id'])}'></td>"
            f"<td><input type='checkbox' name='camera_{index}_enabled'{_checked(bool(camera['enabled']))}></td>"
            f"<td><input name='camera_{index}_name' value='{_html_value(camera['name'])}'></td>"
            f"<td><input name='camera_{index}_host' value='{_html_value(camera.get('host'))}'></td>"
            f"<td><input type='number' min='1' max='65535' name='camera_{index}_port' value='{_html_value(camera['port'])}'></td>"
            f"<td><input type='number' min='1' max='7' name='camera_{index}_visca_id' value='{_html_value(camera.get('visca_id', 1))}'></td>"
            f"<td><input type='number' min='0' max='255' name='camera_{index}_preset_offset' value='{_html_value(camera.get('preset_offset', 0))}'></td>"
            "</tr>"
        )

    button_rows = []
    for button_id in CANONICAL_BUTTON_IDS:
        mapping = buttons.get(button_id, {"action": ButtonAction.NONE.value})
        action = ButtonAction(str(mapping.get("action", ButtonAction.NONE.value)))
        label = registry.label_for(button_id)
        button_rows.append(
            "<tr>"
            f"<td><code>{escape(button_id)}</code></td>"
            f"<td>{escape(label)}</td>"
            f"<td><select class='button-action' data-button-id='{escape(button_id)}' name='button_{button_id}_action'>{_button_action_options(action)}</select></td>"
            f"<td class='button-source-cell' data-button-id='{escape(button_id)}'{'' if action == ButtonAction.PREVIEW_SOURCE else ' hidden'}><select class='button-source-id' data-button-id='{escape(button_id)}' name='button_{button_id}_source_id'>{_source_select_options(source_options, mapping.get('source_id'))}</select></td>"
            f"<td><input class='button-preset-number' data-button-id='{escape(button_id)}' type='number' min='0' max='255' name='button_{button_id}_preset_number' value='{_html_value(mapping.get('preset_number'))}'></td>"
            "</tr>"
        )

    deck_special_mapping_html = ""
    if switcher.get("type") == "osee_gostream_deck":
        camera_ids = [str(camera["id"]) for camera in cameras]
        rows = []
        for source_id in ("AUX", "STILL1", "STILL2", "S/SRC"):
            mapping = source_mappings.get(source_id, {})
            current_camera = mapping.get("ptz_camera_id")
            field = source_id.replace(" ", "_").replace("/", "_")
            options = [f"<option value=''{' selected' if not current_camera else ''}>None</option>"]
            options.extend(
                f"<option value='{_html_value(camera_id)}'{_selected(current_camera, camera_id)}>{escape(camera_id)}</option>"
                for camera_id in camera_ids
            )
            rows.append(
                "<tr>"
                f"<td>{escape(source_id)}</td>"
                f"<td><select name='source_mapping_{field}'>{''.join(options)}</select></td>"
                "</tr>"
            )
        deck_special_mapping_html = (
            "<fieldset><legend>GoStream Deck additional PTZ source mappings</legend>"
            "<p>AUX may be mapped to any configured PTZ camera or None. STILL1, STILL2 and S/SRC default to None.</p>"
            "<table><thead><tr><th>logical source</th><th>PTZ camera</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></fieldset>"
        )

    status_message = message or "Basic form saves only to config.local.yaml. Use Save and apply to update the running process."
    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PTZ Joystick Controller Config</title>
  {THEME_BOOTSTRAP}
  <style>
    {COMMON_THEME_CSS}
    body {{ margin: 0; padding: 1rem; }}
    .config-container {{ max-width: 1760px; margin: 0 auto; }}
    header {{ margin-bottom: 1rem; }}
    h1 {{ font-size: 1.4rem; margin: 0 0 .25rem; }}
    h2 {{ margin-top: 1.3rem; font-size: 1.1rem; }}
    fieldset {{ border: 1px solid var(--border); border-radius: .75rem; margin: .75rem 0; padding: .85rem; background: var(--panel); }}
    label {{ display: inline-block; margin: .3rem .8rem .3rem 0; }}
    input, select, textarea {{ max-width: 100%; }}
    input[type=text], input[type=number], input:not([type]) {{ padding: .35rem; min-width: 8rem; }}
    textarea {{ width: 100%; min-height: 40vh; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .9rem; }}
    table {{ border-collapse: collapse; width: 100%; min-width: 980px; }}
    fieldset:has(table) {{ overflow-x: auto; }}
    th, td {{ border-bottom: 1px solid var(--border); padding: .35rem; text-align: left; }}
    th {{ background: var(--panel-alt); }}
    button {{ padding: .65rem 1rem; font-weight: 700; }}
    .action-bar {{ position: sticky; top: .35rem; z-index: 5; display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; padding: .65rem; margin: .6rem 0 .9rem; background: color-mix(in srgb, var(--panel) 94%, transparent); border: 1px solid var(--border); border-radius: .65rem; backdrop-filter: blur(8px); }}
    .action-bar.bottom {{ position: static; margin-top: .9rem; }}
    .unsaved-indicator {{ display: none; color: var(--warning); font-weight: 700; }}
    .unsaved-indicator.visible {{ display: inline; }}
    .message {{ margin: .75rem 0; padding: .6rem; border-radius: .5rem; border: 1px solid var(--border); }}
    .discovery-panel {{ border: 1px solid var(--border); border-radius: .75rem; margin: .75rem 0; background: var(--panel); overflow: hidden; }}
    .discovery-panel > summary {{ cursor: pointer; font-weight: 700; padding: .85rem; background: var(--panel-alt); }}
    .discovery-body {{ padding: .85rem; }}
    .discovery-controls {{ display: flex; align-items: center; flex-wrap: wrap; gap: .55rem; margin-bottom: .65rem; }}
    .discovery-advanced {{ margin: .65rem 0; padding: .65rem; border: 1px solid var(--border); border-radius: .5rem; }}
    .discovery-progress {{ display: none; align-items: center; gap: .55rem; margin: .55rem 0; }}
    .discovery-progress.visible {{ display: flex; }}
    .spinner {{ width: 1rem; height: 1rem; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin .8s linear infinite; }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    .discovery-table-wrap {{ overflow-x: auto; max-width: 100%; border: 1px solid var(--border); border-radius: .45rem; }}
    .discovery-table {{ min-width: 760px; table-layout: fixed; }}
    .discovery-table th:nth-child(1) {{ width: 10%; }} .discovery-table th:nth-child(2) {{ width: 12%; }}
    .discovery-table th:nth-child(3) {{ width: 18%; }} .discovery-table th:nth-child(4) {{ width: 10%; }}
    .discovery-table th:nth-child(5) {{ width: 34%; }} .discovery-table th:nth-child(6) {{ width: 16%; }}
    .discovery-table td {{ overflow-wrap: anywhere; word-break: break-word; }}
    .copy-actions {{ display: flex; gap: .3rem; flex-wrap: wrap; }}
    .copy-actions button {{ padding: .35rem .5rem; font-weight: 600; }}
    .copy-confirm {{ color: var(--success); min-height: 1.2em; }}
    .ok {{ color: var(--success); }}
    .bad {{ color: var(--error); white-space: pre-wrap; }}
    @media (max-width: 700px) {{ body {{ padding: .6rem; }} th, td {{ min-width: 7rem; }} }}
  </style>
</head>
<body><div class="config-container">
<header>
  {render_navigation('config')}
  <h1>Configuration</h1>
  <p>Safe limited editor. Saves only to <code>config.local.yaml</code>. Never overwrites <code>config.example.yaml</code>.</p>
</header>
<div id="message" class="message ok">{escape(status_message)}</div>

<h2>Basic configuration</h2>
<form method="post" action="/config/basic" id="basic-config-form"><input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
  <div class="action-bar top" data-action-bar="top">
    <button class="primary" type="submit" name="apply" value="0">Save configuration</button>
    <button type="submit" name="apply" value="1">Save and apply configuration</button>
    <span id="unsaved-changes" class="unsaved-indicator" role="status">Unsaved changes</span>
  </div>
  <fieldset>
    <legend>Switcher</legend>
    <label>Type
      <select name="switcher_type" id="switcher-type">
        <option value="vmix"{_selected(switcher.get('type'), 'vmix')}>vMix</option>
        <option value="atem_mini_pro"{_selected(switcher.get('type'), 'atem_mini_pro')}>ATEM Mini Pro</option>
        <option value="atem_television_studio_4k8"{_selected(switcher.get('type'), 'atem_television_studio_4k8')}>ATEM Television Studio 4K8</option>
        <option value="osee_gostream_deck"{_selected(switcher.get('type'), 'osee_gostream_deck')}>Osee GoStream Deck</option>
        <option value="osee_gostream_duet"{_selected(switcher.get('type'), 'osee_gostream_duet')}>Osee GoStream Duet 8 ISO</option>
      </select>
    </label>
    <label>Host <input name="switcher_host" value="{_html_value(switcher.get('host'))}"></label>
    <label>Port <input id="switcher-port" type="number" min="1" max="65535" name="switcher_port" value="{_html_value(switcher.get('port'))}"></label>
    <small id="switcher-sources"></small>
  </fieldset>

  <details id="discovery-panel" class="discovery-panel">
    <summary>Discovery</summary>
    <div class="discovery-body">
      <p>Read-only network scan. Results are not written to configuration.</p>
      <div class="discovery-controls">
        <button type="button" id="discovery-scan" class="primary">Scan Local Network</button>
        <button type="button" id="discovery-cancel" disabled>Cancel</button>
        <span id="discovery-copy-confirm" class="copy-confirm" role="status"></span>
      </div>
      <details class="discovery-advanced">
        <summary>Advanced</summary>
        <label>Subnet <input id="discovery-cidr" placeholder="Auto-detected local subnet"></label>
        <label>Timeout <input id="discovery-timeout" type="number" min="0.05" max="30" step="0.05" value="0.5"></label>
        <label>Concurrency <input id="discovery-concurrency" type="number" min="1" max="256" value="32"></label>
        <label><input id="discovery-protocol-osee" type="checkbox" checked> Osee</label>
        <label><input id="discovery-protocol-vmix" type="checkbox" checked> vMix</label>
        <label><input id="discovery-protocol-visca" type="checkbox" checked> VISCA</label>
        <label><input id="discovery-protocol-atem" type="checkbox" checked> ATEM</label><span hidden>Read-only discovery not yet implemented.</span>
      </details>
      <div id="discovery-progress" class="discovery-progress"><span class="spinner" aria-hidden="true"></span><span id="discovery-progress-text">Preparing scan…</span></div>
      <div id="discovery-error" class="bad" role="alert"></div>
      <div class="discovery-table-wrap">
        <table class="discovery-table"><thead><tr><th>Type</th><th>Status</th><th>IP Address</th><th>Port</th><th>Details</th><th>Copy</th></tr></thead><tbody id="discovery-results"><tr><td colspan="6">No scan results.</td></tr></tbody></table>
      </div>
    </div>
  </details>

  <fieldset>
    <legend>PTZ Cameras</legend>
    <table><thead><tr><th>logical input</th><th>mapped camera</th><th>id</th><th>enabled</th><th>name</th><th>host</th><th>port</th><th>VISCA ID</th><th>preset_offset</th></tr></thead><tbody>{''.join(camera_rows)}</tbody></table>
  </fieldset>
  {deck_special_mapping_html}

  <fieldset>
    <legend>Joystick Axis</legend>
    <label><input type="checkbox" name="invert_pan"{_checked(bool(joystick['invert']['pan']))}> Reverse pan</label>
    <label><input type="checkbox" name="invert_tilt"{_checked(bool(joystick['invert']['tilt']))}> Reverse tilt</label>
    <label><input type="checkbox" name="invert_zoom"{_checked(bool(joystick['invert']['zoom']))}> Reverse zoom</label>
  </fieldset>

  <fieldset>
    <legend>Buttons</legend>
    <table><thead><tr><th>button id</th><th>human label</th><th>action</th><th>source_id</th><th>preset number</th></tr></thead><tbody>{''.join(button_rows)}</tbody></table>
  </fieldset>

  <fieldset>
    <legend>Safety</legend>
    <label>Output deadzone pan_tilt <input type="number" step="0.01" min="0" max="1" name="output_deadzone_pan_tilt" value="{_html_value(joystick['output_deadzone']['pan_tilt'])}"></label>
    <label>Output deadzone zoom <input type="number" step="0.01" min="0" max="1" name="output_deadzone_zoom" value="{_html_value(joystick['output_deadzone']['zoom'])}"></label>
    <label><input type="checkbox" name="stop_watchdog_enabled"{_checked(bool(ptz['stop_watchdog']['enabled']))}> Stop watchdog enabled</label>
    <label>Center confirm samples <input type="number" min="1" name="center_confirm_samples" value="{_html_value(ptz['stop_watchdog']['center_confirm_samples'])}"></label>
  </fieldset>
  <div class="action-bar bottom" data-action-bar="bottom">
    <button class="primary" type="submit" name="apply" value="0">Save configuration</button>
    <button type="submit" name="apply" value="1">Save and apply configuration</button>
    <span class="unsaved-indicator unsaved-indicator-bottom" role="status">Unsaved changes</span>
  </div>
</form>

<h2>Security</h2>
<fieldset>
  <legend>Admin password</legend>
  <p class="security-status"><strong>Authentication:</strong> {("Disabled (empty admin password)" if authentication_disabled else "Enabled")}</p>
  <form method="post" action="/security/change-password">
    <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
    <label>Current password <input type="password" name="current_password" autocomplete="current-password"></label>
    <label>New password <input type="password" name="new_password" autocomplete="new-password"></label>
    <label>Confirm new password <input type="password" name="confirm_password" autocomplete="new-password"></label>
    <button type="submit">Change password</button>
  </form>
  <small>Any password string is accepted. Changing the password signs out all sessions.</small>
</fieldset>

<h2>Configuration Backup / Restore</h2>
<fieldset>
  <legend>Backup / Restore</legend>
  <p><a href="/config/export">Export current configuration</a></p>
  <form method="post" action="/config/import/validate" enctype="multipart/form-data">
    <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
    <label>Configuration YAML <input type="file" name="config_file" accept=".yaml,.yml,text/yaml"></label>
    <button type="submit">Upload and validate</button>
  </form>
  <small>Authentication credentials and session data are never included in configuration exports.</small>
</fieldset>

<details id="advanced-yaml-panel" class="discovery-panel advanced-yaml-panel">
  <summary>Advanced YAML editor <span id="advanced-yaml-unsaved" class="unsaved-indicator" role="status">Unsaved changes</span></summary>
  <div class="discovery-body">
    <p>This raw editor is still available for advanced overrides and uses the same validation path.</p>
    <form method="post" action="/config/raw" id="advanced-yaml-form"><input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
      <textarea id="advanced-yaml-text" name="raw_yaml" spellcheck="false">{escape(raw_yaml)}</textarea>
      <br><button type="submit" name="apply" value="0">Save Advanced YAML editor</button>
      <button type="submit" name="apply" value="1">Save and apply Advanced YAML editor</button>
    </form>
  </div>
</details>
<script>
const sourceOptionsBySwitcher = {json.dumps(source_options_by_switcher, ensure_ascii=False)};
const switcherType = document.getElementById('switcher-type');
const switcherPort = document.getElementById('switcher-port');
const switcherSources = document.getElementById('switcher-sources');
function currentSourceOptions() {{
  return sourceOptionsBySwitcher[switcherType?.value] || [];
}}
function refreshSourceSelectors() {{
  const options = currentSourceOptions();
  document.querySelectorAll('.button-source-id').forEach(select => {{
    const previous = select.value;
    select.replaceChildren(...options.map(sourceId => {{
      const option = document.createElement('option');
      option.value = sourceId;
      option.textContent = sourceId;
      return option;
    }}));
    if (options.includes(previous)) select.value = previous;
    else if (options.length) select.value = options[0];
  }});
}}
function updateSwitcherHints() {{
  if (!switcherType || !switcherPort || !switcherSources) return;
  const type = switcherType.value;
  if (!switcherPort.value) switcherPort.value = (type === 'osee_gostream_duet' || type === 'osee_gostream_deck') ? '19010' : (type === 'vmix' ? '8088' : ((type === 'atem_television_studio_4k8' || type === 'atem_mini_pro') ? '9910' : ''));
  const options = currentSourceOptions();
  switcherSources.textContent = options.length ? `Logical sources: ${{options.join(', ')}}` : 'No logical sources available';
}}
if (switcherType) switcherType.addEventListener('change', () => {{ switcherPort.value = ''; refreshSourceSelectors(); updateSwitcherHints(); updateUnsavedState(); }});
updateSwitcherHints();
function updateButtonPayloadFields(select) {{
  const buttonId = select.dataset.buttonId;
  const source = document.querySelector(`.button-source-id[data-button-id="${{buttonId}}"]`);
  const sourceCell = document.querySelector(`.button-source-cell[data-button-id="${{buttonId}}"]`);
  const preset = document.querySelector(`.button-preset-number[data-button-id="${{buttonId}}"]`);
  const showSource = select.value === 'preview_source';
  if (source) source.disabled = !showSource;
  if (sourceCell) sourceCell.hidden = !showSource;
  if (preset) preset.disabled = select.value !== 'preset_recall';
}}
document.querySelectorAll('.button-action').forEach(select => {{
  updateButtonPayloadFields(select);
  select.addEventListener('change', () => updateButtonPayloadFields(select));
}});
const basicForm = document.getElementById('basic-config-form');
const initialFormState = basicForm ? new FormData(basicForm) : null;
function formSignature(form) {{
  const entries = Array.from(new FormData(form).entries()).filter(([key]) => key !== 'apply');
  return JSON.stringify(entries);
}}
const initialSignature = basicForm ? formSignature(basicForm) : '';
function updateUnsavedState() {{
  if (!basicForm) return;
  const changed = formSignature(basicForm) !== initialSignature;
  document.querySelectorAll('.unsaved-indicator').forEach(el => el.classList.toggle('visible', changed));
}}
if (basicForm) {{
  basicForm.addEventListener('input', updateUnsavedState);
  basicForm.addEventListener('change', updateUnsavedState);
}}
</script>
<script>
(function () {{
  const panel = document.getElementById('discovery-panel');
  const scanButton = document.getElementById('discovery-scan');
  const cancelButton = document.getElementById('discovery-cancel');
  const progress = document.getElementById('discovery-progress');
  const progressText = document.getElementById('discovery-progress-text');
  const resultBody = document.getElementById('discovery-results');
  const errorBox = document.getElementById('discovery-error');
  const copyConfirm = document.getElementById('discovery-copy-confirm');
  let jobId = null;
  let pollTimer = null;
  if (!panel || !scanButton) return;
  panel.open = localStorage.getItem('ptz-discovery-expanded') === 'true';
  panel.addEventListener('toggle', () => localStorage.setItem('ptz-discovery-expanded', String(panel.open)));
  function esc(value) {{ return String(value ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c])); }}
  function selectedProtocols() {{ return ['osee','vmix','visca','atem'].filter(p => document.getElementById('discovery-protocol-' + p)?.checked); }}
  function setRunning(running) {{ scanButton.disabled = running; cancelButton.disabled = !running; progress.classList.toggle('visible', running); }}
  function renderResults(rows) {{
    if (!rows.length) {{ resultBody.innerHTML = '<tr><td colspan="6">No devices confirmed.</td></tr>'; return; }}
    resultBody.innerHTML = rows.map(row => {{
      const target = row.port == null ? row.ip : `${{row.ip}}:${{row.port}}`;
      const stateClass = row.status === 'confirmed' ? 'ok' : row.status === 'error' ? 'bad' : 'warning';
      return `<tr><td>${{esc(row.type)}}</td><td class="${{stateClass}}">${{esc(row.status.charAt(0).toUpperCase() + row.status.slice(1))}}</td><td class="mono">${{esc(row.ip)}}</td><td>${{esc(row.port ?? '')}}</td><td>${{esc(row.details)}}</td><td><div class="copy-actions"><button type="button" data-copy="${{esc(row.ip)}}">Copy IP</button><button type="button" data-copy="${{esc(target)}}">Copy IP:Port</button></div></td></tr>`;
    }}).join('');
  }}
  async function poll() {{
    if (!jobId) return;
    const response = await fetch(`/api/discovery/jobs/${{jobId}}`, {{cache:'no-store'}}); const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Discovery status failed');
    const percent = data.total ? Math.round((data.completed / data.total) * 100) : 0;
    progressText.textContent = `${{data.status}}: ${{data.completed}}/${{data.total}} (${{percent}}%)`;
    if (['complete','cancelled','error'].includes(data.status)) {{
      clearInterval(pollTimer); pollTimer = null; setRunning(false); renderResults(data.results || []); errorBox.textContent = data.error || ''; jobId = null;
    }}
  }}
  scanButton.addEventListener('click', async () => {{
    errorBox.textContent = ''; resultBody.innerHTML = '<tr><td colspan="6">Scanning…</td></tr>'; setRunning(true);
    try {{
      const response = await fetch('/api/discovery/scan', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{
        cidr: document.getElementById('discovery-cidr').value || null,
        timeout: Number(document.getElementById('discovery-timeout').value), concurrency: Number(document.getElementById('discovery-concurrency').value), protocols: selectedProtocols()
      }})}});
      const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Discovery scan failed');
      jobId = data.job_id; progressText.textContent = 'Scanning…'; await poll(); pollTimer = setInterval(() => poll().catch(e => {{ errorBox.textContent = e.message; setRunning(false); }}), 500);
    }} catch (error) {{ errorBox.textContent = String(error); setRunning(false); }}
  }});
  cancelButton.addEventListener('click', async () => {{ if (jobId) await fetch(`/api/discovery/jobs/${{jobId}}/cancel`, {{method:'POST'}}); }});
  async function copyTextToClipboard(text) {{
    const value = String(text ?? '');
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {{
      try {{
        await navigator.clipboard.writeText(value);
        return true;
      }} catch (error) {{
        // Plain HTTP LAN pages are commonly denied access to the modern Clipboard API.
      }}
    }}
    let temporary = null;
    try {{
      temporary = document.createElement('textarea');
      temporary.value = value;
      temporary.setAttribute('readonly', '');
      temporary.setAttribute('aria-hidden', 'true');
      temporary.style.position = 'fixed';
      temporary.style.left = '-9999px';
      temporary.style.top = '0';
      temporary.style.opacity = '0';
      document.body.appendChild(temporary);
      temporary.focus();
      temporary.select();
      temporary.setSelectionRange(0, temporary.value.length);
      return document.execCommand('copy') === true;
    }} catch (error) {{
      return false;
    }} finally {{
      if (temporary && temporary.parentNode) temporary.parentNode.removeChild(temporary);
    }}
  }}
  function showCopyFeedback(message, success) {{
    copyConfirm.textContent = message;
    copyConfirm.classList.toggle('bad', !success);
    copyConfirm.classList.toggle('ok', success);
    setTimeout(() => {{
      copyConfirm.textContent = '';
      copyConfirm.classList.remove('bad', 'ok');
    }}, 1600);
  }}
  resultBody.addEventListener('click', async event => {{
    const button = event.target.closest('button[data-copy]'); if (!button) return;
    const text = button.dataset.copy || '';
    const copied = await copyTextToClipboard(text);
    showCopyFeedback(copied ? 'Copied' : 'Copy failed', copied);
  }});
  fetch('/api/discovery/defaults', {{cache:'no-store'}}).then(r => r.json()).then(data => {{ const cidr = document.getElementById('discovery-cidr'); if (cidr && !cidr.value) cidr.value = data.cidr || ''; }}).catch(() => {{}});
}})();
</script>
<script>
(function () {{
  const panel = document.getElementById('advanced-yaml-panel');
  const textarea = document.getElementById('advanced-yaml-text');
  const unsaved = document.getElementById('advanced-yaml-unsaved');
  if (!panel) return;
  const key = 'ptz.config.advancedYamlExpanded';
  panel.open = localStorage.getItem(key) === 'true';
  panel.addEventListener('toggle', () => localStorage.setItem(key, String(panel.open)));
  const initialYaml = textarea ? textarea.value : '';
  if (textarea && unsaved) {{
    const update = () => unsaved.classList.toggle('visible', textarea.value !== initialYaml);
    textarea.addEventListener('input', update);
    textarea.addEventListener('change', update);
    update();
  }}
}})();
</script>
{THEME_SCRIPT}
</div></body>
</html>"""



LOGGER = logging.getLogger(__name__)
SESSION_COOKIE = "ptz_session"


def _auth_page(*, setup: bool = False, message: str = "") -> str:
    title = "Set admin password" if setup else "Admin login"
    fields = (
        '<label>New password<input type="password" name="new_password" autocomplete="new-password"></label>'
        '<label>Confirm password<input type="password" name="confirm_password" autocomplete="new-password"></label>'
        if setup else
        '<label>Username<input value="admin" readonly aria-readonly="true"></label>'
        '<input type="hidden" name="username" value="admin">'
        '<label>Password<input type="password" name="password" autocomplete="current-password"></label>'
    )
    action = "/setup" if setup else "/login"
    button = "Set password" if setup else "Login"
    error = f'<div class="auth-error" role="alert">{escape(message)}</div>' if message else ""
    return f"""<!doctype html><html lang="en" data-theme="dark"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - PTZ Joystick Controller</title>{THEME_BOOTSTRAP}<style>
{COMMON_THEME_CSS}
body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:1rem}}
.auth-card{{width:min(100%,420px);background:var(--panel);border:1px solid var(--border);border-radius:.8rem;padding:1.2rem}}
.auth-card h1{{margin-top:0}} .auth-card label{{display:block;margin:.8rem 0;color:var(--text)}}
.auth-card input{{display:block;width:100%;padding:.65rem;margin-top:.3rem}}
.auth-card button{{width:100%;padding:.7rem;font-weight:700;background:var(--accent-strong);color:#fff}}
.auth-error{{margin:.7rem 0;padding:.6rem;border:1px solid var(--error);border-radius:.45rem;color:var(--error)}}
.theme-corner{{position:fixed;right:1rem;top:1rem}}
</style></head><body>
<div class="theme-corner"><button type="button" id="theme-toggle">Light theme</button></div>
<main class="auth-card"><h1>{title}</h1><p>Username: <strong>admin</strong></p>{error}
<form method="post" action="{action}">{fields}<button type="submit">{button}</button></form></main>
{THEME_SCRIPT}</body></html>"""


def create_web_app(
    status_provider: RuntimeStatusProvider,
    *,
    config_example_path: str | Path = "config.example.yaml",
    config_local_path: str | Path = "config.local.yaml",
    discovery_manager: DiscoveryJobManager | None = None,
    auth_file_path: str | Path = "config.auth.yaml",
    auth_enabled: bool = False,
    session_lifetime_seconds: float = 24 * 60 * 60,
) -> FastAPI:
    app = FastAPI(title="PTZ Joystick Controller")
    config_editor = ConfigEditor(
        current_config=status_provider.state.config,
        example_config_path=Path(config_example_path),
        local_config_path=Path(config_local_path),
    )
    config_applier = RuntimeConfigApplier(
        status_provider=status_provider,
        example_config_path=Path(config_example_path),
        local_config_path=Path(config_local_path),
    )
    status_provider.config_apply_status = config_applier.status
    discovery_manager = discovery_manager or DiscoveryJobManager()
    app.state.discovery_manager = discovery_manager
    pending_imports: dict[str, dict[str, Any]] = {}
    IMPORT_LIMIT = 1024 * 1024
    IMPORT_TTL = 10 * 60

    auth_store = AuthStore(auth_file_path)
    sessions = SessionManager(session_lifetime_seconds)
    limiter = LoginRateLimiter()
    app.state.auth_store = auth_store
    app.state.sessions = sessions
    app.state.login_rate_limiter = limiter

    def source_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    anonymous_csrf_token = secrets.token_urlsafe(32)

    def authentication_disabled() -> bool:
        return bool(auth_enabled and auth_store.configured and auth_store.authentication_disabled)

    def authentication_active() -> bool:
        return bool(auth_enabled and auth_store.configured and not authentication_disabled())

    def current_session(request: Request):
        if not authentication_active():
            return None
        return sessions.get(request.cookies.get(SESSION_COOKIE))

    def csrf_for(request: Request) -> str:
        if not auth_enabled:
            return ""
        if authentication_disabled():
            return anonymous_csrf_token
        session = current_session(request)
        return session.csrf_token if session else ""

    async def require_csrf(request: Request) -> bool:
        if not auth_enabled:
            return True
        expected = csrf_for(request)
        if not expected:
            return False
        supplied = request.headers.get("x-csrf-token")
        if supplied is None:
            content_type = request.headers.get("content-type", "")
            if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
                form = await request.form()
                supplied = str(form.get("csrf_token", ""))
        return bool(supplied and secrets.compare_digest(supplied, expected))

    def logout_control(request: Request) -> str:
        if not authentication_active():
            return ""
        return (
            '<form method="post" action="/logout" style="display:inline;margin:0">'
            f'<input type="hidden" name="csrf_token" value="{escape(csrf_for(request))}">'
            '<button type="submit">Logout</button></form>'
        )

    def render_protected(html: str, request: Request) -> HTMLResponse:
        rendered = html.replace("__CSRF_TOKEN__", escape(csrf_for(request)))
        rendered = rendered.replace("__LOGOUT_CONTROL__", logout_control(request))
        return HTMLResponse(rendered)

    @app.middleware("http")
    async def authentication_middleware(request: Request, call_next):
        if not auth_enabled:
            return await call_next(request)
        path = request.url.path
        public = path in {"/health", "/login", "/setup"}
        if public:
            return await call_next(request)
        if not auth_store.configured:
            if path.startswith("/api/"):
                return JSONResponse({"status": "error", "error": "Admin password setup required"}, status_code=401)
            return RedirectResponse("/setup", status_code=303)
        if authentication_disabled():
            return await call_next(request)
        if current_session(request) is None:
            if path.startswith("/api/"):
                return JSONResponse({"status": "error", "error": "Authentication required"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    def login_page() -> HTMLResponse:
        if not auth_enabled:
            return RedirectResponse("/", status_code=303)
        if not auth_store.configured:
            return RedirectResponse("/setup", status_code=303)
        if authentication_disabled():
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(_auth_page())

    @app.post("/login", response_class=HTMLResponse)
    async def login(request: Request):
        if not auth_store.configured:
            return RedirectResponse("/setup", status_code=303)
        if authentication_disabled():
            return RedirectResponse("/", status_code=303)
        ip = source_ip(request)
        if limiter.limited(ip):
            LOGGER.warning("Web login rate limit active source_ip=%s", ip)
            return HTMLResponse(_auth_page(message="Too many failed attempts. Try again shortly."), status_code=429)
        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        if username != ADMIN_USERNAME or not auth_store.verify(password):
            activated = limiter.failure(ip)
            LOGGER.warning("Web login failed source_ip=%s", ip)
            if activated:
                LOGGER.warning("Web login rate limit activated source_ip=%s", ip)
            return HTMLResponse(_auth_page(message="Invalid username or password"), status_code=401)
        limiter.success(ip)
        session = sessions.create(ip)
        LOGGER.info("Web login successful source_ip=%s", ip)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE, session.token, max_age=int(session_lifetime_seconds),
            httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/",
        )
        return response

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page() -> HTMLResponse:
        if auth_store.configured:
            return RedirectResponse("/" if authentication_disabled() else "/login", status_code=303)
        return HTMLResponse(_auth_page(setup=True))

    @app.post("/setup", response_class=HTMLResponse)
    async def setup_password(request: Request):
        if auth_store.configured:
            return RedirectResponse("/" if authentication_disabled() else "/login", status_code=303)
        form = await request.form()
        new_password = str(form.get("new_password", ""))
        confirm = str(form.get("confirm_password", ""))
        if new_password != confirm:
            return HTMLResponse(_auth_page(setup=True, message="Passwords do not match."), status_code=400)
        try:
            auth_store.set_password(new_password)
        except AuthError as exc:
            return HTMLResponse(_auth_page(setup=True, message=str(exc)), status_code=400)
        sessions.invalidate_all()
        LOGGER.info("Admin password initialized source_ip=%s auth_disabled=%s", source_ip(request), auth_store.authentication_disabled)
        return RedirectResponse("/" if auth_store.authentication_disabled else "/login", status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        if authentication_disabled():
            return RedirectResponse("/", status_code=303)
        if not await require_csrf(request):
            return JSONResponse({"status": "error", "error": "Invalid CSRF token"}, status_code=403)
        token = request.cookies.get(SESSION_COOKIE)
        sessions.invalidate(token)
        LOGGER.info("Web logout source_ip=%s", source_ip(request))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.post("/security/change-password", response_class=HTMLResponse)
    async def change_password(request: Request):
        if not await require_csrf(request):
            return JSONResponse({"status": "error", "error": "Invalid CSRF token"}, status_code=403)
        form = await request.form()
        current = str(form.get("current_password", ""))
        new = str(form.get("new_password", ""))
        confirm = str(form.get("confirm_password", ""))
        if not auth_store.verify(current):
            return HTMLResponse(render_config_html(config_editor, message="Current password is incorrect.", csrf_token=csrf_for(request), authentication_disabled=authentication_disabled()).replace("__CSRF_TOKEN__", escape(csrf_for(request))).replace("__LOGOUT_CONTROL__", logout_control(request)), status_code=400)
        if new != confirm:
            return HTMLResponse(render_config_html(config_editor, message="New passwords do not match.", csrf_token=csrf_for(request), authentication_disabled=authentication_disabled()).replace("__CSRF_TOKEN__", escape(csrf_for(request))).replace("__LOGOUT_CONTROL__", logout_control(request)), status_code=400)
        try:
            auth_store.set_password(new)
        except AuthError as exc:
            return HTMLResponse(render_config_html(config_editor, message=str(exc), csrf_token=csrf_for(request), authentication_disabled=authentication_disabled()).replace("__CSRF_TOKEN__", escape(csrf_for(request))).replace("__LOGOUT_CONTROL__", logout_control(request)), status_code=400)
        sessions.invalidate_all()
        LOGGER.info("Admin password changed source_ip=%s auth_disabled=%s", source_ip(request), auth_store.authentication_disabled)
        response = RedirectResponse("/config" if auth_store.authentication_disabled else "/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/api/status")
    def api_status() -> dict[str, Any]:
        return status_provider.status()

    @app.get("/api/diagnostics")
    def api_diagnostics() -> dict[str, Any]:
        return status_provider.diagnostics()

    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        return {"editable_config": config_editor.editable_payload()}

    @app.post("/api/config/apply")
    async def api_config_apply(request: Request) -> JSONResponse:
        if not await require_csrf(request):
            return JSONResponse({"status": "error", "error": "Invalid CSRF token"}, status_code=403)
        try:
            result = config_applier.apply_from_disk()
            config_editor.current_config = status_provider.state.config
            return JSONResponse(result)
        except (ConfigError, ConfigEditError, Exception) as exc:
            return JSONResponse({"status": "error", "error": str(exc), "message": str(exc)}, status_code=400)

    @app.get("/api/discovery/defaults")
    def api_discovery_defaults() -> JSONResponse:
        try:
            return JSONResponse(discovery_manager.defaults())
        except ValueError as exc:
            return JSONResponse({"cidr": "", "error": str(exc)})

    @app.post("/api/discovery/scan")
    async def api_discovery_scan(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            job = discovery_manager.start(
                cidr=payload.get("cidr"), timeout=float(payload.get("timeout", 0.5)),
                concurrency=int(payload.get("concurrency", 32)), protocols=payload.get("protocols", ["osee", "vmix", "visca"]),
            )
            return JSONResponse(job.payload(), status_code=202)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"status": "error", "error": str(exc)}, status_code=400)

    @app.get("/api/discovery/jobs/{job_id}")
    def api_discovery_job(job_id: str) -> JSONResponse:
        job = discovery_manager.get(job_id)
        return JSONResponse(job.payload()) if job is not None else JSONResponse({"status": "error", "error": "Discovery job not found"}, status_code=404)

    @app.post("/api/discovery/jobs/{job_id}/cancel")
    def api_discovery_cancel(job_id: str) -> JSONResponse:
        job = discovery_manager.cancel(job_id)
        return JSONResponse(job.payload()) if job is not None else JSONResponse({"status": "error", "error": "Discovery job not found"}, status_code=404)

    def import_scope(request: Request) -> str:
        session = current_session(request)
        if session is not None:
            return session.token
        if authentication_disabled():
            return "auth-disabled:" + source_ip(request)
        return "no-auth:" + source_ip(request)

    def cleanup_pending_imports() -> None:
        now = time.monotonic()
        for token, item in list(pending_imports.items()):
            if item["expires"] <= now:
                pending_imports.pop(token, None)

    @app.get("/config/export")
    def export_config(request: Request) -> Response:
        data = config_editor.export_mapping()
        body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        filename = f"ptz-joystick-controller-config-{datetime.now().strftime('%Y-%m-%d-%H%M')}.yaml"
        return Response(body, media_type="application/yaml", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/config/import/validate", response_class=HTMLResponse)
    async def validate_import(request: Request):
        if not await require_csrf(request):
            return JSONResponse({"status": "error", "error": "Invalid CSRF token"}, status_code=403)
        form = await request.form()
        upload = form.get("config_file")
        if upload is None or not hasattr(upload, "read"):
            return HTMLResponse("Missing configuration file", status_code=400)
        raw = await upload.read(IMPORT_LIMIT + 1)
        if len(raw) > IMPORT_LIMIT:
            return HTMLResponse("Configuration upload exceeds 1 MiB limit", status_code=413)
        try:
            text = raw.decode("utf-8")
            data = yaml.safe_load(text) or {}
            if not isinstance(data, dict):
                raise ConfigEditError("Config root must be a mapping")
            config_editor.validate_import_mapping(data)
        except (UnicodeDecodeError, yaml.YAMLError, ConfigEditError, ConfigError) as exc:
            return HTMLResponse(f"Configuration import validation failed: {escape(str(exc))}", status_code=400)
        cleanup_pending_imports()
        token = secrets.token_urlsafe(32)
        pending_imports[token] = {"scope": import_scope(request), "data": data, "expires": time.monotonic() + IMPORT_TTL}
        html = ("<!doctype html><html><body><h1>Confirm configuration import</h1>"
                "<p>Validation succeeded. Upload has not changed the production configuration.</p>"
                '<form method="post" action="/config/import/confirm">'
                f'<input type="hidden" name="csrf_token" value="{escape(csrf_for(request))}">'
                f'<input type="hidden" name="import_token" value="{escape(token)}">'
                '<button type="submit" name="apply" value="0">Import / Save</button>'
                '<button type="submit" name="apply" value="1">Import / Save and Apply</button>'
                "</form></body></html>")
        return HTMLResponse(html)

    @app.post("/config/import/confirm")
    async def confirm_import(request: Request):
        if not await require_csrf(request):
            return JSONResponse({"status": "error", "error": "Invalid CSRF token"}, status_code=403)
        form = await request.form()
        token = str(form.get("import_token", ""))
        cleanup_pending_imports()
        item = pending_imports.get(token)
        if item is None or item["scope"] != import_scope(request):
            return HTMLResponse("Pending import is missing, expired, or belongs to another session.", status_code=400)
        pending_imports.pop(token, None)
        try:
            result = config_editor.import_mapping(item["data"])
            if str(form.get("apply", "0")) == "1":
                apply_result = config_applier.apply_from_disk()
                config_editor.current_config = status_provider.state.config
                message = str(apply_result["message"])
            else:
                message = result["message"]
            return HTMLResponse(render_config_html(config_editor, message=message, csrf_token=csrf_for(request), authentication_disabled=authentication_disabled()).replace("__CSRF_TOKEN__", escape(csrf_for(request))).replace("__LOGOUT_CONTROL__", logout_control(request)))
        except (ConfigEditError, ConfigError) as exc:
            return HTMLResponse(f"Configuration import failed: {escape(str(exc))}", status_code=400)

    @app.get("/config", response_class=HTMLResponse)
    def config_page(request: Request) -> HTMLResponse:
        return render_protected(
            render_config_html(
                config_editor,
                csrf_token=csrf_for(request),
                authentication_disabled=authentication_disabled(),
            ),
            request,
        )

    @app.post("/config/basic")
    async def save_basic_config(request: Request):
        if not await require_csrf(request):
            return JSONResponse({"status": "error", "error": "Invalid CSRF token"}, status_code=403)
        try:
            content_type = request.headers.get("content-type", "")
            if content_type.startswith("application/json"):
                payload = await request.json()
                if not isinstance(payload, dict):
                    raise ConfigEditError("Configuration payload must be a JSON object")
                result = config_editor.save_patch(payload)
                return JSONResponse(result)
            form = await request.form()
            result = config_editor.save_form(form)
            if str(form.get("apply", "0")) == "1":
                apply_result = config_applier.apply_from_disk()
                config_editor.current_config = status_provider.state.config
                return HTMLResponse(render_config_html(config_editor, message=str(apply_result["message"]), csrf_token=csrf_for(request), authentication_disabled=authentication_disabled()).replace("__CSRF_TOKEN__", escape(csrf_for(request))).replace("__LOGOUT_CONTROL__", logout_control(request)))
            return HTMLResponse(render_config_html(config_editor, message=result["message"], csrf_token=csrf_for(request), authentication_disabled=authentication_disabled()).replace("__CSRF_TOKEN__", escape(csrf_for(request))).replace("__LOGOUT_CONTROL__", logout_control(request)))
        except (ConfigEditError, ConfigError) as exc:
            if request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"status": "error", "error": str(exc), "message": str(exc)}, status_code=400)
            return HTMLResponse(render_config_html(config_editor, message=str(exc), csrf_token=csrf_for(request), authentication_disabled=authentication_disabled()).replace("__CSRF_TOKEN__", escape(csrf_for(request))).replace("__LOGOUT_CONTROL__", logout_control(request)), status_code=400)

    @app.post("/config/raw")
    async def save_raw_config(request: Request):
        if not await require_csrf(request):
            return JSONResponse({"status": "error", "error": "Invalid CSRF token"}, status_code=403)
        try:
            content_type = request.headers.get("content-type", "")
            if content_type.startswith("application/json"):
                payload = await request.json()
                if not isinstance(payload, dict):
                    raise ConfigEditError("Configuration payload must be a JSON object")
                result = config_editor.save_patch(payload)
                return JSONResponse(result)
            form = await request.form()
            raw_yaml = str(form.get("raw_yaml", ""))
            parsed = yaml.safe_load(raw_yaml) or {}
            if not isinstance(parsed, dict):
                raise ConfigEditError("Raw YAML payload must be a mapping")
            result = config_editor.save_patch(parsed)
            if str(form.get("apply", "0")) == "1":
                apply_result = config_applier.apply_from_disk()
                config_editor.current_config = status_provider.state.config
                return HTMLResponse(render_config_html(config_editor, message=str(apply_result["message"]), csrf_token=csrf_for(request), authentication_disabled=authentication_disabled()).replace("__CSRF_TOKEN__", escape(csrf_for(request))).replace("__LOGOUT_CONTROL__", logout_control(request)))
            return HTMLResponse(render_config_html(config_editor, message=result["message"], csrf_token=csrf_for(request), authentication_disabled=authentication_disabled()).replace("__CSRF_TOKEN__", escape(csrf_for(request))).replace("__LOGOUT_CONTROL__", logout_control(request)))
        except (ConfigEditError, ConfigError, yaml.YAMLError) as exc:
            if request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"status": "error", "error": str(exc), "message": str(exc)}, status_code=400)
            return HTMLResponse(render_config_html(config_editor, message=str(exc), csrf_token=csrf_for(request), authentication_disabled=authentication_disabled()).replace("__CSRF_TOKEN__", escape(csrf_for(request))).replace("__LOGOUT_CONTROL__", logout_control(request)), status_code=400)

    @app.post("/config")
    async def save_config(request: Request) -> JSONResponse:
        if not await require_csrf(request):
            return JSONResponse({"status": "error", "error": "Invalid CSRF token"}, status_code=403)
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ConfigEditError("Configuration payload must be a JSON object")
            result = config_editor.save_patch(payload)
            return JSONResponse(result)
        except (ConfigEditError, ConfigError) as exc:
            return JSONResponse({"status": "error", "error": str(exc), "message": str(exc)}, status_code=400)

    @app.get("/diagnostics", response_class=HTMLResponse)
    def diagnostics_page(request: Request) -> HTMLResponse:
        return render_protected(DIAGNOSTICS_HTML, request)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        return render_protected(DASHBOARD_HTML, request)

    return app

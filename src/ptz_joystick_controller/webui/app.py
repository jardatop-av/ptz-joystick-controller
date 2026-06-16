from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

import yaml

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config_editor import ConfigEditError, ConfigEditor
from .config_runtime import RuntimeConfigApplier
from ..config import ConfigError
from ..joystick.button_metadata import CANONICAL_BUTTON_IDS, ButtonMetadataRegistry
from ..models.joystick import ButtonAction
from .status import RuntimeStatusProvider


NAV_HTML = '<nav class="topnav"><a href="/">Dashboard</a> | <a href="/config">Config</a> | <a href="/diagnostics">Diagnostics</a></nav>'

DASHBOARD_HTML = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>PTZ Joystick Controller</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; }
    body { margin: 0; padding: 1rem; background: Canvas; color: CanvasText; }
    header { margin-bottom: 1rem; }
    .topnav { margin: 0 0 1rem; }
    .topnav a { margin-right: .5rem; }
    h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: .75rem; }
    section { border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); border-radius: .75rem; padding: .85rem; background: color-mix(in srgb, Canvas 92%, CanvasText 8%); }
    h2 { font-size: 1rem; margin: 0 0 .5rem; }
    dl { display: grid; grid-template-columns: max-content 1fr; gap: .3rem .8rem; margin: 0; }
    dt { opacity: .7; }
    dd { margin: 0; overflow-wrap: anywhere; }
    ul { margin: .25rem 0 0; padding-left: 1.2rem; }
    .ok { color: #198754; font-weight: 700; }
    .bad { color: #dc3545; font-weight: 700; }
    .mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .9rem; }
    @media (max-width: 520px) { body { padding: .6rem; } dl { grid-template-columns: 1fr; } dt { font-weight: 700; } }
  </style>
</head>
<body>
<header>
  <nav class="topnav"><a href="/">Dashboard</a> | <a href="/config">Config</a> | <a href="/diagnostics">Diagnostics</a></nav>
  <h1 id=\"title\">PTZ Joystick Controller</h1>
  <div id=\"subtitle\" class=\"mono\">Loading…</div>
</header>
<div class=\"grid\">
  <section><h2>System</h2><dl id=\"system\"></dl></section>
  <section><h2>Joystick</h2><dl id=\"joystick\"></dl></section>
  <section><h2>Switcher</h2><dl id=\"switcher\"></dl></section>
  <section><h2>PTZ</h2><dl id=\"ptz\"></dl></section>
  <section><h2>Safety</h2><dl id=\"safety\"></dl></section>
  <section><h2>Config</h2><dl id=\"config\"></dl></section>
  <section><h2>Configured Cameras</h2><ul id=\"cameras\"></ul></section>
  <section><h2>Recent activity</h2><ul id=\"events\"></ul></section>
</div>
<script>
function dtdd(key, value) { return `<dt>${key}</dt><dd>${value ?? ''}</dd>`; }
function boolBadge(value) { return value ? '<span class=\"ok\">connected</span>' : '<span class=\"bad\">disconnected</span>'; }
function seconds(value) { return `${Math.round(value)} s`; }
function byId(id) { return document.getElementById(id); }
function setList(id, rows) { const el = byId(id); if (el) el.innerHTML = rows.join(''); }
function setHtml(id, html) { const el = byId(id); if (el) el.innerHTML = html; }
async function refresh() {
  try {
    const r = await fetch('/api/status', {cache: 'no-store'});
    const s = await r.json();
    const title = byId('title'); if (title) title.textContent = s.system.application_name;
    const subtitle = byId('subtitle'); if (subtitle) subtitle.textContent = `${s.system.stage} / ${s.system.version} / uptime ${seconds(s.uptime)}`;
    setList('system', [dtdd('Application', s.system.application_name), dtdd('Version', s.system.version), dtdd('Stage', s.system.stage), dtdd('Uptime', seconds(s.uptime))]);
    setList('joystick', [dtdd('Status', boolBadge(s.joystick.connected)), dtdd('Device', s.joystick.device_name), dtdd('Buttons', (s.joystick.pressed_buttons || []).join(', ') || 'none'), dtdd('Hat', `${s.joystick.hat.direction} (${s.joystick.hat.x}, ${s.joystick.hat.y})`), dtdd('Pan/Tilt/Zoom', `${s.joystick.normalized_axes.pan} / ${s.joystick.normalized_axes.tilt} / ${s.joystick.normalized_axes.zoom}`)]);
    setList('switcher', [dtdd('Status', boolBadge(s.switcher.connected)), dtdd('Type', s.switcher.type), dtdd('Program', s.program), dtdd('Preview', s.preview)]);
    setList('ptz', [dtdd('Active camera', s.active_ptz_camera), dtdd('Moving', s.ptz.moving), dtdd('Pan/Tilt active', s.ptz.pan_tilt_active), dtdd('Zoom active', s.ptz.zoom_active), dtdd('Hat active', s.ptz.hat_active), dtdd('Last action', s.ptz.last_action)]);
    setList('safety', [dtdd('Watchdog', s.safety.watchdog_enabled), dtdd('Center samples', s.safety.center_confirm_samples), dtdd('Output deadzone', `pan/tilt=${s.safety.output_deadzone.pan_tilt}, zoom=${s.safety.output_deadzone.zoom}`)]);
    setList('config', [dtdd('Loaded at', s.config?.loaded_at), dtdd('Pending changes', s.config?.pending_changes), dtdd('Last apply', s.config?.last_apply_result || 'none'), dtdd('Last error', s.config?.last_apply_error || '')]);
    setHtml('cameras', (s.ptz.configured_cameras || []).map(c => `<li>${c.active ? '▶ ' : ''}${c.name} (${c.id}) — ${c.enabled ? 'enabled' : 'disabled'} ${c.host || ''}</li>`).join('') || '<li>none</li>');
    setHtml('events', (s.recent_activity || []).map(e => `<li><span class=\"mono\">${e.created_at}</span> ${e.type}</li>`).join('') || '<li>none</li>');
  } catch (e) {
    const subtitle = byId('subtitle'); if (subtitle) subtitle.textContent = `Status refresh failed: ${e}`;
  }
}
refresh();
setInterval(refresh, 1000);
</script>
</body>
</html>
"""



DIAGNOSTICS_HTML = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>PTZ Joystick Controller Diagnostics</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; }
    body { margin: 0; padding: 1rem; background: Canvas; color: CanvasText; }
    .topnav { margin-bottom: 1rem; }
    .topnav a { margin-right: .5rem; }
    h1 { font-size: 1.4rem; margin: 0 0 1rem; }
    h2 { font-size: 1.05rem; margin: 0 0 .5rem; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: .75rem; }
    section { border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); border-radius: .75rem; padding: .85rem; background: color-mix(in srgb, Canvas 92%, CanvasText 8%); min-width: 0; }
    .table-wrap { overflow-x: auto; max-width: 100%; }
    table { width: 100%; border-collapse: collapse; font-size: .88rem; table-layout: fixed; }
    th, td { border-bottom: 1px solid color-mix(in srgb, CanvasText 18%, transparent); padding: .25rem; text-align: left; vertical-align: top; overflow-wrap: anywhere; word-break: break-word; }
    td.mono, .hex, .details { overflow-wrap: anywhere; word-break: break-word; white-space: pre-wrap; }
    dl { display: grid; grid-template-columns: max-content 1fr; gap: .25rem .7rem; margin: 0; }
    dt { opacity: .7; } dd { margin: 0; overflow-wrap: anywhere; }
    .mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .86rem; }
    .ok { color: #198754; font-weight: 700; } .bad { color: #dc3545; font-weight: 700; }
    @media (max-width: 620px) { body { padding: .6rem; } .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
<nav class="topnav"><a href="/">Dashboard</a> | <a href="/config">Config</a> | <a href="/diagnostics">Diagnostics</a></nav>
<h1>Runtime Diagnostics</h1>
<div class=\"grid\">
  <section><h2>Joystick diagnostics</h2><dl id=\"joystick\"></dl></section>
  <section><h2>Switcher diagnostics</h2><dl id=\"switcher\"></dl></section>
  <section><h2>PTZ diagnostics</h2><div class="table-wrap"><table><thead><tr><th>Time</th><th>Camera</th><th>Action</th><th>Details</th></tr></thead><tbody id=\"ptz\"></tbody></table></div></section>
  <section><h2>VISCA diagnostics</h2><div class="table-wrap"><table><thead><tr><th>Time</th><th>Target</th><th>Dir</th><th>Hex payload</th></tr></thead><tbody id=\"visca\"></tbody></table></div></section>
  <section style=\"grid-column: 1 / -1\"><h2>Runtime log</h2><div class="table-wrap"><table><thead><tr><th>Time</th><th>Level</th><th>Event</th><th>Details</th></tr></thead><tbody id=\"runtime\"></tbody></table></div></section>
</div>
<script>
function esc(v) { return String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;'); }
function dtdd(k, v) { return `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`; }
function badge(v) { return v ? '<span class=\"ok\">connected</span>' : '<span class=\"bad\">disconnected</span>'; }
function shortTime(ts) { return ts ? esc(ts).replace('T', ' ').replace('+00:00', 'Z') : ''; }
function json(v) { try { return esc(JSON.stringify(v)); } catch { return esc(v); } }
async function refreshDiagnostics() {
  try {
    const r = await fetch('/api/diagnostics', {cache: 'no-store'});
    const d = await r.json();
    const j = d.joystick || {};
    document.getElementById('joystick').innerHTML = [
      dtdd('State', j.connected ? 'connected' : 'disconnected'), dtdd('Device', j.device_name), dtdd('Raw axes', json(j.raw_axes)),
      dtdd('Normalized axes', json(j.normalized_axes)), dtdd('Output deadzone', json((d.ptz || {}).output_deadzone || 'see status')),
      dtdd('Hat', `${j.hat?.direction || 'center'} (${j.hat?.x || 0}, ${j.hat?.y || 0})`),
      dtdd('Buttons', (j.pressed_buttons || []).join(', ') || 'none'), dtdd('Last seen/error', `${j.last_seen_at || ''} ${j.last_error || ''}`)
    ].join('');
    const s = d.switcher || {};
    document.getElementById('switcher').innerHTML = [
      dtdd('State', s.connected ? 'connected' : 'disconnected'), dtdd('Type', s.type), dtdd('Preview', s.preview_source), dtdd('Program', s.program_source),
      dtdd('Last sync', s.last_sync_time), dtdd('Last HTTP error', s.last_http_error), dtdd('Last command', s.last_command)
    ].join('');
    document.getElementById('ptz').innerHTML = (d.ptz_actions || []).map(a => `<tr><td class=\"mono\">${shortTime(a.timestamp)}</td><td>${esc(a.camera_id)}</td><td>${esc(a.action_type)}</td><td class=\"mono\">${json(a.details)}</td></tr>`).join('') || '<tr><td colspan=\"4\">No PTZ actions</td></tr>';
    document.getElementById('visca').innerHTML = (d.visca_packets || []).map(p => `<tr><td class=\"mono\">${shortTime(p.timestamp)}</td><td>${esc(p.host)}:${esc(p.port)}</td><td>${esc(p.direction)}</td><td class=\"mono\">${esc(p.hex_payload)}</td></tr>`).join('') || '<tr><td colspan=\"4\">No VISCA packets</td></tr>';
    document.getElementById('runtime').innerHTML = (d.runtime_events || []).map(e => `<tr><td class=\"mono\">${shortTime(e.timestamp)}</td><td>${esc(e.level)}</td><td>${esc(e.event_type || e.type)}</td><td class=\"mono\">${json(e.details)}</td></tr>`).join('') || '<tr><td colspan=\"4\">No runtime events</td></tr>';
  } catch (e) {
    document.getElementById('runtime').innerHTML = `<tr><td colspan=\"4\">Diagnostics refresh failed: ${esc(e)}</td></tr>`;
  }
}
refreshDiagnostics();
setInterval(refreshDiagnostics, 1000);
</script>
</body>
</html>"""


BUTTON_ACTION_OPTIONS = (
    ButtonAction.PREVIEW_SOURCE,
    ButtonAction.PRESET_RECALL,
    ButtonAction.NONE,
    ButtonAction.CUT,
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


def render_config_html(config_editor: ConfigEditor, *, message: str = "") -> str:
    payload = config_editor.editable_payload()
    registry = ButtonMetadataRegistry(getattr(config_editor.current_config.joystick, "button_labels", {}))
    switcher = payload["switcher"]
    ptz = payload["ptz"]
    joystick = payload["joystick"]
    cameras = ptz["cameras"]
    buttons = joystick["buttons"]
    # Render the raw editor from the currently loaded configuration without
    # applying save-time validation. Generic example configs may intentionally
    # contain disabled/incomplete hardware placeholders; validation runs on
    # submit before writing config.local.yaml.
    raw_yaml = yaml.safe_dump(config_editor.patch_to_local_override_unvalidated(payload), sort_keys=False, allow_unicode=True)

    camera_rows = []
    for index, camera in enumerate(cameras):
        camera_rows.append(
            "<tr>"
            f"<td><code>{escape(str(camera['id']))}</code><input type='hidden' name='camera_{index}_id' value='{_html_value(camera['id'])}'></td>"
            f"<td><input name='camera_{index}_name' value='{_html_value(camera['name'])}'></td>"
            f"<td><input name='camera_{index}_host' value='{_html_value(camera.get('host'))}'></td>"
            f"<td><input type='number' min='1' max='65535' name='camera_{index}_port' value='{_html_value(camera['port'])}'></td>"
            f"<td><input type='checkbox' name='camera_{index}_enabled'{_checked(bool(camera['enabled']))}></td>"
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
            f"<td><select name='button_{button_id}_action'>{_button_action_options(action)}</select></td>"
            f"<td><input name='button_{button_id}_source_id' value='{_html_value(mapping.get('source_id'))}' placeholder='Input 1'></td>"
            f"<td><input type='number' min='0' max='255' name='button_{button_id}_preset_number' value='{_html_value(mapping.get('preset_number'))}'></td>"
            "</tr>"
        )

    status_message = message or "Basic form saves only to config.local.yaml. Use Save and apply to update the running process."
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PTZ Joystick Controller Config</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; padding: 1rem; background: Canvas; color: CanvasText; }}
    header {{ margin-bottom: 1rem; }}
    h1 {{ font-size: 1.4rem; margin: 0 0 .25rem; }}
    h2 {{ margin-top: 1.3rem; font-size: 1.1rem; }}
    fieldset {{ border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); border-radius: .75rem; margin: .75rem 0; padding: .85rem; }}
    label {{ display: inline-block; margin: .3rem .8rem .3rem 0; }}
    input, select, textarea {{ font: inherit; max-width: 100%; }}
    input[type=text], input[type=number], input:not([type]) {{ padding: .35rem; min-width: 8rem; }}
    textarea {{ width: 100%; min-height: 40vh; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .9rem; }}
    table {{ border-collapse: collapse; width: 100%; overflow-x: auto; display: block; }}
    th, td {{ border-bottom: 1px solid color-mix(in srgb, CanvasText 18%, transparent); padding: .35rem; text-align: left; }}
    button {{ padding: .65rem 1rem; margin-top: .75rem; font-weight: 700; }}
    .message {{ margin: .75rem 0; padding: .6rem; border-radius: .5rem; border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); }}
    .ok {{ color: #198754; }}
    .bad {{ color: #dc3545; white-space: pre-wrap; }}
    .topnav {{ margin: 0 0 1rem; }}
    .topnav a {{ margin-right: .75rem; }}
    @media (max-width: 700px) {{ body {{ padding: .6rem; }} th, td {{ min-width: 7rem; }} }}
  </style>
</head>
<body>
<header>
  {NAV_HTML}
  <h1>Configuration</h1>
  <p>Safe limited editor. Saves only to <code>config.local.yaml</code>. Never overwrites <code>config.example.yaml</code>.</p>
</header>
<div id="message" class="message ok">{escape(status_message)}</div>

<h2>Basic configuration</h2>
<form method="post" action="/config/basic" id="basic-config-form">
  <fieldset>
    <legend>Switcher</legend>
    <label>Host <input name="switcher_host" value="{_html_value(switcher.get('host'))}"></label>
    <label>Port <input type="number" min="1" max="65535" name="switcher_port" value="{_html_value(switcher.get('port'))}"></label>
  </fieldset>

  <fieldset>
    <legend>PTZ Cameras</legend>
    <table><thead><tr><th>id</th><th>name</th><th>host</th><th>port</th><th>enabled</th><th>preset_offset</th></tr></thead><tbody>{''.join(camera_rows)}</tbody></table>
  </fieldset>

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
  <button type="submit" name="apply" value="0">Save configuration</button>
  <button type="submit" name="apply" value="1">Save and apply configuration</button>
</form>

<h2>Advanced YAML editor</h2>
<p>This raw editor is still available for advanced overrides and uses the same validation path.</p>
<form method="post" action="/config/raw">
  <textarea name="raw_yaml" spellcheck="false">{escape(raw_yaml)}</textarea>
  <br><button type="submit" name="apply" value="0">Save Advanced YAML editor</button>
  <button type="submit" name="apply" value="1">Save and apply Advanced YAML editor</button>
</form>
</body>
</html>"""


def create_web_app(
    status_provider: RuntimeStatusProvider,
    *,
    config_example_path: str | Path = "config.example.yaml",
    config_local_path: str | Path = "config.local.yaml",
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

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
    def api_config_apply() -> JSONResponse:
        try:
            result = config_applier.apply_from_disk()
            config_editor.current_config = status_provider.state.config
            return JSONResponse(result)
        except (ConfigError, ConfigEditError, Exception) as exc:
            return JSONResponse({"status": "error", "error": str(exc), "message": str(exc)}, status_code=400)

    @app.get("/config", response_class=HTMLResponse)
    def config_page() -> HTMLResponse:
        return HTMLResponse(render_config_html(config_editor))

    @app.post("/config/basic")
    async def save_basic_config(request: Request):
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
                return HTMLResponse(render_config_html(config_editor, message=str(apply_result["message"])))
            return HTMLResponse(render_config_html(config_editor, message=result["message"]))
        except (ConfigEditError, ConfigError) as exc:
            if request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"status": "error", "error": str(exc), "message": str(exc)}, status_code=400)
            return HTMLResponse(render_config_html(config_editor, message=str(exc)), status_code=400)

    @app.post("/config/raw")
    async def save_raw_config(request: Request):
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
                return HTMLResponse(render_config_html(config_editor, message=str(apply_result["message"])))
            return HTMLResponse(render_config_html(config_editor, message=result["message"]))
        except (ConfigEditError, ConfigError, yaml.YAMLError) as exc:
            if request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"status": "error", "error": str(exc), "message": str(exc)}, status_code=400)
            return HTMLResponse(render_config_html(config_editor, message=str(exc)), status_code=400)

    @app.post("/config")
    async def save_config(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ConfigEditError("Configuration payload must be a JSON object")
            result = config_editor.save_patch(payload)
            return JSONResponse(result)
        except (ConfigEditError, ConfigError) as exc:
            return JSONResponse({"status": "error", "error": str(exc), "message": str(exc)}, status_code=400)

    @app.get("/diagnostics", response_class=HTMLResponse)
    def diagnostics_page() -> HTMLResponse:
        return HTMLResponse(DIAGNOSTICS_HTML)

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(DASHBOARD_HTML)

    return app

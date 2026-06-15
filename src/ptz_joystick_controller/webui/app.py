from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config_editor import ConfigEditError, ConfigEditor
from .status import RuntimeStatusProvider


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
  <h1 id=\"title\">PTZ Joystick Controller</h1>
  <div id=\"subtitle\" class=\"mono\">Loading…</div>
</header>
<div class=\"grid\">
  <section><h2>System</h2><dl id=\"system\"></dl></section>
  <section><h2>Joystick</h2><dl id=\"joystick\"></dl></section>
  <section><h2>Switcher</h2><dl id=\"switcher\"></dl></section>
  <section><h2>PTZ</h2><dl id=\"ptz\"></dl></section>
  <section><h2>Safety</h2><dl id=\"safety\"></dl></section>
  <section><h2>Configured Cameras</h2><ul id=\"cameras\"></ul></section>
  <section><h2>Recent activity</h2><ul id=\"events\"></ul></section>
</div>
<script>
function dtdd(key, value) { return `<dt>${key}</dt><dd>${value ?? ''}</dd>`; }
function boolBadge(value) { return value ? '<span class=\"ok\">connected</span>' : '<span class=\"bad\">disconnected</span>'; }
function seconds(value) { return `${Math.round(value)} s`; }
function setList(id, rows) { document.getElementById(id).innerHTML = rows.join(''); }
async function refresh() {
  try {
    const r = await fetch('/api/status', {cache: 'no-store'});
    const s = await r.json();
    document.getElementById('title').textContent = s.system.application_name;
    document.getElementById('subtitle').textContent = `${s.system.stage} / ${s.system.version} / uptime ${seconds(s.uptime)}`;
    setList('system', [dtdd('Application', s.system.application_name), dtdd('Version', s.system.version), dtdd('Stage', s.system.stage), dtdd('Uptime', seconds(s.uptime))]);
    setList('joystick', [dtdd('Status', boolBadge(s.joystick.connected)), dtdd('Device', s.joystick.device_name), dtdd('Buttons', (s.joystick.pressed_buttons || []).join(', ') || 'none'), dtdd('Hat', `${s.joystick.hat.direction} (${s.joystick.hat.x}, ${s.joystick.hat.y})`), dtdd('Pan/Tilt/Zoom', `${s.joystick.normalized_axes.pan} / ${s.joystick.normalized_axes.tilt} / ${s.joystick.normalized_axes.zoom}`)]);
    setList('switcher', [dtdd('Status', boolBadge(s.switcher.connected)), dtdd('Type', s.switcher.type), dtdd('Program', s.program), dtdd('Preview', s.preview)]);
    setList('ptz', [dtdd('Active camera', s.active_ptz_camera), dtdd('Moving', s.ptz.moving), dtdd('Pan/Tilt active', s.ptz.pan_tilt_active), dtdd('Zoom active', s.ptz.zoom_active), dtdd('Hat active', s.ptz.hat_active), dtdd('Last action', s.ptz.last_action)]);
    setList('safety', [dtdd('Watchdog', s.safety.watchdog_enabled), dtdd('Center samples', s.safety.center_confirm_samples), dtdd('Output deadzone', `pan/tilt=${s.safety.output_deadzone.pan_tilt}, zoom=${s.safety.output_deadzone.zoom}`)]);
    document.getElementById('cameras').innerHTML = (s.ptz.configured_cameras || []).map(c => `<li>${c.active ? '▶ ' : ''}${c.name} (${c.id}) — ${c.enabled ? 'enabled' : 'disabled'} ${c.host || ''}</li>`).join('') || '<li>none</li>';
    document.getElementById('events').innerHTML = (s.recent_activity || []).map(e => `<li><span class=\"mono\">${e.created_at}</span> ${e.type}</li>`).join('') || '<li>none</li>';
  } catch (e) {
    document.getElementById('subtitle').textContent = `Status refresh failed: ${e}`;
  }
}
refresh();
setInterval(refresh, 1000);
</script>
</body>
</html>
"""


CONFIG_HTML = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>PTZ Joystick Controller Config</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; padding: 1rem; background: Canvas; color: CanvasText; }
    header { margin-bottom: 1rem; }
    h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
    textarea { width: 100%; min-height: 60vh; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: .9rem; }
    button { padding: .65rem 1rem; margin-top: .75rem; font-weight: 700; }
    .message { margin: .75rem 0; padding: .6rem; border-radius: .5rem; border: 1px solid color-mix(in srgb, CanvasText 25%, transparent); }
    .ok { color: #198754; }
    .bad { color: #dc3545; white-space: pre-wrap; }
    nav a { margin-right: .75rem; }
  </style>
</head>
<body>
<header>
  <h1>Configuration</h1>
  <nav><a href=\"/\">Dashboard</a></nav>
  <p>Safe limited editor. Saves only to <code>config.local.yaml</code>. Restart required after saving.</p>
</header>
<div id=\"message\" class=\"message\">Loading…</div>
<textarea id=\"config\" spellcheck=\"false\"></textarea>
<br />
<button id=\"save\">Save to config.local.yaml</button>
<script>
async function loadConfig() {
  const r = await fetch('/api/config', {cache: 'no-store'});
  const data = await r.json();
  document.getElementById('config').value = JSON.stringify(data.editable_config, null, 2);
  document.getElementById('message').textContent = 'Loaded current merged configuration.';
  document.getElementById('message').className = 'message ok';
}
async function saveConfig() {
  const box = document.getElementById('config');
  const msg = document.getElementById('message');
  let payload;
  try { payload = JSON.parse(box.value); } catch (e) {
    msg.textContent = 'Invalid JSON: ' + e;
    msg.className = 'message bad';
    return;
  }
  const r = await fetch('/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
  const data = await r.json();
  msg.textContent = data.message || data.error || JSON.stringify(data);
  msg.className = 'message ' + (r.ok ? 'ok' : 'bad');
}
document.getElementById('save').addEventListener('click', saveConfig);
loadConfig();
</script>
</body>
</html>
"""


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

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status")
    def api_status() -> dict[str, Any]:
        return status_provider.status()

    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        return {"editable_config": config_editor.editable_payload()}

    @app.get("/config", response_class=HTMLResponse)
    def config_page() -> HTMLResponse:
        return HTMLResponse(CONFIG_HTML)

    @app.post("/config")
    async def save_config(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ConfigEditError("Configuration payload must be a JSON object")
            result = config_editor.save_patch(payload)
            return JSONResponse(result)
        except ConfigEditError as exc:
            return JSONResponse({"status": "error", "error": str(exc), "message": str(exc)}, status_code=400)

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(DASHBOARD_HTML)

    return app

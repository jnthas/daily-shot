"""Local-only Web UI for live MJPEG preview, V4L2 parameter tuning, and manual 12MP capture."""

from __future__ import annotations

import ipaddress
import json
import logging
import subprocess
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .camera import (
    CameraError,
    apply_controls,
    build_archive_path,
    camera_exclusive_lock,
    capture_photo,
    is_capture_requested,
    list_camera_controls,
    list_camera_resolutions,
    load_effective_controls,
    parse_resolution,
    resolve_device,
    save_control_overrides,
)
from .config import AppConfig
from .solar import resolve_timezone
from .state import load_state, save_state

logger = logging.getLogger(__name__)

_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>daily-shot Camera Control</title>
  <style>
    :root {
      --bg: #0f141b;
      --panel: #18202c;
      --border: #2b3748;
      --text: #e6edf3;
      --muted: #93a1b2;
      --accent: #f59e0b;
      --accent-2: #38bdf8;
      --danger: #ef4444;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      padding: 14px 20px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 10px;
    }
    header h1 { margin: 0; font-size: 1.15rem; }
    .status-pill { font-size: 0.85rem; color: var(--muted); }
    main {
      display: grid;
      grid-template-columns: minmax(320px, 1.4fr) minmax(300px, 1fr);
      gap: 16px;
      padding: 16px;
      max-width: 1500px;
      margin: 0 auto;
    }
    @media (max-width: 960px) {
      main { grid-template-columns: 1fr; }
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px;
    }
    .preview-box {
      position: relative;
      background: #05070a;
      border-radius: 8px;
      overflow: hidden;
      aspect-ratio: 16 / 9;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .preview-box img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
    }
    .actions {
      margin-top: 12px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
    }
    button {
      background: var(--accent);
      color: #111;
      font-weight: 600;
      border: none;
      border-radius: 6px;
      padding: 9px 14px;
      cursor: pointer;
    }
    button.secondary { background: #293548; color: var(--text); }
    button.danger { background: var(--danger); color: #fff; }
    button:disabled { opacity: 0.55; cursor: wait; }
    label.check {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 0.92rem;
      cursor: pointer;
    }
    .controls-scroll {
      max-height: 76vh;
      overflow-y: auto;
      padding-right: 4px;
    }
    h2 { font-size: 0.95rem; margin: 14px 0 8px; color: var(--accent-2); }
    h2:first-child { margin-top: 0; }
    .ctrl-row {
      margin-bottom: 10px;
      padding-bottom: 8px;
      border-bottom: 1px solid rgba(255,255,255,0.06);
    }
    .ctrl-head {
      display: flex;
      justify-content: space-between;
      font-size: 0.85rem;
      margin-bottom: 4px;
    }
    .ctrl-name { font-family: ui-monospace, SFMono-Regular, monospace; }
    .ctrl-val { color: var(--accent); font-family: ui-monospace, monospace; }
    input[type="range"] { width: 100%; accent-color: var(--accent); }
    .range-wrap {
      display: grid;
      grid-template-columns: 28px 1fr 28px 72px;
      gap: 6px;
      align-items: center;
    }
    .step-btn {
      padding: 4px 0;
      font-size: 0.9rem;
      line-height: 1.1;
      background: #293548;
      color: var(--text);
    }
    .num-input {
      width: 100%;
      background: #0f141b;
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 4px 6px;
      font-family: ui-monospace, monospace;
      font-size: 0.85rem;
    }
    .hint { font-size: 0.78rem; color: var(--muted); margin-bottom: 10px; }
    select {
      width: 100%;
      background: #0f141b;
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 6px;
    }
    .inactive { opacity: 0.45; }
    .latest-shot { margin-top: 14px; }
    .latest-shot img {
      max-width: 100%;
      border-radius: 8px;
      border: 1px solid var(--border);
      margin-top: 8px;
    }
    .toast { font-size: 0.85rem; color: var(--accent-2); min-height: 1.2em; }
  </style>
</head>
<body>
  <header>
    <h1>daily-shot &mdash; Local Camera Tuning</h1>
    <div class="status-pill" id="deviceStatus">Connecting...</div>
  </header>
  <main>
    <section class="card">
      <div class="preview-box">
        <img id="liveStream" src="/stream.mjpg" alt="Live camera preview" />
      </div>
      <div class="actions">
        <button id="captureBtn" type="button">Take Photo</button>
        <select id="resolutionSelect" style="width:auto"></select>
        <label class="check">
          <input type="checkbox" id="watermarkToggle" />
          Timestamp Watermark
        </label>
        <button id="reloadStreamBtn" class="secondary" type="button">Reload Preview</button>
      </div>
      <div class="toast" id="toast"></div>
      <div class="latest-shot" id="latestShotBox" hidden>
        <div class="ctrl-head">
          <span>Latest Capture</span>
          <a id="latestLink" href="#" target="_blank" style="color:var(--accent-2)">Open Full Photo</a>
        </div>
        <img id="latestImg" src="" alt="Latest capture" />
      </div>
    </section>

    <section class="card">
      <div class="actions" style="margin-top:0; margin-bottom:6px;">
        <button id="saveDefaultsBtn" type="button">Save as Capture Defaults</button>
        <button id="resetDefaultsBtn" class="danger" type="button">Reset Hardware Defaults</button>
      </div>
      <div class="hint">Tip: Focus any slider and use Arrow keys (&plusmn;1 step) or Shift+Arrow (&plusmn;10 steps) to fine-tune. Changes apply after 450ms idle.</div>
      <div class="controls-scroll" id="controlsContainer">Loading controls...</div>
    </section>
  </main>

  <script>
    const toastEl = document.getElementById("toast");
    const controlsContainer = document.getElementById("controlsContainer");
    const captureBtn = document.getElementById("captureBtn");
    const resolutionSelect = document.getElementById("resolutionSelect");
    const watermarkToggle = document.getElementById("watermarkToggle");
    const liveStream = document.getElementById("liveStream");
    const deviceStatus = document.getElementById("deviceStatus");

    function setToast(msg) { toastEl.textContent = msg; }

    const SLIDER_DEBOUNCE_MS = 450;
    const INSTANT_DEBOUNCE_MS = 50;
    const controlWidgets = {};
    let currentControls = [];
    let pendingPatch = {};
    let patchTimer = null;

    function queueControlUpdate(name, value, delayMs = SLIDER_DEBOUNCE_MS) {
      pendingPatch[name] = value;
      const w = controlWidgets[name];
      if (w) w.valEl.textContent = `${value} (pending)`;
      clearTimeout(patchTimer);
      patchTimer = setTimeout(async () => {
        const payload = { controls: pendingPatch };
        pendingPatch = {};
        patchTimer = null;
        try {
          const res = await fetch("/api/controls", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || "Update failed");
          syncControls(data.controls);
          setToast("Updated live camera controls.");
        } catch (err) {
          setToast("Error: " + err.message);
        }
      }, delayMs);
    }

    function applyIntDelta(c, delta, delayMs = SLIDER_DEBOUNCE_MS) {
      const w = controlWidgets[c.name];
      if (!w || w.slider.disabled) return;
      const minV = c.min ?? 0;
      const maxV = c.max ?? 100;
      const next = Math.min(maxV, Math.max(minV, Number(w.slider.value) + delta));
      w.slider.value = next;
      w.numInput.value = next;
      queueControlUpdate(c.name, next, delayMs);
    }

    function syncControls(controls) {
      currentControls = controls;
      if (Object.keys(controlWidgets).length === 0) {
        buildControls(controls);
        return;
      }
      for (const c of controls) {
        const w = controlWidgets[c.name];
        if (!w) {
          buildControls(controls);
          return;
        }
        w.meta = c;
        w.row.className = "ctrl-row" + (c.inactive ? " inactive" : "");
        const hasPending = Object.prototype.hasOwnProperty.call(pendingPatch, c.name);
        if (!hasPending) {
          const autoTag = c.inactive ? " (live)" : "";
          w.valEl.textContent = `${c.value}${autoTag}`;
        }

        if (c.kind === "bool") {
          w.checkbox.disabled = c.inactive;
          if (!hasPending) w.checkbox.checked = Boolean(c.value);
        } else if (c.kind === "menu") {
          w.select.disabled = c.inactive;
          if (!hasPending) w.select.value = String(c.value);
        } else {
          w.slider.disabled = c.inactive;
          w.numInput.disabled = c.inactive;
          w.minusBtn.disabled = c.inactive;
          w.plusBtn.disabled = c.inactive;
          if (!hasPending) {
            if (document.activeElement !== w.slider) w.slider.value = c.value;
            if (document.activeElement !== w.numInput) w.numInput.value = c.value;
          }
        }
      }
    }

    function buildControls(controls) {
      const groups = {};
      for (const c of controls) {
        (groups[c.group] ||= []).push(c);
      }
      controlsContainer.innerHTML = "";
      for (const k of Object.keys(controlWidgets)) delete controlWidgets[k];

      for (const [groupName, items] of Object.entries(groups)) {
        const h2 = document.createElement("h2");
        h2.textContent = groupName;
        controlsContainer.appendChild(h2);

        for (const c of items) {
          const row = document.createElement("div");
          row.className = "ctrl-row" + (c.inactive ? " inactive" : "");

          const head = document.createElement("div");
          head.className = "ctrl-head";
          const nameEl = document.createElement("span");
          nameEl.className = "ctrl-name";
          nameEl.textContent = c.name;
          const valEl = document.createElement("span");
          valEl.className = "ctrl-val";
          valEl.id = "val-" + c.name;
          valEl.textContent = `${c.value}${c.inactive ? " (live)" : ""}`;
          head.appendChild(nameEl);
          head.appendChild(valEl);
          row.appendChild(head);

          const widget = { row, valEl, meta: c };

          if (c.kind === "bool") {
            const lbl = document.createElement("label");
            lbl.className = "check";
            const cb = document.createElement("input");
            cb.type = "checkbox";
            cb.checked = Boolean(c.value);
            cb.disabled = c.inactive;
            cb.addEventListener("change", () => {
              const v = cb.checked ? 1 : 0;
              queueControlUpdate(c.name, v, INSTANT_DEBOUNCE_MS);
            });
            lbl.appendChild(cb);
            lbl.appendChild(document.createTextNode("Enabled"));
            row.appendChild(lbl);
            widget.checkbox = cb;
          } else if (c.kind === "menu") {
            const sel = document.createElement("select");
            sel.disabled = c.inactive;
            for (const [k, label] of Object.entries(c.menu_items || {})) {
              const opt = document.createElement("option");
              opt.value = k;
              opt.textContent = `${k}: ${label}`;
              if (Number(k) === c.value) opt.selected = true;
              sel.appendChild(opt);
            }
            sel.addEventListener("change", () => {
              const v = Number(sel.value);
              queueControlUpdate(c.name, v, INSTANT_DEBOUNCE_MS);
            });
            row.appendChild(sel);
            widget.select = sel;
          } else {
            const wrap = document.createElement("div");
            wrap.className = "range-wrap";

            const step = c.step ?? 1;
            const minusBtn = document.createElement("button");
            minusBtn.type = "button";
            minusBtn.className = "step-btn";
            minusBtn.textContent = "-";
            minusBtn.disabled = c.inactive;
            minusBtn.addEventListener("click", (e) => {
              applyIntDelta(widget.meta, e.shiftKey ? -10 * step : -step);
            });

            const slider = document.createElement("input");
            slider.type = "range";
            slider.min = c.min ?? 0;
            slider.max = c.max ?? 100;
            slider.step = step;
            slider.value = c.value;
            slider.disabled = c.inactive;

            const numInput = document.createElement("input");
            numInput.type = "number";
            numInput.className = "num-input";
            numInput.min = c.min ?? 0;
            numInput.max = c.max ?? 100;
            numInput.step = step;
            numInput.value = c.value;
            numInput.disabled = c.inactive;

            const plusBtn = document.createElement("button");
            plusBtn.type = "button";
            plusBtn.className = "step-btn";
            plusBtn.textContent = "+";
            plusBtn.disabled = c.inactive;
            plusBtn.addEventListener("click", (e) => {
              applyIntDelta(widget.meta, e.shiftKey ? 10 * step : step);
            });

            slider.addEventListener("input", () => {
              numInput.value = slider.value;
              queueControlUpdate(c.name, Number(slider.value), SLIDER_DEBOUNCE_MS);
            });
            slider.addEventListener("keydown", (e) => {
              if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
                e.preventDefault();
                applyIntDelta(widget.meta, e.shiftKey ? -10 * step : -step);
              } else if (e.key === "ArrowRight" || e.key === "ArrowUp") {
                e.preventDefault();
                applyIntDelta(widget.meta, e.shiftKey ? 10 * step : step);
              } else if (e.key === "PageDown") {
                e.preventDefault();
                applyIntDelta(widget.meta, -10 * step);
              } else if (e.key === "PageUp") {
                e.preventDefault();
                applyIntDelta(widget.meta, 10 * step);
              }
            });

            numInput.addEventListener("input", () => {
              if (numInput.value === "") return;
              const minV = c.min ?? 0;
              const maxV = c.max ?? 100;
              const clamped = Math.min(maxV, Math.max(minV, Number(numInput.value)));
              slider.value = clamped;
              queueControlUpdate(c.name, clamped, SLIDER_DEBOUNCE_MS);
            });

            wrap.appendChild(minusBtn);
            wrap.appendChild(slider);
            wrap.appendChild(plusBtn);
            wrap.appendChild(numInput);
            row.appendChild(wrap);
            widget.slider = slider;
            widget.numInput = numInput;
            widget.minusBtn = minusBtn;
            widget.plusBtn = plusBtn;
          }
          controlWidgets[c.name] = widget;
          controlsContainer.appendChild(row);
        }
      }
    }

    function hasActiveAutoControls() {
      return currentControls.some((c) => c.inactive);
    }

    setInterval(async () => {
      if (patchTimer !== null || Object.keys(pendingPatch).length > 0) return;
      if (!hasActiveAutoControls()) return;
      try {
        const res = await fetch("/api/controls?fast=1");
        if (!res.ok) return;
        const data = await res.json();
        syncControls(data.controls);
      } catch (_) {}
    }, 1200);

    async function loadInitial() {
      const res = await fetch("/api/controls");
      const data = await res.json();
      deviceStatus.textContent = `Device: ${data.device}`;
      watermarkToggle.checked = Boolean(data.manual_capture_watermark_default);
      resolutionSelect.innerHTML = "";
      for (const r of (data.resolutions || [])) {
        const opt = document.createElement("option");
        opt.value = r.label;
        opt.textContent = r.label;
        if (r.label === data.default_resolution) opt.selected = true;
        resolutionSelect.appendChild(opt);
      }
      syncControls(data.controls);
      if (data.latest_shot_url) {
        showLatestShot(data.latest_shot_url);
      }
    }

    function showLatestShot(url) {
      const box = document.getElementById("latestShotBox");
      const img = document.getElementById("latestImg");
      const link = document.getElementById("latestLink");
      const cacheBusted = url + "?t=" + Date.now();
      img.src = cacheBusted;
      link.href = cacheBusted;
      box.hidden = false;
    }

    captureBtn.addEventListener("click", async () => {
      const resChoice = resolutionSelect.value || "";
      captureBtn.disabled = true;
      setToast(`Pausing preview & capturing ${resChoice || "photo"}...`);
      liveStream.src = "";
      try {
        const res = await fetch("/api/capture", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            watermark: watermarkToggle.checked,
            resolution: resChoice || undefined,
          }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Capture failed");
        showLatestShot(data.url);
        setToast(`Saved ${data.relative_path} (${data.width}x${data.height}, ${Math.round(data.size_bytes / 1024)} KB, watermark=${data.watermarked})`);
      } catch (err) {
        setToast("Capture error: " + err.message);
      } finally {
        liveStream.src = "/stream.mjpg?t=" + Date.now();
        captureBtn.disabled = false;
      }
    });

    document.getElementById("saveDefaultsBtn").addEventListener("click", async () => {
      const res = await fetch("/api/controls/save", { method: "POST" });
      const data = await res.json();
      setToast(res.ok ? `Saved ${Object.keys(data.saved).length} defaults to override file.` : data.error);
    });

    document.getElementById("resetDefaultsBtn").addEventListener("click", async () => {
      const res = await fetch("/api/controls/reset", { method: "POST" });
      const data = await res.json();
      if (res.ok) {
        syncControls(data.controls);
        setToast("Reset controls to hardware defaults.");
      } else {
        setToast("Reset error: " + data.error);
      }
    });

    document.getElementById("reloadStreamBtn").addEventListener("click", () => {
      liveStream.src = "/stream.mjpg?t=" + Date.now();
    });

    loadInitial().catch((e) => setToast("Init failed: " + e.message));
  </script>
</body>
</html>
"""


def is_client_allowed(client_ip: str, allowed_cidrs: tuple[str, ...]) -> bool:
    """Return True if client_ip matches one of the configured local CIDRs."""
    if not allowed_cidrs:
        return True
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped

    for cidr in allowed_cidrs:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if addr in net:
            return True
    return False


def _latest_shot_relative(archive_dir: Path) -> str | None:
    if not archive_dir.exists():
        return None
    jpgs = sorted(
        (p for p in archive_dir.rglob("*.jpg") if p.is_file() and not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not jpgs:
        return None
    return jpgs[0].relative_to(archive_dir).as_posix()


def _iter_mjpeg_frames(stdout_stream: Any, lock_dir: Path | None) -> Any:
    """Yield individual JPEG frames from ffmpeg `-f image2pipe` stdout, yielding on capture request."""
    buf = bytearray()
    while True:
        if lock_dir is not None and is_capture_requested(lock_dir):
            break
        chunk = stdout_stream.read(16384)
        if not chunk:
            break
        buf.extend(chunk)
        while True:
            soi = buf.find(b"\xff\xd8")
            if soi == -1:
                buf.clear()
                break
            if soi > 0:
                del buf[:soi]
            eoi = buf.find(b"\xff\xd9", 2)
            if eoi == -1:
                break
            frame = bytes(buf[: eoi + 2])
            del buf[: eoi + 2]
            yield frame


def make_web_handler(cfg: AppConfig) -> type[BaseHTTPRequestHandler]:
    """Create a request handler bound to AppConfig."""
    lock_dir = cfg.storage.state_file.parent

    class DailyShotHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("web %s - " + format, self.address_string(), *args)

        def _check_access(self) -> bool:
            client_ip = self.client_address[0]
            if not is_client_allowed(client_ip, cfg.web.allowed_cidrs):
                self._send_json({"error": "Forbidden: local access only"}, HTTPStatus.FORBIDDEN)
                return False
            return True

        def _send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            self._send_bytes(
                json.dumps(payload).encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode("utf-8"))
                return parsed if isinstance(parsed, dict) else {}
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid JSON body: {exc}") from exc

        def do_GET(self) -> None:
            if not self._check_access():
                return
            path = self.path.split("?", 1)[0]

            if path == "/":
                self._send_bytes(_INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
                return

            if path == "/api/controls":
                try:
                    fast = "fast=1" in self.path
                    device = resolve_device(cfg.camera)
                    controls = [c.to_dict() for c in list_camera_controls(device)]
                    payload: dict[str, Any] = {
                        "device": device,
                        "controls": controls,
                    }
                    if not fast:
                        res_pairs = list_camera_resolutions(device, cfg.camera.input_format)
                        if not res_pairs:
                            res_pairs = [(cfg.camera.width, cfg.camera.height)]
                        rel = _latest_shot_relative(cfg.storage.archive_dir)
                        payload.update(
                            {
                                "resolutions": [
                                    {"width": w, "height": h, "label": f"{w}x{h}"} for w, h in res_pairs
                                ],
                                "default_resolution": f"{cfg.camera.width}x{cfg.camera.height}",
                                "manual_capture_watermark_default": cfg.web.manual_capture_watermark_default,
                                "latest_shot_url": f"/shots/{rel}" if rel else None,
                            }
                        )
                    self._send_json(payload)
                except CameraError as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
                return

            if path == "/stream.mjpg":
                self._handle_mjpeg_stream()
                return

            if path.startswith("/shots/"):
                self._handle_serve_shot(path[len("/shots/") :])
                return

            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if not self._check_access():
                return
            path = self.path.split("?", 1)[0]

            try:
                body = self._read_json_body()
            except ValueError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return

            if path == "/api/controls":
                raw_ctrls = body.get("controls", {})
                if not isinstance(raw_ctrls, dict) or not raw_ctrls:
                    self._send_json({"error": "controls object is required"}, HTTPStatus.BAD_REQUEST)
                    return
                try:
                    updates = {str(k): int(v) for k, v in raw_ctrls.items()}
                    device = resolve_device(cfg.camera)
                    apply_controls(device, updates)
                    controls = [c.to_dict() for c in list_camera_controls(device)]
                    self._send_json({"device": device, "controls": controls})
                except (ValueError, TypeError) as exc:
                    self._send_json({"error": f"Invalid control value: {exc}"}, HTTPStatus.BAD_REQUEST)
                except CameraError as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return

            if path == "/api/controls/save":
                try:
                    device = resolve_device(cfg.camera)
                    live = {
                        c.name: c.value
                        for c in list_camera_controls(device)
                        if not c.inactive
                    }
                    save_control_overrides(cfg.camera.controls_override_file, live)
                    self._send_json({"saved": live, "path": str(cfg.camera.controls_override_file)})
                except CameraError as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
                return

            if path == "/api/controls/reset":
                try:
                    device = resolve_device(cfg.camera)
                    defaults = {c.name: c.default for c in list_camera_controls(device)}
                    apply_controls(device, defaults)
                    cfg.camera.controls_override_file.unlink(missing_ok=True)
                    controls = [c.to_dict() for c in list_camera_controls(device)]
                    self._send_json({"device": device, "controls": controls})
                except CameraError as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
                return

            if path == "/api/capture":
                watermark = bool(
                    body.get("watermark", cfg.web.manual_capture_watermark_default)
                )
                try:
                    if body.get("resolution"):
                        cap_w, cap_h = parse_resolution(str(body["resolution"]))
                    elif "width" in body and "height" in body:
                        cap_w, cap_h = int(body["width"]), int(body["height"])
                        if cap_w <= 0 or cap_h <= 0:
                            raise ValueError("width and height must be positive")
                    else:
                        cap_w, cap_h = cfg.camera.width, cfg.camera.height
                except (ValueError, TypeError) as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return

                tz = resolve_timezone(cfg.location.timezone)
                now = datetime.now(tz)
                out_path = build_archive_path(cfg.storage.archive_dir, now)
                try:
                    device = resolve_device(cfg.camera)
                    live_ctrls = {
                        c.name: c.value
                        for c in list_camera_controls(device)
                        if not c.inactive
                    }
                    capture_photo(
                        cfg.camera,
                        out_path,
                        width=cap_w,
                        height=cap_h,
                        controls_override=live_ctrls,
                        watermark_config=cfg.watermark,
                        watermark=watermark,
                        timestamp=now,
                        lock_dir=lock_dir,
                    )
                    state = load_state(cfg.storage.state_file)
                    state["sync_pending"] = True
                    save_state(cfg.storage.state_file, state)
                    rel = out_path.relative_to(cfg.storage.archive_dir).as_posix()
                    self._send_json(
                        {
                            "path": str(out_path),
                            "relative_path": rel,
                            "url": f"/shots/{rel}",
                            "width": cap_w,
                            "height": cap_h,
                            "size_bytes": out_path.stat().st_size,
                            "watermarked": watermark,
                        }
                    )
                except CameraError as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
                return

            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        def _handle_serve_shot(self, rel_raw: str) -> None:
            archive_root = cfg.storage.archive_dir.resolve()
            if rel_raw == "latest":
                latest_rel = _latest_shot_relative(cfg.storage.archive_dir)
                if not latest_rel:
                    self._send_json({"error": "No captures available"}, HTTPStatus.NOT_FOUND)
                    return
                target = (archive_root / latest_rel).resolve()
            else:
                target = (archive_root / unquote(rel_raw)).resolve()

            if not target.is_relative_to(archive_root) or not target.is_file():
                self._send_json({"error": "Shot not found"}, HTTPStatus.NOT_FOUND)
                return
            self._send_bytes(target.read_bytes(), "image/jpeg")

        def _handle_mjpeg_stream(self) -> None:
            if is_capture_requested(lock_dir):
                self._send_json({"error": "Capture in progress"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            try:
                device = resolve_device(cfg.camera)
                apply_controls(device, load_effective_controls(cfg.camera))
            except CameraError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
                return

            try:
                with camera_exclusive_lock(lock_dir, request_capture=False, blocking=False):
                    cmd = [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "v4l2",
                        "-input_format",
                        cfg.camera.input_format,
                        "-video_size",
                        f"{cfg.web.preview_width}x{cfg.web.preview_height}",
                        "-framerate",
                        str(cfg.web.preview_fps),
                        "-i",
                        device,
                        "-c:v",
                        "copy",
                        "-f",
                        "image2pipe",
                        "-",
                    ]
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    try:
                        self.send_response(HTTPStatus.OK)
                        self.send_header(
                            "Content-Type", "multipart/x-mixed-replace; boundary=frame"
                        )
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        assert proc.stdout is not None
                        for frame in _iter_mjpeg_frames(proc.stdout, lock_dir):
                            header = (
                                f"--frame\r\nContent-Type: image/jpeg\r\n"
                                f"Content-Length: {len(frame)}\r\n\r\n"
                            ).encode("ascii")
                            self.wfile.write(header + frame + b"\r\n")
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        proc.terminate()
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
            except BlockingIOError:
                self._send_json({"error": "Camera busy"}, HTTPStatus.SERVICE_UNAVAILABLE)
                time.sleep(0.1)

    return DailyShotHandler


def serve_web(cfg: AppConfig, *, host: str | None = None, port: int | None = None) -> None:
    """Start the local-only Web UI server."""
    bind_host = host or cfg.web.host
    bind_port = port or cfg.web.port
    server = ThreadingHTTPServer((bind_host, bind_port), make_web_handler(cfg))
    logger.info("Serving daily-shot Web UI on http://%s:%d", bind_host, bind_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

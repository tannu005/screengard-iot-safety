/**
 * app.js — Dashboard Application
 * IoT Proximity-Based Screen Safety System
 *
 * Connects to Flask server via Socket.IO for real-time updates.
 * Renders brightness arc, proximity radar, detection cards, event log.
 * Supports full remote control and simulation mode.
 */

"use strict";

// ─── CONFIGURATION ────────────────────────────────────────────────────────────
const SERVER_URL   = window.location.origin;
const RECONNECT_MS = 3000;

// ─── DOM REFERENCES ───────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

const dom = {
  statusDot:      $("status-dot"),
  statusText:     $("status-text"),
  sysPill:        $("system-status-pill"),
  connBanner:     $("conn-banner"),

  brightnessVal:  $("brightness-value"),
  brightnessFill: $("brightness-fill"),
  brightnessThumb:$("brightness-thumb"),
  brightnessReason:$("brightness-reason"),
  brightnessLevelBadge: $("brightness-level-badge"),
  brightnessArcCanvas: $("brightness-arc"),
  manualSlider:   $("manual-brightness"),
  manualValue:    $("manual-value"),

  distanceVal:    $("distance-value"),
  zoneBadge:      $("zone-badge"),
  zoneSafe:       $("zone-safe"),
  zoneWarn:       $("zone-warning"),
  zoneDanger:     $("zone-danger"),
  radarCanvas:    $("radar-canvas"),

  cameraImg:      $("camera-feed"),
  cameraOverlay:  $("camera-overlay"),
  recDot:         $("rec-dot"),
  recText:        $("rec-text"),
  fpsCounter:     $("fps-counter"),

  faceStatus:     $("face-status"),
  faceDot:        $("face-dot"),
  cardFace:       $("card-face"),
  ageValue:       $("age-value"),
  ageDot:         $("age-dot"),
  cardAge:        $("card-age"),
  humanStatus:    $("human-status"),
  humanDot:       $("human-dot"),
  cardHuman:      $("card-human"),
  alertStatus:    $("alert-status"),
  alertDot:       $("alert-dot"),
  cardAlert:      $("card-alert"),

  powerBtn:       $("power-btn"),
  powerLabel:     $("power-label"),
  btnToggle:      $("btn-toggle"),
  btnRestore:     $("btn-restore"),
  actionRestore:  $("action-restore"),
  actionEmergency:$("action-emergency"),

  eventLog:       $("event-log"),
  btnClearLog:    $("btn-clear-log"),
  toastContainer: $("toast-container"),
};

// ─── STATE ────────────────────────────────────────────────────────────────────
let state = {
  systemEnabled:   true,
  connected:       false,
  brightness:      100,
  zone:            "absent",
  distanceCm:      -1,
  humanPresent:    false,
  faceDetected:    false,
  ageEstimate:     null,
  ageGroup:        "unknown",
  isChild:         false,
  alertLevel:      "none",
};

let brightnessArcCtx = null;
let radarCtx         = null;
let radarAngle       = 0;
let radarAnimId      = null;
let bgCtx            = null;
let bgParticles      = [];

// ─── SOCKET.IO ────────────────────────────────────────────────────────────────
const socket = io(SERVER_URL, {
  reconnection: true,
  reconnectionDelay: RECONNECT_MS,
  transports: ["websocket", "polling"],
});

socket.on("connect", () => {
  state.connected = true;
  setConnectionStatus(true);
  fetchInitialStatus();
});

socket.on("disconnect", () => {
  state.connected = false;
  setConnectionStatus(false);
});

socket.on("proximity_update", data => {
  state.distanceCm  = data.distance_cm;
  state.zone        = data.zone;
  state.humanPresent = data.human_present;
  updateProximityUI();
});

socket.on("vision_update", data => {
  state.faceDetected = data.face_detected;
  state.ageEstimate  = data.age_estimate;
  state.ageGroup     = data.age_group;
  state.isChild      = data.is_child;
  dom.fpsCounter.textContent = data.camera_fps ? `${data.camera_fps} fps` : "-- fps";
  updateVisionUI();
});

socket.on("brightness_update", data => {
  state.brightness = data.brightness;
  updateBrightnessUI(data.brightness, data.level, data.reason);
});

socket.on("system_update", data => {
  state.systemEnabled = data.enabled;
  updateSystemUI();
});

socket.on("event", data => {
  appendLogEntry(data);
  updateAlertUI(data);
});

// ─── REST API ─────────────────────────────────────────────────────────────────
async function api(action, extra = {}) {
  try {
    const res = await fetch(`${SERVER_URL}/api/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...extra }),
    });
    return await res.json();
  } catch (e) {
    showToast(`API error: ${e.message}`, "error");
    return null;
  }
}

async function fetchInitialStatus() {
  try {
    const res = await fetch(`${SERVER_URL}/api/status`);
    const data = await res.json();

    state.systemEnabled = data.system_enabled;
    state.distanceCm    = data.proximity.distance_cm;
    state.zone          = data.proximity.zone;
    state.humanPresent  = data.proximity.human_present;
    state.faceDetected  = data.vision.face_detected;
    state.ageEstimate   = data.vision.age_estimate;
    state.ageGroup      = data.vision.age_group;
    state.isChild       = data.vision.is_child;
    state.brightness    = data.brightness.current;

    updateProximityUI();
    updateVisionUI();
    updateBrightnessUI(data.brightness.current, data.brightness.level, data.brightness.reason);
    updateSystemUI();

    // Load recent events
    const evRes = await fetch(`${SERVER_URL}/api/events`);
    const evData = await evRes.json();
    evData.events.forEach(e => appendLogEntry(e));

  } catch (e) {
    console.warn("Could not fetch initial status:", e.message);
  }
}

// ─── UI UPDATERS ──────────────────────────────────────────────────────────────
function setConnectionStatus(online) {
  dom.statusDot.className = `status-dot ${online ? "online" : "offline"}`;
  dom.statusText.textContent = online ? "Connected" : "Disconnected";
  dom.connBanner.hidden = online;
}

function updateBrightnessUI(pct, level, reason) {
  pct = Math.max(0, Math.min(100, Math.round(pct)));
  dom.brightnessVal.textContent = pct;
  dom.brightnessFill.style.width = `${pct}%`;
  dom.brightnessThumb.style.left = `${pct}%`;
  drawBrightnessArc(pct);

  const levelMap = {
    NORMAL:   { text: "NORMAL",   cls: "level-normal" },
    REDUCED:  { text: "REDUCED",  cls: "level-reduced" },
    SAFE_DIM: { text: "SAFE DIM", cls: "level-dim" },
  };
  const lm = levelMap[level] || levelMap.NORMAL;
  dom.brightnessLevelBadge.textContent = lm.text;
  dom.brightnessLevelBadge.className   = `badge ${lm.cls}`;

  const reasonMap = {
    no_human_or_safe:       "No human detected — full brightness",
    human_in_warning_zone:  "Human in warning zone — brightness reduced",
    adult_in_danger_zone:   "Adult too close — brightness reduced",
    child_in_danger_zone:   "⚠ Child too close — protecting eyes",
    child_detected_confirming: "Child detected — confirming…",
    manual_restore:         "Manually restored to 100%",
    none:                   "System ready",
  };
  dom.brightnessReason.textContent = reasonMap[reason] || reason || "System ready";
  dom.brightnessReason.style.color = (level === "SAFE_DIM") ? "var(--red)" :
                                     (level === "REDUCED")  ? "var(--yellow)" : "var(--text-secondary)";
}

function updateProximityUI() {
  const d = state.distanceCm;
  dom.distanceVal.textContent = d >= 0 ? Math.round(d) : "--";
  dom.distanceVal.style.color = state.zone === "danger" ? "var(--red)" :
                                state.zone === "warning" ? "var(--yellow)" : "var(--text-primary)";

  const zoneMap = {
    safe: { text: "SAFE", cls: "zone-safe" },
    warning: { text: "WARNING", cls: "zone-warning" },
    danger: { text: "DANGER", cls: "zone-danger" },
    absent: { text: "ABSENT", cls: "" },
  };
  const zm = zoneMap[state.zone] || zoneMap.absent;
  dom.zoneBadge.textContent  = zm.text;
  dom.zoneBadge.className    = `badge badge-zone ${zm.cls}`;

  ["safe", "warning", "danger"].forEach(z => {
    const el = { safe: dom.zoneSafe, warning: dom.zoneWarn, danger: dom.zoneDanger }[z];
    el.classList.toggle("active", state.zone === z);
  });

  drawRadar(state.zone, d);

  // Human present card
  dom.humanStatus.textContent = state.humanPresent ? "Detected" : "Absent";
  dom.humanDot.className      = `detect-dot ${state.humanPresent ? "on" : ""}`;
  dom.cardHuman.classList.toggle("active", state.humanPresent);
}

function updateVisionUI() {
  dom.faceStatus.textContent = state.faceDetected
    ? `Detected (${state.ageGroup})`
    : "No Face";
  dom.faceDot.className = `detect-dot ${state.faceDetected ? "on" : ""}`;
  dom.cardFace.classList.toggle("active", state.faceDetected);

  let ageLabel = "Unknown";
  if (state.ageEstimate !== null && state.ageEstimate >= 0) {
    ageLabel = `~${Math.round(state.ageEstimate)}y (${state.ageGroup})`;
  }
  dom.ageValue.textContent = ageLabel;
  dom.ageDot.className     = `detect-dot ${state.isChild ? "alert" : state.faceDetected ? "on" : ""}`;
  dom.cardAge.classList.toggle("alert",  state.isChild);
  dom.cardAge.classList.toggle("active", state.faceDetected && !state.isChild);

  // Camera overlay
  const hasFrame = state.faceDetected || state.ageGroup !== "unknown";
  dom.cameraOverlay.classList.toggle("hidden", true); // Always hide once connected
  dom.recDot.className  = `rec-dot ${state.faceDetected ? "active" : ""}`;
  dom.recText.textContent = state.faceDetected ? "Live" : "Active";
}

function updateAlertUI(event) {
  if (!event || !event.type) return;
  const isAlert = event.type === "ALERT_CHILD";
  const isWarn  = event.type === "ALERT_ADULT";

  if (dom.alertStatus) dom.alertStatus.textContent = isAlert ? "Child Alert!" : isWarn ? "Adult Close" : "None";
  if (dom.alertDot)    dom.alertDot.className = `detect-dot ${isAlert ? "alert" : isWarn ? "warn" : ""}`;
  if (dom.cardAlert)   dom.cardAlert.classList.toggle("alert",  isAlert);
  if (dom.cardAlert)   dom.cardAlert.classList.toggle("active", isWarn && !isAlert);

  if (isAlert) {
    showToast("⚠ Child detected close to screen — dimming display", "warn");
  }
}


function updateSystemUI() {
  const on = state.systemEnabled;
  dom.powerBtn.classList.toggle("off", !on);
  dom.powerLabel.textContent = on ? "ACTIVE" : "OFF";
  dom.powerBtn.setAttribute("aria-pressed", String(on));
}

// ─── CANVAS: BRIGHTNESS ARC ───────────────────────────────────────────────────
function drawBrightnessArc(pct) {
  const canvas = dom.brightnessArcCanvas;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  const cx = W / 2, cy = H - 10;
  const r  = 90;
  const startAngle = Math.PI;
  const endAngle   = 2 * Math.PI;

  // Track
  ctx.beginPath();
  ctx.arc(cx, cy, r, startAngle, endAngle);
  ctx.strokeStyle = "rgba(255,255,255,0.06)";
  ctx.lineWidth = 14;
  ctx.lineCap = "round";
  ctx.stroke();

  // Value
  const valAngle = startAngle + (pct / 100) * Math.PI;
  const grad = ctx.createLinearGradient(cx - r, 0, cx + r, 0);
  grad.addColorStop(0, "#63b3ed");
  grad.addColorStop(1, "#90cdf4");

  ctx.beginPath();
  ctx.arc(cx, cy, r, startAngle, valAngle);
  ctx.strokeStyle = grad;
  ctx.lineWidth = 14;
  ctx.lineCap = "round";
  ctx.stroke();

  // Glow tip
  const tipX = cx + r * Math.cos(valAngle);
  const tipY = cy + r * Math.sin(valAngle);
  ctx.beginPath();
  ctx.arc(tipX, tipY, 7, 0, 2 * Math.PI);
  ctx.fillStyle = "#90cdf4";
  ctx.shadowColor = "#63b3ed";
  ctx.shadowBlur = 12;
  ctx.fill();
  ctx.shadowBlur = 0;
}

// ─── CANVAS: PROXIMITY RADAR ──────────────────────────────────────────────────
function drawRadar(zone, distanceCm) {
  const canvas = dom.radarCanvas;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  const cx = W / 2, cy = H - 10;
  const maxR = Math.min(W / 2, H) - 10;

  const colorMap = {
    safe:    "#48bb78",
    warning: "#ecc94b",
    danger:  "#fc8181",
    absent:  "#4a5568",
  };
  const color = colorMap[zone] || colorMap.absent;

  // Draw 3 concentric arcs (thresholds)
  const radii = [maxR * 0.35, maxR * 0.65, maxR];
  const labels = ["10cm", "25cm", ""];

  radii.forEach((r, i) => {
    ctx.beginPath();
    ctx.arc(cx, cy, r, Math.PI, 2 * Math.PI);
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.lineWidth = 1;
    ctx.stroke();

    if (labels[i]) {
      ctx.fillStyle = "rgba(255,255,255,0.2)";
      ctx.font = "9px Inter";
      ctx.fillText(labels[i], cx + r + 2, cy - 2);
    }
  });

  // Radar sweep line
  radarAngle = (radarAngle + 2) % 180;
  const sweepRad = (Math.PI + radarAngle * Math.PI / 180);
  const sweepX = cx + maxR * Math.cos(sweepRad);
  const sweepY = cy + maxR * Math.sin(sweepRad);

  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(sweepX, sweepY);
  ctx.strokeStyle = `${color}60`;
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // Object blip
  if (distanceCm >= 0 && zone !== "absent") {
    const blipR = maxR * Math.min(distanceCm / 200, 1.0);
    const blipX = cx;
    const blipY = cy - blipR;

    ctx.beginPath();
    ctx.arc(blipX, blipY, 5, 0, 2 * Math.PI);
    ctx.fillStyle = color;
    ctx.shadowColor = color;
    ctx.shadowBlur = 10;
    ctx.fill();
    ctx.shadowBlur = 0;
  }

  // Center dot
  ctx.beginPath();
  ctx.arc(cx, cy, 3, 0, 2 * Math.PI);
  ctx.fillStyle = "rgba(255,255,255,0.4)";
  ctx.fill();
}

function startRadarAnimation() {
  function frame() {
    drawRadar(state.zone, state.distanceCm);
    radarAnimId = requestAnimationFrame(frame);
  }
  frame();
}

// ─── BACKGROUND PARTICLES ─────────────────────────────────────────────────────
function initBackground() {
  const canvas = $("bg-canvas");
  if (!canvas) return;
  bgCtx = canvas.getContext("2d");

  function resize() {
    canvas.width  = window.innerWidth;
    canvas.height = window.innerHeight;
  }
  resize();
  window.addEventListener("resize", resize);

  // Create particles
  for (let i = 0; i < 60; i++) {
    bgParticles.push({
      x: Math.random() * window.innerWidth,
      y: Math.random() * window.innerHeight,
      r: Math.random() * 1.5 + 0.3,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      alpha: Math.random() * 0.4 + 0.1,
    });
  }

  function animateBg() {
    bgCtx.clearRect(0, 0, canvas.width, canvas.height);
    bgParticles.forEach(p => {
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < 0 || p.x > canvas.width)  p.vx *= -1;
      if (p.y < 0 || p.y > canvas.height) p.vy *= -1;

      bgCtx.beginPath();
      bgCtx.arc(p.x, p.y, p.r, 0, 2 * Math.PI);
      bgCtx.fillStyle = `rgba(99,179,237,${p.alpha})`;
      bgCtx.fill();
    });
    requestAnimationFrame(animateBg);
  }
  animateBg();
}

// ─── TOAST NOTIFICATIONS ──────────────────────────────────────────────────────
function showToast(msg, type = "info") {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  if (type === "error") t.style.borderColor = "var(--red)";
  if (type === "warn")  t.style.borderColor = "var(--yellow)";
  dom.toastContainer.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

// ─── EVENT LOG ────────────────────────────────────────────────────────────────
function appendLogEntry(event) {
  const empty = dom.eventLog.querySelector(".log-empty");
  if (empty) empty.remove();

  const entry = document.createElement("div");
  entry.className = "log-entry";
  entry.innerHTML = `
    <span class="log-type ${event.type}">${event.type}</span>
    <span class="log-time">${event.timestamp_str || "--:--:--"}</span>
    <span class="log-msg">${event.message || JSON.stringify(event)}</span>
  `;
  dom.eventLog.prepend(entry);

  // Keep max 50 entries in DOM
  const entries = dom.eventLog.querySelectorAll(".log-entry");
  if (entries.length > 50) entries[entries.length - 1].remove();
}

// ─── BUTTON EVENT LISTENERS ───────────────────────────────────────────────────
function bindControls() {
  // Power / toggle
  async function toggleSystem() {
    const action = state.systemEnabled ? "disable" : "enable";
    const res = await api(action);
    if (res?.status === "ok") {
      state.systemEnabled = res.system_enabled;
      updateSystemUI();
      showToast(`System ${state.systemEnabled ? "enabled" : "disabled"}`);
    }
  }
  dom.powerBtn.addEventListener("click", toggleSystem);
  dom.btnToggle.addEventListener("click", toggleSystem);

  // Restore brightness
  dom.btnRestore.addEventListener("click", async () => {
    await api("restore");
    showToast("Brightness restored to 100%");
  });
  dom.actionRestore.addEventListener("click", async () => {
    await api("restore");
    showToast("Brightness restored to 100%");
  });

  // Emergency dim
  dom.actionEmergency.addEventListener("click", async () => {
    await api("set_brightness", { value: 10 });
    showToast("Emergency dim: 10%", "warn");
  });

  // Manual slider
  dom.manualSlider.addEventListener("input", () => {
    dom.manualValue.textContent = `${dom.manualSlider.value}%`;
  });
  dom.manualSlider.addEventListener("change", async () => {
    const val = parseInt(dom.manualSlider.value);
    await api("set_brightness", { value: val });
  });

  // Simulation buttons
  document.querySelectorAll(".sim-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const dist = parseFloat(btn.dataset.dist);
      await api("simulate_proximity", { distance_cm: dist });
      showToast(`Simulated: ${dist}cm`);
    });
  });

  // Clear log
  dom.btnClearLog.addEventListener("click", () => {
    dom.eventLog.innerHTML = '<div class="log-empty">No events yet</div>';
  });
}

// ─── INIT ─────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  initBackground();
  drawBrightnessArc(100);
  startRadarAnimation();
  bindControls();

  // Initial UI
  dom.cameraOverlay.classList.remove("hidden");
  dom.cameraImg.addEventListener("load", () => {
    dom.cameraOverlay.classList.add("hidden");
  });
  dom.cameraImg.addEventListener("error", () => {
    dom.cameraOverlay.classList.remove("hidden");
    dom.recText.textContent = "Offline";
  });
});

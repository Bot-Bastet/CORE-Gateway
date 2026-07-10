// === Telecommande & Navigation ===
// SpotMicro 3D viewer + posture sliders + demo mode + popup navigation
// State is server-synced via WS (no cache/localStorage)

var controlSpeed = 0.15;
var controlActiveDir = null;
var controlWalkInterval = null;
var navTarget = null;
var robotPosture = { height: 0.0, speed: 50.0, roll: 0.0, pitch: 0.0, yaw: 0.0, demo_mode: false, powered: true };
var keysPressed = {};

function initControlTab() {
  if (!window._spot3dInitDone && typeof initSpotMicro3D === "function") {
    initSpotMicro3D("spotmicro-3d-container");
    window._spot3dInitDone = true;
  }
  if (!window.controlKeyboardInitialized) {
    window.controlKeyboardInitialized = true;
    window.addEventListener("keydown", function (e) {
      if (activeTab !== "control") return;
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      var keyMap = { "z": "up", "KeyW": "up", "ArrowUp": "up", "s": "down", "KeyS": "down", "ArrowDown": "down", "q": "left", "KeyA": "left", "ArrowLeft": "left", "d": "right", "KeyD": "right", "ArrowRight": "right" };
      var dir = keyMap[e.key] || keyMap[e.code];
      if (dir && !keysPressed[dir]) { e.preventDefault(); keysPressed[dir] = true; startWalking(dir); }
      if (e.key === " " || e.key === "x" || e.key === "Escape") { e.preventDefault(); sendControlStop(); }
    });
    window.addEventListener("keyup", function (e) {
      if (activeTab !== "control") return;
      var keyMap = { "z": "up", "KeyW": "up", "ArrowUp": "up", "s": "down", "KeyS": "down", "ArrowDown": "down", "q": "left", "KeyA": "left", "ArrowLeft": "left", "d": "right", "KeyD": "right", "ArrowRight": "right" };
      var dir = keyMap[e.key] || keyMap[e.code];
      if (dir) { keysPressed[dir] = false; if (!Object.values(keysPressed).includes(true)) stopWalking(); }
    });
  }
}

function onPostureSliderChange(key, value) {
  robotPosture[key] = parseFloat(value);
  var labelEl = document.getElementById("posture-val-" + key);
  if (labelEl) { var units = (key === "height" || key === "speed") ? "%" : "°"; labelEl.textContent = value + units; }
  if (typeof updateSpotMicroPosture === "function") updateSpotMicroPosture(robotPosture);
  debouncePostureSync(key, value);
}

var _postureSyncTimers = {};
function debouncePostureSync(key, value) {
  if (_postureSyncTimers[key]) clearTimeout(_postureSyncTimers[key]);
  _postureSyncTimers[key] = setTimeout(function () {
    if (appWs && appWs.readyState === WebSocket.OPEN) appWs.send(JSON.stringify({ type: "robot_posture_update", key: key, value: value }));
  }, 100);
}

function toggleDemoMode(checked) {
  robotPosture.demo_mode = checked;
  if (typeof setSpotMicroDemoMode === "function") setSpotMicroDemoMode(checked);
  if (appWs && appWs.readyState === WebSocket.OPEN) appWs.send(JSON.stringify({ type: "demo_mode", enabled: checked }));
  var badge = document.getElementById("demo-mode-badge");
  if (badge) { badge.textContent = checked ? "SIMULATION" : "ACTIF"; badge.style.background = checked ? "rgba(239,68,68,0.15)" : "rgba(16,185,129,0.15)"; badge.style.color = checked ? "var(--danger)" : "var(--success)"; }
}

function applyRobotPostureSync(postureData) {
  Object.keys(postureData).forEach(function (key) { if (robotPosture.hasOwnProperty(key)) robotPosture[key] = postureData[key]; });
  ["height", "speed", "roll", "pitch", "yaw"].forEach(function (key) {
    var slider = document.getElementById("posture-slider-" + key);
    var label = document.getElementById("posture-val-" + key);
    if (slider) slider.value = robotPosture[key];
    if (label) { var units = (key === "height" || key === "speed") ? "%" : "°"; label.textContent = robotPosture[key] + units; }
  });
  var demoCheck = document.getElementById("demo-mode-checkbox");
  if (demoCheck) demoCheck.checked = robotPosture.demo_mode;
  var badge = document.getElementById("demo-mode-badge");
  if (badge) { badge.textContent = robotPosture.demo_mode ? "SIMULATION" : "ACTIF"; badge.style.background = robotPosture.demo_mode ? "rgba(239,68,68,0.15)" : "rgba(16,185,129,0.15)"; badge.style.color = robotPosture.demo_mode ? "var(--danger)" : "var(--success)"; }
  if (typeof updateSpotMicroPosture === "function") updateSpotMicroPosture(robotPosture);
  if (typeof setSpotMicroDemoMode === "function") setSpotMicroDemoMode(robotPosture.demo_mode);
}

function openNavPopup() {
  var overlay = document.getElementById("nav-popup-overlay");
  if (!overlay) return;
  overlay.style.display = "flex";
  window._navPopupActive = true;
  drawNavPopupMap();
}

function closeNavPopup() {
  var overlay = document.getElementById("nav-popup-overlay");
  if (overlay) overlay.style.display = "none";
  window._navPopupActive = false;
  navTarget = null;
  var panel = document.getElementById("nav-target-panel");
  if (panel) { panel.style.opacity = "0"; panel.style.pointerEvents = "none"; }
}

function onNavPopupClick(e) {
  var canvas = document.getElementById("nav-popup-canvas");
  if (!canvas) return;
  var rect = canvas.getBoundingClientRect();
  var cx = rect.width / 2, cy = rect.height / 2, scale = 40;
  navTarget = { x: parseFloat(((e.clientX - rect.left - cx) / scale).toFixed(2)), y: parseFloat((-(e.clientY - rect.top - cy) / scale).toFixed(2)) };
  document.getElementById("nav-target-x").textContent = navTarget.x.toFixed(2);
  document.getElementById("nav-target-y").textContent = navTarget.y.toFixed(2);
  var panel = document.getElementById("nav-target-panel");
  if (panel) { panel.style.opacity = "1"; panel.style.pointerEvents = "auto"; }
  drawNavPopupMap();
}

function clearNavGoal() {
  navTarget = null;
  var panel = document.getElementById("nav-target-panel");
  if (panel) { panel.style.opacity = "0"; panel.style.pointerEvents = "none"; }
  drawNavPopupMap();
}

function sendNavGoal() {
  if (!navTarget) return;
  if (appWs && appWs.readyState === WebSocket.OPEN) {
    appWs.send(JSON.stringify({ type: "nav_goal", x: navTarget.x, y: navTarget.y }));
    var btn = document.querySelector("#nav-target-panel .btn-primary");
    if (btn) { var orig = btn.innerHTML; btn.innerHTML = "⚡ Objectif Envoyé !"; btn.style.backgroundColor = "var(--success)"; setTimeout(function () { btn.innerHTML = orig; btn.style.backgroundColor = ""; clearNavGoal(); }, 1500); }
  } else if (typeof showToast === "function") { showToast("Erreur", "Le robot est hors-ligne.", "error"); }
}

function drawNavPopupMap() {
  var canvas = document.getElementById("nav-popup-canvas");
  if (!canvas || !window._navPopupActive) return;
  var ctx = canvas.getContext("2d");
  var dpr = window.devicePixelRatio || 1;
  var rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr; canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);
  var w = rect.width, h = rect.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#07070a"; ctx.fillRect(0, 0, w, h);
  var scale = 40, cx = w / 2, cy = h / 2;
  ctx.strokeStyle = "#101015"; ctx.lineWidth = 0.5;
  for (var x = cx % (scale * 0.5); x < w; x += scale * 0.5) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
  for (var y = cy % (scale * 0.5); y < h; y += scale * 0.5) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
  ctx.fillStyle = "rgba(255,255,255,0.05)";
  [{ x: -1.5, y: -2, w: 3, h: 0.1 }, { x: -1.5, y: 2, w: 3, h: 0.1 }, { x: -1.5, y: -2, w: 0.1, h: 4 }, { x: 1.5, y: -2, w: 0.1, h: 4 }, { x: 0.5, y: -0.5, w: 0.5, h: 1 }].forEach(function (wl) { ctx.fillRect(cx + wl.x * scale, cy - (wl.y + wl.h) * scale, wl.w * scale, wl.h * scale); });
  ctx.fillStyle = "#10b981";
  if (window.slamPoints && window.slamPoints.length) { window.slamPoints.forEach(function (pt) { ctx.beginPath(); ctx.arc(cx + pt.x * scale, cy - pt.y * scale, 1.5, 0, Math.PI * 2); ctx.fill(); }); }
  else { for (var a = 0; a < Math.PI * 2; a += 0.05) { var d = 1.8 + Math.sin(a * 4) * 0.1; ctx.beginPath(); ctx.arc(cx + Math.cos(a) * d * scale, cy - Math.sin(a) * d * scale, 1.5, 0, Math.PI * 2); ctx.fill(); } }
  if (window.slamPath && window.slamPath.length) { ctx.strokeStyle = "rgba(99,102,241,0.6)"; ctx

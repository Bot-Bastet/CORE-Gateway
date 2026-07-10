// === Telecommande & Navigation ===
// SpotMicro 3D viewer + posture sliders + demo mode + popup navigation
// State is server-synced via WS (no cache/localStorage)

var controlSpeed = 0.15;
var controlActiveDir = null;
var controlWalkInterval = null;
var navPointA = null;
var navPointB = null;
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
  if (labelEl) { var units = (key === "height" || key === "speed") ? "%" : "deg"; labelEl.textContent = value + units; }
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
    if (label) { var units = (key === "height" || key === "speed") ? "%" : "deg"; label.textContent = robotPosture[key] + units; }
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
  navPointA = null; navPointB = null;
  var hint = document.getElementById("nav-click-hint");
  if (hint) hint.textContent = "Cliquez pour definir le point A";
  var secB = document.getElementById("nav-section-b");
  if (secB) secB.style.display = "none";
  drawNavPopupMap();
}

function closeNavPopup() {
  var overlay = document.getElementById("nav-popup-overlay");
  if (overlay) overlay.style.display = "none";
  window._navPopupActive = false;
  navPointA = null; navPointB = null;
  var panel = document.getElementById("nav-target-panel");
  if (panel) { panel.style.opacity = "0"; panel.style.pointerEvents = "none"; }
}

function onNavPopupClick(e) {
  var canvas = document.getElementById("nav-popup-canvas");
  if (!canvas) return;
  var rect = canvas.getBoundingClientRect();
  var cx = rect.width / 2, cy = rect.height / 2, scale = 40;
  var pt = { x: parseFloat(((e.clientX - rect.left - cx) / scale).toFixed(2)), y: parseFloat((-(e.clientY - rect.top - cy) / scale).toFixed(2)) };

  if (!navPointA) { navPointA = pt; navPointB = null; }
  else if (!navPointB) { navPointB = pt; }
  else { navPointA = pt; navPointB = null; }

  var elAX = document.getElementById("nav-target-ax");
  var elAY = document.getElementById("nav-target-ay");
  var elBX = document.getElementById("nav-target-bx");
  var elBY = document.getElementById("nav-target-by");
  var elSecB = document.getElementById("nav-section-b");
  if (elAX && navPointA) { elAX.textContent = navPointA.x.toFixed(2); elAY.textContent = navPointA.y.toFixed(2); }
  if (elBX && navPointB) { elBX.textContent = navPointB.x.toFixed(2); elBY.textContent = navPointB.y.toFixed(2); }
  if (elSecB) elSecB.style.display = navPointB ? "" : "none";

  var panel = document.getElementById("nav-target-panel");
  if (panel) { panel.style.opacity = "1"; panel.style.pointerEvents = "auto"; }

  var btn = document.querySelector("#nav-target-panel .btn-primary");
  if (btn) btn.innerHTML = navPointB ? "De A vers B" : "Aller a ce point";

  var hint = document.getElementById("nav-click-hint");
  if (hint) {
    if (!navPointA) hint.textContent = "Cliquez pour definir le point A";
    else if (!navPointB) hint.textContent = "Cliquez pour definir le point B";
    else hint.textContent = "Cliquez pour recommencer (A vers B defini)";
  }
  drawNavPopupMap();
}

function clearNavGoal() {
  navPointA = null; navPointB = null;
  var panel = document.getElementById("nav-target-panel");
  if (panel) { panel.style.opacity = "0"; panel.style.pointerEvents = "none"; }
  var hint = document.getElementById("nav-click-hint");
  if (hint) hint.textContent = "Cliquez pour definir le point A";
  drawNavPopupMap();
}

function sendNavGoal() {
  if (!navPointA) return;
  if (appWs && appWs.readyState === WebSocket.OPEN) {
    var payload = { type: navPointB ? "nav_path" : "nav_goal" };
    if (navPointB) { payload.points = [navPointA, navPointB]; }
    else { payload.x = navPointA.x; payload.y = navPointA.y; }
    appWs.send(JSON.stringify(payload));
    var btn = document.querySelector("#nav-target-panel .btn-primary");
    if (btn) { var orig = btn.innerHTML; btn.innerHTML = "Envoye !"; btn.style.backgroundColor = "var(--success)"; setTimeout(function () { btn.innerHTML = orig; btn.style.backgroundColor = ""; clearNavGoal(); }, 1500); }
  } else if (typeof showToast === "function") { showToast("Erreur", "Le robot est hors-ligne.", "error"); }
}

function drawNavPopupMap() {
  var canvas = document.getElementById("nav-popup-canvas");
  if (!canvas || !window._navPopupActive) 

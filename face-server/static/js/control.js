// === Telecommande & Navigation ===
// SpotMicro 3D viewer + posture sliders + demo mode + popup navigation
// State is server-synced via WS (no cache/localStorage)
// NOTE: no var/let — dashboard.js already declares these with let at script scope

controlSpeed = 0.15;
controlActiveDir = null;
controlWalkInterval = null;
navPointA = null;
navPointB = null;
robotPosture = { height: 100.0, speed: 10.0, roll: 0.0, pitch: 0.0, yaw: 0.0, demo_mode: false, powered: false };

// LLM auto-execute: disabled by default
window.llmAutoControl = false;

// Use window.keysPressed to avoid let/var conflict with dashboard.js
window.keysPressed = window.keysPressed || {};

// Dernières positions servos reçues en live depuis le robot (mis à jour par ws.js)
window.latestServoAngles = window.latestServoAngles || null;

// Debounce badge pour éviter le clignotement 1ms lors du chargement
window._badgeUpdateTimer = null;
function _setBadge(powered, demo_mode) {
  if (window._badgeUpdateTimer) clearTimeout(window._badgeUpdateTimer);
  window._badgeUpdateTimer = setTimeout(function() {
    var pBadge = document.getElementById('robot-power-badge');
    var mBadge = document.getElementById('robot-mode-badge');
    
    if (pBadge) {
      if (powered) {
        pBadge.textContent = 'ALLUMÉ';
        pBadge.style.background = 'rgba(16,185,129,0.15)';
        pBadge.style.color = 'var(--success)';
      } else {
        pBadge.textContent = 'ÉTEINT';
        pBadge.style.background = 'rgba(100,100,100,0.15)';
        pBadge.style.color = '#888';
      }
    }
    
    if (mBadge) {
      if (demo_mode) {
        mBadge.textContent = 'SIMULATION';
        mBadge.style.background = 'rgba(217,70,239,0.15)';
        mBadge.style.color = 'var(--accent)';
      } else {
        mBadge.textContent = 'RÉEL';
        mBadge.style.background = 'rgba(59,130,246,0.15)';
        mBadge.style.color = '#3b82f6';
      }
    }
  }, 80);
}

function initControlTab() {
  // Neutralize the old drawControlMap that might be running
  if (typeof drawControlMap === "function") { window._oldDrawControlMap = drawControlMap; drawControlMap = function(){}; }
  
  if (!window._spot3dInitDone && typeof initSpotMicro3D === "function") {
    initSpotMicro3D("spotmicro-3d-container");
    window._spot3dInitDone = true;
  } else if (!window._spot3dInitDone) {
    // Fallback: 3D not available, show message
    var c = document.getElementById("spotmicro-3d-container");
    if (c && !c.querySelector(".fallback-msg")) {
      c.innerHTML = '<div class="fallback-msg" style="display:flex;align-items:center;justify-content:center;height:100%;color:#888;font-family:monospace;text-align:center;flex-direction:column;gap:0.5rem"><div style="font-size:3rem">🤖</div><div>SpotMicro 3D indisponible</div><div style="font-size:0.7rem;color:#555">Verifiez la connexion Internet (Three.js CDN)</div></div>';
    }
  }
  if (!window.controlKeyboardInitialized) {
    window.controlKeyboardInitialized = true;
    window.addEventListener("keydown", function (e) {
      if (activeTab !== "control") return;
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
      var keyMap = { "z": "up", "KeyW": "up", "ArrowUp": "up", "s": "down", "KeyS": "down", "ArrowDown": "down", "q": "strafe-left", "d": "strafe-right", "KeyA": "turn-left", "ArrowLeft": "turn-left", "KeyD": "strafe-right", "ArrowRight": "strafe-right", "a": "turn-left", "A": "turn-left", "e": "turn-right", "E": "turn-right", "KeyE": "turn-right" };
      var dir = keyMap[e.key] || keyMap[e.code];
      if (dir && !window.keysPressed[dir]) { e.preventDefault(); window.keysPressed[dir] = true; startWalking(dir); }
      if (e.key === " " || e.key === "x" || e.key === "Escape") { e.preventDefault(); sendControlStop(); }
    });
    window.addEventListener("keyup", function (e) {
      if (activeTab !== "control") return;
      var keyMap = { "z": "up", "KeyW": "up", "ArrowUp": "up", "s": "down", "KeyS": "down", "ArrowDown": "down", "q": "strafe-left", "d": "strafe-right", "KeyA": "turn-left", "ArrowLeft": "turn-left", "KeyD": "strafe-right", "ArrowRight": "strafe-right", "a": "turn-left", "A": "turn-left", "e": "turn-right", "E": "turn-right", "KeyE": "turn-right" };
      var dir = keyMap[e.key] || keyMap[e.code];
      if (dir) { window.keysPressed[dir] = false; if (!Object.values(window.keysPressed).includes(true)) stopWalking(); }
    });
  }
}

// ─── D-Pad Walking ────────────────────────────────────────────────────────
// Speed is now controlled via Posture & Allure slider (posture-slider-speed)

function sendControlCmd(cmd) {
  if (appWs && appWs.readyState === WebSocket.OPEN) {
    // Envoyer la commande arduino directement
    appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: cmd }));
    // Envoyer aussi via robot_posture pour que le système de mouvement ROS en soit informé
    if (cmd === 'stand' || cmd === 'sit') {
      appWs.send(JSON.stringify({ type: 'robot_posture', posture: cmd }));
    }
    if (typeof showToast === 'function') {
      var labels = { stand: 'Se lever', sit: "S'asseoir", stop: 'Stop' };
      showToast('Télécommande', labels[cmd] || cmd + ' envoyé', 'info');
    }
  } else {
    if (typeof showToast === 'function') {
      showToast('Erreur', 'WebSocket non connecte. Le robot est peut-etre hors ligne.', 'error');
    }
  }
}

function sendControlStop() {
  stopWalking();
  window.keysPressed = {};
  // Stop the 3D viewer animation
  if (typeof stopSpotMicroMovement === "function") stopSpotMicroMovement();
  if (appWs && appWs.readyState === WebSocket.OPEN) {
    appWs.send(JSON.stringify({ type: "cmd_vel", linear: 0.0, lateral: 0.0, angular: 0.0 }));
    appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stop" }));
    // Also publish power-off to gateway so all clients sync
    appWs.send(JSON.stringify({ type: "robot_posture_update", key: "powered", value: false }));
  }
  document.querySelectorAll(".dpad-btn").forEach(function (btn) {
    btn.style.backgroundColor = "";
    btn.style.color = "";
  });
  var stopBtn = document.getElementById("dpad-stop");
  if (stopBtn) stopBtn.style.backgroundColor = "rgba(239, 68, 68, 0.2)";
}

function startWalking(dir) {
  if (controlActiveDir === dir) return;
  controlActiveDir = dir;

  document.querySelectorAll(".dpad-btn").forEach(function (btn) {
    btn.style.backgroundColor = "";
    btn.style.color = "";
  });
  var activeBtn = document.getElementById("dpad-" + dir);
  if (activeBtn) {
    activeBtn.style.backgroundColor = "var(--accent)";
    activeBtn.style.color = "white";
  }

  if (controlWalkInterval) clearInterval(controlWalkInterval);

  function sendVel() {
    if (!appWs || appWs.readyState !== WebSocket.OPEN) return;
    var vx = 0.0, vy = 0.0, wz = 0.0;
    if (dir === "up") vx = controlSpeed;
    else if (dir === "down") vx = -controlSpeed;
    else if (dir === "strafe-left") vy = -controlSpeed;
    else if (dir === "strafe-right") vy = controlSpeed;
    else if (dir === "turn-left") wz = 1.0;
    else if (dir === "turn-right") wz = -1.0;
    // Legacy: left/right keyboard still rotate
    else if (dir === "left") wz = 1.0;
    else if (dir === "right") wz = -1.0;
    appWs.send(JSON.stringify({ type: "cmd_vel", linear: vx, lateral: vy, angular: wz }));
  }

  sendVel();
  controlWalkInterval = setInterval(sendVel, 100);
}

function stopWalking() {
  if (controlWalkInterval) { clearInterval(controlWalkInterval); controlWalkInterval = null; }
  controlActiveDir = null;
  document.querySelectorAll(".dpad-btn").forEach(function (btn) {
    btn.style.backgroundColor = "";
    btn.style.color = "";
  });
  var stopBtn = document.getElementById("dpad-stop");
  if (stopBtn) stopBtn.style.backgroundColor = "rgba(239, 68, 68, 0.1)";
  if (appWs && appWs.readyState === WebSocket.OPEN) {
    appWs.send(JSON.stringify({ type: "cmd_vel", linear: 0.0, lateral: 0.0, angular: 0.0 }));
  }
}

// ─── Posture Sliders ──────────────────────────────────────────────────────

function onPostureSliderChange(key, value) {
  robotPosture[key] = parseFloat(value);
  var labelEl = document.getElementById("posture-val-" + key);
  if (labelEl) {
    // Affichage correct des unités et multiplicateurs
    if (key === 'speed') {
      labelEl.textContent = (parseFloat(value) / 10).toFixed(1) + 'x';
    } else if (key === 'height') {
      labelEl.textContent = value + '%';
    } else {
      labelEl.textContent = value + '°';
    }
  }
  if (typeof updateSpotMicroPosture === "function") updateSpotMicroPosture(robotPosture);
  debouncePostureSync(key, value);
}

window._postureSyncTimers = window._postureSyncTimers || {};
function debouncePostureSync(key, value) {
  if (window._postureSyncTimers[key]) clearTimeout(window._postureSyncTimers[key]);
  window._postureSyncTimers[key] = setTimeout(function () {
    if (appWs && appWs.readyState === WebSocket.OPEN) appWs.send(JSON.stringify({ type: "robot_posture_update", key: key, value: value }));
  }, 100);
}

function toggleDemoMode(checked) {
  robotPosture.demo_mode = checked;
  if (typeof setSpotMicroDemoMode === 'function') setSpotMicroDemoMode(checked);
  if (appWs && appWs.readyState === WebSocket.OPEN)
    appWs.send(JSON.stringify({ type: 'demo_mode', enabled: checked }));

  if (checked) {
    // Passage en SIMULATION : reset la posture locale
    robotPosture.height = 100.0;
    robotPosture.roll = 0.0;
    robotPosture.pitch = 0.0;
    robotPosture.yaw = 0.0;
    robotPosture.speed = 10.0;

    // Reset le viewer 3D à la position neutre (stand)
    if (typeof window.resetSpotMicro3D === 'function') {
      window.resetSpotMicro3D();
    }
  } else {
    // Passage en PHYSIQUE (RÉEL) : appliquer immédiatement les angles servo réels s'ils sont dispo
    if (window.latestServoAngles && typeof window.updateSpotMicroServos === 'function') {
      window.updateSpotMicroServos(window.latestServoAngles);
    }
  }

  _setBadge(robotPosture.powered, checked);
}

function toggleRobotPower() {
  robotPosture.powered = !robotPosture.powered;
  var btn = document.getElementById('power-toggle-btn');
  if (btn) {
    btn.textContent = robotPosture.powered ? '⏻ Éteindre' : '⏻ Allumer';
    btn.style.background = robotPosture.powered ? 'rgba(239,68,68,0.15)' : 'rgba(16,185,129,0.15)';
    btn.style.color = robotPosture.powered ? 'var(--danger)' : 'var(--success)';
  }
  if (typeof updateSpotMicroPosture === 'function') updateSpotMicroPosture(robotPosture);
  if (appWs && appWs.readyState === WebSocket.OPEN) {
    appWs.send(JSON.stringify({ type: 'robot_posture_update', key: 'powered', value: robotPosture.powered }));
    if (!robotPosture.powered) {
      stopWalking();
      appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'stop' }));
    } else {
      // Power on : sit (position sécurisée)
      appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'sit' }));
      appWs.send(JSON.stringify({ type: 'robot_posture', posture: 'sit' }));
    }
  }
  _setBadge(robotPosture.powered, robotPosture.demo_mode);
}

function toggleLLMControl() {
  window.llmAutoControl = !window.llmAutoControl;
  var btn = document.getElementById("llm-control-toggle-btn");
  if (btn) { btn.textContent = window.llmAutoControl ? "🧠 IA: ON" : "🧠 IA: OFF"; btn.style.background = window.llmAutoControl ? "rgba(99,102,241,0.2)" : "rgba(100,100,100,0.15)"; btn.style.color = window.llmAutoControl ? "var(--accent)" : "#888"; }
}

function applyRobotPostureSync(postureData) {
  Object.keys(postureData).forEach(function(key) { if (robotPosture.hasOwnProperty(key)) robotPosture[key] = postureData[key]; });
  ['height', 'speed', 'roll', 'pitch', 'yaw'].forEach(function(key) {
    var slider = document.getElementById('posture-slider-' + key);
    var label  = document.getElementById('posture-val-' + key);
    if (slider) slider.value = robotPosture[key];
    if (label) {
      if (key === 'speed') {
        label.textContent = (parseFloat(robotPosture[key]) / 10).toFixed(1) + 'x';
      } else if (key === 'height') {
        label.textContent = robotPosture[key] + '%';
      } else {
        label.textContent = robotPosture[key] + '°';
      }
    }
  });
  var demoCheck = document.getElementById('demo-mode-checkbox');
  if (demoCheck) demoCheck.checked = robotPosture.demo_mode;
  var powerBtn = document.getElementById('power-toggle-btn');
  if (powerBtn) {
    powerBtn.textContent = robotPosture.powered ? '⏻ Éteindre' : '⏻ Allumer';
    powerBtn.style.background = robotPosture.powered ? 'rgba(239,68,68,0.15)' : 'rgba(16,185,129,0.15)';
    powerBtn.style.color = robotPosture.powered ? 'var(--danger)' : 'var(--success)';
  }
  if (typeof updateSpotMicroPosture === 'function') updateSpotMicroPosture(robotPosture);
  if (typeof setSpotMicroDemoMode === 'function') setSpotMicroDemoMode(robotPosture.demo_mode);
  _setBadge(robotPosture.powered, robotPosture.demo_mode);
}

// ─── Navigation Popup (A→B) ───────────────────────────────────────────────

function openNavPopup() {
  var overlay = document.getElementById("nav-popup-overlay");
  if (!overlay) return;
  overlay.style.display = "flex";
  overlay.classList.add("active");
  window._navPopupActive = true;
  navPointA = null; navPointB = null;
  var hint = document.getElementById("nav-click-hint");
  if (hint) hint.textContent = "Cliquez pour definir le point A";
  var secB = document.getElementById("nav-section-b");
  if (secB) secB.style.display = "none";
  var panel = document.getElementById("nav-target-panel");
  if (panel) { panel.style.opacity = "0"; panel.style.pointerEvents = "none"; }
  drawNavPopupMap();
}

function closeNavPopup() {
  var overlay = document.getElementById("nav-popup-overlay");
  if (overlay) {
    overlay.classList.remove("active");
    overlay.style.display = "none";
  }
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
  var secB = document.getElementById("nav-section-b");
  if (secB) secB.style.display = "none";
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
  var canvas = document.getElementById('nav-popup-canvas');
  if (!canvas || !window._navPopupActive) return;
  var ctx = canvas.getContext('2d');
  var w = canvas.width, h = canvas.height;
  var cx = w / 2, cy = h / 2;

  // Calcul adaptatif du scale : faire tenir toute la trajectoire SLAM dans le canvas
  // avec une marge de 20%. Scale min=20, max=80 px/m.
  var scale = 40; // défaut
  if (window.slamPath && window.slamPath.length > 0) {
    var maxCoord = 0;
    window.slamPath.forEach(function(p) {
      var d = Math.max(Math.abs(p.x), Math.abs(p.y));
      if (d > maxCoord) maxCoord = d;
    });
    if (window.robotPose) {
      var dr = Math.max(Math.abs(window.robotPose.x), Math.abs(window.robotPose.y));
      if (dr > maxCoord) maxCoord = dr;
    }
    if (navPointA) { var da = Math.max(Math.abs(navPointA.x), Math.abs(navPointA.y)); if (da > maxCoord) maxCoord = da; }
    if (navPointB) { var db = Math.max(Math.abs(navPointB.x), Math.abs(navPointB.y)); if (db > maxCoord) maxCoord = db; }
    if (maxCoord > 0.1) {
      scale = Math.min(80, Math.max(20, Math.floor((Math.min(cx, cy) * 0.85) / maxCoord)));
    }
  }

  ctx.fillStyle = '#0f0f13';
  ctx.fillRect(0, 0, w, h);

  // Grille
  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  ctx.lineWidth = 0.5;
  var gridSize = scale;
  for (var gx = cx % gridSize; gx < w; gx += gridSize) { ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, h); ctx.stroke(); }
  for (var gy = cy % gridSize; gy < h; gy += gridSize) { ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(w, gy); ctx.stroke(); }

  // Axes
  ctx.strokeStyle = 'rgba(255,255,255,0.15)';
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(cx, 0); ctx.lineTo(cx, h); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(0, cy); ctx.lineTo(w, cy); ctx.stroke();

  // Origine
  ctx.strokeStyle = 'rgba(255,255,255,0.3)';
  ctx.beginPath(); ctx.arc(cx, cy, 4, 0, Math.PI * 2); ctx.stroke();

  // Légende scale
  ctx.fillStyle = 'rgba(255,255,255,0.3)';
  ctx.font = '10px monospace';
  ctx.textAlign = 'left';
  ctx.fillText(scale + ' px/m', 8, h - 8);

  // Trajectoire SLAM
  if (window.slamPath && window.slamPath.length > 1) {
    ctx.strokeStyle = 'rgba(16, 185, 129, 0.5)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    var firstPt = window.slamPath[0];
    ctx.moveTo(cx + firstPt.x * scale, cy - firstPt.y * scale);
    for (var i = 1; i < window.slamPath.length; i++) {
      ctx.lineTo(cx + window.slamPath[i].x * scale, cy - window.slamPath[i].y * scale);
    }
    ctx.stroke();
  }

  // Robot
  var rx = cx + (window.robotPose ? window.robotPose.x * scale : 0);
  var ry = cy - (window.robotPose ? window.robotPose.y * scale : 0);
  var rAngle = window.robotPose ? (window.robotPose.yaw || 0) : 0;

  ctx.fillStyle = '#f59e0b';
  ctx.strokeStyle = '#f59e0b';
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(rx, ry, 5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(rx, ry); ctx.lineTo(rx + Math.cos(rAngle) * 12, ry - Math.sin(rAngle) * 12); ctx.stroke();

  // Point A
  if (navPointA) {
    var ax = cx + navPointA.x * scale, ay = cy - navPointA.y * scale;
    ctx.fillStyle = '#10b981'; ctx.strokeStyle = '#10b981'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(ax, ay, 7, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(ax, ay, 7, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = '#fff'; ctx.font = 'bold 11px monospace'; ctx.textAlign = 'center';
    ctx.fillText('A', ax, ay - 12);
  }

  // Point B
  if (navPointB) {
    var ax2 = cx + navPointA.x * scale, ay2 = cy - navPointA.y * scale;
    var bx  = cx + navPointB.x * scale, by  = cy - navPointB.y * scale;
    ctx.strokeStyle = '#6366f1'; ctx.lineWidth = 2; ctx.setLineDash([6, 4]);
    ctx.beginPath(); ctx.moveTo(ax2, ay2); ctx.lineTo(bx, by); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#6366f1'; ctx.strokeStyle = '#6366f1'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(bx, by, 7, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(bx, by, 7, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = '#fff'; ctx.font = 'bold 11px monospace'; ctx.textAlign = 'center';
    ctx.fillText('B', bx, by - 12);
  }
}

  ctx.fillStyle = "#0f0f13";
  ctx.fillRect(0, 0, w, h);

  ctx.strokeStyle = "rgba(255,255,255,0.06)";
  ctx.lineWidth = 0.5;
  var gridSize = scale;
  for (var x = cx % gridSize; x < w; x += gridSize) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
  for (var y = cy % gridSize; y < h; y += gridSize) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }

  ctx.strokeStyle = "rgba(255,255,255,0.15)";
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(cx, 0); ctx.lineTo(cx, h); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(0, cy); ctx.lineTo(w, cy); ctx.stroke();

  ctx.strokeStyle = "rgba(255,255,255,0.3)";
  ctx.beginPath(); ctx.arc(cx, cy, 4, 0, Math.PI * 2); ctx.stroke();

  if (window.slamPath && window.slamPath.length > 1) {
    ctx.strokeStyle = "rgba(16, 185, 129, 0.5)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    var firstPt = window.slamPath[0];
    ctx.moveTo(cx + firstPt.x * scale, cy - firstPt.y * scale);
    for (var i = 1; i < window.slamPath.length; i++) {
      ctx.lineTo(cx + window.slamPath[i].x * scale, cy - window.slamPath[i].y * scale);
    }
    ctx.stroke();
  }

  var rx = cx + (window.robotPose ? window.robotPose.x * scale : 0);
  var ry = cy - (window.robotPose ? window.robotPose.y * scale : 0);
  var rAngle = window.robotPose ? (window.robotPose.yaw || 0) : 0;

  ctx.fillStyle = "#f59e0b";
  ctx.strokeStyle = "#f59e0b";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(rx, ry, 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(rx, ry);
  ctx.lineTo(rx + Math.cos(rAngle) * 12, ry - Math.sin(rAngle) * 12);
  ctx.stroke();

  if (navPointA) {
    var ax = cx + navPointA.x * scale, ay = cy - navPointA.y * scale;
    ctx.fillStyle = "#10b981";
    ctx.strokeStyle = "#10b981";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(ax, ay, 7, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(ax, ay, 7, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.font = "bold 11px monospace";
    ctx.textAlign = "center";
    ctx.fillText("A", ax, ay - 12);
  }

  if (navPointB) {
    var ax2 = cx + navPointA.x * scale, ay2 = cy - navPointA.y * scale;
    var bx = cx + navPointB.x * scale, by = cy - navPointB.y * scale;
    ctx.strokeStyle = "#6366f1";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.beginPath(); ctx.moveTo(ax2, ay2); ctx.lineTo(bx, by); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#6366f1";
    ctx.strokeStyle = "#6366f1";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(bx, by, 7, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(bx, by, 7, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.font = "bold 11px monospace";
    ctx.textAlign = "center";
    ctx.fillText("B", bx, by - 12);
  }
}

// ─── EXPLICIT WINDOW ASSIGNMENTS (guarantees onclick handlers find them) ──
window.initControlTab = initControlTab;
window.sendControlCmd = sendControlCmd;
window.sendControlStop = sendControlStop;
window.startWalking = startWalking;
window.stopWalking = stopWalking;
window.onPostureSliderChange = onPostureSliderChange;
window.debouncePostureSync = debouncePostureSync;
window.toggleDemoMode = toggleDemoMode;
window.toggleRobotPower = toggleRobotPower;
window.toggleLLMControl = toggleLLMControl;
window.applyRobotPostureSync = applyRobotPostureSync;
window.openNavPopup = openNavPopup;
window.closeNavPopup = closeNavPopup;
window.onNavPopupClick = onNavPopupClick;
window.clearNavGoal = clearNavGoal;
window.sendNavGoal = sendNavGoal;
window.drawNavPopupMap = drawNavPopupMap;
// Kill old drawControlMap
window.drawControlMap = function(){};
window.onControlMapClick = function(){};

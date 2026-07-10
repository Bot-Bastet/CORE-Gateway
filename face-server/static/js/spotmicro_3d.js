// === SpotMicro 3D Viewer — Bastet ===
// Based on original spotmicro_3d.html (STL meshes from chvmp/spotmicro_description)
// Public API: window.initSpotMicro3D, updateSpotMicroPosture, setSpotMicroDemoMode, etc.
(function () {
  "use strict";
  var PI = Math.PI, DEG = PI / 180;

  // ─── STL Files (from chvmp/spotmicro_description — CDN verified alive) ──
  var STL_BASE = "https://raw.githubusercontent.com/chvmp/spotmicro_description/master/meshes/stl/";
  var UNIQUE_STLS = [
    { id: "mainbody",   file: "mainbody.stl" },
    { id: "frontpart",  file: "frontpart.stl" },
    { id: "backpart",   file: "backpart.stl" },
    { id: "lshoulder",  file: "lshoulder.stl" },
    { id: "rshoulder",  file: "rshoulder.stl" },
    { id: "larm",       file: "larm.stl" },
    { id: "rarm",       file: "rarm.stl" },
    { id: "larm_cover", file: "larm_cover.stl" },
    { id: "rarm_cover", file: "rarm_cover.stl" },
    { id: "lfoot",      file: "lfoot.stl" },
    { id: "rfoot",      file: "rfoot.stl" },
    { id: "foot",       file: "foot.stl" }
  ];

  var LEGS = {
    fl: { left: true,  shiftx: 0.093,  shifty: 0.0395,  shift: 0.055,  sz: 1, sx: -1, label: "FL" },
    fr: { left: false, shiftx: 0.093,  shifty: -0.0395, shift: -0.055, sz: 1, sx: 1,  label: "FR" },
    rl: { left: true,  shiftx: -0.093, shifty: 0.0395,  shift: 0.055,  sz: -1, sx: -1, label: "RL" },
    rr: { left: false, shiftx: -0.093, shifty: -0.0395, shift: -0.055, sz: -1, sx: 1,  label: "RR" }
  };
  var TROT_PHASE = { fl: 0, rr: 0, fr: PI, rl: PI };
  var STAND_H = 0.205, LIE_H = 0.045, L1 = 0.109, L2 = 0.130;
  var GAIT_SPEED = 8;

  var scene, camera, renderer, controls, worldGrp, robotGrp, bodyGrp;
  var legData = {}, meshCache = {};
  var gaitT = 0;
  var cmd = "s";
  var posture = { height: 100, speed: 10, roll: 0, pitch: 0, yaw: 0, demo_mode: false, powered: false };
  var powerFrac = 0;
  var worldPos = { x: 0, z: 0, yaw: 0 };
  var keys = {};
  var cur = {}, tgt = {};
  var animFrameId, containerEl, lastTime = 0;

  Object.keys(LEGS).forEach(function (id) {
    ["s", "t", "c"].forEach(function (j) { cur[id + "_" + j] = 0; tgt[id + "_" + j] = 0; });
  });

  // ─── Motor position display ──────────────────────────────────────
  var MOTOR_J = [
    { id: "fl_s", label: "FL Coxa" },{ id: "fl_t", label: "FL Thigh" },{ id: "fl_c", label: "FL Calf" },
    { id: "fr_s", label: "FR Coxa" },{ id: "fr_t", label: "FR Thigh" },{ id: "fr_c", label: "FR Calf" },
    { id: "rl_s", label: "RL Coxa" },{ id: "rl_t", label: "RL Thigh" },{ id: "rl_c", label: "RL Calf" },
    { id: "rr_s", label: "RR Coxa" },{ id: "rr_t", label: "RR Thigh" },{ id: "rr_c", label: "RR Calf" }
  ];
  function refreshMotorDisplay() {
    var r2d = 180 / PI;
    MOTOR_J.forEach(function (j) {
      var el = document.getElementById("motor-" + j.id);
      if (el) el.textContent = (cur[j.id] * r2d).toFixed(1) + "\u00B0";
    });
  }

  // ─── IK Solver (from original) ──────────────────────────────────
  function solveLegIK(isLeft, D) {
    var dx = D.x, dy = D.y, dz = D.z;
    var ld = isLeft ? 0.055 : -0.055;
    var r_lat = Math.sqrt(dy * dy + dz * dz);
    var s = Math.atan2(dy, dz) - Math.atan2(ld, Math.sqrt(Math.max(0, r_lat * r_lat - ld * ld)));
    var z_plane = -Math.sqrt(Math.max(0.001, r_lat * r_lat - ld * ld));
    var D2_raw = Math.sqrt(dx * dx + z_plane * z_plane);
    var D2_min = Math.abs(L1 - L2) + 0.001;
    var D2_max = L1 + L2 - 0.001;
    var D2 = Math.max(D2_min, Math.min(D2_max, D2_raw));
    var scale = D2_raw > 0.001 ? D2 / D2_raw : 1;
    var dx_c = dx * scale, z_c = z_plane * scale;
    var cos_c = (L1 * L1 + L2 * L2 - D2 * D2) / (2 * L1 * L2);
    var c_inner = Math.acos(Math.max(-1, Math.min(1, cos_c)));
    var c = -(PI - c_inner);
    var cos_a = (L1 * L1 + D2 * D2 - L2 * L2) / (2 * L1 * D2);
    var alpha = Math.acos(Math.max(-1, Math.min(1, cos_a)));
    var gamma = Math.atan2(dx_c, -z_c);
    var t = gamma + alpha;
    return { s: s, t: t, c: c };
  }

  // ─── Gait Engine (from original) ─────────────────────────────────
  function computeTargets(dt) {
    var moving = (cmd !== "s") && posture.powered;
    if (moving) gaitT += dt * GAIT_SPEED * (posture.speed / 10);

    var H = STAND_H * (posture.height / 100);
    var roll = posture.roll * DEG, pitch = posture.pitch * DEG, yawB = posture.yaw * DEG;

    Object.keys(LEGS).forEach(function (id) {
      var d = LEGS[id], isLeft = d.left, ph = TROT_PHASE[id];

      if (!posture.powered) {
        tgt[id + "_s"] = 0;
        tgt[id + "_t"] = 1.25;
        tgt[id + "_c"] = -2.59;
        return;
      }

      var foot_world_x = d.shiftx;
      var foot_world_y = d.shifty + (isLeft ? 0.04 : -0.04);
      var foot_world_z = 0.0;

      var gait_dx = 0, gait_dy = 0, gait_dz = 0;
      if (moving) {
        var wave = Math.sin(gaitT + ph), wave2 = Math.sin(gaitT + ph + PI * 0.5);
        gait_dz = Math.max(0, 0.04 * wave2);
        if (cmd === "fw" || cmd === "bk") { gait_dx = (cmd === "bk" ? -1 : 1) * 0.05 * wave; }
        else if (cmd === "sl" || cmd === "sr") { gait_dy = (cmd === "sl" ? -1 : 1) * 0.04 * wave; }
        else if (cmd === "tl" || cmd === "tr") {
          var tdir = cmd === "tl" ? -1 : 1;
          var fwd = tdir * (isLeft ? 1 : -1);
          gait_dx = fwd * 0.045 * wave;
          gait_dy = tdir * (isLeft ? -1 : 1) * 0.04 * wave;
        }
      }

      var cP = Math.cos(pitch), sP = Math.sin(pitch);
      var cR = Math.cos(roll), sR = Math.sin(roll);
      var cY = Math.cos(yawB), sY = Math.sin(yawB);
      var hx_local = d.shiftx, hy_local = d.shifty, hz_local = 0;
      var hx_rot = cY * cP * hx_local + (-sY * cR + cY * sP * sR) * hy_local + (sY * sR + cY * sP * cR) * hz_local;
      var hy_rot = sY * cP * hx_local + (cY * cR + sY * sP * sR) * hy_local + (-cY * sR + sY * sP * cR) * hz_local;
      var hz_rot = -sP * hx_local + cP * sR * hy_local + cP * cR * hz_local;
      var hip_x = hx_rot, hip_y = hy_rot, hip_z = H + hz_rot;

      var w_dx = (foot_world_x + gait_dx) - hip_x;
      var w_dy = (foot_world_y + gait_dy) - hip_y;
      var w_dz = (foot_world_z + gait_dz) - hip_z;

      var nR = -roll, nP = -pitch, nY = -yawB;
      var cnY = Math.cos(nY), snY = Math.sin(nY);
      var cnP = Math.cos(nP), snP = Math.sin(nP);
      var cnR = Math.cos(nR), snR = Math.sin(nR);
      var ux = cnY * w_dx - snY * w_dy, uy = snY * w_dx + cnY * w_dy, uz = w_dz;
      var px = cnP * ux + snP * uz, py = uy, pz = -snP * ux + cnP * uz;
      var lx = px, ly = cnR * py - snR * pz, lz = snR * py + cnR * pz;

      var sol = solveLegIK(isLeft, { x: lx, y: ly, z: -lz });
      tgt[id + "_s"] = sol.s;
      tgt[id + "_t"] = sol.t;
      tgt[id + "_c"] = sol.c;
    });
  }

  // ─── STL Loader ──────────────────────────────────────────────────
  function loadAllMeshes(onComplete) {
    var loader = new THREE.STLLoader();
    var loadedCount = 0, total = UNIQUE_STLS.length;

    function updateProgress(pct, text) {
      var bar = document.getElementById("spot3d-progress-bar");
      var txt = document.getElementById("spot3d-progress-text");
      var pctEl = document.getElementById("spot3d-progress-pct");
      if (bar) bar.style.width = pct + "%";
      if (pctEl) pctEl.textContent = pct + "%";
      if (txt) txt.textContent = text;
    }

    UNIQUE_STLS.forEach(function (item) {
      loader.load(STL_BASE + item.file, function (geometry) {
        meshCache[item.id] = geometry;
        loadedCount++;
        var pct = Math.round(loadedCount / total * 100);
        updateProgress(pct, "Chargement STL... " + pct + "%");
        if (loadedCount === total) {
          setTimeout(function () {
            var ov = document.getElementById("spot3d-loading");
            if (ov) { ov.style.opacity = "0"; setTimeout(function () { if (ov.parentNode) ov.parentNode.removeChild(ov); }, 500); }
            onComplete();
          }, 400);
        }
      }, undefined, function () {
        meshCache[item.id] = new THREE.BufferGeometry();
        loadedCount++;
        if (loadedCount === total) {
          var ov = document.getElementById("spot3d-loading");
          if (ov) { ov.style.opacity = "0"; setTimeout(function () { if (ov.parentNode) ov.parentNode.removeChild(ov); }, 500); }
          onComplete();
        }
      });
    });
  }

  // ─── Build Robot from STL Meshes (from original) ─────────────────
  var matGold = new THREE.MeshStandardMaterial({ color: 0xead400, roughness: 0.38, metalness: 0.45 });
  var matDark = new THREE.MeshStandardMaterial({ color: 0x1b1b1f, roughness: 0.55, metalness: 0.25 });
  var matFoot = new THREE.MeshStandardMaterial({ color: 0x4a4a52, roughness: 0.70, metalness: 0.05 });

  function buildRobotFromCache() {
    // Body
    var mainbody = new THREE.Mesh(meshCache.mainbody, matDark);
    mainbody.position.set(0.0425, 0.055, -0.02);
    mainbody.rotation.set(0, 0, PI, "XYZ");
    mainbody.scale.setScalar(0.001);
    mainbody.castShadow = mainbody.receiveShadow = true;
    bodyGrp.add(mainbody);

    var backpart = new THREE.Mesh(meshCache.backpart, matGold);
    backpart.position.set(0.042, 0.055, -0.0195);
    backpart.rotation.set(0, 0, PI, "XYZ");
    backpart.scale.setScalar(0.001);
    backpart.castShadow = backpart.receiveShadow = true;
    bodyGrp.add(backpart);

    var frontpart = new THREE.Mesh(meshCache.frontpart, matGold);
    frontpart.position.set(0.042, 0.055, -0.0205);
    frontpart.rotation.set(0, 0, PI, "XYZ");
    frontpart.scale.setScalar(0.001);
    frontpart.castShadow = frontpart.receiveShadow = true;
    bodyGrp.add(frontpart);

    // Legs
    Object.keys(LEGS).forEach(function (id) {
      var d = LEGS[id], isLeft = d.left;

      var shoulderGrp = new THREE.Group();
      shoulderGrp.position.set(d.shiftx, d.shifty, 0);
      bodyGrp.add(shoulderGrp);

      var shGeom = meshCache[isLeft ? "lshoulder" : "rshoulder"];
      var shoulderMesh = new THREE.Mesh(shGeom, matDark);
      if (isLeft) {
        shoulderMesh.position.set(0.135, 0.0163, -0.0195);
      } else {
        shoulderMesh.position.set(0.135, 0.0938, -0.0205);
      }
      shoulderMesh.rotation.set(0, 0, PI, "XYZ");
      shoulderMesh.scale.setScalar(0.001);
      shoulderMesh.castShadow = shoulderMesh.receiveShadow = true;
      shoulderGrp.add(shoulderMesh);

      var legGrp = new THREE.Group();
      legGrp.position.set(0, d.shift, 0);
      shoulderGrp.add(legGrp);

      var armGeom = meshCache[isLeft ? "larm" : "rarm"];
      var armMesh = new THREE.Mesh(armGeom, matDark);
      var coverGeom = meshCache[isLeft ? "larm_cover" : "rarm_cover"];
      var coverMesh = new THREE.Mesh(coverGeom, matGold);
      var visualPos = isLeft ? new THREE.Vector3(0.134, -0.04, -0.0095) : new THREE.Vector3(0.134, 0.149, -0.0095);
      armMesh.position.copy(visualPos);
      armMesh.rotation.set(0, 0, PI, "XYZ");
      armMesh.scale.setScalar(0.001);
      armMesh.castShadow = armMesh.receiveShadow = true;
      coverMesh.position.copy(visualPos);
      coverMesh.rotation.set(0, 0, PI, "XYZ");
      coverMesh.scale.setScalar(0.001);
      coverMesh.castShadow = coverMesh.receiveShadow = true;
      legGrp.add(armMesh);
      legGrp.add(coverMesh);

      var footGrp = new THREE.Group();
      footGrp.position.set(0.014, 0, -0.109);
      legGrp.add(footGrp);

      var footGeom = meshCache[isLeft ? "lfoot" : "rfoot"];
      var footMesh = new THREE.Mesh(footGeom, matDark);
      if (isLeft) {
        footMesh.position.set(0.1195, -0.04, 0.099);
      } else {
        footMesh.position.set(0.1195, 0.149, 0.099);
      }
      footMesh.rotation.set(0, 0, PI, "XYZ");
      footMesh.scale.setScalar(0.001);
      footMesh.castShadow = footMesh.receiveShadow = true;
      footGrp.add(footMesh);

      var toeGrp = new THREE.Group();
      toeGrp.position.set(0, 0, -0.130);
      footGrp.add(toeGrp);

      var toeGeom = meshCache.foot;
      var toeMesh = new THREE.Mesh(toeGeom, matFoot);
      toeMesh.position.set(0, -0.01, 0.015);
      toeMesh.rotation.set(0, 0.40010, 0, "XYZ");
      toeMesh.scale.setScalar(0.001);
      toeMesh.castShadow = toeMesh.receiveShadow = true;
      toeGrp.add(toeMesh);

      legData[id] = { shoulderGrp: shoulderGrp, legGrp: legGrp, footGrp: footGrp };
    });
  }

  // ─── Animation (from original) ───────────────────────────────────
  function animate(timestamp) {
    animFrameId = requestAnimationFrame(animate);
    var dt = (timestamp - lastTime) / 1000;
    if (dt > 0.05) dt = 0.05;
    lastTime = timestamp;

    var tgtPower = posture.powered ? 1 : 0;
    powerFrac += (tgtPower - powerFrac) * Math.min(1, dt * 3.2);

    if (posture.powered && cmd !== "s") {
      var v = 0.15 * (posture.speed / 10);
      if (cmd === "fw") { worldPos.x += Math.cos(worldPos.yaw) * v * dt; worldPos.z += Math.sin(worldPos.yaw) * v * dt; }
      else if (cmd === "bk") { worldPos.x -= Math.cos(worldPos.yaw) * v * dt; worldPos.z -= Math.sin(worldPos.yaw) * v * dt; }
      else if (cmd === "tl") { worldPos.yaw -= 0.8 * (posture.speed / 10) * dt; }
      else if (cmd === "tr") { worldPos.yaw += 0.8 * (posture.speed / 10) * dt; }
    }

    worldGrp.position.x = worldPos.x;
    worldGrp.position.z = -worldPos.z;
    worldGrp.rotation.y = worldPos.yaw;

    computeTargets(dt);

    var rate = Math.min(1, dt * 10);
    Object.keys(cur).forEach(function (k) { cur[k] += (tgt[k] - cur[k]) * rate; });

    Object.keys(legData).forEach(function (id) {
      var leg = legData[id];
      leg.shoulderGrp.rotation.x = cur[id + "_s"];
      leg.legGrp.rotation.y = cur[id + "_t"];
      leg.footGrp.rotation.y = cur[id + "_c"];
    });

    var standH = STAND_H * (posture.height / 100);
    var currentH = LIE_H + (standH - LIE_H) * powerFrac;
    bodyGrp.position.z = currentH;
    bodyGrp.rotation.set(posture.roll * DEG * powerFrac, posture.pitch * DEG * powerFrac, posture.yaw * DEG * powerFrac, "ZXY");
    if (posture.powered && cmd === "s") bodyGrp.position.z += Math.sin(timestamp * 0.003) * 0.001;

    controls.target.set(worldGrp.position.x, currentH * 0.5, worldGrp.position.z);
    controls.update();
    refreshMotorDisplay();
    if (renderer && scene && camera) renderer.render(scene, camera);
  }

  // ─── Keyboard (from original — Z=bk, S=fw, Q=sr, D=sl, A=tr, E=tl) ──
  function onKeyDown(e) {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    keys[e.key.toLowerCase()] = true;
    if (posture.powered) processKeys();
  }
  function onKeyUp(e) {
    keys[e.key.toLowerCase()] = false;
    if (posture.powered) processKeys();
  }
  function processKeys() {
    if (keys["z"] || keys["arrowup"]) { cmd = "bk"; return; }
    if (keys["s"] || keys["arrowdown"]) { cmd = "fw"; return; }
    if (keys["q"] || keys["arrowleft"]) { cmd = "sr"; return; }
    if (keys["d"] || keys["arrowright"]) { cmd = "sl"; return; }
    if (keys["a"]) { cmd = "tr"; return; }
    if (keys["e"]) { cmd = "tl"; return; }
    if (keys[" "]) { cmd = "s"; return; }
    if (!Object.values(keys).some(Boolean)) cmd = "s";
  }

  // ─── Orbit buttons ──────────────────────────────────────────────
  function buildOrbitButtons(container) {
    var btns = document.createElement("div");
    btns.style.cssText = "position:absolute;top:8px;right:8px;display:flex;gap:4px;z-index:6";
    function btn(label, title, onclick) {
      var b = document.createElement("button");
      b.textContent = label; b.title = title;
      b.style.cssText = "width:28px;height:28px;border-radius:6px;border:1px solid var(--border-color, rgba(255,255,255,0.1));background:var(--bg-card, rgba(12,12,18,0.8));color:var(--text-secondary, #aaa);font-size:13px;cursor:pointer;display:flex;align-items:center;justify-content:center;line-height:1;padding:0;transition:all 0.15s;font-family:inherit";
      b.onmouseenter = function () { b.style.background = "var(--accent, #6366f1)"; b.style.borderColor = "var(--accent, #6366f1)"; b.style.color = "#fff"; };
      b.onmouseleave = function () { b.style.background = "var(--bg-card, rgba(12,12,18,0.8))"; b.style.borderColor = "var(--border-color, rgba(255,255,255,0.1))"; b.style.color = "var(--text-secondary, #aaa)"; };
      b.onclick = function (e) { e.stopPropagation(); e.preventDefault(); onclick(); };
      return b;
    }
    btns.appendChild(btn("\u21BA", "Rotation gauche", function () {
      var s = controls.spherical || controls;
      var az = controls.getAzimuthalAngle ? controls.getAzimuthalAngle() : 0;
      controls.autoRotate = false;
      if (controls.target) controls.target.set(worldPos.x, (STAND_H * (posture.height / 100)) * 0.5, -worldPos.z);
      camera.position.applyAxisAngle(new THREE.Vector3(0, 1, 0), 0.5);
      controls.update();
    }));
    btns.appendChild(btn("\u21BB", "Rotation droite", function () {
      controls.autoRotate = false;
      if (controls.target) controls.target.set(worldPos.x, (STAND_H * (posture.height / 100)) * 0.5, -worldPos.z);
      camera.position.applyAxisAngle(new THREE.Vector3(0, 1, 0), -0.5);
      controls.update();
    }));
    btns.appendChild(btn("\u2191", "Vue dessus", function () {
      controls.target.set(worldPos.x, (STAND_H * (posture.height / 100)) * 0.5, -worldPos.z);
      var d = controls.getDistance ? controls.getDistance() : 0.65;
      camera.position.set(worldPos.x, (STAND_H * (posture.height / 100)) * 0.5 + d, -worldPos.z);
      controls.update();
    }));
    btns.appendChild(btn("\u2299", "Vue face", function () {
      controls.target.set(worldPos.x, (STAND_H * (posture.height / 100)) * 0.5, -worldPos.z);
      camera.position.set(worldPos.x + 0.65, (STAND_H * (posture.height / 100)) * 0.5 + 0.08, -worldPos.z);
      controls.update();
    }));
    btns.appendChild(btn("\uFF0B", "Zoom +", function () { controls.dollyIn(1.1); controls.update(); }));
    btns.appendChild(btn("\u2212", "Zoom \u2212", function () { controls.dollyOut(1.1); controls.update(); }));
    container.appendChild(btns);
  }

  // ─── Resize ─────────────────────────────────────────────────────
  function onResize() {
    if (!containerEl || !renderer || !camera) return;
    var rw = containerEl.clientWidth, rh = containerEl.clientHeight;
    if (rw <= 0 || rh <= 0) return;
    camera.aspect = rw / rh;
    camera.updateProjectionMatrix();
    renderer.setSize(rw, rh);
  }

  // ─── Public API ─────────────────────────────────────────────────
  window.initSpotMicro3D = function (containerId) {
    var c = document.getElementById(containerId);
    if (!c) return;
    containerEl = c;
    c.innerHTML = "";

    // Loading overlay (matching original spinner + progress bar)
    var loadOverlay = document.createElement("div");
    loadOverlay.id = "spot3d-loading";
    loadOverlay.style.cssText = "position:absolute;top:0;left:0;right:0;bottom:0;display:flex;flex-direction:column;align-items:center;justify-content:center;background:rgba(10,10,15,0.95);z-index:10;transition:opacity 0.5s";
    loadOverlay.innerHTML = '<div style="width:44px;height:44px;border:3px solid rgba(99,102,241,0.12);border-top-color:#6366f1;border-radius:50%;animation:spot3d-spin 1s linear infinite;margin-bottom:16px"></div><div id="spot3d-progress-pct" style="font-size:1.5rem;font-weight:700;color:#6366f1;margin-bottom:4px;font-family:monospace">0%</div><div style="width:180px;height:4px;background:rgba(255,255,255,0.06);border-radius:100px;overflow:hidden;margin-bottom:10px"><div id="spot3d-progress-bar" style="height:100%;width:0%;background:linear-gradient(90deg,#6366f1,#d946ef);border-radius:100px;transition:width 0.2s ease;box-shadow:0 0 8px rgba(99,102,241,0.4)"></div></div><span id="spot3d-progress-text" style="font-size:0.8rem;color:#a1a1aa;font-family:monospace">Chargement des modeles STL...</span><style>@keyframes spot3d-spin{to{transform:rotate(360deg)}}</style>';
    c.appendChild(loadOverlay);

    var w = c.clientWidth || 400, h = c.clientHeight || 450;

    // Scene
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0a0f);
    scene.fog = new THREE.FogExp2(0x0a0a0f, 0.25);

    // Camera
    camera = new THREE.PerspectiveCamera(40, w / h, 0.01, 30);
    camera.position.set(0.65, 0.35, 0.70);

    // Renderer
    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(w, h);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.domElement.style.cssText = "display:block;width:100%;height:100%";
    c.appendChild(renderer.domElement);

    // OrbitControls
    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;
    controls.maxPolarAngle = PI / 2 - 0.01;
    controls.minDistance = 0.12;
    controls.maxDistance = 3.5;
    controls.target.set(0, 0.06, 0);

    // Lighting
    scene.add(new THREE.AmbientLight(0xffffff, 0.25));
    var sun = new THREE.DirectionalLight(0xfff6e8, 0.95);
    sun.position.set(1, 3, 1.5);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.bias = -0.00015;
    scene.add(sun);
    var accent1 = new THREE.PointLight(0x6366f1, 2.5, 2.2);
    accent1.position.set(-0.35, 0.6, 0.4);
    scene.add(accent1);
    var accent2 = new THREE.PointLight(0xd946ef, 1.6, 1.8);
    accent2.position.set(0.4, 0.15, -0.5);
    scene.add(accent2);

    // Floor
    var grid = new THREE.GridHelper(18, 180, 0x6366f1, 0x151520);
    scene.add(grid);
    var floorMat = new THREE.ShadowMaterial({ opacity: 0.45 });
    var floor = new THREE.Mesh(new THREE.PlaneGeometry(18, 18), floorMat);
    floor.rotation.x = -PI / 2;
    floor.receiveShadow = true;
    scene.add(floor);

    // Robot structure
    worldGrp = new THREE.Group();
    scene.add(worldGrp);
    robotGrp = new THREE.Group();
    robotGrp.rotation.x = -PI / 2;
    robotGrp.rotation.z = PI;
    worldGrp.add(robotGrp);
    bodyGrp = new THREE.Group();
    bodyGrp.position.z = LIE_H;
    robotGrp.add(bodyGrp);

    // Orbit buttons
    buildOrbitButtons(c);

    // Resize
    window.addEventListener("resize", onResize);

    // Load STLs then start
    loadAllMeshes(function () {
      buildRobotFromCache();
      window.addEventListener("keydown", onKeyDown);
      window.addEventListener("keyup", onKeyUp);
      lastTime = performance.now();
      animate(lastTime);
    });
  };

  window.updateSpotMicroPosture = function (np) {
    if (np.height !== undefined) posture.height = np.height;
    if (np.speed !== undefined) posture.speed = np.speed;
    if (np.roll !== undefined) posture.roll = np.roll;
    if (np.pitch !== undefined) posture.pitch = np.pitch;
    if (np.yaw !== undefined) posture.yaw = np.yaw;
    if (np.powered !== undefined) posture.powered = np.powered;
    if (np.demo_mode !== undefined) posture.demo_mode = np.demo_mode;
  };
  window.setSpotMicroDemoMode = function (enabled) { posture.demo_mode = enabled; if (enabled) { cmd = "s"; keys = {}; } };
  window.setSpotMicroPowered = function (on) { posture.powered = on; if (!on) { cmd = "s"; keys = {}; } };
  window.getSpotMicroPosture = function () { return posture; };
  window.stopSpotMicroMovement = function () { cmd = "s"; keys = {}; };

  window.disposeSpotMicro3D = function () {
    if (animFrameId) cancelAnimationFrame(animFrameId);
    window.removeEventListener("keydown", onKeyDown);
    window.removeEventListener("keyup", onKeyUp);
    window.removeEventListener("resize", onResize);
    if (renderer) renderer.dispose();
    if (containerEl) containerEl.innerHTML = "";
    scene = null; camera = null; renderer = null; controls = null;
    worldGrp = null; robotGrp = null; bodyGrp = null; legData = {}; meshCache = {}; containerEl = null;
  };
})();

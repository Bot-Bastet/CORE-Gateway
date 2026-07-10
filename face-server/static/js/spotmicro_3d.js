// === SpotMicro 3D Viewer ===
// Ported from spotmicro_3d.html for Bastet Gateway Dashboard
// Uses Three.js r128 + STL meshes from spotmicro_description CDN
// Joint names match Arduino/calibration naming convention

(function () {
  "use strict";

  // ─── Constants ──────────────────────────────────────────────────────
  var PI = Math.PI;
  var DEG = PI / 180;
  var STL_BASE = "https://raw.githubusercontent.com/michaelromer/spotmicro_description/master/meshes/";

  // Joint name mapping: internal (fl_s, fl_t, fl_c...) → calibration (FR Abad, FR Upper...)
  var JOINT_CALIB_NAMES = {
    "fr_s": "FR Abad",
    "fr_t": "FR Upper",
    "fr_c": "FR Lower",
    "fl_s": "FL Abad",
    "fl_t": "FL Upper",
    "fl_c": "FL Lower",
    "rr_s": "BR Abad",
    "rr_t": "BR Upper",
    "rr_c": "BR Lower",
    "rl_s": "BL Abad",
    "rl_t": "BL Upper",
    "rl_c": "BL Lower"
  };

  var HUD_J = [
    { id: "fr_s", label: "FR Abad" },
    { id: "fr_t", label: "FR Upper" },
    { id: "fr_c", label: "FR Lower" },
    { id: "fl_s", label: "FL Abad" },
    { id: "fl_t", label: "FL Upper" },
    { id: "fl_c", label: "FL Lower" },
    { id: "rr_s", label: "BR Abad" },
    { id: "rr_t", label: "BR Upper" },
    { id: "rr_c", label: "BR Lower" },
    { id: "rl_s", label: "BL Abad" },
    { id: "rl_t", label: "BL Upper" },
    { id: "rl_c", label: "BL Lower" }
  ];

  // Gait phases for trot
  var TROT_PHASE = { fl: 0, fr: PI, rl: PI, rr: 0 };

  // Leg geometry (meters)
  var L1 = 0.035; // coxa length
  var L2 = 0.100; // femur length
  var L3 = 0.115; // tibia length

  // STL component list
  var UNIQUE_STLS = [
    { id: "mainbody", file: "body_mg.stl" },
    { id: "frontpart", file: "B%2Bfront_mg.stl" },
    { id: "backpart", file: "B%2Bback_mg.stl" },
    { id: "lshoulder", file: "l_shoulder_mg.stl" },
    { id: "rshoulder", file: "r_shoulder_mg.stl" },
    { id: "larm", file: "l_arm_mg.stl" },
    { id: "rarm", file: "r_arm_mg.stl" },
    { id: "larm_cover", file: "l_arm_cover.stl" },
    { id: "rarm_cover", file: "r_arm_cover.stl" },
    { id: "lfoot", file: "l_wrist_mg.stl" },
    { id: "rfoot", file: "r_wrist_mg.stl" },
    { id: "foot", file: "foot.stl" }
  ];

  // Leg positions (fl, fr, rl, rr) — ROS coords: +X=Forward, +Y=Left, +Z=Up
  var LEGS = {
    fl: { left: true, shiftx: -0.10, shifty: 0.06, shift: -0.02 },
    fr: { left: false, shiftx: 0.10, shifty: 0.06, shift: -0.02 },
    rl: { left: true, shiftx: -0.10, shifty: -0.10, shift: 0.02 },
    rr: { left: false, shiftx: 0.10, shifty: -0.10, shift: 0.02 }
  };

  // ─── State ──────────────────────────────────────────────────────────
  var scene, camera, renderer, controls, clock;
  var worldGrp, robotGrp, bodyGrp;
  var meshCache = {};
  var legData = {};
  var cur = {};    // current joint angles (radians)
  var tgt = {};    // target joint angles (radians)
  var posture = { height: 0.0, speed: 50.0, roll: 0.0, pitch: 0.0, yaw: 0.0, demo_mode: false, powered: true };
  var cmd = "s";   // movement command: s=stop, fw=forward, bk=back, sl=left, sr=right, tl=turn-left, tr=turn-right
  var keys = {};
  var animFrameId = null;
  var containerEl = null;
  var loading = true;
  var gaitTime = 0;
  var worldPos = { x: 0, z: 0, yaw: 0 };

  // Init joint angles to standing position
  var defaultAngles = { s: 0, t: -0.6, c: 1.2 };
  ["fl", "fr", "rl", "rr"].forEach(function (id) {
    cur[id + "_s"] = defaultAngles.s;
    cur[id + "_t"] = defaultAngles.t;
    cur[id + "_c"] = defaultAngles.c;
    tgt[id + "_s"] = defaultAngles.s;
    tgt[id + "_t"] = defaultAngles.t;
    tgt[id + "_c"] = defaultAngles.c;
  });

  // ─── Materials ──────────────────────────────────────────────────────
  var matGold, matDark, matFoot;

  // ─── Inverse Kinematics ─────────────────────────────────────────────
  function solveLegIK(isLeft, D) {
    // D = [dx, dy, dz] foot position relative to coxa joint
    var dx = D[0], dy = D[1], dz = D[2];
    var sign = isLeft ? 1 : -1;

    // Shoulder (abad) angle — lateral rotation
    var a0 = Math.atan2(dy, -dz);

    // Distance in the leg plane
    var d_plane = Math.sqrt(dx * dx + dz * dz + dy * dy - (L1 * L1));
    if (d_plane > L2 + L3) d_plane = L2 + L3;
    if (d_plane < Math.abs(L2 - L3)) d_plane = Math.abs(L2 - L3);

    // Law of cosines for knee angle
    var cos_knee = (L2 * L2 + L3 * L3 - d_plane * d_plane) / (2 * L2 * L3);
    cos_knee = Math.max(-1, Math.min(1, cos_knee));
    var knee = PI - Math.acos(cos_knee); // calf angle

    // Hip angle
    var alpha = Math.atan2(dx, Math.sqrt(dy * dy + dz * dz - L1 * L1));
    var beta = Math.acos((L2 * L2 + d_plane * d_plane - L3 * L3) / (2 * L2 * d_plane));
    var hip = alpha + beta;

    return [a0 * sign, -hip, knee];
  }

  // ─── Gait Engine ────────────────────────────────────────────────────
  function computeTargets(dt) {
    if (!posture.powered) return;

    var h = posture.height / 100;        // 0..1
    var spd = posture.speed / 100;       // 0..1
    var roll = posture.roll * DEG;
    var pitch = posture.pitch * DEG;
    var yawB = posture.yaw * DEG;

    var standH = 0.12 + h * 0.06;        // standing height 0.12..0.18
    var stepLen = spd * 0.06;            // step length
    var stepH = 0.03 + spd * 0.02;       // step lift height
    var freq = 1.5 + spd * 1.5;          // gait frequency

    var moving = (cmd === "fw" || cmd === "bk" || cmd === "sl" || cmd === "sr" || cmd === "tl" || cmd === "tr");

    if (moving) {
      gaitTime += dt * freq;
    } else {
      // Slowly return to standing phase
      gaitTime = Math.floor(gaitTime / (2 * PI)) * (2 * PI);
    }

    var phase = gaitTime;

    ["fl", "fr", "rl", "rr"].forEach(function (id) {
      var leg = LEGS[id];
      var legPhase = phase + (TROT_PHASE[id] || 0);

      // Default foot position (standing)
      var fx = leg.shiftx;
      var fy = leg.shifty;
      var fz = -standH;

      // Body attitude offsets
      fx += pitch * 0.08 * (id.charAt(0) === "f" ? 1 : -1); // pitch tilts front/back up/down
      fy += roll * 0.08 * (leg.left ? -1 : 1);               // roll tilts left/right
      var yawOff = yawB * 0.06;
      fx += yawOff * (leg.left ? -1 : 1);
      fy += yawOff * (id.charAt(0) === "f" ? -1 : 1);

      if (moving) {
        var sinVal = Math.sin(legPhase);
        var stance = sinVal > 0; // stance phase when sin > 0

        // Directional offsets
        var dxOff = 0, dyOff = 0;
        if (cmd === "fw") dxOff = stepLen;
        else if (cmd === "bk") dxOff = -stepLen;
        else if (cmd === "sl") dyOff = stepLen * (leg.left ? -1 : 1);
        else if (cmd === "sr") dyOff = stepLen * (leg.left ? 1 : -1);
        else if (cmd === "tl" || cmd === "tr") {
          var turnDir = cmd === "tl" ? 1 : -1;
          dxOff = stepLen * 0.5 * turnDir * (id.charAt(0) === "f" ? -1 : 1);
          dyOff = stepLen * 0.5 * turnDir * (leg.left ? -1 : 1);
        }

        if (stance) {
          // Stance: foot on ground, pushes body
          fx -= dxOff * (1 - Math.abs(sinVal));
          fy -= dyOff * (1 - Math.abs(sinVal));
        } else {
          // Swing: foot lifts
          fx += dxOff * Math.abs(sinVal);
          fy += dyOff * Math.abs(sinVal);
          fz += stepH * Math.abs(sinVal) * 4;
        }
      }

      // Solve IK
      var angles = solveLegIK(leg.left, [fx - leg.shift, fy, fz]);
      tgt[id + "_s"] = angles[0];
      tgt[id + "_t"] = angles[1];
      tgt[id + "_c"] = angles[2];
    });

    // Update world position for moving robot
    if (moving) {
      var moveSpeed = spd * 0.15 * dt;
      if (cmd === "fw") worldPos.x += moveSpeed;
      else if (cmd === "bk") worldPos.x -= moveSpeed;
      else if (cmd === "sl") worldPos.z -= moveSpeed;
      else if (cmd === "sr") worldPos.z += moveSpeed;
      else if (cmd === "tl") worldPos.yaw += 1.5 * spd * dt;
      else if (cmd === "tr") worldPos.yaw -= 1.5 * spd * dt;
    }
  }

  // ─── Animation ──────────────────────────────────────────────────────
  var lastTime = 0;
  function animate(timestamp) {
    animFrameId = requestAnimationFrame(animate);

    var dt = (timestamp - lastTime) / 1000;
    if (dt > 0.1) dt = 0.016; // cap at 60fps
    lastTime = timestamp;

    if (!loading && posture.powered) {
      computeTargets(dt);
    }

    // Lerp current → target
    var lerpFactor = Math.min(1, dt * 12);
    ["fl", "fr", "rl", "rr"].forEach(function (id) {
      ["_s", "_t", "_c"].forEach(function (j) {
        var key = id + j;
        cur[key] += (tgt[key] - cur[key]) * lerpFactor;
      });
    });

    // Apply joint rotations to 3D model
    if (!loading) {
      ["fl", "fr", "rl", "rr"].forEach(function (id) {
        var ld = legData[id];
        if (!ld) return;
        ld.shoulderGrp.rotation.x = cur[id + "_s"];
        ld.legGrp.rotation.y = cur[id + "_t"];
        ld.footGrp.rotation.y = cur[id + "_c"];
      });

      // Body attitude
      if (bodyGrp) {
        bodyGrp.rotation.z = -posture.roll * DEG * 0.5;
        bodyGrp.rotation.x = posture.pitch * DEG * 0.5;
        bodyGrp.rotation.y = posture.yaw * DEG * 0.5;
      }

      // World position
      if (robotGrp) {
        robotGrp.position.x = worldPos.x;
        robotGrp.position.z = worldPos.z;
        robotGrp.rotation.y = worldPos.yaw;
      }

      // Breathing idle animation
      if (cmd === "s" && posture.powered && !posture.demo_mode) {
        var breathe = Math.sin(timestamp * 0.002) * 0.003;
        if (robotGrp) robotGrp.position.y = breathe;
      } else if (robotGrp) {
        robotGrp.position.y += (0 - robotGrp.position.y) * lerpFactor;
      }

      // Update camera target
      if (controls && robotGrp) {
        controls.target.set(robotGrp.position.x, robotGrp.position.y + 0.05, robotGrp.position.z);
      }
    }

    if (renderer && scene && camera) {
      renderer.render(scene, camera);
    }
  }

  // ─── Build Robot from Loaded STLs ───────────────────────────────────
  function buildRobotFromCache() {
    robotGrp = new THREE.Group();
    bodyGrp = new THREE.Group();
    robotGrp.add(bodyGrp);

    // Helper to create mesh from cache
    function addPart(cacheId, parent, pos, rot, scale) {
      var geom = meshCache[cacheId];
      if (!geom) return null;
      var mat = (cacheId === "lfoot" || cacheId === "rfoot" || cacheId === "foot") ? matFoot :
        (cacheId.indexOf("cover") >= 0) ? matDark : matGold;
      var mesh = new THREE.Mesh(geom, mat);
      if (pos) mesh.position.set(pos[0], pos[1], pos[2]);
      if (rot) mesh.rotation.set(rot[0], rot[1], rot[2]);
      if (scale) mesh.scale.set(scale[0], scale[1], scale[2]);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      parent.add(mesh);
      return mesh;
    }

    // Main body
    addPart("mainbody", bodyGrp, [0, 0, 0], [0, 0, 0], [1, 1, 1]);
    addPart("frontpart", bodyGrp, [0.115, 0, 0], [0, 0, 0], [1, 1, 1]);
    addPart("backpart", bodyGrp, [-0.115, 0, 0], [0, 0, 0], [1, 1, 1]);

    // Build each leg
    ["fl", "fr", "rl", "rr"].forEach(function (id) {
      var leg = LEGS[id];
      var isLeft = leg.left;

      // Shoulder group
      var shoulderGrp = new THREE.Group();
      shoulderGrp.position.set(leg.shiftx * 0.7, 0.01, leg.shifty * 0.7);
      bodyGrp.add(shoulderGrp);

      // Shoulder mesh
      var shoulderMeshId = isLeft ? "lshoulder" : "rshoulder";
      var shoulderMesh = addPart(shoulderMeshId, shoulderGrp, [0, -0.01, 0],
        [isLeft ? 0 : PI, 0, -PI / 2], [1, 1, 1]);
      if (shoulderMesh && isLeft) shoulderMesh.rotation.y += PI;

      // Leg group (thigh)
      var legGrp = new THREE.Group();
      legGrp.position.set(0, -0.02, leg.shift);
      shoulderGrp.add(legGrp);

      // Arm mesh
      var armId = isLeft ? "larm" : "rarm";
      addPart(armId, legGrp, [0, 0.04, 0], [0, 0, 0], [1, 1, 1]);

      // Cover
      var coverId = isLeft ? "larm_cover" : "rarm_cover";
      addPart(coverId, legGrp, [0, 0.04, 0], [0, 0, 0], [1, 1, 1]);

      // Foot group (calf)
      var footGrp = new THREE.Group();
      footGrp.position.set(0, 0.10, 0);
      legGrp.add(footGrp);

      // Foot mesh
      var footId = isLeft ? "lfoot" : "rfoot";
      addPart(footId, footGrp, [0, 0.08, 0], [0, 0, 0], [1, 1, 1]);
      addPart("foot", footGrp, [0, 0.13, 0], [0, 0, 0], [1, 1, 1]);

      legData[id] = {
        shoulderGrp: shoulderGrp,
        legGrp: legGrp,
        footGrp: footGrp
      };
    });

    // Ground plane
    var groundGeom = new THREE.PlaneGeometry(2, 2);
    var groundMat = new THREE.MeshStandardMaterial({ color: 0x111118, roughness: 0.9, metalness: 0.1 });
    var ground = new THREE.Mesh(groundGeom, groundMat);
    ground.rotation.x = -PI / 2;
    ground.position.y = -0.25;
    ground.receiveShadow = true;
    robotGrp.add(ground);

    // Grid helper
    var gridHelper = new THREE.GridHelper(1.5, 20, 0x222233, 0x111118);
    gridHelper.position.y = -0.249;
    robotGrp.add(gridHelper);

    worldGrp.add(robotGrp);
    loading = false;
  }

  // ─── Load All STLs ──────────────────────────────────────────────────
  function loadAllMeshes(onComplete) {
    var total = UNIQUE_STLS.length;
    var loaded = 0;

    UNIQUE_STLS.forEach(function (item) {
      var loader = new THREE.STLLoader();
      var url = STL_BASE + item.file;
      loader.load(url, function (geometry) {
        geometry.computeVertexNormals();
        meshCache[item.id] = geometry;
        loaded++;
        if (loaded >= total) {
          onComplete();
        }
      }, undefined, function () {
        // Retry once with alternative URL encoding
        var altUrl = STL_BASE + item.file.replace(/%2B/g, "+");
        var loader2 = new THREE.STLLoader();
        loader2.load(altUrl, function (geometry) {
          geometry.computeVertexNormals();
          meshCache[item.id] = geometry;
          loaded++;
          if (loaded >= total) onComplete();
        }, undefined, function () {
          loaded++;
          if (loaded >= total) onComplete();
        });
      });
    });
  }

  // ─── Build HUD ──────────────────────────────────────────────────────
  function buildHUD(container) {
    var hud = document.createElement("div");
    hud.id = "spot3d-hud";
    hud.style.cssText = "position:absolute;bottom:8px;left:8px;right:8px;display:grid;grid-template-columns:repeat(4,1fr);gap:4px;pointer-events:none;z-index:5";

    HUD_J.forEach(function (j) {
      var el = document.createElement("div");
      el.style.cssText = "background:rgba(0,0,0,0.75);border-radius:4px;padding:2px 6px;font-size:0.65rem;text-align:center;color:#aaa";
      el.innerHTML = '<span style="font-size:0.55rem;display:block;color:#666">' + j.label + '</span>' +
        '<span id="hud-' + j.id + '" style="font-weight:700;color:var(--accent,#6366f1);font-size:0.7rem">90°</span>';
      hud.appendChild(el);
    });

    container.appendChild(hud);
  }

  function refreshHUD() {
    HUD_J.forEach(function (j) {
      var el = document.getElementById("hud-" + j.id);
      if (el) {
        var deg = Math.round(cur[j.id] / DEG);
        el.textContent = deg + "°";
      }
    });
  }

  // ─── Public API ─────────────────────────────────────────────────────
  window.initSpotMicro3D = function (containerId) {
    var container = document.getElementById(containerId);
    if (!container) return;
    containerEl = container;

    // Clear any previous
    container.innerHTML = "";

    // Scene
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0a10);
    scene.fog = new THREE.Fog(0x0a0a10, 0.8, 3);

    // Camera
    var w = container.clientWidth || 400;
    var h = container.clientHeight || 450;
    camera = new THREE.PerspectiveCamera(45, w / h, 0.05, 10);
    camera.position.set(0.35, 0.2, 0.45);
    camera.lookAt(0, 0, 0);

    // Renderer
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(w, h);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.appendChild(renderer.domElement);
    renderer.domElement.style.cssText = "display:block;width:100%;height:100%";

    // Lights
    var ambLight = new THREE.AmbientLight(0x404060, 1.2);
    scene.add(ambLight);
    var dirLight = new THREE.DirectionalLight(0xffeedd, 1.5);
    dirLight.position.set(1, 2, 1);
    dirLight.castShadow = true;
    dirLight.shadow.mapSize.set(512, 512);
    dirLight.shadow.camera.near = 0.1;
    dirLight.shadow.camera.far = 5;
    scene.add(dirLight);
    var hemiLight = new THREE.HemisphereLight(0x6060a0, 0x202030, 0.6);
    scene.add(hemiLight);

    // Orbit controls
    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0.05, 0);
    controls.enableDamping = true;
    controls.dampingFactor = 0.1;
    controls.minDistance = 0.25;
    controls.maxDistance = 1.2;
    controls.maxPolarAngle = PI * 0.7;
    controls.update();

    // Materials
    matGold = new THREE.MeshStandardMaterial({ color: 0xdaa520, roughness: 0.4, metalness: 0.6 });
    matDark = new THREE.MeshStandardMaterial({ color: 0x2a2a35, roughness: 0.5, metalness: 0.3 });
    matFoot = new THREE.MeshStandardMaterial({ color: 0x1a1a25, roughness: 0.7, metalness: 0.1 });

    // World
    worldGrp = new THREE.Group();
    scene.add(worldGrp);

    // Build HUD overlay
    buildHUD(container);

    // Load meshes
    loading = true;
    loadAllMeshes(function () {
      buildRobotFromCache();
    });

    // Handle resize
    if (!window._spot3dResizeHandler) {
      window._spot3dResizeHandler = function () {
        if (!containerEl || !renderer || !camera) return;
        var rw = containerEl.clientWidth;
        var rh = containerEl.clientHeight;
        if (rw <= 0 || rh <= 0) return;
        renderer.setSize(rw, rh);
        camera.aspect = rw / rh;
        camera.updateProjectionMatrix();
      };
      window.addEventListener("resize", window._spot3dResizeHandler);
    }

    // Start animation
    lastTime = performance.now();
    animate(lastTime);

    // Keyboard handling
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
  };

  // ─── Keyboard Input ─────────────────────────────────────────────────
  function onKeyDown(e) {
    var map = { "z": "fw", "KeyW": "fw", "ArrowUp": "fw",
      "s": "bk", "KeyS": "bk", "ArrowDown": "bk",
      "q": "tl", "KeyA": "tl", "ArrowLeft": "tl",
      "d": "tr", "KeyD": "tr", "ArrowRight": "tr" };
    var dir = map[e.key] || map[e.code];
    if (dir && !keys[dir]) {
      keys[dir] = true;
      cmd = dir;
    }
  }

  function onKeyUp(e) {
    var map = { "z": "fw", "KeyW": "fw", "ArrowUp": "fw",
      "s": "bk", "KeyS": "bk", "ArrowDown": "bk",
      "q": "tl", "KeyA": "tl", "ArrowLeft": "tl",
      "d": "tr", "KeyD": "tr", "ArrowRight": "tr" };
    var dir = map[e.key] || map[e.code];
    if (dir) {
      keys[dir] = false;
      if (!Object.values(keys).some(function (v) { return v; })) {
        cmd = "s"; // stop
      }
    }
  }

  window.stopSpotMicroMovement = function () {
    cmd = "s";
    keys = {};
  };

  // ─── Posture Updates ────────────────────────────────────────────────
  window.updateSpotMicroPosture = function (newPosture) {
    if (newPosture.height !== undefined) posture.height = newPosture.height;
    if (newPosture.speed !== undefined) posture.speed = newPosture.speed;
    if (newPosture.roll !== undefined) posture.roll = newPosture.roll;
    if (newPosture.pitch !== undefined) posture.pitch = newPosture.pitch;
    if (newPosture.yaw !== undefined) posture.yaw = newPosture.yaw;
    if (newPosture.powered !== undefined) posture.powered = newPosture.powered;
    if (newPosture.demo_mode !== undefined) posture.demo_mode = newPosture.demo_mode;
  };

  window.setSpotMicroDemoMode = function (enabled) {
    posture.demo_mode = enabled;
    if (enabled) {
      // Freeze robot, motors off in simulation
      cmd = "s";
      keys = {};
    }
  };

  window.setSpotMicroPowered = function (on) {
    posture.powered = on;
    if (!on) {
      cmd = "s";
      keys = {};
    }
  };

  window.getSpotMicroPosture = function () {
    return posture;
  };

  // ─── Dispose ────────────────────────────────────────────────────────
  window.disposeSpotMicro3D = function () {
    if (animFrameId) cancelAnimationFrame(animFrameId);
    window.removeEventListener("keydown", onKeyDown);
    window.removeEventListener("keyup", onKeyUp);
    if (renderer) renderer.dispose();
    if (containerEl) containerEl.innerHTML = "";
    scene = null; camera = null; renderer = null; controls = null;
    worldGrp = null; robotGrp = null; bodyGrp = null;
    meshCache = {}; legData = {};
    containerEl = null;
  };
})();

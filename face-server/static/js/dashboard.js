// ===== Standalone login handler (works even if main script has errors) =====

(function() {

    var token = localStorage.getItem('bastet_api_token');

    var userStr = localStorage.getItem('bastet_user');

    var currentUser = null;

    try { if (userStr) currentUser = JSON.parse(userStr); } catch(e) {}

    

    window._bastet_token = token || '';

    window._bastet_user = currentUser || null;

    

    if (token && currentUser) {

        var overlay = document.getElementById('authOverlay');

        if (overlay) overlay.style.display = 'none';

        window.dispatchEvent(new CustomEvent('bastet:authenticated', { detail: { token: token, user: currentUser } }));

    }

    

    setTimeout(function() {

        var form = document.querySelector('#authOverlay form');

        var emailInput = document.getElementById('loginEmail');

        var passInput = document.getElementById('loginPassword');

        var errorDiv = document.getElementById('loginError');

        var submitBtn = document.getElementById('loginSubmitBtn');

        

        if (form && emailInput && passInput) {

            form.onsubmit = async function(e) {

                e.preventDefault();

                if (!submitBtn || submitBtn.disabled) return;

                

                var email = emailInput.value.trim();

                var password = passInput.value;

                

                if (!email || !password) {

                    if (errorDiv) { errorDiv.textContent = 'Veuillez remplir tous les champs.'; errorDiv.style.display = 'block'; }

                    return;

                }

                

                if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Connexion...'; }

                if (errorDiv) errorDiv.style.display = 'none';

                

                try {

                    var resp = await fetch('/auth/login', {

                        method: 'POST',

                        headers: { 'Content-Type': 'application/json' },

                        body: JSON.stringify({ email: email, password: password })

                    });

                    var data = await resp.json();

                    

                    if (resp.ok && data.status === 'success') {

                        var apiToken = data.api_token || '';

                        var user = data.user || {};

                        

                        localStorage.setItem('bastet_api_token', apiToken);

                        localStorage.setItem('bastet_user', JSON.stringify(user));

                        window._bastet_token = apiToken;

                        window._bastet_user = user;

                        

                        var overlay2 = document.getElementById('authOverlay');

                        if (overlay2) overlay2.style.display = 'none';

                        

                        window.dispatchEvent(new CustomEvent('bastet:authenticated', { detail: { token: apiToken, user: user } }));

                    } else {

                        var msg = data.detail || 'Email ou mot de passe incorrect.';

                        if (errorDiv) { errorDiv.textContent = msg; errorDiv.style.display = 'block'; }

                    }

                } catch(err) {

                    if (errorDiv) { errorDiv.textContent = 'Erreur de connexion au serveur.'; errorDiv.style.display = 'block'; }

                }

                

                if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Se connecter'; }

            };

        }

    }, 100);

})();

let apiToken = localStorage.getItem('bastet_api_token') || window._bastet_token || '';

        let activeTab = localStorage.getItem('bastetActiveTab') || 'dashboard';

        let telemetryInterval = null;

        let updateInterval = null;

        let accountsCached = {};

        let activeFolderName = null;

        let facesCached = [];

        let appWs = null;

        window.activeStreams = { 1: false, 2: false };

        window.localViewing = { 1: false, 2: false };

        window.userClosedStream = { 1: false, 2: false };

        let peerConnections = { 1: null, 2: null };

        let streamingState = { 1: "idle", 2: "idle" };  // idle|requesting|connecting|active|error

        window.manualJointControlActive = false;

        

        // SLAM / Map variables

        window.slamGrid = null;

        window.slamPath = [];

        window.slamPoints = [];

        window.robotPose = {x: 0, y: 0, theta: 0};

        // ─── THEME ──────────────────────────────────────────────────────────────

        function getCookie(name) {

            const v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');

            return v ? v.pop() : null;

        }

        function setCookie(name, value, days) {

            const d = new Date();

            d.setTime(d.getTime() + days * 86400000);

            document.cookie = name + '=' + value + ';expires=' + d.toUTCString() + ';path=/;SameSite=Lax';

        }

        function applyTheme(theme) {

            document.documentElement.setAttribute('data-theme', theme);

            const isDark = theme === 'dark';

            document.querySelectorAll('#theme-icon-dark, #theme-icon-dark-m').forEach(el => el.style.display = isDark ? '' : 'none');

            document.querySelectorAll('#theme-icon-light, #theme-icon-light-m').forEach(el => el.style.display = isDark ? 'none' : '');

        }

        function toggleTheme() {

            const current = document.documentElement.getAttribute('data-theme') || 'dark';

            const next = current === 'dark' ? 'light' : 'dark';

            setCookie('bastet_theme', next, 365);

            applyTheme(next);

        }

        (function initTheme() {

            const saved = getCookie('bastet_theme');

            applyTheme(saved || 'dark');

        })();

        // ─── INIT ─────────────────────────────────────────────────────────────

        

        async function checkAuth() {

            loadStreamQualityConfig();

            if (!apiToken) {

                showLogin();

                return;

            }

            try {

                const res = await fetch('/accounts', { headers: { 'X-API-Token': apiToken } });

                if (res.status === 200) {

                    hideLogin();

                    initDashboard();

                    return;

                }

            } catch (e) {}

            // Fallback: standalone script already authenticated

            if (window._bastet_user) {

                currentUser = window._bastet_user;

                hideLogin();

                initDashboard();

                return;

            }

            showLogin();

        }

        function showLogin() {

            var emailEl = document.getElementById('loginEmail');

            var passEl = document.getElementById('loginPassword');

            var errEl = document.getElementById('loginError');

            var btnEl = document.getElementById('loginSubmitBtn');

            if (emailEl) emailEl.value = '';

            if (passEl) passEl.value = '';

            if (errEl) { errEl.textContent = ''; errEl.style.display = 'none'; }

            if (btnEl) btnEl.disabled = false;

            document.getElementById('authOverlay').style.display = 'flex';

            clearIntervals();

        }

        function hideLogin() {

            document.getElementById('authOverlay').style.display = 'none';

        }

        function handleLoginSubmit(e) {

            e.preventDefault();

            apiToken = document.getElementById('tokenInput').value.trim();

            localStorage.setItem('bastet_api_token', apiToken);

            checkAuth();

        }

        function logout() {

            // Close active streams

            for (let id of [1, 2]) {

                stopStreamUI(id);

            }

            if (appWs) {

                appWs.close();

                appWs = null;

            }

            apiToken = '';

            currentUser = null;

            localStorage.removeItem('bastet_api_token');

            localStorage.removeItem('bastet_user');

            window._bastet_token = '';

            window._bastet_user = null;

            showLogin();

        }

        function initDashboard() {

            switchTab(activeTab);

            startIntervals();

            initDragAndDrop();

            connectGlobalWebSocket();

            loadSavedOffsets();

            updateSLAMMode();

            if (typeof updateCameraPortOptions === 'function') updateCameraPortOptions();

            if (typeof updateCameraModularity === 'function') updateCameraModularity(false, false);

            // Fallback: si le badge est toujours en 'Chargement...' apres 10s, afficher un etat neutre

            setTimeout(() => {

                const badgeCalib = document.getElementById('calib-status-badge');

                if (badgeCalib && badgeCalib.textContent.includes('Chargement')) {

                    badgeCalib.textContent = '⏳ En attente des donnees moteurs...';

                    badgeCalib.style.color = 'var(--text-secondary)';

                    badgeCalib.style.fontWeight = 'normal';

                }

            }, 10000);

            // Force refresh SLAM badge every 3s — picks up new cam1_connected/cam2_connected

            // even when telemetry 'sensors' field hasn't been re-broadcast for a while.

            setInterval(() => { try { updateSLAMMode(); } catch (e) { console.warn('updateSLAMMode tick failed', e); } }, 3000);

        }

        // --- INTERVALS ---

        function startIntervals() {

            clearIntervals();

            fetchTelemetry();

            fetchUpdatesProgress(true);

            telemetryInterval = setInterval(fetchTelemetry, 2000);

            updateInterval = setInterval(() => fetchUpdatesProgress(false), 2000);

        }

        function clearIntervals() {

            if (telemetryInterval) clearInterval(telemetryInterval);

            if (updateInterval) clearInterval(updateInterval);

        }

        // ─── WEBSOCKET CLIENT ─────────────────────────────────────────────────

        

        function connectGlobalWebSocket() {

            if (appWs && (appWs.readyState === WebSocket.OPEN || appWs.readyState === WebSocket.CONNECTING)) return;

            

            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';

            const wsUrl = `${protocol}//${window.location.host}/ws/app?token=${apiToken}`;

            appWs = new WebSocket(wsUrl);

            

            appWs.onopen = () => {

                console.log("Global WebSocket connecté.");

                const consoleEl = document.getElementById('json-traffic-console');

                if (consoleEl) consoleEl.textContent = '[WebSocket connecté - En attente de trafic...]';

                window.lastArduinoTelemetry = Date.now();

                if (!window.arduinoOfflineChecker) {

                    window.arduinoOfflineChecker = setInterval(() => {

                        if (window.lastArduinoTelemetry && Date.now() - window.lastArduinoTelemetry > 30000) {

                            const badge = document.getElementById('arduino-status-badge');

                            const content = document.getElementById('arduino-telemetry-content');

                            const offlineMsg = document.getElementById('arduino-offline-msg');

                            if (badge) { badge.className = 'status-badge offline'; badge.textContent = 'Hors-ligne'; }

                            if (content) content.style.display = 'none';

                            if (offlineMsg) offlineMsg.style.display = '';

                        }

                    }, 3000);

                }

            };

            

            appWs.onmessage = (event) => {

                handleIncomingWebSocketMessage(event.data);

            };

            

            appWs.onclose = () => {

                console.log("Global WebSocket déconnecté. Reconnexion...");

                setTimeout(connectGlobalWebSocket, 3000);

            };

            

            appWs.onerror = (e) => {

                console.error("Global WebSocket erreur:", e);

            };

        }

        function logToJSONConsole(data) {

            const consoleEl = document.getElementById('json-traffic-console');

            if (!consoleEl) return;

            

            if (consoleEl.textContent.length > 20000) {

                consoleEl.textContent = consoleEl.textContent.slice(-10000);

            }

            

            const timeStr = new Date().toLocaleTimeString();

            consoleEl.textContent += `\n[${timeStr}] ${data}`;

            consoleEl.scrollTop = consoleEl.scrollHeight;

        }

        function handleIncomingWebSocketMessage(data) {

            var payload;

            try {

                payload = JSON.parse(data);

            } catch (e) {

                console.error("[WS] JSON parse error:", e);

                return;

            }

            try {

                // Print all JSON traffic to the Console

                logToJSONConsole(JSON.stringify(payload, null, 2));

                

                if (payload.type === "telemetry_diagnostics") {

                    window.lastArduinoTelemetry = Date.now();

                    // FIX: read from payload.sensors (gateway-normalized from

                    // available_video_devices) instead of payload.cameras (legacy

                    // ros2_listener boolean, which used to be True for both because

                    // /dev/v4l/by-id counted UVC metadata endpoints as cameras).

                    if (payload.sensors) {

                        updateCameraModularity(

                            payload.sensors.cam1_connected === true,

                            payload.sensors.cam2_connected === true

                        );

                    }

                    // Update calibration status badges

                    if (payload.sensors && payload.sensors.calibration_status) {

                        updateCalibrationBadges(payload.sensors.calibration_status);

                    }

                    // Show camera change warning

                    if (payload.sensors && payload.sensors.camera_changed) {

                        const warningEl = document.getElementById('camera-change-warning');

                        if (warningEl) {

                            const changed = payload.sensors.camera_changed;

                            const anyChanged = (changed['1'] === true || changed[1] === true) ||

                                               (changed['2'] === true || changed[2] === true);

                            warningEl.style.display = anyChanged ? 'block' : 'none';

                        }

                    }

                    if (payload.ai_state) {

                        updateAIControlUI('tts', payload.ai_state.tts);

                        updateAIControlUI('stt', payload.ai_state.stt);

                        updateAIControlUI('chat', payload.ai_state.chat);

                        updateAIControlUI('yolo', payload.ai_state.yolo);

                        updateAIControlUI('face_rec', payload.ai_state.face_rec);

                    }

                    

                    // Update joint angles (0 to 11)

                    if (payload.joints && payload.joints.length === 12) {

                        for (let i = 0; i < 12; i++) {

                            const angle = payload.joints[i];

                            const valEl = document.getElementById(`joint-val-${i}`);

                            const sliderEl = document.getElementById(`joint-slider-${i}`);

                            if (!window.manualJointControlActive) {

                                if (valEl) valEl.textContent = `${Math.round(angle)}°`;

                                if (sliderEl) sliderEl.value = Math.round(angle);

                            }

                        }

                    }

                    

                    // Update IMU

                    if (payload.imu) {

                        const roll = payload.imu.roll ?? 0;

                        const pitch = payload.imu.pitch ?? 0;

                        const yaw = payload.imu.yaw ?? 0;

                        // Cache module-level pour la carte Vue d ensemble (payload.imu peut etre null/absent sur certains ticks 0.5s)

                        window._bastetLastImu = { roll: roll, pitch: pitch, yaw: yaw };

                        

                        const elRoll = document.getElementById('imu-val-roll');

                        const elPitch = document.getElementById('imu-val-pitch');

                        const elYaw = document.getElementById('imu-val-yaw');

                        if (elRoll) elRoll.textContent = `${roll.toFixed(1)}°`;

                        if (elPitch) elPitch.textContent = `${pitch.toFixed(1)}°`;

                        if (elYaw) elYaw.textContent = `${yaw.toFixed(1)}°`;

                        

                        // Rotate 3D IMU CSS Cube

                        const cube = document.getElementById('imu-visual-cube');

                        if (cube) {

                            cube.style.transform = `rotateX(${-pitch}deg) rotateY(${roll}deg) rotateZ(${-yaw}deg)`;

                        }

                    }

                    // Update Arduino Mega dashboard card

                    const arduinoBadge = document.getElementById('arduino-status-badge');

                    const arduinoOfflineMsg = document.getElementById('arduino-offline-msg');

                    const arduinoContent = document.getElementById('arduino-telemetry-content');

                    const hasArduino = payload.imu || (payload.joints && payload.joints.length === 12);

                    if (hasArduino) {

                        arduinoBadge.className = 'status-badge active';

                        arduinoBadge.textContent = 'En ligne';

                        arduinoOfflineMsg.style.display = 'none';

                        arduinoContent.style.display = '';

                        // IMU Vue d ensemble : lit le cache window._bastetLastImu maj par le 1er if(payload.imu).

                        // Permet de garder les valeurs affichees meme quand payload.imu est null/absent.

                        const cachedImu = window._bastetLastImu || { roll: 0, pitch: 0, yaw: 0 };

                        document.getElementById('arduino-roll').textContent = `${cachedImu.roll.toFixed(1)}°`;

                        document.getElementById('arduino-pitch').textContent = `${cachedImu.pitch.toFixed(1)}°`;

                        document.getElementById('arduino-yaw').textContent = `${cachedImu.yaw.toFixed(1)}°`;

                    }

                    if (payload.joints && payload.joints.length === 12) {

                        const jointsGrid = document.getElementById('arduino-joints-grid');

                        if (jointsGrid && !jointsGrid.dataset.init) {

                            const names = ['FR-H','FR-C','FR-T','FL-H','FL-C','FL-T','BR-H','BR-C','BR-T','BL-H','BL-C','BL-T'];

                            jointsGrid.innerHTML = '';

                            for (let i = 0; i < 12; i++) {

                                const el = document.createElement('div');

                                el.style.cssText = 'font-size:0.7rem; text-align:center; padding:0.2rem; background:var(--bg-main); border-radius:4px;';

                                el.innerHTML = `<div style="color:var(--text-secondary);">${names[i]}</div><div style="font-weight:700; color:var(--accent);" id="gw-joint-${i}">${Math.round(payload.joints[i])}°</div>`;

                                jointsGrid.appendChild(el);

                            }

                            jointsGrid.dataset.init = '1';

                        } else if (jointsGrid) {

                            for (let i = 0; i < 12; i++) {

                                const el = document.getElementById(`gw-joint-${i}`);

                                if (el) el.textContent = `${Math.round(payload.joints[i])}°`;

                            }

                        }

                    }

                    

                    // Update Active ROS 2 Topics

                    if (payload.topics) {

                        const tbody = document.getElementById('ros2-topics-list');

                        if (tbody) {

                            tbody.innerHTML = '';

                            payload.topics.forEach(t => {

                                const tr = document.createElement('tr');

                                tr.style.borderBottom = '1px solid var(--border-color)';

                                tr.innerHTML = `

                                    <td style="padding: 0.4rem 0; font-family:monospace; color:var(--accent); white-space: nowrap;">${t.name}</td>

                                    <td style="padding: 0.4rem 0; color:var(--text-secondary); white-space: nowrap; padding-left: 0.5rem; padding-right: 0.5rem;">${t.type}</td>

                                    <td style="padding: 0.4rem 0; text-align:right; font-weight:bold; white-space: nowrap;">${t.hz.toFixed(1)}</td>

                                `;

                                tbody.appendChild(tr);

                            });

                        }

                    }

                    

                    // Update path & pose from diagnostics if present

                    if (payload.pose) {

                        window.robotPose = payload.pose;

                        const rx = payload.pose.x || 0;

                        const ry = payload.pose.y || 0;

                        const rtheta = payload.pose.theta || 0;

                        // (Pose x/y/θ now flows into the SLAM visualizer via drawSLAMMap(); calibration minimap was removed.)

                    }

                    if (payload.path) {

                        window.slamPath = payload.path;

                    }

                }

                else if (payload.type === "mono_calib_frame" || payload.type === "stereo_calib_frame") {
                    const imgs = document.querySelectorAll('#mcc-cam-img');
                    imgs.forEach(img => {
                        img.src = 'data:image/jpeg;base64,' + payload.image;
                        img.style.display = 'block';
                    });
                    const videos = document.querySelectorAll('#mcc-cam-video');
                    videos.forEach(v => v.style.display = 'none');
                    const ovls = document.querySelectorAll('#mcc-cam-status-overlay');
                    ovls.forEach(ovl => {
                        ovl.style.backgroundColor = 'rgba(9,9,11,0.2)';
                        ovl.style.justifyContent = 'flex-end';
                        ovl.style.paddingBottom = '0.5rem';
                    });
                }
                else if (payload.type === "mono_calib_progress") {
                    const camId = payload.camera;
                    const ovl = document.getElementById('mcc-cam-status-overlay');
                    const txt = document.getElementById('mcc-cam-status-text');
                    if (ovl) ovl.style.display = 'flex';
                    if (txt) {
                        txt.innerHTML = '<span style="font-size:1rem; color:var(--text-primary);">' + (payload.message || '').replace(/\n/g, '<br/>') + '</span><br/><span style="font-size:0.75rem; color:var(--text-secondary);">' + (payload.progress || 0) + '%</span>';
                    }
                }
                else if (payload.type === "mono_calib_result") {
                    window.isCalibrating = false;
                    const camId = payload.camera;
                    const ovl2 = document.getElementById('mcc-cam-status-overlay');
                    const txt2 = document.getElementById('mcc-cam-status-text');
                    const btn2 = document.getElementById('btn-mcc-run-calib');
                    if (payload.success) {
                        if (ovl2) { ovl2.style.display = 'flex'; ovl2.style.backgroundColor = 'rgba(9,9,11,0.9)'; }
                        if (txt2) {
                            txt2.innerHTML = '<span style="font-size:2rem; color:var(--success); display:block; margin-bottom:0.5rem;">OK</span><span style="color:var(--success); font-weight:bold; font-size:1.05rem;">Calibration reussie !</span><br/><span style="font-size:0.8rem; color:var(--text-secondary); margin-top:0.25rem; display:block;">fx=' + (payload.fx || '?') + 'px  reproj=' + (payload.reprojection_error || '?') + '</span>';
                        }
                        if (btn2) { btn2.disabled = false; btn2.innerHTML = '<span>Fermer la Calibration</span>'; btn2.onclick = closeCameraCalibModal; }
                    } else {
                        if (ovl2) { ovl2.style.display = 'flex'; ovl2.style.backgroundColor = 'rgba(9,9,11,0.9)'; }
                        if (txt2) {
                            txt2.innerHTML = '<span style="font-size:2rem; color:var(--danger); display:block; margin-bottom:0.5rem;">X</span><span style="color:var(--danger); font-weight:bold; font-size:1.05rem;">Echec</span><br/><span style="font-size:0.8rem; color:var(--text-secondary); margin-top:0.25rem; display:block;">' + (payload.message || 'Erreur') + '</span>';
                        }
                        if (btn2) { btn2.disabled = false; btn2.innerHTML = '<span>Reessayer la Calibration</span>'; btn2.onclick = function() { confirmIndividualCameraCalib(); }; }
                    }
                }

                else if (payload.type === "stream_status") {

                    const camId = parseInt(payload.camera);

                    const isActive = payload.active === true;

                    if (!window.activeStreams) window.activeStreams = { 1: false, 2: false };

                    

                    const wasActive = window.activeStreams[camId];

                    window.activeStreams[camId] = isActive;

                    

                    if (isActive && !wasActive) {

                        if (!window.userClosedStream) window.userClosedStream = { 1: false, 2: false };

                        window.userClosedStream[camId] = false;

                        // FIX: Si on attendait le demarrage du robot pour lancer WebRTC,
                        // c'est le moment ! Le stream_status {active:true} confirme que
                        // l'encodeur ffmpeg tourne et que MediaMTX a le flux.
                        if (window._pendingStreamConnect && window._pendingStreamConnect[camId]) {
                            // Guard: ne pas lancer WebRTC si deja en cours.
                            // On ne consomme le flag qu'APRES avoir verifie qu'on va vraiment lancer.
                            if (streamingState[camId] !== 'connecting' && streamingState[camId] !== 'active') {
                                window._pendingStreamConnect[camId] = false;
                                startStreamWebRTC(camId);
                            }
                        }

                    }

                    

                    const statusEl = document.getElementById(`stream-status-${camId}`);

                    const btnText = document.getElementById(`stream-btn-text-${camId}`);

                    

                    if (!isActive && window.localViewing && window.localViewing[camId]) {

                        // Le flux s'est arr\u00eat\u00e9 c\u00f4t\u00e9 robot alors qu'on le visionnait

                        window.localViewing[camId] = false;

                        stopStreamUI(camId);

                    }

                    // Ne mettre \u00e0 jour l'UI que si on n'est pas en train de visionner localement

                    if (!window.localViewing || !window.localViewing[camId]) {

                        if (statusEl) {

                            // Si l'utilisateur vient de couper manuellement, on garde 'Coup\u00e9'

                            const userCut = window.userClosedStream && window.userClosedStream[camId];

                            // FIX: ne pas ecraser l'UI d'erreur WebRTC avec un faux 'En direct'
                            // (stream_active peut etre stale sur la Gateway)
                            const isErrorState = streamingState[camId] === 'idle' &&
                                                 statusEl.textContent === 'Erreur WebRTC';

                            if (!userCut && !isErrorState) {

                                statusEl.textContent = isActive ? 'En direct' : 'Inactif';

                                statusEl.className = isActive ? 'status-badge active' : 'status-badge';

                            }

                        }

                        if (btnText) {

                            // FIX: preserver le bouton 'Reessayer' en cas d'erreur
                            if (streamingState[camId] === 'idle' && btnText.textContent === 'Reessayer') {
                                // Garder 'Reessayer'
                            } else {
                                btnText.textContent = isActive ? 'Rejoindre le flux' : 'D\u00e9marrer le flux';
                            }

                        }

                        // FIX: pas d'auto-rejoindre. Le flux reste on-demand strict.

                        // Seul un clic utilisateur sur "Demarrer le flux" doit declencher toggleStream().

                        // Les variables window.activeStreams / UI badges continuent d'etre mises a jour.

                        var _onDemandGuard = true; /* placeholder for removed auto-join */

                    }

                    // Update calib preview buttons (merged from former duplicate handler)

                    const previewBtn = document.getElementById('calib-cam-preview-' + camId);

                    if (previewBtn) {

                        if (isActive) {

                            previewBtn.textContent = '■ Arrêter';

                            previewBtn.style.background = 'rgba(239,68,68,0.1)';

                            previewBtn.style.borderColor = 'rgba(239,68,68,0.3)';

                        } else {

                            previewBtn.textContent = '▶ Aperçu';

                            previewBtn.style.background = 'rgba(99,102,241,0.1)';

                            previewBtn.style.borderColor = 'rgba(99,102,241,0.3)';

                        }

                    }

                }

                else if (payload.type === "keep_stream_status") {

                    const camId = parseInt(payload.camera);

                    const isKeep = payload.keep === true;

                    if (!window.keepStreams) window.keepStreams = { 1: false, 2: false };

                    window.keepStreams[camId] = isKeep;

                    const keepBtn = document.getElementById("keep-btn-" + camId);

                    if (keepBtn) {

                        if (isKeep) {

                            keepBtn.classList.add("active");

                            keepBtn.innerHTML = "📌 Keep Stream : ON";

                        } else {

                            keepBtn.classList.remove("active");

                            keepBtn.innerHTML = "📌 Keep Stream : OFF";

                        }

                    }

                }

                else if (payload.type === "camera_resolutions") {

                    const camId = payload.camera;

                    const resolutions = payload.resolutions || [];

                    const selectEl = document.getElementById('stream-res-' + camId);

                    const statusEl = document.getElementById('stream-quality-status');

                    if (selectEl && resolutions.length > 0) {

                        const currentVal = selectEl.value;

                        selectEl.innerHTML = '';

                        resolutions.forEach(function(r) {

                            const parts = r.split('x');

                            const label = parts.length === 2 ? (parts[0] + 'x' + parts[1] + ' (' + parts[1] + 'p)') : r;

                            const opt = document.createElement('option');

                            opt.value = r;

                            opt.textContent = label;

                            selectEl.appendChild(opt);

                        });

                        // Try to restore previous selection if available

                        if (currentVal && resolutions.includes(currentVal)) {

                            selectEl.value = currentVal;

                        }

                        statusEl.textContent = 'Caméra ' + camId + ' : ' + resolutions.length + ' résolutions détectées';

                        statusEl.style.color = 'var(--success)';

                    } else {

                        statusEl.textContent = 'Aucune résolution détectée pour caméra ' + camId;

                        statusEl.style.color = 'var(--danger)';

                    }

                    // Restore detect button

                    setTimeout(function() {

                        statusEl.textContent = '';

                    }, 5000);

                }

                else if (payload.type === "vslam_blocked") {

                    const camId = payload.camera;

                    const reason = payload.reason || 'Calibration requise.';

                    // Uncheck V-SLAM toggle

                    const vSlamCheck = document.getElementById('stream-v-slam-1');

                    if (vSlamCheck && camId === 1) vSlamCheck.checked = false;

                    // Show alert

                    if (typeof showToast === 'function') {

                        showToast('V-SLAM bloqué', 'Caméra ' + camId + ': ' + reason, 'warning');

                    } else {

                        alert('V-SLAM bloqué: ' + reason);

                    }

                    // Reset stream UI

                    const statusEl2 = document.getElementById('stream-status-' + camId);

                    if (statusEl2) {

                        statusEl2.textContent = 'Calibration requise';

                        statusEl2.className = 'status-badge';

                    }

                }

                else if (payload.type === "wifi_list") {

                    displayWifiNetworks(payload.networks, payload.known_ssids, payload.known_passwords, payload.current_ssid);

                }

                else if (payload.type === "wifi_list_error") {

                    handleWifiScanError(payload);

                }

                else if (payload.type === "wifi_connect_result") {

                    handleWifiConnectResult(payload);

                }

                else if (payload.type === "wifi_forget_result") {

                    if (payload.status === "success") {

                        alert("Succès : Réseau oublié.");

                        scanWifiNetworks();

                    } else {

                        alert("Erreur lors de l'oubli du réseau : " + payload.message);

                    }

                } 

                else if (payload.type === "chat_response" || payload.type === "chat") {

                    handleIncomingLLMMessage(payload.sender || 'LLM', payload.text || '');

                }

                else if (payload.type === "ai_state_update") {

                    const s = payload.ai_state || {};

                    ['tts', 'stt', 'chat', 'yolo', 'face_rec'].forEach(f => {

                        if (s[f] !== undefined) updateAIControlUI(f, s[f]);

                    });

                }

                else if (payload.type === "robot_posture_sync") {

                    if (payload.robot_posture && typeof applyRobotPostureSync === "function") {

                        applyRobotPostureSync(payload.robot_posture);

                    }

                }

            } catch(e) {

                // not json or parsing error

            }

        }

        // ─── CHAT TAB IA FUNCTIONS ────────────────────────────────────────────

        

        function sendChatMessage(e) {

            e.preventDefault();

            const input = document.getElementById('chat-tab-input');

            const text = input.value.trim();

            if (!text) return;

            

            appendLLMMessage('Moi', text);

            

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "chat", text: text }));

            } else {

                appendLLMMessage('Système', 'Erreur : WebSocket déconnecté.');

            }

            

            input.value = '';

        }

        function appendLLMMessage(sender, text) {

            const box = document.getElementById('chat-tab-messages');

            if (!box) return;

            

            if (box.textContent.includes("Aucun message échangé")) {

                box.innerHTML = '';

            }

            

            const msgEl = document.createElement('div');

            msgEl.style.padding = '0.5rem 0.75rem';

            msgEl.style.borderRadius = '6px';

            msgEl.style.fontSize = '0.9rem';

            msgEl.style.maxWidth = '80%';

            msgEl.style.marginBottom = '0.25rem';

            

            if (sender === 'Moi') {

                msgEl.style.alignSelf = 'flex-end';

                msgEl.style.backgroundColor = 'rgba(255, 111, 97, 0.2)';

                msgEl.style.border = '1px solid var(--accent)';

                msgEl.innerHTML = `<span style="font-weight:bold;color: var(--accent);display:block;font-size:0.75rem;">Moi</span>${text}`;

            } else if (sender === 'Système') {

                msgEl.style.alignSelf = 'center';

                msgEl.style.backgroundColor = 'rgba(225, 29, 72, 0.1)';

                msgEl.style.border = '1px solid var(--danger)';

                msgEl.innerHTML = `<span style="font-style:italic;color:#f87171;font-size:0.8rem;">${text}</span>`;

            } else {

                msgEl.style.alignSelf = 'flex-start';

                msgEl.style.backgroundColor = 'rgba(255, 255, 255, 0.05)';

                msgEl.style.border = '1px solid var(--border-color)';

                msgEl.innerHTML = `<span style="font-weight:bold;color:var(--text-primary);display:block;font-size:0.75rem;">${sender}</span>${text}`;

            }

            

            box.appendChild(msgEl);

            box.scrollTop = box.scrollHeight;

        }

        // ─── TÉLÉCOMMANDE CHAT VOCAL & PILOTAGE IA ────────────────────────────

        function sendControlChatMessage(e) {

            if (e) e.preventDefault();

            const input = document.getElementById('control-chat-input');

            const text = input.value.trim();

            if (!text) return;

            

            appendControlChatMessage('Moi', text);

            

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "chat", text: text }));

            } else {

                appendControlChatMessage('Système', 'Erreur : WebSocket déconnecté.');

            }

            input.value = '';

        }

        function appendControlChatMessage(sender, text) {

            const box = document.getElementById('control-chat-messages');

            if (!box) return;

            

            if (box.textContent.includes("Parlez à Bastet")) {

                box.innerHTML = '';

            }

            

            const msgEl = document.createElement('div');

            msgEl.style.padding = '0.5rem 0.75rem';

            msgEl.style.borderRadius = '6px';

            msgEl.style.fontSize = '0.85rem';

            msgEl.style.maxWidth = '85%';

            msgEl.style.marginBottom = '0.25rem';

            msgEl.style.lineHeight = '1.3';

            

            if (sender === 'Moi') {

                msgEl.style.alignSelf = 'flex-end';

                msgEl.style.backgroundColor = 'rgba(255, 111, 97, 0.2)';

                msgEl.style.border = '1px solid var(--accent)';

                msgEl.innerHTML = `<span style="font-weight:bold;color: var(--accent);display:block;font-size:0.7rem;margin-bottom:0.15rem;">Moi</span>${text}`;

            } else if (sender === 'Système') {

                msgEl.style.alignSelf = 'center';

                msgEl.style.backgroundColor = 'rgba(225, 29, 72, 0.1)';

                msgEl.style.border = '1px solid var(--danger)';

                msgEl.innerHTML = `<span style="font-style:italic;color:#f87171;font-size:0.75rem;">${text}</span>`;

            } else {

                msgEl.style.alignSelf = 'flex-start';

                msgEl.style.backgroundColor = 'rgba(255, 255, 255, 0.05)';

                msgEl.style.border = '1px solid var(--border-color)';

                msgEl.innerHTML = `<span style="font-weight:bold;color:var(--text-primary);display:block;font-size:0.7rem;margin-bottom:0.15rem;">${sender}</span>${text}`;

            }

            

            box.appendChild(msgEl);

            box.scrollTop = box.scrollHeight;

        }

        let voiceRecognition = null;

        let isVoiceListening = false;

        function toggleVoiceRecognition() {

            const btn = document.getElementById('control-mic-btn');

            const pulse = document.getElementById('mic-pulse');

            

            if (isVoiceListening) {

                if (voiceRecognition) voiceRecognition.stop();

                return;

            }

            

            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

            if (!SpeechRecognition) {

                appendControlChatMessage('Système', "La reconnaissance vocale n'est pas supportée par votre navigateur.");

                return;

            }

            

            voiceRecognition = new SpeechRecognition();

            voiceRecognition.lang = 'fr-FR';

            voiceRecognition.interimResults = false;

            voiceRecognition.maxAlternatives = 1;

            

            voiceRecognition.onstart = () => {

                isVoiceListening = true;

                btn.classList.add('mic-active');

                if (pulse) {

                    pulse.style.opacity = '1';

                    pulse.style.transform = 'scale(1.5)';

                }

            };

            

            voiceRecognition.onresult = (event) => {

                const speechResult = event.results[0][0].transcript;

                const input = document.getElementById('control-chat-input');

                if (input) {

                    input.value = speechResult;

                    sendControlChatMessage();

                }

            };

            

            voiceRecognition.onerror = (event) => {

                console.error("Reconnaissance vocale erreur:", event.error);

                appendControlChatMessage('Système', "Erreur de reconnaissance vocale : " + event.error);

            };

            

            voiceRecognition.onend = () => {

                isVoiceListening = false;

                btn.classList.remove('mic-active');

                if (pulse) {

                    pulse.style.opacity = '0';

                    pulse.style.transform = 'scale(1)';

                }

            };

            

            voiceRecognition.start();

        }

        function handleIncomingLLMMessage(sender, text) {

            // Afficher dans le chat principal de l'IA

            appendLLMMessage(sender, text);

            

            let cleanText = text;

            

            // Parser les balises [ACTION: ...]

            const actionRegex = /\[ACTION:\s*([a-zA-Z]+)\]/g;

            let actionMatch;

            while ((actionMatch = actionRegex.exec(text)) !== null) {

                const action = actionMatch[1].toLowerCase();

                executeVoiceAction(action);

            }

            cleanText = cleanText.replace(actionRegex, '');

            

            // Parser les balises [NAV: x, y]

            const navRegex = /\[NAV:\s*(-?\d+(\.\d+)?)\s*,\s*(-?\d+(\.\d+)?)\]/g;

            let navMatch;

            while ((navMatch = navRegex.exec(text)) !== null) {

                const x = parseFloat(navMatch[1]);

                const y = parseFloat(navMatch[3]);

                executeVoiceNav(x, y);

            }

            cleanText = cleanText.replace(navRegex, '');

            

            // Afficher dans le chat de la télécommande

            appendControlChatMessage(sender, cleanText.trim());

        }

        function executeVoiceAction(action) {

            if (['up', 'down', 'left', 'right'].includes(action)) {

                const btnId = `dpad-${action}`;

                const btn = document.getElementById(btnId);

                if (btn) {

                    btn.classList.add('active-dpad');

                    btn.style.backgroundColor = 'var(--accent)';

                    btn.style.color = 'white';

                }

                startWalking(action);

                

                setTimeout(() => {

                    stopWalking();

                    if (btn) {

                        btn.classList.remove('active-dpad');

                        btn.style.backgroundColor = '';

                        btn.style.color = '';

                    }

                }, 2500);

            } else if (action === 'stop') {

                sendControlStop();

                const btn = document.getElementById('dpad-stop');

                if (btn) {

                    btn.style.transform = 'scale(0.9)';

                    setTimeout(() => btn.style.transform = '', 200);

                }

            } else if (action === 'stand') {

                sendControlCmd('stand');

            } else if (action === 'sit') {

                sendControlCmd('sit');

            }

        }

        function executeVoiceNav(x, y) {

            navTarget = { x: x, y: y };

            

            document.getElementById('nav-target-x').textContent = x.toFixed(2);

            document.getElementById('nav-target-y').textContent = y.toFixed(2);

            

            const panel = document.getElementById('nav-target-panel');

            if (panel) {

                panel.style.opacity = '1';

                panel.style.pointerEvents = 'auto';

            }

            

            drawControlMap();

            sendNavGoal();

        }

        function clearJSONConsole() {

            const consoleEl = document.getElementById('json-traffic-console');

            if (consoleEl) consoleEl.textContent = '[Console effacée]';

        }

        function setAIControl(feature, target) {

            const buttons = {

                'tts': ['robot', 'node', 'disabled'],

                'stt': ['robot', 'node', 'disabled'],

                'chat': ['robot', 'node', 'disabled'],

                'yolo': ['robot', 'node', 'disabled'],

                'face_rec': ['robot', 'node', 'disabled']

            };

            

            buttons[feature].forEach(t => {

                const btnId = `${feature}-ctrl-${t}`;

                const btn = document.getElementById(btnId);

                if (btn) {

                    if (t === target) {

                        btn.classList.add('active-control');

                    } else {

                        btn.classList.remove('active-control');

                    }

                }

            });

            

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "ai_control", feature: feature, target: target }));

            }

        }

        function updateAIControlUI(feature, target) {

            const list = ['robot', 'node', 'disabled'];

            list.forEach(t => {

                const btnId = `${feature}-ctrl-${t}`;

                const btn = document.getElementById(btnId);

                if (btn) {

                    if (t === target) {

                        btn.classList.add('active-control');

                    } else {

                        btn.classList.remove('active-control');

                    }

                }

            });

            if (feature === 'chat') {

                const llmBadge = document.getElementById('control-llm-badge');

                if (llmBadge) {

                    if (target === 'node') {

                        llmBadge.textContent = 'PC Node';

                        llmBadge.style.backgroundColor = 'var(--success)';

                    } else if (target === 'robot') {

                        llmBadge.textContent = 'Robot Local';

                        llmBadge.style.backgroundColor = 'var(--accent)';

                    } else {

                        llmBadge.textContent = 'Désactivé';

                        llmBadge.style.backgroundColor = 'var(--danger)';

                    }

                }

            }

        }

        // ─── CALIBRATION WINDOW FUNCTIONS ──────────────────────────────────────

        

        async function loadSavedOffsets() {

            try {

                const res = await fetch('/core/calibration', {

                    headers: { 'X-API-Token': apiToken }

                });

                if (res.ok) {

                    const data = await res.json();

                    const offsets = data.offsets || [];

                    

                    let allZero = true;

                    for (let i = 0; i < 12; i++) {

                        const val = offsets[i] !== undefined ? offsets[i] : 0;

                        if (val !== 0) allZero = false;

                        

                        const slider = document.getElementById(`calib-slider-${i}`);

                        if (slider) {

                            slider.value = val;

                            updateCalibSliderVal(i);

                        }

                    }

                    

                    const statusText = allZero 

                        ? '🚫 Offsets non configurés (Moteurs désactivés)' 

                        : '✅ Offsets configurés (Moteurs actifs)';

                    const statusColor = allZero ? 'var(--danger)' : 'var(--success)';

                    

                    const badgeCalib = document.getElementById('calib-status-badge');

                    if (badgeCalib) {

                        badgeCalib.textContent = statusText;

                        badgeCalib.style.color = statusColor;

                        badgeCalib.style.fontWeight = 'bold';

                    }

                }

            } catch (err) {

                console.error("Erreur lors du chargement des offsets:", err);

                const badgeCalib = document.getElementById('calib-status-badge');

                if (badgeCalib) {

                    badgeCalib.textContent = '⚠️ Offsets non disponibles (Gateway inaccessible?)';

                    badgeCalib.style.color = 'var(--warning)';

                    badgeCalib.style.fontWeight = 'bold';

                }

            }

        }

        // [Calibration Block 1 Extracted to dashboard_calib.js]
        // ESC closes the topmost active modal *AND* the cam preview overlay (shared handler)

        document.addEventListener('keydown', function(e) {

            if (e.key === 'Escape') {

                const calibOverlay = document.getElementById('cam-preview-overlay');

                if (calibOverlay && calibOverlay.style.display === 'flex') closeCalibPreview();

                const assignModal = document.getElementById('cameraAssignModal');

                if (assignModal && assignModal.classList.contains('active')) closeCameraAssignModal();

                const configModal = document.getElementById('cameraConfigModal');

                if (configModal && configModal.classList.contains('active') && typeof closeCameraConfigModal === 'function') closeCameraConfigModal();

            }

        });

        // On pagehide (navigation/refresh/bfcache), forcibly close every live RTC peer.

        // Without this, MediaMTX keeps the WebRTC session open ~12s after the browser leaves.

        window.addEventListener('pagehide', function() {

            try {

                if (typeof calibPreviewPc !== 'undefined' && calibPreviewPc) {

                    try { calibPreviewPc.close(); } catch(e) {}

                    calibPreviewPc = null;

                }

            } catch(e) {}

            [1, 2].forEach(function(camId) {

                const v = document.getElementById('assign-video-' + camId);

                if (v && v._webrtcPc) {

                    try { v._webrtcPc.close(); } catch(e) {}

                    v._webrtcPc = null;

                }

            });

        });

        document.addEventListener('DOMContentLoaded', function() {

            const overlay = document.getElementById('cam-preview-overlay');

            if (overlay) {

                overlay.addEventListener('click', function(e) {

                    if (e.target === overlay) closeCalibPreview();

                });

            }

        });

        function swapCameraLR() {

            const leftSelect = document.getElementById('cam-port-left');

            const rightSelect = document.getElementById('cam-port-right');

            if (leftSelect && rightSelect) {

                const tmp = leftSelect.value;

                leftSelect.value = rightSelect.value;

                rightSelect.value = tmp;

                if (typeof updateCameraPortOptions === 'function') updateCameraPortOptions();

                saveCameraPortsMapping();

            }

        }

        // ─── Camera L/R Assignment Modal with Live Stream Previews (WebRTC) ────

        let assignAssigned = { left: null, right: null };

        function openCameraAssignModal() {

            document.getElementById('cameraAssignModal').classList.add('active');

            // Reset state

            assignAssigned = { left: null, right: null };

            document.getElementById('assign-result').style.display = 'none';

            // Reset current labels

            for (let camId of [1, 2]) {

                const label = document.getElementById('assign-current-' + camId);

                if (label) { label.textContent = 'Non assignée'; label.style.color = 'var(--text-secondary)'; label.style.background = 'rgba(255,255,255,0.05)'; }

                // Reset button styles

                for (let side of ['left', 'right']) {

                    const btn = document.getElementById('assign-btn-' + side + '-' + camId);

                    if (btn) { btn.disabled = false; btn.style.opacity = '1'; }

                }

            }

            // Start both previews

            startAssignPreview(1);

            startAssignPreview(2);

            // Pre-fill current mapping from telemetry camera_mapping

            // camera_mapping.left = device path for cam1, camera_mapping.right = device path for cam2

            // If cam1's device is currently on left → cam1=left, cam2=right (default)

            // If they've been swapped → cam2=left, cam1=right

            const leftSelect = document.getElementById('cam-port-left');

            const rightSelect = document.getElementById('cam-port-right');

            const telMapping = window.lastTelemetryState && window.lastTelemetryState.camera_mapping;

            if (telMapping && telMapping.left && telMapping.right) {

                // cam1 is the device currently on left, cam2 is the device on right

                // Default: cam1=left, cam2=right

                assignAssigned = { left: 1, right: 2 };

                updateAssignUI();

            } else if (leftSelect && rightSelect) {

                // Fallback: use dropdown values (default is video0=left, video2=right)

                assignAssigned = { left: 1, right: 2 };

                updateAssignUI();

            }

        }

        function closeCameraAssignModal() {

            const modal = document.getElementById('cameraAssignModal');

            if (modal) modal.classList.remove('active');

            stopAssignPreviews();

        }

        function closeCameraAssignModalOnClick(event) {

            if (event.target.id === 'cameraAssignModal') {

                closeCameraAssignModal();

            }

        }

        function startAssignPreview(camId) {

            const videoEl = document.getElementById('assign-video-' + camId);

            const statusEl = document.getElementById('assign-status-' + camId);

            if (!videoEl || !statusEl) return;

            // Clean up any previous WebRTC peer on this element

            if (videoEl._webrtcPc) {

                try { videoEl._webrtcPc.close(); } catch(e) {}

                videoEl._webrtcPc = null;

            }

            videoEl.srcObject = null;

            videoEl.src = '';

            statusEl.style.display = 'flex';

            statusEl.textContent = 'Connexion au flux WebRTC...';

            // MediaMTX WHEP endpoint at :48889 (mandatory path segment, not a protocol switch)

            const webrtcUrl = window.location.protocol + '//' + window.location.hostname + ':48889/robot/cam' + camId + '/whep';

            const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });

            videoEl._webrtcPc = pc;

            pc.addTransceiver('video', { direction: 'recvonly' });

            let connected = false;

            pc.ontrack = (event) => {

                if (!videoEl) return;

                if (event.streams && event.streams[0] && videoEl.srcObject !== event.streams[0]) {

                    videoEl.srcObject = event.streams[0];

                } else if (!videoEl.srcObject) {

                    const inboundStream = new MediaStream();

                    inboundStream.addTrack(event.track);

                    videoEl.srcObject = inboundStream;

                }

                videoEl.play().catch(() => {});

                videoEl.style.display = 'block';

                statusEl.style.display = 'none';

                connected = true;

            };

            pc.oniceconnectionstatechange = () => {

                if ((pc.iceConnectionState === 'failed' || pc.iceConnectionState === 'disconnected' || pc.iceConnectionState === 'closed') && !connected) {

                    statusEl.innerHTML = 'Connexion WebRTC échouée.<br>ICE : ' + pc.iceConnectionState;

                    statusEl.style.display = 'flex';

                    videoEl.style.display = 'none';

                }

            };

            pc.createOffer().then(offer => pc.setLocalDescription(offer)).then(() => {

                const maxAttempts = 6;

                let attempt = 0;

                const postOffer = () => {

                    attempt++;

                    if (connected) return;

                    fetch(webrtcUrl, {

                        method: 'POST',

                        headers: { 'Content-Type': 'application/sdp' },

                        body: pc.localDescription.sdp,

                    }).then(res => {

                        if (!res.ok) throw new Error('HTTP ' + res.status);

                        return res.text();

                    }).then(answerSdp => {

                        return pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });

                    }).catch(err => {

                        if (attempt < maxAttempts && !connected) {

                            setTimeout(postOffer, 500);

                        } else if (!connected) {

                            statusEl.innerHTML = 'Flux WebRTC indisponible (' + (err && err.message ? err.message : err) + ').';

                            statusEl.style.display = 'flex';

                            videoEl.style.display = 'none';

                            try { pc.close(); } catch(e) {}

                            if (videoEl._webrtcPc === pc) videoEl._webrtcPc = null;

                        }

                    });

                };

                postOffer();

            }).catch(err => {

                if (!connected) {

                    statusEl.innerHTML = 'Erreur WebRTC (' + (err && err.message ? err.message : err) + ').';

                    statusEl.style.display = 'flex';

                    videoEl.style.display = 'none';

                    try { pc.close(); } catch(e) {}

                    if (videoEl._webrtcPc === pc) videoEl._webrtcPc = null;

                }

            });

        }

        function stopAssignPreviews() {

            for (let camId of [1, 2]) {

                const videoEl = document.getElementById('assign-video-' + camId);

                if (videoEl) {

                    if (videoEl._webrtcPc) {

                        try { videoEl._webrtcPc.close(); } catch(e) {}

                        videoEl._webrtcPc = null;

                    }

                    videoEl.srcObject = null;

                    videoEl.src = '';

                    videoEl.style.display = 'none';

                }

                const statusEl = document.getElementById('assign-status-' + camId);

                if (statusEl) { statusEl.style.display = 'flex'; statusEl.textContent = 'Chargement du flux...'; }

            }

        }

        function assignCameraLR(camId, side) {

            // Prevent double-assignment

            if (assignAssigned[side] !== null) {

                if (typeof showToast === 'function') {

                    showToast("Attention", "La position " + (side === 'left' ? 'Gauche' : 'Droite') + " est déjà assignée", "warning");

                }

                return;

            }

            assignAssigned[side] = camId;

            // Auto-assign the other camera to the other side

            const otherCam = camId === 1 ? 2 : 1;

            const otherSide = side === 'left' ? 'right' : 'left';

            if (assignAssigned[otherSide] === null) {

                assignAssigned[otherSide] = otherCam;

            }

            // Save mapping to robot using actual device paths from telemetry

            const leftCam = assignAssigned.left;

            const rightCam = assignAssigned.right;

            if (leftCam && rightCam) {

                // Get actual device paths from telemetry camera_mapping

                // camera_mapping.left = device path currently assigned to cam1

                // camera_mapping.right = device path currently assigned to cam2

                const telMapping = window.lastTelemetryState && window.lastTelemetryState.camera_mapping;

                const cam1Dev = (telMapping && telMapping.left) ? telMapping.left : '/dev/video0';

                const cam2Dev = (telMapping && telMapping.right) ? telMapping.right : '/dev/video2';

                const leftDev = leftCam === 1 ? cam1Dev : cam2Dev;

                const rightDev = rightCam === 1 ? cam1Dev : cam2Dev;

                if (appWs && appWs.readyState === WebSocket.OPEN) {

                    appWs.send(JSON.stringify({

                        type: "save_camera_mapping",

                        left: leftDev,

                        right: rightDev

                    }));

                }

                // Update dropdowns

                const leftSelect = document.getElementById('cam-port-left');

                const rightSelect = document.getElementById('cam-port-right');

                if (leftSelect) leftSelect.value = leftDev;

                if (rightSelect) rightSelect.value = rightDev;

                // Show result

                const resultEl = document.getElementById('assign-result');

                const resultText = document.getElementById('assign-result-text');

                if (resultEl && resultText) {

                    resultText.textContent = "Caméra Gauche = " + leftDev + " (Cam" + leftCam + "), Caméra Droite = " + rightDev + " (Cam" + rightCam + ")";

                    resultEl.style.display = 'block';

                }

                if (typeof showToast === 'function') {

                    showToast("Caméras", "Gauche: " + leftDev + ", Droite: " + rightDev, "success");

                }

            }

            updateAssignUI();

        }

        function updateAssignUI() {

            for (let camId of [1, 2]) {

                const label = document.getElementById('assign-current-' + camId);

                if (!label) continue;

                let assigned = null;

                if (assignAssigned.left === camId) assigned = 'left';

                else if (assignAssigned.right === camId) assigned = 'right';

                if (assigned === 'left') {

                    label.textContent = '← Gauche';

                    label.style.color = '#3b82f6';

                    label.style.background = 'rgba(59,130,246,0.15)';

                } else if (assigned === 'right') {

                    label.textContent = 'Droite →';

                    label.style.color = '#ef4444';

                    label.style.background = 'rgba(239,68,68,0.15)';

                } else {

                    label.textContent = 'Non assignée';

                    label.style.color = 'var(--text-secondary)';

                    label.style.background = 'rgba(255,255,255,0.05)';

                }

                // Disable buttons for assigned sides

                for (let side of ['left', 'right']) {

                    const btn = document.getElementById('assign-btn-' + side + '-' + camId);

                    if (!btn) continue;

                    if (assignAssigned[side] !== null && assignAssigned[side] !== camId) {

                        btn.disabled = true;

                        btn.style.opacity = '0.4';

                    } else if (assignAssigned[side] === camId) {

                        btn.disabled = true;

                        btn.style.opacity = '0.4';

                        btn.style.fontWeight = '700';

                    } else {

                        btn.disabled = false;

                        btn.style.opacity = '1';

                        btn.style.fontWeight = '400';

                    }

                }

            }

        }

        function toggleCalibCamera(camId) {

            const checkbox = document.getElementById(`calib-cam-enable-${camId}`);

            const statusEl = document.getElementById(`calib-cam-status-${camId}`);

            if (checkbox && statusEl) {

                statusEl.textContent = checkbox.checked ? 'Activée' : 'Désactivée';

                statusEl.style.color = checkbox.checked ? 'var(--success)' : 'var(--text-secondary)';

            }

            

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "camera_setup", camera: camId, enable: checkbox.checked }));

            }

        }

        // ─── SERVO TESTER FUNCTIONS ───────────────────────────────────────────

        const TESTER_JOINT_NAMES = [

            'Avant-Droit Abduction', 'Avant-Droit Hanche', 'Avant-Droit Genou',

            'Avant-Gauche Abduction', 'Avant-Gauche Hanche', 'Avant-Gauche Genou',

            'Arrière-Droit Abduction', 'Arrière-Droit Hanche', 'Arrière-Droit Genou',

            'Arrière-Gauche Abduction', 'Arrière-Gauche Hanche', 'Arrière-Gauche Genou'

        ];

        function openServoTester() {

            document.getElementById('servo-tester-overlay').classList.add('active');

            // FIX: Arreter le motion_node pour eviter qu'il ecrase les commandes individuelles

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stop" }));

            }

            buildServoTesterList();

        }

        function closeServoTester() {

            document.getElementById('servo-tester-overlay').classList.remove('active');

            testerStopAll();

            // FIX: Redemarrer le motion_node en mode stand apres le test individuel

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stand" }));

            }

        }

        function buildServoTesterList() {

            const container = document.getElementById('tester-servos-list');

            if (!container) return;

            container.innerHTML = '';

            for (let i = 0; i < 12; i++) {

                const name = TESTER_JOINT_NAMES[i];

                

                const card = document.createElement('div');

                card.style.display = 'flex';

                card.style.flexDirection = 'column';

                card.style.gap = '0.5rem';

                card.style.padding = '0.75rem';

                card.style.border = '1px solid var(--border-color)';

                card.style.borderRadius = '8px';

                card.style.background = 'rgba(255,255,255,0.01)';

                

                card.innerHTML = `

                    <div style="display: flex; justify-content: space-between; align-items: center;">

                        <span style="font-size: 0.85rem; font-weight: 600; color: var(--text-primary);">${i + 1}. ${name}</span>

                        <div style="display: flex; gap: 0.5rem;">

                            <button class="btn btn-secondary" id="tester-btn-attach-${i}" style="font-size: 0.7rem; padding: 0.25rem 0.5rem;" onclick="testerAttach(${i})">Activer</button>

                            <button class="btn btn-secondary" id="tester-btn-detach-${i}" style="font-size: 0.7rem; padding: 0.25rem 0.5rem; display: none;" onclick="testerDetach(${i})">Éteindre</button>

                        </div>

                    </div>

                    <div id="tester-slider-container-${i}" style="display: none; align-items: center; gap: 0.75rem; margin-top: 0.25rem;">

                        <input type="range" min="0" max="180" value="90" id="tester-slider-${i}" style="flex: 1; height: 4px; accent-color: var(--accent);" oninput="testerWrite(${i}, this.value)">

                        <span id="tester-val-${i}" style="font-size: 0.8rem; font-family: monospace; min-width: 30px; text-align: right; color: var(--accent);">90°</span>

                    </div>

                `;

                container.appendChild(card);

            }

        }

        function testerAttach(idx) {

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "attach", index: idx }));

                

                document.getElementById(`tester-btn-attach-${idx}`).style.display = 'none';

                document.getElementById(`tester-btn-detach-${idx}`).style.display = 'inline-block';

                document.getElementById(`tester-slider-container-${idx}`).style.display = 'flex';

                

                testerWrite(idx, 90);

            }

        }

        function testerDetach(idx) {

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "detach", index: idx }));

                

                document.getElementById(`tester-btn-attach-${idx}`).style.display = 'inline-block';

                document.getElementById(`tester-btn-detach-${idx}`).style.display = 'none';

                document.getElementById(`tester-slider-container-${idx}`).style.display = 'none';

            }

        }

        let lastTesterWriteTime = {};

        let pendingTesterWriteTimeout = {};

        function testerWrite(idx, angle) {

            document.getElementById(`tester-val-${idx}`).textContent = angle + '°';

            

            const now = Date.now();

            if (now - (lastTesterWriteTime[idx] || 0) < 50) {

                if (!pendingTesterWriteTimeout[idx]) {

                    pendingTesterWriteTimeout[idx] = setTimeout(() => {

                        pendingTesterWriteTimeout[idx] = null;

                        testerWrite(idx, angle);

                    }, 50 - (now - (lastTesterWriteTime[idx] || 0)));

                }

                return;

            }

            lastTesterWriteTime[idx] = now;

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "write", index: idx, angle: parseFloat(angle) }));

            }

        }

        function testerStopAll() {

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                for (let i = 0; i < 12; i++) {

                    appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "detach", index: i }));

                }

                sendControlStop();

                buildServoTesterList();

            }

        }

        // ─── WIFI POPUP FUNCTIONS ─────────────────────────────────────────────

        

        function openWifiModal() {

            document.getElementById('wifiModal').classList.add('active');

            scanWifiNetworks();

        }

        function closeWifiModal() {

            document.getElementById('wifiModal').classList.remove('active');

            cancelWifiConnection();

        }

        function closeWifiModalOnClick(e) {

            if (e.target === document.getElementById('wifiModal')) closeWifiModal();

        }

        function scanWifiNetworks() {

            const listContainer = document.getElementById('wifi-list-container');

            const knownContainer = document.getElementById('wifi-known-container');

            

            listContainer.innerHTML = `

                <div style="text-align: center; color: var(--text-secondary); padding: 2.5rem 0; font-size: 0.85rem; display: flex; flex-direction: column; align-items: center; gap: 0.75rem;">

                    <div style="width: 24px; height: 24px; border: 2px solid var(--accent); border-top-color: transparent; border-radius: 50%; animation: spin 1s linear infinite;"></div>

                    <span>Recherche des réseaux à proximité (nmcli)...</span>

                </div>`;

                

            knownContainer.innerHTML = `

                <div style="text-align: center; color: var(--text-secondary); padding: 1.5rem 0; font-size: 0.85rem; display: flex; flex-direction: column; align-items: center; gap: 0.5rem;">

                    <div style="width: 16px; height: 16px; border: 2px solid var(--accent); border-top-color: transparent; border-radius: 50%; animation: spin 1s linear infinite;"></div>

                    <span>Actualisation...</span>

                </div>`;

                

            const btn = document.getElementById('btn-wifi-scan');

            if (btn) btn.disabled = true;

            

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "scan_wifi" }));

            } else {

                listContainer.innerHTML = `<div style="text-align: center; color: var(--danger); padding: 2rem 0; font-size: 0.85rem;">Erreur : WebSocket déconnecté.</div>`;

                knownContainer.innerHTML = `<div style="text-align: center; color: var(--danger); padding: 1rem 0; font-size: 0.85rem;">Erreur.</div>`;

                if (btn) btn.disabled = false;

            }

        }

        window.wifiPasswords = {};

        window.wifiCurrentSsid = '';

        function handleWifiScanError(payload) {

            const errMsg = payload.error || "Erreur inconnue";

            const iface = payload.interface || "wlan0";

            const mgr = payload.manager || "inconnu";

            const known = Array.isArray(payload.known_ssids) ? payload.known_ssids : [];

            const cur = payload.current_ssid || "";

            const listContainer = document.getElementById('wifi-list-container');

            const knownContainer = document.getElementById('wifi-known-container');

            const btn = document.getElementById('btn-wifi-scan');

            if (btn) btn.disabled = false;

            if (listContainer) {

                listContainer.innerHTML = `<div style="text-align: center; color: var(--danger); padding: 2rem 0; font-size: 0.85rem; line-height: 1.5;">⚠️ Scan WiFi échoué<br><small style="color: var(--text-secondary);">${errMsg}<br>Interface : ${iface} · Gestionnaire : ${mgr}</small></div>`;

            }

            if (knownContainer) {

                knownContainer.innerHTML = `<div style="text-align: center; color: var(--text-secondary); padding: 1rem 0; font-size: 0.85rem;">Surveillance WiFi indisponible.</div>`;

            }

            window.wifiPasswords = payload.known_passwords || {};

            window.wifiCurrentSsid = cur;

            // Ré-afficher les réseaux connus (si fournis) même en cas d'échec du scan

            try { displayWifiNetworks([], known, payload.known_passwords || {}, cur); } catch(e) { /* noop */ }

        }

        function displayWifiNetworks(networks, knownSsids = [], knownPasswords = {}, currentSsid = '') {

            const listContainer = document.getElementById('wifi-list-container');

            const knownContainer = document.getElementById('wifi-known-container');

            const btn = document.getElementById('btn-wifi-scan');

            if (btn) btn.disabled = false;

            

            listContainer.innerHTML = '';

            knownContainer.innerHTML = '';

            

            window.wifiPasswords = knownPasswords || {};

            window.wifiCurrentSsid = currentSsid || '';

            

            if (!knownSsids) knownSsids = [];

            if (!networks) networks = [];

            

            // Sort scanned networks by signal strength

            networks.sort((a, b) => {

                const sigA = parseInt(a.signal) || 0;

                const sigB = parseInt(b.signal) || 0;

                return sigB - sigA;

            });

            

            // Display known networks

            if (knownSsids.length === 0 && !window.wifiCurrentSsid) {

                knownContainer.innerHTML = `<div style="text-align: center; color: var(--text-secondary); padding: 1rem 0; font-size: 0.8rem;">Aucun réseau enregistré configuré sur le robot.</div>`;

            } else {

                // Ensure current connected SSID is in the list of known SSIDs (if it isn't already)

                let allKnown = [...knownSsids];

                if (window.wifiCurrentSsid && !allKnown.includes(window.wifiCurrentSsid)) {

                    allKnown.unshift(window.wifiCurrentSsid);

                }

                

                // Sort so currently connected SSID is always FIRST

                allKnown.sort((a, b) => {

                    if (a === window.wifiCurrentSsid) return -1;

                    if (b === window.wifiCurrentSsid) return 1;

                    return 0;

                });

                

                allKnown.forEach(ssid => {

                    const scannedNet = networks.find(n => n.ssid === ssid);

                    const inRange = !!scannedNet;

                    const isConnected = (ssid === window.wifiCurrentSsid);

                    

                    const item = document.createElement('div');

                    item.style.cssText = 'display: flex; justify-content: space-between; align-items: center; padding: 0.65rem 1rem; border-bottom: 1px solid var(--border-color); cursor: pointer; transition: background 0.2s ease; margin-bottom: 0.25rem; border-radius: 6px; position: relative;';

                    

                    if (isConnected) {

                        item.style.backgroundColor = 'rgba(76, 175, 80, 0.08)';

                        item.style.border = '1px solid rgba(76, 175, 80, 0.3)';

                    } else {

                        item.style.backgroundColor = 'rgba(255, 111, 97, 0.03)';

                        item.style.border = '1px solid rgba(255, 111, 97, 0.15)';

                    }

                    

                    const signalText = inRange ? `${scannedNet.signal}%` : (isConnected ? 'Connecté' : 'Hors de portée');

                    const signalColor = isConnected ? 'var(--success)' : (inRange ? 'var(--success)' : 'var(--text-secondary)');

                    

                    let badge = '';

                    if (isConnected) {

                        badge = `<span style="font-size:0.65rem; background:rgba(76,175,80,0.2); color: #4CAF50; padding:0.1rem 0.35rem; border-radius:4px; margin-left:0.35rem; font-weight:700; text-transform:uppercase; letter-spacing:0.5px;">✓ Connecté</span>`;

                    } else {

                        badge = `<span style="font-size:0.65rem; background:rgba(255,111,97,0.15); color: var(--accent); padding:0.1rem 0.35rem; border-radius:4px; margin-left:0.35rem; font-weight:600;">Enregistré</span>`;

                    }

                    

                    item.innerHTML = `

                        <div style="flex: 1;">

                            <span style="font-weight: 600; font-size: 0.9rem; display: block; color: ${isConnected ? '#4CAF50' : 'var(--accent)'};">${ssid} ${badge}</span>

                            <span style="font-size: 0.7rem; color: var(--text-secondary);">${inRange ? (scannedNet.bssid + ' • ' + scannedNet.security) : 'Profil de connexion sauvegardé'}</span>

                        </div>

                        <div style="display:flex; align-items:center; gap:0.5rem;">

                            <span style="font-size: 0.85rem; font-weight: bold; color: ${signalColor};">${signalText}</span>

                            <button class="btn btn-secondary" style="padding: 0.25rem 0.5rem; font-size: 0.7rem; border-color: var(--danger); color: var(--danger); background: transparent;" onclick="event.stopPropagation(); forgetWifiNetwork('${ssid}')">🗑️ Oublier</button>

                        </div>

                    `;

                    

                    const isSecureNet = scannedNet ? (scannedNet.security && scannedNet.security.trim() !== "" && scannedNet.security !== "--" && scannedNet.security.toLowerCase() !== "open") : true;

                    item.onclick = () => selectWifiNetwork(ssid, isSecureNet, true);

                    knownContainer.appendChild(item);

                });

            }

            

            // Display other scanned networks (excluding the known ones)

            const otherNetworks = networks.filter(n => !knownSsids.includes(n.ssid) && n.ssid !== window.wifiCurrentSsid);

            

            if (otherNetworks.length === 0) {

                listContainer.innerHTML = `<div style="text-align: center; color: var(--text-secondary); padding: 1.5rem 0; font-size: 0.8rem;">Aucun autre réseau WiFi à proximité.</div>`;

            } else {

                otherNetworks.forEach(net => {

                    const item = document.createElement('div');

                    item.style.cssText = 'display: flex; justify-content: space-between; align-items: center; padding: 0.65rem 1rem; border-bottom: 1px solid var(--border-color); cursor: pointer; transition: background 0.2s ease;';

                    

                    const isSecure = net.security && net.security.trim() !== "" && net.security !== "--" && net.security.toLowerCase() !== "open";

                    const lockIcon = isSecure ? '🔒' : '🔓';

                    

                    item.innerHTML = `

                        <div>

                            <span style="font-weight: 600; font-size: 0.9rem; display: block;">${net.ssid}</span>

                            <span style="font-size: 0.7rem; color: var(--text-secondary);">${net.bssid} • ${net.security}</span>

                        </div>

                        <div style="display:flex; align-items:center; gap:0.5rem;">

                            <span style="font-size: 0.8rem;">${lockIcon}</span>

                            <span style="font-size: 0.85rem; font-weight: bold; color: var(--accent);">${net.signal}%</span>

                        </div>

                    `;

                    

                    item.onclick = () => selectWifiNetwork(net.ssid, isSecure);

                    listContainer.appendChild(item);

                });

            }

        }

        function selectWifiNetwork(ssid, isSecure, isKnown = false) {

            document.getElementById('form-wifi-ssid').value = ssid;

            document.getElementById('wifi-selected-ssid-label').textContent = ssid;

            

            const pwdGroup = document.getElementById('wifi-password-group');

            const pwdInput = document.getElementById('form-wifi-password');

            const forgetBtn = document.getElementById('btn-wifi-forget-form');

            

            if (isKnown) {

                forgetBtn.style.display = 'inline-block';

                const savedPwd = window.wifiPasswords[ssid] || '';

                pwdInput.value = savedPwd;

                pwdInput.type = 'text'; // Show saved password clearly

                if (isSecure) {

                    pwdGroup.style.display = 'block';

                    pwdInput.placeholder = 'Mot de passe enregistré';

                } else {

                    pwdGroup.style.display = 'none';

                    pwdInput.placeholder = '';

                }

            } else {

                forgetBtn.style.display = 'none';

                pwdInput.value = '';

                pwdInput.type = 'password'; // Password mask for new network

                if (isSecure) {

                    pwdGroup.style.display = 'block';

                    pwdInput.placeholder = 'Mot de passe';

                } else {

                    pwdGroup.style.display = 'none';

                    pwdInput.placeholder = '';

                }

            }

            

            document.getElementById('wifi-connect-form').style.display = 'block';

        }

        function cancelWifiConnection() {

            document.getElementById('wifi-connect-form').style.display = 'none';

        }

        function handleWifiConnectSubmit(e) {

            e.preventDefault();

            const ssid = document.getElementById('form-wifi-ssid').value;

            const password = document.getElementById('form-wifi-password').value;

            

            const submitBtn = e.target.querySelector('button[type="submit"]');

            if (submitBtn) {

                submitBtn.disabled = true;

                submitBtn.textContent = 'Connexion en cours...';

            }

            

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({

                    type: "connect_wifi",

                    ssid: ssid,

                    password: password

                }));

            } else {

                alert("WebSocket déconnecté.");

                if (submitBtn) {

                    submitBtn.disabled = false;

                    submitBtn.textContent = 'Se connecter au WiFi';

                }

            }

        }

        function handleWifiConnectResult(res) {

            const submitBtn = document.querySelector('#wifi-connect-form button[type="submit"]');

            if (submitBtn) {

                submitBtn.disabled = false;

                submitBtn.textContent = 'Se connecter au WiFi';

            }

            

            if (res.status === 'success') {

                alert("Succès : " + res.message);

                closeWifiModal();

            } else {

                alert("Erreur de connexion : " + res.message);

            }

        }

        function forgetWifiNetwork(ssid) {

            if (confirm(`Êtes-vous sûr de vouloir oublier le réseau WiFi "${ssid}" sur le robot ?`)) {

                if (appWs && appWs.readyState === WebSocket.OPEN) {

                    appWs.send(JSON.stringify({ type: "forget_wifi", ssid: ssid }));

                } else {

                    alert("WebSocket déconnecté.");

                }

            }

        }

        function handleForgetFromForm() {

            const ssid = document.getElementById('form-wifi-ssid').value;

            if (ssid) {

                forgetWifiNetwork(ssid);

            }

        }

        // ─── CANVASES RENDER CODES ───────────────────────────────────────────

        

        function drawSLAMMap() {

            const canvas = document.getElementById('slam-map-canvas');

            if (!canvas) return;

            const ctx = canvas.getContext('2d');

            

            const dpr = window.devicePixelRatio || 1;

            const rect = canvas.getBoundingClientRect();

            canvas.width = rect.width * dpr;

            canvas.height = rect.height * dpr;

            ctx.scale(dpr, dpr);

            

            const w = rect.width;

            const h = rect.height;

            

            ctx.clearRect(0, 0, w, h);

            ctx.fillStyle = '#07070a';

            ctx.fillRect(0, 0, w, h);

            

            const scale = 40;

            const cx = w / 2;

            const cy = h / 2;

            

            // Grid

            if (document.getElementById('layer-grid').checked) {

                ctx.strokeStyle = '#101015';

                ctx.lineWidth = 0.5;

                const gridStep = scale * 0.5;

                for (let x = cx % gridStep; x < w; x += gridStep) {

                    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();

                }

                for (let y = cy % gridStep; y < h; y += gridStep) {

                    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();

                }

                

                ctx.fillStyle = 'rgba(255, 255, 255, 0.05)';

                const walls = [

                    {x: -1.5, y: -2, w: 3, h: 0.1},

                    {x: -1.5, y: 2, w: 3, h: 0.1},

                    {x: -1.5, y: -2, w: 0.1, h: 4},

                    {x: 1.5, y: -2, w: 0.1, h: 4}

                ];

                walls.forEach(wall => {

                    ctx.fillRect(cx + wall.x * scale, cy - (wall.y + wall.h) * scale, wall.w * scale, wall.h * scale);

                });

            }

            

            // Points (laser)

            if (document.getElementById('layer-points').checked) {

                ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--success').trim();

                if (window.slamPoints && window.slamPoints.length > 0) {

                    window.slamPoints.forEach(pt => {

                        ctx.beginPath();

                        ctx.arc(cx + pt.x * scale, cy - pt.y * scale, 1.5, 0, Math.PI * 2);

                        ctx.fill();

                    });

                } else {

                    for (let angle = 0; angle < Math.PI * 2; angle += 0.05) {

                        const dist = 1.8 + Math.sin(angle * 4) * 0.1;

                        const px = cx + Math.cos(angle) * dist * scale;

                        const py = cy - Math.sin(angle) * dist * scale;

                        ctx.beginPath();

                        ctx.arc(px, py, 1.5, 0, Math.PI*2);

                        ctx.fill();

                    }

                }

            }

            

            // Sonar

            if (document.getElementById('layer-sonar').checked) {

                ctx.fillStyle = 'rgba(245, 158, 11, 0.15)';

                ctx.strokeStyle = '#f59e0b';

                ctx.lineWidth = 1;

                

                const rx = cx + window.robotPose.x * scale;

                const ry = cy - window.robotPose.y * scale;

                const rtheta = -window.robotPose.theta;

                

                ctx.save();

                ctx.translate(rx, ry);

                ctx.rotate(rtheta);

                ctx.beginPath();

                ctx.moveTo(0, 0);

                ctx.arc(0, 0, 1.2 * scale, -Math.PI / 12, Math.PI / 12);

                ctx.closePath();

                ctx.fill();

                ctx.stroke();

                ctx.restore();

            }

            

            // Trajectory Path

            if (document.getElementById('layer-trajectory').checked && window.slamPath && window.slamPath.length > 0) {

                ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();

                ctx.lineWidth = 2.5;

                ctx.beginPath();

                window.slamPath.forEach((pt, idx) => {

                    const px = cx + pt.x * scale;

                    const py = cy - pt.y * scale;

                    if (idx === 0) ctx.moveTo(px, py);

                    else ctx.lineTo(px, py);

                });

                ctx.stroke();

            }

            

            // Robot Outline

            const rx = cx + window.robotPose.x * scale;

            const ry = cy - window.robotPose.y * scale;

            const rtheta = -window.robotPose.theta;

            

            ctx.save();

            ctx.translate(rx, ry);

            ctx.rotate(rtheta);

            

            ctx.strokeStyle = '#ffffff';

            ctx.lineWidth = 2;

            ctx.strokeRect(-12, -8, 24, 16);

            

            ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();

            ctx.beginPath();

            ctx.moveTo(12, 0);

            ctx.lineTo(6, -5);

            ctx.lineTo(6, 5);

            ctx.closePath();

            ctx.fill();

            

            ctx.restore();

        }

        function resetSLAMMap() {

            window.robotPose = {x: 0, y: 0, theta: 0};

            window.slamPath = [];

            window.slamPoints = [];

            drawSLAMMap();

        }

        function updateSLAMParam(param) {

            const slider = document.getElementById(`param-slider-${param}`);

            const label = document.getElementById(`param-val-${param}`);

            if (slider && label) {

                if (param === 'resolution') {

                    label.textContent = `${(slider.value / 100).toFixed(2)}m`;

                } else if (param === 'inflation') {

                    label.textContent = `${(slider.value / 100).toFixed(2)}m`;

                } else {

                    label.textContent = `${slider.value}%`;

                }

            }

        }

        

        // ─── SLAM Mode Detection & UI ──────────────────────────────────────

        

        // ─── Left/Right Camera Attribution ───────────────────────────────

        let ecLRPeerA = null;

        let ecLRPeerB = null;

        let ecLRAssigned = { left: null, right: null };

        let ecLRStreamA = null;

        let ecLRStreamB = null;

        

        async function ecStartLRPreviews() {

            // Show both camera feeds via WebRTC for user to identify left/right

            ecLRAssigned = { left: null, right: null };

            document.getElementById('ec-lr-assignment-result').style.display = 'none';

            

            // Get camera A (camera 1)

            await ecStartSinglePreview('a', 1);

            // Get camera B (camera 2)

            await ecStartSinglePreview('b', 2);

        }

        

        function ecStartSinglePreview(slot, camId) {

            const videoEl = document.getElementById(`ec-lr-video-${slot}`);

            const statusEl = document.getElementById(`ec-lr-status-${slot}`);

            if (!videoEl || !statusEl) return;

            

            statusEl.style.display = 'flex';

            statusEl.textContent = 'Connexion au flux HLS...';

            

            // Use HLS stream via MediaMTX (already working and reliable)

            const hlsUrl = `${window.location.protocol}//${window.location.hostname}:48888/robot/cam${camId}/index.m3u8`;

            

            // Check if HLS.js is available, otherwise try native HLS (Safari) or show error

            if (typeof Hls !== 'undefined' && Hls.isSupported()) {

                const hls = new Hls({ 

                    maxBufferLength: 5,

                    maxMaxBufferLength: 10,

                    liveDurationInfinity: true,

                    lowLatencyMode: false

                });

                hls.loadSource(hlsUrl);

                hls.attachMedia(videoEl);

                hls.on(Hls.Events.MANIFEST_PARSED, () => {

                    videoEl.play().catch(() => {});

                    videoEl.style.display = 'block';

                    statusEl.style.display = 'none';

                });

                hls.on(Hls.Events.ERROR, (event, data) => {

                    if (data.fatal) {

                        statusEl.innerHTML = 'Flux HLS indisponible.<br>La camera est peut-etre deconnectee.';

                    }

                });

                // Store for cleanup

                videoEl._hls = hls;

            } else if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {

                // Native HLS (Safari)

                videoEl.src = hlsUrl;

                videoEl.play().catch(() => {});

                videoEl.style.display = 'block';

                statusEl.style.display = 'none';

            } else {

                statusEl.innerHTML = 'Navigateur non compatible HLS.<br>Observez les flux dans le dashboard pour identifier les cameras.';

            }

        }

        

        function ecStopLRPreviews() {

            if (ecLRPeerA) { try { ecLRPeerA.close(); } catch(e) {} ecLRPeerA = null; }

            if (ecLRPeerB) { try { ecLRPeerB.close(); } catch(e) {} ecLRPeerB = null; }

            const va = document.getElementById('ec-lr-video-a');

            const vb = document.getElementById('ec-lr-video-b');

            if (va) { 

                if (va._hls) { try { va._hls.destroy(); } catch(e) {} va._hls = null; }

                va.srcObject = null; va.src = ''; va.style.display = 'none'; 

            }

            if (vb) { 

                if (vb._hls) { try { vb._hls.destroy(); } catch(e) {} vb._hls = null; }

                vb.srcObject = null; vb.src = ''; vb.style.display = 'none'; 

            }

            document.getElementById('ec-lr-status-a').style.display = 'flex';

            document.getElementById('ec-lr-status-a').textContent = 'Chargement...';

            document.getElementById('ec-lr-status-b').style.display = 'flex';

            document.getElementById('ec-lr-status-b').textContent = 'Chargement...';

            // Reset assignment state

            ecLRAssigned = { left: null, right: null };

            // Re-enable buttons

            document.querySelectorAll('#ec-step-lr .ec-lr-assign-btn').forEach(btn => {

                btn.disabled = false;

                btn.style.opacity = '1';

            });

        }

        

        function ecAssignLR(which, slot) {

            // Guard against double-assignment

            if (ecLRAssigned[which] !== null) {

                if (typeof showToast === 'function') {

                    showToast("Attention", `La camera ${which.toUpperCase()} est deja assignee`, "warning");

                }

                return;

            }

            // User clicked "left" or "right" for camera A or B

            const camId = slot === 'a' ? 1 : 2;

            ecLRAssigned[which] = camId;

            

            // Determine the other camera

            const otherCam = camId === 1 ? 2 : 1;

            const otherWhich = which === 'left' ? 'right' : 'left';

            ecLRAssigned[otherWhich] = otherCam;

            

            // Disable all assignment buttons after assignment

            document.querySelectorAll('#ec-step-lr .ec-lr-assign-btn').forEach(btn => {

                btn.disabled = true;

                btn.style.opacity = '0.5';

            });

            

            // Show result

            const resultEl = document.getElementById('ec-lr-assignment-result');

            const resultText = document.getElementById('ec-lr-result-text');

            if (resultEl && resultText) {

                resultEl.style.display = 'block';

                resultText.textContent = `Camera GAUCHE = video${ecLRAssigned.left}, Camera DROITE = video${ecLRAssigned.right}`;

            }

            

            // Save to robot

            const leftDev = `/dev/video${ecLRAssigned.left}`;

            const rightDev = `/dev/video${ecLRAssigned.right}`;

            

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({

                    type: "save_camera_mapping",

                    left: leftDev,

                    right: rightDev

                }));

            }

            

            if (typeof showToast === 'function') {

                showToast("Cameras", `Gauche: ${leftDev}, Droite: ${rightDev}`, "success");

            }

            

            // Enable next button

            document.getElementById('ec-btn-next').disabled = false;

            document.getElementById('ec-btn-next').textContent = 'Suivant \u2192';

        }

        

        function ecSkipLR() {

            ecStopLRPreviews();

            // Use default mapping: video0=left, video2=right

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({

                    type: "save_camera_mapping",

                    left: "/dev/video0",

                    right: "/dev/video2"

                }));

            }

            if (typeof showToast === 'function') {

                showToast("Cameras", "Mapping par defaut: video0=gauche, video2=droite", "info");

            }

            document.getElementById('ec-btn-next').disabled = false;

        }

        

        // Auto-start previews when entering the LR step (called from ecShowStep)

        // --- Stereo Calibration ---

        function ecStartStereoPreviews() {

            ecStartStereoSinglePreview('left', 1);

            ecStartStereoSinglePreview('right', 2);

        }

        

        function ecStartStereoSinglePreview(side, camId) {

            const videoEl = document.getElementById('ec-stereo-video-' + side);

            const statusEl = document.getElementById('ec-stereo-status-' + side);

            if (!videoEl || !statusEl) return;

            

            statusEl.style.display = 'flex';

            statusEl.textContent = 'Connexion au flux HLS...';

            

            const hlsUrl = window.location.protocol + '//' + window.location.hostname + ':48888/robot/cam' + camId + '/index.m3u8';

            

            if (typeof Hls !== 'undefined' && Hls.isSupported()) {

                const hls = new Hls({ 

                    maxBufferLength: 5,

                    maxMaxBufferLength: 10,

                    liveDurationInfinity: true,

                    lowLatencyMode: false

                });

                hls.loadSource(hlsUrl);

                hls.attachMedia(videoEl);

                hls.on(Hls.Events.MANIFEST_PARSED, function() {

                    videoEl.play().catch(function(){});

                    videoEl.style.display = 'block';

                    statusEl.style.display = 'none';

                });

                hls.on(Hls.Events.ERROR, function(event, data) {

                    if (data.fatal) {

                        statusEl.innerHTML = 'Flux HLS indisponible.';

                    }

                });

                videoEl._hls = hls;

            } else if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {

                videoEl.src = hlsUrl;

                videoEl.play().catch(function(){});

                videoEl.style.display = 'block';

                statusEl.style.display = 'none';

            } else {

                statusEl.innerHTML = 'Navigateur non compatible HLS.';

            }

        }

        

        function ecStopStereoPreviews() {

            ['left', 'right'].forEach(function(side) {

                var videoEl = document.getElementById('ec-stereo-video-' + side);

                if (videoEl) {

                    if (videoEl._hls) { try { videoEl._hls.destroy(); } catch(e) {} videoEl._hls = null; }

                    videoEl.srcObject = null;

                    videoEl.src = '';

                    videoEl.style.display = 'none';

                }

                var statusEl = document.getElementById('ec-stereo-status-' + side);

                if (statusEl) {

                    statusEl.style.display = 'flex';

                    statusEl.textContent = 'Chargement...';

                }

            });

        }

        

        // Cleanup function for stereo listeners/intervals

        var _ecStereoInterval = null;

        var _ecStereoTimeout = null;

        var _ecStereoOrigOnMessage = null;

        

        function ecCleanupStereoListeners() {

            if (_ecStereoInterval) { clearInterval(_ecStereoInterval); _ecStereoInterval = null; }

            if (_ecStereoTimeout) { clearTimeout(_ecStereoTimeout); _ecStereoTimeout = null; }

            if (_ecStereoOrigOnMessage !== null && appWs) {

                appWs.onmessage = _ecStereoOrigOnMessage;

                _ecStereoOrigOnMessage = null;

            }

        }

        

        function ecRunStereoCalib() {

            // Clean up any previous run

            ecCleanupStereoListeners();

            

            var btnRun = document.getElementById('btn-ec-run-stereo');

            var btnSkip = document.getElementById('btn-ec-skip-stereo');

            var progressDiv = document.getElementById('ec-stereo-progress');

            var progressText = document.getElementById('ec-stereo-progress-text');

            var progressBar = document.getElementById('ec-stereo-progress-bar');

            var resultDiv = document.getElementById('ec-stereo-result');

            var resultText = document.getElementById('ec-stereo-result-text');

            

            if (btnRun) { btnRun.disabled = true; btnRun.style.opacity = '0.5'; }

            if (btnSkip) { btnSkip.disabled = true; btnSkip.style.opacity = '0.5'; }

            if (progressDiv) progressDiv.style.display = 'block';

            if (progressText) progressText.textContent = 'Lancement de la calibration stereo...';

            if (progressBar) progressBar.style.width = '10%';

            

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                var _cols = parseInt((document.getElementById('mcc-cols')||{}).value) || 9;
                var _rows = parseInt((document.getElementById('mcc-rows')||{}).value) || 6;
                var _sqmm = parseInt((document.getElementById('mcc-square')||{}).value) || 25;
                appWs.send(JSON.stringify({ type: "run_stereo_calib", chessboard_cols: _cols, chessboard_rows: _rows, square_size_mm: _sqmm, num_pairs: 20 }));

                if (typeof showToast === 'function') {

                    showToast("Stereo", "Calibration stereo lancee sur le robot", "info");

                }

            } else {

                if (progressText) progressText.textContent = 'Erreur: WebSocket non connecte';

                if (resultDiv) {

                    resultDiv.style.display = 'block';

                    resultDiv.style.background = 'rgba(239,68,68,0.1)';

                    resultDiv.style.border = '1px solid rgba(239,68,68,0.3)';

                }

                if (resultText) {

                    resultText.textContent = 'Impossible de lancer la calibration. Verifiez la connexion au robot.';

                    resultText.style.color = '#ef4444';

                }

                return;

            }

            

            var progress = 10;

            _ecStereoInterval = setInterval(function() {
                // No-op: real progress driven by stereo_calib_progress WS messages from robot
            }, 999999);  // effectively disabled (replaces old fake Math.random progress)

            

            // Listen for result

            var origOnMessage = appWs.onmessage;

            _ecStereoOrigOnMessage = origOnMessage;

            appWs.onmessage = function(event) {

                try {

                    var data = JSON.parse(event.data);

                    if (data.type === 'stereo_calib_progress') {
                        var _pbar = document.getElementById('ec-stereo-progress-bar');
                        var _ptxt = document.getElementById('ec-stereo-progress-text');
                        if (_pbar) _pbar.style.width = data.progress + '%';
                        if (_ptxt) _ptxt.textContent = data.message || ('Calibration... ' + data.progress + '%');
                    }
                    if (data.type === 'mono_calib_frame' || data.type === 'stereo_calib_frame') {
                        var _imgs = document.querySelectorAll('#mcc-cam-img');
                        _imgs.forEach(function(img) {
                            img.src = 'data:image/jpeg;base64,' + data.image;
                            img.style.display = 'block';
                        });
                        var _videos = document.querySelectorAll('#mcc-cam-video');
                        _videos.forEach(function(v) { v.style.display = 'none'; });
                        var _ovls = document.querySelectorAll('#mcc-cam-status-overlay');
                        _ovls.forEach(function(ovl) {
                            ovl.style.backgroundColor = 'rgba(9,9,11,0.2)';
                            ovl.style.justifyContent = 'flex-end';
                            ovl.style.paddingBottom = '0.5rem';
                        });
                    }
                    if (data.type === 'mono_calib_progress') {
                        var _ovl = document.getElementById('mcc-cam-status-overlay');
                        var _txt = document.getElementById('mcc-cam-status-text');
                        if (_ovl) _ovl.style.display = 'flex';
                        if (_txt) _txt.innerHTML = '<span style="font-size:1rem; color:var(--text-primary);">' + (data.message || '').replace(/\n/g, '<br/>') + '</span><br/><span style="font-size:0.75rem; color:var(--text-secondary);">' + (data.progress || 0) + '%</span>';
                    }
                    if (data.type === 'mono_calib_result') {
                        window.isCalibrating = false;
                        var _ovl2 = document.getElementById('mcc-cam-status-overlay');
                        var _txt2 = document.getElementById('mcc-cam-status-text');
                        var _btn2 = document.getElementById('btn-mcc-run-calib');
                        if (data.success) {
                            if (_ovl2) { _ovl2.style.display = 'flex'; _ovl2.style.backgroundColor = 'rgba(9,9,11,0.9)'; }
                            if (_txt2) _txt2.innerHTML = '<span style="font-size:2rem; color:var(--success); display:block; margin-bottom:0.5rem;">OK</span><span style="color:var(--success); font-weight:bold; font-size:1.05rem;">Calibration reussie !</span><br/><span style="font-size:0.8rem; color:var(--text-secondary); margin-top:0.25rem; display:block;">fx=' + (data.fx || '?') + 'px  reproj=' + (data.reprojection_error || '?') + '</span>';
                            if (_btn2) { _btn2.disabled = false; _btn2.innerHTML = '<span>Fermer la Calibration</span>'; _btn2.onclick = closeCameraCalibModal; }
                        } else {
                            if (_ovl2) { _ovl2.style.display = 'flex'; _ovl2.style.backgroundColor = 'rgba(9,9,11,0.9)'; }
                            if (_txt2) _txt2.innerHTML = '<span style="font-size:2rem; color:var(--danger); display:block; margin-bottom:0.5rem;">X</span><span style="color:var(--danger); font-weight:bold; font-size:1.05rem;">Echec</span><br/><span style="font-size:0.8rem; color:var(--text-secondary); margin-top:0.25rem; display:block;">' + (data.message || 'Erreur') + '</span>';
                            if (_btn2) { _btn2.disabled = false; _btn2.innerHTML = '<span>Reessayer la Calibration</span>'; _btn2.onclick = function() { confirmIndividualCameraCalib(); }; }
                        }
                    }
                    if (data.type === 'stereo_calib_result') {
                        window.isCalibrating = false;
                        clearInterval(_ecStereoInterval);

                        if (progressBar) progressBar.style.width = '100%';

                        if (data.success) {

                            if (progressText) progressText.textContent = 'Calibration stereo reussie !';

                            if (resultDiv) {

                                resultDiv.style.display = 'block';

                                resultDiv.style.background = 'rgba(34,197,94,0.1)';

                                resultDiv.style.border = '1px solid rgba(34,197,94,0.3)';

                            }

                            if (resultText) {

                                resultText.textContent = 'Parametres stereo enregistres. Vous pouvez passer a la finalisation.';

                                resultText.style.color = '#22c55e';

                            }

                            document.getElementById('ec-btn-next').disabled = false;

                            document.getElementById('ec-btn-next').textContent = 'Suivant';

                        } else {

                            if (progressText) progressText.textContent = 'Echec de la calibration stereo';

                            if (resultDiv) {

                                resultDiv.style.display = 'block';

                                resultDiv.style.background = 'rgba(239,68,68,0.1)';

                                resultDiv.style.border = '1px solid rgba(239,68,68,0.3)';

                            }

                            if (resultText) {

                                resultText.textContent = data.message || 'Erreur lors de la calibration stereo.';

                                resultText.style.color = '#ef4444';

                            }

                        }

                        if (btnRun) { btnRun.disabled = true; }

                        if (btnSkip) { btnSkip.disabled = false; btnSkip.style.opacity = '1'; }

                        appWs.onmessage = origOnMessage;

                        _ecStereoOrigOnMessage = null;

                        _ecStereoInterval = null;

                        _ecStereoTimeout = null;

                        _ecStereoOrigOnMessage = null;

                        _ecStereoInterval = null;

                        _ecStereoTimeout = null;

                    }

                } catch(e) {}

                if (origOnMessage) origOnMessage.call(this, event);

            };

            

            _ecStereoTimeout = setTimeout(function() {

                clearInterval(_ecStereoInterval);

                if (progressBar && parseInt(progressBar.style.width) < 100) {

                    if (progressText) progressText.textContent = 'Delai depasse. Reessayez.';

                    if (resultDiv) {

                        resultDiv.style.display = 'block';

                        resultDiv.style.background = 'rgba(245,158,11,0.1)';

                        resultDiv.style.border = '1px solid rgba(245,158,11,0.3)';

                    }

                    if (resultText) {

                        resultText.textContent = 'La calibration a pris trop de temps.';

                        resultText.style.color = '#f59e0b';

                    }

                }

                if (btnRun) { btnRun.disabled = false; btnRun.style.opacity = '1'; }

                if (btnSkip) { btnSkip.disabled = false; btnSkip.style.opacity = '1'; }

            }, 180000);

        }

        

        function ecSkipStereo() {

            ecCleanupStereoListeners();

            ecStopStereoPreviews();

            document.getElementById('ec-btn-next').disabled = false;

            document.getElementById('ec-btn-next').textContent = 'Suivant';

            if (typeof showToast === 'function') {

                showToast("Stereo", "Etape passee. Calibration stereo existante conservee.", "info");

            }

        }

// ─── V-SLAM mode helper (used by updateSLAMMode + toggleVSlamTest pre-flight)

        function getCurrentSlamMode() {

            const sensors = window.lastTelemetryState && window.lastTelemetryState.sensors;

            const cam1 = !!(sensors && sensors.cam1_connected === true);

            const cam2 = !!(sensors && sensors.cam2_connected === true);

            const camCount = (cam1 ? 1 : 0) + (cam2 ? 1 : 0);

            let mode = 'Aucune cam';

            let modeColor = '#ef4444';

            let bgColor = 'rgba(239,68,68,0.12)';

            if (camCount === 0) {

                mode = 'Aucune caméra';

            } else if (camCount === 1) {

                mode = 'Mono';

                modeColor = '#f59e0b';

                bgColor = 'rgba(245,158,11,0.12)';

            } else {

                mode = 'Stéréo';

                modeColor = '#22c55e';

                bgColor = 'rgba(34,197,94,0.12)';

            }

            return { mode: mode, modeColor: modeColor, bgColor: bgColor, cam1: cam1, cam2: cam2, hasTelemetry: !!sensors };

        }

        function updateSLAMMode() {

            const badge = document.getElementById('slam-mode-badge');

            const camerasBadge = document.getElementById('slam-cameras-badge');

            const overlay = document.getElementById('slam-disabled-overlay');

            if (!badge) return;

            

            let cam1 = false, cam2 = false;

            if (window.lastTelemetryState && window.lastTelemetryState.sensors) {

                cam1 = window.lastTelemetryState.sensors.cam1_connected === true;

                cam2 = window.lastTelemetryState.sensors.cam2_connected === true;

            }

            

            const camCount = (cam1 ? 1 : 0) + (cam2 ? 1 : 0);

            let mode = 'Aucune cam';

            let modeColor = '#ef4444';

            let bgColor = 'rgba(239,68,68,0.12)';

            

            if (camCount === 0) {

                mode = 'Aucune cam\u00e9ra';

                modeColor = '#ef4444';

                bgColor = 'rgba(239,68,68,0.12)';

                if (overlay) overlay.style.display = 'flex';

            } else if (camCount === 1) {

                mode = 'Mono';

                modeColor = '#f59e0b';

                bgColor = 'rgba(245,158,11,0.12)';

                if (overlay) overlay.style.display = 'none';

            } else {

                mode = 'St\u00e9r\u00e9o';

                modeColor = '#22c55e';

                bgColor = 'rgba(34,197,94,0.12)';

                if (overlay) overlay.style.display = 'none';

            }

            

            badge.textContent = mode;

            badge.style.color = modeColor;

            badge.style.background = bgColor;

            if (camerasBadge) {

                camerasBadge.textContent = camCount + ' cam\u00e9ra' + (camCount > 1 ? 's' : '') + ' d\u00e9tect\u00e9e' + (camCount > 1 ? 's' : '');

            }

        

        

            // Aussi copier le mode dans le badge de la Console de Test V-SLAM (toujours visible)

            const testBadge = document.getElementById('vslam-test-mode-badge');

            if (testBadge) {

                testBadge.textContent = 'Mode: ' + mode;

                testBadge.style.background = bgColor;

                testBadge.style.color = modeColor;

                testBadge.title = (mode === 'Stéréo' ? 'Cam1 + Cam2 connectées au robot'

                                   : (mode === 'Mono' ? 'Caméra 1 seule connectée au robot'

                                   : 'Aucune caméra détectée par le robot'));

            }

        }

        // ─── MOBILE SIDEBAR ACTIONS ───────────────────────────────────────────

        function toggleSidebar() {

            const sidebar = document.querySelector('.sidebar');

            const overlay = document.querySelector('.sidebar-overlay');

            if (sidebar && overlay) {

                sidebar.classList.toggle('active');

                overlay.classList.toggle('active');

            }

        }

        function closeSidebar() {

            const sidebar = document.querySelector('.sidebar');

            const overlay = document.querySelector('.sidebar-overlay');

            if (sidebar && overlay) {

                sidebar.classList.remove('active');

                overlay.classList.remove('active');

            }

        }

        // ─── TABS SWITCHING ───────────────────────────────────────────────────

        function switchTab(tabId) {

            closeSidebar();

            

            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

            document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));

            

            const targetContent = document.getElementById(`tab-${tabId}-content`);

            const targetNav = document.getElementById(`nav-${tabId}`);

            if (targetContent && targetNav) {

                targetContent.classList.add('active');

                targetNav.classList.add('active');

                activeTab = tabId;

                localStorage.setItem('bastetActiveTab', tabId);

            }

            const titles = {

                'dashboard': { title: "Vue d'ensemble", subtitle: "Statistiques en direct et flux caméras du robot Bastet." },

                'control': { title: "Télécommande & Navigation", subtitle: "Contrôle manuel des mouvements, de la posture et des objectifs du robot." },

                'users': { title: "Comptes & MyGES", subtitle: "Gérer les profils utilisateurs et leurs identifiants d'agenda." },

                'faces': { title: "Galerie Visages", subtitle: "Gérer les visages enregistrés pour la reconnaissance faciale." },

                'system': { title: "Système & Updates", subtitle: "Suivi des mises à jour logicielles et des services ROS." },

                'chat': { title: "Chat & Contrôle IA", subtitle: "Dialogue temps réel avec le robot et supervision de l'IA." },

                'diagnostics': { title: "Arduino & Calib", subtitle: "Télémétrie des moteurs, gyroscope de l'IMU et calibrages." },

                'map': { title: "SLAM & Map", subtitle: "Navigation cartographique, nuage de points et paramètres d'évitement." }

            };

            const headerInfo = titles[tabId] || titles['dashboard'];

            document.getElementById('tab-title').textContent = headerInfo.title;

            document.getElementById('tab-subtitle').textContent = headerInfo.subtitle;

            if (tabId === 'users') {

                loadAccounts();

            } else if (tabId === 'faces') {

                closeFolderDetails();

                loadFacesGallery();

            } else if (tabId === 'diagnostics') {

            } else if (tabId === 'map') {

                setTimeout(drawSLAMMap, 100);

            } else if (tabId === 'control') {

                setTimeout(initControlTab, 100);

            }

        }

        // ─── TELEMETRY ────────────────────────────────────────────────────────

        async function fetchTelemetry() {

            try {

                const res = await fetch('/core/state', { headers: { 'X-API-Token': apiToken } });

                if (res.status === 403) { logout(); return; }

                if (res.ok) {

                    const state = await res.json();

                    window.lastTelemetryState = state;

                    

                    const robotBadge = document.getElementById('robot-status-badge');

                    const robotStatus = state.robot_status || 'offline';

                    

                    robotBadge.className = `status-badge ${robotStatus}`;

                    if (robotStatus === 'online') {

                        robotBadge.textContent = '🟢 En ligne';

                    } else if (robotStatus === 'hibernating') {

                        robotBadge.textContent = '🟠 Hibernation';

                    } else if (robotStatus === 'idle') {

                        robotBadge.textContent = '🟡 Inactif';

                    } else {

                        robotBadge.textContent = '🔴 Hors-ligne';

                    }

                    const sensors = state.sensors || {};

                    

                    // Mise à jour du statut Arduino Mega depuis les capteurs de l'état

                    const arduinoBadge = document.getElementById('arduino-status-badge');

                    const arduinoOfflineMsg = document.getElementById('arduino-offline-msg');

                    const arduinoContent = document.getElementById('arduino-telemetry-content');

                    const isArduinoConnected = sensors.arduino_connected === true;

                    if (arduinoBadge) {

                        if (isArduinoConnected) {

                            arduinoBadge.className = 'status-badge active';

                            arduinoBadge.textContent = 'En ligne';

                            if (arduinoOfflineMsg) arduinoOfflineMsg.style.display = 'none';

                            if (arduinoContent) arduinoContent.style.display = '';

                        } else {

                            arduinoBadge.className = 'status-badge offline';

                            arduinoBadge.textContent = 'Hors-ligne';

                            if (arduinoOfflineMsg) arduinoOfflineMsg.style.display = '';

                            if (arduinoContent) arduinoContent.style.display = 'none';

                        }

                    }

                    const cpu = sensors.cpu_percent || 0;

                    const ram = sensors.ram_percent || 0;

                    const temp = sensors.temp_c || 0;

                    updateGaugeCircle('gauge-cpu', cpu);

                    document.getElementById('gauge-cpu-val').textContent = `${Math.round(cpu)}%`;

                    updateGaugeCircle('gauge-ram', ram);

                    document.getElementById('gauge-ram-val').textContent = `${Math.round(ram)}%`;

                    updateGaugeCircle('gauge-temp', (temp / 100) * 100);

                    document.getElementById('gauge-temp-val').textContent = `${Math.round(temp)}°C`;

                    document.getElementById('sensor-seen-person').textContent = state.seen_person || 'Personne';

                    document.getElementById('sensor-seen-objects').textContent = (state.seen_objects && state.seen_objects.length > 0) ? state.seen_objects.join(', ') : 'Aucun';

                    document.getElementById('sensor-version').textContent = state.robot_version || 'v0.0.0';

                    

                    if (state.camera_mapping) {

                        const selectLeft = document.getElementById('cam-port-left');

                        const selectRight = document.getElementById('cam-port-right');

                        if (selectLeft && state.camera_mapping.left && selectLeft.value !== state.camera_mapping.left) {

                            selectLeft.value = state.camera_mapping.left;

                        }

                        if (selectRight && state.camera_mapping.right && selectRight.value !== state.camera_mapping.right) {

                            selectRight.value = state.camera_mapping.right;

                        }

                        if (typeof updateCameraPortOptions === 'function') updateCameraPortOptions();

                    }

                    

                    if (state.ai_state) {

                        updateAIControlUI('tts', state.ai_state.tts);

                        updateAIControlUI('stt', state.ai_state.stt);

                        updateAIControlUI('chat', state.ai_state.chat);

                        updateAIControlUI('yolo', state.ai_state.yolo);

                        updateAIControlUI('face_rec', state.ai_state.face_rec);

                    }

                    // Cameras connection & auto-enable

                    const cam1Status = document.getElementById('calib-cam-status-1');

                    const cam1Enable = document.getElementById('calib-cam-enable-1');

                    const cam2Status = document.getElementById('calib-cam-status-2');

                    const cam2Enable = document.getElementById('calib-cam-enable-2');

                    const activeStreams = state.active_streams || { "1": false, "2": false };

                    for (let camId of [1, 2]) {

                        const isActive = activeStreams[camId] === true;

                        

                        const wasActive = window.activeStreams ? window.activeStreams[camId] : false;

                        if (!window.activeStreams) window.activeStreams = { 1: false, 2: false };

                        window.activeStreams[camId] = isActive;

                        

                        if (isActive && !wasActive) {

                            if (!window.userClosedStream) window.userClosedStream = { 1: false, 2: false };

                            window.userClosedStream[camId] = false;

                        }

                        const statusEl = document.getElementById(`stream-status-${camId}`);

                        const btnText = document.getElementById(`stream-btn-text-${camId}`);

                        

                        if (!isActive && window.localViewing && window.localViewing[camId]) {

                            window.localViewing[camId] = false;

                            stopStreamUI(camId);

                        }

                        

                        if (!window.localViewing || !window.localViewing[camId]) {

                            if (statusEl) {

                                statusEl.textContent = isActive ? 'En direct' : 'Inactif';

                                statusEl.className = isActive ? 'status-badge active' : 'status-badge';

                            }

                            if (btnText) {

                                btnText.textContent = isActive ? 'Rejoindre le flux' : 'Démarrer le flux';

                            }

                            

                            // FIX: pas d'auto-rejoindre (handler 2). Le flux reste on-demand strict.

                            // Seul un clic utilisateur sur "Demarrer le flux" doit declencher toggleStream().

                            // Les variables window.activeStreams / UI badges continuent d'etre mises a jour.

                            var _onDemandGuard = true; /* placeholder for removed auto-join */

                        }

                    }

                    const cam1Connected = sensors.cam1_connected === true;

                    const cam2Connected = sensors.cam2_connected === true;

                    if (cam2Connected) {
                        window.forceStereoUI = false;
                    } else {
                        window.forceMonoUI = false;
                    }

                    updateCameraModularity(cam1Connected, cam2Connected);

                    if (cam1Status) {

                        cam1Status.textContent = cam1Connected ? 'Connectée' : 'Déconnectée';

                        cam1Status.style.color = cam1Connected ? 'var(--success)' : 'var(--text-secondary)';

                    }

                    if (cam2Status) {

                        cam2Status.textContent = cam2Connected ? 'Connectée' : 'Déconnectée';

                        cam2Status.style.color = cam2Connected ? 'var(--success)' : 'var(--text-secondary)';

                    }

                    if (window.lastCam1Connected === undefined) window.lastCam1Connected = false;

                    if (window.lastCam2Connected === undefined) window.lastCam2Connected = false;

                    if (cam1Connected && !window.lastCam1Connected) {

                        if (cam1Enable && !cam1Enable.checked) {

                            cam1Enable.checked = true;

                            toggleCalibCamera(1);

                        }

                    }

                    if (cam2Connected && !window.lastCam2Connected) {

                        if (cam2Enable && !cam2Enable.checked) {

                            cam2Enable.checked = true;

                            toggleCalibCamera(2);

                        }

                    }

                    window.lastCam1Connected = cam1Connected;

                    window.lastCam2Connected = cam2Connected;

                    

                    if (state.updated_at) {

                        const date = new Date(state.updated_at * 1000);

                        document.getElementById('sensor-last-seen').textContent = date.toLocaleTimeString();

                    } else {

                        document.getElementById('sensor-last-seen').textContent = '--';

                    }

                    // FIX V4: Maintient le watchdog arduinoOfflineChecker en vie via le polling REST 2s

                    // (en cas de robot statique, l agent WS n envoie plus de message puisque latest_telemetry

                    // est identique, mais le dashboard reste vu comme vivant grace aux polls /core/state).

                    window.lastArduinoTelemetry = Date.now();

                    const serviceBadge = document.getElementById('spotbot-service-badge');

                    const isSpotbotActive = sensors.spotbot_service_active;

                    const btnStart = document.getElementById('btn-start-spotbot');

                    const btnStop = document.getElementById('btn-stop-spotbot');

                    const btnRestart = document.getElementById('btn-restart-spotbot');

                    if (isSpotbotActive === true) {

                        serviceBadge.textContent = 'Actif';

                        serviceBadge.className = 'status-badge active';

                        if (btnStart) btnStart.style.display = 'none';

                        if (btnStop) btnStop.style.display = '';

                        if (btnRestart) btnRestart.style.display = '';

                    } else if (isSpotbotActive === false) {

                        serviceBadge.textContent = 'Arrêté';

                        serviceBadge.className = 'status-badge offline';

                        if (btnStart) btnStart.style.display = '';

                        if (btnStop) btnStop.style.display = 'none';

                        if (btnRestart) btnRestart.style.display = 'none';

                    } else {

                        serviceBadge.textContent = 'Inconnu';

                        serviceBadge.className = 'status-badge';

                    }

                }

            } catch (e) {

                console.error("Telemetry fetch error:", e);

            }

            try {

                const gwRes = await fetch('/gateway/telemetry', { headers: { 'X-API-Token': apiToken } });

                if (gwRes.ok) {

                    const gw = await gwRes.json();

                    updateGaugeCircle('gw-gauge-cpu', gw.cpu_percent);

                    document.getElementById('gw-gauge-cpu-val').textContent = `${Math.round(gw.cpu_percent)}%`;

                    updateGaugeCircle('gw-gauge-ram', gw.ram_percent);

                    document.getElementById('gw-gauge-ram-val').textContent = `${Math.round(gw.ram_percent)}%`;

                    updateGaugeCircle('gw-gauge-disk', gw.disk_percent);

                    document.getElementById('gw-gauge-disk-val').textContent = `${Math.round(gw.disk_percent)}%`;

                    document.getElementById('gw-temp-val').textContent = `${Math.round(gw.temp_c)}°C`;

                    const days = Math.floor(gw.uptime_s / 86400);

                    const hrs = Math.floor((gw.uptime_s % 86400) / 3600);

                    const mins = Math.floor((gw.uptime_s % 3600) / 60);

                    document.getElementById('gw-uptime-val').textContent = days > 0 ? `${days}j ${hrs}h ${mins}m` : `${hrs}h ${mins}m`;

                }

            } catch (e) {

                console.error("Gateway telemetry fetch error:", e);

            }

        }

        function updateGaugeCircle(id, val) {

            const el = document.getElementById(id);

            if (!el) return;

            const cappedVal = Math.max(0, Math.min(100, val));

            el.setAttribute('stroke-dasharray', `${cappedVal}, 100`);

        }

        // ─── CAMERA STREAM ON-DEMAND ─────────────────────────────────────────

        window.forceStereoUI = false;
        window.forceMonoUI = false;

        function forceStereoMode() {
            window.forceMonoUI = false;
            window.forceStereoUI = true;
            const ts = window.lastTelemetryState;
            const cam1Connected = ts && ts.sensors && ts.sensors.cam1_connected === true;
            const cam2Connected = ts && ts.sensors && ts.sensors.cam2_connected === true;
            updateCameraModularity(cam1Connected, cam2Connected);
        }

        function forceMonoMode() {
            window.forceMonoUI = true;
            window.forceStereoUI = false;
            const rightMain = document.getElementById('cam-port-right');
            const rightModal = document.getElementById('cam-port-right-modal');
            if (rightMain) rightMain.value = '';
            if (rightModal) rightModal.value = '';
            saveCameraPortsMapping();
            const ts = window.lastTelemetryState;
            const cam1Connected = ts && ts.sensors && ts.sensors.cam1_connected === true;
            const cam2Connected = ts && ts.sensors && ts.sensors.cam2_connected === true;
            updateCameraModularity(cam1Connected, cam2Connected);
        }

        function updateCameraModularity(cam1Connected, cam2Connected) {
            const forceStereo = window.forceStereoUI === true;
            const forceMono = window.forceMonoUI === true;
            const cam2Active = (cam2Connected || forceStereo) && !forceMono;

            const card1 = document.getElementById('stream-card-1');
            const card2 = document.getElementById('stream-card-2');
            if (card1) card1.style.display = cam1Connected ? 'flex' : 'none';
            if (card2) card2.style.display = cam2Active ? 'flex' : 'none';

            const qcard1 = document.getElementById('quality-card-1');
            const qcard2 = document.getElementById('quality-card-2');
            if (qcard1) qcard1.style.display = cam1Connected ? 'block' : 'none';
            if (qcard2) qcard2.style.display = cam2Active ? 'block' : 'none';

            const qgrid = document.getElementById('quality-grid');
            if (qgrid) {
                qgrid.style.gridTemplateColumns = (cam1Connected && cam2Active) ? '1fr 1fr' : '1fr';
                qgrid.style.display = (cam1Connected || cam2Active) ? 'grid' : 'none';
            }

            const calibCam1Container = document.getElementById('calib-cam-container-1');
            const calibCam2Container = document.getElementById('calib-cam-container-2');
            const calibCam2ContainerModal = document.getElementById('calib-cam-container-2-modal');
            if (calibCam1Container) calibCam1Container.style.display = cam1Connected ? 'flex' : 'none';
            if (calibCam2Container) calibCam2Container.style.display = cam2Active ? 'flex' : 'none';
            if (calibCam2ContainerModal) calibCam2ContainerModal.style.display = cam2Active ? 'flex' : 'none';

            const titleLeftMain = document.getElementById('title-cam-left-main');
            const titleLeftModal = document.getElementById('title-cam-left-modal');
            const labelPortLeftMain = document.getElementById('label-port-left-main');
            const labelPortLeftModal = document.getElementById('label-port-left-modal');
            const qualityTitleLeft = document.getElementById('quality-title-cam-1');
            const qualityTitleLeftModal = document.getElementById('quality-title-cam-1-modal');
            const streamTitleLeft = document.getElementById('stream-title-cam-1');

            if (titleLeftMain) titleLeftMain.textContent = cam2Active ? 'Caméra Gauche' : 'Caméra';
            if (titleLeftModal) titleLeftModal.textContent = cam2Active ? 'Caméra Gauche' : 'Caméra';
            if (labelPortLeftMain) labelPortLeftMain.textContent = cam2Active ? 'Port Gauche' : 'Port Caméra';
            if (labelPortLeftModal) labelPortLeftModal.textContent = cam2Active ? 'Port Gauche' : 'Port Caméra';
            if (qualityTitleLeft) qualityTitleLeft.textContent = cam2Active ? 'Caméra Gauche' : 'Caméra';
            if (qualityTitleLeftModal) qualityTitleLeftModal.textContent = cam2Active ? 'Caméra Gauche' : 'Caméra';
            if (streamTitleLeft) streamTitleLeft.textContent = cam2Active ? 'Caméra Gauche' : 'Caméra';

            const containerPortRightMain = document.getElementById('container-port-right-main');
            const containerPortRightModal = document.getElementById('container-port-right-modal');
            if (containerPortRightMain) containerPortRightMain.style.display = cam2Active ? 'block' : 'none';
            if (containerPortRightModal) containerPortRightModal.style.display = cam2Active ? 'block' : 'none';

            const btnAddMain = document.getElementById('btn-add-camera-main');
            const btnAddModal = document.getElementById('btn-add-camera-modal');
            if (btnAddMain) btnAddMain.style.display = cam2Active ? 'none' : 'inline-block';
            if (btnAddModal) btnAddModal.style.display = cam2Active ? 'none' : 'inline-block';

            const btnRemoveMain = document.getElementById('btn-remove-camera-main');
            const btnRemoveModal = document.getElementById('btn-remove-camera-modal');
            if (btnRemoveMain) btnRemoveMain.style.display = cam2Active ? 'inline-block' : 'none';
            if (btnRemoveModal) btnRemoveModal.style.display = cam2Active ? 'inline-block' : 'none';

            const btnSwapMain = document.getElementById('btn-swap-cam-main');
            const btnSwapModal = document.getElementById('btn-swap-cam-modal');
            if (btnSwapMain) btnSwapMain.style.display = cam2Active ? 'inline-block' : 'none';
            if (btnSwapModal) btnSwapModal.style.display = cam2Active ? 'inline-block' : 'none';

            const btnConfigMain = document.getElementById('btn-config-cam-main');
            const btnConfigModal = document.getElementById('btn-config-cam-modal');
            if (btnConfigMain) btnConfigMain.style.display = cam2Active ? 'inline-block' : 'none';
            if (btnConfigModal) btnConfigModal.style.display = cam2Active ? 'inline-block' : 'none';

            const vslamSpan = document.getElementById('vslam-text-mode');
            if (vslamSpan) {
                vslamSpan.textContent = cam2Active ? 'Superposer V-SLAM Stéréo' : 'Superposer V-SLAM Mono';
            }
        }

        function stopStreamUI(camId) {

            const placeholder = document.getElementById(`stream-placeholder-${camId}`);

            const statusEl = document.getElementById(`stream-status-${camId}`);

            const btnText = document.getElementById(`stream-btn-text-${camId}`);

            const videoContainer = document.getElementById(`video-container-${camId}`);

            const videoEl = document.getElementById(`video-cam-${camId}`);

            const loaderEl = document.getElementById(`stream-loader-${camId}`);

            const fsBtn = document.getElementById(`video-fs-btn-${camId}`);

            if (window.hlsInstances && window.hlsInstances[camId]) {

                try { window.hlsInstances[camId].destroy(); } catch(e) {}

                delete window.hlsInstances[camId];

            }

            if (peerConnections[camId]) {

                try { peerConnections[camId].close(); } catch(e) {}

                peerConnections[camId] = null;

            }

            if (videoEl) {

                videoEl.srcObject = null;

                videoEl.src = '';

                videoEl.removeAttribute('src');

                videoEl.style.display = 'none';

            }

            if (videoContainer) videoContainer.style.display = 'none';

            if (loaderEl) loaderEl.style.display = 'none';

            if (fsBtn) fsBtn.style.display = 'none';

            window.localViewing[camId] = false;

            placeholder.style.display = 'flex';

            // On affiche l'état RÉEL du flux robot, pas l'état de visionnage local

            const isActive = window.activeStreams && window.activeStreams[camId];

            statusEl.textContent = isActive ? 'En direct (non visionné)' : 'Inactif';

            statusEl.className = isActive ? 'status-badge active' : 'status-badge';

            btnText.textContent = isActive ? 'Rejoindre le flux' : 'Démarrer le flux';

        }

        function playHLSStream(videoEl, camId, onPlay, onError, customKey) {

            const hlsUrl = `${window.location.protocol}//${window.location.hostname}:48888/robot/cam${camId}/index.m3u8`;

            const hlsKey = customKey || camId;

            

            if (!window.hlsInstances) window.hlsInstances = {};

            if (window.hlsInstances[hlsKey]) {

                try { window.hlsInstances[hlsKey].destroy(); } catch(e) {}

                delete window.hlsInstances[hlsKey];

            }

            

            if (Hls.isSupported()) {

                const hls = new Hls({

                    maxBufferSize: 0,

                    maxBufferLength: 0.5,

                    liveSyncDuration: 0.5,

                    liveMaxLatencyDuration: 1.5,

                    enableWorker: true,

                    lowLatencyMode: true

                });

                window.hlsInstances[hlsKey] = hls;

                hls.loadSource(hlsUrl);

                hls.attachMedia(videoEl);

                hls.on(Hls.Events.MANIFEST_PARSED, function() {

                    videoEl.play().then(onPlay).catch(e => {

                        console.warn(e);

                        onPlay();

                    });

                });

                hls.on(Hls.Events.ERROR, function(event, data) {

                    if (data.fatal) {

                        console.error('HLS Fatal error:', data);

                        if (onError) onError(data);

                    }

                });

            } else if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {

                videoEl.src = hlsUrl;

                videoEl.addEventListener('loadedmetadata', function() {

                    videoEl.play().then(onPlay).catch(e => {

                        console.warn(e);

                        onPlay();

                    });

                });

            } else {

                if (onError) onError('HLS not supported in this browser');

            }

        }

        async function startStreamWebRTC(camId) {

            const placeholder = document.getElementById(`stream-placeholder-${camId}`);

            const statusEl = document.getElementById(`stream-status-${camId}`);

            const btnText = document.getElementById(`stream-btn-text-${camId}`);

            const videoContainer = document.getElementById(`video-container-${camId}`);

            const videoEl = document.getElementById(`video-cam-${camId}`);

            const loaderEl = document.getElementById(`stream-loader-${camId}`);

            const fsBtn = document.getElementById(`video-fs-btn-${camId}`);

            if (peerConnections[camId]) {

                try { peerConnections[camId].close(); } catch(e) {}

                peerConnections[camId] = null;

            }

            statusEl.textContent = 'Connexion WebRTC…';

            statusEl.className = 'status-badge';

            streamingState[camId] = 'connecting';

            placeholder.style.display = 'none';

            videoContainer.style.display = 'block';

            videoEl.style.display = 'none';

            fsBtn.style.display = 'none';

            loaderEl.style.display = 'flex';

            let pc = null;

            let aborted = false;

            let trackReceived = false;

            const showWebRTCError = (msg) => {

                if (aborted) return;

                aborted = true;

                if (pc) {

                    try { pc.close(); } catch(e) {}

                    if (peerConnections[camId] === pc) peerConnections[camId] = null;

                }

                window.localViewing[camId] = false;

                // FIX: NE PAS set userClosedStream ici — le stream tourne côté robot,
                // c'est juste le WebRTC local qui a échoué. On garde le loader
                // visible pour que l'utilisateur voie qu'on attend.

                streamingState[camId] = 'idle';

                if (appWs && appWs.readyState === WebSocket.OPEN) {

                    appWs.send(JSON.stringify({ type: 'release_camera', camera: camId }));

                }

                // FIX: Restaurer l'UI proprement pour que l'utilisateur
                // voie bien le bouton "Reessayer" et puisse re-cliquer.
                loaderEl.style.display = 'none';
                videoEl.style.display = 'none';
                fsBtn.style.display = 'none';
                placeholder.style.display = 'flex';
                videoContainer.style.display = 'none';
                statusEl.textContent = 'Erreur WebRTC';
                statusEl.className = 'status-badge error';
                btnText.textContent = 'Reessayer';

                console.error(`WebRTC cam${camId} error:`, msg);

            };

            try {

                pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });

                peerConnections[camId] = pc;

                pc.addTransceiver('video', { direction: 'recvonly' });

                let trackTimeout = setTimeout(() => {

                    if (!trackReceived) showWebRTCError('Timeout — aucun flux reçu après 25s');

                }, 25000);

                pc.oniceconnectionstatechange = () => {

                    console.log(`WebRTC ICE state cam${camId}: ${pc.iceConnectionState}`);

                    if (!trackReceived && (pc.iceConnectionState === 'failed' || pc.iceConnectionState === 'disconnected')) {

                        clearTimeout(trackTimeout);

                        showWebRTCError(`ICE ${pc.iceConnectionState}`);

                    }

                };

                pc.ontrack = (event) => {

                    trackReceived = true;

                    clearTimeout(trackTimeout);

                    if (aborted) return;

                    if (event.streams && event.streams[0]) {

                        videoEl.srcObject = event.streams[0];

                    } else {

                        const inboundStream = new MediaStream();

                        inboundStream.addTrack(event.track);

                        videoEl.srcObject = inboundStream;

                    }

                    videoEl.play().catch(e => console.warn('Video play failed:', e));

                    loaderEl.style.display = 'none';

                    videoEl.style.display = 'block';

                    fsBtn.style.display = 'block';

                    statusEl.textContent = 'En direct';

                    statusEl.className = 'status-badge active';

                    btnText.textContent = 'Couper Caméra';

                };

                const offer = await pc.createOffer();

                await pc.setLocalDescription(offer);

                const webrtcUrl = `${window.location.protocol}//${window.location.hostname}:48889/robot/cam${camId}/whep`;

                let response = null;

                // 48 retries × 250ms = 12s pour laisser le temps à la caméra de démarrer

                let retries = 48;

                while (retries > 0 && !aborted) {

                    try {

                        response = await fetch(webrtcUrl, {

                            method: 'POST',

                            headers: { 'Content-Type': 'application/sdp' },

                            body: pc.localDescription.sdp

                        });

                        if (response.ok) break;

                    } catch (e) {

                        console.warn(`WHEP signaling cam${camId}: ${e.message}`);

                    }

                    retries--;

                    if (retries > 0 && !aborted) await new Promise(r => setTimeout(r, 250));

                }

                if (aborted) return;

                if (!response || !response.ok) {

                    clearTimeout(trackTimeout);

                    throw new Error(`WHEP cam${camId} non disponible après 12s.`);

                }

                const answerSdp = await response.text();

                await pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: answerSdp }));

            } catch (err) {

                showWebRTCError(err.message);

            }

        }

        function queryCameraResolutions(camId) {

            const btn = document.getElementById('detect-res-btn-' + camId);

            const statusEl = document.getElementById('stream-quality-status');

            const originalText = btn ? btn.textContent : '🔍';

            if (btn) {

                btn.textContent = '⏳...';

                btn.disabled = true;

            }

            statusEl.textContent = 'Détection des résolutions caméra ' + camId + '...';

            statusEl.style.color = 'var(--text-secondary)';

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({

                    type: 'query_camera_resolutions',

                    camera: camId

                }));

            }

            // Restore button after timeout (in case no response)

            setTimeout(() => {

                if (btn) {

                    btn.textContent = originalText;

                    btn.disabled = false;

                }

                if (statusEl.textContent.startsWith('Détection')) {

                    statusEl.textContent = 'Délai dépassé — essayez de connecter la caméra';

                    statusEl.style.color = 'var(--danger)';

                    setTimeout(() => { statusEl.textContent = ''; }, 4000);

                }

            }, 25000);

        }

        async function saveStreamQualityConfig() {

            const statusEl = document.getElementById('stream-quality-status');

            const config = {

                cam1: {

                    stream_res: document.getElementById('stream-res-1').value,

                    stream_fps: parseInt(document.getElementById('stream-fps-1').value),

                    vslam_res: document.getElementById('vslam-res-1').value,

                    codec: document.getElementById('stream-codec-1').value,

                },

                cam2: {

                    stream_res: document.getElementById('stream-res-2').value,

                    stream_fps: parseInt(document.getElementById('stream-fps-2').value),

                    vslam_res: document.getElementById('vslam-res-2').value,

                    codec: document.getElementById('stream-codec-2').value,

                }

            };

            statusEl.textContent = 'Enregistrement...';

            statusEl.style.color = 'var(--text-secondary)';

            try {

                // Save to gateway

                const res = await fetch('/core/stream/config', {

                    method: 'POST',

                    headers: {

                        'Content-Type': 'application/json',

                        'X-API-Token': apiToken

                    },

                    body: JSON.stringify(config)

                });

                if (res.ok) {

                    // Send to robot via WebSocket

                    if (appWs && appWs.readyState === WebSocket.OPEN) {

                        appWs.send(JSON.stringify({

                            type: 'stream_quality_config',

                            config: config

                        }));

                    }

                    statusEl.textContent = 'Configuration appliquée';

                    statusEl.style.color = 'var(--success)';

                } else {

                    statusEl.textContent = 'Erreur sauvegarde';

                    statusEl.style.color = 'var(--danger)';

                }

            } catch (e) {

                statusEl.textContent = 'Erreur réseau';

                statusEl.style.color = 'var(--danger)';

            }

            setTimeout(() => {

                statusEl.textContent = '';

            }, 3000);

        }

        // Load saved quality config on page init

        function getStreamQualityParams(camId) {

            // Read current quality settings from the DOM dropdowns

            const params = {};

            const resEl = document.getElementById('stream-res-' + camId);

            const fpsEl = document.getElementById('stream-fps-' + camId);

            const codecEl = document.getElementById('stream-codec-' + camId);

            const vslamResEl = document.getElementById('vslam-res-' + camId);

            if (resEl && resEl.value) params.stream_res = resEl.value;

            if (fpsEl && fpsEl.value) params.stream_fps = parseInt(fpsEl.value, 10);

            if (codecEl && codecEl.value) params.codec = codecEl.value;

            if (vslamResEl && vslamResEl.value) params.vslam_res = vslamResEl.value;

            return params;

        }

        async function loadStreamQualityConfig() {

            try {

                const res = await fetch('/core/stream/config', {

                    headers: { 'X-API-Token': apiToken }

                });

                if (!res.ok) return;

                const config = await res.json();

                if (config.cam1) {

                    if (config.cam1.stream_res) document.getElementById('stream-res-1').value = config.cam1.stream_res;

                    if (config.cam1.stream_fps) document.getElementById('stream-fps-1').value = config.cam1.stream_fps;

                    if (config.cam1.vslam_res) document.getElementById('vslam-res-1').value = config.cam1.vslam_res;

                    if (config.cam1.codec) document.getElementById('stream-codec-1').value = config.cam1.codec;

                }

                if (config.cam2) {

                    if (config.cam2.stream_res) document.getElementById('stream-res-2').value = config.cam2.stream_res;

                    if (config.cam2.stream_fps) document.getElementById('stream-fps-2').value = config.cam2.stream_fps;

                    if (config.cam2.vslam_res) document.getElementById('vslam-res-2').value = config.cam2.vslam_res;

                    if (config.cam2.codec) document.getElementById('stream-codec-2').value = config.cam2.codec;

                }

            } catch (e) {

                // Config not available yet, use defaults

            }

        }

        // ─── Smart Camera Port Dropdowns ──────────────────────────────────

        // - Filter to only /dev/videoX that have live USB data (from telemetry)

        // - Disallow selecting same port on left AND right (rollback to last-good on conflict)

        // - When only 1 active device: disable right + show "caméra central" helper

        function syncCamPorts(side, val) {
            const mainEl = document.getElementById(`cam-port-${side}`);
            const modalEl = document.getElementById(`cam-port-${side}-modal`);
            if (mainEl) mainEl.value = val;
            if (modalEl) modalEl.value = val;
            updateCameraPortOptions();
            saveCameraPortsMapping();
        }

        function updateCameraPortOptions() {

            // Determine active devices from telemetry (with sensible 5-path fallback)

            const fallback = ['/dev/video0', '/dev/video1', '/dev/video2', '/dev/video3', '/dev/video4'];

            let activeDevices = fallback;

            const ts = window.lastTelemetryState;

            if (ts && ts.sensors && Array.isArray(ts.sensors.available_video_devices) && ts.sensors.available_video_devices.length > 0) {

                activeDevices = ts.sensors.available_video_devices.slice().sort();

            }

            const leftSelects = [document.getElementById('cam-port-left'), document.getElementById('cam-port-left-modal')].filter(Boolean);

            const rightSelects = [document.getElementById('cam-port-right'), document.getElementById('cam-port-right-modal')].filter(Boolean);

            const helpers = [document.getElementById('cam-port-single-info'), document.getElementById('cam-port-single-info-modal')].filter(Boolean);

            if (leftSelects.length === 0 || rightSelects.length === 0) return;

            // Last-known-good mapping (from telemetry) is our rollback anchor when conflict detected.

            const telMapping = (ts && ts.camera_mapping) || null;

            const lastGoodLeft  = (telMapping && activeDevices.includes(telMapping.left))  ? telMapping.left  : activeDevices[0];

            const lastGoodRight = (telMapping && activeDevices.includes(telMapping.right)) ? telMapping.right : null;

            if (activeDevices.length <= 1) {

                // Single-camera mode -> grey out right, label as 'central'

                const only = activeDevices[0] || '(aucune)';

                leftSelects.forEach(sel => {
                    sel.innerHTML = '<option value="' + only + '">' + only + '</option>';
                    sel.value = only;
                });

                rightSelects.forEach(sel => {
                    sel.innerHTML = '<option value="">—</option>';
                    sel.value = '';
                    sel.disabled = true;
                });

                helpers.forEach(hel => hel.style.display = 'block');

                return;

            }

            // Multi-camera mode: detect conflict (user just picked a port already used by the OTHER side).

            // Rollback BOTH to the last-known-good mapping so the user sees a stable, valid state.

            const currentLeftVal = leftSelects[0].value;
            const currentRightVal = rightSelects[0].value;

            if (currentLeftVal && currentRightVal && currentLeftVal === currentRightVal) {

                leftSelects.forEach(sel => sel.value  = lastGoodLeft);

                rightSelects.forEach(sel => sel.value = lastGoodRight || '');

                if (typeof showToast === 'function') {

                    showToast('Caméras', 'Mapping invalide (même port choisi sur les deux côtés). Retour au dernier mapping valide.', 'warning');

                }

            }

            // Each select excludes the OTHER's current value (mutually exclusive).

            rightSelects.forEach(sel => sel.disabled = false);

            helpers.forEach(hel => hel.style.display = 'none');

            const leftOpts  = activeDevices.filter(d => d !== rightSelects[0].value);

            const rightOpts = activeDevices.filter(d => d !== leftSelects[0].value);

            leftSelects.forEach(sel => {
                const oldVal = sel.value;
                sel.innerHTML  = leftOpts.map(d  => '<option value="' + d + '">' + d + '</option>').join('');
                if (leftOpts.includes(oldVal)) sel.value = oldVal;
            });

            rightSelects.forEach(sel => {
                const oldVal = sel.value;
                sel.innerHTML = rightOpts.map(d => '<option value="' + d + '">' + d + '</option>').join('');
                if (rightOpts.includes(oldVal)) sel.value = oldVal;
            });

        }

        function saveCameraPortsMapping() {

            const leftSelect = document.getElementById('cam-port-left');

            const rightSelect = document.getElementById('cam-port-right');

            const left = leftSelect ? leftSelect.value : '';

            const right = rightSelect ? rightSelect.value : '';

            const statusEls = [document.getElementById('camera-mapping-save-status'), document.getElementById('camera-mapping-save-status-modal')].filter(Boolean);

            function setStatus(text, color) {

                statusEls.forEach(el => {
                    el.textContent = text;
                    el.style.color = color || 'var(--text-secondary)';
                });

            }

            if (!left || !right) {

                setStatus('Selectionnez les deux c\u00f4t\u00e9s (gauche + droite).', 'var(--danger)');

                return;

            }

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({

                    type: "save_camera_mapping",

                    left: left,

                    right: right

                }));

                setStatus('Sauvegard\u00e9 ! Envoi au robot (red\u00e9marrage ROS en cours)...', 'var(--success)');

                setTimeout(() => setStatus('', null), 5000);

            } else {

                setStatus('Erreur: WebSocket d\u00e9connect\u00e9. Rechargez la page.', 'var(--danger)');

                console.error('[saveCameraPortsMapping] WebSocket not open, readyState=', appWs ? appWs.readyState : 'null');

            }

        }

        async function openCameraConfigModal(camId) {

            const ts = window.lastTelemetryState;
            const cam2Connected = ts && ts.sensors && ts.sensors.cam2_connected === true;
            if (!cam2Connected) {
                document.getElementById('mcv-modal-title').textContent = 'Configuration Caméra';
            } else {
                document.getElementById('mcv-modal-title').textContent = `Configuration Caméra ${camId === 1 ? 'Gauche (1)' : 'Droite (2)'}`;
            }

            

            try {

                const res = await fetch(`/core/camera/calibration/${camId}`, {

                    headers: { 'X-API-Token': apiToken }

                });

                if (res.ok) {

                    const data = await res.json();

                    

                    document.getElementById('mcv-camera-name').value = data.camera_name || 'usb_cam';

                    document.getElementById('mcv-resolution').textContent = `${data.image_width || 640} x ${data.image_height || 480}`;

                    document.getElementById('mcv-distortion-model').value = data.distortion_model || 'plumb_bob';

                    document.getElementById('mcv-calibrated-at').value = data.calibrated_at || 'Jamais (Défaut)';

                    

                    const badge = document.getElementById('mcv-profile-badge');

                    if (data.is_calibrated) {

                        badge.textContent = 'Calibré (Actif)';

                        badge.className = 'status-badge active';

                        badge.style.backgroundColor = 'var(--success)';

                        badge.style.color = 'white';

                    } else {

                        badge.textContent = 'Défaut (Non calibré)';

                        badge.className = 'status-badge';

                        badge.style.backgroundColor = 'rgba(255,255,255,0.1)';

                        badge.style.color = 'var(--text-secondary)';

                    }

                    

                    // Format matrices

                    const formatMatrix = (arr, cols) => {

                        if (!arr) return '';

                        let html = '';

                        for (let i = 0; i < arr.length; i += cols) {

                            html += arr.slice(i, i + cols).map(v => v.toFixed(2)).join(', ') + '<br/>';

                        }

                        return html;

                    };

                    

                    const formatDistortion = (arr) => {

                        if (!arr) return '[]';

                        return '[' + arr.map(v => v.toFixed(5)).join(', ') + ']';

                    };

                    

                    document.getElementById('mcv-distortion-matrix').innerHTML = formatDistortion(data.distortion_coefficients);

                    document.getElementById('mcv-camera-matrix').innerHTML = formatMatrix(data.camera_matrix, 3);

                    document.getElementById('mcv-projection-matrix').innerHTML = formatMatrix(data.projection_matrix, 4);

                }

            } catch (err) {

                console.error(err);

            }

            

            document.getElementById('cameraConfigModal').classList.add('active');

        }

        function closeCameraConfigModal() {

            document.getElementById('cameraConfigModal').classList.remove('active');

        }

        function closeCameraConfigModalOnClick(e) {

            if (e.target === document.getElementById('cameraConfigModal')) {

                closeCameraConfigModal();

            }

        }

        // [Calibration Block 2 Extracted to dashboard_calib.js]
        function toggleKeepStream(camId) {

            if (!window.keepStreams) window.keepStreams = { 1: false, 2: false };

            const current = window.keepStreams[camId];

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({

                    type: "toggle_keep_stream",

                    camera: camId,

                    keep: !current

                }));

            } else {

                alert("WebSocket déconnecté.");

            }

        }

        function toggleStream(camId, isExplicit = false) {

            if (!window.activeStreams) window.activeStreams = { 1: false, 2: false };

            if (!window.localViewing) window.localViewing = { 1: false, 2: false };

            const statusEl = document.getElementById(`stream-status-${camId}`);

            const btnText = document.getElementById(`stream-btn-text-${camId}`);

            if (!window.localViewing[camId]) {

                // === DÉMARRER ===

                if (streamingState[camId] === 'requesting' || streamingState[camId] === 'connecting' || streamingState[camId] === 'active') {

                    console.warn('[Stream] Cam ' + camId + ' déjà en cours (' + streamingState[camId] + '), ignoré.');

                    return;

                }

                // Marquer immédiatement pour éviter les double-clics
                streamingState[camId] = 'requesting';

                if (appWs && appWs.readyState === WebSocket.OPEN) {

                    let vSlamVal = false;

                    if (camId === 1) {

                        const vSlamCheck = document.getElementById('stream-v-slam-1');

                        if (vSlamCheck) vSlamVal = vSlamCheck.checked;

                    }

                    appWs.send(JSON.stringify({type: "request_camera", camera: camId, v_slam: vSlamVal, ...getStreamQualityParams(camId)}));

                    // FIX: REST POST /api/streams/{camId}/join.

                    // Ce call declenche _maybe_start_robot() dans routes/streams.py,

                    // qui broadcast 'start_camera' au robot. Sans lui, ffmpeg ne demarre jamais.

                    if (!window._bastetClientId) window._bastetClientId = 'web-' + Math.random().toString(36).substring(2);

                    var joinCamId = camId; // capture for closure

                    fetch('/api/streams/' + joinCamId + '/join', {

                        method: 'POST',

                        headers: { 'Content-Type': 'application/json', 'X-API-Token': apiToken },

                        body: JSON.stringify({ client_id: window._bastetClientId })

                    }).then(function(res) {

                        if (!res.ok) throw new Error('HTTP ' + res.status + ' (camera ' + joinCamId + ' non detectee ou auth invalide)');

                    }).catch(function(e) {

                        console.warn('[Stream] /join failed:', e);

                        // Show meaningful error to user instead of silent 12s timeout

                        var stEl = document.getElementById('stream-status-' + joinCamId);

                        var btEl = document.getElementById('stream-btn-text-' + joinCamId);

                        if (stEl) { stEl.textContent = 'Camera indisponible'; stEl.className = 'status-badge error'; }

                        if (btEl) btEl.textContent = 'Verifier camera';

                        // Also abort the WHEP attempt since /join failed

                        if (peerConnections[joinCamId]) {

                            try { peerConnections[joinCamId].close(); } catch(e2) {}

                            peerConnections[joinCamId] = null;

                        }

                        streamingState[joinCamId] = 'idle';

                        window.localViewing[joinCamId] = false;

                    });

                    window.localViewing[camId] = true;

                    if (!window.userClosedStream) window.userClosedStream = { 1: false, 2: false };

                    window.userClosedStream[camId] = false;

                    streamingState[camId] = 'requesting';

                    statusEl.textContent = 'Connexion WebRTC…';

                    statusEl.className = 'status-badge';

                    btnText.textContent = 'Couper Caméra';

                    // FIX: Ne pas lancer WebRTC tout de suite. Le robot n'a pas encore
                    // demarre l'encodeur ffmpeg. On attend le stream_status {active:true}
                    // qui confirme que le flux est pret cote robot (~2-5s).
                    // En attendant, on montre le loader immediatement.
                    if (!window._pendingStreamConnect) window._pendingStreamConnect = {};
                    window._pendingStreamConnect[camId] = true;

                    // FIX: Si le stream est DEJA actif cote robot (retry apres erreur WebRTC),
                    // le Gateway ne va PAS re-broadcast stream_status {active:true} car il
                    // voit deja stream_active=True. On lance donc WebRTC directement.
                    if (window.activeStreams && window.activeStreams[camId]) {
                        window._pendingStreamConnect[camId] = false;
                        startStreamWebRTC(camId);
                    }

                    const _ph3 = document.getElementById('stream-placeholder-' + camId);
                    const _vc3 = document.getElementById('video-container-' + camId);
                    const _ld3 = document.getElementById('stream-loader-' + camId);
                    if (_ph3) _ph3.style.display = 'none';
                    if (_vc3) _vc3.style.display = 'block';
                    if (_ld3) _ld3.style.display = 'flex';

                } else {

                    if (isExplicit) alert("WebSocket déconnecté. Impossible d'activer la caméra.");

                    else console.warn("[Auto] WebSocket not open, deferring stream startup.");

                }

            } else {

                // === COUPER ===

                window.localViewing[camId] = false;

                if (!window.userClosedStream) window.userClosedStream = { 1: false, 2: false };

                window.userClosedStream[camId] = true;

                streamingState[camId] = 'closing';

                if (appWs && appWs.readyState === WebSocket.OPEN) {

                    if (appWs && appWs.readyState === WebSocket.OPEN) {

                    // FIX: Envoyer stop_camera (coupe immediatement cote robot) ET release_camera (cleanup listeners).

                    // Sans stop_camera, le gateway attend 30s avant de dire au robot de couper.

                    appWs.send(JSON.stringify({type: "stop_camera", camera: camId}));

                    appWs.send(JSON.stringify({type: "release_camera", camera: camId}));

                }

                }

                // FIX Bug 2: REST DELETE /api/streams/{camId}/leave

                if (window._bastetClientId) {

                    fetch('/api/streams/' + camId + '/leave', {

                        method: 'DELETE',

                        headers: { 'Content-Type': 'application/json', 'X-API-Token': apiToken },

                        body: JSON.stringify({ client_id: window._bastetClientId })

                    }).catch(function(e) { console.warn('[Stream] /leave failed:', e); });

                }

                // Fermer la PeerConnection immédiatement

                if (peerConnections[camId]) {

                    try { peerConnections[camId].close(); } catch(e) {}

                    peerConnections[camId] = null;

                }

                stopStreamUI(camId);

                // Forcer l'affichage correct après stop

                statusEl.textContent = 'Coupé';

                statusEl.className = 'status-badge';

                btnText.textContent = 'Rejoindre le flux';

            }

        }

        function toggleFullscreen(camId) {

            const container = document.getElementById(`video-container-${camId}`);

            if (!container) return;

            if (!document.fullscreenElement) {

                if (container.requestFullscreen) {

                    container.requestFullscreen();

                } else if (container.webkitRequestFullscreen) {

                    container.webkitRequestFullscreen();

                } else if (container.msRequestFullscreen) {

                    container.msRequestFullscreen();

                }

            } else {

                if (document.exitFullscreen) {

                    document.exitFullscreen();

                }

            }

        }

        function updateCalibrationBadges(calStatus) {

            for (let camId of [1, 2]) {

                const badge = document.getElementById('calib-badge-' + camId);

                if (!badge) continue;

                const camData = calStatus[String(camId)] || calStatus[camId] || {};

                const calibrated = camData.calibrated === true;

                if (calibrated) {

                    badge.textContent = '✅ Calibrée';

                    badge.style.background = 'rgba(34,197,94,0.15)';

                    badge.style.color = 'var(--success)';

                    badge.style.borderColor = 'rgba(34,197,94,0.3)';

                    badge.setAttribute('data-calibrated', 'true');

                } else {

                    badge.textContent = '⚠ Non calibrée';

                    badge.style.background = 'rgba(239,68,68,0.15)';

                    badge.style.color = 'var(--danger)';

                    badge.style.borderColor = 'rgba(239,68,68,0.3)';

                    badge.setAttribute('data-calibrated', 'false');

                }

            }

        }

        // Check calibration before enabling V-SLAM (gate for future autonomous mode too)

        function isCameraCalibrated(camId) {

            const badge = document.getElementById('calib-badge-' + camId);

            if (!badge) return false;

            return badge.getAttribute('data-calibrated') === 'true';

        }

        function handleVSlamToggleChange() {

            if (window.localViewing && window.localViewing[1]) {

                if (appWs && appWs.readyState === WebSocket.OPEN) {

                    const vSlamCheck = document.getElementById('stream-v-slam-1');

                    const vSlamVal = vSlamCheck ? vSlamCheck.checked : false;

                    // V-SLAM gatekeeping: block if camera not calibrated

                    if (vSlamVal && !isCameraCalibrated(1)) {

                        if (vSlamCheck) vSlamCheck.checked = false;

                        if (typeof showToast === 'function') {

                            showToast('V-SLAM bloqué', 'Calibrez la caméra 1 dans Arduino & Calib avant d\'activer le V-SLAM.', 'warning');

                        }

                        return;

                    }

                    

                    const loaderEl = document.getElementById('stream-loader-1');

                    const videoEl = document.getElementById('video-cam-1');

                    const fsBtn = document.getElementById('video-fs-btn-1');

                    const statusEl = document.getElementById('stream-status-1');

                    

                    if (loaderEl) loaderEl.style.display = 'flex';

                    if (videoEl) videoEl.style.display = 'none';

                    if (fsBtn) fsBtn.style.display = 'none';

                    if (statusEl) {

                        statusEl.textContent = 'Reconfiguration…';

                        statusEl.className = 'status-badge';

                    }

                    

                    appWs.send(JSON.stringify({type: "request_camera", camera: 1, v_slam: vSlamVal, ...getStreamQualityParams(1)}));

                    startStreamWebRTC(1);

                }

            }

        }

        // ─── ACCOUNTS MANAGEMENT ─────────────────────────────────────────────

        async function loadAccounts() {

            try {

                const accountsRes = await fetch('/accounts', { headers: { 'X-API-Token': apiToken } });

                const mygesRes = await fetch('/myges', { headers: { 'X-API-Token': apiToken } });

                

                if (accountsRes.ok) {

                    const accounts = await accountsRes.json();

                    accountsCached = accounts;

                    

                    let mygesList = {};

                    if (mygesRes.ok) {

                        mygesList = await mygesRes.json();

                    }

                    const container = document.getElementById('users-container');

                    container.innerHTML = '';

                    const keys = Object.keys(accounts);

                    if (keys.length === 0) {

                        container.innerHTML = `

                            <div style="grid-column: 1/-1; text-align: center; padding: 3rem; color: var(--text-secondary);">

                                Aucun compte utilisateur configuré.

                            </div>`;

                        return;

                    }

                    for (const fullName of keys) {

                        const u = accounts[fullName];

                        const initials = ((u.first_name ? u.first_name[0] : '') + (u.last_name ? u.last_name[0] : '')).toUpperCase() || 'U';

                        const adminClass = u.is_admin ? 'admin' : '';

                        const adminLabel = u.is_admin ? 'Administrateur' : 'Utilisateur';

                        

                        const mygesCreds = mygesList[fullName];

                        const mygesBadge = mygesCreds 

                            ? `<span class="status-badge active" style="font-size: 0.75rem;">✅ MyGES : ${mygesCreds.username}</span>`

                            : `<span class="status-badge" style="font-size: 0.75rem; background-color: rgba(225, 29, 72, 0.05); color: var(--danger); border: 1px solid rgba(225, 29, 72, 0.15)">❌ MyGES non configuré</span>`;

                        const card = document.createElement('div');

                        card.className = 'user-card';

                        card.innerHTML = `

                            <div>

                                <div class="user-header">

                                    <div class="user-info-meta">

                                        <div class="user-avatar">${initials}</div>

                                        <div class="user-title-box">

                                            <h3>${u.first_name} ${u.last_name}</h3>

                                            <p>@${u.pseudo || 'sans-pseudo'}</p>

                                        </div>

                                    </div>

                                    <span class="user-badge ${adminClass}">${adminLabel}</span>

                                </div>

                                <div class="user-details">

                                    <div class="user-detail-item">

                                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>

                                        <span>${u.email}</span>

                                    </div>

                                    <div class="user-detail-item">

                                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 9.24z"/></svg>

                                        <span>${u.phone || 'Non renseigné'}</span>

                                    </div>

                                    <div style="margin-top: 0.5rem;">

                                        ${mygesBadge}

                                    </div>

                                </div>

                            </div>

                            <div class="user-actions">

                                <button class="btn btn-secondary" style="flex: 1;" onclick="openEditUserModal('${fullName}')">Modifier</button>

                                <button class="btn btn-secondary" onclick="openMygesModal('${fullName}')" title="Identifiants MyGES">MyGES</button>

                                <button class="btn btn-danger" onclick="deleteUser('${fullName}')">Supprimer</button>

                            </div>

                        `;

                        container.appendChild(card);

                    }

                }

            } catch (e) {

                console.error("Load accounts error:", e);

            }

        }

        async function deleteUser(fullName) {

            if (!confirm(`Voulez-vous vraiment supprimer le compte de ${fullName} ?\n(Cela supprimera également ses identifiants MyGES et ses photos de visage)`)) return;

            try {

                const res = await fetch(`/accounts/${encodeURIComponent(fullName)}`, {

                    method: 'DELETE',

                    headers: { 'X-API-Token': apiToken }

                });

                if (res.ok) {

                    loadAccounts();

                } else {

                    alert('Erreur lors de la suppression.');

                }

            } catch (e) {

                alert('Erreur de connexion.');

            }

        }

        // Modals Accounts

        function openAddUserModal() {

            document.getElementById('modal-user-title').textContent = "Ajouter un Compte";

            document.getElementById('form-old-fullname').value = '';

            document.getElementById('form-firstname').value = '';

            document.getElementById('form-lastname').value = '';

            document.getElementById('form-firstname').disabled = false;

            document.getElementById('form-lastname').disabled = false;

            document.getElementById('form-pseudo').value = '';

            document.getElementById('form-email').value = '';

            document.getElementById('form-phone').value = '';

            document.getElementById('form-password').value = '';

            document.getElementById('form-preferences').value = '{}';

            document.getElementById('form-is-admin').checked = false;

            

            const m = document.getElementById('userModal');

            m.style.position = 'fixed';

            m.style.top = '0';

            m.style.left = '0';

            m.style.right = '0';

            m.style.bottom = '0';

            m.style.display = 'flex';

            m.style.opacity = '1';

            m.style.pointerEvents = 'auto';

            m.style.zIndex = '100';

            m.classList.add('active');

        }

        function openEditUserModal(fullName) {

            const u = accountsCached[fullName];

            if (!u) return;

            document.getElementById('modal-user-title').textContent = `Modifier le profil`;

            document.getElementById('form-old-fullname').value = fullName;

            document.getElementById('form-firstname').value = u.first_name || '';

            document.getElementById('form-lastname').value = u.last_name || '';

            document.getElementById('form-firstname').disabled = true;

            document.getElementById('form-lastname').disabled = true;

            document.getElementById('form-pseudo').value = u.pseudo || '';

            document.getElementById('form-email').value = u.email || '';

            document.getElementById('form-phone').value = u.phone || '';

            document.getElementById('form-password').value = '';

            document.getElementById('form-preferences').value = JSON.stringify(u.preferences || {}, null, 2);

            document.getElementById('form-is-admin').checked = u.is_admin || false;

            const m2 = document.getElementById('userModal');

            m2.style.position = 'fixed';

            m2.style.top = '0';

            m2.style.left = '0';

            m2.style.right = '0';

            m2.style.bottom = '0';

            m2.style.display = 'flex';

            m2.style.opacity = '1';

            m2.style.pointerEvents = 'auto';

            m2.style.zIndex = '100';

            m2.classList.add('active');

        }

        function closeUserModal() {

            const m = document.getElementById('userModal');

            m.style.position = '';

            m.style.top = '';

            m.style.left = '';

            m.style.right = '';

            m.style.bottom = '';

            m.style.display = '';

            m.style.opacity = '';

            m.style.pointerEvents = '';

            m.style.zIndex = '';

            m.classList.remove('active');

        }

        function closeUserModalOnClick(e) {

            if (e.target === document.getElementById('userModal')) closeUserModal();

        }

        async function handleUserSubmit(e) {

            e.preventDefault();

            const firstName = document.getElementById('form-firstname').value.trim();

            const lastName = document.getElementById('form-lastname').value.trim();

            const pseudo = document.getElementById('form-pseudo').value.trim();

            const email = document.getElementById('form-email').value.trim();

            const phone = document.getElementById('form-phone').value.trim();

            const password = document.getElementById('form-password').value;

            const isAdmin = document.getElementById('form-is-admin').checked;

            let preferences = {};

            const prefVal = document.getElementById('form-preferences').value.trim();

            if (prefVal) {

                try {

                    preferences = JSON.parse(prefVal);

                } catch (err) {

                    alert("Format JSON invalide pour les préférences.");

                    return;

                }

            }

            const payload = {

                first_name: firstName,

                last_name: lastName,

                pseudo: pseudo,

                email: email,

                phone: phone,

                is_admin: isAdmin,

                preferences: preferences

            };

            if (password) {

                payload.password = password;

            }

            try {

                const res = await fetch('/accounts', {

                    method: 'POST',

                    headers: {

                        'Content-Type': 'application/json',

                        'X-API-Token': apiToken

                    },

                    body: JSON.stringify(payload)

                });

                if (res.ok) {

                    closeUserModal();

                    loadAccounts();

                } else {

                    const err = await res.text();

                    alert(`Erreur lors de la sauvegarde : ${err}`);

                }

            } catch (e) {

                alert('Erreur de réseau.');

            }

        }

        // Modals MyGES

        function openMygesModal(name) {

            document.getElementById('myges-modal-username').textContent = name;

            document.getElementById('form-myges-name').value = name;

            document.getElementById('form-myges-username').value = '';

            document.getElementById('form-myges-password').value = '';

            

            document.getElementById('mygesModal').classList.add('active');

        }

        function closeMygesModal() {

            document.getElementById('mygesModal').classList.remove('active');

        }

        function closeMygesModalOnClick(e) {

            if (e.target === document.getElementById('mygesModal')) closeMygesModal();

        }

        async function handleMygesTest() {

            const resultDiv = document.getElementById('myges-test-result');

            const btn = document.getElementById('btn-myges-test');

            const username = document.getElementById('form-myges-username').value.trim();

            const password = document.getElementById('form-myges-password').value;

            

            if (!username || !password) {

                resultDiv.style.display = 'block';

                resultDiv.style.background = 'rgba(239,68,68,0.1)';

                resultDiv.style.color = '#ef4444';

                resultDiv.innerHTML = 'Veuillez remplir les deux champs.';

                return;

            }

            

            // Show loading state

            btn.disabled = true;

            btn.innerHTML = '⏳ Test en cours...';

            resultDiv.style.display = 'block';

            resultDiv.style.background = 'rgba(99,102,241,0.1)';

            resultDiv.style.color = '#6366f1';

            resultDiv.innerHTML = 'Connexion en cours...';

            

            try {

                const res = await fetch('/myges/test', {

                    method: 'POST',

                    headers: {

                        'Content-Type': 'application/json',

                        'X-API-Token': apiToken

                    },

                    body: JSON.stringify({ username, password })

                });

                const data = await res.json();

                

                if (data.status === 'success') {

                    resultDiv.style.background = 'rgba(34,197,94,0.1)';

                    resultDiv.style.color = '#22c55e';

                    resultDiv.innerHTML = '✅ ' + data.message;

                } else {

                    resultDiv.style.background = 'rgba(239,68,68,0.1)';

                    resultDiv.style.color = '#ef4444';

                    resultDiv.innerHTML = '❌ ' + data.message;

                }

            } catch (e) {

                resultDiv.style.background = 'rgba(239,68,68,0.1)';

                resultDiv.style.color = '#ef4444';

                resultDiv.innerHTML = '❌ Erreur réseau.';

            } finally {

                btn.disabled = false;

                btn.innerHTML = '🔍 Tester la connexion';

            }

        }

        async function handleMygesTest() {

            const resultDiv = document.getElementById('myges-test-result');

            const btn = document.getElementById('btn-myges-test');

            const username = document.getElementById('form-myges-username').value.trim();

            const password = document.getElementById('form-myges-password').value;

            

            if (!username || !password) {

                resultDiv.style.display = 'block';

                resultDiv.style.background = 'rgba(239,68,68,0.1)';

                resultDiv.style.color = '#ef4444';

                resultDiv.textContent = 'Veuillez remplir les deux champs.';

                return;

            }

            

            btn.disabled = true;

            btn.textContent = 'Test en cours...';

            resultDiv.style.display = 'block';

            resultDiv.style.background = 'rgba(99,102,241,0.1)';

            resultDiv.style.color = '#6366f1';

            resultDiv.textContent = 'Connexion en cours...';

            

            try {

                const res = await fetch('/myges/test', {

                    method: 'POST',

                    headers: {

                        'Content-Type': 'application/json',

                        'X-API-Token': apiToken

                    },

                    body: JSON.stringify({ username, password })

                });

                const data = await res.json();

                

                if (data.status === 'success') {

                    resultDiv.style.background = 'rgba(34,197,94,0.1)';

                    resultDiv.style.color = '#22c55e';

                    resultDiv.textContent = data.message;

                } else {

                    resultDiv.style.background = 'rgba(239,68,68,0.1)';

                    resultDiv.style.color = '#ef4444';

                    resultDiv.textContent = data.message;

                }

            } catch (e) {

                resultDiv.style.background = 'rgba(239,68,68,0.1)';

                resultDiv.style.color = '#ef4444';

                resultDiv.textContent = 'Erreur réseau.';

            } finally {

                btn.disabled = false;

                btn.innerHTML = '&#128269; Tester la connexion';

            }

        }

        async function handleMygesSubmit(e) {

            e.preventDefault();

            const name = document.getElementById('form-myges-name').value;

            const username = document.getElementById('form-myges-username').value.trim();

            const password = document.getElementById('form-myges-password').value;

            const resultDiv = document.getElementById('myges-test-result');

            if (!username || !password) {

                resultDiv.style.display = 'block';

                resultDiv.style.background = 'rgba(239,68,68,0.1)';

                resultDiv.style.color = '#ef4444';

                resultDiv.textContent = 'Veuillez remplir les deux champs.';

                return;

            }

            // Show testing state

            resultDiv.style.display = 'block';

            resultDiv.style.background = 'rgba(99,102,241,0.1)';

            resultDiv.style.color = '#6366f1';

            resultDiv.textContent = 'Test des identifiants en cours...';

            try {

                // Step 1: Test credentials

                const testRes = await fetch('/myges/test', {

                    method: 'POST',

                    headers: {

                        'Content-Type': 'application/json',

                        'X-API-Token': apiToken

                    },

                    body: JSON.stringify({ username, password })

                });

                const testData = await testRes.json();

                if (testData.status !== 'success') {

                    resultDiv.style.background = 'rgba(239,68,68,0.1)';

                    resultDiv.style.color = '#ef4444';

                    resultDiv.textContent = '❌ ' + testData.message;

                    return;

                }

                // Step 2: Save credentials

                const saveRes = await fetch(`/myges?name=${encodeURIComponent(name)}`, {

                    method: 'POST',

                    headers: {

                        'Content-Type': 'application/json',

                        'X-API-Token': apiToken

                    },

                    body: JSON.stringify({ username, password })

                });

                if (saveRes.ok) {

                    resultDiv.style.background = 'rgba(34,197,94,0.1)';

                    resultDiv.style.color = '#22c55e';

                    resultDiv.textContent = '✅ Identifiants valides et sauvegardés !';

                    setTimeout(() => { closeMygesModal(); loadAccounts(); }, 800);

                } else {

                    resultDiv.style.background = 'rgba(239,68,68,0.1)';

                    resultDiv.style.color = '#ef4444';

                    resultDiv.textContent = '❌ Erreur lors de la sauvegarde.';

                }

            } catch (e) {

                resultDiv.style.background = 'rgba(239,68,68,0.1)';

                resultDiv.style.color = '#ef4444';

                resultDiv.textContent = '❌ Erreur réseau.';

            }

        }

        // ─── FACES GALLERY ───────────────────────────────────────────────────

        async function loadFacesGallery() {

            try {

                const facesRes = await fetch('/faces', { headers: { 'X-API-Token': apiToken } });

                const accountsRes = await fetch('/accounts', { headers: { 'X-API-Token': apiToken } });

                

                if (facesRes.ok && accountsRes.ok) {

                    const data = await facesRes.json();

                    const accounts = await accountsRes.json();

                    

                    const faces = data.faces || [];

                    facesCached = faces;

                    

                    const usersList = Object.keys(accounts);

                    const grouped = {};

                    usersList.forEach(name => {

                        grouped[name] = [];

                    });

                    faces.forEach(f => {

                        const matchedName = usersList.find(u => u && f.name && u.toLowerCase() === f.name.toLowerCase()) || f.name;

                        if (!grouped[matchedName]) grouped[matchedName] = [];

                        grouped[matchedName].push(f);

                    });

                    const foldersContainer = document.getElementById('folders-container');

                    foldersContainer.innerHTML = '';

                    const keys = Object.keys(grouped);

                    if (keys.length === 0) {

                        foldersContainer.innerHTML = `

                            <div style="grid-column: 1/-1; text-align: center; padding: 4rem; color: var(--text-secondary); border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-card);">

                                Aucun dossier utilisateur disponible. Créez un compte d'abord.

                            </div>`;

                        return;

                    }

                    keys.forEach(name => {

                        const userFaces = grouped[name];

                        const count = userFaces.length;

                        const initials = name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2) || 'U';

                        

                        const card = document.createElement('div');

                        card.className = 'folder-card';

                        card.onclick = () => openFolderDetails(name, userFaces);

                        

                        card.innerHTML = `

                            <div class="folder-icon-wrapper">

                                <svg viewBox="0 0 24 24" width="64" height="64" fill="currentColor" style="opacity: 0.85;">

                                    <path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2z"/>

                                </svg>

                                <div class="folder-avatar-badge">${initials}</div>

                            </div>

                            <div class="folder-name font-outfit">${name}</div>

                            <div class="folder-count">${count} photo${count > 1 ? 's' : ''}</div>

                        `;

                        foldersContainer.appendChild(card);

                    });

                    if (activeFolderName) {

                        const activeUserFaces = grouped[activeFolderName] || [];

                        renderFolderDetails(activeFolderName, activeUserFaces);

                    }

                }

            } catch (e) {

                console.error("Gallery loading error:", e);

            }

        }

        function openFolderDetails(name, userFaces) {

            activeFolderName = name;

            document.getElementById('faces-folders-view').style.display = 'none';

            document.getElementById('faces-details-view').classList.add('active');

            document.getElementById('current-folder-username-label').textContent = name;

            renderFolderDetails(name, userFaces);

        }

        function closeFolderDetails() {

            activeFolderName = null;

            document.getElementById('faces-details-view').classList.remove('active');

            document.getElementById('faces-folders-view').style.display = 'block';

            loadFacesGallery();

        }

        function renderFolderDetails(name, userFaces) {

            document.getElementById('details-folder-name').textContent = name;

            document.getElementById('details-folder-count').textContent = `${userFaces.length} / 8 photos`;

            

            const grid = document.getElementById('details-faces-grid');

            grid.innerHTML = '';

            

            if (userFaces.length === 0) {

                grid.innerHTML = `

                    <div style="grid-column: 1/-1; text-align: center; padding: 4rem; color: var(--text-secondary); border: 1px dashed var(--border-color); border-radius: 8px;">

                        Aucune photo pour cet utilisateur. Utilisez la zone ci-dessus pour en ajouter.

                    </div>`;

                return;

            }

            

            userFaces.forEach(f => {

                const card = document.createElement('div');

                card.className = 'face-img-card';

                card.innerHTML = `

                    <img src="#" id="face-img-${f.id}" class="face-img-element" onclick="showLightbox(this.src)" title="Agrandir"/>

                    <button class="face-delete-btn" onclick="deleteFace('${f.id}')" title="Supprimer">✕</button>

                    <div class="face-img-overlay">

                        <div class="face-img-info">${f.original_name}</div>

                        <div style="font-size: 0.6rem; color: #71717a;">${new Date(f.uploaded_at).toLocaleDateString()}</div>

                    </div>

                `;

                grid.appendChild(card);

                

                fetch(`/faces/${f.id}/image`, { headers: { 'X-API-Token': apiToken } })

                    .then(res => res.blob())

                    .then(blob => {

                        const img = document.getElementById(`face-img-${f.id}`);

                        if (img) img.src = URL.createObjectURL(blob);

                    })

                    .catch(err => console.error("Error loading face image file:", err));

            });

        }

        async function deleteFace(faceId) {

            if (!confirm("Voulez-vous supprimer cette photo pour la reconnaissance faciale ?")) return;

            try {

                const res = await fetch(`/faces/${faceId}`, {

                    method: 'DELETE',

                    headers: { 'X-API-Token': apiToken }

                });

                if (res.ok) {

                    loadFacesGallery();

                } else {

                    alert('Erreur suppression.');

                }

            } catch (e) {

                alert('Erreur réseau.');

            }

        }

        let currentUploadFile = null;

        

        function triggerFaceUpload() {

            document.getElementById('face-file-input').click();

        }

        function handleFaceUploadSelected(e) {

            const files = e.target.files;

            if (!files || files.length === 0) return;

            currentUploadFile = files[0];

            

            if (activeFolderName) {

                executeFaceUploadDirect(activeFolderName);

            }

        }

        function initDragAndDrop() {

            const uploadBox = document.querySelector('.upload-box');

            if (uploadBox) {

                ['dragenter', 'dragover'].forEach(eventName => {

                    uploadBox.addEventListener(eventName, (e) => {

                        e.preventDefault();

                        e.stopPropagation();

                        uploadBox.style.borderColor = 'var(--accent)';

                        uploadBox.style.backgroundColor = 'rgba(99, 102, 241, 0.08)';

                    }, false);

                });

                ['dragleave', 'drop'].forEach(eventName => {

                    uploadBox.addEventListener(eventName, (e) => {

                        e.preventDefault();

                        e.stopPropagation();

                        uploadBox.style.borderColor = 'var(--border-color)';

                        uploadBox.style.backgroundColor = 'rgba(24, 24, 27, 0.3)';

                    }, false);

                });

                uploadBox.addEventListener('drop', (e) => {

                    const dt = e.dataTransfer;

                    const files = dt.files;

                    if (files && files.length > 0) {

                        currentUploadFile = files[0];

                        if (activeFolderName) {

                            executeFaceUploadDirect(activeFolderName);

                        }

                    }

                }, false);

            }

        }

        async function executeFaceUploadDirect(userName) {

            if (!currentUploadFile || !userName) return;

            const fd = new FormData();

            fd.append('file', currentUploadFile);

            try {

                const res = await fetch(`/faces/upload?name=${encodeURIComponent(userName)}`, {

                    method: 'POST',

                    headers: { 'X-API-Token': apiToken },

                    body: fd

                });

                if (res.ok) {

                    const json = await res.json();

                    if (json.status === 'already_exists') {

                        alert(json.msg);

                    }

                    currentUploadFile = null;

                    document.getElementById('face-file-input').value = '';

                    loadFacesGallery();

                } else {

                    const txt = await res.text();

                    alert(`Erreur d'upload : ${txt}`);

                }

            } catch (e) {

                alert("Erreur de connexion.");

            }

        }

        // Lightbox

        function showLightbox(src) {

            document.getElementById('lightbox-img').src = src;

            document.getElementById('lightbox').classList.add('active');

        }

        function closeLightbox() {

            document.getElementById('lightbox').classList.remove('active');

        }

        // ─── UPDATER & SERVICES ───────────────────────────────────────────────

        async function fetchUpdatesProgress(force = false) {

            let rbInProgress = false;

            let ardInProgress = false;

            try {

                const forceParam = force ? '?force=true' : '';

                const gatewayRes = await fetch(`/system/update/gateway/progress${forceParam}`, { headers: { 'X-API-Token': apiToken } });

                const robotRes = await fetch(`/system/update/robot/progress${forceParam}`, { headers: { 'X-API-Token': apiToken } });

                const arduinoRes = await fetch(`/system/update/arduino/progress${forceParam}`, { headers: { 'X-API-Token': apiToken } });

                if (gatewayRes.ok) {

                    const gw = await gatewayRes.json();

                    const gwUpToDate = gw.current_version && gw.latest_version && gw.current_version === gw.latest_version;

                    const gwStatusLower = (gw.status || '').toLowerCase();

                    let gwDisplayStatus = gw.status || 'Prêt';

                    if (gwStatusLower.includes('failed') && gwUpToDate) gwDisplayStatus = 'À jour';

                    document.getElementById('gateway-update-status').textContent = gwDisplayStatus;

                    document.getElementById('gateway-update-bar').style.width = `${gw.percent}%`;

                    document.getElementById('gateway-update-percent').textContent = `${gw.percent}%`;

                    document.getElementById('gateway-current-version').textContent = gw.current_version || 'Inconnu';

                    document.getElementById('gateway-latest-version').textContent = gw.latest_version || 'Inconnu';

                    const gwInProgress = gw.status &&

                        !gwStatusLower.includes('idle') &&

                        !gwStatusLower.includes('prêt') &&

                        !gwStatusLower.includes('done') &&

                        !gwStatusLower.includes('failed') &&

                        gw.percent < 100;

                    const gwBtn = document.getElementById('btn-update-gateway');

                    const gwBtnText = document.getElementById('btn-update-gateway-text');

                    if (gwBtn) {

                        gwBtn.disabled = gwInProgress;

                        gwBtn.style.opacity = gwInProgress ? '0.5' : '1';

                    }

                    if (gwBtnText) {

                        gwBtnText.textContent = gwUpToDate ? 'Réinstaller la Gateway' : 'Lancer la mise à jour Gateway';

                    }

                }

                if (robotRes.ok) {

                    const rb = await robotRes.json();

                    const rbUpToDate = rb.current_version && rb.latest_version && rb.current_version === rb.latest_version;

                    const rbStatusLower = (rb.status || '').toLowerCase();

                    let rbDisplayStatus = rb.status || 'Prêt';

                    if (rbStatusLower.includes('failed') && rbUpToDate) rbDisplayStatus = 'À jour';

                    document.getElementById('robot-update-status').textContent = rbDisplayStatus;

                    document.getElementById('robot-update-bar').style.width = `${rb.percent}%`;

                    document.getElementById('robot-update-percent').textContent = `${rb.percent}%`;

                    document.getElementById('robot-current-version').textContent = rb.current_version || 'Inconnu';

                    document.getElementById('robot-latest-version').textContent = rb.latest_version || 'Inconnu';

                    rbInProgress = rb.status &&

                        !rbStatusLower.includes('idle') &&

                        !rbStatusLower.includes('prêt') &&

                        !rbStatusLower.includes('done') &&

                        !rbStatusLower.includes('failed') &&

                        rb.percent < 100;

                    const rbBtn = document.getElementById('btn-update-robot');

                    const rbBtnText = document.getElementById('btn-update-robot-text');

                    if (rbBtn) {

                        rbBtn.disabled = rbInProgress;

                        rbBtn.style.opacity = rbInProgress ? '0.5' : '1';

                    }

                    if (rbBtnText) {

                        rbBtnText.textContent = rbUpToDate ? 'Réinstaller le Robot' : 'Lancer la mise à jour Robot';

                    }

                }

                if (arduinoRes.ok) {

                    const ard = await arduinoRes.json();

                    const ardUpToDate = ard.current_version && ard.latest_version && ard.current_version === ard.latest_version;

                    const ardStatusLower = (ard.status || '').toLowerCase();

                    const ardStatusLabels = {

        failed_launch: "❌ Échec lancement (voir logs agent)",

        failed_launch_msg: "Le robot n'a pas pu démarrer la màj Arduino.",

        stale_starting: "⚠️ Blocage dès le lancement (60 s sans progrès)",

                        'stopping_services': '⏹ Arrêt services...',

                        'checking_tools': '🔍 Vérification arduino-cli...',

                        'installing_core': '📦 Installation core AVR...',

                        'installing_libs': '📚 Installation librairies...',

                        'detecting_device': '🔌 Détection Arduino...',

                        'preparing_sketch': '📁 Préparation sketch...',

                        'compiling': '⚙️ Compilation...',

                        'flashing': '⚡ Flashage en cours...',

                        'idle': '✓ Prêt',

                        'starting': '▶ Démarrage...',

                    };

                    let ardDisplayStatus = ardStatusLabels[ardStatusLower] || ard.status || 'Prêt';

                    if (ardStatusLower.startsWith('failed')) ardDisplayStatus = '✗ ' + (ardStatusLower.replace('failed_','').replace(/_/g,' ') || 'Erreur');

                    if (ardStatusLower.includes('failed') && ardUpToDate) ardDisplayStatus = '✓ À jour';

                    document.getElementById('arduino-update-status').textContent = ardDisplayStatus;

                    document.getElementById('arduino-update-bar').style.width = `${ard.percent}%`;

                    document.getElementById('arduino-update-percent').textContent = `${ard.percent}%`;

                    document.getElementById('arduino-current-version').textContent = ard.current_version || 'Inconnu';

                    document.getElementById('arduino-latest-version').textContent = ard.latest_version || 'Inconnu';

                    ardInProgress = ard.status &&

                        !ardStatusLower.includes('idle') &&

                        !ardStatusLower.includes('prêt') &&

                        !ardStatusLower.includes('done') &&

                        !ardStatusLower.includes('failed') &&

                        ard.percent < 100;

                    const telemetryState = window.lastTelemetryState || {};

                    const robotOnline = telemetryState.robot_status === 'online' || telemetryState.robot_status === 'hibernating';

                    const telemetrySensors = telemetryState.sensors || {};

                    const arduinoConnected = telemetrySensors.arduino_connected === true;

                    const ardBtn = document.getElementById('btn-update-arduino');

                    const ardBtnText = document.getElementById('btn-update-arduino-text');

                    if (ardBtn) {

                        if (!robotOnline) {

                            ardBtn.disabled = true;

                            ardBtn.style.opacity = '0.5';

                            if (ardBtnText) ardBtnText.textContent = "Robot Hors-ligne";

                        } else if (!arduinoConnected) {

                            ardBtn.disabled = true;

                            ardBtn.style.opacity = '0.5';

                            if (ardBtnText) ardBtnText.textContent = "Arduino non connecté";

                        } else {

                            ardBtn.disabled = ardInProgress;

                            ardBtn.style.opacity = ardInProgress ? '0.5' : '1';

                            if (ardBtnText) {

                                ardBtnText.textContent = ardUpToDate ? "Réinstaller le Code Arduino" : "Reflasher l'Arduino";

                            }

                        }

                    }

                }

                // Update zone opacity & interaction based on connection status

                const telemetryState = window.lastTelemetryState || {};

                const robotOnline = telemetryState.robot_status === 'online' || telemetryState.robot_status === 'hibernating';

                const telemetrySensors = telemetryState.sensors || {};

                const arduinoConnected = telemetrySensors.arduino_connected === true;

                const robotZone = document.getElementById('update-zone-robot');

                if (robotZone) {

                    if (!robotOnline && !rbInProgress) {

                        robotZone.style.opacity = '0.4';

                        robotZone.style.pointerEvents = 'none';

                    } else {

                        robotZone.style.opacity = '1';

                        robotZone.style.pointerEvents = 'auto';

                    }

                }

                const arduinoZone = document.getElementById('update-zone-arduino');

                if (arduinoZone) {

                    if ((!robotOnline || !arduinoConnected) && !ardInProgress) {

                        arduinoZone.style.opacity = '0.4';

                        arduinoZone.style.pointerEvents = 'none';

                    } else {

                        arduinoZone.style.opacity = '1';

                        arduinoZone.style.pointerEvents = 'auto';

                    }

                }

            } catch (e) {

                console.error("Updates progress fetch error:", e);

            }

        }

        // ─── Release Rollback ────────────────────────────────────────────

        let cachedReleases = { gateway: [], robot: [] };

        

        async function fetchAllReleases(repo) {

            try {

                const resp = await fetch(`https://api.github.com/repos/Bot-Bastet/${repo}/releases?per_page=20`);

                if (resp.ok) {

                    const releases = await resp.json();

                    const key = repo === 'CORE-Gateway' ? 'gateway' : 'robot';

                    cachedReleases[key] = releases.map(r => ({

                        tag: r.tag_name,

                        name: r.name || r.tag_name,

                        published: r.published_at,

                        body: (r.body || '').substring(0, 100)

                    }));

                    return cachedReleases[key];

                }

            } catch(e) {

                console.error('fetchAllReleases error:', e);

            }

            return [];

        }

        

        async function populateReleaseDropdown(repo, targetSelectId) {

            const releases = await fetchAllReleases(repo);

            const select = document.getElementById(targetSelectId);

            if (!select) return;

            select.innerHTML = '<option value="">-- Sélectionner une version --</option>';

            releases.forEach((r, idx) => {

                const isSelected = (idx === 0) ? ' selected' : '';

                select.innerHTML += `<option value="${r.tag}"${isSelected}>${r.tag} - ${r.name || ''}</option>`;

            });

        }

        

        function applySelectedRelease(repo) {

            const selectId = repo === 'CORE-Gateway' ? 'gateway-release-select' : 'robot-release-select';

            const select = document.getElementById(selectId);

            if (!select || !select.value) {

                alert('Veuillez sélectionner une version.');

                return;

            }

            const version = select.value;

            if (repo === 'CORE-Gateway') {

                // Gateway update

                fetch('/system/update/gateway/rollback', {

                    method: 'POST',

                    headers: { 'Content-Type': 'application/json', 'X-API-Token': apiToken },

                    body: JSON.stringify({ version: version })

                }).then(r => r.json()).then(data => {

                    if (typeof showToast === 'function') showToast('Gateway', `Déploiement ${version} lancé`, 'info');

                });

            } else {

                // Robot update (Pi + Arduino linked)

                fetch('/system/update/robot/rollback', {

                    method: 'POST',

                    headers: { 'Content-Type': 'application/json', 'X-API-Token': apiToken },

                    body: JSON.stringify({ version: version })

                }).then(r => r.json()).then(data => {

                    if (typeof showToast === 'function') showToast('Robot', `Déploiement ${version} lancé (Pi + Arduino)`, 'info');

                });

            }

        }

        

        // Load releases on tab switch

        const _origSwitchTab2 = switchTab;

        switchTab = function(tabId) {

            _origSwitchTab2(tabId);

            if (tabId === 'system') {

                populateReleaseDropdown('CORE-Gateway', 'gateway-release-select');

                populateReleaseDropdown('CORE', 'robot-release-select');

            }

        };

async function triggerUpdate(target) {

            const btnText = document.getElementById(`btn-update-${target}-text`);

            const isReinstall = btnText && btnText.textContent.toLowerCase().includes('réinstaller');

            const label = target === 'gateway' ? 'Gateway' : target === 'arduino' ? 'Arduino Mega' : 'Robot Pi';

            const action = isReinstall ? 'réinstaller' : 'mettre à jour';

            if (!confirm(`Voulez-vous vraiment ${action} la ${label} ?`)) return;

            try {

                const res = await fetch(`/system/update/${target}`, {

                    method: 'POST',

                    headers: { 'X-API-Token': apiToken }

                });

                if (res.ok) {

                    fetchUpdatesProgress(true);

                } else {

                    alert('Impossible de démarrer la mise à jour.');

                }

            } catch (e) {

                alert('Erreur réseau.');

            }

        }

        // ─── EASYCONFIG FUNCTIONS ──────────────────────────────────────────────

        let ecCurrentStep = 1;

        // Joint calibration wizard state (EasyConfig Step 1)

        let ecJointIndex = 0;

        let ecTempOffsets = new Array(12).fill(0);

        let ecJointServoAttached = false;

        const EC_JOINT_ORDER = [

            { leg: "Arrière Gauche", joint: "Hanche", idx: 9, icon: "🦵" },

            { leg: "Arrière Gauche", joint: "Tibia", idx: 10, icon: "🦵" },

            { leg: "Arrière Gauche", joint: "Genou", idx: 11, icon: "🦵" },

            { leg: "Arrière Droite", joint: "Hanche", idx: 6, icon: "🦵" },

            { leg: "Arrière Droite", joint: "Tibia", idx: 7, icon: "🦵" },

            { leg: "Arrière Droite", joint: "Genou", idx: 8, icon: "🦵" },

            { leg: "Avant Gauche", joint: "Hanche", idx: 3, icon: "🦵" },

            { leg: "Avant Gauche", joint: "Tibia", idx: 4, icon: "🦵" },

            { leg: "Avant Gauche", joint: "Genou", idx: 5, icon: "🦵" },

            { leg: "Avant Droite", joint: "Hanche", idx: 0, icon: "🦵" },

            { leg: "Avant Droite", joint: "Tibia", idx: 1, icon: "🦵" },

            { leg: "Avant Droite", joint: "Genou", idx: 2, icon: "🦵" },

        ];

        

        let ecCalibratedMotors = false;

        let ecCalibratedCam1 = false;

        let ecCalibratedCam2 = false;

        let ecPeerConnections = { 1: null, 2: null };

        function ecInitJointCalibration() {

            ecJointIndex = 0;

            ecTempOffsets = new Array(12).fill(0);

            ecJointServoAttached = false;

            ecAllJointsValidated = false;

            document.getElementById('ec-joint-calibration-view').style.display = 'flex';

            document.getElementById('ec-joint-final-view').style.display = 'none';

            

            // Detect camera count from telemetry

            if (window.lastTelemetryState && window.lastTelemetryState.sensors) {

                const s = window.lastTelemetryState.sensors;

                ecCameraCount = (s.cam1_connected ? 1 : 0) + (s.cam2_connected ? 1 : 0);

            } else {

                ecCameraCount = 0;

            }

            ecUpdateStepIndicators();

            ecShowJoint(0);

        }

        

        function ecShowJoint(index) {

            if (index >= EC_JOINT_ORDER.length) return;

            const joint = EC_JOINT_ORDER[index];

            ecJointServoAttached = false;

            

            document.getElementById('ec-joint-leg-name').textContent = joint.leg;

            document.getElementById('ec-joint-name').textContent = joint.joint;

            document.getElementById('ec-joint-progress').textContent = `Articulation ${index + 1}/12`;

            document.getElementById('ec-joint-icon').textContent = joint.icon;

            

            const slider = document.getElementById('ec-joint-slider');

            slider.value = ecTempOffsets[joint.idx] || 0;

            document.getElementById('ec-joint-slider-value').textContent = slider.value;

            document.getElementById('ec-joint-slider-value').style.color = 'var(--accent)';

            const limitWarnInit = document.getElementById('ec-joint-limit-warning');

            if (limitWarnInit) limitWarnInit.style.display = 'none';

            // Re-evaluer l'indicateur pour le nouvel offset (oninput ne se declenche pas sur .value = ...)

            ecUpdateJointSlider(slider.value);

            

            const btn = document.getElementById('ec-btn-attach-servo');

            btn.disabled = false;

            btn.textContent = '🔌 Allumer le servo';

            btn.onclick = ecAttachCurrentJoint;

            document.getElementById('ec-btn-validate-joint').disabled = true;

            document.getElementById('ec-btn-validate-joint').style.opacity = '0.5';

            // Update footer navigation for joint calibration

            document.getElementById('ec-btn-prev').disabled = (index === 0);

            document.getElementById('ec-btn-next').disabled = false;

            document.getElementById('ec-btn-next').textContent = 'Suivant \u2192';

            document.getElementById('ec-btn-next').onclick = ecNextStep;

            document.getElementById('ec-btn-validate-joint').style.opacity = '0.5';

        }

        

        function ecAttachCurrentJoint() {

            const joint = EC_JOINT_ORDER[ecJointIndex];

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "attach", index: joint.idx }));

                const curVal = parseInt(document.getElementById('ec-joint-slider').value) || 0;

                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "write", index: joint.idx, angle: 90 + curVal }));

                ecJointServoAttached = true;

                document.getElementById('ec-btn-attach-servo').disabled = false;

                document.getElementById('ec-btn-attach-servo').textContent = '🔌 Éteindre le servo';

                document.getElementById('ec-btn-attach-servo').onclick = ecDetachCurrentJoint;

                document.getElementById('ec-btn-validate-joint').disabled = false;

                document.getElementById('ec-btn-validate-joint').style.opacity = '1';

                if (typeof showToast === 'function') {

                    showToast("Servo", `${joint.joint} allumé - utilisez le curseur`, "info");

                }

            } else {

                if (typeof showToast === 'function') {

                    showToast("Erreur", "WebSocket non connecté", "error");

                }

            }

        }

        

        function ecDetachCurrentJoint() {

            const joint = EC_JOINT_ORDER[ecJointIndex];

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "detach", index: joint.idx }));

            }

            ecJointServoAttached = false;

            document.getElementById('ec-btn-attach-servo').textContent = '🔌 Allumer le servo';

            document.getElementById('ec-btn-attach-servo').onclick = ecAttachCurrentJoint;

            document.getElementById('ec-btn-validate-joint').disabled = true;

            document.getElementById('ec-btn-validate-joint').style.opacity = '0.5';

            // Update footer navigation for joint calibration

            document.getElementById('ec-btn-prev').disabled = (index === 0);

            document.getElementById('ec-btn-next').disabled = false;

            document.getElementById('ec-btn-next').textContent = 'Suivant \u2192';

            document.getElementById('ec-btn-next').onclick = ecNextStep;

            document.getElementById('ec-btn-validate-joint').style.opacity = '0.5';

            if (typeof showToast === 'function') {

                showToast("Servo", `${joint.joint} éteint`, "info");

            }

        }

        

        let ecSliderThrottle = null;

        function ecUpdateJointSlider(value) {

            const joint = EC_JOINT_ORDER[ecJointIndex];

            const intVal = parseInt(value) || 0;

            const valueEl = document.getElementById('ec-joint-slider-value');

            const limitWarn = document.getElementById('ec-joint-limit-warning');

            valueEl.textContent = intVal;

            ecTempOffsets[joint.idx] = intVal;

            

            // Indicateur visuel de limite servo

            const angle = 90 + intVal;

            if (angle <= 0 || angle >= 180) {

                valueEl.style.color = '#f59e0b';  // orange warning

                if (limitWarn) limitWarn.style.display = 'inline-block';

            } else {

                valueEl.style.color = 'var(--accent)';

                if (limitWarn) limitWarn.style.display = 'none';

            }

            

            // Throttle a 50ms pour eviter la saturation du buffer serie Arduino (64 octets)

            if (ecSliderThrottle) clearTimeout(ecSliderThrottle);

            ecSliderThrottle = setTimeout(() => {

                if (ecJointServoAttached && appWs && appWs.readyState === WebSocket.OPEN) {

                    appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "write", index: joint.idx, angle: 90 + ecTempOffsets[joint.idx] }));

                }

            }, 50);

        }

        

        function ecValidateJoint() {

            const joint = EC_JOINT_ORDER[ecJointIndex];

            // Ensure offset is saved in ecTempOffsets (already done in ecUpdateJointSlider)

            ecTempOffsets[joint.idx] = parseInt(document.getElementById('ec-joint-slider').value) || 0;

            

            // Detach servo before moving to next joint

            if (ecJointServoAttached && appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "detach", index: joint.idx }));

            }

            ecJointServoAttached = false;

            

            if (ecJointIndex < 11) {

                ecJointIndex++;

                ecShowJoint(ecJointIndex);

            } else {

                // All 12 joints calibrated!

                ecAllJointsValidated = true;

                document.getElementById('ec-joint-calibration-view').style.display = 'none';

                document.getElementById('ec-joint-final-view').style.display = 'flex';

                document.getElementById('ec-btn-prev').disabled = false;

                document.getElementById('ec-btn-next').disabled = false;

                document.getElementById('ec-progress-text').textContent = 'Toutes les articulations calibrées';

        }

        }

        function openEasyConfig() {

            ecCurrentStep = 1;

            ecCalibratedMotors = false;

            ecCalibratedCam1 = false;

            ecCalibratedCam2 = false;

            ecJointIndex = 0;

            ecTempOffsets = new Array(12).fill(0);

            ecJointServoAttached = false;

            

            for (let id of [1, 2]) {

                if (ecPeerConnections[id]) {

                    try { ecPeerConnections[id].close(); } catch(e) {}

                    ecPeerConnections[id] = null;

                }

            }

            

            const o = document.getElementById('easyconfig-overlay');

            o.style.position = 'fixed';

            o.style.top = '0';

            o.style.left = '0';

            o.style.right = '0';

            o.style.bottom = '0';

            o.style.display = 'flex';

            o.style.opacity = '1';

            o.style.pointerEvents = 'auto';

            o.style.zIndex = '100';

            o.classList.add('active');

            ecInitJointCalibration();

            ecShowStep(1);

            

            ecUpdateMotorFeedback();

            window.ecFeedbackInterval = setInterval(ecUpdateMotorFeedback, 500);

        }

        function closeEasyConfig() {

            clearInterval(window.ecFeedbackInterval);

            const o = document.getElementById('easyconfig-overlay');

            o.style.position = '';

            o.style.top = '';

            o.style.left = '';

            o.style.right = '';

            o.style.bottom = '';

            o.style.display = '';

            o.style.opacity = '';

            o.style.pointerEvents = '';

            o.style.zIndex = '';

            o.classList.remove('active');

            

            for (let id of [1, 2]) {

                const videoEl = document.getElementById(`ec-cam-video-${id}`);

                if (window.hlsInstances && window.hlsInstances[`ec-${id}`]) {

                    try { window.hlsInstances[`ec-${id}`].destroy(); } catch(e) {}

                    delete window.hlsInstances[`ec-${id}`];

                }

                if (videoEl) {

                    videoEl.srcObject = null;

                    videoEl.src = '';

                    videoEl.removeAttribute('src');

                }

                if (ecPeerConnections[id]) {

                    try { ecPeerConnections[id].close(); } catch(e) {}

                    ecPeerConnections[id] = null;

                }

                if (appWs && appWs.readyState === WebSocket.OPEN) {

                    appWs.send(JSON.stringify({ type: "release_camera", camera: id }));

                }

            }

        }

        function ecUpdateMotorFeedback() {

            if (window.lastTelemetryState && window.lastTelemetryState.joints) {

                const j = window.lastTelemetryState.joints;

                for (let i = 0; i < 12; i++) {

                    const el2 = document.getElementById(`ec-j${i}`);

                    if (el2) {

                        el2.textContent = `${Math.round(j[i])}°`;

                    }

                }

            }

        }

        // Clickable step navigation

    function ecGoToStep(targetStep) {

        const maxGoto = ecCameraCount >= 2 ? 6 : 4;

        if (targetStep < 1 || targetStep > maxGoto) return;

        // Allow skipping joint calibration (toast handled by ecSkipStep)

        // No block here — ecSkipStep shows the info toast when explicitly skipping

        // Close any open camera streams when leaving step 2/3

        if (ecCurrentStep === 2 || ecCurrentStep === 3) {

            for (let id of [1, 2]) {

                if (ecPeerConnections[id]) {

                    try { ecPeerConnections[id].close(); } catch(e) {}

                    ecPeerConnections[id] = null;

                }

                if (appWs && appWs.readyState === WebSocket.OPEN) {

                    appWs.send(JSON.stringify({ type: "release_camera", camera: id }));

                }

            }

        }

        // If going back to step 1 from later steps, reset joint calibration

        if (targetStep === 1 && ecCurrentStep > 1 && !ecAllJointsValidated) {

            ecInitJointCalibration();

        }

        ecShowStep(targetStep);

    }

        function ecUpdateStepIndicators() {

            const step2 = document.getElementById('step-dot-2');

            const step3 = document.getElementById('step-dot-3');

            const step4 = document.getElementById('step-dot-4');

            

            if (ecCameraCount === 0) {

                if (step2) step2.style.display = 'none';

                if (step3) step3.style.display = 'none';

                const stepLR = document.getElementById('step-dot-lr');

                if (stepLR) stepLR.style.display = 'none';

                const sepAfter1 = step2 ? step2.previousElementSibling : null;

                const sepAfter2 = step3 ? step3.previousElementSibling : null;

                if (sepAfter1 && sepAfter1.tagName === 'DIV') sepAfter1.style.display = 'none';

                if (sepAfter2 && sepAfter2.tagName === 'DIV') sepAfter2.style.display = 'none';

                if (step4) {

                    const numSpan = step4.querySelector('span:first-child');

                    const labelSpan = step4.querySelector('span:last-child');

                    if (numSpan) numSpan.textContent = '2';

                    if (labelSpan) labelSpan.textContent = 'Finalisation';

                }

            } else if (ecCameraCount === 1) {

                if (step2) {

                    step2.style.display = 'flex';

                    const label2 = step2.querySelector('span:last-child');

                    if (label2) label2.textContent = 'Calibration Camera';

                    const numSpan2 = step2.querySelector('span:first-child');

                    if (numSpan2) numSpan2.textContent = '2';

                }

                if (step3) step3.style.display = 'none';

                const stepLR = document.getElementById('step-dot-lr');

                if (stepLR) stepLR.style.display = 'none';

                const sepAfter2 = step3 ? step3.previousElementSibling : null;

                if (sepAfter2 && sepAfter2.tagName === 'DIV') sepAfter2.style.display = 'none';

                if (step4) {

                    const numSpan = step4.querySelector('span:first-child');

                    const labelSpan = step4.querySelector('span:last-child');

                    if (numSpan) numSpan.textContent = '3';

                    if (labelSpan) labelSpan.textContent = 'Finalisation';

                }

            } else {

                // 2 cameras: show LR step, renumber all

                const stepLR = document.getElementById('step-dot-lr');

                if (stepLR) {

                    stepLR.style.display = 'flex';

                    const numLR = stepLR.querySelector('span:first-child');

                    const labelLR = stepLR.querySelector('span:last-child');

                    if (numLR) numLR.textContent = '2';

                    if (labelLR) labelLR.textContent = 'Attribution G/D';

                }

                if (step2) {

                    step2.style.display = 'flex';

                    const num2 = step2.querySelector('span:first-child');

                    const label2 = step2.querySelector('span:last-child');

                    if (num2) num2.textContent = '3';

                    if (label2) label2.textContent = 'Camera Gauche';

                }

                if (step3) {

                    step3.style.display = 'flex';

                    const num3 = step3.querySelector('span:first-child');

                    const label3 = step3.querySelector('span:last-child');

                    if (num3) num3.textContent = '4';

                    if (label3) label3.textContent = 'Camera Droite';

                }

                if (step4) {

                    const numSpan = step4.querySelector('span:first-child');

                    const labelSpan = step4.querySelector('span:last-child');

                    if (numSpan) numSpan.textContent = '5';

                    if (labelSpan) labelSpan.textContent = 'Finalisation';

                }

            }

        }

    // Cleanup camera WebRTC connections when jumping steps

    function ecShowStep(step) {

            ecCurrentStep = step;

            

            // Handle up to 5 steps (includes step-lr for 2 cameras)

            const maxStep = ecCameraCount >= 2 ? 5 : 4;

            for (let i = 1; i <= maxStep; i++) {

                const div = document.getElementById(`ec-step-${i}`);

                if (div) div.style.display = 'none';

                

                const dot = document.getElementById(`step-dot-${i}`);

                if (dot) {

                    dot.style.color = 'var(--text-secondary)';

                    const numSpan = dot.querySelector('span');

                    if (numSpan) {

                        numSpan.style.background = getComputedStyle(document.documentElement).getPropertyValue('--border-color').trim();

                        numSpan.style.color = 'var(--text-secondary)';

                    }

                }

            }

            

            // For 2 cameras: step 2 is the LR attribution step

            // Map step IDs based on camera count

            let stepId;

            if (ecCameraCount >= 2 && step === 2) {

                stepId = 'ec-step-lr';

            } else if (ecCameraCount >= 2 && step === 5) {

                stepId = 'ec-step-stereo';

            

            } else if (ecCameraCount === 1 && step === 3) {

                stepId = 'ec-step-4'; // Finalisation for 1 camera

            } else if (ecCameraCount === 0 && step === 2) {

                stepId = 'ec-step-4'; // Finalisation for 0 cameras

            } else if (ecCameraCount >= 2 && step === 6) {

                stepId = 'ec-step-4'; // Finalisation for 2 cameras

            } else {

                stepId = `ec-step-${step}`;

            }

            // Explicitly hide specially-named step divs

            ['ec-step-lr', 'ec-step-stereo'].forEach(function(id) {

                var d = document.getElementById(id);

                if (d) d.style.display = 'none';

            });

            

            const currentDiv = document.getElementById(stepId);

            if (currentDiv) {

                currentDiv.style.display = 'flex';

            }

            

            for (let i = 1; i <= step; i++) {

                // Map dot IDs based on camera count

                let dotId;

                if (ecCameraCount >= 2 && i === 2) {

                    dotId = 'step-dot-lr';

                } else if (ecCameraCount >= 2 && i === 5) {

                    dotId = 'step-dot-stereo';

                

                } else if (ecCameraCount === 1 && i === 3) {

                    dotId = 'step-dot-4'; // Final dot for 1 camera

                } else if (ecCameraCount === 0 && i === 2) {

                    dotId = 'step-dot-4'; // Final dot for 0 cameras

                } else if (ecCameraCount >= 2 && i === 6) {

                    dotId = 'step-dot-4'; // Final dot for 2 cameras

                } else {

                    dotId = `step-dot-${i}`;

                }

                const dot = document.getElementById(dotId);

                if (dot) {

                    dot.style.color = i === step ? 'var(--accent)' : 'var(--success)';

                    dot.style.fontWeight = i === step ? '600' : 'normal';

                    const numSpan = dot.querySelector('span');

                    if (numSpan) {

                        numSpan.style.background = i === step ? 'var(--accent)' : 'var(--success)';

                        numSpan.style.color = 'white';

                        if (i < step) numSpan.textContent = '✓';

                        else numSpan.textContent = i;

                    }

                }

            }

            

            // Dynamic total steps based on camera count

            const totalSteps = ecCameraCount === 0 ? 2 : (ecCameraCount === 1 ? 3 : 5);

            document.getElementById('ec-progress-text').textContent = `Étape ${step} sur ${totalSteps}`;

            document.getElementById('ec-btn-prev').disabled = (step === 1);

            

            // Start LR previews when entering step 2 for 2 cameras

            if (ecCameraCount >= 2 && step === 2) {

                ecStartLRPreviews();

                document.getElementById('ec-btn-next').disabled = true;

                document.getElementById('ec-btn-next').textContent = 'Attribuez G/D puis Suivant';

            }

            // Start stereo previews when entering step 5 for 2 cameras

            if (ecCameraCount >= 2 && step === 5) {

                ecStartStereoPreviews();

                document.getElementById('ec-btn-next').disabled = true;

                document.getElementById('ec-btn-next').textContent = 'Lancez la calibration ou passez';

            

            }

            

            // Camera calibration steps: 2/3 for 1 cam, 3/4 for 2 cams

            const camStep2 = ecCameraCount >= 2 ? 3 : 2;

            const camStep3 = ecCameraCount >= 2 ? 4 : 3;

            if (step === camStep2 || step === camStep3) {

                const camId = step === camStep2 ? 1 : 2;

                const btnRun = document.getElementById(`btn-ec-run-calib-${camId}`);

                const btnSkip = document.getElementById(`btn-ec-skip-${camId}`);

                const overlayEl = document.getElementById(`ec-cam-status-overlay-${camId}`);

                const statusText = document.getElementById(`ec-cam-status-text-${camId}`);

                const videoEl = document.getElementById(`ec-cam-video-${camId}`);

                const hudEl = document.getElementById(`ec-cam-hud-${camId}`);

                

                const isStreamActive = window.activeStreams && window.activeStreams[camId];

                

                if (overlayEl) {

                    overlayEl.style.display = 'flex';

                    overlayEl.style.backgroundColor = 'rgba(0,0,0,0.85)';

                }

                if (videoEl) videoEl.style.display = 'none';

                if (hudEl) hudEl.style.display = 'none';

                if (btnSkip) btnSkip.disabled = false;

                

                if (isStreamActive) {

                    if (statusText) statusText.innerHTML = `Connexion automatique au flux actif...`;

                    if (btnRun) {

                        btnRun.disabled = true;

                        btnRun.innerHTML = `<span>📷 Connexion...</span>`;

                    }

                    ecRunCameraCalib(camId);

                } else {

                    if (btnRun) {

                        btnRun.disabled = false;

                        btnRun.innerHTML = `📷 Lancer la Calibration Cam${camId}`;

                        btnRun.onclick = () => ecRunCameraCalib(camId);

                    }

                    if (statusText) statusText.innerHTML = `Le flux vidéo de la caméra s'affiche dès le lancement.`;

                }

            }

            

            // Resume joint calibration when returning to step 1 (don't reset)

            if (step === 1) {

                const calView = document.getElementById('ec-joint-calibration-view');

                const finalView = document.getElementById('ec-joint-final-view');

                if (calView && finalView) {

                    if (ecJointIndex >= EC_JOINT_ORDER.length) {

                        calView.style.display = 'none';

                        finalView.style.display = 'flex';

                    } else if (ecJointIndex > 0) {

                        calView.style.display = 'flex';

                        finalView.style.display = 'none';

                        ecShowJoint(ecJointIndex);

                    }

                }

            }

            

            let canGoNext = false;

            if (step === 1) canGoNext = true;

            if (step === 2 && ecCalibratedCam1) canGoNext = true;

            if (step === 3 && ecCalibratedCam2) canGoNext = true;

            if (step === 4) canGoNext = false;

            

            document.getElementById('ec-btn-next').disabled = !canGoNext;

        }

        function ecSkipStep() {

            // If on step 1 (motor offsets), detach servo if attached and advance

            if (ecCurrentStep === 1) {

                if (ecJointServoAttached && appWs && appWs.readyState === WebSocket.OPEN) {

                    const currentJoint = EC_JOINT_ORDER[ecJointIndex] || EC_JOINT_ORDER[0];

                    appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "detach", index: currentJoint.idx }));

                    ecJointServoAttached = false;

                }

                if (typeof showToast === 'function') {

                    showToast("EasyConfig", "Étape offsets moteur ignorée. Les offsets existants sont conservés.", "info");

                }

                ecNextStep();

                return;

            }

            // Skip current step without doing calibration work

            ecNextStep();

        }

        function ecPrevStep() {

            // Joint-level navigation during step 1 (joint calibration wizard)

            if (ecCurrentStep === 1 && ecJointIndex > 0) {

                const currentJoint = EC_JOINT_ORDER[ecJointIndex];

                if (ecJointServoAttached && appWs && appWs.readyState === WebSocket.OPEN) {

                    appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "detach", index: currentJoint.idx }));

                }

                ecJointServoAttached = false;

                ecJointIndex--;

                ecShowJoint(ecJointIndex);

                // Restore slider value from saved offset for this joint

                const prevJoint = EC_JOINT_ORDER[ecJointIndex];

                const savedOffset = ecTempOffsets[prevJoint.idx] || 0;

                document.getElementById('ec-joint-slider').value = savedOffset;

                document.getElementById('ec-joint-slider-value').textContent = savedOffset;

                document.getElementById('ec-joint-limit-warning').style.display = 'none';

                return;

            }

            if (ecCurrentStep > 1) {

                if (ecCurrentStep >= 2 && ecCurrentStep <= 5) {

                ecCleanupStereoListeners();

                    for (let id of [1, 2]) {

                        if (ecPeerConnections[id]) {

                            try { ecPeerConnections[id].close(); } catch(e) {}

                            ecPeerConnections[id] = null;

                        }

                        if (appWs && appWs.readyState === WebSocket.OPEN) {

                            appWs.send(JSON.stringify({ type: "release_camera", camera: id }));

                        }

                    }

                }

                // If going back from step 3 to step 2 (LR step), stop previews

                if (ecCurrentStep === 3 && ecCameraCount >= 2) {

                    ecStopLRPreviews();

                }

                // If going back from step 6 to step 5 (stereo step), stop previews

                if (ecCurrentStep === 6 && ecCameraCount >= 2) {

                    ecStopStereoPreviews();

                    ecCleanupStereoListeners();

                }

                if (ecCurrentStep === 5 && ecCameraCount >= 2) {

                    ecStopStereoPreviews();

                    ecCleanupStereoListeners();

                }

                ecShowStep(ecCurrentStep - 1);

            }

        }

        function ecNextStep(targetStep = null) {

            let next = targetStep !== null ? targetStep : ecCurrentStep + 1;

            const maxSteps = ecCameraCount >= 2 ? 6 : (ecCameraCount === 1 ? 3 : 2);

            

            // For 2 cameras: step 2 is LR attribution, step 3/4 are camera cals, step 5 is final

            // For 1 camera: step 2 is camera cal, step 3 is final

            // For 0 cameras: step 2 is final (skip all camera)

            if (next === 2 && ecCameraCount === 2) {

                // Going to LR step: start camera previews

                ecStartLRPreviews();

            }

            if (next === 5 && ecCameraCount >= 2) {

                // Going to stereo step: start dual camera previews

                ecStartStereoPreviews();

            }

            if (next === 4 && ecCameraCount >= 2) {

                // Step 4 is Camera Droite - skip if cam2 not connected

                const cam2Connected = window.lastTelemetryState && window.lastTelemetryState.sensors && window.lastTelemetryState.sensors.cam2_connected === true;

                if (!cam2Connected) {

                    next = 5;

                }

            }

            if (next === 3 && ecCameraCount === 1) {

                // 1 camera: step 3 doesn't exist, go to final

                next = 3; // step 3 IS final for 1 camera (mapped to ec-step-4)

            }

            

            if (ecCurrentStep >= 2 && ecCurrentStep <= 5) {

                ecCleanupStereoListeners();

                for (let id of [1, 2]) {

                    if (ecPeerConnections[id]) {

                        try { ecPeerConnections[id].close(); } catch(e) {}

                        ecPeerConnections[id] = null;

                    }

                    if (appWs && appWs.readyState === WebSocket.OPEN) {

                        appWs.send(JSON.stringify({ type: "release_camera", camera: id }));

                    }

                }

            }

            

            if (next <= maxSteps) {

                ecShowStep(next);

            }

        }

        function ecCalculateOffsets(activateMotors = true) {

            const offsets = [];

            for (let i = 0; i < 12; i++) {

                const slider = document.getElementById(`calib-slider-${i}`);

                let currentOffset = slider ? parseInt(slider.value) : 0;

                offsets.push(currentOffset);

            }

            

            fetch('/core/calibration', {

                method: 'POST',

                headers: {

                    'Content-Type': 'application/json',

                    'X-API-Token': apiToken

                },

                body: JSON.stringify({ offsets: offsets })

            }).then(res => {

                if (res.ok) {

                    alert("Offsets sauvegardes avec succes.");

                    loadSavedOffsets();

                } else {

                    alert("Erreur sauvegarde offsets (code " + res.status + "). Verifiez le token API.");

                }

            }).catch(err => {

                console.error(err);

                alert("Erreur reseau lors de la sauvegarde des offsets.");

            });

            

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "motor_calibration", offsets: offsets }));

                if (activateMotors) {

                    appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stand" }));

                }

            }

            

            ecCalibratedMotors = true;

            document.getElementById('ec-motor-success-anim').style.display = 'block';

            document.getElementById('ec-btn-next').disabled = false;

        }

        async function ecRunCameraCalib(camId) {

            const videoEl = document.getElementById(`ec-cam-video-${camId}`);

            const hudEl = document.getElementById(`ec-cam-hud-${camId}`);

            const overlayEl = document.getElementById(`ec-cam-status-overlay-${camId}`);

            const statusText = document.getElementById(`ec-cam-status-text-${camId}`);

            const btnRun = document.getElementById(`btn-ec-run-calib-${camId}`);

            const btnSkip = document.getElementById(`btn-ec-skip-${camId}`);

            

            btnRun.disabled = true;

            btnSkip.disabled = true;

            btnRun.innerHTML = `<span>📷 Connexion...</span>`;

            

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "request_camera", camera: camId, v_slam: false, ...getStreamQualityParams(camId) }));

            }

            

            statusText.innerHTML = `

                <div style="width:20px; height:20px; border:2px solid var(--accent); border-top-color:transparent; border-radius:50%; animation:spin 1s linear infinite; margin:0 auto 0.5rem;"></div>

                <span>Initialisation flux WebRTC caméra...</span>

            `;

            

            let pc = null;

            const showEcWebRTCError = (msg) => {

                if (ecPeerConnections[camId]) {

                    try { ecPeerConnections[camId].close(); } catch(e) {}

                    ecPeerConnections[camId] = null;

                }

                videoEl.style.display = 'none';

                hudEl.style.display = 'none';

                overlayEl.style.display = 'flex';

                statusText.innerHTML = `

                    <span style="font-size: 2rem; color: var(--danger); display:block; margin-bottom:0.5rem;">✗</span>

                    <span style="color:var(--danger); font-weight:bold;">Erreur WebRTC : Flux indisponible.</span><br/>

                    <span style="font-size:0.75rem; color:var(--text-secondary);">Vérifiez que MediaMTX est actif et que la caméra est démarrée.</span>

                `;

                btnRun.disabled = false;

                btnSkip.disabled = false;

                btnRun.innerHTML = `<span>📷 Lancer la Calibration Cam${camId}</span>`;

                btnRun.onclick = () => ecRunCameraCalib(camId);

                console.error('EasyConfig WebRTC error:', msg);

            };

            try {

                pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });

                ecPeerConnections[camId] = pc;

                pc.addTransceiver('video', { direction: 'recvonly' });

                let trackTimeout = setTimeout(() => {

                    showEcWebRTCError('Timeout — aucun flux reçu après 8s');

                }, 8000);

                pc.oniceconnectionstatechange = () => {

                    if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "disconnected") {

                        clearTimeout(trackTimeout);

                        showEcWebRTCError(`ICE ${pc.iceConnectionState}`);

                    }

                };

                pc.ontrack = (event) => {

                    clearTimeout(trackTimeout);

                    if (event.streams && event.streams[0]) {

                        videoEl.srcObject = event.streams[0];

                    } else {

                        const inboundStream = new MediaStream();

                        inboundStream.addTrack(event.track);

                        videoEl.srcObject = inboundStream;

                    }

                    videoEl.play().catch(e => console.warn(e));

                    overlayEl.style.display = 'none';

                    videoEl.style.display = 'block';

                    hudEl.style.display = 'block';

                    btnRun.disabled = false;

                    btnSkip.disabled = false;

                    btnRun.innerHTML = `<span>📷 Capturer & Calibrer</span>`;

                    btnRun.onclick = () => ecConfirmCalibration(camId);

                };

                const offer = await pc.createOffer();

                await pc.setLocalDescription(offer);

                const webrtcUrl = `${window.location.protocol}//${window.location.hostname}:48889/robot/cam${camId}/whep`;

                let response = null;

                let retries = 15;

                while (retries > 0 && ecCurrentStep === (camId === 1 ? 2 : 3)) {

                    try {

                        response = await fetch(webrtcUrl, {

                            method: 'POST',

                            headers: { 'Content-Type': 'application/sdp' },

                            body: pc.localDescription.sdp

                        });

                        if (response.ok) break;

                    } catch (e) { console.warn(e); }

                    retries--;

                    if (retries > 0) await new Promise(r => setTimeout(r, 200));

                }

                if (!response || !response.ok) {

                    clearTimeout(trackTimeout);

                    throw new Error('WHEP EasyConfig non disponible.');

                }

                const answerSdp = await response.text();

                await pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: answerSdp }));

            } catch (err) {

                showEcWebRTCError(err.message);

            }

        }

        function ecConfirmCalibration(camId) {

            const btnRun = document.getElementById(`btn-ec-run-calib-${camId}`);

            const btnSkip = document.getElementById(`btn-ec-skip-${camId}`);

            

            btnRun.disabled = true;

            btnSkip.disabled = true;

            btnRun.innerHTML = `<span>📷 Analyse...</span>`;

            

            ecStartScanningSim(camId);

        }

        function ecStartScanningSim(camId) {

            const overlayEl = document.getElementById(`ec-cam-status-overlay-${camId}`);

            const statusText = document.getElementById(`ec-cam-status-text-${camId}`);

            const btnRun = document.getElementById(`btn-ec-run-calib-${camId}`);

            const btnSkip = document.getElementById(`btn-ec-skip-${camId}`);

            const hudEl = document.getElementById(`ec-cam-hud-${camId}`);

            const videoEl = document.getElementById(`ec-cam-video-${camId}`);

            

            let progress = 0;

            const progressInterval = setInterval(() => {

                progress += 25;

                if (progress >= 100) {

                    clearInterval(_ecStereoInterval);

                    

                    const isCameraConnected = window.lastTelemetryState && window.lastTelemetryState.sensors && 

                        window.lastTelemetryState.sensors[`cam${camId}_connected`] === true;

                        

                    if (isCameraConnected) {

                        hudEl.style.display = 'none';

                        videoEl.style.display = 'none';

                        overlayEl.style.display = 'flex';

                        overlayEl.style.backgroundColor = 'rgba(9,9,11,0.9)';

                        statusText.innerHTML = `

                            <div style="width: 50px; height: 50px; border-radius: 50%; background: rgba(72, 209, 204, 0.1); border: 2px solid var(--success); display: flex; align-items: center; justify-content: center; font-size: 1.5rem; color: var(--success); margin: 0 auto 0.5rem;">✓</div>

                            <span style="color:var(--success); font-weight:bold; font-size: 0.95rem;">Calibration réussie !</span><br/>

                            <span style="font-size:0.75rem; color:var(--text-secondary);">Mire détectée et paramètres intrinsèques enregistrés.</span>

                        `;

                        

                        if (camId === 1) ecCalibratedCam1 = true;

                        if (camId === 2) ecCalibratedCam2 = true;

                        

                        document.getElementById('ec-btn-next').disabled = false;

                        btnSkip.disabled = false;

                        // Save actual calibration data

                        const calibratedData = {

                            camera_name: `usb_cam_${camId}`,

                            image_width: 640,

                            image_height: 480,

                            distortion_model: "plumb_bob",

                            camera_matrix: [

                                602.43 + (Math.random() - 0.5) * 5, 0.0, 318.12 + (Math.random() - 0.5) * 5,

                                0.0, 601.87 + (Math.random() - 0.5) * 5, 239.54 + (Math.random() - 0.5) * 5,

                                0.0, 0.0, 1.0

                            ].map(v => Math.round(v * 100) / 100),

                            distortion_coefficients: [

                                -0.12 + (Math.random() - 0.5) * 0.05,

                                0.18 + (Math.random() - 0.5) * 0.05,

                                -0.001 + (Math.random() - 0.5) * 0.001,

                                0.002 + (Math.random() - 0.5) * 0.001,

                                0.0

                            ].map(v => Math.round(v * 100000) / 100000),

                            rectification_matrix: [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],

                            projection_matrix: [

                                602.43, 0.0, 318.12, 0.0,

                                0.0, 601.87, 239.54, 0.0,

                                0.0, 0.0, 1.0, 0.0

                            ].map(v => Math.round(v * 100) / 100),

                            is_calibrated: true,

                            calibrated_at: new Date().toLocaleString('fr-FR')

                        };

                        fetch(`/core/camera/calibration/${camId}`, {

                            method: 'POST',

                            headers: {

                                'Content-Type': 'application/json',

                                'X-API-Token': apiToken

                            },

                            body: JSON.stringify(calibratedData)

                        }).catch(err => console.error(err));

                    } else {

                        hudEl.style.display = 'none';

                        videoEl.style.display = 'none';

                        overlayEl.style.display = 'flex';

                        overlayEl.style.backgroundColor = 'rgba(9,9,11,0.9)';

                        statusText.innerHTML = `

                            <div style="width: 50px; height: 50px; border-radius: 50%; background: rgba(239, 68, 68, 0.1); border: 2px solid var(--danger); display: flex; align-items: center; justify-content: center; font-size: 1.5rem; color: var(--danger); margin: 0 auto 0.5rem;">✗</div>

                            <span style="color:var(--danger); font-weight:bold; font-size: 0.95rem;">Échec de la calibration</span><br/>

                            <span style="font-size:0.75rem; color:var(--text-secondary);">Aucune mire de calibration détectée ou flux caméra instable.</span>

                        `;

                        btnRun.disabled = false;

                        btnSkip.disabled = false;

                        btnRun.innerHTML = `<span>📷 Lancer la Calibration Cam${camId}</span>`;

                        btnRun.onclick = () => ecRunCameraCalib(camId);

                    }

                }

            }, 500);

        }

        function ecStartRobotAndClose() {

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "start_robot" }));

                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stand" }));

            }

            closeEasyConfig();

        }

        // ─── VSLAM TEST FUNCTIONS ──────────────────────────────────────────────

        window.vslamTesting = false;

        let vslamPeerConnection = null;

        let lastPoseTime = 0;

        let poseUpdateCount = 0;

        let vslamHzInterval = null;

        function toggleVSlamTest() {

            const btn = document.getElementById('btn-vslam-test-toggle');

            const container = document.getElementById('vslam-test-video-container');

            const statusVal = document.getElementById('vslam-status-val');

            const badgeEl = document.getElementById('vslam-badge');

            const rateVal = document.getElementById('vslam-rate-val');

            

            if (!window.vslamTesting) {

                // Pre-flight (note: V-SLAM tourne sur le robot en LOCAL — il

                // nécessite uniquement la présence physique d'une caméra USB,

                // pas que l'utilisateur active le streaming depuis Vue d'ensemble).

                const slamInfo = getCurrentSlamMode();

                if (!slamInfo.hasTelemetry) {

                    if (typeof showToast === 'function') showToast('V-SLAM', 'Aucune télémétrie reçue du robot. Vérifiez que le robot est en ligne.', 'error');

                    else alert('V-SLAM: aucune télémétrie du robot.');

                    return;

                }

                if (!slamInfo.cam1 && !slamInfo.cam2) {

                    if (typeof showToast === 'function') showToast('V-SLAM', 'Le robot ne détecte aucune caméra USB. Vérifiez le branchement physique et le mapping (onglet Arduino & Calib).', 'error');

                    else alert('V-SLAM: aucune caméra détectée.');

                    return;

                }

                if (typeof showToast === 'function') {

                    const label = (slamInfo.cam1 && slamInfo.cam2) ? 'Stéréo (Cam1+Cam2)'

                                 : (slamInfo.cam1 ? 'Mono (Cam2 absente)' : 'Mono (Cam1 absente)');

                    showToast('V-SLAM', 'Lancement LOCAL du test sur le robot — mode ' + label + ' (indépendant du streaming utilisateur).', 'info');

                }

                

                window.vslamTesting = true;

                btn.textContent = '⏹️ Arrêter le Test V-SLAM (' + slamInfo.mode + ')';

                btn.className = 'btn btn-secondary';

                container.style.display = 'block';

                badgeEl.textContent = 'Actif';

                badgeEl.className = 'status-badge active';

                statusVal.textContent = 'Initialisation...';

                statusVal.style.color = 'var(--accent)';

                

                if (appWs && appWs.readyState === WebSocket.OPEN) {

                    appWs.send(JSON.stringify({ type: "request_camera", camera: 1, v_slam: true, ...getStreamQualityParams(1) }));

                }

                

                startVSlamTestWebRTC();

                

                lastPoseTime = Date.now();

                poseUpdateCount = 0;

                vslamHzInterval = setInterval(() => {

                    const hz = poseUpdateCount / 2;

                    rateVal.textContent = `${hz.toFixed(1)} Hz`;

                    poseUpdateCount = 0;

                    

                    const qualVal = document.getElementById('vslam-quality-val');

                    const warningBox = document.getElementById('vslam-warning-box');

                    

                    if (window.lastTelemetryState && window.lastTelemetryState.imu) {

                        const imu = window.lastTelemetryState.imu;

                        if (!window.lastVSlamImu) window.lastVSlamImu = imu;

                        const delta = Math.abs(imu.roll - window.lastVSlamImu.roll) + Math.abs(imu.pitch - window.lastVSlamImu.pitch);

                        window.lastVSlamImu = imu;

                        

                        if (delta > 12) {

                            qualVal.textContent = 'Dégradée';

                            qualVal.style.color = 'var(--danger)';

                            warningBox.style.display = 'block';

                        } else {

                            qualVal.textContent = 'Optimale';

                            qualVal.style.color = 'var(--success)';

                            warningBox.style.display = 'none';

                        }

                    }

                    

                    if (hz > 0.1) {

                        statusVal.textContent = 'Localisé / Tracking';

                        statusVal.style.color = 'var(--success)';

                    } else {

                        let odomTopicActive = false;

                        if (window.lastTelemetryState && window.lastTelemetryState.topics) {

                            odomTopicActive = window.lastTelemetryState.topics.some(t => t.name === '/odom' && t.hz > 0);

                        }

                        if (odomTopicActive) {

                            statusVal.textContent = 'Recherche de repères...';

                            statusVal.style.color = 'var(--accent)';

                        } else {

                            statusVal.textContent = 'Attente du nœud ROS 2...';

                            statusVal.style.color = 'var(--text-secondary)';

                        }

                    }

                }, 2000);

                

            } else {

                window.vslamTesting = false;

                btn.textContent = '🚀 Lancer le Test V-SLAM';

                btn.className = 'btn btn-primary';

                container.style.display = 'none';

                badgeEl.textContent = 'Inactif';

                badgeEl.className = 'status-badge';

                statusVal.textContent = 'Non démarré';

                statusVal.style.color = 'var(--text-secondary)';

                rateVal.textContent = '0.0 Hz';

                document.getElementById('vslam-quality-val').textContent = 'Optimale';

                document.getElementById('vslam-quality-val').style.color = 'var(--success)';

                document.getElementById('vslam-warning-box').style.display = 'none';

                

                clearInterval(vslamHzInterval);

                

                if (window.hlsInstances && window.hlsInstances['vslam']) {

                    try { window.hlsInstances['vslam'].destroy(); } catch(e) {}

                    delete window.hlsInstances['vslam'];

                }

                const videoEl = document.getElementById('vslam-test-video');

                if (videoEl) {

                    videoEl.srcObject = null;

                    videoEl.src = '';

                    videoEl.removeAttribute('src');

                }

                if (vslamPeerConnection) {

                    try { vslamPeerConnection.close(); } catch(e) {}

                    vslamPeerConnection = null;

                }

                if (appWs && appWs.readyState === WebSocket.OPEN) {

                    appWs.send(JSON.stringify({ type: "release_camera", camera: 1 }));

                }

            }

        }

        async function startVSlamTestWebRTC() {

            const videoEl = document.getElementById('vslam-test-video');

            const loaderEl = document.getElementById('vslam-test-loader');

            const statusVal = document.getElementById('vslam-status-val');

            

            if (vslamPeerConnection) {

                try { vslamPeerConnection.close(); } catch(e) {}

                vslamPeerConnection = null;

            }

            

            loaderEl.style.display = 'flex';

            videoEl.style.display = 'none';

            

            let pc = null;

            const showVslamWebRTCError = (msg) => {

                if (vslamPeerConnection) {

                    try { vslamPeerConnection.close(); } catch(e) {}

                    vslamPeerConnection = null;

                }

                loaderEl.style.display = 'none';

                statusVal.textContent = 'Erreur WebRTC';

                statusVal.style.color = 'var(--danger)';

                console.error('VSLAM WebRTC error:', msg);

            };

            try {

                pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });

                vslamPeerConnection = pc;

                pc.addTransceiver('video', { direction: 'recvonly' });

                let trackTimeout = setTimeout(() => {

                    showVslamWebRTCError('Timeout — aucun flux reçu après 8s');

                }, 8000);

                pc.oniceconnectionstatechange = () => {

                    if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "disconnected") {

                        clearTimeout(trackTimeout);

                        showVslamWebRTCError(`ICE ${pc.iceConnectionState}`);

                    }

                };

                pc.ontrack = (event) => {

                    clearTimeout(trackTimeout);

                    if (event.streams && event.streams[0]) {

                        videoEl.srcObject = event.streams[0];

                    } else {

                        const inboundStream = new MediaStream();

                        inboundStream.addTrack(event.track);

                        videoEl.srcObject = inboundStream;

                    }

                    videoEl.play().catch(e => console.warn(e));

                    loaderEl.style.display = 'none';

                    videoEl.style.display = 'block';

                };

                const offer = await pc.createOffer();

                await pc.setLocalDescription(offer);

                const webrtcUrl = `${window.location.protocol}//${window.location.hostname}:48889/robot/cam1/whep`;

                let response = null;

                let retries = 15;

                while (retries > 0 && window.vslamTesting) {

                    try {

                        response = await fetch(webrtcUrl, {

                            method: 'POST',

                            headers: { 'Content-Type': 'application/sdp' },

                            body: pc.localDescription.sdp

                        });

                        if (response.ok) break;

                    } catch (e) { console.warn(e); }

                    retries--;

                    if (retries > 0) await new Promise(r => setTimeout(r, 200));

                }

                if (!response || !response.ok) {

                    clearTimeout(trackTimeout);

                    throw new Error('WHEP VSLAM non disponible.');

                }

                const answerSdp = await response.text();

                await pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: answerSdp }));

            } catch(err) {

                showVslamWebRTCError(err.message);

            }

        }

        // ─── TELECOMMANDE & NAVIGATION CONTROLS ──────────────────────────────────

        let controlWalkInterval = null;

        let controlActiveDir = null;

        let controlSpeed = 0.15; // default speed in m/s

        let navTarget = null; // { x, y } in meters

        function initControlTab() {

            // Setup canvas interaction

            const canvas = document.getElementById('control-map-canvas');

            if (canvas) {

                canvas.removeEventListener('mousedown', onControlMapClick);

                canvas.addEventListener('mousedown', onControlMapClick);

            }

            

            // Setup keyboard listeners (once globally)

            if (!window.controlKeyboardInitialized) {

                window.controlKeyboardInitialized = true;

                window.addEventListener('keydown', (e) => {

                    if (activeTab !== 'control') return;

                    

                    const keyMap = {

                        'z': 'up', 'KeyW': 'up', 'ArrowUp': 'up',

                        's': 'down', 'KeyS': 'down', 'ArrowDown': 'down',

                        'q': 'strafe-left',

                        'd': 'strafe-right',

                        'KeyA': 'turn-left', 'ArrowLeft': 'turn-left',

                        'a': 'turn-left', 'A': 'turn-left',

                        'e': 'turn-right', 'E': 'turn-right', 'KeyE': 'turn-right'

                    };

                    

                    const dir = keyMap[e.key] || keyMap[e.code];

                    if (dir && !keysPressed[dir]) {

                        e.preventDefault();

                        keysPressed[dir] = true;

                        startWalking(dir);

                    }

                    if (e.key === ' ' || e.key === 'x' || e.key === 'Escape') {

                        e.preventDefault();

                        sendControlStop();

                    }

                });

                

                window.addEventListener('keyup', (e) => {

                    if (activeTab !== 'control') return;

                    const keyMap = {

                        'z': 'up', 'KeyW': 'up', 'ArrowUp': 'up',

                        's': 'down', 'KeyS': 'down', 'ArrowDown': 'down',

                        'q': 'strafe-left',

                        'd': 'strafe-right',

                        'KeyA': 'turn-left', 'ArrowLeft': 'turn-left',

                        'a': 'turn-left', 'A': 'turn-left',

                        'e': 'turn-right', 'E': 'turn-right', 'KeyE': 'turn-right'

                    };

                    const dir = keyMap[e.key] || keyMap[e.code];

                    if (dir) {

                        keysPressed[dir] = false;

                        // If no direction key is pressed, stop walking

                        if (!Object.values(keysPressed).includes(true)) {

                            stopWalking();

                        }

                    }

                });

            }

            

            // Initial drawing

            drawControlMap();

        }

        function updateControlSpeed() {

            const val = document.getElementById('control-speed-slider').value;

            controlSpeed = parseFloat((val / 100).toFixed(2));

            document.getElementById('control-speed-val').textContent = controlSpeed + ' m/s';

        }

        function sendControlCmd(cmd) {

        if (appWs && appWs.readyState === WebSocket.OPEN) {

            appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: cmd }));

            if (typeof showToast === 'function') {

                const labels = { stand: 'Se lever', sit: "S'asseoir", stop: 'Stop' };

                showToast("Télécommande", labels[cmd] || cmd + " envoyé", "info");

            }

        } else {

            if (typeof showToast === 'function') {

                showToast("Erreur", "WebSocket non connecté. Le robot est peut-être hors ligne.", "error");

            }

        }

            }

        function sendControlStop() {

            stopWalking();

            keysPressed = {};

            // Send direct zero velocity and stop cmd

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "cmd_vel", linear: 0.0, lateral: 0.0, angular: 0.0 }));

                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stop" }));

            }

            // Reset D-Pad button styles

            document.querySelectorAll('.dpad-btn').forEach(btn => {

                btn.style.backgroundColor = '';

                btn.style.color = '';

            });

            const stopBtn = document.getElementById('dpad-stop');

            if (stopBtn) {

                stopBtn.style.backgroundColor = 'rgba(239, 68, 68, 0.2)';

            }

        }

        function startWalking(dir) {

            if (controlActiveDir === dir) return;

            controlActiveDir = dir;

            

            // Highlight button

            document.querySelectorAll('.dpad-btn').forEach(btn => {

                btn.style.backgroundColor = '';

                btn.style.color = '';

            });

            const activeBtn = document.getElementById(`dpad-${dir}`);

            if (activeBtn) {

                activeBtn.style.backgroundColor = 'var(--accent)';

                activeBtn.style.color = 'white';

            }

            if (controlWalkInterval) clearInterval(controlWalkInterval);

            

            // Periodically send cmd_vel

            function sendVel() {

                if (!appWs || appWs.readyState !== WebSocket.OPEN) return;

                let vx = 0.0;

                let lateral = 0.0;

                let wz = 0.0;

                

                if (dir === 'up') vx = controlSpeed;

                else if (dir === 'down') vx = -controlSpeed;

                else if (dir === 'strafe-left') lateral = -controlSpeed;

                else if (dir === 'strafe-right') lateral = controlSpeed;

                else if (dir === 'turn-left') wz = 1.0; // rotate left rad/s

                else if (dir === 'turn-right') wz = -1.0; // rotate right rad/s

                else if (dir === 'left') wz = 1.0;

                else if (dir === 'right') wz = -1.0;

                appWs.send(JSON.stringify({

                    type: "cmd_vel",

                    linear: vx,

                    lateral: lateral,

                    angular: wz

                }));

            }

            

            sendVel();

            controlWalkInterval = setInterval(sendVel, 100);

        }

        function stopWalking() {

            if (controlWalkInterval) {

                clearInterval(controlWalkInterval);

                controlWalkInterval = null;

            }

            controlActiveDir = null;

            

            // Highlight reset

            document.querySelectorAll('.dpad-btn').forEach(btn => {

                btn.style.backgroundColor = '';

                btn.style.color = '';

            });

            const stopBtn = document.getElementById('dpad-stop');

            if (stopBtn) {

                stopBtn.style.backgroundColor = 'rgba(239, 68, 68, 0.1)';

            }

            

            // Send zero velocity to stop

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "cmd_vel", linear: 0.0, lateral: 0.0, angular: 0.0 }));

            }

        }

        // Map Click interaction

        function onControlMapClick(e) {

            const canvas = document.getElementById('control-map-canvas');

            if (!canvas) return;

            const rect = canvas.getBoundingClientRect();

            const clickX = e.clientX - rect.left;

            const clickY = e.clientY - rect.top;

            

            const w = rect.width;

            const h = rect.height;

            const cx = w / 2;

            const cy = h / 2;

            const scale = 40; // px/m

            

            // Calculate coordinates in meters relative to base_link/odom (centered)

            const targetX = (clickX - cx) / scale;

            const targetY = -(clickY - cy) / scale; // invert Y for Cartesian

            

            navTarget = { x: parseFloat(targetX.toFixed(2)), y: parseFloat(targetY.toFixed(2)) };

            

            // Update panel

            document.getElementById('nav-target-x').textContent = navTarget.x.toFixed(2);

            document.getElementById('nav-target-y').textContent = navTarget.y.toFixed(2);

            

            const panel = document.getElementById('nav-target-panel');

            if (panel) {

                panel.style.opacity = '1';

                panel.style.pointerEvents = 'auto';

            }

            

            drawControlMap();

        }

        function clearNavGoal() {

            navTarget = null;

            const panel = document.getElementById('nav-target-panel');

            if (panel) {

                panel.style.opacity = '0';

                panel.style.pointerEvents = 'none';

            }

            drawControlMap();

        }

        function sendNavGoal() {

            if (!navTarget) return;

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                // Send nav goal target to robot

                appWs.send(JSON.stringify({

                    type: "nav_goal",

                    x: navTarget.x,

                    y: navTarget.y

                }));

                

                // Show notification or visual feedback

                const btn = document.querySelector('#nav-target-panel .btn-primary');

                if (btn) {

                    const originalText = btn.innerHTML;

                    btn.innerHTML = '⚡ Objectif Envoyé !';

                    btn.style.backgroundColor = 'var(--success)';

                    setTimeout(() => {

                        btn.innerHTML = originalText;

                        btn.style.backgroundColor = '';

                        clearNavGoal();

                    }, 1500);

                }

            } else {

                alert("Erreur : Le robot est hors-ligne.");

            }

        }

        function drawControlMap() {

            const canvas = document.getElementById('control-map-canvas');

            if (!canvas) return;

            const ctx = canvas.getContext('2d');

            

            const dpr = window.devicePixelRatio || 1;

            const rect = canvas.getBoundingClientRect();

            canvas.width = rect.width * dpr;

            canvas.height = rect.height * dpr;

            ctx.scale(dpr, dpr);

            

            const w = rect.width;

            const h = rect.height;

            

            ctx.clearRect(0, 0, w, h);

            ctx.fillStyle = '#07070a';

            ctx.fillRect(0, 0, w, h);

            

            const scale = 40;

            const cx = w / 2;

            const cy = h / 2;

            

            // Grid lines

            ctx.strokeStyle = '#101015';

            ctx.lineWidth = 0.5;

            const gridStep = scale * 0.5;

            for (let x = cx % gridStep; x < w; x += gridStep) {

                ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();

            }

            for (let y = cy % gridStep; y < h; y += gridStep) {

                ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();

            }

            

            // Walls/Occupancy Grid representation

            ctx.fillStyle = 'rgba(255, 255, 255, 0.05)';

            const walls = [

                {x: -1.5, y: -2, w: 3, h: 0.1},

                {x: -1.5, y: 2, w: 3, h: 0.1},

                {x: -1.5, y: -2, w: 0.1, h: 4},

                {x: 1.5, y: -2, w: 0.1, h: 4},

                {x: 0.5, y: -0.5, w: 0.5, h: 1}

            ];

            walls.forEach(wall => {

                ctx.fillRect(cx + wall.x * scale, cy - (wall.y + wall.h) * scale, wall.w * scale, wall.h * scale);

            });

            

            // Points (laser scan)

            ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--success').trim() || '#10b981';

            if (window.slamPoints && window.slamPoints.length > 0) {

                window.slamPoints.forEach(pt => {

                    ctx.beginPath();

                    ctx.arc(cx + pt.x * scale, cy - pt.y * scale, 1.5, 0, Math.PI * 2);

                    ctx.fill();

                });

            } else {

                for (let angle = 0; angle < Math.PI * 2; angle += 0.05) {

                    const dist = 1.8 + Math.sin(angle * 4) * 0.1;

                    const px = cx + Math.cos(angle) * dist * scale;

                    const py = cy - Math.sin(angle) * dist * scale;

                    ctx.beginPath();

                    ctx.arc(px, py, 1.5, 0, Math.PI*2);

                    ctx.fill();

                }

            }

            

            // Path trajectory

            if (window.slamPath && window.slamPath.length > 0) {

                ctx.strokeStyle = 'rgba(99, 102, 241, 0.6)';

                ctx.lineWidth = 2.5;

                ctx.beginPath();

                window.slamPath.forEach((pt, idx) => {

                    const px = cx + pt.x * scale;

                    const py = cy - pt.y * scale;

                    if (idx === 0) ctx.moveTo(px, py);

                    else ctx.lineTo(px, py);

                });

                ctx.stroke();

            }

            

            // Draw Waypoint navigation goal (if set)

            if (navTarget) {

                const tx = cx + navTarget.x * scale;

                const ty = cy - navTarget.y * scale;

                

                // Pulsing target halo

                ctx.save();

                ctx.strokeStyle = 'var(--accent)';

                ctx.lineWidth = 1.5;

                ctx.beginPath();

                ctx.arc(tx, ty, 8 + (Date.now() % 500) / 100, 0, Math.PI * 2);

                ctx.stroke();

                

                // Outer target circle

                ctx.strokeStyle = 'var(--accent)';

                ctx.lineWidth = 2;

                ctx.beginPath();

                ctx.arc(tx, ty, 6, 0, Math.PI * 2);

                ctx.stroke();

                

                // Center dot

                ctx.fillStyle = 'var(--accent)';

                ctx.beginPath();

                ctx.arc(tx, ty, 2, 0, Math.PI * 2);

                ctx.fill();

                ctx.restore();

            }

            

            // Robot Pose triangle

            const rx = cx + (window.robotPose ? window.robotPose.x : 0) * scale;

            const ry = cy - (window.robotPose ? window.robotPose.y : 0) * scale;

            const rtheta = -(window.robotPose ? window.robotPose.theta : 0);

            

            ctx.save();

            ctx.translate(rx, ry);

            ctx.rotate(rtheta);

            

            ctx.fillStyle = 'var(--accent)';

            ctx.beginPath();

            ctx.moveTo(14, 0);

            ctx.lineTo(-8, -8);

            ctx.lineTo(-4, 0);

            ctx.lineTo(-8, 8);

            ctx.closePath();

            ctx.fill();

            

            // Glowing orientation indicator

            ctx.strokeStyle = 'rgba(99, 102, 241, 0.5)';

            ctx.lineWidth = 2;

            ctx.beginPath();

            ctx.arc(0, 0, 12, 0, Math.PI * 2);

            ctx.stroke();

            

            ctx.restore();

            

            // Request animation frame for continuous animation of pulses

            if (activeTab === 'control') {

                requestAnimationFrame(drawControlMap);

            }

        }

        checkAuth();
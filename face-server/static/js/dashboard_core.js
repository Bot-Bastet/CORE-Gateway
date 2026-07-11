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


window.apiToken = localStorage.getItem('bastet_api_token') || window._bastet_token || '';
window.activeTab = localStorage.getItem('bastetActiveTab') || 'dashboard';
window.telemetryInterval = null;
window.updateInterval = null;
window.accountsCached = {};
window.activeFolderName = null;
window.facesCached = [];
window.appWs = null;
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
            (window.connectGlobalWebSocket || connectGlobalWebSocket)();
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
                (window.handleIncomingWebSocketMessage || handleIncomingWebSocketMessage)(event.data);
            };
            

            appWs.onclose = () => {
                console.log("Global WebSocket déconnecté. Reconnexion...");
                setTimeout(window.connectGlobalWebSocket || connectGlobalWebSocket, 3000);
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


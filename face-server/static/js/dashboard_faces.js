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
        window.switchTab = switchTab;
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


        // EasyConfig functions moved to static/js/easyconfig.js
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
        let keysPressed = {};


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
                        'q': 'left', 'KeyA': 'left', 'ArrowLeft': 'left',
                        'd': 'right', 'KeyD': 'right', 'ArrowRight': 'right'
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
                        'q': 'left', 'KeyA': 'left', 'ArrowLeft': 'left',
                        'd': 'right', 'KeyD': 'right', 'ArrowRight': 'right'
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
                appWs.send(JSON.stringify({ type: "cmd_vel", linear: 0.0, angular: 0.0 }));
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
                let wz = 0.0;
                

                if (dir === 'up') vx = controlSpeed;
                else if (dir === 'down') vx = -controlSpeed;
                else if (dir === 'left') wz = 1.0; // rotate left rad/s
                else if (dir === 'right') wz = -1.0; // rotate right rad/s
                

                appWs.send(JSON.stringify({
                    type: "cmd_vel",
                    linear: vx,
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
                appWs.send(JSON.stringify({ type: "cmd_vel", linear: 0.0, angular: 0.0 }));
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
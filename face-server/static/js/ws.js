// === WebSocket + handler ===
        // ─── WEBSOCKET CLIENT ─────────────────────────────────────────────────
        
        function connectGlobalWebSocket() {
            if (appWs && (appWs.readyState === WebSocket.OPEN || appWs.readyState === WebSocket.CONNECTING)) return;
            
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws/app?token=${apiToken}`;
            window.appWs = new WebSocket(wsUrl);
            
            window.appWs.onopen = () => {
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
            
            window.appWs.onmessage = (event) => {
                handleIncomingWebSocketMessage(event.data);
            };
            
            window.appWs.onclose = () => {
                console.log("Global WebSocket déconnecté. Reconnexion...");
                setTimeout(connectGlobalWebSocket, 3000);
            };
            
            window.appWs.onerror = (e) => {
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
                    // os.path.exists from ros2_listener, which returns True for Pi5
                    // ISP virtual nodes and causes the 'vue d ensemble' to flicker
                    // 1<->2 every ~0.5s as WS broadcasts alternate).
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
                        // Toujours mettre à jour le cache global des angles servo réels
                        window.latestServoAngles = payload.joints.slice();

                        for (let i = 0; i < 12; i++) {
                            const angle = payload.joints[i];
                            const valEl = document.getElementById(`joint-val-${i}`);
                            const sliderEl = document.getElementById(`joint-slider-${i}`);
                            if (!window.manualJointControlActive) {
                                if (valEl) valEl.textContent = `${Math.round(angle)}°`;
                                if (sliderEl) sliderEl.value = Math.round(angle);
                            }
                        }

                        // Synchroniser le viewer 3D en mode Actif (non-démo)
                        const demoCheck = document.getElementById('demo-mode-checkbox');
                        const isDemo = demoCheck ? demoCheck.checked : true;
                        if (!isDemo && typeof window.updateSpotMicroServos === 'function') {
                            window.updateSpotMicroServos(payload.joints);
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

                        // BNO085 detection: if roll/pitch/yaw all 0.0 for >10s while Arduino is online, show warning
                        if (roll === 0.0 && pitch === 0.0 && yaw === 0.0) {
                            window._bnoZeroCount = (window._bnoZeroCount || 0) + 1;
                        } else {
                            window._bnoZeroCount = 0;
                        }
                        const warningEl = document.getElementById('imu-bno085-warning');
                        if (warningEl) {
                            const hasJoints = payload.joints && payload.joints.length === 12;
                            if (window._bnoZeroCount >= 20 && hasJoints) {
                                warningEl.style.display = '';
                            } else if (window._bnoZeroCount === 0) {
                                warningEl.style.display = 'none';
                            }
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
                                else if (payload.type === "stream_state_sync") {
                    const cid = parseInt(payload.camera);
                    const running = payload.running === true;
                    const viewers = (typeof payload.viewers === "number") ? payload.viewers : 0;
                    if (!window.activeStreams) window.activeStreams = { 1: false, 2: false };
                    if (!window.streamViewers) window.streamViewers = { 1: 0, 2: 0 };
                    if (!window.userClosedStream) window.userClosedStream = { 1: false, 2: false };
                    window.activeStreams[cid] = running;
                    window.streamViewers[cid] = viewers;
                    // Auto-attach dynamique : si flux devenu actif et user n'a pas coupe, on suit.
                    // Idempotent via guard streamingState dans toggleStream.
                    if (running && (!window.localViewing || !window.localViewing[cid]) && !window.userClosedStream[cid]) {
                        try { toggleStream(cid); } catch (e) { console.warn('auto-attach (sync) failed', e); }
                    }
                    // Badge optionnel "X viewers watching" si la zone d'UI l'expose
                    const syncBadge = document.getElementById('stream-viewers-' + cid);
                    if (syncBadge) {
                        if (viewers > 0) { syncBadge.textContent = '👁 ' + viewers; syncBadge.title = viewers + ' viewer(s)'; }
                        else { syncBadge.textContent = ''; syncBadge.title = ''; }
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
                            if (!userCut) {
                                statusEl.textContent = isActive ? 'En direct' : 'Inactif';
                                statusEl.className = isActive ? 'status-badge active' : 'status-badge';
                            }
                        }
                        if (btnText) {
                            btnText.textContent = isActive ? 'Rejoindre le flux' : 'D\u00e9marrer le flux';
                        }

                        // Auto-rejoindre seulement si flux devenu actif ET user n'a pas coup\u00e9 manuellement
                        if (isActive && (!window.userClosedStream || !window.userClosedStream[camId])) {
                            toggleStream(camId);
                        }
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
                else if (payload.type === "robot_posture_sync") {
                    // Server-synced posture state (no cache/localStorage)
                    if (payload.robot_posture && typeof applyRobotPostureSync === "function") {
                        applyRobotPostureSync(payload.robot_posture);
                    }
                }
            } catch(e) {
                // not json or parsing error
            }
        }

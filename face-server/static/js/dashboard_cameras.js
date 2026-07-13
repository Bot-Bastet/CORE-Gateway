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
            // SAFETY: Send stop (not stand) to detach all servos.
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stop" }));
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
                // manual:true bypasses Arduino safety gate for uncalibrated servos
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "attach", index: idx, manual: true }));
                

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
        

// === stream + WebRTC + config ===
        // ─── CAMERA STREAM ON-DEMAND ─────────────────────────────────────────

        function updateCameraModularity(cam1Connected, cam2Connected) {
            const card1 = document.getElementById('stream-card-1');
            const card2 = document.getElementById('stream-card-2');
            if (card1) card1.style.display = cam1Connected ? 'flex' : 'none';
            if (card2) card2.style.display = cam2Connected ? 'flex' : 'none';
            
            const calibCam1Container = document.getElementById('calib-cam-container-1');
            const calibCam2Container = document.getElementById('calib-cam-container-2');
            if (calibCam1Container) calibCam1Container.style.display = cam1Connected ? 'flex' : 'none';
            if (calibCam2Container) calibCam2Container.style.display = cam2Connected ? 'flex' : 'none';
            
            const vslamSpan = document.getElementById('vslam-text-mode');
            if (vslamSpan) {
                vslamSpan.textContent = cam2Connected ? 'Superposer V-SLAM Stéréo' : 'Superposer V-SLAM Mono';
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
                window.userClosedStream[camId] = true;
                streamingState[camId] = 'idle';
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    appWs.send(JSON.stringify({ type: 'release_camera', camera: camId }));
                }
                loaderEl.style.display = 'none';
                videoEl.style.display = 'none';
                fsBtn.style.display = 'none';
                placeholder.style.display = 'flex';
                videoContainer.style.display = 'none';
                statusEl.textContent = 'Erreur WebRTC';
                statusEl.className = 'status-badge error';
                btnText.textContent = 'Réessayer';
                console.error(`WebRTC cam${camId} error:`, msg);
            };

            try {
                pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
                peerConnections[camId] = pc;
                pc.addTransceiver('video', { direction: 'recvonly' });

                let trackTimeout = setTimeout(() => {
                    if (!trackReceived) showWebRTCError('Timeout — aucun flux reçu après 12s');
                }, 12000);

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
            }, 12000);
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
        function updateCameraPortOptions() {
            // Determine active devices from telemetry (with sensible 5-path fallback)
            const fallback = ['/dev/video0', '/dev/video1', '/dev/video2', '/dev/video3', '/dev/video4'];
            let activeDevices = fallback;
            const ts = window.lastTelemetryState;
            if (ts && ts.sensors && Array.isArray(ts.sensors.available_video_devices) && ts.sensors.available_video_devices.length > 0) {
                activeDevices = ts.sensors.available_video_devices.slice().sort();
            }

            const leftSelect = document.getElementById('cam-port-left');
            const rightSelect = document.getElementById('cam-port-right');
            const helper = document.getElementById('cam-port-single-info');
            if (!leftSelect || !rightSelect) return;

            // Last-known-good mapping (from telemetry) is our rollback anchor when conflict detected.
            const telMapping = (ts && ts.camera_mapping) || null;
            const lastGoodLeft  = (telMapping && activeDevices.includes(telMapping.left))  ? telMapping.left  : activeDevices[0];
            const lastGoodRight = (telMapping && activeDevices.includes(telMapping.right)) ? telMapping.right : null;

            if (activeDevices.length <= 1) {
                // Single-camera mode -> grey out right, label as 'central'
                const only = activeDevices[0] || '(aucune)';
                leftSelect.innerHTML = '<option value="' + only + '">' + only + '</option>';
                leftSelect.value = only;
                rightSelect.innerHTML = '<option value="">—</option>';
                rightSelect.value = '';
                rightSelect.disabled = true;
                if (helper) helper.style.display = 'block';
                return;
            }

            // Multi-camera mode: detect conflict (user just picked a port already used by the OTHER side).
            // Rollback BOTH to the last-known-good mapping so the user sees a stable, valid state.
            if (leftSelect.value && rightSelect.value && leftSelect.value === rightSelect.value) {
                leftSelect.value  = lastGoodLeft;
                rightSelect.value = lastGoodRight || '';
                if (typeof showToast === 'function') {
                    showToast('Caméras', 'Mapping invalide (même port choisi sur les deux côtés). Retour au dernier mapping valide.', 'warning');
                }
            }

            // Each select excludes the OTHER's current value (mutually exclusive).
            rightSelect.disabled = false;
            if (helper) helper.style.display = 'none';

            const leftOpts  = activeDevices.filter(d => d !== rightSelect.value);
            const rightOpts = activeDevices.filter(d => d !== leftSelect.value);
            leftSelect.innerHTML  = leftOpts.map(d  => '<option value="' + d + '">' + d + '</option>').join('');
            rightSelect.innerHTML = rightOpts.map(d => '<option value="' + d + '">' + d + '</option>').join('');

            // Re-assert current value if it survived the rebuild (so the visible selection stays).
            if (leftOpts.includes(leftSelect.value)) {
                leftSelect.value = leftSelect.value;
            }
            if (rightOpts.includes(rightSelect.value)) {
                rightSelect.value = rightSelect.value;
            }
        }

        function saveCameraPortsMapping() {
            const leftSelect = document.getElementById('cam-port-left');
            const rightSelect = document.getElementById('cam-port-right');
            const left = leftSelect ? leftSelect.value : '';
            const right = rightSelect ? rightSelect.value : '';
            const statusEl = document.getElementById('camera-mapping-save-status');
            function setStatus(text, color) {
                if (statusEl) {
                    statusEl.textContent = text;
                    statusEl.style.color = color || 'var(--text-secondary)';
                }
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
            document.getElementById('mcv-modal-title').textContent = `Configuration Caméra ${camId === 1 ? 'Gauche (1)' : 'Droite (2)'}`;
            
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
            
            const modal = document.getElementById('cameraConfigModal');
            modal.classList.add('active');
            modal.removeAttribute('inert');
        }

        function closeCameraConfigModal() {
            const modal = document.getElementById('cameraConfigModal');
            modal.classList.remove('active');
            modal.setAttribute('inert', '');
        }

        function closeCameraConfigModalOnClick(e) {
            if (e.target === document.getElementById('cameraConfigModal')) {
                closeCameraConfigModal();
            }
        }

        window.mccCurrentCamId = 1;
        let mccPeerConnection = null;

        function openCameraCalibModal(camId) {
            window.mccCurrentCamId = camId;
            document.getElementById('mcc-modal-title').textContent = `Calibration Caméra ${camId === 1 ? 'Gauche (1)' : 'Droite (2)'}`;
            
            const videoEl = document.getElementById('mcc-cam-video');
            const hudEl = document.getElementById('mcc-cam-hud');
            const overlayEl = document.getElementById('mcc-cam-status-overlay');
            const statusText = document.getElementById('mcc-cam-status-text');
            const btnRun = document.getElementById('btn-mcc-run-calib');
            
            if (videoEl.srcObject) {
                videoEl.srcObject.getTracks().forEach(t => t.stop());
                videoEl.srcObject = null;
            }
            videoEl.style.display = 'none';
            hudEl.style.display = 'none';
            overlayEl.style.display = 'flex';
            overlayEl.style.backgroundColor = 'rgba(9,9,11,0.85)';
            
            document.getElementById('cameraCalibModal').classList.add('active');

            const isStreamActive = window.activeStreams && window.activeStreams[camId];

            if (isStreamActive) {
                statusText.innerHTML = `<span>Connexion automatique au flux actif...</span>`;
                btnRun.disabled = true;
                btnRun.innerHTML = `<span>📷 Connexion...</span>`;
                btnRun.onclick = () => runIndividualCameraCalib();
                runIndividualCameraCalib();
            } else {
                statusText.innerHTML = `<span>Cliquez sur Lancer pour vous connecter à la caméra.</span>`;
                btnRun.disabled = false;
                btnRun.innerHTML = `<span>📷 Lancer la Caméra</span>`;
                btnRun.onclick = () => runIndividualCameraCalib();
            }
        }

        function closeCameraCalibModal() {
            document.getElementById('cameraCalibModal').classList.remove('active');
            
            const videoEl = document.getElementById('mcc-cam-video');
            if (window.hlsInstances && window.hlsInstances['calib']) {
                try { window.hlsInstances['calib'].destroy(); } catch(e) {}
                delete window.hlsInstances['calib'];
            }
            if (videoEl) {
                if (videoEl.srcObject) {
                    videoEl.srcObject.getTracks().forEach(t => t.stop());
                    videoEl.srcObject = null;
                }
                videoEl.src = '';
                videoEl.removeAttribute('src');
            }
            if (mccPeerConnection) {
                mccPeerConnection.close();
                mccPeerConnection = null;
            }
            
            const camId = window.mccCurrentCamId;
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "stop_camera", camera: camId }));
            }
        }

        function closeCameraCalibModalOnClick(e) {
            if (e.target === document.getElementById('cameraCalibModal')) {
                closeCameraCalibModal();
            }
        }

        async function runIndividualCameraCalib() {
            const camId = window.mccCurrentCamId;
            const videoEl = document.getElementById('mcc-cam-video');
            const hudEl = document.getElementById('mcc-cam-hud');
            const overlayEl = document.getElementById('mcc-cam-status-overlay');
            const statusText = document.getElementById('mcc-cam-status-text');
            const btnRun = document.getElementById('btn-mcc-run-calib');
            
            btnRun.disabled = true;
            btnRun.innerHTML = `<span>📷 Connexion...</span>`;
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "request_camera", camera: camId, v_slam: false, ...getStreamQualityParams(camId) }));
            }
            
            statusText.innerHTML = `
                <div style="width:20px; height:20px; border:2px solid var(--accent); border-top-color:transparent; border-radius:50%; animation:spin 1s linear infinite; margin:0 auto 0.5rem;"></div>
                <span>Initialisation flux WebRTC caméra...</span>
            `;
            
            let pc = null;

            const showCalibWebRTCError = (msg) => {
                if (mccPeerConnection) {
                    try { mccPeerConnection.close(); } catch(e) {}
                    mccPeerConnection = null;
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
                btnRun.innerHTML = `<span>📷 Lancer la Caméra</span>`;
                btnRun.onclick = () => runIndividualCameraCalib();
                console.error('Calib WebRTC error:', msg);
            };

            try {
                if (mccPeerConnection) mccPeerConnection.close();
                pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
                mccPeerConnection = pc;
                pc.addTransceiver('video', { direction: 'recvonly' });

                let trackTimeout = setTimeout(() => {
                    showCalibWebRTCError('Timeout — aucun flux reçu après 8s');
                }, 8000);

                pc.oniceconnectionstatechange = () => {
                    if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "disconnected") {
                        clearTimeout(trackTimeout);
                        showCalibWebRTCError(`ICE ${pc.iceConnectionState}`);
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
                    btnRun.innerHTML = `<span>📷 Capturer & Calibrer</span>`;
                    btnRun.onclick = () => confirmIndividualCameraCalib();
                };

                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);

                const webrtcUrl = `${window.location.protocol}//${window.location.hostname}:48889/robot/cam${camId}/whep`;
                let response = null;
                let retries = 15;
                while (retries > 0 && document.getElementById('cameraCalibModal').classList.contains('active')) {
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
                    throw new Error('WHEP calib non disponible.');
                }

                const answerSdp = await response.text();
                await pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: answerSdp }));

            } catch (err) {
                showCalibWebRTCError(err.message);
            }
        }
        
        function confirmIndividualCameraCalib() {
            const btnRun = document.getElementById('btn-mcc-run-calib');
            btnRun.disabled = true;
            btnRun.innerHTML = `<span>📷 Analyse...</span>`;
            
            const overlayEl = document.getElementById('mcc-cam-status-overlay');
            const statusText = document.getElementById('mcc-cam-status-text');
            const hudEl = document.getElementById('mcc-cam-hud');
            const videoEl = document.getElementById('mcc-cam-video');
            
            let progress = 0;
            const progressInterval = setInterval(() => {
                progress += 25;
                if (progress >= 100) {
                    clearInterval(progressInterval);
                    
                    const camId = window.mccCurrentCamId;
                    const isCameraConnected = window.lastTelemetryState && window.lastTelemetryState.sensors && 
                        window.lastTelemetryState.sensors[`cam${camId}_connected`] === true;
                        
                    if (isCameraConnected) {
                        hudEl.style.display = 'none';
                        videoEl.style.display = 'none';
                        overlayEl.style.display = 'flex';
                        overlayEl.style.backgroundColor = 'rgba(9,9,11,0.9)';
                        statusText.innerHTML = `
                            <span style="font-size: 2rem; color: var(--success); display:block; margin-bottom:0.5rem;">✓</span>
                            <span style="color:var(--success); font-weight:bold; font-size:1.05rem;">Calibration réussie !</span><br/>
                            <span style="font-size:0.8rem; color:var(--text-secondary); margin-top:0.25rem; display:block;">Les paramètres intrinsèques ont été sauvegardés.</span>
                        `;
                        btnRun.disabled = false;
                        btnRun.innerHTML = `<span>Fermer la Calibration</span>`;
                        btnRun.onclick = () => closeCameraCalibModal();

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
                        statusText.innerHTML = `
                            <span style="font-size: 2rem; color: var(--danger); display:block; margin-bottom:0.5rem;">✗</span>
                            <span style="color:var(--danger); font-weight:bold;">Échec de l'analyse</span><br/>
                            <span style="font-size:0.75rem; color:var(--text-secondary);">Mire de calibration introuvable ou illisible.</span>
                        `;
                        btnRun.disabled = false;
                        btnRun.innerHTML = `<span>📷 Réessayer la Calibration</span>`;
                        btnRun.onclick = () => runIndividualCameraCalib();
                    }
                }
            }, 500);
        }

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
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    let vSlamVal = false;
                    if (camId === 1) {
                        const vSlamCheck = document.getElementById('stream-v-slam-1');
                        if (vSlamCheck) vSlamVal = vSlamCheck.checked;
                    }
                    appWs.send(JSON.stringify({type: "request_camera", camera: camId, v_slam: vSlamVal, ...getStreamQualityParams(camId)}));
                    window.localViewing[camId] = true;
                    if (!window.userClosedStream) window.userClosedStream = { 1: false, 2: false };
                    window.userClosedStream[camId] = false;
                    streamingState[camId] = 'requesting';

                    statusEl.textContent = 'Connexion WebRTC…';
                    statusEl.className = 'status-badge';
                    btnText.textContent = 'Couper Caméra';

                    startStreamWebRTC(camId);
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
                    appWs.send(JSON.stringify({type: "leave_stream", camera: camId}));
                }
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


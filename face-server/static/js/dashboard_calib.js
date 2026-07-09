// ===== BASTET CALIBRATION MODULE (EXTRACTED) =====
// This file handles motor/camera calibration and IMU resetting.

        function openCalibrationOverlay() {

            document.getElementById('calibration-overlay').classList.add('active');

            loadSavedOffsets();

        }



        function closeCalibrationOverlay() {

            document.getElementById('calibration-overlay').classList.remove('active');

        }



        function updateCalibSliderVal(index) {

            const slider = document.getElementById(`calib-slider-${index}`);

            const label = document.getElementById(`calib-val-${index}`);

            if (slider && label) {

                label.textContent = slider.value >= 0 ? `+${slider.value}` : slider.value;

            }

        }



        function resetMotorCalibration() {

            for (let i = 0; i < 12; i++) {

                const slider = document.getElementById(`calib-slider-${i}`);

                if (slider) {

                    slider.value = 0;

                    updateCalibSliderVal(i);

                }

            }

        }



        async function resetCameraCalibration() {
            if (!confirm('Reinitialiser TOUTES les calibrations camera aux valeurs par defaut ?\n\nCela effacera les calibrations mono et stereo. Le robot devra etre re-calibre.')) return;

            // Reinitialiser sur la Gateway
            try {
                const res = await fetch('/core/camera/calibration/reset', {
                    method: 'POST',
                    headers: { 'X-API-Token': apiToken }
                });
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                console.log('Calibration reset Gateway:', data);
            } catch(e) {
                alert('Erreur lors de la reinitialisation Gateway: ' + e.message);
                return;
            }

            // Dire au robot d'effacer calib_status.json
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "reset_calibration" }));
            }

            if (typeof showToast === 'function') {
                showToast('Calibration', 'Calibrations camera reinitialisees aux valeurs par defaut.', 'success');
            } else {
                alert('Calibrations camera reinitialisees.');
            }
        }

        async function resetAndSendZeroOffsets() {

            resetMotorCalibration();

            const zeroes = new Array(12).fill(0);

            try {

                await fetch('/core/calibration', {

                    method: 'POST',

                    headers: { 'Content-Type': 'application/json', 'X-API-Token': apiToken },

                    body: JSON.stringify({ offsets: zeroes })

                });

            } catch(e) {}

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "motor_calibration", offsets: zeroes }));

            }

            loadSavedOffsets();

        }



        function sendStopServos() {

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stop" }));

            }

        }

        async function sendCalibrationOffsets() {

            const offsets = [];

            for (let i = 0; i < 12; i++) {

                const slider = document.getElementById(`calib-slider-${i}`);

                offsets.push(slider ? parseInt(slider.value) : 0);

            }

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "motor_calibration", offsets: offsets }));

            } else {

                alert("WebSocket déconnecté.");

            }

        }



        function toggleManualJointControl(checked) {

            window.manualJointControlActive = checked;

            for (let i = 0; i < 12; i++) {

                const slider = document.getElementById(`joint-slider-${i}`);

                if (slider) {

                    slider.disabled = !checked;

                    slider.style.cursor = checked ? 'pointer' : 'not-allowed';

                }

            }

            // FIX: Arreter le motion_node quand mode manuel actif (evite ecrasement des angles)

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: checked ? "stop" : "stand" }));

            }

            if (checked) {

                sendManualJointAngles();

            }

        }



        function onJointSliderInput(index, val) {

            const valEl = document.getElementById(`joint-val-${index}`);

            if (valEl) valEl.textContent = `${Math.round(val)}°`;

            sendManualJointAngles();

        }



        let lastSendManualJointTime = 0;

        let pendingManualJointTimeout = null;



        function sendManualJointAngles() {

            const now = Date.now();

            if (now - lastSendManualJointTime < 50) {

                if (!pendingManualJointTimeout) {

                    pendingManualJointTimeout = setTimeout(() => {

                        pendingManualJointTimeout = null;

                        sendManualJointAngles();

                    }, 50 - (now - lastSendManualJointTime));

                }

                return;

            }

            lastSendManualJointTime = now;



            const angles = [];

            for (let i = 0; i < 12; i++) {

                const slider = document.getElementById(`joint-slider-${i}`);

                angles.push(slider ? parseFloat(slider.value) : 90.0);

            }

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "manual_joint_control", angles: angles }));

            }

        }



        function resetIMU() {

            if (appWs && appWs.readyState === WebSocket.OPEN) {

                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "reset_imu" }));

                if (typeof showToast === 'function') showToast("IMU", "Recalibrage BNO085 en cours...", "info");

                else console.log("[IMU] Recalibrage BNO085 envoye");

            } else {

                if (typeof showToast === 'function') showToast("IMU", "WebSocket non connecte", "error");

                else console.warn("[IMU] WebSocket non connecte");

            }

        }

        

        async function saveCalibrationOffsets() {

            const offsets = [];

            for (let i = 0; i < 12; i++) {

                const slider = document.getElementById(`calib-slider-${i}`);

                offsets.push(slider ? parseInt(slider.value) : 0);

            }

            

            try {

                const res = await fetch('/core/calibration', {

                    method: 'POST',

                    headers: {

                        'Content-Type': 'application/json',

                        'X-API-Token': apiToken

                    },

                    body: JSON.stringify({ offsets: offsets })

                });

                if (res.ok) {

                    alert("Offsets sauvegardés avec succès sur la Gateway.");

                    if (appWs && appWs.readyState === WebSocket.OPEN) {

                        appWs.send(JSON.stringify({ type: "motor_calibration", offsets: offsets }));

                    }

                    loadSavedOffsets();

                } else {

                    alert("Erreur lors de la sauvegarde.");

                }

            } catch(e) {

                alert("Erreur réseau.");

            }

        }



        let calibPreviewPc = null;



        function toggleCalibPreview(camId) {

            // Open the dedicated calibration preview modal overlay (popup above calibration overlay).

            // WebRTC/WHEP path (low latency vs HLS) - uses the same pattern already proven for cam1/cam2 live streaming.

            const overlay = document.getElementById('cam-preview-overlay');

            if (!overlay) {

                alert('Erreur interne: le popup d\'apercu n\'est pas chargé. Rafraichissez la page.');

                return;

            }

            // Reset state

            const videoEl = document.getElementById('calib-preview-video');

            const loaderEl = document.getElementById('calib-preview-loader');

            const errorEl = document.getElementById('calib-preview-error');

            if (loaderEl) loaderEl.style.display = 'flex';

            if (errorEl) errorEl.style.display = 'none';

            // Destroy previous WebRTC peer connection (close old preview cleanly)

            if (calibPreviewPc) {

                try { calibPreviewPc.close(); } catch(e) {}

                calibPreviewPc = null;

            }

            if (videoEl) {

                videoEl.pause();

                videoEl.src = '';

                videoEl.srcObject = null;

            }

            // Title

            const titleEl = document.getElementById('cam-preview-title');

            if (titleEl) titleEl.textContent = 'Apercu Caméra ' + camId + ' (' + (camId === 1 ? 'Gauche' : 'Droite') + ')';

            overlay.style.display = 'flex';

            // Connect to MediaMTX WHEP endpoint - Caddy exposes HTTPS at :48889, reverse-proxied to mediamtx :8889

            const webrtcUrl = window.location.protocol + '//' + window.location.hostname + ':48889/robot/cam' + camId + '/whep';

            const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });

            calibPreviewPc = pc;

            pc.addTransceiver('video', { direction: 'recvonly' });

            pc.addTransceiver('audio', { direction: 'recvonly' });

            let trackReceived = false;

            // 12s timeout for first ontrack (camera warm-up buffer)

            const trackTimeout = setTimeout(() => {

                if (!trackReceived) {

                    if (loaderEl) loaderEl.style.display = 'none';

                    if (errorEl) {

                        errorEl.innerHTML = 'Flux WebRTC indisponible.<br>Temps de connexion dépassé (' + camId + ').<br>Vérifiez que la caméra est active.';

                        errorEl.style.display = 'flex';

                    }

                    try { pc.close(); } catch(e) {}

                    if (calibPreviewPc === pc) calibPreviewPc = null;

                }

            }, 25000);

            pc.ontrack = (event) => {

                trackReceived = true;

                clearTimeout(trackTimeout);

                if (videoEl && event.streams && event.streams[0]) {

                    videoEl.srcObject = event.streams[0];

                    videoEl.play().catch(() => {});

                }

                if (loaderEl) loaderEl.style.display = 'none';

            };

            pc.oniceconnectionstatechange = () => {

                if (pc.iceConnectionState === 'failed' || pc.iceConnectionState === 'closed' || pc.iceConnectionState === 'disconnected') {

                    if (!trackReceived) {

                        if (loaderEl) loaderEl.style.display = 'none';

                        if (errorEl) {

                            errorEl.innerHTML = 'Connexion WebRTC échouée (' + camId + ').<br>ICE : ' + pc.iceConnectionState;

                            errorEl.style.display = 'flex';

                        }

                    }

                }

            };

            pc.createOffer().then(offer => pc.setLocalDescription(offer)).then(() => {

                // POST offer SDP to MediaMTX WHEP endpoint - retry up to 48x250ms in case camera isn't streaming yet

                const maxAttempts = 48;

                let attempt = 0;

                const postOffer = () => {

                    attempt++;

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

                        if (attempt < maxAttempts && !trackReceived) {

                            setTimeout(postOffer, 250);

                        } else if (!trackReceived) {

                            if (loaderEl) loaderEl.style.display = 'none';

                            if (errorEl) {

                                errorEl.innerHTML = 'WHEP échoué après ' + maxAttempts + ' tentatives (' + camId + ').<br>' + (err && err.message ? err.message : err);

                                errorEl.style.display = 'flex';

                            }

                            try { pc.close(); } catch(e) {}

                            if (calibPreviewPc === pc) calibPreviewPc = null;

                        }

                    });

                };

                postOffer();

            }).catch(err => {

                if (!trackReceived) {

                    if (loaderEl) loaderEl.style.display = 'none';

                    if (errorEl) {

                        errorEl.innerHTML = 'Erreur createOffer (' + camId + ').<br>' + (err && err.message ? err.message : err);

                        errorEl.style.display = 'flex';

                    }

                    try { pc.close(); } catch(e) {}

                    if (calibPreviewPc === pc) calibPreviewPc = null;

                }

            });

        }



        function closeCalibPreview() {

            const overlay = document.getElementById('cam-preview-overlay');

            const videoEl = document.getElementById('calib-preview-video');

            if (calibPreviewPc) {

                try { calibPreviewPc.close(); } catch(e) {}

                calibPreviewPc = null;

            }

            if (videoEl) {

                videoEl.pause();

                videoEl.src = '';

                videoEl.srcObject = null;

            }

            if (overlay) overlay.style.display = 'none';

        }

        window.mccCurrentCamId = 1;

        let mccPeerConnection = null;



        function openCameraCalibModal(camId) {

            window.mccCurrentCamId = camId;

            const ts = window.lastTelemetryState;
            const cam2Connected = ts && ts.sensors && ts.sensors.cam2_connected === true;
            if (!cam2Connected) {
                document.getElementById('mcc-modal-title').textContent = 'Calibration Caméra';
            } else {
                document.getElementById('mcc-modal-title').textContent = `Calibration Caméra ${camId === 1 ? 'Gauche (1)' : 'Droite (2)'}`;
            }

            

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

            const btnRun = document.getElementById('btn-mcc-run-calib');
            if (btnRun) {
                btnRun.disabled = false;
                btnRun.innerHTML = '<span>📷 Lancer la Caméra</span>';
                btnRun.onclick = () => runIndividualCameraCalib();
            }
            const statusText = document.getElementById('mcc-cam-status-text');
            if (statusText) {
                statusText.innerHTML = '<span>Cliquez sur Lancer pour vous connecter à la caméra.</span>';
            }
            const overlayEl = document.getElementById('mcc-cam-status-overlay');
            if (overlayEl) {
                overlayEl.style.display = 'flex';
                overlayEl.style.backgroundColor = 'rgba(9,9,11,0.85)';
            }
            const hudEl = document.getElementById('mcc-cam-hud');
            if (hudEl) {
                hudEl.style.display = 'none';
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
            btnRun.innerHTML = '<span>Demarrage calibration...</span>';
            const overlayEl = document.getElementById('mcc-cam-status-overlay');
            const statusText = document.getElementById('mcc-cam-status-text');
            const hudEl = document.getElementById('mcc-cam-hud');
            const videoEl = document.getElementById('mcc-cam-video');
            const camId = window.mccCurrentCamId;
            if (window.mccPeerConnection) {
                window.mccPeerConnection.close();
                window.mccPeerConnection = null;
            }
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: 'stop_camera', camera: camId }));
            }
            var _cols = parseInt((document.getElementById('mcc-cols')||{}).value) || 9;
            var _rows = parseInt((document.getElementById('mcc-rows')||{}).value) || 6;
            var _sqmm = parseInt((document.getElementById('mcc-square')||{}).value) || 25;
            if (overlayEl) { overlayEl.style.display = 'flex'; overlayEl.style.backgroundColor = 'rgba(9,9,11,0.95)'; }
            if (statusText) statusText.innerHTML = '<span style="font-size:1rem; color:var(--text-primary);">Arret du flux WebRTC...</span><br/><span style="font-size:0.75rem; color:var(--text-secondary);">0%</span>';
            if (hudEl) hudEl.style.display = 'none';
            if (videoEl) { videoEl.style.display = 'none'; videoEl.srcObject = null; }
            setTimeout(function() {
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    appWs.send(JSON.stringify({
                        type: 'run_mono_calib',
                        camera: camId,
                        chessboard_cols: _cols,
                        chessboard_rows: _rows,
                        square_size_mm: _sqmm,
                        timeout_seconds: 300
                    }));
                } else {
                    if (statusText) statusText.innerHTML = '<span style="color:var(--danger);">WebSocket deconnecte. Verifiez la connexion au robot.</span>';
                    btnRun.disabled = false;
                    btnRun.innerHTML = '<span>Reessayer</span>';
                    btnRun.onclick = function() { confirmIndividualCameraCalib(); };
                }
            }, 2000);
            window._monoCalibCamId = camId;
        }

// === camera preview + assign ===
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
            }, 12000);
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
            const modal = document.getElementById('cameraAssignModal');
            modal.classList.add('active');
            modal.removeAttribute('inert');
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
            if (modal) { modal.classList.remove('active'); modal.setAttribute('inert', ''); }
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

// === V-SLAM test ===
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
                
                const activeCam = slamInfo.cam1 ? 1 : (slamInfo.cam2 ? 2 : 1);
                window.vslamActiveCamera = activeCam;
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    appWs.send(JSON.stringify({ type: "request_camera", camera: activeCam, v_slam: true, ...getStreamQualityParams(activeCam) }));
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
                const activeCam = window.vslamActiveCamera || 1;
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    appWs.send(JSON.stringify({ type: "release_camera", camera: activeCam }));
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

                const activeCam = window.vslamActiveCamera || 1;
                const webrtcUrl = `${window.location.protocol}//${window.location.hostname}:48889/robot/cam${activeCam}/whep`;
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

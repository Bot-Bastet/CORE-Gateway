// === easyconfig_cameras.js - Configuration caméras WebRTC/HLS ===
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
                appWs.send(JSON.stringify({ type: "run_stereo_calib" }));
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
                progress += Math.random() * 15;
                if (progress > 90) progress = 90;
                if (progressBar) progressBar.style.width = progress + '%';
                if (progressText) progressText.textContent = 'Calibration stereo en cours... ' + Math.round(progress) + '%';
            }, 2000);
            
            // Listen for result
            var origOnMessage = appWs.onmessage;
            _ecStereoOrigOnMessage = origOnMessage;
            appWs.onmessage = function(event) {
                try {
                    var data = JSON.parse(event.data);
                    if (data.type === 'stereo_calib_result') {
                        clearInterval(progressInterval);
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

// === easyconfig joints + camera calib ===


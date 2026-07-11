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

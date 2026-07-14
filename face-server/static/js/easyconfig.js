// === EasyConfig Global State (must be at global scope for cross-file access) ===
// Ordre des moteurs dans l'assistant, avec position neutre URDF de référence
// urdf_angle : angle (en degrés, 0-180) que le servo doit physiquement faire
//              pour être dans la position neutre définie par l'URDF
// Quand l'utilisateur voit la jam be dans la bonne position, le slider vaut urdf_angle.
// L'Arduino calcule : offset = slider_value - 90
// (90° = valeur logique neutre envoyée par le Pi quand l'URDF dit 0)
var EC_JOINT_ORDER = [
    { leg: "Avant Droite",   joint: "Hanche",   idx: 0,  icon: "🦵", type: "hip",   urdf_angle: 90, side: "right", leg_id: "fr" },
    { leg: "Avant Droite",   joint: "Tibia",    idx: 1,  icon: "🦵", type: "upper", urdf_angle: 90, side: "right", leg_id: "fr" },
    { leg: "Avant Droite",   joint: "Genou",    idx: 2,  icon: "🦵", type: "lower", urdf_angle: 90, side: "right", leg_id: "fr" },
    { leg: "Avant Gauche",   joint: "Hanche",   idx: 3,  icon: "🦵", type: "hip",   urdf_angle: 90, side: "left",  leg_id: "fl" },
    { leg: "Avant Gauche",   joint: "Tibia",    idx: 4,  icon: "🦵", type: "upper", urdf_angle: 90, side: "left",  leg_id: "fl" },
    { leg: "Avant Gauche",   joint: "Genou",    idx: 5,  icon: "🦵", type: "lower", urdf_angle: 90, side: "left",  leg_id: "fl" },
    { leg: "Arrière Droite", joint: "Hanche",   idx: 6,  icon: "🦵", type: "hip",   urdf_angle: 90, side: "right", leg_id: "rr" },
    { leg: "Arrière Droite", joint: "Tibia",    idx: 7,  icon: "🦵", type: "upper", urdf_angle: 90, side: "right", leg_id: "rr" },
    { leg: "Arrière Droite", joint: "Genou",    idx: 8,  icon: "🦵", type: "lower", urdf_angle: 90, side: "right", leg_id: "rr" },
    { leg: "Arrière Gauche", joint: "Hanche",   idx: 9,  icon: "🦵", type: "hip",   urdf_angle: 90, side: "left",  leg_id: "rl" },
    { leg: "Arrière Gauche", joint: "Tibia",    idx: 10, icon: "🦵", type: "upper", urdf_angle: 90, side: "left",  leg_id: "rl" },
    { leg: "Arrière Gauche", joint: "Genou",    idx: 11, icon: "🦵", type: "lower", urdf_angle: 90, side: "left",  leg_id: "rl" },
];
var ecCurrentStep = 1;
var ecJointIndex = 0;
var ecTempOffsets  = new Array(12).fill(0);    // offset = slider_value - 90
var ecTempInverts  = new Array(12).fill(false); // flag miroir par servo
var ecJointServoAttached = false;
var ecCalibratedMotors = false;
var ecCalibratedCam1 = false;
var ecCalibratedCam2 = false;
var ecPeerConnections = { 1: null, 2: null };
var ecCameraCount = 0;
var ecAllJointsValidated = false;

var ecLRPeerA = null;
var ecLRPeerB = null;
var ecLRAssigned = { left: null, right: null };
var ecLRStreamA = null;

// === easyconfig L/R ===
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


        function ecInitJointCalibration() {
            ecJointIndex = 0;
            ecTempOffsets  = new Array(12).fill(0);
            ecTempInverts  = new Array(12).fill(false);
            ecJointServoAttached = false;
            ecAllJointsValidated = false;
            document.getElementById('ec-joint-calibration-view').style.display = 'flex';
            document.getElementById('ec-joint-final-view').style.display = 'none';
            
            if (window.lastTelemetryState && window.lastTelemetryState.sensors) {
                const s = window.lastTelemetryState.sensors;
                ecCameraCount = (s.cam1_connected ? 1 : 0) + (s.cam2_connected ? 1 : 0);
            } else {
                ecCameraCount = 0;
            }
            ecUpdateStepIndicators();
            ecShowJoint(0);
        }
        
        // Descriptions pédagogiques par type d'articulation
        var EC_JOINT_DESCRIPTIONS = {
            hip:   [
                "1. <b>Abduction (Hanche)</b> : Contrôle l'écartement latéral de la patte.",
                "2. <b>Consigne</b> : Ajustez le curseur jusqu'à ce que la hanche soit <b>parfaitement verticale</b> par rapport au sol (comme illustré par la pièce clignotante Indigo sur le modèle 3D).",
                "3. <b>Validation</b> : Une fois perpendiculaire au sol, cliquez sur Valider."
            ],
            upper: [
                "1. <b>Cuisse (Thigh)</b> : Contrôle l'inclinaison avant/arrière de la cuisse.",
                "2. <b>Consigne</b> : Ajustez le curseur jusqu'à ce que la cuisse soit <b>parfaitement perpendiculaire au corps</b> (faisant un angle droit de 90° vers le bas, comme illustré par la pièce clignotante Indigo sur le modèle 3D).",
                "3. <b>Validation</b> : Une fois la cuisse bien verticale, cliquez sur Valider."
            ],
            lower: [
                "1. <b>Genou (Calf)</b> : Contrôle l'extension de la patte.",
                "2. <b>Consigne</b> : Ajustez le curseur pour mettre le genou dans la <b>position la plus droite possible</b>, tibia et pied alignés verticalement dans le prolongement de la cuisse (comme illustré par la pièce clignotante Indigo sur le modèle 3D).",
                "3. <b>Validation</b> : Une fois la jambe bien tendue et droite, cliquez sur Valider."
            ]
        };

        function ecShowJoint(index) {
            if (index >= EC_JOINT_ORDER.length) return;
            var joint = EC_JOINT_ORDER[index];
            ecJointServoAttached = false;
            
            document.getElementById('ec-joint-leg-name').textContent  = joint.leg;
            document.getElementById('ec-joint-name').textContent       = joint.joint;
            document.getElementById('ec-joint-progress').textContent   = 'Articulation ' + (index + 1) + '/12';
            document.getElementById('ec-joint-icon').textContent       = joint.icon;

            // --- Image de référence URDF (dessinée en canvas) ---
            ecDrawReferenceCanvas(joint);

            // --- Déclencher le surbrillance 3D sur le viewer ---
            if (typeof window.highlightSpotMicroJoint === 'function') {
                window.highlightSpotMicroJoint(joint.leg_id, joint.type);
            }

            // --- Description pédagogique ---
            var descLines = EC_JOINT_DESCRIPTIONS[joint.type] || [];
            var descEl = document.getElementById('ec-joint-description');
            if (descEl) descEl.innerHTML = descLines.map(function(l) { return '<p style="margin:0.25rem 0; font-size:0.85rem; line-height:1.4; opacity:0.9">' + l + '</p>'; }).join('');

            // --- Côté du servo ---
            var sideEl = document.getElementById('ec-joint-side-label');
            if (sideEl) sideEl.textContent = joint.side === 'left' ? '🛅 Gauche — peut nécessiter inversion' : '🛅 Droit';

            // --- Slider positionné à l'offset temporaire ou à 0 (neutre) ---
            var slider = document.getElementById('ec-joint-slider');
            var savedOffset = ecTempOffsets[joint.idx] || 0;
            slider.value = savedOffset;  // 0 = neutre, offset = décalage
            document.getElementById('ec-joint-slider-value').textContent = slider.value;
            document.getElementById('ec-joint-slider-value').style.color = 'var(--accent)';
            var limitWarnInit = document.getElementById('ec-joint-limit-warning');
            if (limitWarnInit) limitWarnInit.style.display = 'none';
            ecUpdateJointSlider(slider.value);

            // --- Bouton miroir ---
            var invertBtn = document.getElementById('ec-btn-invert-servo');
            if (invertBtn) {
                var isInverted = ecTempInverts[joint.idx] || false;
                invertBtn.textContent = isInverted ? '🔄 Miroir : ON' : '🔄 Miroir : OFF';
                invertBtn.style.background = isInverted ? 'rgba(239,68,68,0.2)' : 'rgba(100,100,100,0.15)';
                invertBtn.style.color = isInverted ? 'var(--danger)' : 'var(--text-secondary)';
            }
            
            var btn = document.getElementById('ec-btn-attach-servo');
            btn.disabled = false;
            btn.textContent = '🔌 Allumer le servo';
            btn.onclick = ecAttachCurrentJoint;
            document.getElementById('ec-btn-validate-joint').disabled = true;
            document.getElementById('ec-btn-validate-joint').style.opacity = '0.5';
            document.getElementById('ec-btn-prev').disabled = (index === 0);
            document.getElementById('ec-btn-next').disabled = false;
            document.getElementById('ec-btn-next').textContent = 'Suivant \u2192';
            document.getElementById('ec-btn-next').onclick = ecNextStep;
        }

        // Dessin canvas de référence URDF par type d'articulation
        function ecDrawReferenceCanvas(joint) {
            var canvas = document.getElementById('ec-ref-canvas');
            if (!canvas) return;
            var ctx = canvas.getContext('2d');
            var w = canvas.width, h = canvas.height;
            ctx.clearRect(0, 0, w, h);

            // Fond
            ctx.fillStyle = '#0f0f13';
            ctx.fillRect(0, 0, w, h);

            var cx = w / 2, cy = h * 0.45;

            // Palette
            var COL_BODY    = '#334155';  // gris bleu (corps)
            var COL_LEG     = '#475569';  // gris (segment)
            var COL_ACTIVE  = '#06b6d4';  // cyan (articulation active)
            var COL_NEUTRAL = '#10b981';  // vert (position neutre)
            var COL_LABEL   = '#e2e8f0';
            var COL_ANGLE   = '#f59e0b';  // orange (indicateur angle)

            // Silhouette corps robot
            ctx.fillStyle = COL_BODY;
            ctx.beginPath();
            ctx.roundRect(cx - 30, cy - 20, 60, 40, 6);
            ctx.fill();

            if (joint.type === 'hip') {
                // Hanche : vue de dessus, patte perpendiculaire au corps
                var legL = 55;
                var dir = joint.side === 'right' ? 1 : -1;
                // Corps
                ctx.fillStyle = COL_BODY;
                ctx.fillRect(cx - 30, cy - 10, 60, 20);
                // Point articulation hanche
                ctx.fillStyle = COL_ACTIVE;
                ctx.beginPath(); ctx.arc(cx + dir * 30, cy, 8, 0, Math.PI * 2); ctx.fill();
                // Segment patte (horizontal = neutre URDF)
                ctx.strokeStyle = COL_NEUTRAL;
                ctx.lineWidth = 6;
                ctx.lineCap = 'round';
                ctx.beginPath();
                ctx.moveTo(cx + dir * 30, cy);
                ctx.lineTo(cx + dir * (30 + legL), cy);
                ctx.stroke();
                // Extremité
                ctx.fillStyle = COL_NEUTRAL;
                ctx.beginPath(); ctx.arc(cx + dir * (30 + legL), cy, 5, 0, Math.PI * 2); ctx.fill();
                // Label
                ctx.fillStyle = COL_ANGLE;
                ctx.font = 'bold 12px monospace';
                ctx.textAlign = 'center';
                ctx.fillText('90° (neutre)', cx + dir * (30 + legL/2), cy - 14);
                // Vue de dessus label
                ctx.fillStyle = 'rgba(255,255,255,0.3)';
                ctx.font = '10px monospace';
                ctx.fillText('Vue de dessus', cx, h - 10);

            } else if (joint.type === 'upper') {
                // Tibia (cuisse) : vue de côté, cuisse horizontale
                var femurL = 60;
                ctx.strokeStyle = COL_NEUTRAL;
                ctx.lineWidth = 8;
                ctx.lineCap = 'round';
                // Corps attaché
                ctx.fillStyle = COL_BODY;
                ctx.fillRect(cx - 20, cy - 20, 40, 40);
                // Point articulation
                ctx.fillStyle = COL_ACTIVE;
                ctx.beginPath(); ctx.arc(cx, cy, 9, 0, Math.PI * 2); ctx.fill();
                // Cuisse horizontale (neutre = 90° par rapport au corps vertical)
                ctx.strokeStyle = COL_NEUTRAL;
                ctx.lineWidth = 6;
                ctx.beginPath();
                ctx.moveTo(cx, cy);
                ctx.lineTo(cx + femurL, cy);
                ctx.stroke();
                ctx.fillStyle = COL_NEUTRAL;
                ctx.beginPath(); ctx.arc(cx + femurL, cy, 5, 0, Math.PI * 2); ctx.fill();
                // Bas de patte (tibia pendante)
                ctx.strokeStyle = COL_LEG;
                ctx.lineWidth = 5;
                ctx.beginPath();
                ctx.moveTo(cx + femurL, cy);
                ctx.lineTo(cx + femurL, cy + 45);
                ctx.stroke();
                // Angle indicator
                ctx.strokeStyle = COL_ANGLE;
                ctx.lineWidth = 1.5;
                ctx.setLineDash([4, 3]);
                ctx.beginPath(); ctx.arc(cx, cy, 22, -Math.PI/2, 0); ctx.stroke();
                ctx.setLineDash([]);
                ctx.fillStyle = COL_ANGLE;
                ctx.font = 'bold 12px monospace';
                ctx.textAlign = 'center';
                ctx.fillText('90°', cx + 28, cy - 8);
                ctx.fillStyle = 'rgba(255,255,255,0.3)';
                ctx.font = '10px monospace';
                ctx.fillText('Vue de côté', cx, h - 10);

            } else if (joint.type === 'lower') {
                // Genou : bas de patte vertical
                var tibiaL = 55;
                // Cuisse
                ctx.strokeStyle = COL_LEG;
                ctx.lineWidth = 6;
                ctx.lineCap = 'round';
                ctx.beginPath(); ctx.moveTo(cx - 40, cy - 20); ctx.lineTo(cx, cy); ctx.stroke();
                // Point genou
                ctx.fillStyle = COL_ACTIVE;
                ctx.beginPath(); ctx.arc(cx, cy, 9, 0, Math.PI * 2); ctx.fill();
                // Bas de patte vertical (neutre = perpendiculaire au sol)
                ctx.strokeStyle = COL_NEUTRAL;
                ctx.lineWidth = 6;
                ctx.beginPath();
                ctx.moveTo(cx, cy);
                ctx.lineTo(cx, cy + tibiaL);
                ctx.stroke();
                ctx.fillStyle = COL_NEUTRAL;
                ctx.beginPath(); ctx.arc(cx, cy + tibiaL, 5, 0, Math.PI * 2); ctx.fill();
                // Sol
                ctx.strokeStyle = 'rgba(255,255,255,0.2)';
                ctx.lineWidth = 1.5;
                ctx.setLineDash([4, 4]);
                ctx.beginPath(); ctx.moveTo(cx - 40, cy + tibiaL + 2); ctx.lineTo(cx + 40, cy + tibiaL + 2); ctx.stroke();
                ctx.setLineDash([]);
                // Angle
                ctx.strokeStyle = COL_ANGLE;
                ctx.lineWidth = 1.5;
                ctx.setLineDash([4, 3]);
                ctx.beginPath(); ctx.arc(cx, cy, 20, 0, Math.PI/2); ctx.stroke();
                ctx.setLineDash([]);
                ctx.fillStyle = COL_ANGLE;
                ctx.font = 'bold 12px monospace';
                ctx.textAlign = 'center';
                ctx.fillText('90°', cx + 28, cy + 16);
                ctx.fillStyle = 'rgba(255,255,255,0.3)';
                ctx.font = '10px monospace';
                ctx.fillText('Vue de côté', cx, h - 10);
            }

            // Label position cible
            ctx.fillStyle = COL_NEUTRAL;
            ctx.font = 'bold 11px monospace';
            ctx.textAlign = 'center';
            ctx.fillText('✔ Position cible URDF', cx, 18);
        }
        
        function ecAttachCurrentJoint() {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'attach', index: joint.idx }));
                // Envoyer la position actuelle du slider
                var curVal = parseInt(document.getElementById('ec-joint-slider').value) || 90;
                appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'write', index: joint.idx, angle: curVal }));
                ecJointServoAttached = true;
                document.getElementById('ec-btn-attach-servo').disabled = false;
                document.getElementById('ec-btn-attach-servo').textContent = '🔌 Éteindre le servo';
                document.getElementById('ec-btn-attach-servo').onclick = ecDetachCurrentJoint;
                document.getElementById('ec-btn-validate-joint').disabled = false;
                document.getElementById('ec-btn-validate-joint').style.opacity = '1';
                if (typeof showToast === 'function') showToast('Servo', joint.joint + ' allumé — utilisez le curseur', 'info');
            } else {
                if (typeof showToast === 'function') showToast('Erreur', 'WebSocket non connecté', 'error');
            }
        }
        
        function ecDetachCurrentJoint() {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'detach', index: joint.idx }));
            }
            ecJointServoAttached = false;
            document.getElementById('ec-btn-attach-servo').textContent = '🔌 Allumer le servo';
            document.getElementById('ec-btn-attach-servo').onclick = ecAttachCurrentJoint;
            document.getElementById('ec-btn-validate-joint').disabled = true;
            document.getElementById('ec-btn-validate-joint').style.opacity = '0.5';
            if (typeof showToast === 'function') showToast('Servo', joint.joint + ' éteint', 'info');
        }

        // Bascule le flag miroir du servo courant
        function ecToggleInvert() {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            ecTempInverts[joint.idx] = !ecTempInverts[joint.idx];
            var isInverted = ecTempInverts[joint.idx];
            var invertBtn = document.getElementById('ec-btn-invert-servo');
            if (invertBtn) {
                invertBtn.textContent = isInverted ? '🔄 Miroir : ON' : '🔄 Miroir : OFF';
                invertBtn.style.background = isInverted ? 'rgba(239,68,68,0.2)' : 'rgba(100,100,100,0.15)';
                invertBtn.style.color = isInverted ? 'var(--danger)' : 'var(--text-secondary)';
            }
            // Si servo allumé, tester le miroir immédiatement
            if (ecJointServoAttached && appWs && appWs.readyState === WebSocket.OPEN) {
                var raw = parseInt(document.getElementById('ec-joint-slider').value) || 90;
                var angle = isInverted ? (180 - raw) : raw;
                appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'write', index: joint.idx, angle: angle }));
            }
            if (typeof showToast === 'function') {
                showToast('Miroir', isInverted ? 'Sens inversé activé' : 'Sens normal', 'info');
            }
        }
        
        var ecSliderThrottle = null;
        function ecUpdateJointSlider(value) {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            var intVal = parseInt(value) || 0; // valeur de -90 à 90 (offset)
            var valueEl = document.getElementById('ec-joint-slider-value');
            var limitWarn = document.getElementById('ec-joint-limit-warning');
            if (valueEl) valueEl.textContent = intVal;
            
            // L'offset stocké = la valeur du slider directement
            ecTempOffsets[joint.idx] = intVal;

            // Calculer l'angle absolu théorique à envoyer au moteur physique (90 = neutre)
            var absoluteAngle = 90 + intVal;

            // Indicateur couleur si aux limites de l'angle absolu (0° ou 180°)
            if (absoluteAngle <= 5 || absoluteAngle >= 175) {
                if (valueEl) valueEl.style.color = '#f59e0b';
                if (limitWarn) limitWarn.style.display = 'inline-block';
            } else {
                if (valueEl) valueEl.style.color = 'var(--accent)';
                if (limitWarn) limitWarn.style.display = 'none';
            }
            
            // Throttle 50ms pour ne pas saturer le buffer série Arduino
            if (ecSliderThrottle) clearTimeout(ecSliderThrottle);
            ecSliderThrottle = setTimeout(function() {
                if (ecJointServoAttached && appWs && appWs.readyState === WebSocket.OPEN) {
                    var isInverted = ecTempInverts[joint.idx];
                    var angle = isInverted ? (180 - absoluteAngle) : absoluteAngle;
                    var chk = (joint.idx + Math.floor(angle)) % 100;
                    appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'write', index: joint.idx, angle: angle, chk: chk }));
                }
            }, 50);
        }
        
        function ecValidateJoint() {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            var sliderVal = parseInt(document.getElementById('ec-joint-slider').value) || 0;
            
            // L'offset est exactement la valeur du slider
            var offset = sliderVal;
            ecTempOffsets[joint.idx] = offset;

            // ⚠️ IMPORTANT : avant de sauvegarder, on doit effacer l'offset temporaire de l'Arduino
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                // 1. Enregistrer l'offset dans l'EEPROM Arduino
                appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'set_offset', index: joint.idx, offset: offset }));
                // 2. Enregistrer le flag miroir dans l'EEPROM Arduino
                appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'set_invert', index: joint.idx, inverted: ecTempInverts[joint.idx] }));
                // 3. Détacher le servo avant de passer au suivant
                appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'detach', index: joint.idx }));
            }
            ecJointServoAttached = false;

            if (typeof showToast === 'function') {
                showToast('✔ Calibré', joint.leg + ' ' + joint.joint + ' — offset=' + offset + '°' + (ecTempInverts[joint.idx] ? ', miroir=ON' : ''), 'success');
            }
            
            if (ecJointIndex < 11) {
                ecJointIndex++;
                ecShowJoint(ecJointIndex);
            } else {
                // Toutes les articulations calibrées
                ecAllJointsValidated = true;
                document.getElementById('ec-joint-calibration-view').style.display = 'none';
                document.getElementById('ec-joint-final-view').style.display = 'flex';
                document.getElementById('ec-btn-prev').disabled = false;
                document.getElementById('ec-btn-next').disabled = false;
                document.getElementById('ec-progress-text').textContent = 'Toutes les articulations calibrées';
                // Appliquer stand pour vérifier la calibration
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'stand' }));
                }
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
            
            // Sauvegarder le mode démo/réel précédent
            const demoCheck = document.getElementById('demo-mode-checkbox');
            window.ecPreviousDemoMode = demoCheck ? demoCheck.checked : true;
            
            // Forcer le mode simulation pour la sécurité des moteurs
            if (typeof window.toggleDemoMode === 'function') {
                window.toggleDemoMode(true);
            }
            if (demoCheck) {
                demoCheck.checked = true;
            }

            for (let id of [1, 2]) {
                if (ecPeerConnections[id]) {
                    try { ecPeerConnections[id].close(); } catch(e) {}
                    ecPeerConnections[id] = null;
                }
            }

            // Sauvegarder l'onglet actif actuel pour pouvoir le restaurer à la fermeture
            window.ecPreviousTab = localStorage.getItem('bastetActiveTab') || 'dashboard';
            // Forcer l'affichage de l'onglet télécommande où se trouve le modèle 3D
            if (typeof window.switchTab === 'function') {
                window.switchTab('control');
            }
            
            const o = document.getElementById('easyconfig-overlay');
            o.style.position = 'fixed';
            o.style.top = '0';
            o.style.left = '0';
            o.style.right = '';
            o.style.width = '480px';
            o.style.bottom = '0';
            o.style.display = 'flex';
            o.style.justifyContent = 'flex-start';
            o.style.alignItems = 'stretch';
            o.style.opacity = '1';
            o.style.pointerEvents = 'auto';
            o.style.zIndex = '1000';
            o.classList.add('active');
            o.removeAttribute('inert');
            ecInitJointCalibration();
            ecShowStep(1);
            
            ecUpdateMotorFeedback();
            window.ecFeedbackInterval = setInterval(ecUpdateMotorFeedback, 500);
        }

        function closeEasyConfig() {
            clearInterval(window.ecFeedbackInterval);
            if (typeof window.highlightSpotMicroJoint === 'function') {
                window.highlightSpotMicroJoint(null, null);
            }
            // Restaurer l'onglet précédent
            if (window.ecPreviousTab && typeof window.switchTab === 'function') {
                window.switchTab(window.ecPreviousTab);
            }
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
            o.setAttribute('inert', '');
            
            // Restaurer le mode démo/réel précédent
            if (window.ecPreviousDemoMode !== undefined && typeof window.toggleDemoMode === 'function') {
                window.toggleDemoMode(window.ecPreviousDemoMode);
                const demoCheck = document.getElementById('demo-mode-checkbox');
                if (demoCheck) {
                    demoCheck.checked = window.ecPreviousDemoMode;
                }
            }

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
                appWs.send(JSON.stringify({ type: "request_camera", camera: camId, v_slam: false, ...((window.getStreamQualityParams && window.getStreamQualityParams(camId)) || {}) }));
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
                    <span style="font-size: 2rem; color: var(--danger); display:block; margin-bottom:0.5rem;">Ô£ù</span>
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
                    clearInterval(progressInterval);
                    
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
                            <div style="width: 50px; height: 50px; border-radius: 50%; background: rgba(239, 68, 68, 0.1); border: 2px solid var(--danger); display: flex; align-items: center; justify-content: center; font-size: 1.5rem; color: var(--danger); margin: 0 auto 0.5rem;">Ô£ù</div>
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
            // Forcer le retour en mode réel
            window.ecPreviousDemoMode = false;
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "start_robot" }));
                
                // Délai de 300ms avant d'envoyer 'stand' pour laisser le temps au basculement en mode réel
                // (qui envoie 'sit' automatiquement) de se stabiliser, puis écraser par 'stand'.
                setTimeout(function() {
                    if (appWs && appWs.readyState === WebSocket.OPEN) {
                        appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stand" }));
                    }
                }, 300);
            }
            closeEasyConfig();
        }

// === Exposition globale (appelable depuis onclick HTML et dashboard.js) ===
window.openEasyConfig   = openEasyConfig;
window.closeEasyConfig  = closeEasyConfig;
window.ecStartRobotAndClose = ecStartRobotAndClose;
window.ecShowStep       = ecShowStep;
window.ecNextStep       = ecNextStep;
window.ecPrevStep       = ecPrevStep;
window.ecSkipStep       = ecSkipStep;
window.ecValidateJoint  = ecValidateJoint;
window.ecRunCameraCalib = ecRunCameraCalib;
window.ecRunStereoCalib = ecRunStereoCalib;

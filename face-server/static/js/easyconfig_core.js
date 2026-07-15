// === easyconfig_core.js - Variables globales, navigation, open/close ===
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
var ecTempMinLimits = new Array(12).fill(0);    // safety min limit par servo
var ecTempMaxLimits = new Array(12).fill(180);  // safety max limit par servo
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
        

// === Navigation et cycle de vie EasyConfig ===
        function openEasyConfig() {
            ecCurrentStep = 1;
            ecCalibratedMotors = false;
            ecCalibratedCam1 = false;
            ecCalibratedCam2 = false;
            ecJointIndex = 0;
            // Ne pas réinitialiser ecTempOffsets ici — ecInitJointCalibration() les charge depuis la Gateway
            ecJointServoAttached = false;
            
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
            // 🔴 SECURITE: desactiver les moteurs quand on quitte EasyConfig
            // Ne PAS clear la calibration si elle a ete terminee avec succes
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                if (ecJointServoAttached) {
                    const currentJoint = EC_JOINT_ORDER[ecJointIndex] || EC_JOINT_ORDER[0];
                    appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "detach", index: currentJoint.idx }));
                }
                // N'effacer l'EEPROM que si une écriture partielle a réellement eu lieu
                // pendant cette session (abandon en cours de parcours). Ouvrir puis
                // fermer EasyConfig sans rien toucher ne doit PAS détruire une
                // calibration existante valide.
                if (!ecAllJointsValidated && !window._ecNormalClose && window._ecSessionDirty) {
                    appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "clear_servo_calib" }));
                }
                window._ecSessionDirty = false;
                if (!window._ecNormalClose) {
                    appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stop" }));
                }
                window._ecNormalClose = false;
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
            const telemetry = window.lastTelemetryState || {};
            const j = (telemetry.servo_angles && telemetry.servo_angles.length === 12)
                ? telemetry.servo_angles
                : (telemetry.joints && telemetry.joints.length === 12 ? telemetry.joints : null);
            if (j) {
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
                ecNextStep(2);
                return;
            }
            // Skip current step without doing calibration work
            ecNextStep();
        }

        function ecPrevStep() {
            // Joint-level navigation during step 1 (joint calibration wizard)
            if (ecCurrentStep === 1) {
                if (typeof ecJointPhase !== 'undefined' && ecJointPhase > 1) {
                    // Reculer d'une sous-étape (C→B ou B→A) pour la même articulation
                    ecJointPhase--;
                    ecShowJoint(ecJointIndex);
                    return;
                } else if (ecJointIndex > 0) {
                    // Revenir à l'étape C de l'articulation précédente
                    const currentJoint = EC_JOINT_ORDER[ecJointIndex];
                    if (ecJointServoAttached && appWs && appWs.readyState === WebSocket.OPEN) {
                        appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "detach", index: currentJoint.idx }));
                    }
                    ecJointServoAttached = false;
                    ecJointIndex--;
                    ecJointPhase = 3;
                    ecShowJoint(ecJointIndex);
                    return;
                }
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
            // Joint-level forward navigation during step 1 (joint calibration wizard)
            if (ecCurrentStep === 1 && !ecAllJointsValidated && targetStep === null) {
                if (typeof ecValidateJoint === 'function') {
                    ecValidateJoint();
                    return;
                }
            }
            
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
            const offsets = ecTempOffsets;
            const limits = [];
            const inverts = ecTempInverts;
            for (let i = 0; i < 12; i++) {
                limits.push([
                    ecTempMinLimits[i] !== undefined ? ecTempMinLimits[i] : 0,
                    ecTempMaxLimits[i] !== undefined ? ecTempMaxLimits[i] : 180
                ]);
            }
            
            fetch('/core/calibration', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Token': window.apiToken || localStorage.getItem('bastet_api_token') || ''
                },
                body: JSON.stringify({ offsets: offsets, limits: limits, inverts: inverts })
            }).then(res => {
                if (res.ok) {
                    alert("Offsets et limites sauvegardes avec succes.");
                    if (typeof loadSavedOffsets === 'function') loadSavedOffsets();
                    ecCalibratedMotors = true;
                    document.getElementById('ec-motor-success-anim').style.display = 'block';
                    document.getElementById('ec-btn-next').disabled = false;
                    
                    // Auto advance to next step after a short delay
                    setTimeout(function() {
                        ecNextStep();
                    }, 500);
                } else {
                    alert("Erreur sauvegarde configuration (code " + res.status + "). Verifiez le token API.");
                }
            }).catch(err => {
                console.error(err);
                alert("Erreur reseau lors de la sauvegarde de la calibration.");
            });
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "motor_calibration", offsets: offsets, limits: limits, inverts: inverts }));
                if (activateMotors) {
                    appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stand" }));
                }
            }
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
                    <span style="font-size: 2rem; color: var(--danger); display:block; margin-bottom:0.5rem;">✗</span>
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
                            <div style="width: 50px; height: 50px; border-radius: 50%; background: rgba(239, 68, 68, 0.1); border: 2px solid var(--danger); display: flex; align-items: center; justify-content: center; font-size: 1.5rem; color: var(--danger); margin: 0 auto 0.5rem;">✗</div>
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
            window._ecNormalClose = true;
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "start_robot" }));
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stand" }));
            }
            closeEasyConfig();
        }
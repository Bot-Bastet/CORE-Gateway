// === calibration offsets ===
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

        function openCalibrationOverlay() {
            const overlay = document.getElementById('calibration-overlay');
            overlay.classList.add('active');
            overlay.removeAttribute('inert');
            loadSavedOffsets();
        }

        function closeCalibrationOverlay() {
            const overlay = document.getElementById('calibration-overlay');
            overlay.classList.remove('active');
            overlay.setAttribute('inert', '');
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

// === servo tester ===
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
            // FIX: Redemarrer le motion_node en mode stand apres le test individuel
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stand" }));
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
                        <label for="tester-slider-${i}" class="sr-only">Angle du servo ${i+1}</label> <input type="range" min="0" max="180" value="90" id="tester-slider-${i}" style="flex: 1; height: 4px; accent-color: var(--accent);" oninput="testerWrite(${i}, this.value)">
                        <span id="tester-val-${i}" style="font-size: 0.8rem; font-family: monospace; min-width: 30px; text-align: right; color: var(--accent);">90°</span>
                    </div>
                `;
                container.appendChild(card);
            }
        }

        function testerAttach(idx) {
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "attach", index: idx }));
                
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

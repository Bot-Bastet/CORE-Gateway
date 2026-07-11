// === easyconfig_motors.js - Calibration articulations moteurs ===
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
                    appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'write', index: joint.idx, angle: angle }));
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

// === easyconfig_motors.js - Calibration articulations moteurs ===
        var ecJointPhase = 1;
        var ecPhase1Offset = 0;
        var ecJointModified = false;

        async function ecInitJointCalibration() {
            ecJointIndex = 0;
            ecJointPhase = 1;
            ecPhase1Offset = 0;
            ecTempOffsets  = new Array(12).fill(0);
            ecTempInverts  = new Array(12).fill(false);
            ecTempMinLimits = new Array(12).fill(0);
            ecTempMaxLimits = new Array(12).fill(180);
            
            // Pré-charger les calibrations existantes depuis la Gateway pour ne pas les écraser
            try {
                const res = await fetch('/core/calibration', {
                    headers: { 'X-API-Token': window.apiToken || localStorage.getItem('bastet_api_token') || '' }
                });
                if (res.ok) {
                    const data = await res.json();
                    if (data.offsets && data.offsets.length === 12) {
                        ecTempOffsets = [...data.offsets];
                        console.log("[EasyConfig] Offsets pré-chargés :", ecTempOffsets);
                    }
                    if (data.inverts && data.inverts.length === 12) {
                        ecTempInverts = [...data.inverts];
                        console.log("[EasyConfig] Inversions pré-chargées :", ecTempInverts);
                    }
                    if (data.limits && data.limits.length === 12) {
                        for (let i = 0; i < 12; i++) {
                            ecTempMinLimits[i] = data.limits[i][0] !== undefined ? data.limits[i][0] : 0;
                            ecTempMaxLimits[i] = data.limits[i][1] !== undefined ? data.limits[i][1] : 180;
                        }
                        console.log("[EasyConfig] Limites pré-chargées.");
                    }
                }
            } catch (err) {
                console.error("[EasyConfig] Erreur pré-chargement calibration :", err);
            }
            
            ecJointServoAttached = false;
            ecJointModified = false;
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

        function ecShowJoint(index) {
            if (index >= EC_JOINT_ORDER.length) return;
            var joint = EC_JOINT_ORDER[index];
            
            if (ecJointPhase === 1) {
                ecJointModified = false;
            }
            
            document.getElementById('ec-joint-leg-name').textContent  = joint.leg;
            document.getElementById('ec-joint-name').textContent       = joint.joint;
            
            var progressText = 'Articulation ' + (index + 1) + '/12';
            if (ecJointPhase === 1) {
                progressText += ' - Étape A : Régler le Zéro';
            } else {
                progressText += ' - Étape B : Détecter le Sens';
            }
            document.getElementById('ec-joint-progress').textContent   = progressText;
            document.getElementById('ec-joint-icon').textContent       = joint.icon;

            // Masquer le bouton miroir manuel
            var invertBtn = document.getElementById('ec-btn-invert-servo');
            if (invertBtn) invertBtn.style.display = 'none';

            // --- Image de référence URDF ---
            ecDrawReferenceCanvas(joint);

            // --- Déclencher la surbrillance 3D ---
            if (typeof window.highlightSpotMicroJoint === 'function') {
                window.highlightSpotMicroJoint(joint.leg_id, joint.type, ecJointPhase);
            }

            // --- Description pédagogique selon la phase ---
            var descEl = document.getElementById('ec-joint-description');
            if (descEl) {
                var html = '';
                if (ecJointPhase === 1) {
                    html += '<p style="margin:0.25rem 0; font-size:0.85rem; line-height:1.4; opacity:0.9"><b>1. Étape A : Réglage du Zéro (Offset)</b></p>';
                    if (joint.type === 'hip') {
                        html += '<p style="margin:0.25rem 0; font-size:0.85rem; line-height:1.4; opacity:0.9">Ajustez le curseur jusqu\'à ce que la hanche soit <b>parfaitement verticale</b> par rapport au sol (comme le montre la ligne verte).</p>';
                    } else if (joint.type === 'upper') {
                        html += '<p style="margin:0.25rem 0; font-size:0.85rem; line-height:1.4; opacity:0.9">Ajustez le curseur jusqu\'à ce que la cuisse soit <b>horizontale / perpendiculaire au corps</b> (comme le montre la ligne verte).</p>';
                    } else {
                        html += '<p style="margin:0.25rem 0; font-size:0.85rem; line-height:1.4; opacity:0.9">Ajustez le curseur pour que le genou et la patte soient alignés de façon <b>la plus droite possible</b> (comme le montre la ligne verte).</p>';
                    }
                    html += '<p style="margin:0.25rem 0; font-size:0.85rem; line-height:1.4; opacity:0.9; color:var(--success)"><b>Une fois droit, cliquez sur "Valider le Zéro".</b></p>';
                } else {
                    html += '<p style="margin:0.25rem 0; font-size:0.85rem; line-height:1.4; opacity:0.9"><b>2. Étape B : Détection du Sens (Miroir)</b></p>';
                    if (joint.type === 'hip') {
                        html += '<p style="margin:0.25rem 0; font-size:0.85rem; line-height:1.4; opacity:0.9">Déplacez le curseur pour <b>écarter la patte vers l\'extérieur</b> du robot (comme l\'indique la position orange).</p>';
                    } else if (joint.type === 'upper') {
                        html += '<p style="margin:0.25rem 0; font-size:0.85rem; line-height:1.4; opacity:0.9">Déplacez le curseur pour <b>incliner la cuisse vers l\'avant</b> (comme l\'indique la position orange).</p>';
                    } else {
                        html += '<p style="margin:0.25rem 0; font-size:0.85rem; line-height:1.4; opacity:0.9">Déplacez le curseur pour <b>plier le genou vers l\'intérieur</b> (comme l\'indique la position orange).</p>';
                    }
                    html += '<p style="margin:0.25rem 0; font-size:0.85rem; line-height:1.4; opacity:0.9; color:var(--accent)"><b>Une fois plié/incliné physiquement, cliquez sur "Valider le Sens".</b></p>';
                }
                descEl.innerHTML = html;
            }

            // --- Côté du servo ---
            var sideEl = document.getElementById('ec-joint-side-label');
            if (sideEl) sideEl.textContent = joint.side === 'left' ? '🛅 Côté Gauche' : '🛅 Côté Droit';

            // Hiding limits block in Phase 2
            var limitsContainer = document.getElementById('ec-joint-limits-container');
            var unitLabel = document.getElementById('ec-joint-unit-label');
            if (ecJointPhase === 1) {
                if (limitsContainer) limitsContainer.style.display = 'flex';
                if (unitLabel) unitLabel.textContent = '° offset';
            } else {
                if (limitsContainer) limitsContainer.style.display = 'none';
                if (unitLabel) unitLabel.textContent = '° flexion';
            }

            // --- Slider positionné ---
            var slider = document.getElementById('ec-joint-slider');
            if (ecJointPhase === 1) {
                var savedOffset = ecTempOffsets[joint.idx] || 0;
                slider.value = savedOffset;
            }
            document.getElementById('ec-joint-slider-value').textContent = slider.value;
            document.getElementById('ec-joint-slider-value').style.color = 'var(--accent)';
            var limitWarnInit = document.getElementById('ec-joint-limit-warning');
            if (limitWarnInit) limitWarnInit.style.display = 'none';
            ecUpdateJointSlider(slider.value);

            // --- Bouton Allumer/Éteindre ---
            var btnAttach = document.getElementById('ec-btn-attach-servo');
            var btnValidate = document.getElementById('ec-btn-validate-joint');
            
            var savedOffset = ecTempOffsets[joint.idx] || 0;
            var savedInvert = ecTempInverts[joint.idx] || false;

            if (ecJointServoAttached) {
                btnAttach.textContent = '🔌 Éteindre le servo';
                btnAttach.onclick = ecDetachCurrentJoint;
                btnValidate.disabled = false;
                btnValidate.style.opacity = '1';
                btnValidate.style.background = 'var(--primary-color)';
                
                if (ecJointPhase === 1) {
                    btnValidate.textContent = 'Valider le Zéro';
                } else {
                    btnValidate.textContent = 'Valider le Sens';
                }
            } else {
                btnAttach.textContent = '🔌 Allumer le servo';
                btnAttach.onclick = ecAttachCurrentJoint;
                
                // Permettre de valider l'étape en conservant la valeur existante
                btnValidate.disabled = false;
                btnValidate.style.opacity = '0.85';
                btnValidate.style.background = 'rgba(255,255,255,0.05)';
                btnValidate.style.border = '1px solid var(--border-color)';
                
                if (ecJointPhase === 1) {
                    btnValidate.textContent = `Conserver Zéro (${savedOffset > 0 ? '+' : ''}${savedOffset}°) & Suivant`;
                } else {
                    btnValidate.textContent = `Conserver Sens (${savedInvert ? 'Miroir' : 'Normal'}) & Suivant`;
                }
            }
            btnAttach.disabled = false;
            btnValidate.onclick = ecValidateJoint;

            document.getElementById('ec-btn-prev').disabled = (index === 0 && ecJointPhase === 1);
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
                // Hanche : vue de dessus
                var legL = 55;
                var dir = joint.side === 'right' ? 1 : -1;
                // Corps
                ctx.fillStyle = COL_BODY;
                ctx.fillRect(cx - 30, cy - 10, 60, 20);
                // Point articulation
                ctx.fillStyle = COL_ACTIVE;
                ctx.beginPath(); ctx.arc(cx + dir * 30, cy, 8, 0, Math.PI * 2); ctx.fill();
                
                // Segment patte (neutre vs plié)
                var angleRad = 0; // 0 = horizontal
                if (ecJointPhase === 2) {
                    angleRad = -Math.PI / 6; // écarte de 30°
                }
                
                ctx.strokeStyle = ecJointPhase === 1 ? COL_NEUTRAL : COL_ANGLE;
                ctx.lineWidth = 6;
                ctx.lineCap = 'round';
                ctx.beginPath();
                ctx.moveTo(cx + dir * 30, cy);
                var targetX = cx + dir * 30 + Math.cos(angleRad) * dir * legL;
                var targetY = cy + Math.sin(angleRad) * dir * legL;
                ctx.lineTo(targetX, targetY);
                ctx.stroke();
                
                ctx.fillStyle = ecJointPhase === 1 ? COL_NEUTRAL : COL_ANGLE;
                ctx.beginPath(); ctx.arc(targetX, targetY, 5, 0, Math.PI * 2); ctx.fill();
                
                ctx.fillStyle = COL_LABEL;
                ctx.font = 'bold 11px monospace';
                ctx.textAlign = 'center';
                if (ecJointPhase === 1) {
                    ctx.fillText('90° (neutre)', cx + dir * (30 + legL/2), cy - 14);
                } else {
                    ctx.fillText('Écarté (+30°)', cx + dir * (30 + legL/2), cy - 14);
                }
                
                ctx.fillStyle = 'rgba(255,255,255,0.3)';
                ctx.font = '10px monospace';
                ctx.fillText('Vue de dessus', cx, h - 10);

            } else if (joint.type === 'upper') {
                // Cuisse : vue de côté
                var femurL = 60;
                ctx.fillStyle = COL_BODY;
                ctx.fillRect(cx - 20, cy - 20, 40, 40);
                // Point articulation
                ctx.fillStyle = COL_ACTIVE;
                ctx.beginPath(); ctx.arc(cx, cy, 9, 0, Math.PI * 2); ctx.fill();
                
                var angleRad = 0; // 0 = horizontal
                if (ecJointPhase === 2) {
                    angleRad = Math.PI / 4; // pivoté vers le bas/avant (45°)
                }
                
                ctx.strokeStyle = ecJointPhase === 1 ? COL_NEUTRAL : COL_ANGLE;
                ctx.lineWidth = 6;
                ctx.lineCap = 'round';
                ctx.beginPath();
                ctx.moveTo(cx, cy);
                var targetX = cx + Math.cos(angleRad) * femurL;
                var targetY = cy + Math.sin(angleRad) * femurL;
                ctx.lineTo(targetX, targetY);
                ctx.stroke();
                
                ctx.fillStyle = ecJointPhase === 1 ? COL_NEUTRAL : COL_ANGLE;
                ctx.beginPath(); ctx.arc(targetX, targetY, 5, 0, Math.PI * 2); ctx.fill();
                
                // Bas de patte (tibia pendante)
                ctx.strokeStyle = COL_LEG;
                ctx.lineWidth = 5;
                ctx.beginPath();
                ctx.moveTo(targetX, targetY);
                ctx.lineTo(targetX, targetY + 45);
                ctx.stroke();
                
                ctx.fillStyle = COL_LABEL;
                ctx.font = 'bold 11px monospace';
                ctx.textAlign = 'center';
                if (ecJointPhase === 1) {
                    ctx.fillText('90° (neutre)', cx + femurL/2, cy - 10);
                } else {
                    ctx.fillText('Incliné vers l\'avant', cx + femurL/2, cy - 10);
                }
                
                ctx.fillStyle = 'rgba(255,255,255,0.3)';
                ctx.font = '10px monospace';
                ctx.fillText('Vue de côté', cx, h - 10);

            } else if (joint.type === 'lower') {
                // Genou : vue de côté
                var tibiaL = 55;
                // Cuisse
                ctx.strokeStyle = COL_LEG;
                ctx.lineWidth = 6;
                ctx.lineCap = 'round';
                ctx.beginPath(); ctx.moveTo(cx - 40, cy - 20); ctx.lineTo(cx, cy); ctx.stroke();
                // Point genou
                ctx.fillStyle = COL_ACTIVE;
                ctx.beginPath(); ctx.arc(cx, cy, 9, 0, Math.PI * 2); ctx.fill();
                
                var angleRad = Math.PI / 2; // vertical/aligné
                if (ecJointPhase === 2) {
                    angleRad = Math.PI * 3 / 4; // plié à 90° vers l'intérieur (135°)
                }
                
                ctx.strokeStyle = ecJointPhase === 1 ? COL_NEUTRAL : COL_ANGLE;
                ctx.lineWidth = 6;
                ctx.lineCap = 'round';
                ctx.beginPath();
                ctx.moveTo(cx, cy);
                var targetX = cx + Math.cos(angleRad) * tibiaL;
                var targetY = cy + Math.sin(angleRad) * tibiaL;
                ctx.lineTo(targetX, targetY);
                ctx.stroke();
                
                ctx.fillStyle = ecJointPhase === 1 ? COL_NEUTRAL : COL_ANGLE;
                ctx.beginPath(); ctx.arc(targetX, targetY, 5, 0, Math.PI * 2); ctx.fill();
                
                // Sol
                ctx.strokeStyle = 'rgba(255,255,255,0.2)';
                ctx.lineWidth = 1.5;
                ctx.setLineDash([4, 4]);
                ctx.beginPath(); ctx.moveTo(cx - 40, cy + tibiaL + 2); ctx.lineTo(cx + 40, cy + tibiaL + 2); ctx.stroke();
                ctx.setLineDash([]);
                
                ctx.fillStyle = COL_LABEL;
                ctx.font = 'bold 11px monospace';
                ctx.textAlign = 'center';
                if (ecJointPhase === 1) {
                    ctx.fillText('Jambe tendue (neutre)', cx, cy + tibiaL + 15);
                } else {
                    ctx.fillText('Genou plié (90°)', cx - 15, cy + tibiaL - 10);
                }
                
                ctx.fillStyle = 'rgba(255,255,255,0.3)';
                ctx.font = '10px monospace';
                ctx.fillText('Vue de côté', cx, h - 10);
            }

            // Label position cible
            ctx.fillStyle = ecJointPhase === 1 ? COL_NEUTRAL : COL_ANGLE;
            ctx.font = 'bold 11px monospace';
            ctx.textAlign = 'center';
            ctx.fillText(ecJointPhase === 1 ? '✔ Position cible URDF' : '⚡ Position de test (flexion)', cx, 18);
        }
        
        function ecAttachCurrentJoint() {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            ecJointModified = true;
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'attach', index: joint.idx, manual: true }));
                // Envoyer la position actuelle du slider en angle absolu
                var curVal = parseInt(document.getElementById('ec-joint-slider').value) || 0;
                var absoluteAngle = curVal + 90;
                var isInverted = ecTempInverts[joint.idx];
                var angle = isInverted ? (180 - absoluteAngle) : absoluteAngle;
                appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'write', index: joint.idx, angle: angle, manual: true }));
                
                ecJointServoAttached = true;
                ecShowJoint(ecJointIndex);
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
            ecShowJoint(ecJointIndex);
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
                appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'write', index: joint.idx, angle: angle, manual: true }));
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
                    appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'write', index: joint.idx, angle: angle, manual: true }));
                }
            }, 50);
        }
        
        function ecValidateJoint() {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            var sliderVal = parseInt(document.getElementById('ec-joint-slider').value) || 0;
            
            if (ecJointPhase === 1) {
                if (ecJointServoAttached) {
                    // Étape A avec servo allumé : on lit la valeur courante du slider
                    ecPhase1Offset = sliderVal;
                } else {
                    // Étape A sans servo allumé : on conserve l'offset préexistant
                    ecPhase1Offset = ecTempOffsets[joint.idx] || 0;
                }
                ecJointPhase = 2;
                ecShowJoint(ecJointIndex);
                if (typeof showToast === 'function') {
                    showToast('✔ Zéro enregistré', 'Déplacez le curseur ou passez au sens suivant', 'info');
                }
            } else {
                var inverted = false;
                if (ecJointServoAttached) {
                    // Étape B avec servo allumé : détection dynamique
                    var delta = sliderVal - ecPhase1Offset;
                    if (delta < 0) {
                        inverted = true;
                    }
                } else {
                    // Étape B sans servo allumé : on conserve l'inversion préexistante
                    inverted = ecTempInverts[joint.idx] || false;
                }
                
                var offset = ecPhase1Offset;
                ecTempOffsets[joint.idx] = offset;
                ecTempInverts[joint.idx] = inverted;
                
                if (ecJointModified && appWs && appWs.readyState === WebSocket.OPEN) {
                    // 1. Enregistrer les limites min/max en PREMIER (avant offset/invert)
                    //    pour éviter un conflit avec le save distribué EEPROM de set_offset.
                    var minLim = ecTempMinLimits[joint.idx] !== undefined ? ecTempMinLimits[joint.idx] : 0;
                    var maxLim = ecTempMaxLimits[joint.idx] !== undefined ? ecTempMaxLimits[joint.idx] : 180;
                    appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'set_limit', index: joint.idx, min: minLim, max: maxLim }));
                    // 2. Enregistrer le flag miroir (inversion) dans l'EEPROM de l'Arduino
                    appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'set_invert', index: joint.idx, inverted: inverted }));
                    // 3. Enregistrer l'offset dans l'EEPROM de l'Arduino
                    appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'set_offset', index: joint.idx, offset: offset }));
                    // 4. Détacher le servo avant de passer au suivant
                    appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'detach', index: joint.idx }));
                }
                ecJointServoAttached = false;
                
                if (typeof showToast === 'function') {
                    showToast('✔ Articulation Validée', joint.leg + ' ' + joint.joint + ' — offset=' + offset + '°' + (inverted ? ', miroir=ON' : ', miroir=OFF'), 'success');
                }
                
                // Réinitialiser la phase pour l'articulation suivante
                ecJointPhase = 1;
                
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
                    // 🔴 SAFETY: Do NOT send 'stand' here. The EEPROM save is
                    // asynchronous (distributed save takes ~54 loops for offsets,
                    // ~102 loops for limits). Sending stand before the save
                    // completes would move motors with incomplete calibration.
                    // The user must explicitly press 'Finaliser' to send stand.
                }
            }
        }

        function ecUpdateJointMinLimit(value) {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            var val = parseInt(value) || 0;
            ecTempMinLimits[joint.idx] = val;
            var valEl = document.getElementById('ec-joint-min-limit-val');
            if (valEl) valEl.textContent = val + '°';
            
            var maxSlider = document.getElementById('ec-joint-max-limit-slider');
            if (maxSlider && val > parseInt(maxSlider.value)) {
                maxSlider.value = val;
                ecUpdateJointMaxLimit(val);
            }
            
            if (ecJointServoAttached && appWs && appWs.readyState === WebSocket.OPEN) {
                var maxVal = ecTempMaxLimits[joint.idx] !== undefined ? ecTempMaxLimits[joint.idx] : 180;
                appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'set_limit', index: joint.idx, min: val, max: maxVal }));
            }
        }

        function ecUpdateJointMaxLimit(value) {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            var val = parseInt(value) || 180;
            ecTempMaxLimits[joint.idx] = val;
            var valEl = document.getElementById('ec-joint-max-limit-val');
            if (valEl) valEl.textContent = val + '°';
            
            var minSlider = document.getElementById('ec-joint-min-limit-slider');
            if (minSlider && val < parseInt(minSlider.value)) {
                minSlider.value = val;
                ecUpdateJointMinLimit(val);
            }
            
            if (ecJointServoAttached && appWs && appWs.readyState === WebSocket.OPEN) {
                var minVal = ecTempMinLimits[joint.idx] !== undefined ? ecTempMinLimits[joint.idx] : 0;
                appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'set_limit', index: joint.idx, min: minVal, max: val }));
            }
        }

        function ecTestLimit(type) {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            if (!ecJointServoAttached) {
                if (typeof showToast === 'function') showToast('Attention', 'Veuillez d\'abord allumer le servo.', 'warning');
                return;
            }
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                var targetAngle = 90;
                var offset = parseInt(document.getElementById('ec-joint-slider').value) || 0;
                
                if (type === 'min') {
                    targetAngle = parseInt(document.getElementById('ec-joint-min-limit-slider').value) || 0;
                } else if (type === 'max') {
                    targetAngle = parseInt(document.getElementById('ec-joint-max-limit-slider').value) || 180;
                } else {
                    targetAngle = 90 + offset;
                }
                
                var isInverted = ecTempInverts[joint.idx];
                var angle = isInverted ? (180 - targetAngle) : targetAngle;
                appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'write', index: joint.idx, angle: angle, manual: true }));
                if (typeof showToast === 'function') {
                    var label = type === 'min' ? 'limite Min' : (type === 'max' ? 'limite Max' : 'position Offset');
                    showToast('Test', 'Positionnement sur ' + label + ' (' + targetAngle + '°)', 'info');
                }
            }
        }

        // Exposition globale
        window.ecUpdateJointMinLimit = ecUpdateJointMinLimit;
        window.ecUpdateJointMaxLimit = ecUpdateJointMaxLimit;
        window.ecTestLimit = ecTestLimit;
        window.ecToggleInvert = ecToggleInvert;
        window.ecUpdateJointSlider = ecUpdateJointSlider;
        window.ecAttachCurrentJoint = ecAttachCurrentJoint;
        window.ecDetachCurrentJoint = ecDetachCurrentJoint;
        window.ecInitJointCalibration = ecInitJointCalibration;

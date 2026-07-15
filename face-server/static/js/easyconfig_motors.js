// === easyconfig_motors.js - Calibration articulations moteurs ===
// Parcours en 3 étapes par moteur :
//   Étape A (phase 1) : Offset — aligner le moteur physique sur le zéro du modèle 3D.
//   Étape B (phase 2) : Limites min/max, exprimées EN RELATIF par rapport au zéro calibré.
//   Étape C (phase 3) : Sens — reproduire la flexion montrée par le modèle 3D ;
//                       le signe du mouvement détecte le miroir.
// Convention : le client envoie TOUJOURS des angles logiques bruts (0-180, 90 = centre).
// L'Arduino applique seul inversion + offset + limites depuis son EEPROM
// (physical = constrain((inverted ? 180-L : L) + offset, min, max)).
        var ecJointPhase = 1;
        var ecPhaseAOffset = 0;
        var ecJointModified = false;

        // Delta logique (°) montré par le modèle 3D en étape C, par type d'articulation.
        // Le genou fléchit en logique négative (convention IK : coude toujours négatif).
        var EC_SHOWN_DELTA = { hip: 30, upper: 40, lower: -45 };
        // Limites par défaut (± depuis le zéro calibré) proposées quand aucune
        // calibration n'existe. Hanches : ±25° (course mécanique du châssis).
        var EC_DEFAULT_LIMITS = { hip: 25, upper: 60, lower: 60 };

        async function ecInitJointCalibration() {
            ecJointIndex = 0;
            ecJointPhase = 1;
            ecPhaseAOffset = 0;
            ecTempOffsets  = new Array(12).fill(0);
            ecTempInverts  = new Array(12).fill(false);
            ecTempMinLimits = new Array(12).fill(0);
            ecTempMaxLimits = new Array(12).fill(180);

            // Pré-charger les calibrations existantes depuis la Gateway : permet de
            // « Suivant (garder les valeurs) » sans tout refaire.
            try {
                const res = await fetch('/core/calibration', {
                    headers: { 'X-API-Token': window.apiToken || localStorage.getItem('bastet_api_token') || '' }
                });
                if (res.ok) {
                    const data = await res.json();
                    if (data.offsets && data.offsets.length === 12) {
                        ecTempOffsets = data.offsets.map(function(v) { return parseInt(v) || 0; });
                    }
                    if (data.inverts && data.inverts.length === 12) {
                        ecTempInverts = data.inverts.map(function(v) { return v === true; });
                    }
                    if (data.limits && data.limits.length === 12) {
                        for (let i = 0; i < 12; i++) {
                            ecTempMinLimits[i] = (data.limits[i] && data.limits[i][0] !== undefined) ? data.limits[i][0] : 0;
                            ecTempMaxLimits[i] = (data.limits[i] && data.limits[i][1] !== undefined) ? data.limits[i][1] : 180;
                        }
                    }
                    console.log("[EasyConfig] Calibration pré-chargée :", ecTempOffsets, ecTempInverts);
                }
            } catch (err) {
                console.error("[EasyConfig] Erreur pré-chargement calibration :", err);
            }

            ecJointServoAttached = false;
            ecJointModified = false;
            ecAllJointsValidated = false;
            window._ecSessionDirty = false;
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

        // Envoi d'un angle logique brut au servo courant (aucune inversion côté client).
        // Le firmware applique physical = logical + offset : on borne donc en espace
        // PHYSIQUE (0-180 servo), pas en logique. Avec un gros offset (ex. genou à
        // ±80°), le logique doit pouvoir sortir de 0-180 pour couvrir toute la course
        // physique — l'ancien bornage 0-180 amputait l'articulation de |offset|°.
        function ecWriteServo(logicalAngle) {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            // Offset actif dans l'EEPROM : remis à 0 en phase A, appliqué en B/C
            // (voir ecAttachCurrentJoint).
            var o = (ecJointPhase === 1) ? 0 : (ecTempOffsets[joint.idx] || 0);
            var angle = Math.max(0 - o, Math.min(180 - o, Math.round(logicalAngle)));
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: 'arduino_cmd', cmd: 'write', index: joint.idx, angle: angle, manual: true }));
            }
        }

        function ecSendCmd(payload) {
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify(payload));
            }
        }

        // Bornes de la plage relative autour du zéro calibré (l'absolu doit rester 0-180) :
        // absolu = 90 + offset + rel  →  rel ∈ [-(90+offset), 90-offset]
        function ecRelBounds(offset) {
            return { lo: -(90 + offset), hi: 90 - offset };
        }

        function ecPhaseLabel() {
            if (ecJointPhase === 1) return 'Étape A : Régler le Zéro (Offset)';
            if (ecJointPhase === 2) return 'Étape B : Limites de Sécurité';
            return 'Étape C : Détecter le Sens (Miroir)';
        }

        function ecShowJoint(index) {
            if (index >= EC_JOINT_ORDER.length) return;
            var joint = EC_JOINT_ORDER[index];

            if (ecJointPhase === 1) {
                ecJointModified = false;
            }

            document.getElementById('ec-joint-leg-name').textContent = joint.leg;
            document.getElementById('ec-joint-name').textContent = joint.joint;
            document.getElementById('ec-joint-progress').textContent =
                'Articulation ' + (index + 1) + '/12 — ' + ecPhaseLabel();
            document.getElementById('ec-joint-icon').textContent = joint.icon;

            var sideEl = document.getElementById('ec-joint-side-label');
            if (sideEl) sideEl.textContent = joint.side === 'left' ? '🛅 Côté Gauche' : '🛅 Côté Droit';

            // Bouton miroir manuel masqué : le sens est détecté automatiquement en étape C
            var invertBtn = document.getElementById('ec-btn-invert-servo');
            if (invertBtn) invertBtn.style.display = 'none';

            // Pose cible dans la vue 3D interactive : neutre (A/B), fléchie (C)
            if (typeof window.highlightSpotMicroJoint === 'function') {
                window.highlightSpotMicroJoint(joint.leg_id, joint.type, ecJointPhase);
            }

            ecRenderPhaseDescription(joint);
            ecRenderPhaseControls(joint);
            ecUpdateActionButtons(joint);

            document.getElementById('ec-btn-prev').disabled = (index === 0 && ecJointPhase === 1);
            var btnNext = document.getElementById('ec-btn-next');
            btnNext.disabled = false;
            btnNext.textContent = ecKeepLabel(joint);
            // Ne pas assigner ecNextStep directement : onclick passerait le
            // MouseEvent comme targetStep et casserait la navigation.
            btnNext.onclick = function () { ecNextStep(); };
        }

        // Libellé du bouton Suivant : reprend les valeurs existantes sans re-calibrer.
        function ecKeepLabel(joint) {
            if (ecJointServoAttached) return 'Suivant →';
            if (ecJointPhase === 1) {
                var o = ecTempOffsets[joint.idx] || 0;
                return 'Suivant (garder offset ' + (o > 0 ? '+' : '') + o + '°)';
            }
            if (ecJointPhase === 2) {
                var o2 = ecTempOffsets[joint.idx] || 0;
                var rMin = ecTempMinLimits[joint.idx] - 90 - o2;
                var rMax = ecTempMaxLimits[joint.idx] - 90 - o2;
                return 'Suivant (garder ' + rMin + '°/' + '+' + rMax + '°)';
            }
            return 'Suivant (garder ' + (ecTempInverts[joint.idx] ? 'Miroir' : 'Normal') + ')';
        }

        function ecRenderPhaseDescription(joint) {
            var descEl = document.getElementById('ec-joint-description');
            if (!descEl) return;
            var P = function(t, c) {
                return '<p style="margin:0.25rem 0; font-size:0.85rem; line-height:1.4; opacity:0.9' +
                       (c ? '; color:' + c : '') + '">' + t + '</p>';
            };
            var html = '';
            if (ecJointPhase === 1) {
                html += P('<b>Étape A : Réglage du Zéro (Offset)</b>');
                html += P('Le modèle 3D montre la position neutre de cette articulation. ' +
                          'Allumez le servo puis déplacez le curseur jusqu\'à ce que le moteur physique ' +
                          '<b>corresponde exactement à la pose du modèle 3D</b>.');
                html += P('<b>Une fois aligné, cliquez sur « Valider le Zéro ».</b>', 'var(--success)');
            } else if (ecJointPhase === 2) {
                html += P('<b>Étape B : Limites de Sécurité (min/max)</b>');
                html += P('Définissez jusqu\'où le moteur a le droit d\'aller <b>de chaque côté du zéro calibré</b>, ' +
                          'pour qu\'il ne force jamais sur le châssis. Utilisez « Test Min/Max » pour vérifier ' +
                          'physiquement que la butée est bonne.');
                html += P('<b>Une fois les deux butées sûres, cliquez sur « Valider les Limites ».</b>', 'var(--success)');
            } else {
                html += P('<b>Étape C : Détection du Sens (Miroir)</b>');
                html += P('Le modèle 3D montre maintenant une <b>flexion</b> de cette articulation. ' +
                          'Déplacez le curseur pour reproduire <b>exactement la même position physique</b> sur le robot ' +
                          '(peu importe le sens du curseur, c\'est justement ce qu\'on mesure).');
                html += P('<b>Une fois la flexion reproduite, cliquez sur « Valider le Sens ».</b>', 'var(--accent)');
            }
            descEl.innerHTML = html;
        }

        function ecRenderPhaseControls(joint) {
            var mainBlock = document.getElementById('ec-joint-main-block');
            var limitsContainer = document.getElementById('ec-joint-limits-container');
            var slider = document.getElementById('ec-joint-slider');
            var unitLabel = document.getElementById('ec-joint-unit-label');
            var limitWarn = document.getElementById('ec-joint-limit-warning');
            if (limitWarn) limitWarn.style.display = 'none';

            if (ecJointPhase === 2) {
                if (mainBlock) mainBlock.style.display = 'none';
                if (limitsContainer) limitsContainer.style.display = 'flex';
                ecInitLimitSliders(joint);
                return;
            }

            if (mainBlock) mainBlock.style.display = '';
            if (limitsContainer) limitsContainer.style.display = 'none';

            if (ecJointPhase === 1) {
                slider.min = -90; slider.max = 90;
                slider.value = ecTempOffsets[joint.idx] || 0;
                if (unitLabel) unitLabel.textContent = '° offset';
            } else {
                // Étape C : course fixe ±45° autour du zéro calibré. Les butées EEPROM
                // sont ouvertes pendant la mesure (voir ecAttachCurrentJoint) puis
                // re-flashées aux valeurs de l'étape B à la validation.
                slider.min = -45;
                slider.max = 45;
                slider.value = 0;
                if (unitLabel) unitLabel.textContent = '° depuis le zéro';
            }
            document.getElementById('ec-joint-slider-value').textContent = slider.value;
            document.getElementById('ec-joint-slider-value').style.color = 'var(--accent)';
        }

        function ecInitLimitSliders(joint) {
            var o = ecTempOffsets[joint.idx] || 0;
            var b = ecRelBounds(o);
            var minSlider = document.getElementById('ec-joint-min-limit-slider');
            var maxSlider = document.getElementById('ec-joint-max-limit-slider');
            if (!minSlider || !maxSlider) return;

            var relMin = ecTempMinLimits[joint.idx] - 90 - o;
            var relMax = ecTempMaxLimits[joint.idx] - 90 - o;
            // Aucune limite définie (0/180 par défaut) → proposer le préréglage du type
            if (ecTempMinLimits[joint.idx] <= 0 && ecTempMaxLimits[joint.idx] >= 180) {
                var d = EC_DEFAULT_LIMITS[joint.type] || 45;
                relMin = -d; relMax = d;
            }
            relMin = Math.max(b.lo, Math.min(0, relMin));
            relMax = Math.min(b.hi, Math.max(0, relMax));

            minSlider.min = b.lo; minSlider.max = 0;   minSlider.value = relMin;
            maxSlider.min = 0;    maxSlider.max = b.hi; maxSlider.value = relMax;
            ecStoreRelLimits(joint, relMin, relMax, false);
            ecRefreshLimitLabels(joint);
        }

        function ecStoreRelLimits(joint, relMin, relMax, sendToArduino) {
            var o = ecTempOffsets[joint.idx] || 0;
            ecTempMinLimits[joint.idx] = Math.max(0, Math.min(180, 90 + o + relMin));
            ecTempMaxLimits[joint.idx] = Math.max(0, Math.min(180, 90 + o + relMax));
            if (sendToArduino && ecJointServoAttached) {
                window._ecSessionDirty = true;
                ecSendCmd({ type: 'arduino_cmd', cmd: 'set_limit', index: joint.idx,
                            min: ecTempMinLimits[joint.idx], max: ecTempMaxLimits[joint.idx] });
            }
        }

        function ecRefreshLimitLabels(joint) {
            var o = ecTempOffsets[joint.idx] || 0;
            var minEl = document.getElementById('ec-joint-min-limit-val');
            var maxEl = document.getElementById('ec-joint-max-limit-val');
            var relMin = ecTempMinLimits[joint.idx] - 90 - o;
            var relMax = ecTempMaxLimits[joint.idx] - 90 - o;
            if (minEl) minEl.textContent = relMin + '° (' + ecTempMinLimits[joint.idx] + '°)';
            if (maxEl) maxEl.textContent = '+' + relMax + '° (' + ecTempMaxLimits[joint.idx] + '°)';
        }

        function ecUpdateActionButtons(joint) {
            var btnAttach = document.getElementById('ec-btn-attach-servo');
            var btnValidate = document.getElementById('ec-btn-validate-joint');

            if (ecJointServoAttached) {
                btnAttach.textContent = '🔌 Éteindre le servo';
                btnAttach.onclick = ecDetachCurrentJoint;
                btnValidate.disabled = false;
                btnValidate.style.opacity = '1';
                btnValidate.style.background = 'var(--primary-color)';
            } else {
                btnAttach.textContent = '🔌 Allumer le servo';
                btnAttach.onclick = ecAttachCurrentJoint;
                btnValidate.disabled = true;
                btnValidate.style.opacity = '0.5';
                btnValidate.style.background = 'rgba(255,255,255,0.05)';
            }
            if (ecJointPhase === 1) btnValidate.textContent = '✅ Valider le Zéro';
            else if (ecJointPhase === 2) btnValidate.textContent = '✅ Valider les Limites';
            else btnValidate.textContent = '✅ Valider le Sens';
            btnValidate.onclick = ecValidateJoint;
        }

        // Allumage du servo courant : remet l'EEPROM du moteur dans un état de
        // mesure déterministe selon la phase, puis positionne le moteur.
        function ecAttachCurrentJoint() {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            if (!appWs || appWs.readyState !== WebSocket.OPEN) {
                if (typeof showToast === 'function') showToast('Erreur', 'WebSocket non connecté', 'error');
                return;
            }
            ecJointModified = true;
            window._ecSessionDirty = true;
            ecSendCmd({ type: 'arduino_cmd', cmd: 'attach', index: joint.idx, manual: true });

            var slider = document.getElementById('ec-joint-slider');
            var val = parseInt(slider.value) || 0;

            if (ecJointPhase === 1) {
                // Mesure d'offset : tout remettre à plat pour que écrire L donne physique = L
                ecSendCmd({ type: 'arduino_cmd', cmd: 'set_invert', index: joint.idx, inverted: false });
                ecSendCmd({ type: 'arduino_cmd', cmd: 'set_offset', index: joint.idx, offset: 0 });
                ecSendCmd({ type: 'arduino_cmd', cmd: 'set_limit', index: joint.idx, min: 0, max: 180 });
                ecWriteServo(90 + val);
            } else if (ecJointPhase === 2) {
                // Limites : offset validé appliqué, inversion neutralisée pendant le réglage
                ecSendCmd({ type: 'arduino_cmd', cmd: 'set_invert', index: joint.idx, inverted: false });
                ecSendCmd({ type: 'arduino_cmd', cmd: 'set_offset', index: joint.idx, offset: ecTempOffsets[joint.idx] || 0 });
                ecSendCmd({ type: 'arduino_cmd', cmd: 'set_limit', index: joint.idx,
                            min: ecTempMinLimits[joint.idx], max: ecTempMaxLimits[joint.idx] });
                ecWriteServo(90);
            } else {
                // Détection du sens : inversion OBLIGATOIREMENT désactivée pour mesurer
                // le comportement brut du montage, et butées ouvertes pour que la
                // course de mesure (±45°) ne soit jamais bloquée par d'anciennes
                // limites serrées. Miroir et limites sont réécrits à la validation.
                ecSendCmd({ type: 'arduino_cmd', cmd: 'set_invert', index: joint.idx, inverted: false });
                ecSendCmd({ type: 'arduino_cmd', cmd: 'set_offset', index: joint.idx, offset: ecTempOffsets[joint.idx] || 0 });
                ecSendCmd({ type: 'arduino_cmd', cmd: 'set_limit', index: joint.idx, min: 0, max: 180 });
                ecWriteServo(90);
            }

            ecJointServoAttached = true;
            ecShowJoint(ecJointIndex);
            if (typeof showToast === 'function') showToast('Servo', joint.joint + ' allumé — utilisez le curseur', 'info');
        }

        function ecDetachCurrentJoint() {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            ecSendCmd({ type: 'arduino_cmd', cmd: 'detach', index: joint.idx });
            ecJointServoAttached = false;
            ecShowJoint(ecJointIndex);
            if (typeof showToast === 'function') showToast('Servo', joint.joint + ' éteint', 'info');
        }

        var ecSliderThrottle = null;
        function ecUpdateJointSlider(value) {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            var intVal = parseInt(value) || 0;
            var valueEl = document.getElementById('ec-joint-slider-value');
            var limitWarn = document.getElementById('ec-joint-limit-warning');
            if (valueEl) valueEl.textContent = intVal;

            var logical;
            if (ecJointPhase === 1) {
                ecTempOffsets[joint.idx] = intVal;
                logical = 90 + intVal;   // offset EEPROM remis à 0 à l'allumage → physique = L
            } else {
                logical = 90 + intVal;   // offset appliqué par l'EEPROM → physique = 90+offset+rel
            }

            // Avertissement de butée : en PHYSIQUE (position réelle du servo),
            // le logique pouvant légitimement dépasser 0-180 quand l'offset est grand.
            var physical = logical + ((ecJointPhase === 1) ? 0 : (ecTempOffsets[joint.idx] || 0));
            if (physical <= 5 || physical >= 175) {
                if (valueEl) valueEl.style.color = '#f59e0b';
                if (limitWarn) limitWarn.style.display = 'inline-block';
            } else {
                if (valueEl) valueEl.style.color = 'var(--accent)';
                if (limitWarn) limitWarn.style.display = 'none';
            }

            if (ecSliderThrottle) clearTimeout(ecSliderThrottle);
            ecSliderThrottle = setTimeout(function() {
                if (ecJointServoAttached) ecWriteServo(logical);
            }, 50);
        }

        function ecUpdateJointMinLimit(value) {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            var rel = parseInt(value) || 0;
            var maxSlider = document.getElementById('ec-joint-max-limit-slider');
            var relMax = maxSlider ? (parseInt(maxSlider.value) || 0) : 90;
            ecStoreRelLimits(joint, rel, relMax, true);
            ecRefreshLimitLabels(joint);
        }

        function ecUpdateJointMaxLimit(value) {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            var rel = parseInt(value) || 0;
            var minSlider = document.getElementById('ec-joint-min-limit-slider');
            var relMin = minSlider ? (parseInt(minSlider.value) || 0) : -90;
            ecStoreRelLimits(joint, relMin, rel, true);
            ecRefreshLimitLabels(joint);
        }

        function ecTestLimit(type) {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            if (!ecJointServoAttached) {
                if (typeof showToast === 'function') showToast('Attention', 'Veuillez d\'abord allumer le servo.', 'warning');
                return;
            }
            var o = ecTempOffsets[joint.idx] || 0;
            var rel = 0;
            if (type === 'min') rel = ecTempMinLimits[joint.idx] - 90 - o;
            else if (type === 'max') rel = ecTempMaxLimits[joint.idx] - 90 - o;
            ecWriteServo(90 + rel);
            if (typeof showToast === 'function') {
                var label = type === 'min' ? 'limite Min' : (type === 'max' ? 'limite Max' : 'zéro calibré');
                showToast('Test', 'Positionnement sur ' + label + ' (' + rel + '° relatif)', 'info');
            }
        }

        // Conservé pour compatibilité (bouton masqué) : bascule manuelle du miroir.
        function ecToggleInvert() {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            ecTempInverts[joint.idx] = !ecTempInverts[joint.idx];
            if (ecJointServoAttached) {
                window._ecSessionDirty = true;
                ecSendCmd({ type: 'arduino_cmd', cmd: 'set_invert', index: joint.idx, inverted: ecTempInverts[joint.idx] });
            }
            if (typeof showToast === 'function') {
                showToast('Miroir', ecTempInverts[joint.idx] ? 'Sens inversé activé' : 'Sens normal', 'info');
            }
        }

        function ecValidateJoint() {
            var joint = EC_JOINT_ORDER[ecJointIndex];
            var slider = document.getElementById('ec-joint-slider');
            var sliderVal = parseInt(slider.value) || 0;

            if (ecJointPhase === 1) {
                if (ecJointServoAttached) {
                    ecPhaseAOffset = sliderVal;
                    ecTempOffsets[joint.idx] = sliderVal;
                    window._ecSessionDirty = true;
                    ecSendCmd({ type: 'arduino_cmd', cmd: 'set_offset', index: joint.idx, offset: sliderVal });
                }
                ecJointPhase = 2;
                ecShowJoint(ecJointIndex);
                if (typeof showToast === 'function') {
                    showToast('✔ Zéro enregistré', 'Réglez maintenant les butées min/max', 'info');
                }
                return;
            }

            if (ecJointPhase === 2) {
                if (ecJointServoAttached) {
                    window._ecSessionDirty = true;
                    ecSendCmd({ type: 'arduino_cmd', cmd: 'set_limit', index: joint.idx,
                                min: ecTempMinLimits[joint.idx], max: ecTempMaxLimits[joint.idx] });
                }
                ecJointPhase = 3;
                ecShowJoint(ecJointIndex);
                if (typeof showToast === 'function') {
                    showToast('✔ Limites enregistrées', 'Reproduisez la flexion montrée par le modèle 3D', 'info');
                }
                return;
            }

            // Étape C : détection du sens
            var inverted;
            if (ecJointServoAttached) {
                if (Math.abs(sliderVal) < 5) {
                    if (typeof showToast === 'function') {
                        showToast('⚠ Mouvement insuffisant',
                            'Déplacez le curseur d\'au moins 5° pour reproduire la flexion du modèle 3D, puis validez.',
                            'warning');
                    }
                    return;
                }
                // Le modèle 3D montre un delta logique EC_SHOWN_DELTA[type]. Si l'utilisateur
                // a dû aller dans le sens opposé pour reproduire la pose, le montage est inversé.
                // Convention IK (ik_solver.py) : gauche = 90 + θ, droite = 90 − θ → pour la
                // même flexion physique (tangage : upper/lower), le delta logique attendu est
                // opposé côté droit. La hanche (roulis) est déjà miroir dans la pose affichée.
                var shown = EC_SHOWN_DELTA[joint.type] || 30;
                if (joint.side === 'right' && joint.type !== 'hip') shown = -shown;
                inverted = (sliderVal > 0) !== (shown > 0);
                window._ecSessionDirty = true;
                ecSendCmd({ type: 'arduino_cmd', cmd: 'set_invert', index: joint.idx, inverted: inverted });
                // Restaurer les butées de l'étape B (ouvertes pendant la mesure du sens)
                ecSendCmd({ type: 'arduino_cmd', cmd: 'set_limit', index: joint.idx,
                            min: ecTempMinLimits[joint.idx], max: ecTempMaxLimits[joint.idx] });
                ecSendCmd({ type: 'arduino_cmd', cmd: 'detach', index: joint.idx });
            } else {
                inverted = ecTempInverts[joint.idx] || false;
            }
            ecTempInverts[joint.idx] = inverted;
            ecJointServoAttached = false;

            if (typeof showToast === 'function') {
                showToast('✔ Articulation Validée',
                    joint.leg + ' ' + joint.joint + ' — offset=' + (ecTempOffsets[joint.idx] || 0) + '°' +
                    (inverted ? ', miroir=ON' : ', miroir=OFF'), 'success');
            }

            ecJointPhase = 1;
            if (ecJointIndex < 11) {
                ecJointIndex++;
                ecShowJoint(ecJointIndex);
            } else {
                ecAllJointsValidated = true;
                document.getElementById('ec-joint-calibration-view').style.display = 'none';
                document.getElementById('ec-joint-final-view').style.display = 'flex';
                document.getElementById('ec-btn-prev').disabled = false;
                document.getElementById('ec-btn-next').disabled = false;
                document.getElementById('ec-progress-text').textContent = 'Toutes les articulations calibrées';
                // 🔴 SAFETY: ne PAS envoyer 'stand' ici — la sauvegarde EEPROM est
                // asynchrone. L'utilisateur choisit explicitement dans la vue finale.
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
        window.ecValidateJoint = ecValidateJoint;

// === V-SLAM Map & Mode Detection ===
        // ─── CANVASES RENDER CODES ───────────────────────────────────────────



        




        function drawSLAMMap() {



            const canvas = document.getElementById('slam-map-canvas');



            if (!canvas) return;



            const ctx = canvas.getContext('2d');



            




            const dpr = window.devicePixelRatio || 1;



            const rect = canvas.getBoundingClientRect();



            canvas.width = rect.width * dpr;



            canvas.height = rect.height * dpr;



            ctx.scale(dpr, dpr);



            




            const w = rect.width;



            const h = rect.height;



            




            ctx.clearRect(0, 0, w, h);



            ctx.fillStyle = '#07070a';



            ctx.fillRect(0, 0, w, h);



            




            const scale = 40;



            const cx = w / 2;



            const cy = h / 2;



            




            // Grid



            if (document.getElementById('layer-grid').checked) {



                ctx.strokeStyle = '#101015';



                ctx.lineWidth = 0.5;



                const gridStep = scale * 0.5;



                for (let x = cx % gridStep; x < w; x += gridStep) {



                    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();



                }



                for (let y = cy % gridStep; y < h; y += gridStep) {



                    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();



                }



                




                ctx.fillStyle = 'rgba(255, 255, 255, 0.05)';



                const walls = [



                    {x: -1.5, y: -2, w: 3, h: 0.1},



                    {x: -1.5, y: 2, w: 3, h: 0.1},



                    {x: -1.5, y: -2, w: 0.1, h: 4},



                    {x: 1.5, y: -2, w: 0.1, h: 4}



                ];



                walls.forEach(wall => {



                    ctx.fillRect(cx + wall.x * scale, cy - (wall.y + wall.h) * scale, wall.w * scale, wall.h * scale);



                });



            }



            




            // Points (laser)



            if (document.getElementById('layer-points').checked) {



                ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--success').trim();



                if (window.slamPoints && window.slamPoints.length > 0) {



                    window.slamPoints.forEach(pt => {



                        ctx.beginPath();



                        ctx.arc(cx + pt.x * scale, cy - pt.y * scale, 1.5, 0, Math.PI * 2);



                        ctx.fill();



                    });



                } else {



                    for (let angle = 0; angle < Math.PI * 2; angle += 0.05) {



                        const dist = 1.8 + Math.sin(angle * 4) * 0.1;



                        const px = cx + Math.cos(angle) * dist * scale;



                        const py = cy - Math.sin(angle) * dist * scale;



                        ctx.beginPath();



                        ctx.arc(px, py, 1.5, 0, Math.PI*2);



                        ctx.fill();



                    }



                }



            }



            




            // Sonar



            if (document.getElementById('layer-sonar').checked) {



                ctx.fillStyle = 'rgba(245, 158, 11, 0.15)';



                ctx.strokeStyle = '#f59e0b';



                ctx.lineWidth = 1;



                




                const rx = cx + window.robotPose.x * scale;



                const ry = cy - window.robotPose.y * scale;



                const rtheta = -window.robotPose.theta;



                




                ctx.save();



                ctx.translate(rx, ry);



                ctx.rotate(rtheta);



                ctx.beginPath();



                ctx.moveTo(0, 0);



                ctx.arc(0, 0, 1.2 * scale, -Math.PI / 12, Math.PI / 12);



                ctx.closePath();



                ctx.fill();



                ctx.stroke();



                ctx.restore();



            }



            




            // Trajectory Path



            if (document.getElementById('layer-trajectory').checked && window.slamPath && window.slamPath.length > 0) {



                ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();



                ctx.lineWidth = 2.5;



                ctx.beginPath();



                window.slamPath.forEach((pt, idx) => {



                    const px = cx + pt.x * scale;



                    const py = cy - pt.y * scale;



                    if (idx === 0) ctx.moveTo(px, py);



                    else ctx.lineTo(px, py);



                });



                ctx.stroke();



            }



            




            // Robot Outline



            const rx = cx + window.robotPose.x * scale;



            const ry = cy - window.robotPose.y * scale;



            const rtheta = -window.robotPose.theta;



            




            ctx.save();



            ctx.translate(rx, ry);



            ctx.rotate(rtheta);



            




            ctx.strokeStyle = '#ffffff';



            ctx.lineWidth = 2;



            ctx.strokeRect(-12, -8, 24, 16);



            




            ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();



            ctx.beginPath();



            ctx.moveTo(12, 0);



            ctx.lineTo(6, -5);



            ctx.lineTo(6, 5);



            ctx.closePath();



            ctx.fill();



            




            ctx.restore();



        }








        function resetSLAMMap() {



            window.robotPose = {x: 0, y: 0, theta: 0};



            window.slamPath = [];



            window.slamPoints = [];



            drawSLAMMap();



        }








        function updateSLAMParam(param) {



            const slider = document.getElementById(`param-slider-${param}`);



            const label = document.getElementById(`param-val-${param}`);



            if (slider && label) {



                if (param === 'resolution') {



                    label.textContent = `${(slider.value / 100).toFixed(2)}m`;



                } else if (param === 'inflation') {



                    label.textContent = `${(slider.value / 100).toFixed(2)}m`;



                } else {



                    label.textContent = `${slider.value}%`;



                }



            }



        }








        




        // ─── SLAM Mode Detection & UI ──────────────────────────────────────



        









// ─── V-SLAM mode helper (used by updateSLAMMode + toggleVSlamTest pre-flight)



        function getCurrentSlamMode() {



            const sensors = window.lastTelemetryState && window.lastTelemetryState.sensors;



            const cam1 = !!(sensors && sensors.cam1_connected === true);



            const cam2 = !!(sensors && sensors.cam2_connected === true);



            const camCount = (cam1 ? 1 : 0) + (cam2 ? 1 : 0);



            let mode = 'Aucune cam';



            let modeColor = '#ef4444';



            let bgColor = 'rgba(239,68,68,0.12)';



            if (camCount === 0) {



                mode = 'Aucune caméra';



            } else if (camCount === 1) {



                mode = 'Mono';



                modeColor = '#f59e0b';



                bgColor = 'rgba(245,158,11,0.12)';



            } else {



                mode = 'Stéréo';



                modeColor = '#22c55e';



                bgColor = 'rgba(34,197,94,0.12)';



            }



            return { mode: mode, modeColor: modeColor, bgColor: bgColor, cam1: cam1, cam2: cam2, hasTelemetry: !!sensors };



        }








        function updateSLAMMode() {



            const badge = document.getElementById('slam-mode-badge');



            const camerasBadge = document.getElementById('slam-cameras-badge');



            const overlay = document.getElementById('slam-disabled-overlay');



            if (!badge) return;



            




            let cam1 = false, cam2 = false;



            if (window.lastTelemetryState && window.lastTelemetryState.sensors) {



                cam1 = window.lastTelemetryState.sensors.cam1_connected === true;



                cam2 = window.lastTelemetryState.sensors.cam2_connected === true;



            }



            




            const camCount = (cam1 ? 1 : 0) + (cam2 ? 1 : 0);



            let mode = 'Aucune cam';



            let modeColor = '#ef4444';



            let bgColor = 'rgba(239,68,68,0.12)';



            




            if (camCount === 0) {



                mode = 'Aucune cam\u00e9ra';



                modeColor = '#ef4444';



                bgColor = 'rgba(239,68,68,0.12)';



                if (overlay) overlay.style.display = 'flex';



            } else if (camCount === 1) {



                mode = 'Mono';



                modeColor = '#f59e0b';



                bgColor = 'rgba(245,158,11,0.12)';



                if (overlay) overlay.style.display = 'none';



            } else {



                mode = 'St\u00e9r\u00e9o';



                modeColor = '#22c55e';



                bgColor = 'rgba(34,197,94,0.12)';



                if (overlay) overlay.style.display = 'none';



            }



            




            badge.textContent = mode;



            badge.style.color = modeColor;



            badge.style.background = bgColor;



            if (camerasBadge) {



                camerasBadge.textContent = camCount + ' cam\u00e9ra' + (camCount > 1 ? 's' : '') + ' d\u00e9tect\u00e9e' + (camCount > 1 ? 's' : '');



            }



        




        









            // Aussi copier le mode dans le badge de la Console de Test V-SLAM (toujours visible)



            const testBadge = document.getElementById('vslam-test-mode-badge');



            if (testBadge) {



                testBadge.textContent = 'Mode: ' + mode;



                testBadge.style.background = bgColor;



                testBadge.style.color = modeColor;



                testBadge.title = (mode === 'Stéréo' ? 'Cam1 + Cam2 connectées au robot'



                                   : (mode === 'Mono' ? 'Caméra 1 seule connectée au robot'



                                   : 'Aucune caméra détectée par le robot'));



            }



        }








        // ─── MOBILE SIDEBAR ACTIONS ───────────────────────────────────────────




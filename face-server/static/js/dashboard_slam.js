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
        

        // ─── Left/Right Camera Attribution ───────────────────────────────
        let ecLRPeerA = null;
        let ecLRPeerB = null;
        let ecLRAssigned = { left: null, right: null };
        let ecLRStreamA = null;
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
        

// === Control & Navigation ===
// State variables for control tab
var keysPressed = {};
var controlSpeed = 0.15;
var controlActiveDir = null;
var controlWalkInterval = null;
var navTarget = null;

// ─── EASYCONFIG FUNCTIONS ──────────────────────────────────────────────

        function initControlTab() {
            // Setup canvas interaction
            const canvas = document.getElementById('control-map-canvas');
            if (canvas) {
                canvas.removeEventListener('mousedown', onControlMapClick);
                canvas.addEventListener('mousedown', onControlMapClick);
            }
            
            // Setup keyboard listeners (once globally)
            if (!window.controlKeyboardInitialized) {
                window.controlKeyboardInitialized = true;
                window.addEventListener('keydown', (e) => {
                    if (activeTab !== 'control') return;
                    
                    const keyMap = {
                        'z': 'up', 'KeyW': 'up', 'ArrowUp': 'up',
                        's': 'down', 'KeyS': 'down', 'ArrowDown': 'down',
                        'q': 'left', 'KeyA': 'left', 'ArrowLeft': 'left',
                        'd': 'right', 'KeyD': 'right', 'ArrowRight': 'right'
                    };
                    
                    const dir = keyMap[e.key] || keyMap[e.code];
                    if (dir && !keysPressed[dir]) {
                        e.preventDefault();
                        keysPressed[dir] = true;
                        startWalking(dir);
                    }
                    if (e.key === ' ' || e.key === 'x' || e.key === 'Escape') {
                        e.preventDefault();
                        sendControlStop();
                    }
                });
                
                window.addEventListener('keyup', (e) => {
                    if (activeTab !== 'control') return;
                    const keyMap = {
                        'z': 'up', 'KeyW': 'up', 'ArrowUp': 'up',
                        's': 'down', 'KeyS': 'down', 'ArrowDown': 'down',
                        'q': 'left', 'KeyA': 'left', 'ArrowLeft': 'left',
                        'd': 'right', 'KeyD': 'right', 'ArrowRight': 'right'
                    };
                    const dir = keyMap[e.key] || keyMap[e.code];
                    if (dir) {
                        keysPressed[dir] = false;
                        // If no direction key is pressed, stop walking
                        if (!Object.values(keysPressed).includes(true)) {
                            stopWalking();
                        }
                    }
                });
            }
            
            // Initial drawing
            drawControlMap();
        }

function updateControlSpeed() {
            const val = document.getElementById('control-speed-slider').value;
            controlSpeed = parseFloat((val / 100).toFixed(2));
            document.getElementById('control-speed-val').textContent = controlSpeed + ' m/s';
        }

function sendControlCmd(cmd) {
        if (appWs && appWs.readyState === WebSocket.OPEN) {
            appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: cmd }));
            if (typeof showToast === 'function') {
                const labels = { stand: 'Se lever', sit: "S'asseoir", stop: 'Stop' };
                showToast("Télécommande", labels[cmd] || cmd + " envoyé", "info");
            }
        } else {
            if (typeof showToast === 'function') {
                showToast("Erreur", "WebSocket non connecté. Le robot est peut-être hors ligne.", "error");
            }
        }
            }

function sendControlStop() {
            stopWalking();
            keysPressed = {};
            // Send direct zero velocity and stop cmd
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "cmd_vel", linear: 0.0, angular: 0.0 }));
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stop" }));
            }
            // Reset D-Pad button styles
            document.querySelectorAll('.dpad-btn').forEach(btn => {
                btn.style.backgroundColor = '';
                btn.style.color = '';
            });
            const stopBtn = document.getElementById('dpad-stop');
            if (stopBtn) {
                stopBtn.style.backgroundColor = 'rgba(239, 68, 68, 0.2)';
            }
        }

function startWalking(dir) {
            if (controlActiveDir === dir) return;
            controlActiveDir = dir;
            
            // Highlight button
            document.querySelectorAll('.dpad-btn').forEach(btn => {
                btn.style.backgroundColor = '';
                btn.style.color = '';
            });
            const activeBtn = document.getElementById(`dpad-${dir}`);
            if (activeBtn) {
                activeBtn.style.backgroundColor = 'var(--accent)';
                activeBtn.style.color = 'white';
            }

            if (controlWalkInterval) clearInterval(controlWalkInterval);
            
            // Periodically send cmd_vel
            function sendVel() {
                if (!appWs || appWs.readyState !== WebSocket.OPEN) return;
                let vx = 0.0;
                let wz = 0.0;
                
                if (dir === 'up') vx = controlSpeed;
                else if (dir === 'down') vx = -controlSpeed;
                else if (dir === 'left') wz = 1.0; // rotate left rad/s
                else if (dir === 'right') wz = -1.0; // rotate right rad/s
                
                appWs.send(JSON.stringify({
                    type: "cmd_vel",
                    linear: vx,
                    angular: wz
                }));
            }
            
            sendVel();
            controlWalkInterval = setInterval(sendVel, 100);
        }

function stopWalking() {
            if (controlWalkInterval) {
                clearInterval(controlWalkInterval);
                controlWalkInterval = null;
            }
            controlActiveDir = null;
            
            // Highlight reset
            document.querySelectorAll('.dpad-btn').forEach(btn => {
                btn.style.backgroundColor = '';
                btn.style.color = '';
            });
            const stopBtn = document.getElementById('dpad-stop');
            if (stopBtn) {
                stopBtn.style.backgroundColor = 'rgba(239, 68, 68, 0.1)';
            }
            
            // Send zero velocity to stop
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "cmd_vel", linear: 0.0, angular: 0.0 }));
            }
        }

        // Map Click interaction
function onControlMapClick(e) {
            const canvas = document.getElementById('control-map-canvas');
            if (!canvas) return;
            const rect = canvas.getBoundingClientRect();
            const clickX = e.clientX - rect.left;
            const clickY = e.clientY - rect.top;
            
            const w = rect.width;
            const h = rect.height;
            const cx = w / 2;
            const cy = h / 2;
            const scale = 40; // px/m
            
            // Calculate coordinates in meters relative to base_link/odom (centered)
            const targetX = (clickX - cx) / scale;
            const targetY = -(clickY - cy) / scale; // invert Y for Cartesian
            
            navTarget = { x: parseFloat(targetX.toFixed(2)), y: parseFloat(targetY.toFixed(2)) };
            
            // Update panel
            document.getElementById('nav-target-x').textContent = navTarget.x.toFixed(2);
            document.getElementById('nav-target-y').textContent = navTarget.y.toFixed(2);
            
            const panel = document.getElementById('nav-target-panel');
            if (panel) {
                panel.style.opacity = '1';
                panel.style.pointerEvents = 'auto';
            }
            
            drawControlMap();
        }

function clearNavGoal() {
            navTarget = null;
            const panel = document.getElementById('nav-target-panel');
            if (panel) {
                panel.style.opacity = '0';
                panel.style.pointerEvents = 'none';
            }
            drawControlMap();
        }

function sendNavGoal() {
            if (!navTarget) return;
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                // Send nav goal target to robot
                appWs.send(JSON.stringify({
                    type: "nav_goal",
                    x: navTarget.x,
                    y: navTarget.y
                }));
                
                // Show notification or visual feedback
                const btn = document.querySelector('#nav-target-panel .btn-primary');
                if (btn) {
                    const originalText = btn.innerHTML;
                    btn.innerHTML = '⚡ Objectif Envoyé !';
                    btn.style.backgroundColor = 'var(--success)';
                    setTimeout(() => {
                        btn.innerHTML = originalText;
                        btn.style.backgroundColor = '';
                        clearNavGoal();
                    }, 1500);
                }
            } else {
                alert("Erreur : Le robot est hors-ligne.");
            }
        }

function drawControlMap() {
            const canvas = document.getElementById('control-map-canvas');
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
            
            // Grid lines
            ctx.strokeStyle = '#101015';
            ctx.lineWidth = 0.5;
            const gridStep = scale * 0.5;
            for (let x = cx % gridStep; x < w; x += gridStep) {
                ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
            }
            for (let y = cy % gridStep; y < h; y += gridStep) {
                ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
            }
            
            // Walls/Occupancy Grid representation
            ctx.fillStyle = 'rgba(255, 255, 255, 0.05)';
            const walls = [
                {x: -1.5, y: -2, w: 3, h: 0.1},
                {x: -1.5, y: 2, w: 3, h: 0.1},
                {x: -1.5, y: -2, w: 0.1, h: 4},
                {x: 1.5, y: -2, w: 0.1, h: 4},
                {x: 0.5, y: -0.5, w: 0.5, h: 1}
            ];
            walls.forEach(wall => {
                ctx.fillRect(cx + wall.x * scale, cy - (wall.y + wall.h) * scale, wall.w * scale, wall.h * scale);
            });
            
            // Points (laser scan)
            ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--success').trim() || '#10b981';
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
            
            // Path trajectory
            if (window.slamPath && window.slamPath.length > 0) {
                ctx.strokeStyle = 'rgba(99, 102, 241, 0.6)';
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
            
            // Draw Waypoint navigation goal (if set)
            if (navTarget) {
                const tx = cx + navTarget.x * scale;
                const ty = cy - navTarget.y * scale;
                
                // Pulsing target halo
                ctx.save();
                ctx.strokeStyle = 'var(--accent)';
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.arc(tx, ty, 8 + (Date.now() % 500) / 100, 0, Math.PI * 2);
                ctx.stroke();
                
                // Outer target circle
                ctx.strokeStyle = 'var(--accent)';
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.arc(tx, ty, 6, 0, Math.PI * 2);
                ctx.stroke();
                
                // Center dot
                ctx.fillStyle = 'var(--accent)';
                ctx.beginPath();
                ctx.arc(tx, ty, 2, 0, Math.PI * 2);
                ctx.fill();
                ctx.restore();
            }
            
            // Robot Pose triangle
            const rx = cx + (window.robotPose ? window.robotPose.x : 0) * scale;
            const ry = cy - (window.robotPose ? window.robotPose.y : 0) * scale;
            const rtheta = -(window.robotPose ? window.robotPose.theta : 0);
            
            ctx.save();
            ctx.translate(rx, ry);
            ctx.rotate(rtheta);
            
            ctx.fillStyle = 'var(--accent)';
            ctx.beginPath();
            ctx.moveTo(14, 0);
            ctx.lineTo(-8, -8);
            ctx.lineTo(-4, 0);
            ctx.lineTo(-8, 8);
            ctx.closePath();
            ctx.fill();
            
            // Glowing orientation indicator
            ctx.strokeStyle = 'rgba(99, 102, 241, 0.5)';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(0, 0, 12, 0, Math.PI * 2);
            ctx.stroke();
            
            ctx.restore();
            
            // Request animation frame for continuous animation of pulses
            if (activeTab === 'control') {
                requestAnimationFrame(drawControlMap);
            }
        }
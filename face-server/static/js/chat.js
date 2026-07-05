// === chat + voice + AI control ===
        // ─── CHAT TAB IA FUNCTIONS ────────────────────────────────────────────
        
        function sendChatMessage(e) {
            e.preventDefault();
            const input = document.getElementById('chat-tab-input');
            const text = input.value.trim();
            if (!text) return;
            
            appendLLMMessage('Moi', text);
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "chat", text: text }));
            } else {
                appendLLMMessage('Système', 'Erreur : WebSocket déconnecté.');
            }
            
            input.value = '';
        }

        function appendLLMMessage(sender, text) {
            const box = document.getElementById('chat-tab-messages');
            if (!box) return;
            
            if (box.textContent.includes("Aucun message échangé")) {
                box.innerHTML = '';
            }
            
            const msgEl = document.createElement('div');
            msgEl.style.padding = '0.5rem 0.75rem';
            msgEl.style.borderRadius = '6px';
            msgEl.style.fontSize = '0.9rem';
            msgEl.style.maxWidth = '80%';
            msgEl.style.marginBottom = '0.25rem';
            
            if (sender === 'Moi') {
                msgEl.style.alignSelf = 'flex-end';
                msgEl.style.backgroundColor = 'rgba(255, 111, 97, 0.2)';
                msgEl.style.border = '1px solid var(--accent)';
                msgEl.innerHTML = `<span style="font-weight:bold;color: var(--accent);display:block;font-size:0.75rem;">Moi</span>${text}`;
            } else if (sender === 'Système') {
                msgEl.style.alignSelf = 'center';
                msgEl.style.backgroundColor = 'rgba(225, 29, 72, 0.1)';
                msgEl.style.border = '1px solid var(--danger)';
                msgEl.innerHTML = `<span style="font-style:italic;color:#f87171;font-size:0.8rem;">${text}</span>`;
            } else {
                msgEl.style.alignSelf = 'flex-start';
                msgEl.style.backgroundColor = 'rgba(255, 255, 255, 0.05)';
                msgEl.style.border = '1px solid var(--border-color)';
                msgEl.innerHTML = `<span style="font-weight:bold;color:var(--text-primary);display:block;font-size:0.75rem;">${sender}</span>${text}`;
            }
            
            box.appendChild(msgEl);
            box.scrollTop = box.scrollHeight;
        }

        // ─── TÉLÉCOMMANDE CHAT VOCAL & PILOTAGE IA ────────────────────────────
        function sendControlChatMessage(e) {
            if (e) e.preventDefault();
            const input = document.getElementById('control-chat-input');
            const text = input.value.trim();
            if (!text) return;
            
            appendControlChatMessage('Moi', text);
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "chat", text: text }));
            } else {
                appendControlChatMessage('Système', 'Erreur : WebSocket déconnecté.');
            }
            input.value = '';
        }

        function appendControlChatMessage(sender, text) {
            const box = document.getElementById('control-chat-messages');
            if (!box) return;
            
            if (box.textContent.includes("Parlez à Bastet")) {
                box.innerHTML = '';
            }
            
            const msgEl = document.createElement('div');
            msgEl.style.padding = '0.5rem 0.75rem';
            msgEl.style.borderRadius = '6px';
            msgEl.style.fontSize = '0.85rem';
            msgEl.style.maxWidth = '85%';
            msgEl.style.marginBottom = '0.25rem';
            msgEl.style.lineHeight = '1.3';
            
            if (sender === 'Moi') {
                msgEl.style.alignSelf = 'flex-end';
                msgEl.style.backgroundColor = 'rgba(255, 111, 97, 0.2)';
                msgEl.style.border = '1px solid var(--accent)';
                msgEl.innerHTML = `<span style="font-weight:bold;color: var(--accent);display:block;font-size:0.7rem;margin-bottom:0.15rem;">Moi</span>${text}`;
            } else if (sender === 'Système') {
                msgEl.style.alignSelf = 'center';
                msgEl.style.backgroundColor = 'rgba(225, 29, 72, 0.1)';
                msgEl.style.border = '1px solid var(--danger)';
                msgEl.innerHTML = `<span style="font-style:italic;color:#f87171;font-size:0.75rem;">${text}</span>`;
            } else {
                msgEl.style.alignSelf = 'flex-start';
                msgEl.style.backgroundColor = 'rgba(255, 255, 255, 0.05)';
                msgEl.style.border = '1px solid var(--border-color)';
                msgEl.innerHTML = `<span style="font-weight:bold;color:var(--text-primary);display:block;font-size:0.7rem;margin-bottom:0.15rem;">${sender}</span>${text}`;
            }
            
            box.appendChild(msgEl);
            box.scrollTop = box.scrollHeight;
        }

        let voiceRecognition = null;
        let isVoiceListening = false;

        function toggleVoiceRecognition() {
            const btn = document.getElementById('control-mic-btn');
            const pulse = document.getElementById('mic-pulse');
            
            if (isVoiceListening) {
                if (voiceRecognition) voiceRecognition.stop();
                return;
            }
            
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SpeechRecognition) {
                appendControlChatMessage('Système', "La reconnaissance vocale n'est pas supportée par votre navigateur.");
                return;
            }
            
            voiceRecognition = new SpeechRecognition();
            voiceRecognition.lang = 'fr-FR';
            voiceRecognition.interimResults = false;
            voiceRecognition.maxAlternatives = 1;
            
            voiceRecognition.onstart = () => {
                isVoiceListening = true;
                btn.classList.add('mic-active');
                if (pulse) {
                    pulse.style.opacity = '1';
                    pulse.style.transform = 'scale(1.5)';
                }
            };
            
            voiceRecognition.onresult = (event) => {
                const speechResult = event.results[0][0].transcript;
                const input = document.getElementById('control-chat-input');
                if (input) {
                    input.value = speechResult;
                    sendControlChatMessage();
                }
            };
            
            voiceRecognition.onerror = (event) => {
                console.error("Reconnaissance vocale erreur:", event.error);
                appendControlChatMessage('Système', "Erreur de reconnaissance vocale : " + event.error);
            };
            
            voiceRecognition.onend = () => {
                isVoiceListening = false;
                btn.classList.remove('mic-active');
                if (pulse) {
                    pulse.style.opacity = '0';
                    pulse.style.transform = 'scale(1)';
                }
            };
            
            voiceRecognition.start();
        }

        function handleIncomingLLMMessage(sender, text) {
            // Afficher dans le chat principal de l'IA
            appendLLMMessage(sender, text);
            
            let cleanText = text;
            
            // Parser les balises [ACTION: ...]
            const actionRegex = /\[ACTION:\s*([a-zA-Z]+)\]/g;
            let actionMatch;
            while ((actionMatch = actionRegex.exec(text)) !== null) {
                const action = actionMatch[1].toLowerCase();
                executeVoiceAction(action);
            }
            cleanText = cleanText.replace(actionRegex, '');
            
            // Parser les balises [NAV: x, y]
            const navRegex = /\[NAV:\s*(-?\d+(\.\d+)?)\s*,\s*(-?\d+(\.\d+)?)\]/g;
            let navMatch;
            while ((navMatch = navRegex.exec(text)) !== null) {
                const x = parseFloat(navMatch[1]);
                const y = parseFloat(navMatch[3]);
                executeVoiceNav(x, y);
            }
            cleanText = cleanText.replace(navRegex, '');
            
            // Afficher dans le chat de la télécommande
            appendControlChatMessage(sender, cleanText.trim());
        }

        function executeVoiceAction(action) {
            if (['up', 'down', 'left', 'right'].includes(action)) {
                const btnId = `dpad-${action}`;
                const btn = document.getElementById(btnId);
                if (btn) {
                    btn.classList.add('active-dpad');
                    btn.style.backgroundColor = 'var(--accent)';
                    btn.style.color = 'white';
                }
                startWalking(action);
                
                setTimeout(() => {
                    stopWalking();
                    if (btn) {
                        btn.classList.remove('active-dpad');
                        btn.style.backgroundColor = '';
                        btn.style.color = '';
                    }
                }, 2500);
            } else if (action === 'stop') {
                sendControlStop();
                const btn = document.getElementById('dpad-stop');
                if (btn) {
                    btn.style.transform = 'scale(0.9)';
                    setTimeout(() => btn.style.transform = '', 200);
                }
            } else if (action === 'stand') {
                sendControlCmd('stand');
            } else if (action === 'sit') {
                sendControlCmd('sit');
            }
        }

        function executeVoiceNav(x, y) {
            navTarget = { x: x, y: y };
            
            document.getElementById('nav-target-x').textContent = x.toFixed(2);
            document.getElementById('nav-target-y').textContent = y.toFixed(2);
            
            const panel = document.getElementById('nav-target-panel');
            if (panel) {
                panel.style.opacity = '1';
                panel.style.pointerEvents = 'auto';
            }
            
            drawControlMap();
            sendNavGoal();
        }

        function clearJSONConsole() {
            const consoleEl = document.getElementById('json-traffic-console');
            if (consoleEl) consoleEl.textContent = '[Console effacée]';
        }

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

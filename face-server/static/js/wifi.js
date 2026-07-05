// === all WiFi functions ===
        // ─── WIFI POPUP FUNCTIONS ─────────────────────────────────────────────
        
        function openWifiModal() {
            const modal = document.getElementById('wifiModal');
            modal.classList.add('active');
            modal.removeAttribute('inert');
            scanWifiNetworks();
        }

        function closeWifiModal() {
            const modal = document.getElementById('wifiModal');
            modal.classList.remove('active');
            modal.setAttribute('inert', '');
            cancelWifiConnection();
        }

        function closeWifiModalOnClick(e) {
            if (e.target === document.getElementById('wifiModal')) closeWifiModal();
        }

        function scanWifiNetworks() {
            const listContainer = document.getElementById('wifi-list-container');
            const knownContainer = document.getElementById('wifi-known-container');
            
            listContainer.innerHTML = `
                <div style="text-align: center; color: var(--text-secondary); padding: 2.5rem 0; font-size: 0.85rem; display: flex; flex-direction: column; align-items: center; gap: 0.75rem;">
                    <div style="width: 24px; height: 24px; border: 2px solid var(--accent); border-top-color: transparent; border-radius: 50%; animation: spin 1s linear infinite;"></div>
                    <span>Recherche des réseaux à proximité (nmcli)...</span>
                </div>`;
                
            knownContainer.innerHTML = `
                <div style="text-align: center; color: var(--text-secondary); padding: 1.5rem 0; font-size: 0.85rem; display: flex; flex-direction: column; align-items: center; gap: 0.5rem;">
                    <div style="width: 16px; height: 16px; border: 2px solid var(--accent); border-top-color: transparent; border-radius: 50%; animation: spin 1s linear infinite;"></div>
                    <span>Actualisation...</span>
                </div>`;
                
            const btn = document.getElementById('btn-wifi-scan');
            if (btn) btn.disabled = true;
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "scan_wifi" }));
            } else {
                listContainer.innerHTML = `<div style="text-align: center; color: var(--danger); padding: 2rem 0; font-size: 0.85rem;">Erreur : WebSocket déconnecté.</div>`;
                knownContainer.innerHTML = `<div style="text-align: center; color: var(--danger); padding: 1rem 0; font-size: 0.85rem;">Erreur.</div>`;
                if (btn) btn.disabled = false;
            }
        }

        window.wifiPasswords = {};
        window.wifiCurrentSsid = '';

        function handleWifiScanError(payload) {
            const errMsg = payload.error || "Erreur inconnue";
            const iface = payload.interface || "wlan0";
            const mgr = payload.manager || "inconnu";
            const known = Array.isArray(payload.known_ssids) ? payload.known_ssids : [];
            const cur = payload.current_ssid || "";
            const listContainer = document.getElementById('wifi-list-container');
            const knownContainer = document.getElementById('wifi-known-container');
            const btn = document.getElementById('btn-wifi-scan');
            if (btn) btn.disabled = false;
            if (listContainer) {
                listContainer.innerHTML = `<div style="text-align: center; color: var(--danger); padding: 2rem 0; font-size: 0.85rem; line-height: 1.5;">⚠️ Scan WiFi échoué<br><small style="color: var(--text-secondary);">${errMsg}<br>Interface : ${iface} · Gestionnaire : ${mgr}</small></div>`;
            }
            if (knownContainer) {
                knownContainer.innerHTML = `<div style="text-align: center; color: var(--text-secondary); padding: 1rem 0; font-size: 0.85rem;">Surveillance WiFi indisponible.</div>`;
            }
            window.wifiPasswords = payload.known_passwords || {};
            window.wifiCurrentSsid = cur;
            // Ré-afficher les réseaux connus (si fournis) même en cas d'échec du scan
            try { displayWifiNetworks([], known, payload.known_passwords || {}, cur); } catch(e) { /* noop */ }
        }

        function displayWifiNetworks(networks, knownSsids = [], knownPasswords = {}, currentSsid = '') {
            const listContainer = document.getElementById('wifi-list-container');
            const knownContainer = document.getElementById('wifi-known-container');
            const btn = document.getElementById('btn-wifi-scan');
            if (btn) btn.disabled = false;
            
            listContainer.innerHTML = '';
            knownContainer.innerHTML = '';
            
            window.wifiPasswords = knownPasswords || {};
            window.wifiCurrentSsid = currentSsid || '';
            
            if (!knownSsids) knownSsids = [];
            if (!networks) networks = [];
            
            // Sort scanned networks by signal strength
            networks.sort((a, b) => {
                const sigA = parseInt(a.signal) || 0;
                const sigB = parseInt(b.signal) || 0;
                return sigB - sigA;
            });
            
            // Display known networks
            if (knownSsids.length === 0 && !window.wifiCurrentSsid) {
                knownContainer.innerHTML = `<div style="text-align: center; color: var(--text-secondary); padding: 1rem 0; font-size: 0.8rem;">Aucun réseau enregistré configuré sur le robot.</div>`;
            } else {
                // Ensure current connected SSID is in the list of known SSIDs (if it isn't already)
                let allKnown = [...knownSsids];
                if (window.wifiCurrentSsid && !allKnown.includes(window.wifiCurrentSsid)) {
                    allKnown.unshift(window.wifiCurrentSsid);
                }
                
                // Sort so currently connected SSID is always FIRST
                allKnown.sort((a, b) => {
                    if (a === window.wifiCurrentSsid) return -1;
                    if (b === window.wifiCurrentSsid) return 1;
                    return 0;
                });
                
                allKnown.forEach(ssid => {
                    const scannedNet = networks.find(n => n.ssid === ssid);
                    const inRange = !!scannedNet;
                    const isConnected = (ssid === window.wifiCurrentSsid);
                    
                    const item = document.createElement('div');
                    item.style.cssText = 'display: flex; justify-content: space-between; align-items: center; padding: 0.65rem 1rem; border-bottom: 1px solid var(--border-color); cursor: pointer; transition: background 0.2s ease; margin-bottom: 0.25rem; border-radius: 6px; position: relative;';
                    
                    if (isConnected) {
                        item.style.backgroundColor = 'rgba(76, 175, 80, 0.08)';
                        item.style.border = '1px solid rgba(76, 175, 80, 0.3)';
                    } else {
                        item.style.backgroundColor = 'rgba(255, 111, 97, 0.03)';
                        item.style.border = '1px solid rgba(255, 111, 97, 0.15)';
                    }
                    
                    const signalText = inRange ? `${scannedNet.signal}%` : (isConnected ? 'Connecté' : 'Hors de portée');
                    const signalColor = isConnected ? 'var(--success)' : (inRange ? 'var(--success)' : 'var(--text-secondary)');
                    
                    let badge = '';
                    if (isConnected) {
                        badge = `<span style="font-size:0.65rem; background:rgba(76,175,80,0.2); color: #4CAF50; padding:0.1rem 0.35rem; border-radius:4px; margin-left:0.35rem; font-weight:700; text-transform:uppercase; letter-spacing:0.5px;">✓ Connecté</span>`;
                    } else {
                        badge = `<span style="font-size:0.65rem; background:rgba(255,111,97,0.15); color: var(--accent); padding:0.1rem 0.35rem; border-radius:4px; margin-left:0.35rem; font-weight:600;">Enregistré</span>`;
                    }
                    
                    item.innerHTML = `
                        <div style="flex: 1;">
                            <span style="font-weight: 600; font-size: 0.9rem; display: block; color: ${isConnected ? '#4CAF50' : 'var(--accent)'};">${ssid} ${badge}</span>
                            <span style="font-size: 0.7rem; color: var(--text-secondary);">${inRange ? (scannedNet.bssid + ' • ' + scannedNet.security) : 'Profil de connexion sauvegardé'}</span>
                        </div>
                        <div style="display:flex; align-items:center; gap:0.5rem;">
                            <span style="font-size: 0.85rem; font-weight: bold; color: ${signalColor};">${signalText}</span>
                            <button class="btn btn-secondary" style="padding: 0.25rem 0.5rem; font-size: 0.7rem; border-color: var(--danger); color: var(--danger); background: transparent;" onclick="event.stopPropagation(); forgetWifiNetwork('${ssid}')">🗑️ Oublier</button>
                        </div>
                    `;
                    
                    const isSecureNet = scannedNet ? (scannedNet.security && scannedNet.security.trim() !== "" && scannedNet.security !== "--" && scannedNet.security.toLowerCase() !== "open") : true;
                    item.onclick = () => selectWifiNetwork(ssid, isSecureNet, true);
                    knownContainer.appendChild(item);
                });
            }
            
            // Display other scanned networks (excluding the known ones)
            const otherNetworks = networks.filter(n => !knownSsids.includes(n.ssid) && n.ssid !== window.wifiCurrentSsid);
            
            if (otherNetworks.length === 0) {
                listContainer.innerHTML = `<div style="text-align: center; color: var(--text-secondary); padding: 1.5rem 0; font-size: 0.8rem;">Aucun autre réseau WiFi à proximité.</div>`;
            } else {
                otherNetworks.forEach(net => {
                    const item = document.createElement('div');
                    item.style.cssText = 'display: flex; justify-content: space-between; align-items: center; padding: 0.65rem 1rem; border-bottom: 1px solid var(--border-color); cursor: pointer; transition: background 0.2s ease;';
                    
                    const isSecure = net.security && net.security.trim() !== "" && net.security !== "--" && net.security.toLowerCase() !== "open";
                    const lockIcon = isSecure ? '🔒' : '🔓';
                    
                    item.innerHTML = `
                        <div>
                            <span style="font-weight: 600; font-size: 0.9rem; display: block;">${net.ssid}</span>
                            <span style="font-size: 0.7rem; color: var(--text-secondary);">${net.bssid} • ${net.security}</span>
                        </div>
                        <div style="display:flex; align-items:center; gap:0.5rem;">
                            <span style="font-size: 0.8rem;">${lockIcon}</span>
                            <span style="font-size: 0.85rem; font-weight: bold; color: var(--accent);">${net.signal}%</span>
                        </div>
                    `;
                    
                    item.onclick = () => selectWifiNetwork(net.ssid, isSecure);
                    listContainer.appendChild(item);
                });
            }
        }

        function selectWifiNetwork(ssid, isSecure, isKnown = false) {
            document.getElementById('form-wifi-ssid').value = ssid;
            document.getElementById('wifi-selected-ssid-label').textContent = ssid;
            
            const pwdGroup = document.getElementById('wifi-password-group');
            const pwdInput = document.getElementById('form-wifi-password');
            const forgetBtn = document.getElementById('btn-wifi-forget-form');
            
            if (isKnown) {
                forgetBtn.style.display = 'inline-block';
                const savedPwd = window.wifiPasswords[ssid] || '';
                pwdInput.value = savedPwd;
                pwdInput.type = 'text'; // Show saved password clearly
                if (isSecure) {
                    pwdGroup.style.display = 'block';
                    pwdInput.placeholder = 'Mot de passe enregistré';
                } else {
                    pwdGroup.style.display = 'none';
                    pwdInput.placeholder = '';
                }
            } else {
                forgetBtn.style.display = 'none';
                pwdInput.value = '';
                pwdInput.type = 'password'; // Password mask for new network
                if (isSecure) {
                    pwdGroup.style.display = 'block';
                    pwdInput.placeholder = 'Mot de passe';
                } else {
                    pwdGroup.style.display = 'none';
                    pwdInput.placeholder = '';
                }
            }
            
            document.getElementById('wifi-connect-form').style.display = 'block';
        }

        function cancelWifiConnection() {
            document.getElementById('wifi-connect-form').style.display = 'none';
        }

        function handleWifiConnectSubmit(e) {
            e.preventDefault();
            const ssid = document.getElementById('form-wifi-ssid').value;
            const password = document.getElementById('form-wifi-password').value;
            
            const submitBtn = e.target.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.textContent = 'Connexion en cours...';
            }
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({
                    type: "connect_wifi",
                    ssid: ssid,
                    password: password
                }));
            } else {
                alert("WebSocket déconnecté.");
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Se connecter au WiFi';
                }
            }
        }

        function handleWifiConnectResult(res) {
            const submitBtn = document.querySelector('#wifi-connect-form button[type="submit"]');
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.textContent = 'Se connecter au WiFi';
            }
            
            if (res.status === 'success') {
                alert("Succès : " + res.message);
                closeWifiModal();
            } else {
                alert("Erreur de connexion : " + res.message);
            }
        }

        function forgetWifiNetwork(ssid) {
            if (confirm(`Êtes-vous sûr de vouloir oublier le réseau WiFi "${ssid}" sur le robot ?`)) {
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    appWs.send(JSON.stringify({ type: "forget_wifi", ssid: ssid }));
                } else {
                    alert("WebSocket déconnecté.");
                }
            }
        }

        function handleForgetFromForm() {
            const ssid = document.getElementById('form-wifi-ssid').value;
            if (ssid) {
                forgetWifiNetwork(ssid);
            }
        }


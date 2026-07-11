        async function loadAccounts() {
            try {
                const accountsRes = await fetch('/accounts', { headers: { 'X-API-Token': apiToken } });
                const mygesRes = await fetch('/myges', { headers: { 'X-API-Token': apiToken } });
                

                if (accountsRes.ok) {
                    const accounts = await accountsRes.json();
                    accountsCached = accounts;
                    

                    let mygesList = {};
                    if (mygesRes.ok) {
                        mygesList = await mygesRes.json();
                    }


                    const container = document.getElementById('users-container');
                    container.innerHTML = '';


                    const keys = Object.keys(accounts);
                    if (keys.length === 0) {
                        container.innerHTML = `
                            <div style="grid-column: 1/-1; text-align: center; padding: 3rem; color: var(--text-secondary);">
                                Aucun compte utilisateur configuré.
                            </div>`;
                        return;
                    }


                    for (const fullName of keys) {
                        const u = accounts[fullName];
                        const initials = ((u.first_name ? u.first_name[0] : '') + (u.last_name ? u.last_name[0] : '')).toUpperCase() || 'U';
                        const adminClass = u.is_admin ? 'admin' : '';
                        const adminLabel = u.is_admin ? 'Administrateur' : 'Utilisateur';
                        

                        const mygesCreds = mygesList[fullName];
                        const mygesBadge = mygesCreds 
                            ? `<span class="status-badge active" style="font-size: 0.75rem;">✅ MyGES : ${mygesCreds.username}</span>`
                            : `<span class="status-badge" style="font-size: 0.75rem; background-color: rgba(225, 29, 72, 0.05); color: var(--danger); border: 1px solid rgba(225, 29, 72, 0.15)">❌ MyGES non configuré</span>`;


                        const card = document.createElement('div');
                        card.className = 'user-card';
                        card.innerHTML = `
                            <div>
                                <div class="user-header">
                                    <div class="user-info-meta">
                                        <div class="user-avatar">${initials}</div>
                                        <div class="user-title-box">
                                            <h3>${u.first_name} ${u.last_name}</h3>
                                            <p>@${u.pseudo || 'sans-pseudo'}</p>
                                        </div>
                                    </div>
                                    <span class="user-badge ${adminClass}">${adminLabel}</span>
                                </div>
                                <div class="user-details">
                                    <div class="user-detail-item">
                                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>
                                        <span>${u.email}</span>
                                    </div>
                                    <div class="user-detail-item">
                                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 9.24z"/></svg>
                                        <span>${u.phone || 'Non renseigné'}</span>
                                    </div>
                                    <div style="margin-top: 0.5rem;">
                                        ${mygesBadge}
                                    </div>
                                </div>
                            </div>
                            <div class="user-actions">
                                <button class="btn btn-secondary" style="flex: 1;" onclick="openEditUserModal('${fullName}')">Modifier</button>
                                <button class="btn btn-secondary" onclick="openMygesModal('${fullName}')" title="Identifiants MyGES">MyGES</button>
                                <button class="btn btn-danger" onclick="deleteUser('${fullName}')">Supprimer</button>
                            </div>
                        `;
                        container.appendChild(card);
                    }
                }
            } catch (e) {
                console.error("Load accounts error:", e);
            }
        }


        async function deleteUser(fullName) {
            if (!confirm(`Voulez-vous vraiment supprimer le compte de ${fullName} ?\n(Cela supprimera également ses identifiants MyGES et ses photos de visage)`)) return;
            try {
                const res = await fetch(`/accounts/${encodeURIComponent(fullName)}`, {
                    method: 'DELETE',
                    headers: { 'X-API-Token': apiToken }
                });
                if (res.ok) {
                    loadAccounts();
                } else {
                    alert('Erreur lors de la suppression.');
                }
            } catch (e) {
                alert('Erreur de connexion.');
            }
        }


        // Modals Accounts
        function openAddUserModal() {
            document.getElementById('modal-user-title').textContent = "Ajouter un Compte";
            document.getElementById('form-old-fullname').value = '';
            document.getElementById('form-firstname').value = '';
            document.getElementById('form-lastname').value = '';
            document.getElementById('form-firstname').disabled = false;
            document.getElementById('form-lastname').disabled = false;
            document.getElementById('form-pseudo').value = '';
            document.getElementById('form-email').value = '';
            document.getElementById('form-phone').value = '';
            document.getElementById('form-password').value = '';
            document.getElementById('form-preferences').value = '{}';
            document.getElementById('form-is-admin').checked = false;
            

            const m = document.getElementById('userModal');
            m.style.position = 'fixed';
            m.style.top = '0';
            m.style.left = '0';
            m.style.right = '0';
            m.style.bottom = '0';
            m.style.display = 'flex';
            m.style.opacity = '1';
            m.style.pointerEvents = 'auto';
            m.style.zIndex = '100';
            m.classList.add('active');
        }


        function openEditUserModal(fullName) {
            const u = accountsCached[fullName];
            if (!u) return;


            document.getElementById('modal-user-title').textContent = `Modifier le profil`;
            document.getElementById('form-old-fullname').value = fullName;
            document.getElementById('form-firstname').value = u.first_name || '';
            document.getElementById('form-lastname').value = u.last_name || '';
            document.getElementById('form-firstname').disabled = true;
            document.getElementById('form-lastname').disabled = true;
            document.getElementById('form-pseudo').value = u.pseudo || '';
            document.getElementById('form-email').value = u.email || '';
            document.getElementById('form-phone').value = u.phone || '';
            document.getElementById('form-password').value = '';
            document.getElementById('form-preferences').value = JSON.stringify(u.preferences || {}, null, 2);
            document.getElementById('form-is-admin').checked = u.is_admin || false;


            const m2 = document.getElementById('userModal');
            m2.style.position = 'fixed';
            m2.style.top = '0';
            m2.style.left = '0';
            m2.style.right = '0';
            m2.style.bottom = '0';
            m2.style.display = 'flex';
            m2.style.opacity = '1';
            m2.style.pointerEvents = 'auto';
            m2.style.zIndex = '100';
            m2.classList.add('active');
        }


        function closeUserModal() {
            const m = document.getElementById('userModal');
            m.style.position = '';
            m.style.top = '';
            m.style.left = '';
            m.style.right = '';
            m.style.bottom = '';
            m.style.display = '';
            m.style.opacity = '';
            m.style.pointerEvents = '';
            m.style.zIndex = '';
            m.classList.remove('active');
        }


        function closeUserModalOnClick(e) {
            if (e.target === document.getElementById('userModal')) closeUserModal();
        }


        async function handleUserSubmit(e) {
            e.preventDefault();
            const firstName = document.getElementById('form-firstname').value.trim();
            const lastName = document.getElementById('form-lastname').value.trim();
            const pseudo = document.getElementById('form-pseudo').value.trim();
            const email = document.getElementById('form-email').value.trim();
            const phone = document.getElementById('form-phone').value.trim();
            const password = document.getElementById('form-password').value;
            const isAdmin = document.getElementById('form-is-admin').checked;


            let preferences = {};
            const prefVal = document.getElementById('form-preferences').value.trim();
            if (prefVal) {
                try {
                    preferences = JSON.parse(prefVal);
                } catch (err) {
                    alert("Format JSON invalide pour les préférences.");
                    return;
                }
            }


            const payload = {
                first_name: firstName,
                last_name: lastName,
                pseudo: pseudo,
                email: email,
                phone: phone,
                is_admin: isAdmin,
                preferences: preferences
            };


            if (password) {
                payload.password = password;
            }


            try {
                const res = await fetch('/accounts', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Token': apiToken
                    },
                    body: JSON.stringify(payload)
                });


                if (res.ok) {
                    closeUserModal();
                    loadAccounts();
                } else {
                    const err = await res.text();
                    alert(`Erreur lors de la sauvegarde : ${err}`);
                }
            } catch (e) {
                alert('Erreur de réseau.');
            }
        }


        // Modals MyGES
        function openMygesModal(name) {
            document.getElementById('myges-modal-username').textContent = name;
            document.getElementById('form-myges-name').value = name;
            document.getElementById('form-myges-username').value = '';
            document.getElementById('form-myges-password').value = '';
            

            document.getElementById('mygesModal').classList.add('active');
        }


        function closeMygesModal() {
            document.getElementById('mygesModal').classList.remove('active');
        }


        function closeMygesModalOnClick(e) {
            if (e.target === document.getElementById('mygesModal')) closeMygesModal();
        }


        async function handleMygesTest() {
            const resultDiv = document.getElementById('myges-test-result');
            const btn = document.getElementById('btn-myges-test');
            const username = document.getElementById('form-myges-username').value.trim();
            const password = document.getElementById('form-myges-password').value;
            

            if (!username || !password) {
                resultDiv.style.display = 'block';
                resultDiv.style.background = 'rgba(239,68,68,0.1)';
                resultDiv.style.color = '#ef4444';
                resultDiv.innerHTML = 'Veuillez remplir les deux champs.';
                return;
            }
            

            // Show loading state
            btn.disabled = true;
            btn.innerHTML = '⏳ Test en cours...';
            resultDiv.style.display = 'block';
            resultDiv.style.background = 'rgba(99,102,241,0.1)';
            resultDiv.style.color = '#6366f1';
            resultDiv.innerHTML = 'Connexion en cours...';
            

            try {
                const res = await fetch('/myges/test', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Token': apiToken
                    },
                    body: JSON.stringify({ username, password })
                });
                const data = await res.json();
                

                if (data.status === 'success') {
                    resultDiv.style.background = 'rgba(34,197,94,0.1)';
                    resultDiv.style.color = '#22c55e';
                    resultDiv.innerHTML = '✅ ' + data.message;
                } else {
                    resultDiv.style.background = 'rgba(239,68,68,0.1)';
                    resultDiv.style.color = '#ef4444';
                    resultDiv.innerHTML = '❌ ' + data.message;
                }
            } catch (e) {
                resultDiv.style.background = 'rgba(239,68,68,0.1)';
                resultDiv.style.color = '#ef4444';
                resultDiv.innerHTML = '❌ Erreur réseau.';
            } finally {
                btn.disabled = false;
                btn.innerHTML = '🔍 Tester la connexion';
            }
        }


        async function handleMygesTest() {
            const resultDiv = document.getElementById('myges-test-result');
            const btn = document.getElementById('btn-myges-test');
            const username = document.getElementById('form-myges-username').value.trim();
            const password = document.getElementById('form-myges-password').value;
            

            if (!username || !password) {
                resultDiv.style.display = 'block';
                resultDiv.style.background = 'rgba(239,68,68,0.1)';
                resultDiv.style.color = '#ef4444';
                resultDiv.textContent = 'Veuillez remplir les deux champs.';
                return;
            }
            

            btn.disabled = true;
            btn.textContent = 'Test en cours...';
            resultDiv.style.display = 'block';
            resultDiv.style.background = 'rgba(99,102,241,0.1)';
            resultDiv.style.color = '#6366f1';
            resultDiv.textContent = 'Connexion en cours...';
            

            try {
                const res = await fetch('/myges/test', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Token': apiToken
                    },
                    body: JSON.stringify({ username, password })
                });
                const data = await res.json();
                

                if (data.status === 'success') {
                    resultDiv.style.background = 'rgba(34,197,94,0.1)';
                    resultDiv.style.color = '#22c55e';
                    resultDiv.textContent = data.message;
                } else {
                    resultDiv.style.background = 'rgba(239,68,68,0.1)';
                    resultDiv.style.color = '#ef4444';
                    resultDiv.textContent = data.message;
                }
            } catch (e) {
                resultDiv.style.background = 'rgba(239,68,68,0.1)';
                resultDiv.style.color = '#ef4444';
                resultDiv.textContent = 'Erreur réseau.';
            } finally {
                btn.disabled = false;
                btn.innerHTML = '&#128269; Tester la connexion';
            }
        }


        async function handleMygesSubmit(e) {
            e.preventDefault();
            const name = document.getElementById('form-myges-name').value;
            const username = document.getElementById('form-myges-username').value.trim();
            const password = document.getElementById('form-myges-password').value;
            const resultDiv = document.getElementById('myges-test-result');


            if (!username || !password) {
                resultDiv.style.display = 'block';
                resultDiv.style.background = 'rgba(239,68,68,0.1)';
                resultDiv.style.color = '#ef4444';
                resultDiv.textContent = 'Veuillez remplir les deux champs.';
                return;
            }


            // Show testing state
            resultDiv.style.display = 'block';
            resultDiv.style.background = 'rgba(99,102,241,0.1)';
            resultDiv.style.color = '#6366f1';
            resultDiv.textContent = 'Test des identifiants en cours...';


            try {
                // Step 1: Test credentials
                const testRes = await fetch('/myges/test', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Token': apiToken
                    },
                    body: JSON.stringify({ username, password })
                });
                const testData = await testRes.json();


                if (testData.status !== 'success') {
                    resultDiv.style.background = 'rgba(239,68,68,0.1)';
                    resultDiv.style.color = '#ef4444';
                    resultDiv.textContent = '❌ ' + testData.message;
                    return;
                }


                // Step 2: Save credentials
                const saveRes = await fetch(`/myges?name=${encodeURIComponent(name)}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Token': apiToken
                    },
                    body: JSON.stringify({ username, password })
                });


                if (saveRes.ok) {
                    resultDiv.style.background = 'rgba(34,197,94,0.1)';
                    resultDiv.style.color = '#22c55e';
                    resultDiv.textContent = '✅ Identifiants valides et sauvegardés !';
                    setTimeout(() => { closeMygesModal(); loadAccounts(); }, 800);
                } else {
                    resultDiv.style.background = 'rgba(239,68,68,0.1)';
                    resultDiv.style.color = '#ef4444';
                    resultDiv.textContent = '❌ Erreur lors de la sauvegarde.';
                }
            } catch (e) {
                resultDiv.style.background = 'rgba(239,68,68,0.1)';
                resultDiv.style.color = '#ef4444';
                resultDiv.textContent = '❌ Erreur réseau.';
            }
        }


        // ─── FACES GALLERY ───────────────────────────────────────────────────


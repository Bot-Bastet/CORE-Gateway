from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Security, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
import os
import uuid
import time
import json
import hashlib
from pathlib import Path
from typing import Optional
from myges_api import MyGesAPI

# ─── Config ───────────────────────────────────────────────────────────────────
FACES_DIR = Path(os.getenv("FACES_DIR", "/data/faces"))
DATA_DIR = Path("/data") # general data dir for state/myges
META_FILE = FACES_DIR / "meta.json"
MYGES_FILE = DATA_DIR / "myges.json"
STATE_FILE = DATA_DIR / "core_state.json"
USERS_FILE = DATA_DIR / "users.json"

FACES_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

API_TOKEN = os.getenv("API_TOKEN", "your-api-token-here")
api_key_header = APIKeyHeader(name="X-API-Token", auto_error=False)

app = FastAPI(
    title="Bastet Gateway API",
    description="API Gateway pour le robot Bastet (Faces, MyGES, Core State). Protégée par Token.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Auth ─────────────────────────────────────────────────────────────────────

def verify_token(api_key: str = Security(api_key_header)):
    if api_key != API_TOKEN:
        raise HTTPException(status_code=403, detail="Accès refusé. X-API-Token invalide ou manquant.")
    return api_key

def verify_token_optional(api_key: str = Security(api_key_header)):
    """Pour les routes où on gère l'auth différemment (ex. Dashboard)"""
    return api_key

# ─── Models ───────────────────────────────────────────────────────────────────

class MyGESCredentials(BaseModel):
    username: str
    password: str

class CoreState(BaseModel):
    seen_person: Optional[str] = None
    seen_objects: list[str] = []
    last_chat: list[dict] = []
    robot_status: str = "idle"

class AccountInfo(BaseModel):
    email: str
    pseudo: str
    last_name: str
    first_name: str
    phone: str
    is_admin: bool = False

# ─── Nettoyage & Helpers ──────────────────────────────────────────────────────

def load_json(path: Path, default=None):
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return default if default is not None else []

def save_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def find_entry(face_id: str) -> Optional[dict]:
    return next((e for e in load_json(META_FILE) if e["id"] == face_id), None)

def cleanup_duplicates():
    """Vérifie et supprime automatiquement les doublons d'image basés sur le hash MD5."""
    meta = load_json(META_FILE)
    if not meta: return
    
    seen_hashes = set()
    new_meta = []
    modified = False
    
    for entry in meta:
        path = FACES_DIR / entry["filename"]
        if not path.exists():
            modified = True
            continue
            
        with open(path, "rb") as f:
            file_hash = hashlib.md5(f.read()).hexdigest()
            
        unique_id = f"{entry['name']}_{file_hash}"
        
        if unique_id in seen_hashes:
            path.unlink() # Suppression automatique du fichier en double
            modified = True
        else:
            seen_hashes.add(unique_id)
            entry["hash"] = file_hash
            new_meta.append(entry)
            
    if modified:
        save_json(META_FILE, new_meta)
        print("🧹 Nettoyage des doublons terminé.")

@app.on_event("startup")
def startup_event():
    cleanup_duplicates()

# ─── WebSockets Hub (Routage Temps-Réel) ──────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {
            "robot": [],
            "node": [],
            "app": []
        }

    async def connect(self, websocket: WebSocket, client_type: str):
        await websocket.accept()
        if client_type in self.active_connections:
            self.active_connections[client_type].append(websocket)

    def disconnect(self, websocket: WebSocket, client_type: str):
        if client_type in self.active_connections and websocket in self.active_connections[client_type]:
            self.active_connections[client_type].remove(websocket)

    async def broadcast(self, message: str, target_client_type: str):
        for connection in self.active_connections.get(target_client_type, []):
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

@app.websocket("/ws/robot")
async def websocket_robot(websocket: WebSocket):
    await manager.connect(websocket, "robot")
    try:
        while True:
            data = await websocket.receive_text()
            
            # Injection contextuelle (Emploi du temps MyGES)
            try:
                msg_json = json.loads(data)
                if msg_json.get("type") == "chat":
                    comptes = load_json(MYGES_FILE, default={})
                    if comptes:
                        user_name = list(comptes.keys())[0]
                        creds = comptes[user_name]
                        api = MyGesAPI(creds["username"], creds["password"])
                        agenda_text = api.get_upcoming_agenda_text(days=7)
                        msg_json["context"] = f"[CONTEXTE CACHÉ - Agenda de {user_name} pour les 7 prochains jours] : \n{agenda_text}"
                        data = json.dumps(msg_json)
                        print(f"OK: Contexte injecte pour {user_name}.")
            except Exception as e:
                print(f"Erreur injection contexte : {e}")
                
            # Routage automatique du robot vers le noeud
            await manager.broadcast(data, "node")
    except WebSocketDisconnect:
        manager.disconnect(websocket, "robot")

@app.websocket("/ws/node")
async def websocket_node(websocket: WebSocket):
    await manager.connect(websocket, "node")
    try:
        while True:
            data = await websocket.receive_text()
            # Routage de la réponse du noeud (LLM streamé ou audio TTS) vers le robot
            await manager.broadcast(data, "robot")
    except WebSocketDisconnect:
        manager.disconnect(websocket, "node")

@app.websocket("/ws/app")
async def websocket_app(websocket: WebSocket):
    await manager.connect(websocket, "app")
    try:
        while True:
            data = await websocket.receive_text()
            # Routage des commandes de l'app mobile vers le robot
            await manager.broadcast(data, "robot")
    except WebSocketDisconnect:
        manager.disconnect(websocket, "app")

# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
def dashboard():
    """Dashboard HTML listant les visages enregistrés. Requiert le token JS."""
    faces = load_json(META_FILE)
    grouped_faces = {}
    for f in faces:
        grouped_faces.setdefault(f['name'], []).append(f)
        
    rows = ""
    for name, user_faces in grouped_faces.items():
        clean_name = name.replace(" ", "_")
        # Main row for User
        rows += f"""
        <tr style="cursor:pointer; background:#1a1a24; border-top: 2px solid #2d2d3d;" onclick="toggleDetails('{clean_name}')">
          <td colspan="2" style="font-size:1.1rem; padding-left:1rem;">
            <b>{name}</b> 
            <span style="font-size:0.8rem; color:#94a3b8; margin-left:1rem;">{len(user_faces)} photo{"s" if len(user_faces)>1 else ""}</span>
          </td>
          <td colspan="2"></td>
          <td id="mg-status-{clean_name}" style="font-weight:600; font-size:.9rem;"><span style="color:#475569">Chargement MyGES...</span></td>
          <td style="text-align:right; font-size:1.2rem; color:#64748b;">▼</td>
        </tr>
        """
        # Sub-rows for each photo (hidden by default)
        for f in user_faces:
            rows += f"""
            <tr class="detail-row-{clean_name}" style="display:none; background:#12121a;">
              <td style="padding-left: 2rem;"><img src="#" data-src="/faces/{f['id']}/image" height="60" class="lazy-load" style="border-radius:8px;object-fit:cover;"/></td>
              <td style="color:#888">{f['filename']}</td>
              <td style="color:#888">{f['uploaded_at']}</td>
              <td colspan="2"></td>
              <td style="text-align:right;">
                <span style="color:#f87171;cursor:pointer;font-size:0.85rem;" onclick="del('{f['id']}')">✕ Supprimer</span>
              </td>
            </tr>"""


    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Bastet — Face Server</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Inter', system-ui, sans-serif; background: #0f0f14; color: #e2e8f0; min-height: 100vh; padding: 2rem; }}
    header {{ display: flex; align-items: center; gap: 1rem; margin-bottom: 2rem; }}
    header h1 {{ font-size: 1.8rem; font-weight: 700; }}
    header span {{ background: #4ade80; color: #0f0f14; border-radius: 999px; padding: 2px 10px; font-size:.8rem; font-weight: 700; }}
    .card {{ background: #1a1a24; border: 1px solid #2d2d3d; border-radius: 12px; overflow: hidden; }}
    table {{ width: 100%; border-collapse: collapse; }}
    thead tr {{ background: #12121a; }}
    th, td {{ padding: .85rem 1.2rem; text-align: left; border-bottom: 1px solid #2d2d3d; font-size:.9rem; }}
    th {{ color: #94a3b8; font-weight: 600; text-transform: uppercase; font-size:.75rem; letter-spacing:.05em; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #21212e; }}
    .empty {{ text-align:center; padding: 3rem; color: #64748b; }}
    .upload-section {{ margin-bottom: 1.5rem; background: #1a1a24; border: 1px solid #2d2d3d; border-radius: 12px; padding: 1.5rem; }}
    .upload-section h2 {{ font-size: 1rem; margin-bottom: 1rem; color: #94a3b8; text-transform:uppercase; letter-spacing:.05em; }}
    .form-row {{ display: flex; gap: .75rem; align-items: center; flex-wrap: wrap; }}
    input[type=text], input[type=password], input[type=file] {{ background: #0f0f14; border: 1px solid #2d2d3d; border-radius: 8px; color: #e2e8f0; padding: .5rem .9rem; font-size: .9rem; }}
    input[type=text] {{ flex: 1; min-width: 180px; }}
    button {{ background: #4ade80; color: #0f0f14; border: none; border-radius: 8px; padding: .55rem 1.2rem; font-weight: 700; cursor: pointer; font-size: .9rem; }}
    button:hover {{ background: #22c55e; }}
    #tokenOverlay {{ position: fixed; inset: 0; background: rgba(0,0,0,0.9); display: flex; align-items: center; justify-content: center; z-index: 1000; }}
    .token-box {{ background: #1a1a24; padding: 2rem; border-radius: 12px; border: 1px solid #2d2d3d; text-align: center; }}
  </style>
</head>
<body>
  <div id="tokenOverlay">
    <div class="token-box">
      <h2 style="margin-bottom:1rem">Accès Sécurisé</h2>
      <input type="password" id="tokenInput" placeholder="X-API-Token" style="margin-bottom:1rem; width:100%"/>
      <button onclick="saveToken()">Déverrouiller</button>
    </div>
  </div>

  <header style="justify-content:space-between">
    <div style="display:flex; align-items:center; gap:1rem;">
      <h1>Bastet — API Gateway</h1>
      <span>{len(faces)} photo{"s" if len(faces) != 1 else ""}</span>
    </div>
    <button id="camBtnLarge" onclick="startStream()" style="background:#3b82f6; color:white; font-size:1rem; padding:0.75rem 1.5rem; display:flex; align-items:center; gap:0.5rem; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);">
      ▶ Afficher le Stream Caméra (WebRTC)
    </button>
  </header>

  <div class="upload-section" style="display:flex; flex-wrap:wrap; gap:2rem;">
    <!-- MyGES Section -->
    <div style="flex:1; min-width:300px;">
      <h2>Identifiants MyGES</h2>
      <div id="mygesBox" style="margin-top:1rem; padding:1rem; background:#12121a; border-radius:8px; border:1px solid #2d2d3d;">
        <span style="color:#64748b">Chargement...</span>
      </div>
    </div>
    
    <!-- Caméra Section (Player) -->
    <div id="videoContainer" style="display:none; flex:2; min-width:400px;">
      <h2>Vue du Robot (WebRTC en direct)</h2>
      <div style="margin-top:1rem; text-align:center; background:#12121a; border-radius:8px; padding:1rem; border:1px solid #2d2d3d;">
        <iframe id="videoPlayer" width="100%" height="400" frameborder="0" style="border-radius:8px; background:#000;"></iframe>
      </div>
    </div>
  </div>

  <div class="upload-section">
    <h2>Ajouter un visage</h2>
    <form id="uploadForm" class="form-row">
      <input type="text" id="nameInput" placeholder="Nom de la personne" required/>
      <input type="file" id="fileInput" accept="image/*" required/>
      <button type="submit">Upload</button>
    </form>
  </div>

  <div class="card">
    <table>
      <thead>
        <tr style="background:#12121a; font-size:0.8rem;">
          <th colspan="2">Utilisateur / Photo</th>
          <th>Fichier Original</th>
          <th>Date d'Upload</th>
          <th>Status MyGES</th>
          <th style="text-align:right">Actions</th>
        </tr>
      </thead>
      <tbody id="tbody">
        {"".join([rows]) if faces else '<tr><td colspan="6" class="empty">Aucun visage enregistré</td></tr>'}
      </tbody>
    </table>
  </div>

  <script>
    let apiToken = localStorage.getItem('bastet_api_token') || '';
    
    function checkToken() {{
      if(apiToken) {{
        document.getElementById('tokenOverlay').style.display = 'none';
        loadImages();
      }}
    }}
    
    function saveToken() {{
      apiToken = document.getElementById('tokenInput').value;
      localStorage.setItem('bastet_api_token', apiToken);
      checkToken();
    }}
    
    async function loadImages() {{
      const imgs = document.querySelectorAll('.lazy-load');
      for(let img of imgs) {{
        const url = img.getAttribute('data-src');
        try {{
          const res = await fetch(url, {{ headers: {{ 'X-API-Token': apiToken }} }});
          if(res.ok) {{
            const blob = await res.blob();
            img.src = URL.createObjectURL(blob);
          }} else if(res.status === 403) {{
            apiToken = ''; localStorage.removeItem('bastet_api_token');
            document.getElementById('tokenOverlay').style.display = 'flex';
            return;
          }}
        }} catch(e) {{}}
      }}
      
      // Load MyGES info
      try {{
        const mgRes = await fetch('/myges', {{ headers: {{ 'X-API-Token': apiToken }} }});
        const mgBox = document.getElementById('mygesBox');
        if(mgRes.ok) {{
          const allMg = await mgRes.json();
          const count = Object.keys(allMg).length;
          mgBox.innerHTML = `<div style="color:#4ade80;font-weight:700;">${{count}} compte(s) MyGES actif(s)</div>
                             <div style="font-size:.8rem;color:#94a3b8;margin-top:.5rem;">Synchronisé avec le robot</div>`;
          
          // Update status in table
          for(const [name, data] of Object.entries(allMg)) {{
            const cell = document.getElementById('mg-status-' + name.replace(' ', '_'));
            if(cell) cell.innerHTML = '<span style="color:#4ade80">✅ ' + data.username + '</span>';
          }}
          
          // Gray out non-configured users
          document.querySelectorAll('[id^="mg-status-"]').forEach(el => {{
              if(el.innerText.includes("Chargement...")) el.innerHTML = '<span style="color:#ef4444">❌ Absent</span>';
          }});

        }} else {{
          mgBox.innerHTML = `<span style="color:#64748b">Aucun compte configuré.</span>`;
          document.querySelectorAll('[id^="mg-status-"]').forEach(el => el.innerHTML = '<span style="color:#ef4444">❌ Absent</span>');
        }}
      }} catch(e) {{}}
    }}
    
    function startStream() {{
       const iframe = document.getElementById('videoPlayer');
       const container = document.getElementById('videoContainer');
       const btnLarge = document.getElementById('camBtnLarge');
       // Assuming WebRTC proxy is running correctly on gateway's IP at port 48889
       const host = window.location.hostname;
       iframe.src = `http://${{host}}:48889/cam1/`;
       container.style.display = 'block';
       btnLarge.style.display = 'none';
       iframe.scrollIntoView({{behavior: 'smooth'}});
    }}
    
    function toggleDetails(name) {{
       const rows = document.querySelectorAll('.detail-row-' + name);
       rows.forEach(r => {{
           r.style.display = (r.style.display === 'none') ? 'table-row' : 'none';
       }});
    }}

    document.getElementById('uploadForm').addEventListener('submit', async (e) => {{
      e.preventDefault();
      const name = document.getElementById('nameInput').value.trim();
      const file = document.getElementById('fileInput').files[0];
      if (!name || !file) return;
      const fd = new FormData();
      fd.append('name', name);
      fd.append('file', file);
      
      const res = await fetch('/faces/upload', {{ 
        method: 'POST', 
        headers: {{ 'X-API-Token': apiToken }},
        body: fd 
      }});
      if (res.ok) location.reload();
      else alert('Erreur upload: ' + await res.text());
    }});

    async function del(id) {{
      if (!confirm('Supprimer ce visage ?')) return;
      const res = await fetch('/faces/' + id, {{ 
        method: 'DELETE',
        headers: {{ 'X-API-Token': apiToken }}
      }});
      if (res.ok) location.reload();
      else alert('Erreur suppression');
    }}
    
    checkToken();
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


# ─── Faces API ────────────────────────────────────────────────────────────────

@app.post("/faces/upload", tags=["Faces"], summary="Upload une image", dependencies=[Depends(verify_token)])
async def upload_face(
    name: str = Query(..., description="Nom de la personne"),
    file: UploadFile = File(...),
):
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"Format non supporté : {ext}")

    meta = load_json(META_FILE)
    
    # Vérification de la limite de 8 photos par utilisateur
    user_photos = [e for e in meta if e["name"].lower() == name.lower()]
    if len(user_photos) >= 8:
        raise HTTPException(status_code=400, detail=f"Limite atteinte : Impossible d'ajouter plus de 8 photos pour {name}.")

    content = await file.read()
    file_hash = hashlib.md5(content).hexdigest()
    
    # Anti-doublon (même contenu (hash) et même user)
    for e in meta:
        if e["name"] == name and e.get("hash") == file_hash:
            return {"status": "already_exists", "face": e, "msg": "Image identique déjà présente."}
        # Fallback sur original_name si pas de hash
        if e["name"] == name and e.get("original_name") == file.filename and "hash" not in e:
            return {"status": "already_exists", "face": e, "msg": "Image avec le même nom déjà présente."}

    face_id = str(uuid.uuid4())
    dest = FACES_DIR / f"{face_id}{ext}"
    
    with open(dest, "wb") as f_out:
        f_out.write(content)

    entry = {
        "id": face_id,
        "name": name,
        "filename": f"{face_id}{ext}",
        "original_name": file.filename,
        "hash": file_hash,
        "size_bytes": len(content),

        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    meta.append(entry)
    save_json(META_FILE, meta)

    return {"status": "ok", "face": entry}

@app.get("/faces", tags=["Faces"], summary="Lister tous les visages", dependencies=[Depends(verify_token)])
def list_faces(name: Optional[str] = Query(None)):
    meta = load_json(META_FILE)
    if name:
        meta = [e for e in meta if name.lower() in e["name"].lower()]
    return {"count": len(meta), "faces": meta}

@app.get("/faces/{face_id}/image", tags=["Faces"], summary="Télécharger l'image", dependencies=[Depends(verify_token)])
def get_face_image(face_id: str):
    entry = find_entry(face_id)
    if not entry: raise HTTPException(status_code=404)
    path = FACES_DIR / entry["filename"]
    if not path.exists(): raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/*", filename=entry["original_name"])

@app.delete("/faces/{face_id}", tags=["Faces"], summary="Supprimer un visage", dependencies=[Depends(verify_token)])
def delete_face(face_id: str):
    meta = load_json(META_FILE)
    entry = next((e for e in meta if e["id"] == face_id), None)
    if not entry: raise HTTPException(status_code=404)

    path = FACES_DIR / entry["filename"]
    if path.exists(): path.unlink()

    meta = [e for e in meta if e["id"] != face_id]
    save_json(META_FILE, meta)
    return {"status": "deleted", "id": face_id}


# ─── MyGES Credentials ────────────────────────────────────────────────────────

@app.post("/myges", tags=["MyGES"], summary="Enregistrer id/mdp MyGES", dependencies=[Depends(verify_token)])
def save_myges(creds: MyGESCredentials, name: str = Query(..., description="Nom de l'utilisateur (ex: Teano)")):
    """Sauvegarde les identifiants MyGES fournis par l'app mobile, lisibles par le robot."""
    all_comptes = load_json(MYGES_FILE, default={})
    all_comptes[name] = {"username": creds.username, "password": creds.password, "updated_at": time.time()}
    save_json(MYGES_FILE, all_comptes)
    return {"status": "saved", "user": name}

@app.get("/myges", tags=["MyGES"], summary="Récupérer id/mdp MyGES", dependencies=[Depends(verify_token)])
def get_myges():
    """Le robot appelle ceci pour récupérer les id/mdp."""
    data = load_json(MYGES_FILE, default={})
    if not data:
        raise HTTPException(status_code=404, detail="No credentials stored")
    return data


# ─── CORE State ───────────────────────────────────────────────────────────────

@app.post("/accounts", tags=["Accounts"], summary="Créer ou MAJ un compte utilisateur", dependencies=[Depends(verify_token)])
def save_account(info: AccountInfo):
    users = load_json(USERS_FILE, default={})
    full_name = f"{info.first_name} {info.last_name}"
    users[full_name] = info.model_dump()
    save_json(USERS_FILE, users)
    return {"status": "saved", "user": full_name}

@app.get("/accounts", tags=["Accounts"], summary="Lister les comptes", dependencies=[Depends(verify_token)])
def get_accounts():
    return load_json(USERS_FILE, default={})

@app.post("/core/state", tags=["CORE State"], summary="Mettre à jour l'état du robot", dependencies=[Depends(verify_token)])
def update_state(state: CoreState):
    """Le robot publie son état courant (ce qu'il voit, le chat, etc)."""
    data = state.model_dump()
    data["updated_at"] = time.time()
    save_json(STATE_FILE, data)
    return {"status": "updated"}

@app.get("/core/state", tags=["CORE State"], summary="Récupérer l'état du robot", dependencies=[Depends(verify_token)])
def get_state():
    """L'app mobile appelle ceci pour afficher ce que fait/voit le robot."""
    return load_json(STATE_FILE, default={"robot_status": "offline"})

# ─── System ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"], summary="Health check")
def health():
    return {"status": "ok", "https": True}

"""
Auto-updater pour CORE-Gateway (Python / Docker sur serveur Linux).
Vérifie GitHub Releases et télécharge l'archive zip pour mettre à jour le code source.
Le code source est monté en volume bind-mount (./face-server → /app),
donc les fichiers extraits persistent sur l'hôte et survivent aux redémarrages Docker.
"""
import os
import shutil
import subprocess
import tempfile
import requests
import logging
import socket
from pathlib import Path

socket.setdefaulttimeout(30.0)

logger = logging.getLogger("auto_updater")

GITHUB_REPO = "Bot-Bastet/CORE-Gateway"
VERSION_FILE = Path(__file__).parent / "version.txt"


def get_current_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return "v0.0.0"



def apply_specific_release(version: str):
    """Applique une release specifique (rollback)."""
    print(f"[Updater] Demarrage rollback vers {version}...")
    repo = "Bot-Bastet/CORE-Gateway"
    url = f"https://api.github.com/repos/{repo}/releases/tags/{version}"
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        resp = requests.get(url, timeout=10, headers=headers)
        if resp.status_code != 200:
            print(f"[Updater] Release {version} introuvable (HTTP {resp.status_code})")
            return False
        release = resp.json()
        assets = release.get("assets", [])
        if not assets:
            print(f"[Updater] Aucun asset dans la release {version}")
            return False
        # Telecharger le premier asset (presume etre le code source)
        asset_url = assets[0].get("browser_download_url")
        if not asset_url:
            print("[Updater] Aucun download_url dans l'asset")
            return False
        print(f"[Updater] Telechargement de {asset_url}...")
        dl_resp = requests.get(asset_url, timeout=60, headers=headers)
        if dl_resp.status_code != 200:
            print(f"[Updater] Echec telechargement (HTTP {dl_resp.status_code})")
            return False
        # Extraire et remplacer le code
        import zipfile, io, shutil
        with zipfile.ZipFile(io.BytesIO(dl_resp.content)) as z:
            # Extraire dans /tmp
            tmp_dir = "/tmp/gateway_rollback"
            if os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir)
            z.extractall(tmp_dir)
            # Trouver le dossier extrait (peut etre un sous-dossier)
            extracted = tmp_dir
            items = os.listdir(tmp_dir)
            if len(items) == 1 and os.path.isdir(os.path.join(tmp_dir, items[0])):
                extracted = os.path.join(tmp_dir, items[0])
            # Remplacer les fichiers de l'application
            app_dir = os.path.dirname(os.path.abspath(__file__))
            for root, dirs, files in os.walk(extracted):
                rel = os.path.relpath(root, extracted)
                target = os.path.join(app_dir, rel) if rel != '.' else app_dir
                for f in files:
                    src_f = os.path.join(root, f)
                    dst_f = os.path.join(target, f)
                    if os.path.exists(dst_f):
                        shutil.copy2(src_f, dst_f)
        print(f"[Updater] Rollback vers {version} termine.")
        # Mettre a jour version.txt
        version_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.txt")
        with open(version_file, 'w') as vf:
            vf.write(version)
        return True
    except Exception as e:
        print(f"[Updater] Erreur rollback: {e}")
        import traceback
        traceback.print_exc()
        return False

def get_latest_release() -> dict | None:
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        resp = requests.get(url, timeout=5, headers={"Accept": "application/vnd.github+json"})
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning(f"[AutoUpdater] Impossible de joindre GitHub : {e}")
    return None


def _version_tuple(v: str) -> tuple:
    v = v.lstrip("v").split("-")[0]
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0, 0, 0)


def check_and_apply_update() -> bool:
    """
    Vérifie et applique la mise à jour si disponible.
    Le zip de la release contient la structure complète du repo (face-server/, docker-compose.yml, etc.).
    On extrait dans un dossier temporaire, puis on copie seulement face-server/* vers /app.
    Comme /app est un bind-mount vers ./face-server sur l'hôte, les fichiers persistent.
    Retourne True si une mise à jour a été appliquée (le service doit être redémarré).
    """
    current = get_current_version()
    release = get_latest_release()

    if not release:
        logger.info("[AutoUpdater] Impossible de vérifier les mises à jour.")
        return False

    latest_tag = release.get("tag_name", "v0.0.0")

    if _version_tuple(latest_tag) <= _version_tuple(current):
        logger.info(f"[AutoUpdater] A jour ({current}). Aucune mise à jour.")
        return False

    logger.info(f"[AutoUpdater] Nouvelle version disponible : {latest_tag} (actuelle : {current})")

    zip_url = None
    for asset in release.get("assets", []):
        if asset["name"].endswith(".zip"):
            zip_url = asset["browser_download_url"]
            break

    if not zip_url:
        zip_url = release.get("zipball_url")

    if not zip_url:
        logger.warning("[AutoUpdater] Aucune URL de téléchargement (asset ou zipball) trouvée.")
        return False

    try:
        import json

        app_dir = Path("/app")
        progress_file = Path("/data/gateway_update_state.json")

        def save_progress(status: str, percent: int):
            try:
                with open(progress_file, "w") as f_prog:
                    json.dump({"status": status, "percent": percent}, f_prog)
            except Exception:
                pass

        logger.info(f"[AutoUpdater] Téléchargement de {zip_url}...")
        resp = requests.get(zip_url, stream=True, timeout=120)
        total_size = int(resp.headers.get("content-length", 0))
        downloaded = 0

        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "update.zip"

            save_progress("downloading", 0)
            with open(zip_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = int((downloaded / total_size) * 100)
                        save_progress("downloading", percent)

            save_progress("extracting", 100)
            subprocess.run(["unzip", "-o", str(zip_path), "-d", tmp_dir], check=True)

            # Trouver le dossier face-server (gère les zips plats et les zips structurés/zipballs)
            extracted_face = None
            if (Path(tmp_dir) / "main.py").exists():
                extracted_face = Path(tmp_dir)
            else:
                for p in Path(tmp_dir).rglob("face-server"):
                    if p.is_dir():
                        extracted_face = p
                        break

            if not extracted_face:
                logger.warning("[AutoUpdater] Dossier face-server/ ou fichier main.py absent dans l'archive.")
                save_progress("failed", 0)
                return False

            for item in extracted_face.iterdir():
                if item.name == "__pycache__":
                    continue
                dest = app_dir / item.name
                if item.is_dir():
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)

        VERSION_FILE.write_text(latest_tag)
        save_progress("idle", 100)
        logger.info(f"[AutoUpdater] Mise à jour {latest_tag} appliquée. Redémarrage requis.")
        return True

    except Exception as e:
        logger.error(f"[AutoUpdater] Erreur lors de la mise à jour : {e}")
        save_progress("failed", 0)
        return False

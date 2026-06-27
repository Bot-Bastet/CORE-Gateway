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

            # Trouver le dossier face-server dans tmp_dir (gère les sous-dossiers des zipball)
            extracted_face = None
            for p in Path(tmp_dir).rglob("face-server"):
                if p.is_dir():
                    extracted_face = p
                    break

            if not extracted_face:
                logger.warning("[AutoUpdater] Dossier face-server/ absent dans l'archive.")
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

"""
Auto-updater pour CORE-Gateway (Python / Docker sur serveur Linux).
Vérifie GitHub Releases et effectue git pull + docker compose up si une nouvelle version existe.
Appelé au démarrage par main.py.
"""
import os
import subprocess
import requests
import logging
from pathlib import Path

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

    # Trouver l'archive zip dans les assets
    zip_asset = None
    for asset in release.get("assets", []):
        if asset["name"].endswith(".zip"):
            zip_asset = asset
            break

    if not zip_asset:
        logger.warning("[AutoUpdater] Aucun asset .zip trouvé dans la release.")
        return False

    try:
        # Télécharger l'archive
        repo_root = Path(__file__).parent.parent
        zip_path = repo_root / "update.zip"

        logger.info(f"[AutoUpdater] Téléchargement de {zip_asset['browser_download_url']}...")
        resp = requests.get(zip_asset["browser_download_url"], stream=True, timeout=120)
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        # Extraire sur place (écrase les fichiers existants)
        subprocess.run(["unzip", "-o", str(zip_path), "-d", str(repo_root)], check=True)
        zip_path.unlink()

        # Mettre à jour la version
        VERSION_FILE.write_text(latest_tag)
        logger.info(f"[AutoUpdater] Mise à jour {latest_tag} appliquée. Redémarrage requis.")
        return True

    except Exception as e:
        logger.error(f"[AutoUpdater] Erreur lors de la mise à jour : {e}")
        return False

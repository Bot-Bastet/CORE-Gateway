"""Dashboard and logo routes."""
import re
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, FileResponse

router = APIRouter(tags=["Dashboard"])
LOGO_PATH = Path(__file__).parent.parent / "logo.webp"
TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "dashboard.html"

# Timestamp genere au demarrage pour casser le cache navigateur a chaque restart
_CACHE_BUST = str(int(time.time()))


@router.get("/logo.webp", include_in_schema=False)
def serve_logo():
    return FileResponse(LOGO_PATH, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/", response_class=HTMLResponse)
def dashboard():
    """Dashboard d'administration complet de Bastet."""
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()
    # Remplace tous les ?v=X par le timestamp de demarrage du serveur
    html = re.sub(r'\?v=\d+', f'?v={_CACHE_BUST}', html)
    return HTMLResponse(content=html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

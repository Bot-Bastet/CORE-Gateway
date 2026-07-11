"""Dashboard and logo routes."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, FileResponse

router = APIRouter(tags=["Dashboard"])
LOGO_PATH = Path(__file__).parent.parent / "logo.webp"
TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "dashboard.html"


@router.get("/logo.webp", include_in_schema=False)
def serve_logo():
    return FileResponse(LOGO_PATH, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/", response_class=HTMLResponse)
def dashboard():
    """Dashboard d'administration complet de Bastet."""
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        html = f.read()
    # Assembler les fragments HTML extraits
    for fname in ["easyconfig_wizard.html", "calibration_overlay.html"]:
        fpath = TEMPLATE_PATH.parent / fname
        if fpath.exists():
            fragment = fpath.read_text(encoding="utf-8")
            html = html.replace(f"<!-- INCLUDE:{fname} -->", fragment)
    return HTMLResponse(content=html)

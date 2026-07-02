"""Face management routes for Bastet Gateway."""
import os
import uuid
import time
import hashlib
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from config import FACES_DIR, META_FILE, USERS_FILE, load_json, save_json, find_entry
from auth import verify_token

router = APIRouter(prefix="/faces", tags=["Faces"])


@router.post("/upload", summary="Upload une image", dependencies=[Depends(verify_token)])
async def upload_face(
    name: str = Query(..., description="Nom de la personne"),
    file: UploadFile = File(...),
):
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"Format non supporté : {ext}")

    meta = load_json(META_FILE)
    users = load_json(USERS_FILE, default={})
    normalized_name = name
    for u_name in users.keys():
        if u_name.lower() == name.lower():
            normalized_name = u_name
            break

    user_photos = [e for e in meta if e["name"].lower() == normalized_name.lower()]
    if len(user_photos) >= 8:
        raise HTTPException(status_code=400, detail=f"Limite atteinte : Impossible d'ajouter plus de 8 photos pour {normalized_name}.")

    content = await file.read()
    file_hash = hashlib.md5(content).hexdigest()

    for e in meta:
        if e["name"].lower() == normalized_name.lower() and e.get("hash") == file_hash:
            return {"status": "already_exists", "face": e, "msg": "Image identique déjà présente."}
        if e["name"].lower() == normalized_name.lower() and e.get("original_name") == file.filename and "hash" not in e:
            return {"status": "already_exists", "face": e, "msg": "Image avec le même nom déjà présente."}

    face_id = str(uuid.uuid4())
    dest = FACES_DIR / f"{face_id}{ext}"
    with open(dest, "wb") as f_out:
        f_out.write(content)

    entry = {
        "id": face_id,
        "name": normalized_name,
        "filename": f"{face_id}{ext}",
        "original_name": file.filename,
        "hash": file_hash,
        "size_bytes": len(content),
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    meta.append(entry)
    save_json(META_FILE, meta)
    return {"status": "ok", "face": entry}


@router.get("", summary="Lister tous les visages", dependencies=[Depends(verify_token)])
def list_faces(name: Optional[str] = Query(None)):
    meta = load_json(META_FILE)
    if name:
        meta = [e for e in meta if name.lower() in e["name"].lower()]
    return {"count": len(meta), "faces": meta}


@router.get("/{face_id}/image", summary="Télécharger l'image", dependencies=[Depends(verify_token)])
def get_face_image(face_id: str):
    entry = find_entry(face_id)
    if not entry:
        raise HTTPException(status_code=404)
    path = FACES_DIR / entry["filename"]
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/*", filename=entry["original_name"])


@router.delete("/{face_id}", summary="Supprimer un visage", dependencies=[Depends(verify_token)])
def delete_face(face_id: str):
    meta = load_json(META_FILE)
    entry = next((e for e in meta if e["id"] == face_id), None)
    if not entry:
        raise HTTPException(status_code=404)
    path = FACES_DIR / entry["filename"]
    if path.exists():
        path.unlink()
    meta = [e for e in meta if e["id"] != face_id]
    save_json(META_FILE, meta)
    return {"status": "deleted", "id": face_id}

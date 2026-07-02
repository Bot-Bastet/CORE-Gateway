"""Account management and authentication routes."""
import time
from fastapi import APIRouter, Depends, HTTPException, Query

from config import USERS_FILE, API_TOKEN, load_json, save_json
from auth import verify_password, get_password_hash, verify_token
from models import AccountInfo, LoginRequest, PreferencesUpdate

router = APIRouter(tags=["Accounts"])


@router.post("/auth/register", tags=["Auth"], summary="Créer un compte utilisateur (Alias)")
@router.post("/accounts", summary="Créer ou MAJ un compte utilisateur", dependencies=[Depends(verify_token)])
def save_account(info: AccountInfo):
    users = load_json(USERS_FILE, default={})
    full_name = f"{info.first_name} {info.last_name}"
    existing = users.get(full_name, {})
    dumped_info = info.model_dump()
    if info.password:
        dumped_info["password_hash"] = get_password_hash(info.password)
    else:
        dumped_info["password_hash"] = existing.get("password_hash")
    if not info.preferences and "preferences" in existing:
        dumped_info["preferences"] = existing["preferences"]
    dumped_info.pop("password", None)
    users[full_name] = dumped_info
    save_json(USERS_FILE, users)
    return {"status": "saved", "user": full_name}


@router.post("/preferences", summary="MAJ des préférences utilisateur", dependencies=[Depends(verify_token)])
def update_preferences(req: PreferencesUpdate):
    users = load_json(USERS_FILE, default={})
    if req.full_name not in users:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
    if "preferences" not in users[req.full_name]:
        users[req.full_name]["preferences"] = {}
    users[req.full_name]["preferences"].update(req.preferences)
    save_json(USERS_FILE, users)
    return {"status": "updated", "preferences": users[req.full_name]["preferences"]}


@router.post("/auth/login", tags=["Auth"], summary="Connexion utilisateur")
def login_user(creds: LoginRequest):
    users = load_json(USERS_FILE, default={})
    for name, u in users.items():
        if u.get("email", "").lower() == creds.email.lower():
            if "password_hash" in u and verify_password(creds.password, u["password_hash"]):
                user_copy = {k: v for k, v in u.items() if k != "password_hash"}
                return {"status": "success", "user": user_copy, "api_token": API_TOKEN}
            raise HTTPException(status_code=401, detail="Mot de passe incorrect")
    raise HTTPException(status_code=404, detail="Utilisateur non trouvé")


@router.get("/accounts", summary="Lister les comptes", dependencies=[Depends(verify_token)])
def get_accounts():
    return load_json(USERS_FILE, default={})


@router.delete("/accounts/{full_name}", summary="Supprimer un compte", dependencies=[Depends(verify_token)])
def delete_account(full_name: str):
    users = load_json(USERS_FILE, default={})
    if full_name in users:
        del users[full_name]
        save_json(USERS_FILE, users)
        return {"status": "deleted", "user": full_name}
    raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

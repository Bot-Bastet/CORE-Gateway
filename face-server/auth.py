"""Authentication helpers for Bastet Gateway."""
from passlib.context import CryptContext
from fastapi import HTTPException, Security, Depends
from fastapi.security.api_key import APIKeyHeader

from config import API_TOKEN

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
api_key_header = APIKeyHeader(name="X-API-Token", auto_error=False)


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def verify_token(api_key: str = Security(api_key_header)):
    if api_key != API_TOKEN:
        raise HTTPException(status_code=403, detail="Accès refusé. X-API-Token invalide ou manquant.")
    return api_key


def verify_token_optional(api_key: str = Security(api_key_header)):
    """Pour les routes où on gère l'auth différemment (ex. Dashboard)."""
    return api_key

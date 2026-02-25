import hashlib
import hmac
import os
import time
from functools import wraps

from dotenv import load_dotenv
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from paths import ENV_FILE

load_dotenv(str(ENV_FILE))

API_AUTH_ENABLED = os.getenv("API_AUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "").strip()
API_TOKEN_EXPIRY_SECONDS = int(os.getenv("API_TOKEN_EXPIRY_SECONDS", "86400") or "86400")
API_USERNAME = os.getenv("API_USERNAME", "admin").strip()
API_PASSWORD_HASH = os.getenv("API_PASSWORD_HASH", "").strip()

_bearer_scheme = HTTPBearer(auto_error=False)


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def verify_credentials(username: str, password: str) -> bool:
    if not API_AUTH_ENABLED:
        return True
    if username != API_USERNAME:
        return False
    if API_PASSWORD_HASH:
        return hmac.compare_digest(_hash_password(password), API_PASSWORD_HASH)
    expected = os.getenv("API_PASSWORD", "").strip()
    return hmac.compare_digest(password, expected)


def create_token(username: str) -> str:
    import jwt
    if not API_SECRET_KEY:
        raise HTTPException(status_code=500, detail="API_SECRET_KEY not configured")
    payload = {
        "sub": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + API_TOKEN_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, API_SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> dict:
    import jwt
    if not API_SECRET_KEY:
        raise HTTPException(status_code=500, detail="API_SECRET_KEY not configured")
    try:
        return jwt.decode(token, API_SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def require_auth(request: Request) -> dict | None:
    if not API_AUTH_ENABLED:
        return None

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]
    return decode_token(token)


PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
    "/auth/login",
    "/github/webhook",
}


async def auth_middleware(request: Request, call_next):
    if not API_AUTH_ENABLED:
        return await call_next(request)

    path = request.url.path.rstrip("/")
    if path in PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/redoc"):
        return await call_next(request)

    if path.startswith("/ws/"):
        return await call_next(request)

    try:
        await require_auth(request)
    except HTTPException as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})

    return await call_next(request)

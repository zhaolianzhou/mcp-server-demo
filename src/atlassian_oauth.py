import os
import base64
import hashlib
import secrets
from typing import Dict, Optional

import httpx
from fastapi import FastAPI, Request, HTTPException, APIRouter
from fastapi.responses import RedirectResponse, JSONResponse
from dotenv import load_dotenv
from itsdangerous import URLSafeSerializer, BadSignature

load_dotenv()

ATLASSIAN_CLIENT_ID=""
ATLASSIAN_CLIENT_SECRET=""
ATLASSIAN_REDIRECT_URI="http://localhost:8000/atlassian/callback"
SESSION_SECRET=""

if not ATLASSIAN_CLIENT_ID or not ATLASSIAN_CLIENT_SECRET or not ATLASSIAN_REDIRECT_URI:
    raise RuntimeError("Missing ATLASSIAN_CLIENT_ID / ATLASSIAN_CLIENT_SECRET / ATLASSIAN_REDIRECT_URI in env")

# Atlassian 3LO endpoints
AUTH_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"

# NOTE: pick scopes you actually need.
# For Jira example: "read:jira-user read:jira-work offline_access"
DEFAULT_SCOPES = [
    "read:me",
    "read:account",
    "offline_access",
    "write:confluence-content",
    "write:confluence-file",
    "read:confluence-content.all",
    "read:jira-work",
    "read:jira-user",
    "write:jira-work",
]

router = APIRouter()

# Very simple "session" cookie signer (for demo). Replace with proper session middleware if desired.
serializer = URLSafeSerializer(SESSION_SECRET, salt="atlassian-oauth-demo")

# Demo in-memory stores (replace with DB/Redis)
token_store: Dict[str, dict] = {}  # session_id -> token payload
pkce_store: Dict[str, dict] = {}   # session_id -> {"verifier": ..., "state": ...}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_pkce_pair() -> tuple[str, str]:
    """
    PKCE S256: code_verifier + code_challenge
    """
    verifier = _b64url(secrets.token_bytes(32))
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = _b64url(digest)
    return verifier, challenge


def get_session_id(request: Request) -> Optional[str]:
    cookie = request.cookies.get("session")
    if not cookie:
        return None
    try:
        return serializer.loads(cookie)
    except BadSignature:
        return None


def set_session_cookie(resp: RedirectResponse, session_id: str) -> None:
    signed = serializer.dumps(session_id)
    resp.set_cookie(
        "session",
        signed,
        httponly=True,
        secure=False,  # set True behind HTTPS
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
    )


@router.get("/")
async def root():
    return {
        "ok": True,
        "routes": ["/login", "/callback", "/me", "/logout"],
        "note": "Visit /login to start Atlassian OAuth flow.",
    }


@router.get("/login")
async def login(request: Request):
    # Create a session id
    session_id = get_session_id(request) or secrets.token_urlsafe(24)

    state = secrets.token_urlsafe(24)
    verifier, challenge = make_pkce_pair()

    pkce_store[session_id] = {"state": state, "verifier": verifier}

    scope_str = " ".join(DEFAULT_SCOPES)

    params = {
        "audience": "api.atlassian.com",
        "client_id": ATLASSIAN_CLIENT_ID,
        "scope": scope_str,
        "redirect_uri": ATLASSIAN_REDIRECT_URI,
        "state": state,
        "response_type": "code",
        "prompt": "consent",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    # Build URL manually to keep dependencies minimal
    from urllib.parse import urlencode

    url = f"{AUTH_URL}?{urlencode(params)}"
    resp = RedirectResponse(url=url, status_code=302)
    set_session_cookie(resp, session_id)
    return resp


@router.get("/callback")
async def callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    if error:
        # Atlassian may return error + error_description
        return JSONResponse({"error": error, "error_description": request.query_params.get("error_description")}, status_code=400)

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code/state")

    session_id = get_session_id(request)
    if not session_id or session_id not in pkce_store:
        raise HTTPException(status_code=400, detail="No session/PKCE info; start again at /login")

    expected_state = pkce_store[session_id]["state"]
    verifier = pkce_store[session_id]["verifier"]

    if state != expected_state:
        raise HTTPException(status_code=400, detail="Invalid state")

    # Exchange code -> token
    payload = {
        "grant_type": "authorization_code",
        "client_id": ATLASSIAN_CLIENT_ID,
        "client_secret": ATLASSIAN_CLIENT_SECRET,
        "code": code,
        "redirect_uri": ATLASSIAN_REDIRECT_URI,
        "code_verifier": verifier,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(TOKEN_URL, json=payload)
        if r.status_code != 200:
            raise HTTPException(status_code=400, detail={"token_exchange_failed": r.text})

        token_json = r.json()

    payload["token_json"] = token_json
    # Store tokens (demo)
    token_store[session_id] = token_json
    pkce_store.pop(session_id, None)

    return JSONResponse(payload)


@router.get("/me")
async def me(request: Request):
    session_id = get_session_id(request)
    if not session_id or session_id not in token_store:
        raise HTTPException(status_code=401, detail="Not logged in. Go to /login.")

    access_token = token_store[session_id].get("access_token")
    if not access_token:
        raise HTTPException(status_code=401, detail="Missing access token")

    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(ACCESSIBLE_RESOURCES_URL, headers=headers)
        if r.status_code != 200:
            raise HTTPException(status_code=400, detail={"accessible_resources_failed": r.text})

    return {
        "token_info": {
            "expires_in": token_store[session_id].get("expires_in"),
            "scope": token_store[session_id].get("scope"),
            "token_type": token_store[session_id].get("token_type"),
            "has_refresh_token": "refresh_token" in token_store[session_id],
        },
        "accessible_resources": r.json(),
    }


@router.get("/logout")
async def logout(request: Request):
    session_id = get_session_id(request)
    if session_id:
        token_store.pop(session_id, None)
        pkce_store.pop(session_id, None)

    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie("session")
    return resp


# Optional: refresh endpoint if you requested offline_access and received refresh_token
@router.post("/refresh")
async def refresh(request: Request):
    session_id = get_session_id(request)
    if not session_id or session_id not in token_store:
        raise HTTPException(status_code=401, detail="Not logged in")

    refresh_token = token_store[session_id].get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="No refresh_token stored (did you request offline_access?)")

    payload = {
        "grant_type": "refresh_token",
        "client_id": ATLASSIAN_CLIENT_ID,
        "client_secret": ATLASSIAN_CLIENT_SECRET,
        "refresh_token": refresh_token,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(TOKEN_URL, json=payload)
        if r.status_code != 200:
            raise HTTPException(status_code=400, detail={"refresh_failed": r.text})
        token_json = r.json()

    # Atlassian may rotate refresh tokens; keep the new one if present.
    if "refresh_token" not in token_json:
        token_json["refresh_token"] = refresh_token

    token_store[session_id] = token_json
    return {"ok": True, "token_info": {"expires_in": token_json.get("expires_in"), "scope": token_json.get("scope")}}

"""
FastAPI app: Figma OAuth (Authorization Code) flow.

How it works:
1) /auth/figma/login redirects to Figma authorize endpoint
2) /auth/figma/callback exchanges code -> access_token
3) /me fetches user profile from Figma using stored token

Setup:
- Create a Figma OAuth app: https://www.figma.com/developers/apps
- Add redirect URL: http://localhost:8000/auth/figma/callback
- Set env vars:
    FIGMA_CLIENT_ID=...
    FIGMA_CLIENT_SECRET=...
    FIGMA_REDIRECT_URI=http://localhost:8000/auth/figma/callback
    SESSION_SECRET=some-long-random-string

Run:
    pip install fastapi uvicorn httpx itsdangerous
    uvicorn figma_oauth_app:app --reload
"""

from __future__ import annotations

import os
import time
import secrets
from typing import Optional, Dict, Any
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request, HTTPException, APIRouter
from fastapi.responses import RedirectResponse, JSONResponse
from itsdangerous import URLSafeSerializer, BadSignature

router = APIRouter()

FIGMA_AUTH_URL = "https://www.figma.com/oauth"
FIGMA_TOKEN_URL = "https://api.figma.com/v1/oauth/token"
FIGMA_API_BASE = "https://api.figma.com"

FIGMA_CLIENT_ID = os.getenv("FIGMA_CLIENT_ID", "")
FIGMA_CLIENT_SECRET = os.getenv("FIGMA_CLIENT_SECRET", "")
FIGMA_REDIRECT_URI = os.getenv("FIGMA_REDIRECT_URI", "http://localhost:8000/figma/auth/callback")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")

if not FIGMA_CLIENT_ID or not FIGMA_CLIENT_SECRET or not SESSION_SECRET:
    # We don't raise here to keep import-time friendly; we validate at runtime.
    pass

# Simple signed cookie helper (NOT encrypted; don't put secrets in it).
serializer = URLSafeSerializer(SESSION_SECRET or "dev-only-secret", salt="figma-oauth")

# Demo token store: maps session_id -> token payload
# Replace with Redis/DB in real deployments.
TOKEN_STORE: Dict[str, Dict[str, Any]] = {}


def _require_config() -> None:
    if not FIGMA_CLIENT_ID or not FIGMA_CLIENT_SECRET or not SESSION_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Missing env vars: FIGMA_CLIENT_ID, FIGMA_CLIENT_SECRET, SESSION_SECRET (and ideally FIGMA_REDIRECT_URI).",
        )


def _make_session_id() -> str:
    return secrets.token_urlsafe(24)


def _get_session_id_from_cookie(request: Request) -> Optional[str]:
    cookie = request.cookies.get("session")
    if not cookie:
        return None
    try:
        data = serializer.loads(cookie)
        return data.get("sid")
    except BadSignature:
        return None


def _set_session_cookie(resp: RedirectResponse, sid: str) -> None:
    value = serializer.dumps({"sid": sid})
    # In prod: set secure=True behind HTTPS
    resp.set_cookie(
        key="session",
        value=value,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 days
        path="/",
    )


@router.get("/")
def root():
    return {
        "ok": True,
        "routes": {
            "login": "/auth/login",
            "callback": "/auth/callback",
            "me": "/me",
        },
    }


@router.get("/auth/login")
def figma_login():
    _require_config()

    sid = _make_session_id()
    state = secrets.token_urlsafe(24)

    # store state in TOKEN_STORE for this session (or separate store)
    TOKEN_STORE[sid] = {"oauth_state": state}

    params = {
        "client_id": FIGMA_CLIENT_ID,
        "redirect_uri": FIGMA_REDIRECT_URI,
        "scope": "current_user:read",  # adjust scopes as needed
        "state": state,
        "response_type": "code",
    }

    url = f"{FIGMA_AUTH_URL}?{urlencode(params)}"
    resp = RedirectResponse(url=url, status_code=302)
    _set_session_cookie(resp, sid)
    return resp


@router.get("/auth/callback")
async def figma_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    _require_config()

    if error:
        raise HTTPException(status_code=400, detail=f"Figma OAuth error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code/state in callback.")

    sid = _get_session_id_from_cookie(request)
    if not sid or sid not in TOKEN_STORE:
        raise HTTPException(status_code=400, detail="Missing or invalid session. Please restart login flow.")

    expected_state = TOKEN_STORE[sid].get("oauth_state")
    access_token = TOKEN_STORE[sid].get("token")
    if not expected_state or state != expected_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")

    # Exchange code for token
    data = {
        "client_id": FIGMA_CLIENT_ID,
        "client_secret": FIGMA_CLIENT_SECRET,
        "redirect_uri": FIGMA_REDIRECT_URI,
        "code": code,
        "grant_type": "authorization_code",
        "access_token": access_token
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(FIGMA_TOKEN_URL, data=data, headers={"Accept": "application/json"})
        if r.status_code >= 400:
            raise HTTPException(status_code=400, detail={"token_exchange_failed": r.text})

        token_payload = r.json()

    # token_payload typically includes: access_token, expires_in, refresh_token (if provided), token_type
    TOKEN_STORE[sid].update(
        {
            "token": token_payload,
            "token_obtained_at": int(time.time()),
        }
    )

    # Redirect somewhere useful
    return JSONResponse(
        {
            "ok": True,
            "message": "Figma OAuth complete. You can now call /me.",
            "token_fields": token_payload,
        }
    )


def _get_access_token_for_request(request: Request) -> str:
    sid = _get_session_id_from_cookie(request)
    if not sid or sid not in TOKEN_STORE:
        raise HTTPException(status_code=401, detail="Not logged in. Visit /auth/figma/login first.")

    token_payload = TOKEN_STORE[sid].get("token")
    if not token_payload or "access_token" not in token_payload:
        raise HTTPException(status_code=401, detail="No access token found. Re-authenticate.")

    return token_payload["access_token"]


@router.get("/me")
async def figma_me(request: Request):
    token = _get_access_token_for_request(request)

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{FIGMA_API_BASE}/v1/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    return r.json()

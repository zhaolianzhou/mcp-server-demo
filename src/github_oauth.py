import base64
import hashlib
import os
import secrets
from urllib.parse import urlencode

import requests
from fastapi import FastAPI, Request, HTTPException, APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from itsdangerous import URLSafeSerializer, BadSignature

router = APIRouter()

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
APP_URL = os.environ.get("APP_URL", "http://localhost:8000/github")  # used to build redirect_uri

# A server-side secret used to sign state/PKCE data. Store securely.
OAUTH_SIGNING_SECRET = os.getenv("OAUTH_SIGNING_SECRET", "")

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_USER = "https://api.github.com/user"

serializer = URLSafeSerializer(OAUTH_SIGNING_SECRET, salt="github-oauth")


# ---- PKCE helpers (optional but recommended) ----
def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

def make_pkce_pair():
    verifier = _b64url_no_pad(secrets.token_bytes(32))  # 43-128 chars
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = _b64url_no_pad(digest)
    return verifier, challenge


@router.get("/", response_class=HTMLResponse)
def home():
    return '<a href="/login/github">Login with GitHub</a>'


@router.get("/login")
def login_github():
    """
    Starts OAuth:
      - generates state
      - optionally generates PKCE verifier/challenge
      - redirects user to GitHub authorize URL
    """
    state = secrets.token_urlsafe(32)

    use_pkce = True
    pkce_verifier = None
    pkce_challenge = None
    if use_pkce:
        pkce_verifier, pkce_challenge = make_pkce_pair()

    # We need to remember state (+ verifier if PKCE). Common options:
    # - server-side session storage (Redis, DB)
    # - signed cookie
    #
    # Here we use a signed cookie for simplicity.
    signed = serializer.dumps({
        "state": state,
        "pkce_verifier": pkce_verifier,
    })

    redirect_uri = f"{APP_URL}/auth/callback"
    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "state": state,
        # Pick minimal scopes you need:
        "scope": "read:user user:email",
    }
    if use_pkce:
        params["code_challenge"] = pkce_challenge
        params["code_challenge_method"] = "S256"

    authorize_url = f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"
    resp = RedirectResponse(authorize_url, status_code=302)

    # HTTPOnly cookie so JS can’t read it. "lax" is usually fine for OAuth.
    resp.set_cookie(
        "oauth_ctx",
        signed,
        httponly=True,
        secure=False,   # set True in production (HTTPS)
        samesite="lax",
        max_age=10 * 60,
    )
    return resp


@router.get("/auth/callback")
def github_callback(request: Request):
    """
    Callback:
      - verifies state matches what we issued
      - exchanges code for token
      - fetches GitHub user
    """
    code = request.query_params.get("code")
    returned_state = request.query_params.get("state")
    if not code or not returned_state:
        raise HTTPException(status_code=400, detail="Missing code/state")

    signed = request.cookies.get("oauth_ctx")
    if not signed:
        raise HTTPException(status_code=400, detail="Missing oauth context cookie")

    try:
        ctx = serializer.loads(signed)
    except BadSignature:
        raise HTTPException(status_code=400, detail="Invalid oauth context cookie")

    expected_state = ctx.get("state")
    pkce_verifier = ctx.get("pkce_verifier")

    if returned_state != expected_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    redirect_uri = f"{APP_URL}/auth/callback"

    # Exchange code -> access token
    data = {
        "client_id": GITHUB_CLIENT_ID,
        "client_secret": GITHUB_CLIENT_SECRET,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if pkce_verifier:
        data["code_verifier"] = pkce_verifier

    headers = {"Accept": "application/json"}
    token_resp = requests.post(GITHUB_TOKEN_URL, data=data, headers=headers, timeout=15)
    token_resp.raise_for_status()
    token_json = token_resp.json()

    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {token_json}")

    # Fetch user profile
    api_headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {access_token}",
    }
    user_resp = requests.get(GITHUB_API_USER, headers=api_headers, timeout=15)
    user_resp.raise_for_status()
    user = user_resp.json()

    # Clear the context cookie now that we're done with it
    out = JSONResponse({
        "github_user": user,
        "token_prefix_hint": access_token,
        "note": "Store the full token securely server-side (DB/Secrets), not in a client cookie.",
    })
    out.delete_cookie("oauth_ctx")
    return out

import os
import secrets
import time
from typing import Dict, Optional

import httpx
from fastapi import FastAPI, Request, HTTPException, APIRouter
from fastapi.responses import RedirectResponse, JSONResponse
from urllib.parse import urlencode

router = APIRouter()

### TODO: config the slack OAuth client id and secret below
SLACK_CLIENT_ID =  ""
SLACK_CLIENT_SECRET = ""

### Replace with your own redirect URI with ngrok tunnel, remember to add it to your Slack app's OAuth & Permissions redirect url'
SLACK_REDIRECT_URI = "https://zhaolian-local.ninjatech.ngrok.dev/slack/oauth/callback"

BOT_SCOPES = [
    "channels:read",
    "chat:write",
    "chat:write.customize",
    "users:read",
]

USER_SCOPES = [
    "channels:read",
    "chat:write",
    "users:read",
    "users:read.email",
]

# ---- Example storage (replace with DB) ----

INSTALLS: Dict[str, dict] = {}
STATE_STORE: Dict[str, float] = {}
STATE_TTL = 600  # 10 minutes

def cleanup_states():
    now = time.time()
    expired = [k for k, v in STATE_STORE.items() if now - v > STATE_TTL]
    for k in expired:
        STATE_STORE.pop(k, None)


@router.get("/install")
async def slack_install():
    """
    Redirects the user to Slack’s OAuth authorization page.
    """
    cleanup_states()
    state = secrets.token_urlsafe(32)
    STATE_STORE[state] = time.time()

    params = {
        "client_id": SLACK_CLIENT_ID,
        "redirect_uri": SLACK_REDIRECT_URI,
        "state": state,
        "scope": ",".join(BOT_SCOPES),          # bot token scopes
        "user_scope": ",".join(USER_SCOPES),    # user token scopes
    }

    base = "https://slack.com/oauth/v2/authorize"
    url = f"{base}?{urlencode(params)}"
    return RedirectResponse(url, status_code=302)


@router.get("/oauth/callback")
async def slack_oauth_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """
    Handles Slack redirect. Exchanges 'code' for tokens using oauth.v2.access.
    """
    if error:
        # Slack may send error=access_denied, etc.
        raise HTTPException(status_code=400, detail={"slack_error": error})

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    cleanup_states()
    if state not in STATE_STORE:
        raise HTTPException(status_code=400, detail="Invalid/expired state")
    STATE_STORE.pop(state, None)

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": SLACK_CLIENT_ID,
                "client_secret": SLACK_CLIENT_SECRET,
                "code": code,
                "redirect_uri": SLACK_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    data = resp.json()
    if not data.get("ok"):
        raise HTTPException(status_code=400, detail=data)

    # Slack response fields (common):
    # team: { id, name }
    # access_token: xoxb-... (bot token)
    # bot_user_id
    # scope
    # authed_user: { id, scope, access_token (if user scopes requested) }
    team = data.get("team") or {}
    team_id = team.get("id")
    if not team_id:
        raise HTTPException(status_code=400, detail="No team id in Slack response")

    install_record = {
        "team_id": team_id,
        "team_name": team.get("name"),
        "bot_access_token": data.get("access_token"),
        "bot_user_id": data.get("bot_user_id"),
        "bot_scopes": data.get("scope"),
        "authed_user": data.get("authed_user"),  # may include user token if requested
        "raw": data,  # keep raw for debugging (remove if sensitive)
        "installed_at": int(time.time()),
    }

    # Store it (replace with your DB)
    INSTALLS[team_id] = install_record

    return JSONResponse(
        install_record
    )


@router.get("/installations/{team_id}")
async def get_installation(team_id: str):
    """
    Debug endpoint to fetch stored install data (do not expose in production).
    """
    rec = INSTALLS.get(team_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Not found")
    # Never return tokens in real prod endpoints.
    return {"team_id": team_id, "team_name": rec.get("team_name"), "installed_at": rec.get("installed_at")}

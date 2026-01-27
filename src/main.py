from __future__ import annotations

import uvicorn

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from klavis.types import McpServerName, ToolFormat

from src import google_calendar_oauth
from src import github_oauth
from src import figma_oauth
from src import atlassian_oauth
from src import slack_oauth
from src.clients import klavis_client, PLATFORM_NAME, openai_client

# ---- Replace this with your real persistence layer (DB/Redis/DynamoDB/etc.) ----
# Maps your user_id -> Klavis instance metadata you need later.
USER_TO_KLAVIS = {
}  # { user_id: {"instance_id": ..., "server_url": ...} }

app = FastAPI()
app.include_router(google_calendar_oauth.router, prefix="/google")
app.include_router(github_oauth.router, prefix="/github")
app.include_router(figma_oauth.router, prefix="/figma")
app.include_router(atlassian_oauth.router, prefix="/atlassian")

app.include_router(slack_oauth.router, prefix="/slack")


class ConnectRequest(BaseModel):
    user_id: str  # your app's stable user id

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/integrations/google-calendar/connect")
def connect_google_calendar(req: ConnectRequest):
    user_id = req.user_id
    # Create a Klavis-hosted server instance (OAuth-based service)
    server = klavis_client().mcp_server.create_server_instance(
        server_name=McpServerName.GOOGLE_CALENDAR,
        user_id=user_id,
        platform_name=PLATFORM_NAME,
    )

    # Klavis provides server_url + oauth_url for OAuth flows. :contentReference[oaicite:2]{index=2}
    USER_TO_KLAVIS[user_id] = {
        "instance_id": server.instance_id,
        "server_url": server.server_url,
    }

    # If not yet authorized, oauth_url is what you send user to.
    # (You typically render a "Connect Google Calendar" button that opens this URL.)
    if getattr(server, "oauth_url", None):
        return {
            "status": "needs_oauth",
            "oauth_url": server.oauth_url,
            "instance_id": server.instance_id,
        }

    # If oauth_url is absent, it may already be connected (or not required).
    return {"status": "connected", "server_url": server.server_url}


@app.get("/integrations/google-calendar/status")
def google_calendar_status(user_id: str):
    """
    Lightweight "are we connected?" check.
    We try to list tools; if OAuth isn't completed yet, this usually fails.
    """
    meta = USER_TO_KLAVIS.get(user_id)
    if not meta:
        raise HTTPException(404, "No integration record for this user_id")

    server_url = meta["server_url"]

    try:
        tools = klavis_client().mcp_server.list_tools(
            server_url=server_url,
            format=ToolFormat.OPENAI,
        )
        # If tools list loads, you're very likely authorized.
        return {"status": "connected", "tool_count": len(tools.tools)}
    except Exception as e:
        # Treat errors as "not connected yet" for UI purposes
        return {"status": "not_connected", "error": str(e)[:200]}

class AskReq(BaseModel):
    user_id: str
    text: str
    require_approval: str | None = "never"  # "never" or "manual" (recommended for writes)

@app.post("/agent/ask")
def agent_ask(req: AskReq):
    meta = USER_TO_KLAVIS[req.user_id]
    klavis_mcp_url = meta["server_url"]

    resp = openai_client().responses.create(
        model="gpt-5",
        input=[{"role": "user", "content": req.text, "type": "message"}],
        tools=[
            {
                "type": "mcp",
                "server_label": "gcal",
                "server_url": klavis_mcp_url,  # <-- point directly at Klavis-hosted MCP
                "require_approval": req.require_approval,  # "never" or "manual"
                # Optional: restrict tool surface once you know tool names
                # "allowed_tools": ["GoogleCalendar-list_events", "GoogleCalendar-create_event"]
            }
        ],
        tool_choice="auto",
    )
    return resp.output_text

@app.get("/gcal_mcp_instance/get")
async def get_klavis_instance(instance_id: str) -> None:
    return klavis_client().mcp_server.get_server_instance(instance_id=instance_id)

@app.delete("/gcal_mcp_instance/delete")
async def delete_klavis_instance(instance_id: str) -> None:
    return klavis_client().mcp_server.delete_server_instance(
        instance_id=instance_id,
    )
@app.get("/klvais_user/get_all_users")
async def get_all_users() -> None:
    return klavis_client().user.get_all_users()

@app.get("/klvais_user/get_user")
async def get_user_by_id(user_id: str) -> None:
    return klavis_client().user.get_user_by_user_id(user_id=user_id)
@app.get("/klvais_user/get_user_integration")
async def get_user_integrations(user_id: str) -> None:
    return klavis_client().user.get_user_integrations(user_id=user_id)

@app.delete("/klvais_user/delete")
async def delete_klavis_user(user_id: str) -> None:
    return klavis_client().user.delete_user_by_user_id(
        user_id=user_id,
    )



if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
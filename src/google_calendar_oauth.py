import os
import json
import secrets
from fastapi import FastAPI, Request, HTTPException, APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
router = APIRouter()

# Configuration
CLIENT_SECRETS_FILE = "client_secret.json"  # Google cloud client secret file
SCOPES = ['https://www.googleapis.com/auth/calendar']
REDIRECT_URI = "http://localhost:8000/google/oauth2callback"

sessions = {}

@router.get("/", response_class=HTMLResponse)
async def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Google Calendar MCP OAuth Setup</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }
            .container {
                background: white;
                border-radius: 16px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                max-width: 600px;
                width: 100%;
                padding: 40px;
            }
            h1 {
                color: #2d3748;
                margin-bottom: 10px;
                font-size: 28px;
            }
            .subtitle {
                color: #718096;
                margin-bottom: 30px;
            }
            .info-box {
                background: #edf2f7;
                border-left: 4px solid #4299e1;
                padding: 16px;
                margin-bottom: 24px;
                border-radius: 4px;
            }
            .info-box h3 {
                color: #2d3748;
                margin-bottom: 8px;
                font-size: 16px;
            }
            .info-box ol {
                padding-left: 20px;
                color: #4a5568;
            }
            .info-box li {
                margin: 6px 0;
            }
            .warning-box {
                background: #fffaf0;
                border-left: 4px solid #ed8936;
                padding: 16px;
                margin-bottom: 24px;
                border-radius: 4px;
            }
            .warning-box h4 {
                color: #744210;
                margin-bottom: 8px;
                font-size: 14px;
                font-weight: 600;
            }
            .warning-box ul {
                padding-left: 20px;
                color: #744210;
                font-size: 13px;
            }
            .warning-box li {
                margin: 4px 0;
            }
            .btn {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                border: none;
                padding: 16px 32px;
                font-size: 16px;
                font-weight: 600;
                border-radius: 8px;
                cursor: pointer;
                width: 100%;
                transition: transform 0.2s, box-shadow 0.2s;
            }
            .btn:hover {
                transform: translateY(-2px);
                box-shadow: 0 10px 20px rgba(102, 126, 234, 0.4);
            }
            .icon {
                display: inline-block;
                margin-right: 8px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🗓️ Google Calendar MCP Setup</h1>
            <p class="subtitle">Connect your Google Calendar to Claude Code</p>

            <div class="info-box">
                <h3>How it works:</h3>
                <ol>
                    <li>Authorize access to your Google Calendar</li>
                    <li>Receive OAuth credentials</li>
                    <li>Get Claude Code MCP configuration</li>
                </ol>
            </div>

            <div class="warning-box">
                <h4>⚠️ Prerequisites:</h4>
                <ul>
                    <li>Google Cloud project with Calendar API enabled</li>
                    <li>OAuth 2.0 credentials downloaded as client_secret.json</li>
                    <li>Redirect URI: http://localhost:8000/google/oauth2callback</li>
                </ul>
            </div>

            <button class="btn" onclick="window.location.href='/google/authorize'">
                <span class="icon">🔐</span> Connect Google Calendar
            </button>
        </div>
    </body>
    </html>
    """

@router.get("/authorize")
async def authorize():
    """Initiate OAuth flow"""
    try:
        # Create flow instance
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )

        # Generate state token for security
        state = secrets.token_urlsafe(32)
        sessions[state] = {}

        authorization_url, _ = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            state=state,
            prompt='consent'  # Force to get refresh token
        )

        return RedirectResponse(authorization_url)
    except FileNotFoundError as e:
        print(e)
        raise HTTPException(
            status_code=500,
            detail="client_secret.json not found. Download it from Google Cloud Console."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/oauth2callback")
async def oauth2callback(request: Request):
    """Handle OAuth callback from Google"""
    state = request.query_params.get('state')
    code = request.query_params.get('code')
    error = request.query_params.get('error')

    if error:
        return HTMLResponse(f"""
            <html><body>
                <h2>Authorization Failed</h2>
                <p>Error: {error}</p>
                <a href="/">Try Again</a>
            </body></html>
        """)

    if not state or state not in sessions:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    try:
        # Exchange code for tokens
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,
            state=state
        )

        flow.fetch_token(code=code)
        credentials = flow.credentials

        # Store credentials
        sessions[state]['credentials'] = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes
        }

        # Generate MCP config
        mcp_config = generate_mcp_config(credentials)
        auth_env_config = generate_auth_env_config(credentials)
        sessions[state]['mcp_config'] = mcp_config
        sessions[state]['auth_env_config'] = auth_env_config

        return RedirectResponse(f"/google/success?state={state}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to exchange token: {str(e)}")

@router.get("/success", response_class=HTMLResponse)
async def success(state: str):
    """Display success page with MCP configuration"""
    if state not in sessions or 'mcp_config' not in sessions[state]:
        raise HTTPException(status_code=400, detail="Invalid session")

    mcp_config = sessions[state]['mcp_config']
    config_json = json.dumps(mcp_config, indent=2)
    auth_env_config = sessions[state]['auth_env_config']
    auth_env_json = json.dumps(auth_env_config)


    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Setup Complete</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 40px 20px;
            }}
            .container {{
                background: white;
                border-radius: 16px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                max-width: 800px;
                margin: 0 auto;
                padding: 40px;
            }}
            h1 {{
                color: #2d3748;
                margin-bottom: 10px;
                font-size: 28px;
            }}
            .success-badge {{
                background: #c6f6d5;
                color: #22543d;
                padding: 8px 16px;
                border-radius: 20px;
                display: inline-block;
                margin-bottom: 24px;
                font-weight: 600;
            }}
            .section {{
                margin: 24px 0;
            }}
            .section h3 {{
                color: #2d3748;
                margin-bottom: 12px;
                font-size: 18px;
            }}
            .code-block {{
                background: #1a202c;
                color: #e2e8f0;
                padding: 20px;
                border-radius: 8px;
                overflow-x: auto;
                position: relative;
                font-family: 'Courier New', monospace;
                font-size: 13px;
                line-height: 1.6;
            }}
            .copy-btn {{
                position: absolute;
                top: 12px;
                right: 12px;
                background: #4a5568;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 12px;
                transition: background 0.2s;
            }}
            .copy-btn:hover {{
                background: #2d3748;
            }}
            .info-box {{
                background: #bee3f8;
                border-left: 4px solid #3182ce;
                padding: 16px;
                margin: 16px 0;
                border-radius: 4px;
            }}
            .command {{
                background: #edf2f7;
                padding: 12px;
                border-radius: 6px;
                font-family: 'Courier New', monospace;
                margin: 8px 0;
                border-left: 3px solid #667eea;
            }}
            pre {{
                margin: 0;
                white-space: pre-wrap;
                word-wrap: break-word;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>✅ Authentication Successful!</h1>
            <div class="success-badge">🎉 Your Google Calendar is now connected</div>

            <div class="section">
                <h3>Step 1: Locate Claude Code Configuration</h3>
                <div class="command">~/.claude.json</div>
            </div>

            <div class="section">
                <h3>Step 2: Add This MCP Configuration OR Credential Environment</h3>
                <div class="code-block">
                    <button class="copy-btn" onclick="copyConfig()">📋 Copy</button>
                    <pre id="config">{config_json}</pre>
                </div>
                <div class="code-block">
                    <button class="copy-btn" onclick="copyConfig()">📋 Copy</button>
                    <pre id="config">{auth_env_json}</pre>
                </div>
            </div>
            

            <div class="info-box">
                <strong>💡 Quick Start:</strong> Save this configuration to your Claude Code config file and restart Claude Code.
            </div>

            <div class="section">
                <h3>Step 3: Test Your Integration</h3>
                <div class="command">$ claude "List my calendar events for today"</div>
                <div class="command">$ claude "Create a meeting at 2pm tomorrow"</div>
            </div>
        </div>

        <script>
            function copyConfig() {{
                const config = document.getElementById('config').textContent;
                navigator.clipboard.writeText(config).then(() => {{
                    const btn = document.querySelector('.copy-btn');
                    btn.textContent = '✓ Copied!';
                    setTimeout(() => btn.textContent = '📋 Copy', 2000);
                }});
            }}
        </script>
    </body>
    </html>
    """

def generate_mcp_config(credentials):
    """Generate MCP configuration for Claude Code"""
    return {
        "mcpServers": {
            "google-calendar": {
                "command": "python",
                "args": [
                    "/Users/zhaolian.zhou/workspace/mcp-server-demo/src/google_calendar_mcp_server.py"
                ],
                "env": {
                    "GOOGLE_ACCESS_TOKEN": credentials.token,
                    "GOOGLE_REFRESH_TOKEN": credentials.refresh_token,
                    "GOOGLE_CLIENT_ID": credentials.client_id,
                    "GOOGLE_CLIENT_SECRET": credentials.client_secret
                }
            }
        }
    }

def generate_auth_env_config(credentials):
    """Generate environment variables for Claude Code"""
    return {
        "access_token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
    }
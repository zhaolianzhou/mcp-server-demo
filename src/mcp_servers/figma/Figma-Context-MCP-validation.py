#!/usr/bin/env python3
import argparse
import os
import sys
import requests
import json

DEFAULT_BASE_URL = "http://localhost:3333"
PROTOCOL_VERSION = "2025-03-26"  # MCP spec version shown in official docs


def post_jsonrpc(base_url: str, payload: dict, session_id: str | None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    r = requests.post(f"{base_url}/mcp", json=payload, headers=headers, timeout=120)
    r.raise_for_status()

    new_session_id = r.headers.get("Mcp-Session-Id") or session_id

    # Many servers return 202/204 with no body for notifications.
    if r.status_code in (202, 204) or not (r.text and r.text.strip()):
        return None, new_session_id

    ctype = (r.headers.get("Content-Type") or "").lower()

    if "application/json" in ctype:
        return r.json(), new_session_id

    if "text/event-stream" in ctype:
        # Parse SSE and return the first JSON-looking data payload.
        for line in r.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                data = line[len("data:"):].strip()
                if data.startswith("{"):
                    return json.loads(data), new_session_id
        raise RuntimeError(f"Got SSE but no JSON data lines found. Body(head): {r.text[:500]}")

    # If we got here, server gave a body but no recognized Content-Type
    # Try JSON parse as a last resort (some servers forget the header).
    try:
        return r.json(), new_session_id
    except Exception:
        raise RuntimeError(
            f"Unexpected Content-Type: {r.headers.get('Content-Type')}\n"
            f"Status: {r.status_code}\nBody(head): {r.text[:500]}"
        )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--file-key", required=True, help="Figma file key (from the URL)")
    ap.add_argument("--node-id", default=None, help="Optional node id (e.g. 1:23)")
    ap.add_argument("--depth", type=int, default=None, help="Optional depth (avoid unless needed)")
    ap.add_argument("--output-json", action="store_true", help="Ask server to output JSON if configured that way")
    args = ap.parse_args()

    base_url = args.base_url.rstrip("/")

    session_id = None
    rpc_id = 1

    # 1) initialize
    init_req = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "roots": {"listChanged": False},
                "sampling": {},
            },
            "clientInfo": {"name": "python-smoke-test", "version": "0.1.0"},
        },
    }
    init_resp, session_id = post_jsonrpc(base_url, init_req, session_id)
    print("initialize result keys:", list(init_resp.get("result", {}).keys()))
    if not session_id:
        print("WARNING: no Mcp-Session-Id received; server may be running stateless or using a different transport.")
    else:
        print("session:", session_id)

    # 2) notifications/initialized (no id for notifications)
    rpc_id += 1
    initialized_note = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    _, session_id = post_jsonrpc(base_url, initialized_note, session_id)

    # 3) tools/list
    rpc_id += 1
    tools_list_req = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tools/list",
        "params": {},
    }
    tools_list_resp, session_id = post_jsonrpc(base_url, tools_list_req, session_id)
    tools = tools_list_resp.get("result", {}).get("tools", [])
    print("tools:", [t.get("name") for t in tools])

    # 4) tools/call -> get_figma_data
    # Tool name + parameters from DeepWiki: get_figma_data(fileKey, nodeId?, depth?) :contentReference[oaicite:7]{index=7}
    rpc_id += 1
    call_args = {"fileKey": args.file_key}
    if args.node_id:
        call_args["nodeId"] = args.node_id
    if args.depth is not None:
        call_args["depth"] = args.depth

    tool_call_req = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tools/call",
        "params": {"name": "get_figma_data", "arguments": call_args},
    }
    tool_call_resp, session_id = post_jsonrpc(base_url, tool_call_req, session_id)

    result = tool_call_resp.get("result", {})
    content = result.get("content", [])
    is_error = result.get("isError", False)

    print("\n=== get_figma_data ===")
    print("isError:", is_error)
    for item in content:
        if item.get("type") == "text":
            print(item.get("text", ""))
        else:
            print("non-text content item:", item)

if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print("HTTP error:", e.response.status_code, e.response.text[:500], file=sys.stderr)
        raise

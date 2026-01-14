#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

import requests


@dataclass
class SseEvent:
    event: str
    data: str


def iter_sse_events(resp: requests.Response) -> Iterator[SseEvent]:
    """
    Minimal SSE parser for EventSource format:
    - collects `event:` and `data:` lines until a blank line
    - yields SseEvent(event, data)
    """
    event_name = "message"
    data_lines: list[str] = []

    for raw in resp.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw.rstrip("\n")

        # blank line ends one event
        if line == "":
            if data_lines:
                yield SseEvent(event=event_name, data="\n".join(data_lines))
            event_name = "message"
            data_lines = []
            continue

        # comments / keepalives
        if line.startswith(":"):
            continue

        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())

    # flush if stream ends unexpectedly
    if data_lines:
        yield SseEvent(event=event_name, data="\n".join(data_lines))


def post_and_wait_jsonrpc_sse(
    session: requests.Session,
    url: str,
    payload: Dict[str, Any],
    *,
    origin: str,
    timeout: float,
    wait_s: float,
) -> Optional[Dict[str, Any]]:
    """
    Sends a JSON-RPC request via POST and waits for the JSON-RPC response.
    This server returns `text/event-stream` and the response appears as:
      event: message
      data: {jsonrpc...}

    For notifications (no id), we don't wait for a response.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Origin": origin,
        "Cache-Control": "no-cache",
        "MCP-Protocol-Version": "2024-11-05",
    }

    # normalize trailing slash to avoid 307 empty-body redirect
    if url.endswith("/mcp"):
        url = url + "/"

    r = session.post(url, json=payload, headers=headers, timeout=timeout, stream=True, allow_redirects=True)
    r.raise_for_status()

    want_id = payload.get("id")
    if want_id is None:
        # notification: don't block waiting
        return None

    ctype = (r.headers.get("content-type") or "").lower()
    if "text/event-stream" not in ctype:
        # Some servers might return application/json occasionally.
        # Handle both.
        if "application/json" in ctype:
            return r.json()
        # Fallback: try parse first non-empty line
        txt = r.text.strip()
        for line in txt.splitlines():
            line = line.strip()
            if line:
                return json.loads(line)
        raise ValueError(f"Unexpected response content-type={ctype!r} and empty body")

    events = iter_sse_events(r)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            ev = next(events)
        except StopIteration:
            break

        if ev.event != "message":
            continue

        try:
            msg = json.loads(ev.data)
        except json.JSONDecodeError:
            continue

        if isinstance(msg, dict) and msg.get("id") == want_id:
            return msg

    raise TimeoutError(f"Timed out waiting for JSON-RPC response id={want_id} over SSE")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:5000")
    ap.add_argument("--path", default="/mcp/", help="Use /mcp/ (trailing slash) for this server")
    ap.add_argument("--origin", default="http://localhost")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--wait", type=float, default=15.0)
    ap.add_argument(
        "--smoke-slack",
        action="store_true",
        help="Also call a Slack read-only tool to validate tokens really work (uses slack_list_users limit=1).",
    )
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    url = base + args.path

    session = requests.Session()

    print(f"[1/4] initialize -> {url}")
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"roots": {"listChanged": True}, "sampling": {}},
            "clientInfo": {"name": "klavis-streamablehttp-validator", "version": "0.1.0"},
        },
    }

    try:
        init_resp = post_and_wait_jsonrpc_sse(session, url, init_req, origin=args.origin, timeout=args.timeout, wait_s=args.wait)
    except Exception as e:
        print(f"ERROR: initialize failed: {e}")
        return 1

    if init_resp is None:
        print("ERROR: initialize returned no response")
        return 1
    if "error" in init_resp:
        print("ERROR: initialize returned JSON-RPC error:")
        print(json.dumps(init_resp["error"], indent=2))
        return 1

    server_info = init_resp.get("result", {}).get("serverInfo", {})
    print(f"    serverInfo: {server_info}")

    print("[2/4] notifications/initialized")
    try:
        post_and_wait_jsonrpc_sse(
            session,
            url,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            origin=args.origin,
            timeout=args.timeout,
            wait_s=args.wait,
        )
    except Exception as e:
        print(f"ERROR: notifications/initialized failed: {e}")
        return 1

    print("[3/4] tools/list")
    try:
        tools_resp = post_and_wait_jsonrpc_sse(
            session,
            url,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            origin=args.origin,
            timeout=args.timeout,
            wait_s=args.wait,
        )
    except Exception as e:
        print(f"ERROR: tools/list failed: {e}")
        return 1

    if tools_resp is None or "error" in tools_resp:
        print("ERROR: tools/list returned error or no response:")
        print(json.dumps(tools_resp, indent=2))
        return 1

    tools = tools_resp.get("result", {}).get("tools", [])
    print(f"    tools/list returned {len(tools)} tools")
    for t in tools[:25]:
        print(f"      - {t.get('name')}")

    if args.smoke_slack:
        # pick a read-only tool that doesn't require extra inputs
        # from your output, slack_list_users has no required fields.
        print("[4/4] Slack smoke test: slack_list_users(limit=1)")
        try:
            smoke_resp = post_and_wait_jsonrpc_sse(
                session,
                url,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "slack_list_users",
                        "arguments": {"limit": 1, "response_format": "concise"},
                    },
                },
                origin=args.origin,
                timeout=args.timeout,
                wait_s=args.wait,
            )
        except Exception as e:
            print(f"ERROR: Slack smoke test failed: {e}")
            return 1

        if smoke_resp is None or "error" in smoke_resp:
            print("ERROR: Slack smoke test returned error:")
            print(json.dumps(smoke_resp, indent=2))
            return 1

        # MCP tools/call typically returns {content:[...]} etc; just print summary
        print("    Slack smoke test succeeded.")
        # print first 400 chars for debugging
        print("    response snippet:", json.dumps(smoke_resp)[:400], "...")

    print("\nSUCCESS: StreamableHTTP validation passed (SSE responses on /mcp/).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

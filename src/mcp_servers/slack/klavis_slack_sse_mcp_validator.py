#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Iterator, Optional

import requests


@dataclass
class SseEvent:
    event: str
    data: str


def iter_sse_events(resp: requests.Response) -> Iterator[SseEvent]:
    event_name = "message"
    data_lines = []

    for raw in resp.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw.strip("\n")

        if line == "":
            if data_lines:
                yield SseEvent(event=event_name, data="\n".join(data_lines))
            event_name = "message"
            data_lines = []
            continue

        if line.startswith(":"):
            continue

        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())

    if data_lines:
        yield SseEvent(event=event_name, data="\n".join(data_lines))


def post_json(session: requests.Session, url: str, payload: dict, origin: str, timeout: float) -> None:
    r = session.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json", "Origin": origin},
        timeout=timeout,
    )
    r.raise_for_status()


def wait_for_response(events: Iterator[SseEvent], want_id: int, timeout_s: float) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            ev = next(events)
        except StopIteration:
            break

        # Most servers deliver JSON-RPC messages as event "message"
        if ev.event != "message":
            continue

        try:
            msg = json.loads(ev.data)
        except json.JSONDecodeError:
            continue

        if isinstance(msg, dict) and msg.get("id") == want_id:
            return msg

    raise TimeoutError(f"Timed out waiting for response id={want_id}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:5000")
    ap.add_argument("--sse-path", default="/sse")
    ap.add_argument("--origin", default="http://localhost")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--wait", type=float, default=15.0)
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    sse_url = base + args.sse_path

    session = requests.Session()

    print(f"[1/4] Connect SSE: {sse_url}")
    try:
        resp = session.get(
            sse_url,
            headers={"Accept": "text/event-stream", "Cache-Control": "no-cache", "Origin": args.origin},
            stream=True,
            timeout=args.timeout,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"ERROR: SSE connect failed: {e}")
        return 1

    events = iter_sse_events(resp)

    print("[2/4] Wait for SSE event 'endpoint'")
    endpoint_url: Optional[str] = None
    deadline = time.time() + args.wait
    while time.time() < deadline:
        try:
            ev = next(events)
        except StopIteration:
            break
        if ev.event == "endpoint":
            endpoint_url = ev.data.strip()
            break

    if not endpoint_url:
        print("ERROR: did not receive 'endpoint' event on SSE.")
        return 1

    if endpoint_url.startswith("/"):
        post_url = base + endpoint_url
    elif endpoint_url.startswith("http://") or endpoint_url.startswith("https://"):
        post_url = endpoint_url
    else:
        post_url = base + "/" + endpoint_url.lstrip("/")

    print(f"    POST endpoint: {post_url}")

    print("[3/4] initialize")
    init_id = 1
    initialize = {
        "jsonrpc": "2.0",
        "id": init_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"roots": {"listChanged": True}, "sampling": {}},
            "clientInfo": {"name": "mcp-sse-validator", "version": "0.1.0"},
        },
    }

    try:
        post_json(session, post_url, initialize, origin=args.origin, timeout=args.timeout)
        init_resp = wait_for_response(events, want_id=init_id, timeout_s=args.wait)
    except Exception as e:
        print(f"ERROR: initialize failed: {e}")
        return 1

    if "error" in init_resp:
        print("ERROR: initialize returned error:")
        print(json.dumps(init_resp["error"], indent=2))
        return 1

    print("    Sending notifications/initialized")
    try:
        post_json(session, post_url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, args.origin, args.timeout)
    except Exception as e:
        print(f"ERROR: notifications/initialized failed: {e}")
        return 1

    print("[4/4] tools/list")
    tools_id = 2
    tools_list = {"jsonrpc": "2.0", "id": tools_id, "method": "tools/list", "params": {}}
    try:
        post_json(session, post_url, tools_list, origin=args.origin, timeout=args.timeout)
        tools_resp = wait_for_response(events, want_id=tools_id, timeout_s=args.wait)
    except Exception as e:
        print(f"ERROR: tools/list failed: {e}")
        return 1

    if "error" in tools_resp:
        print("ERROR: tools/list returned error:")
        print(json.dumps(tools_resp["error"], indent=2))
        return 1

    tools = tools_resp.get("result", {}).get("tools", [])
    print(f"    tools: {len(tools)}")
    for t in tools[:50]:
        print(f"      - {t.get('name')}: {str(t.get('description',''))[:120]}")

    print("\nSUCCESS: SSE MCP transport looks good.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

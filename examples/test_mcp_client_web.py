"""MCP Client 设置页、Web API 与一次性权限确认测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.webapp import ResearchWebApp


def expect(name, condition):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        raise SystemExit(f"断言失败: {name}")


def request(base: str, path: str, payload=None):
    data = (json.dumps(payload).encode("utf-8")
            if payload is not None else None)
    req = Request(
        base + path, data=data,
        method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=5) as response:
        return response.status, response.read().decode("utf-8"), dict(response.headers)


def main() -> None:
    root = Path(tempfile.mkdtemp())
    data_dir = root / "downloads"
    data_dir.mkdir()
    previous_cwd = Path.cwd()
    previous_data = os.environ.get("PAPER_STUDIO_DATA_DIR")
    os.chdir(root)
    os.environ["PAPER_STUDIO_DATA_DIR"] = str(data_dir)
    app = ResearchWebApp(runner=lambda *_args, **_kwargs: {})
    server = app._make_server(port=0)  # type: ignore[attr-defined]
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        _status, html, _headers = request(base, "/")
        expect("设置页包含 MCP 连接中心", "MCP 连接中心" in html and
               "mcpConnectionForm" in html)
        status, body, _headers = request(base, "/api/mcp-client/servers")
        initial = json.loads(body)
        expect("Web API 声明 Server + Client 双角色",
               status == 200 and initial["role"] == "server_and_client")

        _status, body, _headers = request(base, "/api/mcp-client/server-save", {
            "name": "Web fixture",
            "category": "filesystem",
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-V"],
            "permissions": {"resources_read": True, "tools_call": False},
        })
        saved = json.loads(body)
        server_id = saved["id"]
        expect("新连接通过 API 保存且未信任",
               server_id.startswith("mcp-") and saved["trusted"] is False)

        _status, body, _headers = request(
            base, "/api/mcp-client/permission-request", {
                "operation": "delete", "server_id": server_id,
            })
        challenge = json.loads(body)
        _status, body, _headers = request(
            base, "/api/mcp-client/permission-approve", {
                "challenge_id": challenge["challenge_id"], "approved": False,
            })
        expect("用户拒绝时不签发权限令牌",
               json.loads(body)["permission_token"] is None)
        try:
            request(base, "/api/mcp-client/server-delete", {
                "server_id": server_id, "permission_token": "",
            })
            missing_token_blocked = False
        except HTTPError as err:
            missing_token_blocked = err.code == 409
        expect("无权限令牌不能删除连接", missing_token_blocked)

        _status, body, _headers = request(
            base, "/api/mcp-client/permission-request", {
                "operation": "delete", "server_id": server_id,
            })
        challenge = json.loads(body)
        _status, body, _headers = request(
            base, "/api/mcp-client/permission-approve", {
                "challenge_id": challenge["challenge_id"], "approved": True,
            })
        token = json.loads(body)["permission_token"]
        _status, body, _headers = request(
            base, "/api/mcp-client/server-delete", {
                "server_id": server_id, "permission_token": token,
            })
        expect("确认后一次性令牌可删除精确连接",
               json.loads(body)["deleted"] is True)
        _status, body, _headers = request(base, "/api/mcp-client/servers")
        expect("删除后连接列表即时更新",
               json.loads(body)["servers"] == [])
    finally:
        server.shutdown()
        server.server_close()
        app._schedule_stop.set()
        os.chdir(previous_cwd)
        if previous_data is None:
            os.environ.pop("PAPER_STUDIO_DATA_DIR", None)
        else:
            os.environ["PAPER_STUDIO_DATA_DIR"] = previous_data
    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

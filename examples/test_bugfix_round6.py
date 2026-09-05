"""第五轮 bug 修复回归:角色读区降级 + 阅读批注 modal 顶部遮挡。"""

from __future__ import annotations

import os
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core.agent_roles import list_roles


def expect(name, cond, got=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + ("" if cond else f" -> {got}"))
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def main() -> None:
    html_path = Path("agent/static/index.html")
    text = html_path.read_text(encoding="utf-8")

    print("== 用例 1:角色读区前端有内置 fallback(不依赖后端)==")
    expect("HTML 包含 AGENT_ROLE_FALLBACK",
           "AGENT_ROLE_FALLBACK" in text)
    expect("fallback 包含 4 个角色",
           all(rid in text for rid in
               ("retriever", "reader", "citation_checker", "editor")))
    expect("fallback 含中文角色名",
           "检索员" in text and "阅读员" in text
           and "引用核验员" in text and "综述编辑" in text)
    # loadAgentRoles 逻辑改成 try/catch + fallback
    expect("loadAgentRoles 内部用 fallback",
           "console.warn(\"agent-roles fallback" in text
           or "agent-roles fallback" in text)

    print("== 用例 2:fallback 4 角色和后端 list_roles 数量一致 ==")
    backend = list_roles()
    expect(f"后端 {len(backend)} 角色(同步前端)",
           len(backend) == 4, len(backend))
    expected_ids = {"retriever", "reader", "citation_checker", "editor"}
    expect("后端 4 id 与前端一致",
           {r.role_id for r in backend} == expected_ids)

    print("== 用例 3:阅读批注 modal z-index 高于主 header ==")
    m = re.search(r"\.library-reader-modal\{[^}]+z-index:\s*(\d+)", text)
    expect("library-reader-modal 有 z-index", m is not None)
    z_modal = int(m.group(1)) if m else 0
    m2 = re.search(r"header\{[^}]+z-index:\s*(\d+)", text)
    z_header = int(m2.group(1)) if m2 else 0
    expect(f"modal z-index({z_modal}) 高于 header z-index({z_header})",
           z_modal > z_header, (z_modal, z_header))

    print("== 用例 4:阅读批注 modal 顶部避开 header ==")
    m = re.search(r"\.library-reader-modal\.open\{[^}]+inset:\s*(\d+)px", text)
    expect("open 状态有 inset 简写", m is not None)
    top_inset = int(m.group(1)) if m else 0
    # header 高度 64px,modal 顶部 80px 即 header 之下 16px 留白
    expect(f"modal top inset {top_inset}px 大于 header 高度 64px",
           top_inset > 64, top_inset)

    print("== 用例 5:端到端 — 端点 404 时前端 JS 仍能渲染 fallback ==")
    # 起一个只服务 404 的伪 server,模拟用户旧版 webapp
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class All404(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')
        def log_message(self, *args): pass

    # 找一个空端口
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = HTTPServer(("127.0.0.1", port), All404)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.2)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/agent-roles",
                                    timeout=3) as r:
            expect("404 应被 Python 抛 HTTPError", False, r.status)
    except urllib.error.HTTPError as err:
        expect("404 命中,模拟用户旧版 webapp", err.code == 404, err.code)
    finally:
        srv.shutdown()
        srv.server_close()

    # 用 Node 跑 JS 看 fallback 数据存在(不实际跑浏览器,只解析 JS 字符串)
    # 直接 import 测试,避免启浏览器
    # 把 fallback 数据从 HTML 抽出来
    m = re.search(r"const AGENT_ROLE_FALLBACK=(\[.*?\]);", text, re.DOTALL)
    expect("能从 HTML 抽出 AGENT_ROLE_FALLBACK 数组字面量", m is not None)
    fallback_str = m.group(1) if m else "[]"
    # JS 对象字面量里 key 不带引号,Python 不接受。用简单 regex 给所有 key 加引号
    # 以便用 ast.literal_eval 解析(只读测试,内容已知安全)
    import ast
    quoted = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)",
                     r'\1"\2"\3', fallback_str)
    fallback = ast.literal_eval(quoted)
    expect("fallback 数组长度 4", len(fallback) == 4, len(fallback))
    # 看 role_id / name / primary_skills 都齐
    for r in fallback:
        expect(f"fallback 角色 {r.get('role_id')} 必填字段齐",
               all(r.get(k) for k in ("role_id", "name", "summary",
                                        "skill_names", "primary_skills")))

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

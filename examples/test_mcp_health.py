"""第五阶段-2:MCP 健康检查 + 调用审计 + 导入导出 测试(无网络)。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.mcp_client import (
    MCPClientError, MCPClientManager, MCPConnectionStore,
)


def expect(name, cond, got=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + ("" if cond else f" -> {got}"))
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def make_store() -> MCPConnectionStore:
    """建一个临时隔离的 store,避免污染真实 mcp_connections.json。"""
    tmp = Path(tempfile.mkdtemp()) / "mcp.json"
    return MCPConnectionStore(path=tmp)


def add_dummy_server(store: MCPConnectionStore, sid: str = "demo") -> None:
    """在 store 里加一个永远不会连通的 server(本地随机端口)。

    save() 会自动生成 ``mcp-{slug}-{token}`` 形式的 id,这里用 ``sid`` 当
    索引 dict key(便于查询),但**值里的 id 字段**保留自动生成的(只读存
    储不会让测试通过 store.get 找到 sid)。所以本函数仅返回 store 返回的
    真实 id。
    """
    payload = {
        "name": f"Demo {sid}", "transport": "streamable_http",
        "url": "http://127.0.0.1:1/mcp",  # 拒绝连接,但不抛 OS 错误
        "trusted": True, "description": "demo",
    }
    return store.save(payload)["id"]


def main() -> None:
    print("== 用例 1：健康检查记录 + 落 store ==")
    store = make_store()
    sid_1 = add_dummy_server(store, "demo-1")
    entry = store.record_health(sid_1, ok=True, latency_ms=42.0,
                                tools_count=5)
    expect("record_health 返回完整字段",
           entry["ok"] and entry["latency_ms"] == 42.0
           and entry["tools_count"] == 5)
    expect("record_health 后 last_status 写入 server",
           store.get(sid_1)["last_status"] == "connected")

    print("== 用例 2：失败健康检查带 error 字段 ==")
    entry2 = store.record_health(sid_1, ok=False, latency_ms=120.0,
                                 error="connection refused")
    expect("失败时 ok=False",
           entry2["ok"] is False and entry2["error"] == "connection refused")
    expect("失败后 last_status=error",
           store.get(sid_1)["last_status"] == "error")
    expect("失败 last_error 被持久化",
           store.get(sid_1)["last_error"] == "connection refused")

    print("== 用例 3：recent_health 按 server 过滤 ==")
    sid_2 = add_dummy_server(store, "demo-2")
    store.record_health(sid_2, ok=True, latency_ms=10.0, tools_count=3)
    all_log = store.recent_health(limit=10)
    only_1 = store.recent_health(server_id=sid_1, limit=10)
    expect("全量返回包含 2 个 server 的记录", len(all_log) >= 3, len(all_log))
    expect(f"server_id 过滤仅返回 {sid_1}",
           all(e["server_id"] == sid_1 for e in only_1) and len(only_1) >= 2)

    print("== 用例 4：调用审计 record_audit 落 store ==")
    record = store.record_audit(sid_1, "list_tools",
                                arguments={"limit": 5}, ok=True,
                                latency_ms=12.5,
                                permission_token="abcdef1234")
    expect("调用审计字段完整", record["server_id"] == sid_1
           and record["tool"] == "list_tools"
           and record["arguments"]["limit"] == 5
           and record["ok"] is True
           and record["permission_token_prefix"] == "abcdef12")
    items = store.recent_audit(limit=10)
    expect("recent_audit 返回 1 条以上", len(items) >= 1, len(items))

    print("== 用例 5：clear_audit 按 server 清空 ==")
    store.record_audit(sid_2, "noop", ok=True)
    removed = store.clear_audit(server_id=sid_1)
    expect(f"清空 {sid_1} 后剩余仅 sid_2",
           all(e["server_id"] != sid_1
               for e in store.recent_audit(limit=20))
           and removed >= 1, removed)
    removed_all = store.clear_audit()
    expect("clear_audit() 清空所有", store.recent_audit(limit=10) == [],
           removed_all)

    print("== 用例 6：export_config 不含敏感字段 ==")
    payload = store.export_config(include_secrets=False)
    expect("export 携带 schema 字段",
           payload["schema"] == 1 and "servers" in payload)
    expect("export 包含 2 个 server",
           {s["id"] for s in payload["servers"]} >= {sid_1, sid_2})
    expect("默认 export 不含 _include_env",
           all("_include_env" not in s for s in payload["servers"]))

    print("== 用例 7：import_config 默认跳过同名 ==")
    exported = json.dumps(payload)
    fresh = make_store()
    result = fresh.import_config(json.loads(exported))
    expect("import 全部 added", result["added"] == 2
           and result["skipped"] == 0, result)
    again = fresh.import_config(json.loads(exported))
    expect("重复 import 默认全部 skipped",
           again["skipped"] == 2 and again["added"] == 0, again)

    print("== 用例 8：import_config overwrite=True 覆盖 ==")
    again = fresh.import_config(json.loads(exported), overwrite=True)
    expect("overwrite 时全部 replaced",
           again["replaced"] == 2 and again["skipped"] == 0, again)

    print("== 用例 9：import 校验拒坏数据 ==")
    try:
        fresh.import_config({"servers": "not a list"})
        expect("非 list 应被拒", False)
    except ValueError as err:
        expect("ValueError 含「servers」", "servers" in str(err), str(err))

    print("== 用例 10：health_check(单 server) 失败不抛 ==")
    mgr = MCPClientManager(store=make_store())
    bad_sid = add_dummy_server(mgr.store, "demo-bad")
    import asyncio
    result = asyncio.run(mgr.health_check(bad_sid))
    expect("无 server 或连不上时 ok=False",
           result["ok"] is False and result.get("error"), result)
    expect("失败 health 被记入 store",
           mgr.store.recent_health(server_id=bad_sid, limit=1)[0]["ok"]
           is False)

    print("== 用例 11：health_check_all 并发跑 ==")
    add_dummy_server(mgr.store, "demo-bad-2")
    results = asyncio.run(mgr.health_check_all())
    expect("所有 server 都返回结果",
           len(results) >= 2
           and all("server_id" in r for r in results), len(results))

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

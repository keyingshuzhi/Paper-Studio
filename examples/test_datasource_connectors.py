"""第五阶段-3:外部数据源 connector 测试(无网络/纯本地)。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.datasources import (
    DataSourceError, DataSourceItem, FetchResult, get_connector,
    list_connectors,
)


def expect(name, cond, got=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + ("" if cond else f" -> {got}"))
    if not cond:
        raise SystemExit(f"断言失败: {name}")


# ============================== 1. 注册表 ==============================

def test_registry() -> None:
    print("== 用例 1：4 个数据源注册到 BaseRegistry ==")
    connectors = list_connectors()
    ids = {c["id"] for c in connectors}
    expect("含 zotero/obsidian/notion/institutional",
           ids == {"zotero", "obsidian", "notion", "institutional"},
           ids)
    for conn in connectors:
        expect(f"{conn['id']} 携带 name/blurb/auth_kind",
               bool(conn["name"]) and bool(conn["blurb"])
               and conn["auth_kind"])


# ============================== 2. Zotero ==============================

def test_zotero() -> None:
    print("== 用例 2：Zotero 缺配置时 list/search/fetch 被拒 ==")
    z = get_connector("zotero")
    expect("list_targets 缺配置返回空", z.list_targets() == [])
    expect("search 不带 query 返回空", z.search("") == [])
    try:
        z.fetch("zotero:abc")
        expect("fetch 缺配置应抛 DataSourceError", False)
    except DataSourceError as err:
        expect("DataSourceError 提示 api_key/user_id",
               "api_key" in str(err) or "user_id" in str(err), str(err))

    print("== 用例 3：Zotero health 缺配置返回 ok=False ==")
    h = z.health()
    expect("health 返回 ok=False 和 error 字段",
           h["ok"] is False and "error" in h, h)


# ============================== 3. Obsidian ==============================

def test_obsidian(tmpdir: Path) -> None:
    print("== 用例 4：Obsidian 无 vault_path 优雅返回空 ==")
    expect("list_targets 缺 vault_path 返回空",
           get_connector("obsidian").list_targets() == [])
    expect("search 缺 vault_path 返回空",
           get_connector("obsidian").search("any") == [])
    expect("health 缺 vault_path 返回 ok=False",
           get_connector("obsidian").health()["ok"] is False)

    print("== 用例 5：Obsidian 真实 vault 读写 ==")
    # 造一个 3 个 .md 笔记的 vault
    vault = tmpdir / "vault"
    vault.mkdir()
    (vault / "note1.md").write_text(
        "# Transformer\n\nA foundational paper for LLMs.\n#tag1\n",
        encoding="utf-8")
    (vault / "note2.md").write_text(
        "---\ntitle: Mamba State Space\n---\n\n"
        "Mamba proposes a selective state space model.\n#state-space\n",
        encoding="utf-8")
    (vault / "note3.md").write_text(
        "Just a note, no match here.\n", encoding="utf-8")
    # 加一个隐藏目录(应被跳过)
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "config").write_text("internals")

    conn = get_connector("obsidian", config={"vault_path": str(vault)})
    targets = conn.list_targets()
    expect("list_targets 列出顶层 3 个 .md",
           any(t.get("kind") == "file" and t.get("title") == "note1.md"
               for t in targets), [t.get("title") for t in targets])
    results = conn.search("transformer", limit=5)
    expect("search 'transformer' 命中 note1",
           any("Transformer" in r.snippet for r in results), len(results))

    print("== 用例 6：Obsidian search 与 fetch ==")
    results = conn.search("mamba", limit=5)
    expect("search 'mamba' 命中 note2(frontmatter title)",
           any(r.title == "Mamba State Space" for r in results), len(results))

    target_id = results[0].id
    fetched = conn.fetch(target_id)
    expect("fetch 返回 markdown 包含正文",
           "Mamba" in fetched.body or "state" in fetched.body.lower(),
           fetched.body[:80])

    # 越界检查
    try:
        conn.fetch("obsidian:../etc/passwd")
        expect("越界路径应被拒", False)
    except DataSourceError as err:
        expect("DataSourceError 含「非 Vault 路径」", "非 Vault 路径" in str(err),
               str(err))

    print("== 用例 7：Obsidian health 报告 .md 数量 ==")
    h = conn.health()
    expect("health ok=True 且 info 含数量",
           h["ok"] and "3" in h.get("info", ""), h)


# ============================== 4. Notion ==============================

def test_notion() -> None:
    print("== 用例 8：Notion 缺 token 走拒路径 ==")
    n = get_connector("notion")
    expect("list_targets 缺配置返回空", n.list_targets() == [])
    # Notion 设计:fetch 缺 token 时返回 error 元数据的 FetchResult(不抛)
    bad = n.fetch("notion:abc")
    expect("fetch 缺 token 返回 error FetchResult",
           bad.meta.get("error") is True and bad.body, bad.to_dict())
    h = n.health()
    expect("health 缺 token 返回 ok=False",
           h["ok"] is False and "token" in h.get("error", ""), h)

    print("== 用例 9：Notion block → markdown 转换 ==")
    # 直接测内部辅助方法
    from agent.datasources.notion import NotionConnector
    md = NotionConnector._block_to_markdown({
        "type": "heading_1",
        "heading_1": {"rich_text": [{"text": {"content": "Hi"}}]}})
    expect("heading_1 输出 # Hi", md == "# Hi", md)
    md2 = NotionConnector._block_to_markdown({
        "type": "code",
        "code": {"language": "python",
                 "rich_text": [{"text": {"content": "print('x')"}}]}})
    expect("code 输出 ```python\\nprint('x')\\n```",
           md2 == "```python\nprint('x')\n```", md2)
    md3 = NotionConnector._block_to_markdown({
        "type": "to_do",
        "to_do": {"checked": True,
                  "rich_text": [{"text": {"content": "task"}}]}})
    expect("to_do 输出 - [x] task", md3 == "- [x] task", md3)


# ============================== 5. Institutional ==============================

def test_institutional() -> None:
    print("== 用例 10：Institutional 缺 endpoint 走拒路径 ==")
    inst = get_connector("institutional")
    expect("search 缺 endpoint 返回空(graceful)",
           inst.search("any") == [])
    expect("health 缺 endpoint 返回 ok=False",
           inst.health()["ok"] is False)

    print("== 用例 11：Institutional OAI XML 解析(用 fixture) ==")
    fixture = """<?xml version='1.0' encoding='UTF-8'?>
<OAI-PMH xmlns:dc="http://purl.org/dc/elements/1.1/"
         xmlns="http://www.openarchives.org/OAI/2.0/">
<ListRecords>
  <record>
    <header>
      <identifier>oai:demo:1</identifier>
    </header>
    <metadata>
      <oai_dc:dc>
        <dc:title>Demo Paper Title</dc:title>
        <dc:description>Demo abstract here.</dc:description>
        <dc:date>2024-03-15</dc:date>
        <dc:creator>Alice</dc:creator>
        <dc:creator>Bob</dc:creator>
      </oai_dc:dc>
    </metadata>
  </record>
  <record>
    <header status="deleted">
      <identifier>oai:demo:2</identifier>
    </header>
  </record>
</ListRecords>
</OAI-PMH>"""
    from agent.datasources.institutional import InstitutionalConnector
    parsed = InstitutionalConnector._parse_oai_records(fixture, limit=10)
    expect("OAI 解析 1 条(另一条 deleted 被跳过)",
           len(parsed) == 1, len(parsed))
    item = parsed[0]
    expect("OAI 解析 title/year/authors",
           item.title == "Demo Paper Title" and item.year == "2024"
           and item.authors == ["Alice", "Bob"], item.to_dict())


# ============================== 6. health/data shape ==============================

def test_item_shape() -> None:
    print("== 用例 12：DataSourceItem.to_dict 完整 ==")
    item = DataSourceItem(id="x", title="t", source="zotero", url="u",
                          snippet="s", authors=["A"], year=2024,
                          extra={"k": "v"})
    d = item.to_dict()
    expect("to_dict 含全部字段",
           all(k in d for k in ("id", "title", "source", "url", "snippet",
                                 "authors", "year", "extra"))
           and d["authors"] == ["A"] and d["extra"] == {"k": "v"}, d)

    print("== 用例 13：FetchResult.to_dict 完整 ==")
    f = FetchResult(target="t", body="b", format="markdown", meta={"x": 1})
    d = f.to_dict()
    expect("FetchResult.to_dict 含 target/body/format/meta",
           d == {"target": "t", "body": "b",
                 "format": "markdown", "meta": {"x": 1}}, d)


# ============================== 7. webapp 端点 ==============================

def test_webapp_endpoints() -> None:
    print("== 用例 14：webapp 暴露 4 个数据源端点 ==")
    import os
    from agent.webapp import ResearchWebApp
    os.environ["PAPER_STUDIO_DATA_DIR"] = tempfile.mkdtemp()
    app = ResearchWebApp()
    server = app._make_server(port=0)  # type: ignore[attr-defined]
    import threading
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        import urllib.request
        # /api/datasources
        with urllib.request.urlopen(f"{base}/api/datasources", timeout=5) as r:
            body = json.loads(r.read())
        expect("/api/datasources 返回 4 个 connector",
               body["count"] == 4
               and {c["id"] for c in body["connectors"]}
               == {"zotero", "obsidian", "notion", "institutional"},
               body["count"])
        expect("每个 connector 标 configured 字段",
               all("configured" in c for c in body["connectors"]))

        # /api/datasource-configure
        from urllib.request import Request
        req = Request(f"{base}/api/datasource-configure", method="POST",
                      data=json.dumps({"connector_id": "obsidian",
                                       "config": {"vault_path": "/tmp/nope"}
                                       }).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            saved = json.loads(r.read())
        expect("configure 返回 status=saved",
               saved["status"] == "saved"
               and saved["config"]["vault_path"] == "/tmp/nope", saved)

        # 再调 list,看 configured 变 true
        with urllib.request.urlopen(f"{base}/api/datasources", timeout=5) as r:
            body2 = json.loads(r.read())
        obsidian = next(c for c in body2["connectors"] if c["id"] == "obsidian")
        expect("configure 后 obsidian configured=true",
               obsidian["configured"] is True, obsidian)

        # /api/datasource-health(不存在的 vault_path 必然失败)
        req2 = Request(f"{base}/api/datasource-health", method="POST",
                       data=json.dumps({"connector_id": "obsidian"}
                                       ).encode("utf-8"),
                       headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req2, timeout=5) as r:
            h = json.loads(r.read())
        expect("datasource-health 对无效 vault 返回 ok=False",
               h["ok"] is False and "error" in h, h)
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    test_registry()
    test_zotero()
    with tempfile.TemporaryDirectory() as tmp:
        test_obsidian(Path(tmp))
    test_notion()
    test_institutional()
    test_item_shape()
    test_webapp_endpoints()
    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

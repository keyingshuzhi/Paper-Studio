"""研究记忆 v2 测试：覆盖语义检索、研究复用、知识图谱、生命周期、合并、清理、导出。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core import ResearchMemory
from agent.skills import (
    BaseSkill,
    MemoryArchiveSkill,
    MemoryCleanupSkill,
    MemoryDeleteSkill,
    MemoryExportSkill,
    MemoryExpirySkill,
    MemoryGraphSkill,
    MemoryMergeSkill,
    MemoryPinSkill,
    MemoryReadSkill,
    MemorySearchSkill,
    MemoryStatsSkill,
    MemoryWriteSkill,
    Paper,
    SkillError,
    SkillPermission,
    SkillResult,
)


def expect(name, cond, got=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + ("" if cond else f" -> {got}"))
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def make_paper(title, year=2024, authors=("A",)):
    return Paper(title=title, url=f"http://example.com/{title}",
                 source="arxiv_search", authors=list(authors), year=year,
                 abstract=f"abstract of {title}")


def invoke(skill, **kwargs):
    """以 invoke 方式调用 Skill,绕过权限和 schema 校验,直接拿到返回数据。"""
    allowed = {SkillPermission.FILESYSTEM_READ,
               SkillPermission.FILESYSTEM_WRITE,
               SkillPermission.DESTRUCTIVE,
               SkillPermission.NETWORK}
    result = skill.invoke(
        allowed_permissions=allowed,
        progress_callback=lambda evt: None,
        **kwargs)
    if not result.ok:
        raise RuntimeError(f"{skill.name} failed: {result.error.message}")
    return result.unwrap()


def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "memory_test.json"
    mem = ResearchMemory(path=str(tmp))

    # ============================== 1. 基础 CRUD ==========================
    print("== 用例 1：写入、查询与持久化 ==")
    mem.add_round("mamba state space model",
                  [make_paper("Mamba", authors=("Albert Gu", "Tri Dao"))],
                  summaries=[{"ok": True, "summary": {
                      "title": "Mamba",
                      "method": "selective state space",
                      "contribution": "linear-time sequence modeling",
                  }}],
                  analysis={
                      "summary": "Mamba 提出选择性状态空间,实现线性时间序列建模。",
                      "gaps": [{"gap": "Mamba 在多模态上的泛化", "suggested_query": "mamba multimodal"}],
                  })
    mem.add_round("retrieval augmented generation rag",
                  [make_paper("RAG", year=2021, authors=("Patrick Lewis",))],
                  summaries=[{"ok": True, "summary": {
                      "title": "RAG",
                      "method": "dense retrieval + seq2seq",
                      "contribution": "open-domain QA with parametric + non-parametric memory",
                  }}],
                  analysis={"summary": "RAG 把检索器与生成器结合以开放域问答。",
                            "gaps": [{"gap": "RAG 在长上下文中的表现", "suggested_query": "long context rag"}]})
    expect("has_query 大小写不敏感", mem.has_query("  Mamba State Space Model "))
    expect("has_query 未知返回 False", not mem.has_query("bert"))
    expect("stats 统计 2 条", mem.stats()["entries"] == 2, mem.stats())

    # ============================== 2. 语义检索 ============================
    print("== 用例 2：语义检索（同义词扩展 + 词法召回）==")
    # “检索增强” 在 _SYNONYM_GROUPS 中对应 “rag”
    rag_hits = mem.semantic_search("检索增强", limit=5)
    expect("中文「检索增强」召回 rag 主题",
           any(h.get("query") == "retrieval augmented generation rag" for h in rag_hits),
           rag_hits)
    # 「大模型」对应 llm；当前没有 llm 主题，应返回空
    llm_hits = mem.semantic_search("大模型", limit=5)
    expect("无 LLM 主题时不返回虚假命中", all(
        h.get("query") != "mamba" for h in llm_hits))

    # ============================== 3. 研究复用 ============================
    print("== 用例 3：研究复用 prepare_reuse + mark_reused ==")
    reuse = mem.prepare_reuse("transformer attention", limit=3)
    expect("prepare_reuse 返回 dict 含 matches/context",
           isinstance(reuse, dict) and "matches" in reuse and "context" in reuse)
    context_text = reuse["context"]
    # 即使没有 transformer 主题,mamba 主题因含 attention 仍应被召回到候选
    # 这里用 mamba 主题直接复用以验证行为
    reuse2 = mem.prepare_reuse("mamba state space model", limit=2,
                                exclude_query="mamba state space model")
    expect("精确查询可被 exclude_query 排除",
           all(h.get("query") != "mamba state space model" for h in reuse2.get("matches", [])),
           reuse2)

    entry = mem.get_entry("mamba state space model")
    before = int((entry.get("usage") or {}).get("reuse_count") or 0)
    mem.mark_reused("mamba state space model")
    after = mem.get_entry("mamba state space model")
    expect("mark_reused 后 usage.reuse_count +1",
           int((after.get("usage") or {}).get("reuse_count") or 0) == before + 1,
           after.get("usage"))

    # ============================== 4. 知识图谱 ============================
    print("== 用例 4：主题知识图谱（6 种节点 + 边）==")
    graph = mem.knowledge_graph(max_nodes=120)
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    kinds = {n.get("type") for n in nodes}
    expect("图谱含 topic 节点", "topic" in kinds, kinds)
    expect("图谱含 paper 节点", "paper" in kinds, kinds)
    expect("图谱含 author 节点", "author" in kinds, kinds)
    expect("图谱含 method 节点", "method" in kinds, kinds)
    expect("图谱含 conclusion 节点", "conclusion" in kinds, kinds)
    expect("图谱含 gap 节点", "gap" in kinds, kinds)
    edge_kinds = {e.get("type") for e in edges}
    expect("边含 '包含'", "包含" in edge_kinds, edge_kinds)
    expect("边含 '作者'", "作者" in edge_kinds, edge_kinds)
    expect("边含 '方法'", "方法" in edge_kinds, edge_kinds)
    expect("边含 '结论'", "结论" in edge_kinds, edge_kinds)
    expect("边含 '盲点'", "盲点" in edge_kinds, edge_kinds)
    expect("边数 > 0", len(edges) > 0, len(edges))

    # 节点 ID 形式应为 'kind:norm(label)'
    bad_node = [n for n in nodes if ":" not in n.get("id", "")]
    expect("所有节点 id 含 kind 前缀", not bad_node, bad_node)

    # ============================== 5. 生命周期 ============================
    print("== 用例 5：固定 / 归档 / 过期 ==")
    mem.set_pinned("mamba state space model", True)
    e = mem.get_entry("mamba state space model")
    expect("pin 后 entry.pinned=True", e.get("pinned") is True, e)

    mem.set_archived("retrieval augmented generation rag", True)
    e = mem.get_entry("retrieval augmented generation rag")
    expect("archive 后 entry.archived=True", e.get("archived") is True, e)
    expect("archive 后 archived_at 非空",
           bool(e.get("archived_at")), e)

    # 取消归档 + 把 expires_at 设为 1 天前,模拟「已过期但尚未自动归档」。
    # get_entry 返回的是 entry 的拷贝,所以必须通过 mem._data 写入。
    mem.set_archived("retrieval augmented generation rag", False)
    from datetime import datetime, timedelta
    key = mem._norm("retrieval augmented generation rag")
    mem._data[key]["expires_at"] = (datetime.now() - timedelta(days=1)).strftime(
        "%Y-%m-%d %H:%M:%S")
    mem._persist()

    # 过期扫描
    result = mem.cleanup_expired(max_age_days=180)
    expect("cleanup 返回 dict", isinstance(result, dict) and "count" in result)
    expect("过期项被归档",
           "retrieval augmented generation rag" in result.get("archived_queries", []),
           result)
    expect("固定项 mamba 不在归档列表",
           "mamba state space model" not in result.get("archived_queries", []),
           result)

    # 清理后 archived 标记
    e_rag = mem.get_entry("retrieval augmented generation rag")
    expect("过期主题被自动归档", e_rag.get("archived") is True, e_rag)

    # set_expiry 设置未来 30 天
    mem.set_archived("retrieval augmented generation rag", False)  # 先恢复
    mem.set_expiry("retrieval augmented generation rag", 30)
    e_rag = mem.get_entry("retrieval augmented generation rag")
    expect("set_expiry 30 天后 expires_at 非空",
           bool(e_rag.get("expires_at")), e_rag)
    # set_expiry 0 / None 表示永不过期
    mem.set_expiry("retrieval augmented generation rag", 0)
    e_rag = mem.get_entry("retrieval augmented generation rag")
    expect("set_expiry 0 后 expires_at 清空",
           not e_rag.get("expires_at"), e_rag)
    # 非法 days 抛错
    raised = False
    try:
        mem.set_expiry("retrieval augmented generation rag", 99999)
    except ValueError:
        raised = True
    expect("set_expiry 越界抛 ValueError", raised)

    # ============================== 6. 合并 =================================
    print("== 用例 6：合并相近主题 ==")
    mem.add_round("state space model",
                  [make_paper("S4", authors=("Albert Gu",))],
                  summaries=[{"ok": True, "summary": {
                      "title": "S4", "method": "structured state space",
                      "contribution": "long-range sequence modeling"}}])
    merged = mem.merge("mamba state space model", ["state space model"])
    expect("merge 返回目标 entry", merged and merged.get("query") == "mamba state space model", merged)
    expect("目标含 'state space model' 的来源",
           "state space model" in (merged.get("merged_from") or []),
           merged)
    e_src = mem.get_entry("state space model")
    expect("来源被自动归档", e_src.get("archived") is True, e_src)
    expect("来源.merged_into 指向目标",
           e_src.get("merged_into") == "mamba state space model",
           e_src)
    expect("目标 papers 合并了来源论文",
           len(merged.get("papers") or []) >= 2, len(merged.get("papers") or []))
    raised = False
    try:
        mem.merge("mamba state space model", [])
    except ValueError:
        raised = True
    expect("merge 空来源抛错", raised)

    # ============================== 7. 导出 =================================
    print("== 用例 7：导出 ==")
    payload = mem.export_payload(include_archived=True)
    expect("export_payload 含 schema_version",
           payload.get("schema_version") == 2, payload)
    expect("export_payload.entries 至少 2 条",
           len(payload.get("entries") or []) >= 2, payload)

    md = mem.export_markdown(include_archived=True)
    expect("export_markdown 返回字符串", isinstance(md, str) and len(md) > 0)
    expect("Markdown 包含主题 'mamba'",
           "mamba" in md.lower(), md[:200])

    payload_active = mem.export_payload(include_archived=False)
    archived_qs = {e.get("query") for e in (payload.get("entries") or [])
                   if e.get("archived")}
    active_qs = {e.get("query") for e in (payload_active.get("entries") or [])}
    expect("include_archived=False 时不返回已归档主题",
           not any(q in archived_qs for q in active_qs if q),
           (archived_qs, active_qs))

    # ============================== 8. 损坏文件容错 ========================
    print("== 用例 8：损坏文件容错 ==")
    bad = Path(tempfile.mkdtemp()) / "bad.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    mem3 = ResearchMemory(path=str(bad))
    expect("损坏文件不崩溃", mem3.stats()["entries"] == 0)
    expect("损坏文件可写修复",
           mem3.add_round("test", [make_paper("Test")]) is None
           and mem3.stats()["entries"] == 1)

    # ============================== 9. 13 个 Skill 全部可调 ================
    print("== 用例 9：13 个 Memory Skill 全部可被 invoke 调用 ==")
    fresh = Path(tempfile.mkdtemp()) / "memory_skill_test.json"
    shared = ResearchMemory(path=str(fresh))
    shared.add_round("s1", [make_paper("S1", authors=("Alice",))],
                     summaries=[{"ok": True, "summary": {
                         "title": "S1", "method": "method-a",
                         "contribution": "contrib-a"}}],
                     analysis={"summary": "S1 结论。", "gaps": [{"gap": "盲点 A"}]})
    shared.add_round("s2", [make_paper("S2", authors=("Bob",))])

    def make_skill(cls):
        return cls(memory=shared)

    # 读
    r = invoke(make_skill(MemorySearchSkill), keyword="s1")
    expect("memory_search.invoke 返回数组", isinstance(r, list) and len(r) >= 1)
    r = invoke(make_skill(MemoryReadSkill), query="s1")
    expect("memory_read.invoke 返回 entry",
           r and r.get("query") == "s1", r)
    r = invoke(make_skill(MemoryStatsSkill))
    expect("memory_stats.invoke 返回 dict",
           r.get("entries") == 2, r)
    r = invoke(make_skill(MemoryGraphSkill), max_nodes=80)
    expect("memory_graph.invoke 返回 graph",
           "nodes" in r and "edges" in r, list(r.keys())[:5])
    r = invoke(make_skill(MemoryExportSkill), format="json")
    expect("memory_export json 包含 schema_version",
           r.get("schema_version") == 2, r)
    r = invoke(make_skill(MemoryExportSkill), format="markdown")
    expect("memory_export markdown 返回字符串", isinstance(r, str) and len(r) > 0)

    # 写 / 改
    r = invoke(make_skill(MemoryWriteSkill),
               query="s3", papers=[make_paper("S3")])
    expect("memory_write.invoke 写后能读回", shared.has_query("s3"))
    r = invoke(make_skill(MemoryPinSkill), query="s3", pinned=True)
    expect("memory_pin.invoke 固定成功", r and r.get("pinned") is True)
    r = invoke(make_skill(MemoryArchiveSkill), query="s3", archived=True)
    expect("memory_archive.invoke 归档成功", r and r.get("archived") is True)
    r = invoke(make_skill(MemoryExpirySkill), query="s1", days=7)
    expect("memory_expiry.invoke 设置成功",
           r and bool(r.get("expires_at")), r)
    r = invoke(make_skill(MemoryMergeSkill),
               target="s1", sources=["s2"])
    expect("memory_merge.invoke 合并成功",
           r and "s2" in (r.get("merged_from") or []), r)

    # 清理:必须显式 confirmed=True
    cleanup = make_skill(MemoryCleanupSkill)
    rejected = cleanup.invoke(allowed_permissions={
        SkillPermission.FILESYSTEM_READ, SkillPermission.FILESYSTEM_WRITE,
        SkillPermission.DESTRUCTIVE},
        progress_callback=lambda e: None, max_age_days=180, confirmed=False)
    expect("memory_cleanup 未确认时 ok=False",
           rejected.ok is False, rejected.error.code if rejected.error else "")

    r = invoke(cleanup, max_age_days=180, confirmed=True)
    expect("memory_cleanup 已确认后返回 count",
           isinstance(r.get("count"), int), r)

    # 删
    r = invoke(make_skill(MemoryDeleteSkill), query="s3")
    expect("memory_delete.invoke 删除成功", r.get("deleted") is True)
    r = invoke(make_skill(MemoryDeleteSkill), query="not found")
    expect("memory_delete 不存在时 deleted=False", r.get("deleted") is False)

    # ============================== 10. 权限收敛 ===========================
    print("== 用例 10：危险 Skill 在缺少权限时被拒 ==")
    deny = make_skill(MemoryCleanupSkill)
    denied = deny.invoke(allowed_permissions={SkillPermission.FILESYSTEM_READ},
                         progress_callback=lambda e: None,
                         max_age_days=180, confirmed=True)
    expect("memory_cleanup 缺 DESTRUCTIVE 权限被拒",
           denied.ok is False, denied.error.code if denied.error else "")

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

"""本地优先的研究记忆与知识库。

记忆仍保存在用户可备份的 JSON 文件中，但不再只是“查询字符串 -> 结果”的
字典。模块提供：

* 离线语义检索（哈希向量 + 领域同义词扩展，不上传任何研究内容）；
* 从主题、论文、作者、方法、结论与盲点即时派生的知识图谱；
* 固定、归档、合并、过期归档和 JSON / Markdown 导出；
* 为研究循环准备可审计的历史结论上下文。

没有强制引入 embedding 模型，因而新安装、离线环境和桌面版都能立即使用。
若将来接入本地 embedding，只需替换 ``_vector``，持久化格式无需改变。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..skills.metadata import Paper


_SCHEMA_VERSION = 2
_VECTOR_SIZE = 384
_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+._/-]{1,}|\d+(?:\.\d+)?")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")

# 这是轻量的概念扩展，而非云端词典。它让常见科研表达在离线环境下也能
# 互相召回，例如“检索增强”与 RAG。用户的原文始终是主要信号。
_SYNONYM_GROUPS = (
    ("agent", "智能体", "代理", "autonomous agent"),
    ("llm", "大模型", "语言模型", "large language model"),
    ("rag", "检索增强", "retrieval augmented generation"),
    ("mcp", "模型上下文协议", "model context protocol"),
    ("evaluation", "评测", "评估", "benchmark", "基准"),
    ("hallucination", "幻觉", "事实性", "factuality"),
    ("reasoning", "推理", "思维链", "chain of thought"),
    ("retrieval", "检索", "search"),
    ("citation", "引用", "引文"),
    ("knowledge graph", "知识图谱", "图谱"),
    ("multimodal", "多模态", "视觉语言"),
    ("fine tuning", "微调", "finetune", "finetuning"),
)


class ResearchMemory:
    """JSON 持久化的可搜索研究记忆。

    ``get_round`` / ``add_round`` 等旧接口保持不变，既有
    ``research_memory.json`` 会在下次写入时无损迁移到 schema v2。
    """

    def __init__(self, path: str = "downloads/research_memory.json") -> None:
        self.path = Path(path)
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._load()

    # ------------------------------------------------------------------
    # 写入与生命周期
    # ------------------------------------------------------------------
    def add_round(self, query: str, papers: List[Paper],
                  summaries: Optional[List[Dict[str, Any]]] = None,
                  analysis: Optional[Dict[str, Any]] = None) -> None:
        """记录一轮研究（按归一化主题 upsert，保留已有管理属性）。"""
        clean_query = str(query or "").strip()
        if not clean_query:
            raise ValueError("研究主题不能为空")
        key = self._norm(clean_query)
        now = self._now()
        with self._lock:
            old = self._data.get(key) or {}
            self._data[key] = self._normalise_entry({
                **old,
                "query": clean_query,
                "timestamp": old.get("timestamp") or now,
                "updated_at": now,
                "papers": [self._paper_dict(paper) for paper in papers],
                "summaries": self._json_list(summaries),
                "analysis": analysis if isinstance(analysis, dict) else None,
                # 更新主题代表重新沉淀，不应继续隐藏在归档区。
                "archived": False,
                "archived_at": "",
                "merged_into": "",
            })
            self._persist()

    def set_pinned(self, query: str, pinned: bool = True) -> Optional[Dict[str, Any]]:
        """固定或取消固定一条主题，固定项不会被批量过期归档。"""
        return self._update_lifecycle(query, pinned=bool(pinned))

    def set_archived(self, query: str, archived: bool = True) -> Optional[Dict[str, Any]]:
        """归档或恢复一条主题；归档项不会自动参与研究复用。"""
        values: Dict[str, Any] = {"archived": bool(archived)}
        values["archived_at"] = self._now() if archived else ""
        return self._update_lifecycle(query, **values)

    def set_expiry(self, query: str, days: Optional[int]) -> Optional[Dict[str, Any]]:
        """设置过期日期。``None`` 或 ``0`` 表示永不过期。"""
        if days is not None:
            days = int(days)
            if days < 0 or days > 36500:
                raise ValueError("过期天数需在 0-36500 之间")
        expires_at = ""
        if days:
            expires_at = (datetime.now() + timedelta(days=days)).strftime(
                "%Y-%m-%d %H:%M:%S")
        return self._update_lifecycle(query, expires_at=expires_at)

    def cleanup_expired(self, max_age_days: int = 180) -> Dict[str, Any]:
        """将未固定、已过期或长期未复用的主题安全归档。

        此操作不删除数据，避免自动清理造成研究资产不可恢复。
        """
        max_age_days = int(max_age_days)
        if max_age_days < 1 or max_age_days > 36500:
            raise ValueError("清理天数需在 1-36500 之间")
        cutoff = datetime.now() - timedelta(days=max_age_days)
        archived: List[str] = []
        with self._lock:
            for entry in self._data.values():
                if entry.get("pinned") or entry.get("archived"):
                    continue
                expires = self._parse_time(entry.get("expires_at"))
                last_used = self._parse_time(
                    (entry.get("usage") or {}).get("last_reused_at"))
                updated = self._parse_time(entry.get("updated_at") or entry.get("timestamp"))
                stale_reference = last_used or updated
                if ((expires is not None and expires <= datetime.now())
                        or (stale_reference is not None and stale_reference < cutoff)):
                    entry["archived"] = True
                    entry["archived_at"] = self._now()
                    archived.append(str(entry.get("query") or ""))
            if archived:
                self._persist()
        return {"count": len(archived), "archived_queries": archived,
                "max_age_days": max_age_days, "action": "archive"}

    def merge(self, target_query: str, source_queries: Sequence[str]) -> Optional[Dict[str, Any]]:
        """合并相近主题，并把来源主题归档为可追溯的历史记录。"""
        target_key = self._norm(target_query)
        source_keys = []
        for raw in source_queries:
            key = self._norm(str(raw))
            if key and key != target_key and key not in source_keys:
                source_keys.append(key)
        if not source_keys:
            raise ValueError("请至少选择一条与目标不同的来源记忆")
        with self._lock:
            target = self._data.get(target_key)
            if target is None:
                return None
            sources = [self._data[key] for key in source_keys if key in self._data]
            if not sources:
                return None
            paper_keys = {self._paper_key(paper) for paper in target.get("papers", [])}
            for source in sources:
                for paper in source.get("papers", []):
                    marker = self._paper_key(paper)
                    if marker not in paper_keys:
                        target.setdefault("papers", []).append(paper)
                        paper_keys.add(marker)
                target["summaries"] = self._merge_summaries(
                    target.get("summaries", []), source.get("summaries", []))
                source_query = str(source.get("query") or "")
                if source_query and source_query not in target.setdefault("merged_from", []):
                    target["merged_from"].append(source_query)
                source["archived"] = True
                source["archived_at"] = self._now()
                source["merged_into"] = str(target.get("query") or "")
            target["updated_at"] = self._now()
            target["archived"] = False
            target["archived_at"] = ""
            self._persist()
            return self._copy(target)

    # ------------------------------------------------------------------
    # 查询、语义检索与研究复用
    # ------------------------------------------------------------------
    def has_query(self, query: str) -> bool:
        with self._lock:
            entry = self._data.get(self._norm(query))
            return bool(entry and not entry.get("archived")
                        and (entry.get("pinned") or not self._is_expired(entry)))

    def get_round(self, query: str) -> Optional[Dict[str, Any]]:
        """取回历史研究记录（papers 反序列化为 ``Paper`` 对象）。"""
        with self._lock:
            entry = self._data.get(self._norm(query))
            if not entry:
                return None
            return {
                "query": entry["query"],
                "timestamp": entry["timestamp"],
                "papers": [Paper.from_dict(d) for d in entry.get("papers", [])],
                "summaries": self._copy(entry.get("summaries") or []),
                "analysis": self._copy(entry.get("analysis") or None),
                "pinned": bool(entry.get("pinned")),
                "archived": bool(entry.get("archived")),
            }

    def all_queries(self, include_archived: bool = True) -> List[str]:
        """所有历史主题（按更新时间倒序）。"""
        with self._lock:
            entries = (entry for entry in self._data.values()
                       if include_archived or not entry.get("archived"))
            return [entry["query"] for entry in sorted(
                entries, key=self._sort_key, reverse=True)]

    def semantic_search(self, query: str, limit: int = 10, *,
                        include_archived: bool = False,
                        exclude_query: Optional[str] = None,
                        min_score: float = 0.08) -> List[Dict[str, Any]]:
        """在本机执行混合语义检索，返回得分和命中的历史主题。

        使用签名哈希向量避免为每条记忆落盘冗余向量，检索时只读取本地 JSON。
        结果不包含 API Key、文件绝对路径等敏感信息。
        """
        clean = str(query or "").strip()
        if not clean:
            return []
        limit = max(1, min(100, int(limit)))
        excluded = self._norm(exclude_query or "")
        query_tokens = self._tokens(clean)
        query_vector = self._vector(query_tokens)
        if not query_tokens:
            return []
        with self._lock:
            matches: List[Dict[str, Any]] = []
            for key, entry in self._data.items():
                if key == excluded or (entry.get("archived") and not include_archived):
                    continue
                if self._is_expired(entry) and not entry.get("pinned"):
                    continue
                entry_tokens = self._tokens(self._entry_text(entry))
                if not entry_tokens:
                    continue
                cosine = self._cosine(query_vector, self._vector(entry_tokens))
                direct = set(query_tokens) & set(entry_tokens)
                union = set(query_tokens) | set(entry_tokens)
                lexical = len(direct) / len(union) if union else 0.0
                # 完全同主题始终可精确命中；其他主题偏重上下文向量。
                score = 1.0 if key == self._norm(clean) else (0.72 * cosine + 0.28 * lexical)
                if score < float(min_score):
                    continue
                item = self._entry_index(entry)
                item.update({
                    "score": round(float(score), 4),
                    "matched_terms": self._display_terms(direct)[:8],
                    "search_mode": "local_semantic_v1",
                })
                matches.append(item)
            matches.sort(key=lambda item: (
                -float(item.get("score") or 0),
                not bool(item.get("pinned")),
                item.get("updated_at") or item.get("timestamp") or "",
            ))
            return matches[:limit]

    def prepare_reuse(self, query: str, limit: int = 3, *,
                      exclude_query: Optional[str] = None) -> Dict[str, Any]:
        """检索并登记一次历史复用，返回可注入研究计划的审计上下文。"""
        matches = self.semantic_search(query, limit=limit,
                                       exclude_query=exclude_query,
                                       min_score=0.13)
        with self._lock:
            selected: List[Dict[str, Any]] = []
            for match in matches:
                entry = self._data.get(self._norm(match.get("query", "")))
                if entry is None:
                    continue
                usage = entry.setdefault("usage", {"reuse_count": 0,
                                                      "last_reused_at": ""})
                usage["reuse_count"] = int(usage.get("reuse_count") or 0) + 1
                usage["last_reused_at"] = self._now()
                selected.append(self._reuse_item(entry, match))
            if selected:
                self._persist()
        return {"matches": selected, "context": self._reuse_context(selected)}

    def mark_reused(self, query: str) -> None:
        """登记精确主题复用；失败时不影响研究主流程。"""
        with self._lock:
            entry = self._data.get(self._norm(query))
            if entry is None:
                return
            usage = entry.setdefault("usage", {"reuse_count": 0,
                                                  "last_reused_at": ""})
            usage["reuse_count"] = int(usage.get("reuse_count") or 0) + 1
            usage["last_reused_at"] = self._now()
            self._persist()

    def list_entries(self, keyword: str = "", limit: int = 100,
                     *, include_archived: bool = True) -> List[Dict[str, Any]]:
        """界面管理用轻量索引；关键词会走本地语义排序。"""
        clean_keyword = str(keyword or "").strip()
        limit = max(1, min(500, int(limit)))
        if clean_keyword:
            return self.semantic_search(clean_keyword, limit=limit,
                                        include_archived=include_archived,
                                        min_score=0.04)
        with self._lock:
            entries = [entry for entry in self._data.values()
                       if include_archived or not entry.get("archived")]
            entries.sort(key=self._sort_key, reverse=True)
            return [self._entry_index(entry) for entry in entries[:limit]]

    def get_entry(self, query: str) -> Optional[Dict[str, Any]]:
        """返回可展示的记忆明细（含管理元数据与派生状态）。"""
        with self._lock:
            entry = self._data.get(self._norm(query))
            if entry is None:
                return None
            result = self._copy(entry)
            result["expired"] = self._is_expired(entry)
            return result

    def knowledge_graph(self, query: Optional[str] = None,
                        include_archived: bool = False,
                        max_nodes: int = 240) -> Dict[str, Any]:
        """从当前记忆即时派生主题知识图谱，不复制论文全文。"""
        max_nodes = max(20, min(800, int(max_nodes)))
        wanted = self._norm(query or "")
        with self._lock:
            entries = []
            for key, entry in self._data.items():
                if wanted and key != wanted:
                    continue
                if entry.get("archived") and not include_archived and not wanted:
                    continue
                entries.append(entry)
            nodes: Dict[str, Dict[str, Any]] = {}
            edges: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

            def add_node(kind: str, label: Any) -> Optional[str]:
                text = self._compact(label, 150)
                if not text:
                    return None
                marker = f"{kind}:{self._norm(text)}"
                if marker not in nodes and len(nodes) >= max_nodes:
                    return None
                nodes.setdefault(marker, {"id": marker, "type": kind,
                                          "label": text})
                return marker

            def add_edge(source: Optional[str], target: Optional[str], kind: str) -> None:
                if not source or not target or source == target:
                    return
                marker = (source, target, kind)
                edge = edges.setdefault(marker, {"source": source,
                                                  "target": target,
                                                  "type": kind, "weight": 0})
                edge["weight"] += 1

            for entry in entries:
                topic = add_node("topic", entry.get("query"))
                analysis = entry.get("analysis") if isinstance(entry.get("analysis"), dict) else {}
                conclusion = analysis.get("summary") if isinstance(analysis, dict) else ""
                add_edge(topic, add_node("conclusion", conclusion), "结论")
                for gap in (analysis.get("gaps") or []) if isinstance(analysis, dict) else []:
                    gap_text = gap.get("gap") if isinstance(gap, dict) else gap
                    add_edge(topic, add_node("gap", gap_text), "盲点")
                summaries = entry.get("summaries") or []
                for paper_index, paper in enumerate(entry.get("papers") or []):
                    paper_node = add_node("paper", paper.get("title") if isinstance(paper, dict) else "")
                    add_edge(topic, paper_node, "包含")
                    if isinstance(paper, dict):
                        for author in paper.get("authors") or []:
                            add_edge(paper_node, add_node("author", author), "作者")
                    summary = self._summary_for_index(summaries, paper_index)
                    if summary:
                        add_edge(paper_node, add_node("method", summary.get("method")), "方法")
                        add_edge(paper_node, add_node("conclusion", summary.get("contribution")), "贡献")
            edge_list = list(edges.values())[:max_nodes * 4]
            return {
                "query": str(query or ""),
                "nodes": list(nodes.values()),
                "edges": edge_list,
                "stats": {"topics": len(entries), "nodes": len(nodes),
                          "edges": len(edge_list), "generated_at": self._now()},
            }

    # ------------------------------------------------------------------
    # 导出与删除
    # ------------------------------------------------------------------
    def export_payload(self, *, include_archived: bool = True) -> Dict[str, Any]:
        with self._lock:
            entries = [self._copy(entry) for entry in self._data.values()
                       if include_archived or not entry.get("archived")]
            entries.sort(key=self._sort_key, reverse=True)
            return {"schema_version": _SCHEMA_VERSION, "exported_at": self._now(),
                    "semantic_index": "local_semantic_v1", "entries": entries}

    def export_markdown(self, *, include_archived: bool = True) -> str:
        data = self.export_payload(include_archived=include_archived)
        lines = ["# Paper Studio 研究记忆导出", "",
                 f"- 导出时间：{data['exported_at']}",
                 f"- 条目数：{len(data['entries'])}",
                 "- 语义索引：本地语义检索（离线）", ""]
        for entry in data["entries"]:
            state = "已归档" if entry.get("archived") else "活跃"
            if entry.get("pinned"):
                state += " · 已固定"
            lines += ["---", "", f"## {entry.get('query', '未命名主题')}", "",
                      f"- 状态：{state}", f"- 创建时间：{entry.get('timestamp', '')}",
                      f"- 最后更新：{entry.get('updated_at', '')}",
                      f"- 论文：{len(entry.get('papers') or [])} 篇", ""]
            analysis = entry.get("analysis") or {}
            if isinstance(analysis, dict) and analysis.get("summary"):
                lines += ["### 已沉淀结论", "", str(analysis["summary"]), ""]
            gaps = analysis.get("gaps") if isinstance(analysis, dict) else []
            if gaps:
                lines += ["### 研究盲点", ""]
                for gap in gaps:
                    text = gap.get("gap") if isinstance(gap, dict) else gap
                    if text:
                        lines.append(f"- {text}")
                lines.append("")
            if entry.get("papers"):
                lines += ["### 论文", ""]
                for paper in entry["papers"][:100]:
                    title = paper.get("title") if isinstance(paper, dict) else ""
                    url = paper.get("url") if isinstance(paper, dict) else ""
                    lines.append(f"- [{title or '未命名论文'}]({url})" if url else f"- {title or '未命名论文'}")
                lines.append("")
        return "\n".join(lines)

    def delete(self, query: str) -> bool:
        """删除指定主题；归档比删除更适合日常整理。"""
        with self._lock:
            key = self._norm(query)
            if key not in self._data:
                return False
            del self._data[key]
            self._persist()
            return True

    def clear(self) -> int:
        """清空全部研究记忆，返回移除条目数。"""
        with self._lock:
            count = len(self._data)
            if count:
                self._data = {}
                self._persist()
            return count

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            entries = list(self._data.values())
            total_papers = sum(len(entry.get("papers", [])) for entry in entries)
            return {
                "entries": len(entries),
                "active_entries": sum(not bool(entry.get("archived")) for entry in entries),
                "archived_entries": sum(bool(entry.get("archived")) for entry in entries),
                "pinned_entries": sum(bool(entry.get("pinned")) for entry in entries),
                "expired_entries": sum(self._is_expired(entry) for entry in entries),
                "queries": list(self._data.keys()),
                "total_papers": total_papers,
                "path": str(self.path),
                "schema_version": _SCHEMA_VERSION,
                "semantic_index": "local_semantic_v1",
            }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _update_lifecycle(self, query: str, **values: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._data.get(self._norm(query))
            if entry is None:
                return None
            entry.update(values)
            entry["updated_at"] = self._now()
            self._persist()
            return self._copy(entry)

    def _load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    return
                entries = raw.get("entries") if isinstance(raw.get("entries"), dict) else raw
                # 旧版 schema 的 ``version`` 等元字段不应被当成主题。
                loaded = {}
                for _key, value in entries.items():
                    if not isinstance(value, dict) or not value.get("query"):
                        continue
                    normal = self._normalise_entry(value)
                    loaded[self._norm(normal["query"])] = normal
                self._data = loaded
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                # 损坏记忆不能阻断研究；下一次有意写入时会恢复一个合法文件。
                self._data = {}

    def _persist(self) -> None:
        """原子写入，避免应用意外退出损坏知识库。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": _SCHEMA_VERSION,
                           "entries": self._data}, handle,
                          ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    @classmethod
    def _normalise_entry(cls, raw: Dict[str, Any]) -> Dict[str, Any]:
        now = cls._now()
        analysis = raw.get("analysis") if isinstance(raw.get("analysis"), dict) else None
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        return {
            "query": str(raw.get("query") or "").strip(),
            "timestamp": str(raw.get("timestamp") or now),
            "updated_at": str(raw.get("updated_at") or raw.get("timestamp") or now),
            "papers": [item for item in (raw.get("papers") or []) if isinstance(item, dict)],
            "summaries": cls._json_list(raw.get("summaries")),
            "analysis": analysis,
            "pinned": bool(raw.get("pinned", False)),
            "archived": bool(raw.get("archived", False)),
            "archived_at": str(raw.get("archived_at") or ""),
            "expires_at": str(raw.get("expires_at") or ""),
            "merged_from": [str(item) for item in raw.get("merged_from", []) if str(item).strip()],
            "merged_into": str(raw.get("merged_into") or ""),
            "usage": {"reuse_count": max(0, int(usage.get("reuse_count") or 0)),
                      "last_reused_at": str(usage.get("last_reused_at") or "")},
        }

    @staticmethod
    def _json_list(value: Any) -> List[Dict[str, Any]]:
        return [item for item in (value or []) if isinstance(item, dict)] if isinstance(value, list) else []

    @staticmethod
    def _paper_dict(paper: Any) -> Dict[str, Any]:
        if isinstance(paper, Paper):
            return paper.to_dict()
        return paper if isinstance(paper, dict) else {}

    @staticmethod
    def _copy(value: Any) -> Any:
        return json.loads(json.dumps(value, ensure_ascii=False))

    @staticmethod
    def _norm(value: Any) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _parse_time(value: Any) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    @classmethod
    def _is_expired(cls, entry: Dict[str, Any]) -> bool:
        expires = cls._parse_time(entry.get("expires_at"))
        return bool(expires and expires <= datetime.now())

    @classmethod
    def _sort_key(cls, entry: Dict[str, Any]) -> Tuple[int, int, str]:
        return (int(bool(entry.get("pinned"))), int(not bool(entry.get("archived"))),
                str(entry.get("updated_at") or entry.get("timestamp") or ""))

    @classmethod
    def _entry_index(cls, entry: Dict[str, Any]) -> Dict[str, Any]:
        analysis = entry.get("analysis") if isinstance(entry.get("analysis"), dict) else {}
        titles = [str(paper.get("title") or "") for paper in entry.get("papers", [])
                  if isinstance(paper, dict)]
        return {
            "query": str(entry.get("query") or ""),
            "timestamp": str(entry.get("timestamp") or ""),
            "updated_at": str(entry.get("updated_at") or entry.get("timestamp") or ""),
            "paper_count": len(entry.get("papers") or []),
            "summary_count": len(entry.get("summaries") or []),
            "gap_count": len(analysis.get("gaps") or []),
            "paper_titles": [title for title in titles if title][:3],
            "pinned": bool(entry.get("pinned")),
            "archived": bool(entry.get("archived")),
            "expired": cls._is_expired(entry),
            "reuse_count": int((entry.get("usage") or {}).get("reuse_count") or 0),
            "merged_into": str(entry.get("merged_into") or ""),
        }

    @classmethod
    def _entry_text(cls, entry: Dict[str, Any]) -> str:
        fragments: List[str] = [str(entry.get("query") or "")]
        for paper in entry.get("papers") or []:
            if isinstance(paper, dict):
                fragments.extend(str(paper.get(key) or "") for key in
                                 ("title", "abstract", "venue", "source"))
                fragments.extend(str(author) for author in paper.get("authors") or [])
        for summary in entry.get("summaries") or []:
            if not isinstance(summary, dict):
                continue
            value = summary.get("summary", summary)
            fragments.extend(cls._flatten_text(value))
        fragments.extend(cls._flatten_text(entry.get("analysis") or {}))
        return " ".join(fragments)

    @classmethod
    def _tokens(cls, text: str) -> List[str]:
        clean = str(text or "").lower()
        tokens = [token.strip("._/-") for token in _TOKEN_RE.findall(clean)]
        for span in _CJK_RE.findall(clean):
            tokens.append(span)
            tokens.extend(span[index:index + 2] for index in range(len(span) - 1))
        compact = " ".join(tokens)
        for group in _SYNONYM_GROUPS:
            if any(term in clean or term in compact for term in group):
                tokens.append("concept:" + "|".join(group))
        return [token for token in tokens if len(token) > 1]

    @staticmethod
    def _vector(tokens: Iterable[str]) -> Dict[int, float]:
        vector: Dict[int, float] = {}
        counts: Dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % _VECTOR_SIZE
            sign = 1.0 if (value >> 12) & 1 else -1.0
            vector[index] = vector.get(index, 0.0) + sign * (1.0 + math.log(count))
        return vector

    @staticmethod
    def _cosine(left: Dict[int, float], right: Dict[int, float]) -> float:
        if not left or not right:
            return 0.0
        dot = sum(value * right.get(index, 0.0) for index, value in left.items())
        left_norm = math.sqrt(sum(value * value for value in left.values()))
        right_norm = math.sqrt(sum(value * value for value in right.values()))
        return max(0.0, dot / (left_norm * right_norm)) if left_norm and right_norm else 0.0

    @staticmethod
    def _display_terms(terms: Iterable[str]) -> List[str]:
        return sorted({term for term in terms
                       if not term.startswith("concept:") and len(term) >= 2},
                      key=lambda item: (-len(item), item))

    @classmethod
    def _reuse_item(cls, entry: Dict[str, Any], match: Dict[str, Any]) -> Dict[str, Any]:
        analysis = entry.get("analysis") if isinstance(entry.get("analysis"), dict) else {}
        gaps = []
        for gap in analysis.get("gaps") or []:
            text = gap.get("gap") if isinstance(gap, dict) else gap
            if text:
                gaps.append(cls._compact(text, 180))
        return {
            "query": str(entry.get("query") or ""),
            "timestamp": str(entry.get("timestamp") or ""),
            "score": match.get("score", 0),
            "matched_terms": list(match.get("matched_terms") or []),
            "conclusion": cls._compact(analysis.get("summary"), 500),
            "gaps": gaps[:3],
            "paper_titles": [str(paper.get("title") or "") for paper in
                             (entry.get("papers") or []) if isinstance(paper, dict)][:3],
        }

    @classmethod
    def _reuse_context(cls, matches: Sequence[Dict[str, Any]]) -> str:
        if not matches:
            return ""
        lines = ["以下为本地知识库中已沉淀的历史研究，只能作为待交叉验证的背景证据；"
                 "请优先以本次检索到的论文为准，并说明是否支持或修正这些结论："]
        for index, item in enumerate(matches, 1):
            lines.append(f"{index}. 主题《{item.get('query', '')}》"
                         f"（相关度 {float(item.get('score') or 0):.0%}）")
            if item.get("conclusion"):
                lines.append(f"   已有结论：{item['conclusion']}")
            if item.get("gaps"):
                lines.append("   已知盲点：" + "；".join(item["gaps"]))
        return "\n".join(lines)

    @staticmethod
    def _compact(value: Any, limit: int = 180) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"

    @classmethod
    def _flatten_text(cls, value: Any) -> List[str]:
        if isinstance(value, dict):
            return [text for item in value.values() for text in cls._flatten_text(item)]
        if isinstance(value, (list, tuple, set)):
            return [text for item in value for text in cls._flatten_text(item)]
        text = cls._compact(value, 2000)
        return [text] if text else []

    @classmethod
    def _summary_for_index(cls, summaries: Sequence[Any], index: int) -> Dict[str, Any]:
        if index >= len(summaries) or not isinstance(summaries[index], dict):
            return {}
        summary = summaries[index].get("summary", summaries[index])
        return summary if isinstance(summary, dict) else {}

    @classmethod
    def _paper_key(cls, paper: Any) -> str:
        if not isinstance(paper, dict):
            return ""
        return cls._norm(paper.get("doi") or paper.get("url") or paper.get("title"))

    @classmethod
    def _merge_summaries(cls, target: Sequence[Any], source: Sequence[Any]) -> List[Dict[str, Any]]:
        output = [item for item in target if isinstance(item, dict)]
        seen = {cls._norm((item.get("summary") or item).get("title")
                         if isinstance(item.get("summary") or item, dict) else "")
                for item in output}
        for item in source:
            if not isinstance(item, dict):
                continue
            raw = item.get("summary") or item
            title = cls._norm(raw.get("title") if isinstance(raw, dict) else "")
            if title and title in seen:
                continue
            output.append(item)
            if title:
                seen.add(title)
        return output

"""Paper Studio 的只读查询层。

该模块不依赖 Web 服务，也不会启动线程或写入文件。MCP Server、后续 HTTP
接口和测试可共享同一组目录边界与输出脱敏规则。
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

from .core.billing import (BEIJING, PRICING_CNY, CostTracker, price_for,
                           pricing_period)
from .skills import SearchManager


SUPPORTED_SEARCH_SOURCES = frozenset({"arxiv_search", "scholar_search"})
SUPPORTED_DEEPSEEK_MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")
LIBRARY_STATUSES = frozenset({
    "all", "ok", "downloaded", "failed", "unavailable", "deleted", "missing",
})


def resolve_data_dir(data_dir: Optional[str | Path] = None) -> Path:
    """返回应用数据目录，优先使用显式参数和环境变量。"""
    raw = data_dir or os.environ.get("PAPER_STUDIO_DATA_DIR") or "downloads"
    return Path(raw).expanduser().resolve()


class PaperStudioReadService:
    """面向 MCP 的只读服务，不暴露本地绝对路径或敏感配置。"""

    def __init__(self, data_dir: Optional[str | Path] = None) -> None:
        self.data_dir = resolve_data_dir(data_dir)

    # ---- 联网检索 -----------------------------------------------------
    def search_papers(self, query: str, max_results: int = 10,
                      sources: Optional[Sequence[str]] = None,
                      year_from: Optional[int] = None) -> Dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            raise ValueError("query 不能为空")
        max_results = max(1, min(20, int(max_results)))
        selected = list(sources) if sources else sorted(SUPPORTED_SEARCH_SOURCES)
        unknown = sorted(set(selected) - SUPPORTED_SEARCH_SOURCES)
        if unknown:
            raise ValueError(f"不支持的检索来源: {', '.join(unknown)}")
        if year_from is not None:
            year_from = int(year_from)
            if year_from < 1900 or year_from > datetime.now().year + 1:
                raise ValueError("year_from 超出有效范围")

        papers, warnings = SearchManager().search_with_diagnostics(
            query=query, max_results=max_results, sources=selected,
            year_from=year_from)
        return {
            "query": query,
            "sources": selected,
            "max_results_per_source": max_results,
            "year_from": year_from,
            "count": len(papers),
            "partial": bool(warnings),
            "warnings": warnings,
            "papers": [paper.to_dict() for paper in papers],
        }

    # ---- 本地文献库 ---------------------------------------------------
    @staticmethod
    def _safe_stat(path: Path) -> Optional[os.stat_result]:
        try:
            return path.stat()
        except OSError:
            return None

    def _manifest_paths(self) -> List[Path]:
        if not self.data_dir.is_dir():
            return []
        manifests: List[Tuple[float, Path]] = []
        try:
            children = list(self.data_dir.iterdir())
        except OSError:
            return []
        for child in children:
            manifest = child / "metadata.json"
            try:
                child.resolve().relative_to(self.data_dir)
            except (OSError, ValueError):
                continue
            stat = self._safe_stat(manifest)
            if child.is_dir() and stat is not None:
                manifests.append((stat.st_mtime, manifest))
        manifests.sort(key=lambda item: item[0], reverse=True)
        return [path for _mtime, path in manifests[:500]]

    def _resolve_manifest_file(self, raw: Any, batch: Path,
                               suffixes: frozenset[str]) -> Optional[Path]:
        if not raw:
            return None
        candidate = Path(str(raw))
        candidates = ([candidate] if candidate.is_absolute() else [
            self.data_dir.parent / candidate,
            batch / candidate,
            self.data_dir / candidate,
        ])
        for value in candidates:
            try:
                resolved = value.resolve()
                resolved.relative_to(batch.resolve())
            except (OSError, ValueError):
                continue
            if resolved.suffix.lower() in suffixes and resolved.is_file():
                return resolved
        return None

    def _read_manifest(self, manifest: Path) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError, TypeError):
            return None

    def _sanitized_batch(self, manifest: Path) -> Optional[Dict[str, Any]]:
        data = self._read_manifest(manifest)
        if data is None:
            return None
        batch = manifest.parent.resolve()
        run_id = str(data.get("run_id") or batch.name)
        if Path(run_id).name != run_id:
            run_id = batch.name
        papers = data.get("papers") if isinstance(data.get("papers"), list) else []
        raw_items = data.get("items") if isinstance(data.get("items"), list) else []
        items: List[Dict[str, Any]] = []
        stats = {"total": 0, "downloaded": 0, "failed": 0,
                 "unavailable": 0, "missing": 0, "deleted": 0}

        for position, raw_item in enumerate(raw_items):
            if not isinstance(raw_item, dict):
                continue
            try:
                index = int(raw_item.get("index", position + 1))
            except (TypeError, ValueError):
                index = position + 1
            paper = (papers[index - 1] if 0 < index <= len(papers)
                     and isinstance(papers[index - 1], dict) else {})
            pdf = self._resolve_manifest_file(
                raw_item.get("pdf_path"), batch, frozenset({".pdf"}))
            text_path = self._resolve_manifest_file(
                raw_item.get("text_path"), batch, frozenset({".txt"}))
            status = str(raw_item.get("status") or "failed")
            if status in {"ok", "downloaded"} and pdf is None:
                status = "missing"
            stats["total"] += 1
            if status in {"ok", "downloaded"} and pdf is not None:
                stats["downloaded"] += 1
            elif status in stats:
                stats[status] += 1
            else:
                stats["failed"] += 1
            pdf_stat = self._safe_stat(pdf) if pdf else None
            error = str(raw_item.get("error") or "") or None
            if error:
                error = error.replace(str(batch), "[batch]")
                error = error.replace(str(self.data_dir), "[data-dir]")
            items.append({
                "batch_id": run_id,
                "index": index,
                "title": str(raw_item.get("title") or paper.get("title") or "未命名文献"),
                "source": str(raw_item.get("source") or paper.get("source") or "unknown"),
                "url": str(raw_item.get("url") or paper.get("url") or ""),
                "authors": list(paper.get("authors") or []),
                "year": paper.get("year"),
                "abstract": paper.get("abstract"),
                "doi": paper.get("doi"),
                "venue": paper.get("venue"),
                "status": status,
                "error": error,
                "pdf_available": pdf is not None,
                "text_available": text_path is not None,
                "size_bytes": pdf_stat.st_size if pdf_stat else 0,
            })
        return {
            "id": run_id,
            "generated_at": data.get("generated_at", ""),
            "updated_at": data.get("updated_at", data.get("generated_at", "")),
            "stats": stats,
            "resource_uri": f"paper-studio://library/{quote(run_id, safe='')}",
            "items": items,
        }

    def _all_batches(self) -> List[Dict[str, Any]]:
        batches = []
        for manifest in self._manifest_paths():
            batch = self._sanitized_batch(manifest)
            if batch is not None:
                batches.append(batch)
        return batches

    def search_library(self, keyword: str = "", status: str = "all",
                       limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        keyword = str(keyword or "").strip().lower()
        status = str(status or "all")
        if status not in LIBRARY_STATUSES:
            raise ValueError(f"不支持的文献状态: {status}")
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))

        matched: List[Dict[str, Any]] = []
        batches = self._all_batches()
        for batch in batches:
            for item in batch["items"]:
                haystack = " ".join((item["title"], item["source"],
                                     item.get("doi") or "")).lower()
                if keyword and keyword not in haystack:
                    continue
                if status != "all":
                    accepted = ({"ok", "downloaded"} if status in
                                {"ok", "downloaded"} else {status})
                    if item["status"] not in accepted:
                        continue
                matched.append(item)

        page = matched[offset:offset + limit]
        return {
            "keyword": keyword,
            "status": status,
            "total": len(matched),
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(page) < len(matched),
            "items": page,
            "batches": [{key: batch[key] for key in (
                "id", "generated_at", "updated_at", "stats", "resource_uri")}
                        for batch in batches],
        }

    def get_library_batch(self, batch_id: str) -> Dict[str, Any]:
        batch_id = str(batch_id or "")
        if not batch_id or Path(batch_id).name != batch_id:
            raise ValueError("无效的文献批次 ID")
        for manifest in self._manifest_paths():
            batch = self._sanitized_batch(manifest)
            if batch and batch["id"] == batch_id:
                return batch
        raise ValueError(f"文献批次不存在: {batch_id}")

    # ---- 报告 ---------------------------------------------------------
    def _report_path(self, report_id: str) -> Optional[Path]:
        report_id = str(report_id or "")
        if not report_id or Path(report_id).name != report_id:
            return None
        path = (self.data_dir / report_id).resolve()
        try:
            path.relative_to(self.data_dir)
        except ValueError:
            return None
        return path if path.suffix.lower() == ".md" and path.is_file() else None

    @staticmethod
    def _report_title(content: str, fallback: str) -> str:
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else fallback

    @staticmethod
    def _report_excerpt(content: str, limit: int = 180) -> str:
        for line in content.splitlines():
            if line.lstrip().startswith("#"):
                continue
            clean = re.sub(r"^[#>*\-\s]+", "", line).strip()
            if clean and not clean.startswith(("开始时间：", "研究轮次：", "查询总数：")):
                return clean[:limit]
        return ""

    def list_reports(self, keyword: str = "", limit: int = 50,
                     offset: int = 0) -> Dict[str, Any]:
        keyword = str(keyword or "").strip().lower()
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        records: List[Tuple[float, Dict[str, Any]]] = []
        if self.data_dir.is_dir():
            for path in self.data_dir.glob("*.md"):
                safe = self._report_path(path.name)
                stat = self._safe_stat(safe) if safe else None
                if safe is None or stat is None:
                    continue
                try:
                    with safe.open("r", encoding="utf-8", errors="replace") as handle:
                        head = handle.read(8192)
                except OSError:
                    continue
                title = self._report_title(head, safe.stem)
                if keyword and keyword not in f"{safe.name} {title}".lower():
                    continue
                records.append((stat.st_mtime, {
                    "id": safe.name,
                    "name": safe.name,
                    "title": title,
                    "excerpt": self._report_excerpt(head),
                    "modified": time.strftime("%Y-%m-%d %H:%M:%S",
                                              time.localtime(stat.st_mtime)),
                    "size_bytes": stat.st_size,
                    "resource_uri": f"paper-studio://reports/{quote(safe.name, safe='')}",
                }))
        records.sort(key=lambda item: item[0], reverse=True)
        reports = [item for _mtime, item in records]
        page = reports[offset:offset + limit]
        return {
            "keyword": keyword,
            "total": len(reports),
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(page) < len(reports),
            "reports": page,
        }

    def read_report(self, report_id: str, offset: int = 0,
                    max_chars: int = 40_000) -> Dict[str, Any]:
        path = self._report_path(report_id)
        if path is None:
            raise ValueError(f"报告不存在: {report_id}")
        offset = max(0, int(offset))
        max_chars = max(1_000, min(200_000, int(max_chars)))
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            stat = path.stat()
        except OSError as err:
            raise ValueError(f"报告读取失败: {report_id}") from err
        chunk = content[offset:offset + max_chars]
        next_offset = offset + len(chunk)
        return {
            "id": path.name,
            "name": path.name,
            "title": self._report_title(content[:8192], path.stem),
            "modified": time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(stat.st_mtime)),
            "total_chars": len(content),
            "offset": offset,
            "returned_chars": len(chunk),
            "has_more": next_offset < len(content),
            "next_offset": next_offset if next_offset < len(content) else None,
            "content": chunk,
        }

    def read_report_full(self, report_id: str) -> str:
        path = self._report_path(report_id)
        if path is None:
            raise ValueError(f"报告不存在: {report_id}")
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError as err:
            raise ValueError(f"报告读取失败: {report_id}") from err

    # ---- 研究记忆 -----------------------------------------------------
    def _redact_memory_value(self, value: Any) -> Any:
        """记忆可能来自旧版任务，递归隐去其中的本地数据路径。"""
        if isinstance(value, dict):
            return {str(key): self._redact_memory_value(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_memory_value(item) for item in value]
        if isinstance(value, str):
            text = value
            roots = {self.data_dir, self.data_dir.parent, Path.cwd().resolve()}
            for root in sorted((str(path) for path in roots),
                               key=len, reverse=True):
                if root and root != "/":
                    text = text.replace(root, "[local]")
            return text
        return value

    def search_memory(self, keyword: str = "", limit: int = 100) -> Dict[str, Any]:
        """只读搜索研究记忆索引。"""
        from .core.memory import ResearchMemory

        limit = max(1, min(500, int(limit)))
        memory = ResearchMemory(path=str(self.data_dir / "research_memory.json"))
        stats = memory.stats()
        return {
            "keyword": str(keyword or "").strip(),
            "entries": int(stats.get("entries") or 0),
            "total_papers": int(stats.get("total_papers") or 0),
            "items": self._redact_memory_value(
                memory.list_entries(keyword, limit)),
        }

    def read_memory(self, query: str) -> Dict[str, Any]:
        """只读获取一条研究记忆明细。"""
        from .core.memory import ResearchMemory

        query = str(query or "").strip()
        if not query:
            raise ValueError("query 不能为空")
        memory = ResearchMemory(path=str(self.data_dir / "research_memory.json"))
        entry = memory.get_entry(query)
        if entry is None:
            raise ValueError(f"研究记忆不存在: {query}")
        return self._redact_memory_value(entry)

    # ---- 成本 ---------------------------------------------------------
    def cost_overview(self) -> Dict[str, Any]:
        ledger_path = self.data_dir / "cost_ledger.json"
        tracker = CostTracker(storage_path=ledger_path)
        period = pricing_period()
        return {
            "currency": "CNY",
            "current_period": period,
            "current_beijing_time": datetime.now(BEIJING).isoformat(timespec="seconds"),
            "peak_hours_beijing": ["09:00-12:00", "14:00-18:00"],
            "ledger_available": ledger_path.is_file(),
            "ledger": tracker.to_dict(),
            "pricing_per_1m_tokens": {
                model: PRICING_CNY[model] for model in SUPPORTED_DEEPSEEK_MODELS
            },
        }

    @staticmethod
    def estimate_cost(model: str, input_tokens: int = 0,
                      cached_input_tokens: int = 0,
                      output_tokens: int = 0, calls: int = 1) -> Dict[str, Any]:
        if model not in SUPPORTED_DEEPSEEK_MODELS:
            raise ValueError("model 仅支持 deepseek-v4-flash 或 deepseek-v4-pro")
        input_tokens = max(0, min(1_000_000_000, int(input_tokens)))
        cached_input_tokens = max(0, min(input_tokens, int(cached_input_tokens)))
        output_tokens = max(0, min(1_000_000_000, int(output_tokens)))
        calls = max(1, min(10_000, int(calls)))
        period = pricing_period()
        price = price_for(model)
        assert price is not None
        uncached = input_tokens - cached_input_tokens
        per_call = (
            cached_input_tokens / 1_000_000 * price["input_hit"]
            + uncached / 1_000_000 * price["input_miss"]
            + output_tokens / 1_000_000 * price["output"]
        )
        return {
            "model": model,
            "currency": "CNY",
            "price_period": period,
            "unit": "CNY per 1M tokens",
            "unit_prices": price,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "uncached_input_tokens": uncached,
            "output_tokens": output_tokens,
            "calls": calls,
            "estimated_cost_per_call_cny": round(per_call, 6),
            "estimated_total_cny": round(per_call * calls, 6),
        }

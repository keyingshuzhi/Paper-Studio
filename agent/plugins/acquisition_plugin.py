"""插件二：数据获取流水线（Data Acquisition Pipeline）。

流程：Paper 列表 → 批量下载 PDF → 抽取并清洗文本 → 生成结构化资料包。

产出（写到磁盘）：
    downloads/<plugin_run_id>/
        metadata.json     全部论文的元数据
        papers/<序号>_<标题>.pdf   已下载的 PDF
        texts/<序号>_<标题>.txt    抽取出的纯文本
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..skills import DownloaderSkill
from ..skills.metadata import Paper
from .base import BasePlugin


class DataAcquisitionPipeline(BasePlugin):
    """论文下载 + 文本抽取流水线插件。"""

    name = "data_acquisition"
    description = "批量下载论文 PDF 并抽取纯文本，生成本地资料包。"

    def __init__(self, downloader: Optional[DownloaderSkill] = None,
                 root_dir: str = "downloads") -> None:
        self.downloader = downloader or DownloaderSkill()
        self.root_dir = Path(root_dir)

    def run(self, papers: List[Paper], max_downloads: Optional[int] = None,
            run_id: Optional[str] = None, delay_seconds: float = 1.5,
            checkpoint: Optional[Callable[[], None]] = None,
            **_: Any) -> Dict[str, Any]:
        """执行下载流水线。

        Args:
            papers: 待下载的 Paper 列表。
            max_downloads: 最多下载几篇（None 表示全部）。
            run_id: 本次运行标识（默认按时间戳生成）。

        Returns:
            资料包摘要：{run_id, base_dir, items: [...], stats: {...}}
        """
        run_id = self._unique_run_id(run_id or time.strftime("%Y%m%d_%H%M%S"))
        base = self.root_dir / run_id
        pdf_dir = base / "papers"
        text_dir = base / "texts"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        text_dir.mkdir(parents=True, exist_ok=True)

        targets = papers[:max_downloads] if max_downloads else papers
        items: List[Dict[str, Any]] = []
        downloaded = 0
        extracted = 0
        failed = 0
        unavailable = 0

        manifest = {
            "run_id": run_id,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "settings": {
                "max_downloads": max_downloads,
                "delay_seconds": max(0.0, float(delay_seconds)),
            },
            "papers": [p.to_dict() for p in targets],
            "items": items,
        }

        for i, paper in enumerate(targets, 1):
            if checkpoint is not None:
                checkpoint()
            item: Dict[str, Any] = {
                "index": i,
                "title": paper.title,
                "source": paper.source,
                "url": paper.url,
                "status": "failed",
                "error": None,
                "pdf_path": None,
                "text_path": None,
            }
            pdf_url = self._resolve_pdf_url(paper)
            if not pdf_url:
                item["status"] = "unavailable"
                item["error"] = "未发现可合法直接下载的公开 PDF，仅保留文献元数据"
                unavailable += 1
            else:
                item["pdf_url"] = pdf_url
                try:
                    fname = f"{i:02d}_{self._safe_name(paper.title)}.pdf"
                    local_pdf = self.downloader.download(
                        pdf_url, dest_dir=str(pdf_dir), filename=fname,
                        expected_pdf=True)
                    item["pdf_path"] = str(local_pdf)
                    item["status"] = "downloaded"
                    downloaded += 1

                    try:
                        _raw, clean = self.downloader.extract_and_clean(
                            str(local_pdf), max_pages=10)
                        text_path = text_dir / f"{i:02d}_{self._safe_name(paper.title)}.txt"
                        text_path.write_text(clean, encoding="utf-8")
                        item["text_path"] = str(text_path)
                        item["status"] = "ok"
                        extracted += 1
                    except Exception as err:  # noqa: BLE001
                        # PDF 已成功保存，解析失败不应被误报为下载失败。
                        item["error"] = f"PDF 已下载，但文本提取失败: {err}"
                except Exception as err:  # noqa: BLE001 - 单篇失败不阻塞整批
                    item["error"] = str(err)
                    failed += 1
            items.append(item)
            manifest["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._write_manifest(base, manifest)
            print(f"  [{i}/{len(targets)}] {item['status']}: {paper.title[:50]}")

            # 不对最后一篇等待；等待期间仍响应暂停或取消。
            if i < len(targets):
                self._interruptible_wait(delay_seconds, checkpoint)

        stats = {
            "total": len(targets), "ok": downloaded,
            "downloaded": downloaded, "extracted": extracted,
            "failed": failed, "unavailable": unavailable,
        }
        manifest["stats"] = stats
        manifest["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._write_manifest(base, manifest)
        print(f"资料包完成: {base} | 下载 {downloaded}/{len(targets)} "
              f"| 无公开 PDF {unavailable} | 失败 {failed}")
        return {"run_id": run_id, "base_dir": str(base),
                "items": items, "stats": stats}

    def _unique_run_id(self, preferred: str) -> str:
        """避免同一秒启动多个下载任务时覆盖资料包。"""
        candidate = preferred
        suffix = 2
        while (self.root_dir / candidate).exists():
            candidate = f"{preferred}_{suffix}"
            suffix += 1
        return candidate

    @staticmethod
    def _write_manifest(base: Path, manifest: Dict[str, Any]) -> None:
        """原子更新资料包清单，使下载中的批次也能安全展示。"""
        path = base / "metadata.json"
        tmp = base / "metadata.json.tmp"
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _interruptible_wait(seconds: float,
                            checkpoint: Optional[Callable[[], None]]) -> None:
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            step = min(0.25, remaining)
            time.sleep(step)
            remaining -= step
            if checkpoint is not None:
                checkpoint()

    @staticmethod
    def _resolve_pdf_url(paper: Paper) -> Optional[str]:
        """只使用可信的直接 PDF 地址，避免把 DOI/出版社 HTML 当 PDF。"""
        if paper.pdf_url:
            return paper.pdf_url
        url = (paper.url or "").strip()
        if not url:
            return None
        lower = url.lower().split("?", 1)[0]
        if lower.endswith(".pdf") or "/pdf/" in lower:
            return url
        if "arxiv.org/abs/" in lower:
            arxiv_id = url.split("/abs/", 1)[1].split("?", 1)[0]
            return f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        return None

    @staticmethod
    def _safe_name(title: str, max_len: int = 40) -> str:
        """标题转安全文件名。"""
        name = re.sub(r"[^\w\u4e00-\u9fff\- ]+", "", title)
        name = re.sub(r"\s+", "_", name).strip("_")
        return name[:max_len] or "paper"

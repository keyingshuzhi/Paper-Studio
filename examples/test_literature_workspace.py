"""Regression tests for the literature reader and report-output workspace."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.agent import ResearchAgent
from agent.core.planner import ResearchPlan
from agent.skills.metadata import Paper
from agent.webapp import ResearchWebApp


def expect(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"  [PASS] {label}")


class _Plan:
    def make_plan(self, query: str, **_kwargs: object) -> ResearchPlan:
        return ResearchPlan(query=query, original_query=query, max_results=10,
                            sources=[], download=False, report=False)


class _SearchMustNotRun:
    def run(self, **_kwargs: object) -> list[Paper]:
        raise AssertionError("existing_papers must bypass external search")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="paper-studio-workspace-") as tmp:
        root = Path(tmp)
        os.environ["PAPER_STUDIO_DATA_DIR"] = str(root)
        os.environ["PAPER_STUDIO_CONFIG_DIR"] = str(root / "config")
        batch = root / "batch-2026"
        pdf = batch / "pdfs" / "01.pdf"
        text = batch / "texts" / "01.txt"
        pdf.parent.mkdir(parents=True)
        text.parent.mkdir(parents=True)
        pdf.write_bytes(b"%PDF-1.4\n% local fixture\n")
        text.write_text("Introduction\nCodex harness improves repeatable agent evaluation.\nReferences\n[1] Example 2025\n", encoding="utf-8")
        paper = {
            "title": "Codex Harness for Repeatable Agent Evaluation",
            "url": "https://example.invalid/paper",
            "source": "arxiv_search", "authors": ["A Researcher"],
            "year": 2025, "abstract": "A reproducible evaluation harness.",
            "doi": "10.1000/example", "citation_count": 24,
        }
        (batch / "metadata.json").write_text(json.dumps({
            "run_id": "batch-2026", "generated_at": "2026-09-04 10:00:00",
            "query": "codex harness agent evaluation",
            "papers": [paper],
            "items": [{"index": 1, **paper, "status": "ok",
                       "pdf_path": str(pdf), "text_path": str(text)}],
        }, ensure_ascii=False), encoding="utf-8")

        app = ResearchWebApp(runner=lambda query, **opts: {
            "report_path": None, "query": query, "options": opts})
        library = app.list_library()
        item = library["batches"][0]["items"][0]
        expect("文献质量评分返回 0-100 分", 0 <= item["quality"]["score"] <= 100)
        expect("质量评分保留引用量", item["quality"]["citation_count"] == 24)
        document = app.get_library_document("batch-2026", 1)
        expect("阅读工作台返回抽取文本", document is not None and "Codex harness" in document["text_content"])
        annotation = app.save_library_annotation({
            "run_id": "batch-2026", "index": 1,
            "quote": "Codex harness improves repeatable agent evaluation.",
            "note": "可作为基准评测入口", "tags": ["评测", "重要"],
            "color": "blue", "page": 1,
        })
        expect("批注保存并返回标签", "评测" in annotation["tags"])
        document = app.get_library_document("batch-2026", 1)
        expect("批注持久化到阅读工作台", len(document["annotations"]) == 1)
        expect("基于已有文献可重建标准 Paper", len(app._library_selected_papers([
            {"run_id": "batch-2026", "index": 1}])) == 1)

        agent = ResearchAgent(planner=_Plan(), search_plugin=_SearchMustNotRun())
        seeded = agent.run("compare the evidence", existing_papers=[Paper.from_dict(paper)],
                           summarize=False, analyze=False, report=False)
        expect("已有文献续研不会调用外部检索", len(seeded["papers"]) == 1)

        report = root / "report_workspace.md"
        report.write_text("# Codex Harness Research Report\n\n## Findings\n\n- Strong local evidence\n\n## References\n\n[1] Example 2025\n", encoding="utf-8")
        first = app.create_report_version(str(report), "初始版本")
        expect("报告可创建版本快照", first is not None)
        versions = app.list_report_versions(str(report))
        expect("报告版本历史可读取", len(versions) == 1)
        markdown = app.export_report(str(report), "markdown")
        docx = app.export_report(str(report), "docx")
        pdf_export = app.export_report(str(report), "pdf")
        expect("Markdown 导出成功", bool(markdown and markdown.read_text(encoding="utf-8").startswith("#")))
        expect("Word 导出是有效 OOXML 包", bool(docx and zipfile.is_zipfile(docx)))
        expect("PDF 导出成功", bool(pdf_export and pdf_export.read_bytes().startswith(b"%PDF")))
        print("QA_EXPORT_DIR=" + str(root / "exports"))
        time.sleep(0.05)


if __name__ == "__main__":
    main()

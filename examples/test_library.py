"""应用内文献库管理测试（无需网络）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.webapp import ResearchWebApp


def expect(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def make_batch(root: Path, run_id: str) -> Path:
    base = root / "downloads" / run_id
    pdf = base / "papers" / "01_test.pdf"
    text = base / "texts" / "01_test.txt"
    pdf.parent.mkdir(parents=True)
    text.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4 fake")
    text.write_text("text", encoding="utf-8")
    (base / "metadata.json").write_text(json.dumps({
        "run_id": run_id,
        "generated_at": "2026-08-22 10:00:00",
        "papers": [],
        "items": [{
            "index": 1, "title": "Managed Paper", "source": "arxiv_search",
            "status": "ok", "error": None,
            "pdf_path": str(pdf), "text_path": str(text),
        }],
    }), encoding="utf-8")
    return base


def main():
    root = Path(tempfile.mkdtemp())
    old_data_dir = os.environ.get("PAPER_STUDIO_DATA_DIR")
    os.environ["PAPER_STUDIO_DATA_DIR"] = str(root / "downloads")
    try:
        batch = make_batch(root, "run-001")
        app = ResearchWebApp(runner=lambda *_args, **_kwargs: {})

        print("== 用例 1：列表与搜索 ==")
        library = app.list_library()
        expect("读取 1 个批次", library["stats"]["batches"] == 1)
        expect("识别本地 PDF", library["stats"]["downloaded"] == 1)
        matched = app.list_library("managed")
        expect("按标题搜索", len(matched["batches"]) == 1)
        expect("搜索统计仅计匹配记录", matched["stats"]["items"] == 1)
        expect("无关搜索为空", app.list_library("unrelated")["batches"] == [])

        print("== 用例 2：删除单篇本地文件 ==")
        expect("删除成功", app.delete_library_item("run-001", 1))
        expect("PDF 已删除", not (batch / "papers" / "01_test.pdf").exists())
        expect("文本已删除", not (batch / "texts" / "01_test.txt").exists())
        item = app.list_library(status="deleted")["batches"][0]["items"][0]
        expect("清单保留删除状态", item["status"] == "deleted")

        print("== 用例 3：整批删除与路径安全 ==")
        batch2 = make_batch(root, "run-002")
        expect("拒绝目录穿越", not app.delete_library_batch("../run-002"))
        foreign_pdf = batch2 / "papers" / "01_test.pdf"
        evil = make_batch(root, "run-evil")
        evil_manifest = evil / "metadata.json"
        evil_data = json.loads(evil_manifest.read_text(encoding="utf-8"))
        evil_data["items"][0]["pdf_path"] = str(foreign_pdf)
        evil_data["items"][0]["text_path"] = None
        evil_manifest.write_text(json.dumps(evil_data), encoding="utf-8")
        expect("篡改清单可隔离处理", app.delete_library_item("run-evil", 1))
        expect("不能跨批次删除文件", foreign_pdf.exists())
        expect("清理测试批次", app.delete_library_batch("run-evil"))
        expect("删除整批", app.delete_library_batch("run-002"))
        expect("批次目录不存在", not batch2.exists())
    finally:
        if old_data_dir is None:
            os.environ.pop("PAPER_STUDIO_DATA_DIR", None)
        else:
            os.environ["PAPER_STUDIO_DATA_DIR"] = old_data_dir

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

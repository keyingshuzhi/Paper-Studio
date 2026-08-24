"""下载限速与文献资料包测试（无需网络）。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.plugins import DataAcquisitionPipeline
from agent.skills import DownloaderSkill, Paper


def expect(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


class FakeDownloader:
    def download(self, _url, dest_dir="downloads", filename=None,
                 expected_pdf=True):
        path = Path(dest_dir) / str(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 fake")
        return path

    def extract_and_clean(self, _file_path, max_pages=None):
        return "raw", "clean text"


class FakeResponse:
    def __init__(self, status, body=b"%PDF-1.4 fake", headers=None):
        self.status_code = status
        self.body = body
        self.headers = headers or {"Content-Type": "application/pdf"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=8192):
        del chunk_size
        yield self.body

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return self.responses.pop(0)


def make_paper(i, pdf=True):
    return Paper(title=f"Paper {i}", url=f"https://example.com/{i}",
                 source="test", pdf_url=(f"https://example.com/{i}.pdf"
                                         if pdf else None))


def main():
    print("== 用例 1：超过 5 篇可完整处理 ==")
    root = Path(tempfile.mkdtemp())
    pipeline = DataAcquisitionPipeline(
        downloader=FakeDownloader(), root_dir=str(root))
    result = pipeline.run([make_paper(i) for i in range(1, 9)],
                          max_downloads=8, delay_seconds=0)
    expect("处理 8 篇", result["stats"]["total"] == 8)
    expect("下载成功 8 篇", result["stats"]["downloaded"] == 8)
    expect("生成 8 个 PDF", len(list(
        (Path(result["base_dir"]) / "papers").glob("*.pdf"))) == 8)
    manifest = json.loads((Path(result["base_dir"]) / "metadata.json").read_text())
    expect("清单持续化完整", len(manifest["items"]) == 8)

    print("== 用例 2：无公开 PDF 单独标记 ==")
    result2 = pipeline.run([make_paper(9, pdf=False)], delay_seconds=0)
    expect("不是网络失败", result2["stats"]["failed"] == 0)
    expect("标记无公开 PDF", result2["stats"]["unavailable"] == 1)

    print("== 用例 3：429 后自动重试 ==")
    session = FakeSession([
        FakeResponse(429, headers={"Retry-After": "0"}),
        FakeResponse(200),
    ])
    downloader = DownloaderSkill(retries=1, min_interval=0, session=session)
    downloader._retry_delay = lambda *_: 0  # type: ignore[method-assign]
    out = downloader.download("https://example.com/paper.pdf",
                              dest_dir=str(root / "retry"))
    expect("重试后下载成功", out.exists())
    expect("发起 2 次请求", session.calls == 2)

    print("== 用例 4：拒绝把 HTML 保存成 PDF ==")
    html_downloader = DownloaderSkill(
        retries=0, min_interval=0,
        session=FakeSession([FakeResponse(
            200, body=b"<html>blocked</html>",
            headers={"Content-Type": "text/html"})]))
    try:
        html_downloader.download("https://example.com/not-pdf",
                                 dest_dir=str(root / "html"),
                                 filename="bad.pdf")
    except RuntimeError as err:
        expect("给出明确 PDF 校验错误", "不是有效 PDF" in str(err))
    else:
        expect("必须拒绝 HTML", False)

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

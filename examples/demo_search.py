"""快速验证：用一个真实关键词跑通 Skills 层搜索链路。"""

import json
import sys
from pathlib import Path

# 允许直接以脚本方式运行（python examples/demo_search.py）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.skills import SearchManager  # noqa: E402


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "transformer attention"
    max_results = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    print(f"[1/2] 正在检索关键词: {query!r} ...")
    manager = SearchManager()
    papers = manager.search(query, max_results=max_results)

    print(f"[2/2] 共聚合到 {len(papers)} 条去重后的文献：\n")
    for i, p in enumerate(papers, 1):
        print(f"  {i:>2}. [{p.source}] ({p.year}) {p.title}")
        authors = ", ".join(p.authors[:3])
        if len(p.authors) > 3:
            authors += " 等"
        print(f"      作者: {authors or '未知'}")
        if p.pdf_url:
            print(f"      PDF : {p.pdf_url}")
        print(f"      链接: {p.url}")
        print()

    out = Path("downloads") / "search_result.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps([p.to_dict() for p in papers],
                              ensure_ascii=False, indent=2))
    print(f"结果已保存至 {out}")


if __name__ == "__main__":
    main()

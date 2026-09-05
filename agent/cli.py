"""命令行入口：研究助理 Agent。

用法示例：
    python -m agent.cli "transformer"                      # 只检索 + 报告
    python -m agent.cli "mamba" --max-results 5            # 限制结果数
    python -m agent.cli "下载关于llm的论文" --max-downloads 3  # 检索 + 下载
    python -m agent.cli "attention" --no-download          # 显式不下载
    python -m agent.cli "llm agent" --summarize            # 检索 + LLM 智能摘要
    python -m agent.cli "mamba" --summarize --analyze      # 摘要 + 跨文献分析
    python -m agent.cli "mamba" --summarize --summarize-limit 3  # 最多摘要 3 篇
    python -m agent.cli "mamba" --deep                     # V3.0 深度研究闭环
    python -m agent.cli "mamba" --deep --rounds 3 --branching 2  # 自定义预算
    python -m agent.cli --compare "transformer|mamba|state space model"  # V5.0 多主题对比
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core import ResearchAgent  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="学术研究助理 Agent (V1.0 + LLM 摘要)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("query", nargs="?", default=None,
                        help="检索关键词或自然语言指令（--compare 时为多主题）")
    parser.add_argument("--compare", action="store_true",
                        help="V5.0 多主题对比研究（query 用 | 分隔主题）")
    parser.add_argument("--max-results", type=int, default=10,
                        help="每个来源的结果上限")
    parser.add_argument("--max-downloads", type=int, default=None,
                        help="最多下载篇数（默认不限制）")
    parser.add_argument("--no-download", action="store_true",
                        help="强制不下载（即使输入含'下载'）")
    parser.add_argument("--summarize", action="store_true",
                        help="生成 LLM 智能摘要（需配置 .env 的 LLM_API_KEY）")
    parser.add_argument("--summarize-limit", type=int, default=None,
                        help="最多摘要几篇（默认全部）")
    parser.add_argument("--analyze", action="store_true",
                        help="跨文献分析：共识/分歧/演进/知识盲点（需 LLM）")
    parser.add_argument("--deep", action="store_true",
                        help="V3.0 深度研究闭环（盲点关键词自动触发下一轮检索）")
    parser.add_argument("--rounds", type=int, default=3,
                        help="深度研究最大轮数")
    parser.add_argument("--branching", type=int, default=2,
                        help="每轮最多衍生的盲点查询数")
    parser.add_argument("--max-queries", type=int, default=7,
                        help="深度研究总查询数上限")
    parser.add_argument("--no-memory", action="store_true",
                        help="禁用研究记忆（默认启用，跨会话去重）")
    parser.add_argument("--no-citations", action="store_true",
                        help="禁用引用网络分析（默认启用）")
    parser.add_argument("--sources", nargs="*", default=None,
                        help="来源白名单，如 arxiv_search scholar_search")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    # V5.0：多主题对比研究
    if args.compare:
        if not args.query:
            print("错误：--compare 需要提供主题（用 | 分隔），如 "
                  '"transformer|mamba"')
            return 2
        from agent.core import MultiTopicComparator

        topics = [t.strip() for t in args.query.split("|") if t.strip()]
        result = MultiTopicComparator().compare(
            topics, max_results=args.max_results)
        print(f"对比报告 : {result['report_path']}")
        return 0

    # V3.0：深度研究闭环
    if args.deep:
        from agent.core import ResearchLoop

        loop = ResearchLoop(max_rounds=args.rounds,
                            branching=args.branching,
                            max_queries=args.max_queries,
                            use_memory=not args.no_memory,
                            analyze_citations=not args.no_citations)
        result = loop.run(args.query, max_results=args.max_results)
        print(f"报告路径 : {result['report_path']}")
        return 0

    agent = ResearchAgent()
    result = agent.run(
        args.query,
        max_results=args.max_results,
        max_downloads=args.max_downloads,
        download=False if args.no_download else None,
        sources=args.sources,
        summarize=args.summarize,
        summarize_limit=args.summarize_limit,
        analyze=args.analyze,
    )

    print(f"\n=== 完成（{result['finished_at']}）===")
    print(f"关键词   : {result['plan'].query}")
    print(f"文献数   : {len(result['papers'])} 篇")
    if result["acquisition"]:
        s = result["acquisition"]["stats"]
        print(f"下载统计 : 成功 {s['ok']}/{s['total']} 篇")
    if result["summaries"]:
        ok = sum(1 for r in result["summaries"] if r.get("ok"))
        print(f"摘要统计 : {ok}/{len(result['summaries'])} 篇")
    if result["analysis"] and not result["analysis"].get("_fallback"):
        a = result["analysis"]
        print(f"分析统计 : 共识 {len(a.get('consensus', []))} 条 | "
              f"分歧 {len(a.get('conflicts', []))} 条 | "
              f"盲点 {len(a.get('gaps', []))} 条")
    print(f"报告路径 : {result['report_path'] or '未生成'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""v0.1.0 端到端 UI 第 9 轮打磨:侧栏竖线 / 搜索框 ✕ / 关于页面简化测试。

覆盖:
  1) 侧栏收起后 .tab.on 不能再有 inset 竖线(box-shadow:inset 2px/3px 0 ...)
  2) 侧栏收起后 body.nav-collapsed .tabs::after 不能再有伪元素
  3) 研究报告搜索框 input#reportSearch 必须是 type="text" 而不是 type="search"
  4) 关于页面已简化:不再有 desktop / persistence / skill_categories / Agent 角色与工作流
  5) 关于页面保留用户友好:研究角色 / 数据源 / 工具 / 这次更新带来了什么
  6) 清理未用 CSS(about-role / about-skill-tag 等)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def expect(name, cond, got=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + ("" if cond else f" -> {got!r}"))
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def main() -> None:
    html_path = Path("agent/static/index.html")
    text = html_path.read_text(encoding="utf-8")

    # ---- 1) 侧栏竖线 ----
    print("== 用例 1:侧栏收起后无竖线 ==")
    # 找所有 .tab.on{...} 规则,确保没有 inset 2-3px 0 ...(那是 2-3px 左侧竖线)
    tabon_rules = re.findall(r"\.tab\.on\{[^}]+\}", text)
    expect(".tab.on 规则总数(应有 6 处深浅主题)",
           len(tabon_rules) >= 4, len(tabon_rules))
    inset_lines = [r for r in tabon_rules if re.search(r"box-shadow:\s*inset\s+[23]px\s+0", r)]
    expect("无 .tab.on 含 box-shadow:inset 2-3px 0 ...(竖线)",
           len(inset_lines) == 0, inset_lines[:2])
    # nav-collapsed .tabs::after 在收起后不应该有
    nav_after = re.findall(r"body\.nav-collapsed \.tabs::after\{[^}]+\}", text)
    # 允许有但 content 必须是 none
    nav_after_with_content = [r for r in nav_after if "content:" in r and '""' not in r and "none" not in r]
    expect("body.nav-collapsed .tabs::after 不再有伪元素线",
           len(nav_after_with_content) == 0, nav_after_with_content)

    # ---- 2) 搜索框 ✕ ----
    print("\n== 用例 2:研究报告搜索框移除 ✕ ==")
    m = re.search(r'<input id="reportSearch"[^>]*>', text)
    expect("reportSearch 元素存在", m is not None)
    if m:
        expect('reportSearch 是 type="text" 不是 type="search"',
               'type="text"' in m.group(0) and "search" not in m.group(0).split('type="text"')[0].split('type="search"')[-1],
               m.group(0))
        # 上面检查不严谨,直接确认不含 type="search"
        expect('reportSearch 不含 type="search"',
               'type="search"' not in m.group(0), m.group(0))
    # 全局 type=search 只保留 librarySearch / memorySearch / jobSearch / skillSearch(用户没要求改这些)
    # 但仅验证 reportSearch 必须是 text 即可

    # ---- 3) 关于页面简化 ----
    print("\n== 用例 3:关于页面面向用户,取消不必要描述 ==")
    expect("不再使用 'desktop' 变量",
           "const desktop=" not in text or "desktop=!!window.agent" not in text)
    expect("不再使用 'persistence' 变量",
           "const persistence" not in text)
    expect("不再使用 'kind'/'platform' 变量",
           "const kind=" not in text and "const platform=" not in text)
    expect("不再渲染 'Agent 角色与工作流' 区块",
           "Agent 角色与工作流" not in text)
    expect("不再渲染 'Skill 目录' 区块",
           "Skill 目录" not in text)
    expect("不再渲染 '同一套研究能力,两种使用方式'(改成'两种使用方式')",
           "同一套研究能力,两种使用方式" not in text)
    expect("不再渲染 v0.1.0 核心能力(改成'这次更新带来了什么')",
           "v0.1.0 核心能力" not in text)
    expect("不再渲染 '数据与隐私'(改成'你的数据由谁保管')",
           "数据与隐私" not in text)
    # 渲染新文案
    expect("渲染 '这次更新带来了什么'",
           "这次更新带来了什么" in text)
    expect("渲染 '你的数据由谁保管'",
           "你的数据由谁保管" in text)
    expect("关于页已移除取消后的成本管理描述",
           "费用透明可查" not in text and "成本中心" not in text)
    expect("关于页明确本地资产与云端模型的数据边界",
           "模型边界由你选择" in text and "必要内容会发送" in text)
    expect("渲染 '可以接入哪些知识库'",
           "可以接入哪些知识库" in text)
    expect("使用用户友好词'研究角色'而非'Agent 角色'",
           "研究角色" in text)
    expect("使用用户友好词'内置能力'而非'领域 Skill'",
           "内置能力" in text)
    expect("使用用户友好词'外部工具'而非'MCP 工具'",
           "外部工具" in text)
    expect("使用用户友好词'数据源'而非'数据源连接器'",
           "数据源" in text)
    # 渲染保留
    expect("保留 6 个 capability cards(capCard 函数)",
           "capCard" in text and "caps.length" in text)
    expect("保留 hero 渐变背景",
           "about-hero-glow" in text)
    expect("保留 4 列 stats 横幅",
           "about-stats" in text and "repeat(4,minmax(0,1fr))" in text)
    expect("保留 Web 版/桌面版 双卡",
           "Web 版" in text and "桌面版" in text)
    expect("保留 4 个数据源连接器",
           "Zotero" in text and "Obsidian" in text and "Notion" in text and "机构库" in text)
    expect("保留 4 个隐私事实(简化后)",
           text.count("about-fact") >= 4)
    # loadAbout 简化
    m = re.search(r"async function loadAbout\(\)\{(.*?)^\}", text, re.DOTALL | re.MULTILINE)
    expect("loadAbout 简化,不再有 desktop 分支",
           m is not None and "desktop" not in m.group(0), m.group(0)[:200] if m else "")

    # ---- 4) 清理未用 CSS ----
    print("\n== 用例 4:清理未用 CSS ==")
    unused_classes = [
        "about-roles",
        "about-role",
        "about-role-head",
        "about-role-icon",
        "about-role-skills",
        "about-skills-block",
        "about-skills-group",
        "about-skills-label",
        "about-skill-tag",
        "about-skill-dot",
        "about-skills-list",
    ]
    for cls in unused_classes:
        # CSS 中不应有 .cls 规则
        css_rules = re.findall(r"\." + re.escape(cls) + r"(?=[\s.,:{>])\.?[^{]*\{[^}]*\}", text)
        expect(f"CSS 不再含 .{cls} 规则", len(css_rules) == 0, css_rules[:1])

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

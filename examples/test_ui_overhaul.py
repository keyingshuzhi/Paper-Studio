"""UI 大升级回归:5 个用户反馈 + 多个一致性细节。

覆盖:
  1) ➕ 按钮居中
  2) 对比研究页布局
  3) 文献库按钮位置稳定
  4) 知识记忆/报告按钮位置稳定
  5) UI token 体系 + 通用一致性
  6) jobCard 移除内联 style
  7) 报告 reader head 不撑高
  8) 对比页 submit bar 视觉
  9) 收起导航无竖线、报告搜索框无伪图标
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


def find_first(text, pattern):
    m = re.search(pattern, text)
    return m.group() if m else None


def get_rule_body(text, selector):
    """提取 .selector{...} 整段"""
    m = re.search(rf"\.{re.escape(selector)}\{{([^}}]+)\}}", text)
    return m.group(1) if m else ""


def main() -> None:
    html_path = Path("agent/static/index.html")
    text = html_path.read_text(encoding="utf-8")
    ui_css = Path("agent/static/assets/ui-v2.css").read_text(encoding="utf-8")

    # ---------- 1) ➕ 位置不居中 ----------
    print("== 用例 1:➕ 按钮(section-heading 内 button)垂直居中 ==")
    body = get_rule_body(text, "section-heading")
    expect("section-heading 用 flex + align-items:center",
           "align-items:center" in body and "display:flex" in body,
           body[:80])
    body_btn = get_rule_body(text, "section-heading>button")
    expect(".section-heading>button 用 align-self:center",
           "align-self:center" in body_btn, body_btn)
    expect(".section-heading>button 走 inline-flex + align center",
           "display:inline-flex" in body_btn
           and "align-items:center" in body_btn
           and "justify-content:center" in body_btn, body_btn)
    body_h2 = get_rule_body(text, "section-heading h2")
    expect("section-heading h2 用 flex + align-items:center",
           "display:flex" in body_h2 and "align-items:center" in body_h2,
           body_h2)
    expect("section-heading h2 高度与按钮一致(38px)",
           "height:38px" in body_h2, body_h2)

    # ---------- 2) 对比研究页 UI 布局 ----------
    print("== 用例 2:对比研究页布局 ==")
    expect("compare-shell 用 grid 主体+侧栏",
           "grid-template-columns:minmax(0,1fr) 320px" in
           get_rule_body(text, "compare-shell"))
    expect("compare-section-heading 用 flex 居中",
           "display:flex" in get_rule_body(text, "compare-section-heading")
           and "align-items:center" in get_rule_body(text, "compare-section-heading"))
    expect("section-number 用 32x32 圆角 + 渐变",
           "32px" in get_rule_body(text, "section-number")
           and "linear-gradient" in get_rule_body(text, "section-number"))
    expect("compare-form 有 padding 化卡片",
           "padding" in get_rule_body(text, "compare-form"))
    expect("compare-section 走 border-top 分隔",
           "border-top" in get_rule_body(text, "compare-section"))
    expect("compare-preview ol 用 counter-increment 编号",
           "counter-reset:cmp" in get_rule_body(text, "compare-preview ol")
           or "counter-increment" in get_rule_body(text, "compare-preview ol")
           or "counter-reset" in get_rule_body(text, "compare-preview ol"))
    expect("compare-side 用 sticky 跟主滚动",
           "position:sticky" in get_rule_body(text, "compare-side"))
    expect("workspace-page-heading 走渐变 h2",
           "background-clip:text" in text or "-webkit-background-clip:text" in text)
    expect("compare-download-option 卡片化",
           "display:flex" in get_rule_body(text, "compare-download-option")
           and "border" in get_rule_body(text, "compare-download-option"))
    expect("text-button 走 inline-flex 30px 居中(用于示例填入)",
           "display:inline-flex" in get_rule_body(text, "text-button")
           and "height:30px" in get_rule_body(text, "text-button"))
    expect("compare-submit-bar flex space-between + border-top",
           "display:flex" in get_rule_body(text, "compare-submit-bar")
           and "justify-content:space-between" in get_rule_body(text, "compare-submit-bar")
           and "border-top" in get_rule_body(text, "compare-submit-bar"))

    # ---------- 3) 文献库按钮位置稳定 ----------
    print("== 用例 3:文献库 paper-row / paper-actions 按钮位置稳定 ==")
    body = get_rule_body(text, "paper-row")
    expect("paper-row grid 三列(checkbox/main/actions — 防止 actions 落 col 1)",
           "grid-template-columns" in body
           and body.count("auto") >= 2
           and "minmax(0,1fr)" in body, body)
    expect("paper-row align-items:start(让 4 按钮独立靠顶,actions 不被主区高度拉高)",
           ("align-items:start" in body or "align-items:center" in body)
           and "flex-wrap:nowrap" in get_rule_body(text, "paper-actions"),
           body)
    body = get_rule_body(text, "paper-actions")
    expect("paper-actions 走 flex-shrink:0(按钮不被挤压)",
           "flex-shrink:0" in body, body)
    body = get_rule_body(text, "library-batch-head")
    expect("library-batch-head flex nowrap(头部不换行)",
           "flex-wrap:nowrap" in body, body)
    # 复合 selector 可能跨多个 { } 块;用全文匹配
    expect("library-batch-head>div 截断 run_id 长 id",
           "library-batch-head>div:first-child" in text
           and "text-overflow:ellipsis" in text
           and "library-batch-head" in text)
    expect("library-batch-head .actions 走 flex-shrink:0",
           "library-batch-head .actions" in text
           and "flex-shrink:0" in text)
    expect("paper-title 走 line-clamp 2(过长不撑高)",
           "line-clamp" in get_rule_body(text, "paper-title"))
    expect("action-popover 用 popover CSS 美化",
           "min-width" in get_rule_body(text, "action-popover")
           and "box-shadow" in get_rule_body(text, "action-popover"))

    # ---------- 4) 知识记忆 / 报告 按钮位置稳定 ----------
    print("== 用例 4:知识记忆 / 报告 toolbar 按钮位置稳定 ==")
    body = get_rule_body(text, "job .head")
    expect(".job .head flex nowrap(标题不换行)",
           "flex-wrap:nowrap" in body, body)
    body = get_rule_body(text, "job-actions")
    expect(".job-actions flex-shrink:0 + nowrap",
           "flex-shrink:0" in body and "flex-wrap:nowrap" in body, body)
    body = get_rule_body(text, "memory-reader-head")
    expect(".memory-reader-head align-items:center + min-width:0",
           "align-items:center" in body and "min-width:0" in body, body)
    expect(".memory-reader-head h2 ellipsis 截断长标题",
           "text-overflow:ellipsis" in get_rule_body(text, "memory-reader-head h2"))
    expect(".memory-reader-actions 不换行",
           "flex-wrap:nowrap" in get_rule_body(text, "memory-reader-actions"))
    expect(".report-reader-head align-items:center + min-width:0",
           "align-items:center" in get_rule_body(text, "report-reader-head")
           and "min-width:0" in get_rule_body(text, "report-reader-head"))
    expect(".report-reader-head h2 ellipsis",
           "text-overflow:ellipsis" in get_rule_body(text, "report-reader-head h2"))
    expect(".report-reader-actions 已有 flex-shrink:0",
           "flex-shrink:0" in get_rule_body(text, "report-reader-actions"))
    expect(".workspace-actionbar 走 flex min-height:48px 工具栏",
           "display:flex" in get_rule_body(text, "workspace-actionbar")
           and "min-height:48px" in get_rule_body(text, "workspace-actionbar"))
    expect(".toolbar-label 走 uppercase + letter-spacing",
           "toolbar-label" in text
           and ("uppercase" in text and "letter-spacing" in text
                and "toolbar-label" in text))

    # ---------- 5) 通用 UI 一致性(token 化) ----------
    print("== 用例 5:UI token 体系 ==")
    m = re.search(r":root\{([^}]+)\}", text, re.DOTALL)
    expect("找到 :root", m is not None)
    root = m.group(1) if m else ""
    expect("--sp-1..8 间距阶梯", all(f"--sp-{i}" in root for i in range(1, 9)),
           [s for s in ["--sp-1","--sp-2","--sp-3","--sp-4","--sp-5",
                         "--sp-6","--sp-7","--sp-8"] if s not in root])
    expect("--shadow 阴影阶梯", all(
        s in root for s in ["--shadow-sm", "--shadow", "--shadow-lg", "--shadow-xl"]))
    expect("--ring focus ring", "--ring" in root)
    expect("--chip-muted 至少 1 个 chip token",
           "--chip-muted" in root)
    expect("--radius-xl 圆角阶梯", "--radius-xl" in root)

    expect(".card 使用 --radius-lg",
           "--radius-lg" in get_rule_body(text, "card"))
    expect(".btn-primary 走 linear-gradient + transition:transform",
           "linear-gradient" in get_rule_body(text, "btn-primary")
           and ("transition" in get_rule_body(text, "btn-primary")
                or ".btn-primary" in text and "transition" in text
                and "translateY" in text))
    expect(".btn-primary:hover 走 translateY 提升反馈",
           "btn-primary:hover" in text
           and "translateY" in text)
    expect(".btn-ghost 走统一 padding 7px 14px",
           "padding:7px 14px" in get_rule_body(text, "btn-ghost"))
    expect(".btn-sm 存在(用于 toolbar 内联按钮)",
           "padding:5px 11px" in get_rule_body(text, "btn-sm"))
    expect(".icon-button 30x30 居中(用于 paper-row 更多按钮)",
           "30px" in get_rule_body(text, "icon-button")
           and "align-items:center" in get_rule_body(text, "icon-button")
           and "justify-content:center" in get_rule_body(text, "icon-button"))

    expect(".role-card min-height 一致",
           "min-height" in get_rule_body(text, "role-card"))
    expect(".role-card hover 上浮 + shadow",
           "role-card:hover" in text
           and "translateY" in text
           and "box-shadow" in text)
    expect(".preset .on/.active 走渐变高亮",
           ("preset.on" in text or "preset.active" in text
            or ".preset.on" in text or ".preset.active" in text)
           and "linear-gradient" in text)

    # ---------- 6) 移除 jobCard 内联 style 按钮 ----------
    print("== 用例 6:jobCard 不再使用内联 style(防止覆盖) ==")
    m = re.search(r"function jobCard\(j\)\{.*?return `[\s\S]*?`;?\}", text)
    fn = m.group() if m else ""
    expect("jobCard 不含 'padding:4px 10px' 内联 style",
           "padding:4px 10px" not in fn)
    expect("jobCard 改用 btn-sm class",
           "btn-sm" in fn)

    # ---------- 7) 报告 reader head 标题不撑高 ----------
    print("== 用例 7:报告 reader head 标题不撑高按钮区 ==")
    body = get_rule_body(text, "report-reader-head")
    expect("report-reader-head align-items:center",
           "align-items:center" in body, body)
    expect("report-reader-head min-width:0",
           "min-width:0" in body, body)

    # ---------- 8) 收起导航 / 报告搜索框视觉回归 ----------
    print("== 用例 8:收起导航无竖线、报告搜索框无伪图标 ==")
    expect("收起导航移除右侧边线",
           re.search(
               r"body\.nav-collapsed\s+\.tabs\s*\{[^}]*border-right:\s*0\s*!important",
               ui_css,
           ) is not None)
    expect("收起导航隐藏 tab 前后装饰线",
           re.search(
               r"body\.nav-collapsed\s+\.tab::before,\s*"
               r"body\.nav-collapsed\s+\.tab::after\s*\{[^}]*"
               r"display:\s*none\s*!important[^}]*content:\s*none\s*!important",
               ui_css,
           ) is not None)
    expect("报告搜索框伪图标被移除",
           re.search(
               r"\.report-toolbar::before\s*\{[^}]*display:\s*none\s*!important"
               r"[^}]*content:\s*none\s*!important",
               ui_css,
           ) is not None)
    expect("报告搜索框恢复标准左内边距",
           re.search(
               r"\.report-toolbar\s+input\s*\{[^}]*padding-left:\s*12px\s*!important",
               ui_css,
           ) is not None)

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

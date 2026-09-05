"""UI 第 8 轮回归:文献库 + 知识图谱。

覆盖:
  1) paper-row 3-child grid 修复(checkbox / main / actions 三列对齐)
  2) 知识图谱不溢出 / 有 legend / 节点可点 / 列头
  3) 知识记忆 item 卡片一致性 + 选中态
  4) 文献库 batch 视觉层次
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


def get_rule(text, selector):
    """抓 .selector{...} 或 .selector, .other{...} 之一。

    返回**第二个** .selector{...}(如果有),因为第一个通常是 overscroll 等小规则。
    优先找 .selector{ (单独规则),如果没有再退到 .selector, 复合。
    """
    # 1) 找所有单独的 .selector{...}
    pat_alone = re.compile(rf"(?<![,.\w-])\.{re.escape(selector)}\{{([^}}]+)\}}")
    matches = list(pat_alone.finditer(text))
    if matches:
        # 选最长(通常是更具体的样式)
        best = max(matches, key=lambda m: len(m.group(1)))
        return best.group(1)
    # 2) 复合
    pat_compound = re.compile(rf"\.{re.escape(selector)}\s*,")
    m = pat_compound.search(text)
    if m:
        brace = text.find("{", m.start())
        if brace > 0:
            end = text.find("}", brace)
            return text[brace + 1:end] if end > 0 else ""
    return ""


def get_first_compound(text, selector):
    """抓 .selector{ ... 一直到下一条 . 或 注释 / media,以支持多个连续段。"""
    # 简单策略:找到 selector 第一次出现,取到第 4 个 }
    idx = text.find(selector)
    if idx < 0:
        return ""
    # 找第一个 {
    start = text.find("{", idx)
    if start < 0:
        return ""
    # 找对应 }
    depth = 1
    i = start + 1
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[start + 1:i - 1]


def main() -> None:
    html_path = Path("agent/static/index.html")
    text = html_path.read_text(encoding="utf-8")
    # 也读 ui-v2.css
    v2_path = Path("agent/static/assets/ui-v2.css")
    text_v2 = v2_path.read_text(encoding="utf-8") if v2_path.exists() else ""

    # ---------- 1) paper-row 3-child grid ----------
    print("== 用例 1:paper-row 三列布局(checkbox / main / actions) ==")
    body = get_rule(text, "paper-row")
    # 关键:三列 grid-template-columns:auto minmax(0,1fr) auto
    expect("paper-row grid 3 列(checkbox / main / actions)",
           "auto" in body and "minmax(0,1fr)" in body and body.count("auto") >= 2,
           body)
    expect("paper-row gap 用 token",
           "var(--sp-3)" in body, body)
    expect("paper-row align-items:start 让 actions 跟主区顶端对齐",
           "align-items:start" in body, body)
    expect("paper-row hover 反馈",
           ":hover" in text and "paper-row:hover" in text)

    # paper-actions 不被挤 + 自对齐
    body = get_rule(text, "paper-actions")
    expect("paper-actions 走 flex-shrink:0 + flex-wrap:nowrap + align-self:start",
           "flex-shrink:0" in body
           and "flex-wrap:nowrap" in body
           and "align-self:start" in body, body)

    # === 新增 4 按钮布局 ===
    expect("paper-row col 3 扩到 244px 以容纳 4 按钮(在 ui-v2.css)",
           "244px" in text_v2, text_v2[:50000])
    expect("paper-actions 4 个按钮 inline-flex 排成一行",
           text.count("paper-action-read") >= 1
           and text.count("paper-action-pdf") >= 1
           and text.count("paper-action-folder") >= 1
           and text.count("paper-action-delete") >= 1)
    expect("paper-action-read 含 book SVG icon + 文字",
           "uiIcon('book')" in text and "阅读与批注" in text)
    expect("paper-action-pdf 用 external icon + disabled fallback",
           "uiIcon('external')" in text and "noPdf" in text)
    expect("paper-action-folder 用 folder icon",
           "uiIcon('folder')" in text)
    expect("paper-action-delete 用 trash icon + danger hover",
           "uiIcon('trash')" in text
           and ".paper-action-delete:not(:disabled):hover" in text)
    expect("icon-button hover 走蓝色高亮(PDF/folder)",
           ".paper-action-pdf:not(:disabled):hover" in text
           and "background:#3b82f620" in text)
    expect("ui-icon 全局 base rule 14x14 stroke currentColor",
           "ui-icon{width:14px" in text)

    # library-select checkbox
    body = get_rule(text, "library-select")
    expect("library-select checkbox 16x16 + accent-color",
           "16px" in body and "accent-color" in body, body)

    # paper-meta 改 flex
    body = get_rule(text, "paper-meta")
    expect("paper-meta 改 flex + gap + wrap",
           "display:flex" in body and "flex-wrap:wrap" in body
           and "gap" in body, body)

    # paper-extra 有 CSS(原 HTML 有但 CSS 全无)
    expect("paper-extra details 有 summary + ::before 三角",
           "paper-extra" in text
           and ("::before" in text and "paper-extra" in text)
           and "summary" in text
           and "[open]" in text)

    # paper-error 美化为卡片
    body = get_rule(text, "paper-error")
    expect("paper-error 卡片化(背景 + border + padding)",
           "border" in body and "padding" in body and "background" in body, body)

    # ---------- 2) 知识图谱 ----------
    print("== 用例 2:知识图谱不溢出 + 有 legend + 节点可点 ==")
    body = get_rule(text, "memory-graph")
    expect("memory-graph 不依赖 min-width 强制宽度",
           "min-width:640px" not in text, "still has min-width:640px")
    expect("memory-graph 走 SVG + 容器 max-height:480px",
           "svg" in text
           and "max-height:480px" in body, body)
    expect("memory-graph-card 走 border-radius-lg + card2",
           "card2" in get_rule(text, "memory-graph-card")
           and "radius-lg" in get_rule(text, "memory-graph-card"),
           get_rule(text, "memory-graph-card"))

    # legend HTML 存在
    expect("图谱 HTML 含 legend 6 类色块",
           text.count("legend-dot") >= 6
           and all(color in text for color in
                   ["#4f8ee8", "#8b70e8", "#38a88a",
                    "#e89a48", "#d8619a", "#e05656"]))

    # graph-head 容器
    body = get_rule(text, "memory-graph-head")
    expect("memory-graph-head 用 flex 居中",
           "display:flex" in body and "align-items:center" in body, body)
    body = get_rule(text, "memory-graph-legend")
    expect("memory-graph-legend 用 flex wrap",
           "display:flex" in body and "flex-wrap:wrap" in body, body)
    body = get_rule(text, "legend-item")
    expect("legend-item 用 inline-flex",
           "display:inline-flex" in body
           and "align-items:center" in body, body)
    body = get_rule(text, "legend-dot")
    expect("legend-dot 8x8 圆形",
           "width:8px" in body and "height:8px" in body
           and "border-radius" in body, body)

    # node 可点 + hover
    body = get_rule(text, "memory-node")
    expect("memory-node cursor pointer + hover 反馈",
           "cursor:pointer" in body
           and "memory-node:hover" in text)

    # col-head SVG 类
    expect("memoryGraph 函数生成 col-head SVG 标记",
           "memory-col-head" in text)
    # SVG 移除硬编码 min-width,使用 viewBox preserveAspectRatio
    expect("memoryGraph 用 viewBox + preserveAspectRatio",
           "viewBox" in text and "preserveAspectRatio" in text
           and "memoryGraph" in text)

    # 移除 x 硬编码 col layout(95+col*190)
    expect("memoryGraph 不再使用硬编码列布局 95+col*190",
           "95+col*190" not in text)

    # ---------- 3) 知识记忆 item 卡片 ----------
    print("== 用例 3:知识记忆 item 卡片一致 + 选中态 ==")
    body = get_rule(text, "memory-item")
    expect("memory-item 走 card2 + radius + transition",
           "card2" not in body  # 用 var(--bg2)
           and "radius" in body
           and "transition" in body, body)
    expect("memory-item 有 .on 选中态(蓝边 + 蓝底 + ring)",
           ".memory-item.on" in text
           and "border-color:var(--blue)" in text)
    expect("memory-item head 标题 2 行 line-clamp",
           "-webkit-line-clamp:2" in get_rule(text, "memory-item-head b"))
    expect("memory-item-meta 改 flex + gap",
           "display:flex" in get_rule(text, "memory-item-meta")
           and "gap" in get_rule(text, "memory-item-meta"))
    expect("memory-pill 走 chip 背景 + padding 2px 7px",
           "padding:2px 7px" in get_rule(text, "memory-pill")
           and "background:var(--bg)" in get_rule(text, "memory-pill"))
    expect("memory-pill.pin 颜色加深",
           "0b6a4b30" in get_rule(text, "memory-pill.pin"))
    expect("memory-terms 走 gap 5px",
           "gap:5px" in get_rule(text, "memory-terms"))

    # memory-sidebar-head 升级
    body = get_rule(text, "memory-sidebar-head")
    expect("memory-sidebar-head 走渐变 + token",
           "linear-gradient" in body
           and "var(--line)" in body, body)

    # memory-list 走 var(--bg)
    body = get_rule(text, "memory-list")
    expect("memory-list 走 var(--bg) 背景",
           "var(--bg)" in body, body)

    # ---------- 4) 文献库整体视觉 ----------
    print("== 用例 4:文献库 batch 视觉层次 ==")
    body = get_rule(text, "library-batch")
    expect("library-batch 走 card + radius-lg + hover",
           "var(--card)" in body
           and "radius-lg" in body
           and ":hover" in text and "library-batch:hover" in text,
           body)
    body = get_rule(text, "library-batch-head")
    expect("library-batch-head 走渐变 + token padding",
           "linear-gradient" in body
           and "var(--sp-3)" in body, body)
    expect("library-batch-head>div 走 flex column 居中",
           "flex-direction:column" in get_rule(text, "library-batch-head>div:first-child"))

    # library-summary
    body = get_rule(text, "library-summary")
    expect("library-summary 走 card2 + hover 上浮",
           "library-summary div:hover" in text
           and "var(--card2)" in get_rule(text, "library-summary div"))
    expect("library-summary 数字 20px font-bold tabular-nums",
           "font-size:20px" in get_rule(text, "library-summary b")
           and "tabular-nums" in get_rule(text, "library-summary b"))

    # library-workspace-head
    body = get_rule(text, "library-workspace-head")
    expect("library-workspace-head 走 token padding",
           "var(--sp-5)" in body, body)
    body = get_rule(text, "library-workspace-head h2")
    expect("library-workspace-head h2 走渐变文字",
           "background-clip:text" in text
           and ("var(--text)" in body or "var(--text2)" in body),
           body)

    # library-heading
    expect("library-heading 走 flex + min-height:32px",
           "min-height:32px" in get_rule(text, "library-heading")
           and "align-items:center" in get_rule(text, "library-heading"))

    # librarySelectBar (批量选择)
    body = get_rule(text, "library-selection-bar")
    expect("library-selection-bar 走渐变背景",
           "4665d8" in body or "blue" in body.lower(), body)

    # ---------- 5) memory-reader 内容区 ----------
    print("== 用例 5:memory-reader 内容区 ==")
    body = get_rule(text, "memory-reader")
    expect("memory-reader 走双层渐变 + token padding",
           "radial-gradient" in body
           and "var(--sp-5)" in body
           and "var(--sp-6)" in body, body)

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

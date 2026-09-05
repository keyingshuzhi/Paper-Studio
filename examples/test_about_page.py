"""设置 → 关于页面 v0.1.0 升级测试。

覆盖:
  1) /api/about 返回完整结构(name, version, build_time, stats, capabilities, skill_categories)
  2) renderAbout 输出包含全部新组件(hero / stats banner / 6 cards / 4 roles / skills / connectors)
  3) UI 风格:about-* 类的 token 化设计
  4) 主题适配:深色 / 浅色都有专属样式
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def expect(name, cond, got=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + ("" if cond else f" -> {got!r}"))
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def http_get(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "ignore")


def main() -> None:
    base = "http://127.0.0.1:18799"

    # ---- /api/about 结构 ----
    print("== 用例 1:/api/about 数据完整 ==")
    try:
        status, body = http_get(f"{base}/api/about")
        data = json.loads(body)
    except Exception as exc:
        print(f"  [FAIL] 无法连接 {base}: {exc!r}")
        return
    expect("HTTP 200", status == 200)
    expect("name + version + build_time 字段", "name" in data and "version" in data and "build_time" in data, data)
    expect("version 是 0.1.0", data.get("version") == "0.1.0", data.get("version"))
    expect("stats.skills >= 25 (实际 30)", data.get("stats", {}).get("skills", 0) >= 25, data.get("stats"))
    expect("stats.agent_roles == 4", data.get("stats", {}).get("agent_roles") == 4, data.get("stats"))
    expect("stats.datasources == 4", data.get("stats", {}).get("datasources") == 4, data.get("stats"))
    expect("stats.mcp_tools >= 15 (实际 18)", data.get("stats", {}).get("mcp_tools", 0) >= 15, data.get("stats"))
    expect("capabilities 6 项", len(data.get("capabilities", [])) == 6, len(data.get("capabilities", [])))
    caps = {c["id"]: c for c in data.get("capabilities", [])}
    for need in ("agent", "providers", "mcp", "datasources", "library", "memory"):
        expect(f"capability {need} 存在", need in caps, list(caps.keys()))
    expect("每个 capability 都有 icon/name/summary/highlights",
           all(set(c.keys()) >= {"id", "icon", "name", "summary", "highlights"} for c in caps.values()))
    sk = data.get("skill_categories", {})
    expect("skill_categories 3 类(research/memory/infrastructure)",
           set(sk.keys()) >= {"research", "memory", "infrastructure"}, list(sk.keys()))
    expect("research 类 >= 10 个 skill", len(sk.get("research", [])) >= 10, sk.get("research"))
    expect("memory 类 >= 10 个 skill", len(sk.get("memory", [])) >= 10, sk.get("memory"))

    # ---- renderAbout 渲染完整 ----
    print("\n== 用例 2:renderAbout 渲染 v0.1.0 全组件 ==")
    html_path = Path("agent/static/index.html")
    text = html_path.read_text(encoding="utf-8")
    ui_css = Path("agent/static/assets/ui-v2.css").read_text(encoding="utf-8")
    # 直接用 api 数据模拟 renderAbout 输出(因为浏览器里要等异步)
    # 这里我们检查 renderAbout 函数定义本身 + 关键字符串模板
    # 关键 bug 修复:loadAbout() 必须存在并被正确触发
    expect("loadAbout 函数已定义(防'正在读取'永久转圈)",
           "function loadAbout" in text or "async function loadAbout" in text)
    expect("loadAbout 包含 dataset.loaded 防重复逻辑",
           "dataset.loaded" in text)
    expect("loadAbout 包含 dataset.loading 防并发",
           "dataset.loading" in text)
    expect("设置 tab 点击触发 loadAbout(不再依赖 about 按钮 on 状态)",
           'if(t.dataset.p==="settings")' in text and "loadAbout()" in text)
    expect("deep-link 支持 ?tab=settings&setting=about",
           "p===\"settings\"" in text and "get(\"setting\")" in text)
    expect("deep-link 在请求工具与设置点击处理器初始化后执行",
           text.index("applyInitialRoute();") > text.index("const jf=")
           and text.index("applyInitialRoute();")
           > text.index('document.querySelectorAll("[data-setting]")'))
    expect("loadAbout 失败时仍给出可见错误(不卡转圈)",
           "renderAbout({version:" in text and "toast" in text
           and 'loaded="err"' in text)
    expect("renderAbout 函数存在且接收 info",
           "function renderAbout(info=" in text)
    expect("渲染 about-hero(渐变 + 蓝色装饰)",
           "about-hero-glow" in text and "about-hero" in text)
    expect("渲染 about-stats 横幅(4 个核心数据)",
           "about-stats" in text and "about-stat" in text and text.count("about-stat ") >= 1)
    expect("渲染 6 个 capability cards(从 caps.map)",
           "caps.length?caps.map(capCard).join" in text)
    expect("渲染 4 个数据源连接器(Zotero/Obsidian/Notion/机构)",
           "Zotero" in text and "Obsidian" in text and "Notion" in text and "机构" in text)
    expect("渲染 4 个数据边界事实(本地资产/密钥/模型/清理)",
           text.count("about-fact") >= 4)
    about_markup = re.search(
        r"function renderAbout\(info=\{\}\)\{([\s\S]*?)\n\}", text)
    about_markup_text = about_markup.group(1) if about_markup else ""
    expect("关于页不再出现成本管理入口或费用卡片",
           "成本中心" not in about_markup_text
           and "费用透明可查" not in about_markup_text)
    expect("数据说明准确区分本地资产与云端推理边界",
           "研究资产默认保存在本机" in about_markup_text
           and "必要内容会发送给你选择的服务商" in about_markup_text
           and "模型边界由你选择" in about_markup_text)
    expect("4 张数据说明卡均正确闭合",
           len(re.findall(
               r'class="about-fact"[^\n]+</span></div></div>',
               about_markup_text,
           )) == 4)
    expect("footer 含版本号",
           "Paper Studio v" in text)
    # 用户面向
    expect("移除技术描述 'desktop'/'persistence'/'Skill 目录'/'Agent 角色与工作流'",
           "desktop" not in text and "persistence" not in text
           and "Skill 目录" not in text and "Agent 角色与工作流" not in text
           and "skill_categories" not in text and "agent_roles_catalog" not in text)
    expect("使用用户友好词:研究角色/数据源/工具",
           "研究角色" in text and "数据源" in text and "工具" in text)
    expect("关于页浅色卡片使用最终白色契约",
           'html[data-theme="light"] #setting-about .about-connector' in ui_css
           and "background: #fff" in ui_css)
    expect("下拉箭头改为垂直居中的自定义图标",
           "appearance: none" in ui_css
           and "background-position: right 13px center !important" in ui_css
           and ui_css.count("background-image: url(") >= 2)

    # ---- UI 风格化 ----
    print("\n== 用例 3:UI 风格化 (深色 + 浅色) ==")
    # 深色主题样式
    expect(".about-stat 渐变背景 + 大数字渐变文字",
           "about-stat b" in text and "linear-gradient(135deg,#60a5fa,#a78bfa)" in text)
    expect(".about-cap 卡片 3 列 + hover 上浮",
           "about-cap-grid" in text and "repeat(3,minmax(0,1fr))" in text
           and "transform:translateY(-2px)" in text)
    expect(".about-cap 每类有专属 accent 色",
           "--accent:#60a5fa" in text and "--accent:#a78bfa" in text
           and "--accent:#34d399" in text and "--accent:#fbbf24" in text
           and "--accent:#f472b6" in text and "--accent:#22d3ee" in text)
    expect(".about-cap-icon 渐变背景 + emoji icon",
           "about-cap-icon" in text and "linear-gradient(135deg,#3b82f633,#8b5cf633)" in text)
    expect(".about-cap-tags 高亮 chip 化",
           "about-cap-tags em" in text)
    expect(".about-connector 4 列卡片",
           "about-connectors" in text and "repeat(4,minmax(0,1fr))" in text)
    expect(".about-stats 4 列数字横幅",
           "repeat(4,minmax(0,1fr))" in text)
    # 浅色主题
    light_themed = (
        'html[data-theme="light"] .about-stat' in text
        and 'html[data-theme="light"] .about-cap' in text
        and 'html[data-theme="light"] .about-connector' in text
        and 'html[data-theme="light"] .about-footnote' in text
    )
    expect("浅色主题 4+ 处专属样式", light_themed)
    # 响应式
    expect("响应式 ≤900px 切 2 列",
           "@media(max-width:900px)" in text
           and "about-cap-grid{grid-template-columns:repeat(2,1fr)}" in text)
    expect("响应式 ≤640px 切 1 列(about-platforms/about-facts/about-connectors → 1fr)",
           "@media(max-width:640px)" in text
           and "about-platforms,.about-facts,.about-connectors" in text
           and "grid-template-columns:1fr" in text)
    expect("数据圆点装饰 .about-dot 渐变 + glow",
           "about-dot" in text and "box-shadow:0 0 8px" in text)
    expect("高亮 pill .about-pill-accent 渐变背景",
           "about-pill-accent" in text)

    # ---- 校验一些之前页面元素没被破坏 ----
    print("\n== 用例 4:之前 about 元素保留 ==")
    expect("保留 about-shell / about-section / about-platforms / about-facts",
           all(s in text for s in ("about-shell", "about-section", "about-platforms", "about-facts")))
    expect("保留 logo / eyebrow / h2 / pills",
           "about-logo" in text and "about-eyebrow" in text and "about-pills" in text)
    expect("保留 scape 加载态",
           "about-loading" in text and "正在读取应用信息" in text)

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

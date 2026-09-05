"""UI 第十轮回归：报告筛选、操作菜单箭头与启动动画。"""
from pathlib import Path


def expect(name: str, condition: bool) -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        raise SystemExit(f"断言失败: {name}")


def main() -> None:
    html = Path("agent/static/index.html").read_text(encoding="utf-8")
    css = Path("agent/static/assets/ui-v2.css").read_text(encoding="utf-8")
    js = Path("agent/static/assets/ui-v2.js").read_text(encoding="utf-8")

    print("== 用例 1：报告排序框文字不会被箭头遮挡 ==")
    expect("报告排序框仍包含三种排序方式",
           all(label in html for label in ("最新优先", "最早优先", "名称排序")))
    expect("报告工具栏使用弹性输入框 + 固定排序框",
           "grid-template-columns: minmax(0, 1fr) 112px" in css)
    expect("排序框为箭头预留稳定宽度",
           ".report-toolbar #reportSort" in css
           and css.count("width: 112px") >= 3)

    print("\n== 用例 2：对比研究首区保留卡片顶部留白 ==")
    expect("首个对比区覆盖旧版零顶部间距",
           ".compare-form .compare-section:first-of-type { padding-top: 26px;" in css)
    expect("窄屏同步使用紧凑但可见的顶部间距",
           ".compare-form .compare-section:first-of-type { padding-top: 22px;" in css
           and ".compare-form .compare-section:first-of-type { padding-top: 20px;" in css)

    print("\n== 用例 3：报告与记忆菜单使用居中矢量箭头 ==")
    contract = css.split("/* Font glyph chevrons sit low", 1)[-1]
    expect("更多/管理与导出共用同一箭头契约",
           ".workspace-menu > summary::after" in contract
           and ".report-export-menu > summary::after" in contract)
    expect("不再以字体字符作为下拉箭头",
           'content: ""' in contract and "mask: url(" in contract)
    expect("导出箭头按按钮高度绝对居中",
           "top: 50%" in contract and "translateY(-50%)" in contract)
    expect("菜单打开后箭头原位旋转",
           ".workspace-menu[open] > summary::after" in contract
           and ".report-export-menu[open] > summary::after" in contract)
    expect("弹出菜单位于内容层之上", "z-index: 160" in contract)

    print("\n== 用例 4：共享 Web/Electron 启动动画完整 ==")
    expect("页面首屏以 busy 状态启动",
           '<body class="booting" aria-busy="true">' in html)
    expect("启动层包含品牌、状态与进度元素",
           all(token in html for token in (
               'id="appBoot"', "app-boot-mark", "正在准备研究工作台",
               "app-boot-progress")))
    expect("启动层位于应用内容之前",
           html.index('id="appBoot"') < html.index('class="skip-link"'))
    expect("启动动画同时适配浅色和减少动态效果偏好",
           'html[data-theme="light"] .app-boot' in css
           and "@media (prefers-reduced-motion: reduce)" in contract)
    expect("初始化完成会清理 busy 状态和启动层",
           "dismissBootScreen" in js
           and 'body.classList.remove("booting")' in js
           and 'body.setAttribute("aria-busy", "false")' in js
           and 'bootScreen.remove()' in js)
    expect("异常时有 4.5 秒自动降级，不会永久遮挡页面",
           "__paperStudioBootFallback" in html and "4500" in html)
    expect("启动完成向 Web/App 共用页面发送 ready 事件",
           'CustomEvent("paperstudio:ready")' in js)

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

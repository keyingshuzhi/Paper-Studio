"""Web 浅色主题对比度与组件覆盖回归测试。"""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "agent" / "static" / "index.html").read_text(
    encoding="utf-8")


def expect(name: str, condition: bool) -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        raise SystemExit(f"断言失败: {name}")


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def _luminance(hex_color: str) -> float:
    channels = []
    for channel in _rgb(hex_color):
        normalized = channel / 255
        channels.append(normalized / 12.92 if normalized <= .04045
                        else ((normalized + .055) / 1.055) ** 2.4)
    return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2]


def contrast(foreground: str, background: str = "#ffffff") -> float:
    light, dark = sorted((_luminance(foreground), _luminance(background)),
                         reverse=True)
    return (light + .05) / (dark + .05)


def main() -> None:
    marker = "Final light-theme contract"
    start = HTML.rfind(marker)
    end = HTML.find("</style>", start)
    final_css = HTML[start:end]
    expect("最终浅色契约位于所有组件样式之后",
           start > 0 and end > start)

    variables = dict(re.findall(r"(--[\w-]+):(#(?:[0-9a-fA-F]{6}))", final_css))
    for name in ("--text", "--text2", "--text3"):
        expect(f"{name} 在白色背景达到 WCAG AA",
               contrast(variables[name]) >= 4.5)
    for name in ("--blue", "--green", "--red", "--amber", "--violet"):
        expect(f"{name} 状态文字在白色背景可读",
               contrast(variables[name]) >= 4.5)

    required = (
        '#providerBadge.ok', '.btn-ghost:hover', '.agent-kicker',
        '.theme-choice button.on', '.report-item.on',
        '.report-item.on::before', '.job-error', '.warn',
        '.memory-create', '.permission-chip.risk', '.mcp-tags .bad',
        '.schema-box pre', '.mcp-host-config', '.about-eyebrow',
    )
    expect("新增工作区和交互状态均有浅色覆盖",
           all(f'html[data-theme="light"] {selector}' in final_css
               for selector in required))
    expect("成本页面和运行脚本已从新版工作台移除",
           'data-p="cost"' not in HTML and 'id="p-cost"' not in HTML and
           '/api/cost' not in HTML and 'drawCostChart' not in HTML)
    expect("多服务商交互具备浅色主题覆盖",
           all(f'html[data-theme="light"] {selector}' in final_css
               for selector in (
                   '.active-provider-row', '.provider-profile',
                   '.provider-profile.selected', '.provider-avatar',
                   '.provider-status.ready', '.provider-empty')))
    print("\n浅色主题用例全部通过 ✅")


if __name__ == "__main__":
    main()

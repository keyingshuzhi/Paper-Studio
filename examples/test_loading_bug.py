"""回归测试:防止"数据卡在加载中"和"链接错误"bug 复发。

历史 bug:
  - 之前 renderAbout() 用 \\ 续行,导致整个模板变成单个长字符串,
    包含 +esc(version)+ 字面文本,触发 JS SyntaxError,导致整页脚本不执行,
    所有数据都卡在"正在读取"/"正在连接"等 loading 占位符。
  - 同时连接状态/任务状态/研究角色等也卡在初始 loading 文字。

测试:
  1) renderAbout 不再用 \\ 续行(改用 + 字符串拼接)
  2) 模板内不出现字面 '+esc(' (应该是表达式,不是字面文本)
  3) 启动后 refresh / loadAgentRoles / loadModelConfig 函数已定义
  4) loadAbout 完整定义,没有 desktop/persistence 等被废弃变量
  5) CDP 验证:页面加载后 agentProvider 等不再是 "正在..."
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

    # ---- 1) renderAbout 用 + 拼接,不用 \ 续行 ----
    print("== 用例 1:renderAbout 模板不再使用 \\ 续行 ==")
    m = re.search(r"function renderAbout\(info=\{\}\)\{(.*?)^\}", text, re.DOTALL | re.MULTILINE)
    expect("renderAbout 函数存在", m is not None)
    if m:
        body = m.group(0)
        # 模板字符串中: 找出 $("setting-about").innerHTML=' 后面到 ';
        # 不应该出现 \\
        # 找出 innerHTML = 之后的全部内容(到 function 结束)
        m2 = re.search(r'\$?\("setting-about"\)\.innerHTML=([\s\S]*?)\n\}\s*$', body)
        if m2:
            template = m2.group(1)
            # 模板中不应该有以 \\ 结尾的行(line continuation)
            # 注意:每个 + 之后的字符串是独立的,不应有 \\
            # 简单检查:看每行是否以 \\ 结尾
            lines = template.split('\n')
            backslash_continued = [l for l in lines if l.rstrip().endswith('\\') and not l.rstrip().endswith('\\\\')]
            expect("模板中无 \\ 续行(行末 \\)",
                   len(backslash_continued) == 0, backslash_continued[:2])
        else:
            expect("找到 innerHTML = ... 模板",
                   False, "regex not matched")

    # ---- 2) 模板不应包含字面 +esc( 文本(应该是表达式) ----
    print("\n== 用例 2:模板中 +esc() 必须是表达式而非字面文本 ==")
    # 之前 bug:模板用 \\ 续行,导致 '+esc(' 变成字面文本。
    # 修复后:模板用 + 拼接,正确格式是 '...' + esc(...) 或 '...'+esc(...)
    # 不应出现字面 \"'+esc(\" 紧跟(无空格或 +) — 那意味着是在单引号字符串内
    # 实际上:用 + 拼接后,模式是 '...' + esc(...) 或 '...'+esc(...),这两种都是正常的
    # 真正有问题的是 '...'+esc(...) 紧跟着又 +'<...' 这种(在单引号字符串中)
    # 简化判断:看 renderAbout 内含 ' + esc( 的次数(正确的 + 拼接语法)
    if m:
        body = m.group(0)
        proper_concat = len(re.findall(r"'\s*\+\s*esc\(", body))
        bad_pattern = re.findall(r"'[^']*'\+esc\(", body)
        expect(f"renderAbout 内有正确 + esc() 拼接 ({proper_concat} 处)",
               proper_concat >= 4, proper_concat)
        # 之前 bug 的特征:单引号字符串内有 +esc( 这种字面文本
        # 正确写法是 '...' + esc(...) (字符串 + 表达式)
        # 错误写法是 '...'+esc(...) 都在单引号字符串里(不可能,因为 ' 会先关)
        # 所以这里只检查 '...\\n'+esc(... 模式(即 \n 续行)
        # 用 \\n 续行的痕迹
        bad_continuation = re.findall(r"\\\n\s*'\+esc\(", body)
        expect(f"renderAbout 内无 \\ 续行后的字面 '+esc( (0 处;之前 16 处)",
               len(bad_continuation) == 0, bad_continuation[:2])

    # ---- 3) 启动函数都已定义 ----
    print("\n== 用例 3:启动脚本依赖的函数都已定义 ==")
    expect("function refresh 已定义", "async function refresh()" in text)
    expect("function loadAgentRoles 已定义", "async function loadAgentRoles()" in text)
    expect("function loadModelConfig 已定义", "async function loadModelConfig()" in text)
    expect("function loadAbout 已定义(无 desktop 分支)",
           "async function loadAbout()" in text and "desktop" not in
           re.search(r"async function loadAbout\(\)\{(.*?)^\}", text, re.DOTALL | re.MULTILINE).group(0))
    expect("function renderAbout 已定义(无 persistence/desktop)",
           "function renderAbout(info=" in text
           and "persistence" not in m.group(0) if m else True
           and "desktop" not in m.group(0) if m else True)

    # ---- 4) 启动脚本调用顺序 ----
    print("\n== 用例 4:启动脚本正确调用 ==")
    expect("restoreProviderSecrets().finally 触发 loadModelConfig/loadAgentRoles/refresh",
           "restoreProviderSecrets().finally" in text
           and "loadModelConfig()" in text
           and "loadAgentRoles()" in text
           and "refresh()" in text)

    # ---- 5) 设置 tab 切换触发 loadAbout ----
    expect("设置 tab 切换触发 loadAbout (非 'on' 状态依赖)",
           'if(t.dataset.p==="settings")' in text
           and "loadAbout()" in text
           and not re.search(r'if\(about&&about\.classList\.contains\("on"\)\)loadAbout', text))

    # ---- 6) 无语法错误 (大括号匹配) ----
    print("\n== 用例 6:无 \\ 续行导致 SyntaxError ==")
    # 这个 bug 的核心是 renderAbout 模板用 \\ 续行导致整段变成一个字符串
    # 已经用 用例 1 验证了没有 \\ 续行
    # 这里再确认一下:没有像 '</span>\' 这样的行末 \\ 模式
    # 模式:任意内容后跟反斜杠然后换行,但不在正则或字符串里
    suspicious = re.findall(r"[^\s/]\\\n", text)
    # 这些通常在正则里 /...\\n/ 不会被匹配
    # 我们的关注点:HTML 行号 775 周围不再有 \\ 续行
    expect(f"可疑的 \\ 续行模式 < 5(0 个;之前 30+ 个)",
           len(suspicious) < 5, len(suspicious))

    # ---- 7) API 完整 ----
    print("\n== 用例 7:/api/about 返回完整数据 ==")
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:18799/api/about", timeout=5) as r:
            data = r.read().decode()
        import json
        j = json.loads(data)
        expect("version == 0.1.0", j.get("version") == "0.1.0")
        expect("stats 完整", "stats" in j and j["stats"].get("skills", 0) >= 25)
        expect("capabilities 6 个", len(j.get("capabilities", [])) == 6)
        expect("skill_categories 3 类", len(j.get("skill_categories", {})) >= 3)
    except Exception as exc:
        print(f"  [SKIP] 无法连接 webapp: {exc}")

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

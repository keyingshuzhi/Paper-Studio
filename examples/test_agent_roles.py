"""多 Agent 角色 + 研究模板的端到端测试（不依赖网络 / LLM）。"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core.agent_roles import (
    build_role_summary, list_roles, resolve_skills_for_role, role_by_id,
)
from agent.skills import (
    BaseSkill, LibraryRagSkill, Paper, ResearchTemplateCompareSkill,
    ResearchTemplateCompetitorSkill, ResearchTemplateDailySkill,
    ResearchTemplateOpeningSkill, ResearchTemplateSurveySkill, SkillError,
    SkillPermission, SkillResult,
)


def expect(name, cond, got=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + ("" if cond else f" -> {got}"))
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def invoke(skill, **kwargs):
    allowed = {SkillPermission.FILESYSTEM_READ,
               SkillPermission.FILESYSTEM_WRITE,
               SkillPermission.DESTRUCTIVE,
               SkillPermission.NETWORK}
    return skill.invoke(
        allowed_permissions=allowed,
        progress_callback=lambda e: None,
        **kwargs)


# ============================== 角色部分 ===================================


def test_roles() -> None:
    print("== 用例 1：4 个角色定义完整 ==")
    roles = list_roles()
    expect("返回 4 个角色", len(roles) == 4, len(roles))
    ids = {r.role_id for r in roles}
    expect("包含 4 个预期 id", ids == {"retriever", "reader",
                                       "citation_checker", "editor"}, ids)
    for role in roles:
        expect(f"角色 {role.role_id} 有 name/icon/summary",
               bool(role.name) and bool(role.icon) and bool(role.summary))

    print("== 用例 2：每个角色能解析为 Skill 实例 ==")
    for role in roles:
        skills, missing = resolve_skills_for_role(role)
        expect(f"角色 {role.role_id} 全部 Skill 解析成功",
               not missing, missing)
        expect(f"角色 {role.role_id} 至少 2 个 Skill",
               len(skills) >= 2, len(skills))
        for skill in skills:
            expect(f"  {role.role_id}.{skill.name} 是 BaseSkill 子类",
                   isinstance(skill, BaseSkill))

    print("== 用例 3：role_by_id 与 build_role_summary ==")
    editor = role_by_id("editor")
    expect("role_by_id('editor') 命中", editor is not None
           and editor.name == "综述编辑")
    expect("role_by_id('不存在') 返回 None",
           role_by_id("不存在") is None)
    summary = build_role_summary(editor)
    expect("build_role_summary 含 resolved_skills 数组",
           isinstance(summary.get("resolved_skills"), list)
           and len(summary["resolved_skills"]) >= 2)
    expect("build_role_summary 含 primary_skills",
           isinstance(summary.get("primary_skills"), list)
           and len(summary["primary_skills"]) >= 1)
    expect("build_role_summary 含 ready 标志",
           summary.get("ready") is True)

    print("== 用例 4：manifests 包含 4 角色相关 Skill ==")
    ms = BaseSkill.manifests()
    primary = {p for r in roles for p in r.primary_skills}
    for name in sorted(primary):
        expect(f"primary skill {name} 注册到 BaseSkill",
               name in ms, name)


# ============================== 模板部分 ===================================


def test_templates_input_validation() -> None:
    print("== 用例 5：5 个模板 Skill 全部注册 ==")
    ms = BaseSkill.manifests()
    expected = {
        "research_template_survey", "research_template_compare",
        "research_template_opening", "research_template_competitor",
        "research_template_daily",
    }
    found = {n for n in ms if n in expected}
    expect("5 个模板都注册", found == expected, found - expected)

    print("== 用例 6：模板 input_schema 校验 ==")
    for cls in (ResearchTemplateSurveySkill, ResearchTemplateCompareSkill,
                ResearchTemplateOpeningSkill, ResearchTemplateCompetitorSkill,
                ResearchTemplateDailySkill):
        m = cls.manifest()
        expect(f"{cls.name} 有非空 input_schema",
               isinstance(m["input_schema"], dict)
               and m["input_schema"].get("type") == "object")
        expect(f"{cls.name} 有 tags",
               isinstance(m.get("tags"), list)
               and len(m["tags"]) >= 2)
        expect(f"{cls.name} 有 examples",
               isinstance(m.get("examples"), list)
               and len(m["examples"]) >= 1)

    print("== 用例 7：模板可被 invoke,缺参数应被拒 ==")
    from agent.skills import SkillError, SkillResult
    for cls in (ResearchTemplateSurveySkill, ResearchTemplateCompareSkill,
                ResearchTemplateOpeningSkill, ResearchTemplateCompetitorSkill,
                ResearchTemplateDailySkill):
        skill = cls()
        result = skill.invoke(
            allowed_permissions={SkillPermission.FILESYSTEM_READ},
            progress_callback=lambda e: None)
        # 缺 query/papers 等必填字段,应被 input_schema 校验拒掉
        expect(f"{cls.name} 缺参数时 result.ok=False",
               result.ok is False, result.error)
        if result.error:
            expect(f"{cls.name} 错误信息提及参数校验或缺 query/papers",
                   "query" in (result.error.message or "").lower()
                   or "papers" in (result.error.message or "").lower()
                   or "topics" in (result.error.message or "").lower()
                   or "required" in (result.error.message or "").lower()
                   or "缺少" in (result.error.message or "")
                   or "必填" in (result.error.message or "")
                   or result.error.code in {"contract", "input_schema", "validation"},
                   result.error.message)

    print("== 用例 8：参数不达标时返回清晰 SkillError ==")
    full_perms = {SkillPermission.FILESYSTEM_READ, SkillPermission.FILESYSTEM_WRITE,
                  SkillPermission.NETWORK, SkillPermission.PAID_API}
    s = ResearchTemplateCompareSkill()
    result = s.invoke(
        allowed_permissions=full_perms,
        progress_callback=lambda e: None, topics=["only-one"])
    expect("compare 单主题应被拒", result.ok is False, result.error)
    if result.error:
        # 两种来源都可能:input_schema minItems:2 → 「元素数量不能少于 2」
        # 或者我们 execute 内的手动检查 → 「至少 2 个主题」。接受任一。
        msg = (result.error.message or "")
        expect("单主题错误信息提及数量下限",
               ("至少" in msg) or ("元素数量" in msg) or ("minItems" in msg)
               or ("少" in msg), msg)

    s = ResearchTemplateCompetitorSkill()
    result = s.invoke(
        allowed_permissions=full_perms,
        progress_callback=lambda e: None,
        papers=[{"title": "x", "url": "u", "source": "s",
                 "authors": [], "year": 2024}])
    expect("competitor 单论文应被拒", result.ok is False, result.error)
    if result.error:
        msg = (result.error.message or "")
        expect("单论文错误信息提及数量下限",
               ("至少" in msg) or ("元素数量" in msg) or ("minItems" in msg)
               or ("少" in msg), msg)


def test_templates_dry_run() -> None:
    """模拟模板入口:用 monkey-patch 替换 ResearchAgent,验证调用链。"""
    print("== 用例 9：模板 dry-run(用 stub agent) ==")
    from agent.skills import research_template_skill as rt_module

    captured: Dict[str, Any] = {}

    @dataclass
    class StubPlan:
        query: str = ""
        original_query: str = ""
        max_results: int = 5
        sources: Any = None
        download: bool = False
        max_downloads: Any = None
        report: bool = True
        year_from: Any = None
        extra: Dict[str, Any] = field(default_factory=dict)

    class StubAgent:
        def run(self, user_input, **kwargs):
            captured["user_input"] = user_input
            captured.update(kwargs)
            return {
                "plan": StubPlan(
                    query=user_input, original_query=user_input,
                    max_results=kwargs.get("max_results", 5),
                    sources=kwargs.get("sources"),
                    download=kwargs.get("download", False),
                    max_downloads=kwargs.get("max_downloads"),
                    report=kwargs.get("report", True),
                    year_from=kwargs.get("year_from"),
                ),
                "papers": [],
                "acquisition": None,
                "summaries": [],
                "analysis": None,
                "report_path": "/tmp/stub-report.md",
            }

    rt_module._build_agent = lambda: StubAgent()  # type: ignore

    s = ResearchTemplateSurveySkill()
    result = s.execute(query="long-context LLM", max_results=8, year_from=2024)
    expect("survey 模板走 stub agent", captured.get("user_input") == "long-context LLM")
    expect("survey 模板透传 max_results",
           captured.get("max_results") == 8, captured.get("max_results"))
    expect("survey 模板默认 summarize=True",
           captured.get("summarize") is True, captured.get("summarize"))
    expect("survey 模板默认 analyze=True",
           captured.get("analyze") is True, captured.get("analyze"))
    expect("survey 模板返回 template=survey",
           result.get("template") == "survey", result)
    expect("survey 模板返回 report_path",
           result.get("report_path") == "/tmp/stub-report.md", result)


def main() -> None:
    test_roles()
    test_templates_input_validation()
    test_templates_dry_run()
    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

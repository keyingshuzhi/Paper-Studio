"""多 Agent 角色（v0.2.0）。

设计目标：把现有 16 项 Skill 重新组织为四个可解释的"研究角色"，
让用户在 UI 上看到「谁负责做什么」。本模块只做 *薄包装*，不引入新
的调度循环——每个角色绑定的 Skill 仍由 ``ResearchAgent.run`` 实际
调用；本模块提供：

* :class:`AgentRole` 数据类：一个角色的元信息（id、名称、说明、icon、
  关联 Skill 列表、典型任务样例）；
* :func:`list_roles` / :func:`role_by_id`：发现接口；
* :func:`build_role_summary`：给 UI 用的扁平结构；
* :func:`resolve_skills_for_role`：把 ``AgentRole.skill_names`` 解析为
  ``BaseSkill`` 实例，并在缺依赖时给出 reason。

不在本模块做的事：

* 不实现 plan / act / observe 三段循环——这是后续大版本工作流编排的
  议题；本期只是把"角色"和"Skill"的概念绑好；
* 不修改现有 Skill 行为或 schema——``paper_summarize`` 等仍是
  ``BaseSkill`` 子类，独立于角色概念存在。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..skills import BaseSkill


@dataclass(frozen=True)
class AgentRole:
    """一个研究角色的元信息。"""

    role_id: str
    name: str
    icon: str  # 单字符或短 emoji,UI 展示用
    summary: str
    skill_names: Tuple[str, ...]  # 绑定的 Skill 名称
    primary_skills: Tuple[str, ...] = ()  # 角色"主打"的 Skill,UI 突出显示
    sample_tasks: Tuple[str, ...] = ()
    # 角色对模型的要求；用于自动选服务商时作为参考权重
    preferred_capability: str = "通用推理"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role_id": self.role_id,
            "name": self.name,
            "icon": self.icon,
            "summary": self.summary,
            "skill_names": list(self.skill_names),
            "primary_skills": list(self.primary_skills),
            "sample_tasks": list(self.sample_tasks),
            "preferred_capability": self.preferred_capability,
        }


# ============================== 角色定义 ===================================

# 检索员:负责"找到对的文献"。绑定搜索 + 下载 + 库内 RAG。
ROLE_RETRIEVER = AgentRole(
    role_id="retriever",
    name="检索员",
    icon="🔍",
    summary=("按查询与方向找到相关文献,下载原文并接入本地库;"
             "为阅读员提供可读材料。"),
    skill_names=("arxiv_search", "scholar_search", "downloader",
                 "library_rag", "memory_search"),
    primary_skills=("arxiv_search", "downloader", "library_rag"),
    sample_tasks=(
        "检索 2024 年关于 long-context LLM 的近半年新论文",
        "在本机文献库查找 Mamba 相关的原文片段",
        "补充 arxiv 上与 RAG 评估相关的最新文献",
    ),
    preferred_capability="通用推理 + 检索排序",
)


# 阅读员:负责"把论文读懂"。绑定摘要 + 单篇分析。
ROLE_READER = AgentRole(
    role_id="reader",
    name="阅读员",
    icon="📖",
    summary=("把每篇论文压成结构化画像(问题/方法/贡献/局限/关键词),"
             "让后续角色不必再读全文。"),
    skill_names=("paper_summarize", "paper_summarize_batch"),
    primary_skills=("paper_summarize", "paper_summarize_batch"),
    sample_tasks=(
        "把这 12 篇论文每篇压成 5 个字段的画像",
        "为下一篇 RAG 综述生成结构化摘要",
    ),
    preferred_capability="强摘要/抽取(长上下文 + JSON 模式)",
)


# 引用核验员:负责"确保引用正确、关系可信"。绑定引用抓取 + 引用分析。
ROLE_CITATION_CHECKER = AgentRole(
    role_id="citation_checker",
    name="引用核验员",
    icon="🔗",
    summary=("抓取引用网络、核对作者-论文-发表场所对应关系,"
             "标记潜在不可核验或冲突的来源。"),
    skill_names=("citation_scraper", "citation_analyze",
                 "citation", "memory_write"),
    primary_skills=("citation_scraper", "citation_analyze"),
    sample_tasks=(
        "核对这 8 篇论文的核心引用是否真实可达",
        "对候选综述构建引用网络并找出关键枢纽文献",
    ),
    preferred_capability="通用推理 + 严格 JSON 输出",
)


# 综述编辑:负责"把研究输出成可读报告"。绑定对比 + 报告 + 记忆沉淀。
ROLE_EDITOR = AgentRole(
    role_id="editor",
    name="综述编辑",
    icon="🧩",
    summary=("把上述角色产出的证据与结论组织为单篇/对比/深度报告,"
             "并把可复用的结论回填到研究记忆。"),
    skill_names=("paper_compare", "report_render", "report_write",
                 "memory_write"),
    primary_skills=("report_render", "report_write"),
    sample_tasks=(
        "把这次研究的共识/分歧/盲点写成长报告",
        "生成可发布的 Mamba vs Transformer 综述",
        "把可复用的结论沉淀进研究记忆",
    ),
    preferred_capability="长文写作 + 中文表达",
)


_ROLES: Tuple[AgentRole, ...] = (
    ROLE_RETRIEVER, ROLE_READER, ROLE_CITATION_CHECKER, ROLE_EDITOR,
)


# ============================== 发现接口 ===================================


def list_roles() -> List[AgentRole]:
    """返回所有内置角色(供 UI 列表展示)。"""
    return list(_ROLES)


def role_by_id(role_id: str) -> Optional[AgentRole]:
    """按 id 取一个角色;找不到返回 ``None``。"""
    for role in _ROLES:
        if role.role_id == role_id:
            return role
    return None


# ============================== 解析为 Skill 实例 ==========================


def resolve_skills_for_role(role: AgentRole) -> Tuple[List[BaseSkill], List[str]]:
    """把 ``role.skill_names`` 解析为 ``BaseSkill`` 实例列表。

    返回 ``(instances, missing_reasons)``:

    * ``instances``: 成功取到的实例(共用 ``BaseSkill.get`` 的缓存)
    * ``missing_reasons``: 缺失/异常原因,UI 用来在角色卡上标"部分可用"

    注意:如果 ``BaseSkill.registry`` 还没有目标 Skill,会抛 ``KeyError``,
    我们转写为 reason,而不是让 UI 崩。
    """
    instances: List[BaseSkill] = []
    missing: List[str] = []
    for name in role.skill_names:
        if not BaseSkill.has(name):
            missing.append(f"未注册 Skill: {name}")
            continue
        try:
            instances.append(BaseSkill.get(name))
        except Exception as err:  # noqa: BLE001
            missing.append(f"Skill {name} 初始化失败: {err}")
    return instances, missing


def build_role_summary(role: AgentRole) -> Dict[str, Any]:
    """给 UI 用的扁平结构,带 ``resolved_skills`` 与 ``missing``。"""
    skills, missing = resolve_skills_for_role(role)
    resolved: List[Dict[str, Any]] = []
    for skill in skills:
        resolved.append({
            "name": skill.name,
            "description": skill.description,
            "version": skill.version,
            "permissions": sorted(p.value for p in skill.permissions),
            "tags": list(skill.tags),
            "enabled": bool(skill.enabled),
        })
    return {
        **role.to_dict(),
        "resolved_skills": resolved,
        "missing": missing,
        "ready": not missing,
    }

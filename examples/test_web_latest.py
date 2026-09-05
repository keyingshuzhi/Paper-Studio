"""v0.1.0 Web、多服务商配置与核心能力接入回归测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.webapp import APP_VERSION, ResearchWebApp


def expect(name, condition):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        raise SystemExit(f"断言失败: {name}")


def request(base: str, path: str, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = Request(
        base + path, data=data,
        method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=8) as response:
            return response.status, json.loads(
                response.read().decode("utf-8"))
    except HTTPError as err:
        return err.code, json.loads(err.read().decode("utf-8"))


def wait_calls(calls, count: int) -> None:
    for _ in range(100):
        if len(calls) >= count:
            return
        time.sleep(.02)
    raise RuntimeError("Web 任务没有进入执行器")


def main() -> None:
    root = Path(tempfile.mkdtemp())
    data_dir = root / "downloads"
    data_dir.mkdir()
    previous_cwd = Path.cwd()
    previous_data = os.environ.get("PAPER_STUDIO_DATA_DIR")
    calls = []

    def runner(query, **options):
        calls.append({"query": query, **options})
        return {"report_path": None}

    os.chdir(root)
    os.environ["PAPER_STUDIO_DATA_DIR"] = str(data_dir)
    app = ResearchWebApp(runner=runner)
    server = app._make_server(port=0)  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(base + "/", timeout=5) as response:
            html = response.read().decode("utf-8")
        expect("Web 包含多主题、Skill、高级参数和手动记忆入口",
               all(marker in html for marker in (
                   'id="p-compare"', 'id="p-skills"', 'id="researchSource"',
                   'id="analyzeCitations"', 'id="newMemory"',
                   'id="mcpServerStatus"')))
        expect("新版品牌与多服务商设置已进入 Web",
               all(marker in html for marker in (
                   '/assets/paper-studio-logo.png', 'id="providerGrid"',
                   'id="providerEditor"', 'id="newProvider"',
                   '/api/provider-test', '真实测试（不保存）',
                   'id="modelConfigView"', '/api/model-config')))
        expect("服务商卡片单击选中、双击打开编辑",
               'title="单击选中为默认 · 双击编辑"' in html and
               'providerCardClick(' in html and
               'providerCardDblClick(' in html and 'ondblclick=' in html and
               'providerCardClickTimer' in html and
               'setTimeout(()=>{providerCardClickTimer=null;selectProviderCard(id)},320)' in html and
               'clearTimeout(providerCardClickTimer);providerCardClickTimer=null;editProvider(id)' in html)
        expect("成本页面及其前端调用已移除",
               'data-p="cost"' not in html and 'id="p-cost"' not in html and
               '/api/cost' not in html)

        with urlopen(base + "/assets/paper-studio-logo.png", timeout=5) as response:
            logo = response.read()
        expect("品牌 Logo 可由 Web 后端正确提供",
               response.status == 200 and logo[:8] == b"\x89PNG\r\n\x1a\n")

        status, about = request(base, "/api/about")
        expect("Web 版本统一为 0.1.0",
               status == 200 and about["version"] == APP_VERSION == "0.1.0")

        status, settings = request(base, "/api/settings")
        builtins = {item["id"] for item in settings["provider_profiles"]}
        expect("内置四家模型服务商",
               status == 200 and
               {"ollama", "deepseek", "openai", "openrouter"}.issubset(builtins))
        custom = {
            "id": "institution", "name": "Institution Gateway",
            "kind": "openai", "base_url": "https://llm.example.edu/v1",
            "models": ["research-model", "review-model"],
            "default_model": "research-model", "requires_api_key": True,
            "api_key_env": "INSTITUTION_LLM_KEY", "builtin": False,
        }
        status, settings = request(base, "/api/settings", {
            "provider": "institution", "model": "research-model",
            "provider_profiles": settings["provider_profiles"] + [custom],
            "api_keys": {"institution": "test-secret-never-persist"},
            "credential_storages": {"institution": "electron_safe_storage"},
            "llm_timeout": 240,
        })
        saved_custom = next(item for item in settings["provider_profiles"]
                            if item["id"] == "institution")
        expect("自定义服务商、模型和超时可保存",
               status == 200 and settings["provider"] == "institution" and
               settings["model"] == "research-model" and
               settings["llm_timeout"] == 240 and
               saved_custom["has_api_key"] is True and
               saved_custom["api_key_source"] == "electron_safe_storage")
        expect("API Key 不回传也不写入普通设置文件",
               "test-secret-never-persist" not in json.dumps(settings) and
               "test-secret-never-persist" not in
               app.settings_path.read_text(encoding="utf-8"))
        status, model_config = request(base, "/api/model-config")
        expect("模型配置文件可查看且不包含 Key 明文",
               status == 200 and model_config["file_name"] == "model_config.json" and
               app.settings_path.name == "model_config.json" and
               app.settings_path.exists() and
               "test-secret-never-persist" not in model_config["content"] and
               any(item["provider_id"] == "institution" and
                   item["storage"] == "系统安全存储（Electron safeStorage）"
                   for item in model_config["credentials"]))
        status, provider = request(base, "/api/provider?id=institution")
        expect("自定义 OpenAI 兼容服务真实接入运行层",
               status == 200 and provider["available"] is True and
               provider["provider"] == "institution" and
               provider["model"] == "research-model")
        status, checked = request(
            base, "/api/provider?id=institution&model=review-model")
        expect("连接检查回显当前请求的模型，不使用上一个默认模型",
               status == 200 and checked["profile"] == "institution" and
               checked["model"] == checked["checked_model"] == "review-model")

        # 新配置不需要先保存：测试请求应使用本次输入的 Key、地址和模型，
        # 同时绝不能把临时 Key 返回给浏览器或写进普通设置。
        import agent.core.llm as llm_mod
        original_post = llm_mod.requests.post

        class ProbeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "OK"}}]}

        probe_calls = []
        try:
            llm_mod.requests.post = lambda *args, **kwargs: (
                probe_calls.append((args, kwargs)) or ProbeResponse())
            transient_key = "transient-key-never-persist"
            status, verified = request(base, "/api/provider-test", {
                "profile": custom, "model": "review-model",
                "api_key": transient_key,
            })
            expect("草稿配置可执行真实模型验证",
                   status == 200 and verified["verified"] is True and
                   verified["checked_model"] == "review-model" and
                   verified["test_mode"] == "live_inference" and
                   verified["stages"][-1]["id"] == "model")
            expect("真实验证使用输入的 Key 且不泄露或落盘",
                   probe_calls[0][1]["headers"]["Authorization"] ==
                   "Bearer " + transient_key and
                   transient_key not in json.dumps(verified) and
                   transient_key not in app.settings_path.read_text(encoding="utf-8"))
        finally:
            llm_mod.requests.post = original_post

        # 模拟顶部「真实验证默认模型」按钮在编辑器里有临时 Key 时的行为：
        # settings 里没有该服务商的 Key,verify 走 /api/provider-test 并把
        # 临时 Key 一并提交,真实请求应使用该 Key 命中 openrouter.ai。
        or_profile = {
            "id": "openrouter", "name": "OpenRouter", "kind": "openai",
            "base_url": "https://openrouter.ai/api/v1",
            "models": ["z-ai/glm-5.2:free"],
            "default_model": "z-ai/glm-5.2:free",
            "requires_api_key": True, "api_key_env": "OPENROUTER_API_KEY",
            "builtin": True, "accent": "amber",
        }
        # 清空 process env,确保 verify 不会从 .env 读到一个偶然存在的 key
        os.environ.pop("OPENROUTER_API_KEY", None)
        # 只重置 openrouter profile 的 key(保留其它自定义 profile,例如前面
        # 注入的 institution,以免后面的 /api/run 高级研究测试找不到该 profile)
        app.settings.setdefault("provider_api_keys", {}).pop("openrouter", None)
        # 确保 settings 的 provider_profiles 里有 openrouter profile
        if not any(p.get("id") == "openrouter"
                   for p in app.settings.get("provider_profiles", [])):
            app.settings.setdefault("provider_profiles", []).append(or_profile)

        class ORProbeResponse:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "OK"}}]}

        or_calls: list = []
        try:
            llm_mod.requests.post = lambda *args, **kwargs: (
                or_calls.append((args, kwargs)) or ORProbeResponse())
            or_temp_key = "sk-or-v1-fake-key-1234567890"
            or_status, or_verified = request(base, "/api/provider-test", {
                "profile": or_profile, "model": "z-ai/glm-5.2:free",
                "api_key": or_temp_key,
            })
            expect("OpenRouter 临时 Key 走 /api/provider-test 验证",
                   or_status == 200 and or_verified.get("verified") is True
                   and or_verified.get("checked_model") == "z-ai/glm-5.2:free")
            expect("OpenRouter 验证请求 URL 命中 openrouter.ai",
                   or_calls and "openrouter.ai/api/v1/chat/completions"
                   in or_calls[0][0][0])
            expect("OpenRouter 验证使用临时 Key 而非 .env",
                   or_calls and or_calls[0][1]["headers"]["Authorization"]
                   == "Bearer " + or_temp_key)
            expect("OpenRouter 临时 Key 不出现在返回 payload",
                   or_temp_key not in json.dumps(or_verified))
        finally:
            llm_mod.requests.post = original_post

        status, catalog = request(base, "/api/skills")
        skill_names = {item["name"] for item in catalog["skills"]}
        expect("Skill 清单完整接入 Web",
               status == 200 and catalog["count"] >= 16 and
               {"paper_summarize", "paper_compare", "memory_write",
                "report_write"}.issubset(skill_names))
        status, invoked = request(base, "/api/skills/invoke", {
            "name": "report_render",
            "arguments": {
                "kind": "single",
                "plan": {"query": "web skill", "original_query": "web skill"},
                "papers": [],
            },
            "timeout_seconds": 30,
        })
        expect("Web Skill 调用返回标准结果和进度",
               status == 200 and invoked["result"]["ok"] is True and
               invoked["progress"])

        status, denied = request(base, "/api/memory-write", {
            "query": "manual memory", "analysis": {"summary": "note"},
        })
        expect("手动记忆未经确认会被拒绝",
               status == 409 and "确认" in denied["error"])
        status, saved = request(base, "/api/memory-write", {
            "query": "manual memory", "papers": [], "summaries": [],
            "analysis": {"summary": "note", "gaps": []}, "confirmed": True,
        })
        expect("确认后可从 Web 写入研究记忆",
               status == 200 and saved["query"] == "manual memory" and
               app.memory.has_query("manual memory"))

        status, server_info = request(base, "/api/mcp-server/info")
        expect("Web 显示 MCP Server 宿主配置和 18 项工具",
               status == 200 and server_info["tool_count"] == 18 and
               server_info["app_version"] == "0.1.0" and
               "mcpServers" in server_info["host_config"])

        # v0.2.0: 多 Agent 角色与研究模板 — 4 角色 + 5 模板应出现在 Web 清单
        status, roles_resp = request(base, "/api/agent-roles")
        role_ids = [r["role_id"] for r in roles_resp.get("roles", [])]
        expect("Agent 角色清单完整,4 角色全部 ready",
               status == 200 and roles_resp.get("count") == 4
               and set(role_ids) == {"retriever", "reader",
                                     "citation_checker", "editor"}
               and all(r["ready"] for r in roles_resp["roles"]))
        for role in roles_resp["roles"]:
            expect(f"角色 {role['role_id']} 至少含 2 个 Skill",
                   len(role.get("skill_names", [])) >= 2)
            expect(f"角色 {role['role_id']} 含主技能 primary_skills",
                   len(role.get("primary_skills", [])) >= 1)

        status, skills_resp = request(base, "/api/skills")
        skills = skills_resp.get("skills", [])
        template_skills = [s["name"] for s in skills if s.get("is_template")]
        expect("研究模板在 /api/skills 中可被分类识别",
               set(template_skills) == {
                   "research_template_survey", "research_template_compare",
                   "research_template_opening", "research_template_competitor",
                   "research_template_daily"})
        for sk in skills:
            expect(f"Skill {sk['name']} 携带 tags/examples/enabled 元数据",
                   isinstance(sk.get("tags"), list)
                   and isinstance(sk.get("examples"), list)
                   and isinstance(sk.get("enabled"), bool)
                   and sk.get("category") in {
                       "template", "memory", "retrieval", "analysis",
                       "report", "general"})

        status, _run = request(base, "/api/run", {
            "q": "advanced web research", "mode": "deep",
            "max_results": 7, "rounds": 3, "branching": 2,
            "max_queries": 5, "provider": "institution",
            "sources": ["arxiv_search"], "year_from": 2024,
            "summarize_limit": 4, "analyze_citations": False,
            "download": False,
        })
        expect("高级研究任务提交成功", status == 200)
        wait_calls(calls, 1)
        advanced = calls[0]
        expect("高级参数真实进入研究执行器",
               advanced["sources"] == ["arxiv_search"] and
               advanced["year_from"] == 2024 and
               advanced["summarize_limit"] == 4 and
               advanced["analyze_citations"] is False and
               advanced["provider"] == "institution")

        # v0.2.0: 模板任务 — /api/run 携带 template 时应走研究模板 Skill 路径
        # 这里 submit_template 内部会做 preview invoke,会真实尝试 LLM/网络,
        # 所以用一个不在 settings 里的 profile + 故意无 LLM 的环境会被拒;
        # 只验证参数校验/调度入口(用不存在的 template 时,返回 400)。
        status, bad = request(base, "/api/run", {
            "q": "test template", "mode": "single",
            "template": "not-a-real-template",
        })
        expect("未注册的 template 应被拒",
               status == 400 and ("模板" in bad.get("error", "")
                                  or "template" in bad.get("error", "").lower()))
        status, schedule = request(base, "/api/schedules", {
            "query": "scheduled research", "mode": "deep",
            "interval_minutes": 60, "max_results": 5,
            "rounds": 2, "branching": 1, "max_queries": 3,
            "sources": ["scholar_search"], "year_from": 2022,
            "summarize_limit": 3, "analyze_citations": False,
            "download": True, "max_downloads": 7, "enabled": False,
        })
        expect("定时计划保留完整研究参数",
               status == 200 and schedule["sources"] == ["scholar_search"] and
               schedule["year_from"] == 2022 and schedule["download"] is True)
        status, _scheduled = request(base, "/api/schedule-run", {
            "id": schedule["id"],
        })
        expect("定时计划可立即进入队列", status == 200)
        wait_calls(calls, 2)
        scheduled = calls[1]
        expect("定时任务将新参数传入执行器",
               scheduled["sources"] == ["scholar_search"] and
               scheduled["year_from"] == 2022 and
               scheduled["summarize_limit"] == 3 and
               scheduled["analyze_citations"] is False and
               scheduled["download"] is True and
               scheduled["max_downloads"] == 7)

        status, _compare = request(base, "/api/compare", {
            "topics": ["Transformer", "Mamba"], "max_results": 6,
            "provider": "ollama", "sources": ["scholar_search"],
            "year_from": 2023, "summarize_limit": 3,
        })
        expect("多主题对比任务提交成功", status == 200)
        wait_calls(calls, 3)
        comparison = calls[2]
        expect("多主题对比进入统一执行器和任务控制",
               comparison["mode"] == "compare" and
               comparison["topics"] == ["Transformer", "Mamba"] and
               comparison["sources"] == ["scholar_search"])
    finally:
        server.shutdown()
        server.server_close()
        app._schedule_stop.set()
        os.chdir(previous_cwd)
        if previous_data is None:
            os.environ.pop("PAPER_STUDIO_DATA_DIR", None)
        else:
            os.environ["PAPER_STUDIO_DATA_DIR"] = previous_data
    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()

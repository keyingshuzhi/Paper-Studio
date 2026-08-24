"""Paper Studio v0.0.4 Web 界面与 Electron 共用后端。

功能：
- 双模式 LLM：Ollama（本地零成本）/ DeepSeek（云端按量）自动切换 + 手动选择
- 成本控制：预算设置、实时成本追踪、超限自动拦截、费用预测、趋势图
- 任务中心：中文阶段、结构化进度、耗时、可搜索日志；研究记忆统计
- 兼容 Electron 桌面封装（window.agent.openPath 打开报告所在文件夹）
- 现代 UI：暗色主题、响应式布局、渐变动画

启动：
    .venv/bin/python -B -m agent.webapp --port 8765
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import shutil
import sys
import threading
import time
import traceback
import unicodedata
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from .core import CostTracker, LLMClient, PaperSummarizer
from .core.memory import ResearchMemory
from .mcp_client import (MCPClientError, MCPClientManager,
                         MCPPermissionBroker, run_async)
from .read_service import resolve_data_dir


APP_VERSION = "0.0.4"
_RESEARCH_SOURCES = frozenset({"arxiv_search", "scholar_search"})
_SKILL_CONFIRM_PERMISSIONS = frozenset({
    "network", "filesystem.write", "paid_api", "external.write", "destructive",
})

# Load static HTML from agent/static/index.html.  Read it per request so an
# already-running development server never keeps serving a stale generated UI.
_STATIC_DIR = Path(__file__).parent / "static"


def _load_index_html() -> Optional[str]:
    try:
        return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
    except OSError:
        return None


def _upgrade_legacy_report_content(content: str) -> str:
    """读取旧报告时补齐摘要五字段，不修改磁盘上的历史原件。

    早期报告可能把本地模型失败项保存为“—”或旧占位文本；报告
    页面读取时利用已保存的问题文本与标题做保守补全，使历史报告也使用
    当前统一结构。新生成的报告不会触发实质变更。
    """
    block_pattern = re.compile(
        r"^###\s+\d+\.\s+.*?(?=^###\s+\d+\.|^##\s+|\Z)", re.M | re.S)
    fields = {
        "问题": "problem", "方法": "method", "贡献": "contribution",
        "局限": "limitation", "关键词": "keywords",
    }

    def upgrade(match: re.Match[str]) -> str:
        block = match.group(0)
        # 只处理论文智能摘要卡片，不碰共识/盲点等其他三级标题。
        if not all(f"- **{label}**：" in block
                   for label in ("问题", "方法", "贡献", "局限")):
            return block
        heading = re.match(r"^###\s+\d+\.\s+(.+)$", block, re.M)
        title = heading.group(1).strip() if heading else "未知标题"
        raw: Dict[str, Any] = {"title": title}
        for label, key in fields.items():
            found = re.search(
                rf"^- \*\*{re.escape(label)}\*\*：(.*)$", block, re.M)
            if found:
                value = found.group(1).strip()
                raw[key] = (re.split(r"[、,，;；]", value)
                            if key == "keywords" else value)
        completed = PaperSummarizer.complete_existing(
            raw, title=title, abstract=str(raw.get("problem") or ""))
        upgraded = block
        for label, key in fields.items():
            value = completed[key]
            display = ("、".join(str(item) for item in value)
                       if key == "keywords" else str(value))
            pattern = rf"^- \*\*{re.escape(label)}\*\*：.*$"
            replacement = f"- **{label}**：{display}"
            if re.search(pattern, upgraded, re.M):
                upgraded = re.sub(pattern, lambda _match: replacement, upgraded,
                                  count=1, flags=re.M)
            elif key == "keywords":
                upgraded = re.sub(
                    r"(^- \*\*局限\*\*：.*$)",
                    lambda found: found.group(1) + "\n\n" + replacement,
                    upgraded, count=1, flags=re.M)
        return upgraded

    upgraded_content = block_pattern.sub(upgrade, content)
    return re.sub(
        r"> ⚠️ (\d+) 篇文献引用获取失败（限流或缺少 ID，不影响主流程）",
        lambda found: (
            f"> ℹ️ 历史报告中有 {found.group(1)} 篇文献未完成引用获取；"
            "旧版未保存具体原因。新版会自动低速重试并按原因分类，"
            "重新执行研究可刷新引用结果。"),
        upgraded_content,
    )

# Legacy inline page fallback (kept for backward compat, not used in V6.0)
_PAGE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>学术研究助理 Agent</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--line:#e4e9f2;--text:#172033;--sub:#64748b;
--blue:#4f46e5;--blue-d:#3730a3;--green:#16a34a;--red:#dc2626;--amber:#b45309;
--violet:#7c3aed;--soft:#eef2ff}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:radial-gradient(circle at 12% -5%,#e0e7ff 0,transparent 28%),var(--bg);color:var(--text)}
header{background:linear-gradient(120deg,#312e81,#4f46e5 58%,#7c3aed);color:#fff;padding:24px max(28px,calc((100vw - 1080px)/2));display:flex;align-items:center;gap:14px;box-shadow:0 8px 24px #312e8140}
header h1{font-size:21px;margin:0;font-weight:700;letter-spacing:.2px}
header .sub{font-size:12px;opacity:.85;margin-top:2px}
#providerBadge{margin-left:auto;font-size:12px;background:rgba(255,255,255,.15);
padding:7px 14px;border-radius:999px;backdrop-filter:blur(8px)}
#providerBadge.off{background:rgba(220,38,38,.75)}
.wrap{max-width:1080px;margin:26px auto;padding:0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px 22px;margin-bottom:16px;box-shadow:0 4px 16px #0f172a0a}
.card h2{font-size:15px;margin:0 0 12px;color:var(--text);display:flex;
align-items:center;gap:8px}
.tabs{display:flex;gap:8px;margin-bottom:18px;overflow:auto;padding-bottom:2px}
.tab{padding:8px 18px;border-radius:8px;border:1px solid var(--line);
background:var(--card);cursor:pointer;font-size:14px;color:var(--sub);white-space:nowrap}
.tab.on{background:var(--blue);color:#fff;border-color:var(--blue);box-shadow:0 3px 8px #4f46e540}
.pane{display:none}.pane.on{display:block}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:8px 0}
input[type=text],input[type=number],select{padding:8px 10px;border:1px solid
var(--line);border-radius:8px;font-size:14px;background:#fff}
#q{flex:1;min-width:240px;font-size:15px}
button{padding:10px 20px;border:0;border-radius:9px;background:var(--blue);color:#fff;font-size:14px;font-weight:600;cursor:pointer;transition:.16s transform,.16s background}
button:hover{background:var(--blue-d)}
button:active{transform:translateY(1px)}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--sub)}
label{font-size:13px;color:var(--sub);display:flex;align-items:center;gap:6px}
label input{width:64px}
.job{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0}
.job .head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.badge{padding:2px 10px;border-radius:999px;font-size:12px;color:#fff}
.b-queued{background:#6b7280}.b-running{background:var(--blue)}
.b-done{background:var(--green)}.b-error,.b-cancelled{background:var(--red)}.b-paused,.b-cancelling{background:var(--amber)}
.job pre{background:#0f172a;color:#e2e8f0;padding:10px;border-radius:8px;
font-size:12.5px;max-height:300px;overflow:auto;white-space:pre-wrap}
.job .meta{font-size:12px;color:var(--sub);margin-top:6px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--sub);font-weight:500}
.cost-big{font-size:26px;font-weight:700}
.cost-big small{font-size:13px;color:var(--sub);font-weight:400}
.warn{background:#fef3c7;border:1px solid #f59e0b;color:#92400e;
padding:10px 14px;border-radius:8px;font-size:13px;margin:8px 0}
.empty{color:var(--sub);font-size:13px;padding:8px 0}
a{color:var(--blue)}
.kpi{display:flex;gap:14px;flex-wrap:wrap}
.kpi div{flex:1;min-width:150px;background:#f8fafc;border:1px solid var(--line);
border-radius:10px;padding:12px}
.kpi b{font-size:20px;display:block}
.kpi span{font-size:12px;color:var(--sub)}
.cost-overview{display:grid;grid-template-columns:minmax(220px,1fr) minmax(280px,1.4fr);gap:18px;align-items:stretch}.cost-total{padding:18px;border-radius:12px;background:linear-gradient(135deg,#eef2ff,#faf5ff);border:1px solid #ddd6fe}.cost-total span,.cost-budget-label{font-size:12px;color:var(--sub)}.cost-total b{display:block;font-size:32px;margin:6px 0}.cost-total small{font-size:12px;color:var(--sub)}.cost-budget{padding:18px;border:1px solid var(--line);border-radius:12px}.budget-line{display:flex;justify-content:space-between;gap:12px;align-items:baseline;margin:6px 0 10px}.budget-line b{font-size:18px}.budget-track{height:10px;border-radius:99px;background:#e9edf5;overflow:hidden}.budget-track i{display:block;height:100%;width:0;background:var(--green);transition:width .2s;background:linear-gradient(90deg,#22c55e,#16a34a)}.budget-track.warn i{background:linear-gradient(90deg,#fbbf24,#d97706)}.budget-track.danger i{background:linear-gradient(90deg,#fb7185,#dc2626)}.cost-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px}.cost-summary div{border:1px solid var(--line);border-radius:10px;padding:11px;background:#fbfcff}.cost-summary span{display:block;font-size:12px;color:var(--sub);margin-bottom:4px}.cost-summary b{font-size:17px}.cost-provider-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin-top:14px}.cost-provider{border:1px solid var(--line);padding:11px 12px;border-radius:10px;background:#fff}.cost-provider b{display:block;font-size:14px}.cost-provider small{color:var(--sub);font-size:12px}.cost-toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:4px 0 12px}.cost-toolbar input,.cost-toolbar select{padding:8px 10px;border:1px solid var(--line);border-radius:8px;font-size:13px}.cost-toolbar input{flex:1;min-width:190px}.cost-records{max-height:400px;overflow:auto;border:1px solid var(--line);border-radius:10px}.cost-records table{min-width:700px}.cost-records td:first-child{white-space:nowrap}.cost-records .local{color:var(--green);font-weight:600}.cost-records .cloud{color:var(--violet);font-weight:600}
.hero{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:16px;padding:20px 22px;border-radius:16px;background:linear-gradient(115deg,#eef2ff,#faf5ff);border:1px solid #ddd6fe}
.hero h2{font-size:19px;margin:0 0 5px}.hero p{margin:0;color:var(--sub);font-size:13px}.step{display:flex;gap:8px;flex-wrap:wrap}.step span{font-size:12px;background:#fff;border:1px solid #ddd6fe;padding:6px 10px;border-radius:99px;color:#5b21b6}.hint{font-size:12px;color:var(--sub);line-height:1.7}.setup-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.setup-grid label{align-items:flex-start;flex-direction:column;gap:5px}.setup-grid input,.setup-grid select{width:100%}.price-note{font-size:12px;color:var(--sub);margin:10px 0 0}.price-note b{color:var(--text)}.status-good{color:var(--green)}.status-bad{color:var(--red)}
.report{background:#fcfcfe;border:1px solid var(--line);border-radius:10px;padding:16px;min-height:380px;max-height:68vh;overflow:auto}.report pre{margin:0;white-space:pre-wrap;font:13px/1.75 ui-monospace,SFMono-Regular,Menlo,monospace;color:#273244}.report-list button{margin:4px 6px 4px 0;padding:7px 10px;font-size:12px}.job-actions{margin-left:auto;display:flex;gap:6px}.schedule-actions{display:flex;gap:6px;margin-top:8px}
@media(max-width:680px){header{padding:20px}.wrap{padding:0 12px}.setup-grid{grid-template-columns:1fr}.hero{align-items:flex-start;flex-direction:column}#providerBadge{margin-left:0}.row{align-items:stretch}#q{min-width:100%}.cost-overview{grid-template-columns:1fr}.cost-summary{grid-template-columns:1fr 1fr}}
</style></head><body>
<header>
  <div><h1>📚 学术研究助理 Agent</h1>
  <div class="sub">多源检索 · 智能摘要 · 跨文献分析 · 深度研究闭环</div></div>
  <div id="providerBadge">检测中…</div>
</header>
<div class="wrap">
 <div class="tabs">
   <div class="tab on" data-p="research">🔍 发起研究</div>
   <div class="tab" data-p="jobs">📋 任务队列</div>
   <div class="tab" data-p="schedules">⏱ 定时任务</div>
   <div class="tab" data-p="reports">📄 报告</div>
   <div class="tab" data-p="cost">💰 成本</div>
   <div class="tab" data-p="memory">🧠 记忆</div>
 </div>

 <!-- 研究 -->
 <div class="pane on" id="p-research">
  <div class="hero">
   <div><h2>从问题出发，得到可追溯的研究报告</h2><p>选择本地免费模型或 DeepSeek 云端模型；每次请求都会受预算保护。</p></div>
   <div class="step"><span>① 配置模型</span><span>② 设置预算</span><span>③ 开始研究</span></div>
  </div>
  <div class="card">
   <h2>⚙️ 模型与费用设置</h2>
   <div class="setup-grid">
    <label>推理方式<select id="setProvider"><option value="auto">自动选择（优先本地 Ollama）</option><option value="ollama">Ollama 本地 · 免费</option><option value="deepseek">DeepSeek 云端 · 按量</option></select></label>
    <label>模型<input id="setModel" list="modelList" value="gemma4:e4b" placeholder="Ollama 模型可读取或手动输入"><datalist id="modelList"><option value="deepseek-v4-flash"><option value="deepseek-v4-pro"></datalist></label>
    <label>DeepSeek API Key（桌面版安全保存）<input id="setKey" type="password" autocomplete="off" placeholder="输入后将使用系统加密存储"></label>
    <label>会话预算（CNY）<input id="setBudget" type="number" step="0.01" min="0" placeholder="建议首次设为 5.00"></label>
    <label>模型请求超时（秒）<input id="setTimeout" type="number" value="90" min="10" max="600" step="5"></label>
    <label>Ollama 服务地址<input id="ollamaUrl" type="text" placeholder="http://localhost:11434"></label>
    <label>DeepSeek API 地址<input id="deepseekUrl" type="text" placeholder="https://api.deepseek.com"></label>
   </div>
   <div class="row"><button type="button" class="ghost" id="refreshModels">读取 Ollama 模型</button><button type="button" class="ghost" id="saveSettings">保存本次设置</button><button type="button" class="ghost" id="clearKey">清除保存的 Key</button><span class="hint" id="settingState">桌面版会使用系统加密存储 Key；浏览器模式仅在当前会话保留。</span></div>
   <div class="price-note"><b>DeepSeek 官方价（人民币 / 每 100 万 tokens）：</b>高峰时段 Flash 缓存命中 ¥0.10／未命中 ¥3.00／输出 ¥9.00；Pro ¥0.30／¥9.00／¥27.00；空闲时段均为一半。</div>
  </div>
  <div class="card">
   <h2>🔍 发起一项研究</h2>
   <form id="f">
    <div class="row"><input type="text" id="q" placeholder="研究主题，如：mamba state space model" required>
     <select id="mode"><option value="deep" selected>深度研究闭环</option>
       <option value="single">单轮研究</option></select>
     <button type="submit">开始研究</button></div>
    <div class="row">
     <label>每来源 ≤ <input type="number" id="mr" value="5" min="1" max="20"></label>
     <label>轮数 <input type="number" id="rd" value="2" min="1" max="5"></label>
     <label>分支 <input type="number" id="br" value="1" min="1" max="3"></label>
     <label>总查询 ≤ <input type="number" id="mq" value="3" min="1" max="20"></label>
     <label>模型 <select id="prov"><option value="auto" selected>按上方设置</option><option value="ollama">Ollama 本地</option><option value="deepseek">DeepSeek 云端</option></select></label>
    </div>
   </form>
   <div id="runWarn" class="warn" style="display:none"></div>
  </div>
  <div class="card">
   <h2>💳 费用保护如何工作</h2>
   <table>
    <tr><th>模型</th><th>输入 / 1M tokens</th><th>输出 / 1M tokens</th></tr>
    <tr><td>deepseek-v4-flash（推荐）</td><td>$0.14（缓存命中 $0.0028）</td><td>$0.28</td></tr>
    <tr><td>deepseek-v4-pro</td><td>$0.435（缓存命中 $0.003625）</td><td>$0.87</td></tr>
   </table>
   <div class="empty">Ollama 本地推理没有 API token 费用。DeepSeek 会先按最保守价格估算单次调用；任何一次预计越过会话预算的请求都会被拦截，已发生费用可在“成本”中核对。</div>
  </div>
 </div>

 <!-- 任务 -->
 <div class="pane" id="p-jobs">
  <div class="card"><div class="head"><h2>任务队列</h2><span class="job-actions"><button class="ghost" onclick="clearFinishedJobs()">清空已完成</button></span></div><p class="hint">完成、取消或失败的任务可删除；删除队列记录不会删除已经生成的报告。</p><div id="jobs"><p class="empty">暂无任务</p></div></div>
 </div>

 <!-- 定时任务 -->
 <div class="pane" id="p-schedules">
  <div class="card"><h2>⏱ 自动研究计划</h2><p class="hint">应用运行期间按设定间隔自动发起研究；计划保存在本地，可随时停用、立即执行或删除。</p>
   <form id="scheduleForm"><div class="row"><input id="sq" type="text" placeholder="研究主题，如：agent memory" required><select id="smode"><option value="deep">深度研究</option><option value="single">单轮研究</option></select><label>每隔（分钟）<input id="sinterval" type="number" value="1440" min="1"></label><button type="submit">保存计划</button></div>
   <div class="row"><label>每来源 ≤ <input id="smr" type="number" value="5" min="1" max="20"></label><label>轮数 <input id="srounds" type="number" value="2" min="1" max="5"></label><label>分支 <input id="sbranch" type="number" value="1" min="1" max="3"></label><label>总查询 ≤ <input id="squeries" type="number" value="3" min="1" max="20"></label></div></form>
   <div id="schedules"><p class="empty">暂无定时计划</p></div></div>
 </div>

 <!-- 报告 -->
 <div class="pane" id="p-reports">
  <div class="card"><h2>📄 研究报告</h2><div id="reportList" class="report-list"><p class="empty">正在加载报告…</p></div></div>
  <div class="card"><h2 id="reportTitle">选择一份报告预览</h2><div class="report"><pre id="reportBody">报告生成后会出现在这里。选择左侧条目即可在应用内阅读；也可以打开所在文件夹。</pre></div></div>
 </div>

 <!-- 成本 -->
 <div class="pane" id="p-cost">
  <div class="card">
   <div class="head"><h2>本次会话成本</h2><span class="job-actions"><button class="ghost" onclick="clearCost()">清空本次记录</button></span></div>
   <p class="hint">费用仅统计本应用本次启动后发起的模型调用。Ollama 显示调用量但不计 API 费用；清空仅重置应用内统计，不会影响云端实际账单。</p>
   <div class="cost-overview">
    <div class="cost-total"><span>累计 API 成本</span><b id="cTotal">$0.000000</b><small id="cTotalNote">尚未产生云端费用</small></div>
    <div class="cost-budget"><span class="cost-budget-label">会话预算状态</span><div class="budget-line"><b id="cBudget">未设置预算</b><span class="hint" id="cRemaining">可自由使用</span></div><div class="budget-track" id="cProgress"><i></i></div><div class="hint" id="cUsageText">设置预算后，每次云端请求都会在发送前预检。</div></div>
   </div>
   <div id="costNotice"></div>
   <div class="cost-summary">
    <div><span>模型调用</span><b id="cCalls">0</b></div>
    <div><span>云端调用</span><b id="cCloudCalls">0</b></div>
    <div><span>本地调用</span><b id="cLocalCalls">0</b></div>
    <div><span>预算拦截</span><b id="cRejected">0</b></div>
   </div>
   <div id="costProviders" class="cost-provider-list"></div>
  </div>
  <div class="card">
   <h2>调用明细</h2>
   <div class="cost-toolbar"><select id="costProviderFilter"><option value="all">全部方式</option><option value="deepseek">仅 DeepSeek</option><option value="ollama">仅 Ollama</option></select><input id="costSearch" type="search" placeholder="筛选用途或模型名称"></div>
   <div id="costEntries" class="cost-records"><p class="empty">暂无调用记录</p></div>
  </div>
 </div>

 <!-- 记忆 -->
 <div class="pane" id="p-memory">
  <div class="card"><div class="head"><h2>研究记忆（跨会话）</h2><span class="job-actions"><button class="ghost" onclick="clearMemory()">清空全部记忆</button></span></div>
   <p class="hint">记忆用于复用已检索的查询、论文和分析，避免重复研究。删除后下次研究会重新检索；正在运行的研究仍可能再次写入相同主题。</p>
   <div class="row"><input id="memorySearch" type="search" placeholder="搜索查询或论文标题"><button class="ghost" onclick="refresh()">搜索</button></div>
   <div id="mem"><p class="empty">加载中…</p></div>
   <div class="report" style="margin-top:12px;min-height:160px"><div class="meta" id="memoryTitle">选择一条记忆查看详情</div><pre id="memoryDetail">可查看本次研究保存的论文、摘要数量与盲点分析；也可删除不再需要的条目。</pre></div>
  </div>
 </div>
</div>
<script>
const $=id=>document.getElementById(id);
const jf=async(u,o={})=>{const c=new AbortController(),t=setTimeout(()=>c.abort(),5000);try{const r=await fetch(u,{...o,signal:c.signal});if(!r.ok)throw new Error("HTTP "+r.status);return await r.json()}finally{clearTimeout(t)}};
const post=(u,p)=>jf(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(p)});
let settingsLoaded=false,secretLoaded=false;
document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("on"));
  document.querySelectorAll(".pane").forEach(x=>x.classList.remove("on"));
  t.classList.add("on");$("p-"+t.dataset.p).classList.add("on");refresh();});
$("f").onsubmit=async e=>{e.preventDefault();
  $("runWarn").style.display="none";
  const r=await jf("/api/run",{method:"POST",headers:
    {"Content-Type":"application/json"},body:JSON.stringify({
    q:$("q").value,mode:$("mode").value,max_results:+$("mr").value,
    rounds:+$("rd").value,branching:+$("br").value,
    max_queries:+$("mq").value,provider:$("prov").value==="auto"?$("setProvider").value:$("prov").value})});
  if(r.error){$("runWarn").textContent="⚠ "+r.error;
    $("runWarn").style.display="block";return;}
  $("q").value="";document.querySelectorAll(".tab")[1].click();refresh();};
$("saveSettings").onclick=async()=>{
  const payload={provider:$("setProvider").value,model:$("setModel").value||null,
    budget_usd:$("setBudget").value===""?null:+$("setBudget").value,
    llm_timeout:+$("setTimeout").value,
    ollama_base_url:$("ollamaUrl").value,deepseek_base_url:$("deepseekUrl").value};
  if($("setKey").value) payload.api_key=$("setKey").value;
  const r=await jf("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  if($("setKey").value&&window.agent?.saveDeepSeekKey)await window.agent.saveDeepSeekKey($("setKey").value);
  $("setKey").value="";
  $("settingState").innerHTML=`<span class="status-good">已保存：</span>${r.provider==="ollama"?"本地 Ollama（零 API 费用）":r.provider==="deepseek"?"DeepSeek，预算保护已开启":"自动选择可用模型"}${r.budget_usd==null?"，未设置预算":"，预算 $"+r.budget_usd}`;
  refresh();
};
$("clearKey").onclick=async()=>{if(!confirm("确定清除系统中保存的 DeepSeek API Key 吗？"))return;if(window.agent?.saveDeepSeekKey)await window.agent.saveDeepSeekKey("");await post("/api/settings",{api_key:null});$("setKey").value="";$("settingState").textContent="已清除保存的 Key。";refresh()};
$("refreshModels").onclick=async()=>{
  await $("saveSettings").onclick();
  const r=await jf("/api/models"); const list=$("modelList");
  list.innerHTML='<option value="deepseek-v4-flash"><option value="deepseek-v4-pro">'+
    (r.models||[]).map(m=>`<option value="${esc(m.name)}">`).join("");
  $("settingState").innerHTML=r.available?`<span class="status-good">已读取 ${r.models.length} 个 Ollama 模型。</span> 现在可在“模型”中选择或手动输入。`:`<span class="status-bad">无法连接 Ollama。</span> 请确认服务地址并运行 ollama serve。`;
};
function jobCard(j){
  const openBtn=j.report_path?`<button class="ghost" onclick="revealReport('${encodeURIComponent(j.report_path)}')">在文件夹中显示</button>`:"";
  const controls=j.status==="running"?`<button class="ghost" onclick="controlJob('${j.id}','pause')">暂停</button><button class="ghost" onclick="controlJob('${j.id}','cancel')">取消</button>`:j.status==="paused"?`<button class="ghost" onclick="controlJob('${j.id}','resume')">继续</button><button class="ghost" onclick="controlJob('${j.id}','cancel')">取消</button>`:"";
  const preview=j.report_path?`<button class="ghost" onclick="viewReport('${encodeURIComponent(j.report_path)}')">应用内查看</button>`:"";
  const manage=["done","error","cancelled"].includes(j.status)?`<button class="ghost" onclick="deleteJob('${j.id}')">删除任务</button>`:"";
  return `<div class="job"><div class="head">
    <span class="badge b-${j.status}">${j.status}</span>
    <b>${j.id}</b> <span>${esc(j.desc)}</span><span class="job-actions">${controls}${manage}</span></div>
    <div class="meta">${j.started_at||""} · 报告：${j.report_path||"—"} ${preview} ${openBtn}</div>
    <pre>${(j.log||[]).join("\\n")||"(等待输出…)"}</pre></div>`;}
function esc(s){return String(s||"").replace(/[<>&"']/g,c=>c.charCodeAt(0)===34?"&quot;":c==="'"?"&#39;":c==="<"?"&lt;":c===">"?"&gt;":"&amp;")}
function usd(v){const n=Number(v||0);return "$"+(Math.abs(n)<0.01?n.toFixed(6):n.toFixed(2))}
function providerName(p){return p==="ollama"?"Ollama 本地":p==="deepseek"?"DeepSeek 云端":p||"未知方式"}
async function controlJob(id,action){await post("/api/job-control",{id,action});refresh()}
async function deleteJob(id){if(!confirm("删除这条任务记录？已生成的报告会保留。"))return;const r=await post("/api/job-delete",{id});if(r.error)alert(r.error);refresh()}
async function clearFinishedJobs(){if(!confirm("清空所有已完成、已取消和失败的任务记录？报告不会被删除。"))return;await post("/api/jobs-clear",{});refresh()}
function scheduleCard(s){const state=s.enabled?"已启用":"已停用";return `<div class="job"><div class="head"><span class="badge b-${s.enabled?"done":"queued"}">${state}</span><b>${esc(s.query)}</b></div><div class="meta">每 ${s.interval_minutes} 分钟 · ${s.mode==="deep"?"深度研究":"单轮研究"} · 上次：${s.last_run||"尚未执行"}</div><div class="schedule-actions"><button class="ghost" onclick="runSchedule('${s.id}')">立即执行</button><button class="ghost" onclick="toggleSchedule('${s.id}',${!s.enabled})">${s.enabled?"停用":"启用"}</button><button class="ghost" onclick="deleteSchedule('${s.id}')">删除</button></div></div>`}
async function runSchedule(id){await post("/api/schedule-run",{id});document.querySelector('[data-p="jobs"]').click()}
async function deleteSchedule(id){await post("/api/schedule-delete",{id});refresh()}
async function toggleSchedule(id,enabled){const all=await jf("/api/schedules"),s=all.find(x=>x.id===id);if(s)await post("/api/schedules",{...s,enabled});refresh()}
$("scheduleForm").onsubmit=async e=>{e.preventDefault();const r=await post("/api/schedules",{query:$("sq").value,mode:$("smode").value,interval_minutes:+$("sinterval").value,max_results:+$("smr").value,rounds:+$("srounds").value,branching:+$("sbranch").value,max_queries:+$("squeries").value,enabled:true});if(r.error)return;$("sq").value="";refresh()};
async function viewReport(raw){const r=await jf("/api/report?path="+raw);if(r.error)return;$("reportTitle").textContent=r.name;$("reportBody").textContent=r.content;document.querySelector('[data-p="reports"]').click()}
async function revealReport(raw){const path=decodeURIComponent(raw);if(!window.agent?.revealReport){alert("桌面版可在系统文件夹中显示报告；当前浏览器模式请使用应用内预览。");return}const ok=await window.agent.revealReport(path);if(!ok)alert("找不到报告文件，可能已被移动或删除。")}
async function deleteReport(raw){if(!confirm("删除这份报告？此操作不可恢复。"))return;const r=await post("/api/report-delete",{path:decodeURIComponent(raw)});if(r.ok){$("reportTitle").textContent="选择一份报告预览";$("reportBody").textContent="报告已删除。";refresh()}}
function memoryCard(m){const q=encodeURIComponent(m.query);const titles=(m.paper_titles||[]).map(esc).join(" · ")||"未保存论文标题";return `<div class="job"><div class="head"><b>${esc(m.query)}</b><span class="job-actions"><button class="ghost" onclick="viewMemory('${q}')">查看</button><button class="ghost" onclick="deleteMemory('${q}')">删除</button></span></div><div class="meta">${esc(m.timestamp)} · ${m.paper_count} 篇论文 · ${m.summary_count} 条摘要 · ${m.gap_count} 个研究盲点</div><div class="hint">${titles}</div></div>`}
async function viewMemory(raw){const m=await jf("/api/memory-entry?query="+raw);if(m.error)return;const nl=String.fromCharCode(10);$("memoryTitle").textContent=`${m.query||"研究记忆"} · ${m.timestamp||""}`;const papers=(m.papers||[]).map((p,i)=>`${i+1}. ${p.title||"未命名"}${p.year?` (${p.year})`:""}${p.source?` · ${p.source}`:""}${p.url?nl+"   "+p.url:""}`).join(nl)||"未保存论文";const gaps=(m.analysis&&Array.isArray(m.analysis.gaps))?m.analysis.gaps.map((g,i)=>`${i+1}. ${g.gap||g.suggested_query||JSON.stringify(g)}`).join(nl):"无盲点分析";$("memoryDetail").textContent=[`论文（${(m.papers||[]).length}）`,papers,"",`摘要记录：${(m.summaries||[]).length}`,"","研究盲点",gaps].join(nl)}
async function deleteMemory(raw){if(!confirm("删除这条研究记忆？下次研究该主题时将重新检索。"))return;const r=await post("/api/memory-delete",{query:decodeURIComponent(raw)});if(r.ok){$("memoryTitle").textContent="选择一条记忆查看详情";$("memoryDetail").textContent="记忆已删除。";refresh()}}
async function clearMemory(){if(!confirm("清空全部研究记忆？此操作不可恢复，报告文件不会删除。"))return;const r=await post("/api/memory-clear",{});if(r.ok){$("memoryTitle").textContent="选择一条记忆查看详情";$("memoryDetail").textContent="全部记忆已清空。";refresh()}}
$("memorySearch").onsearch=()=>refresh();
async function clearCost(){if(!confirm("清空本次会话的成本统计与拦截计数？不会影响 DeepSeek 的实际账单，也不会修改预算。"))return;await post("/api/cost-clear",{});refresh()}
$("costProviderFilter").onchange=()=>refresh();
$("costSearch").onsearch=()=>refresh();
async function refresh(){try{
  const prov=await jf("/api/provider");
  const b=$("providerBadge");
  if(prov.provider==="ollama"){
    b.textContent="Ollama 本地 · "+prov.model+" · 零成本";
    b.className="";}
  else if(prov.provider==="deepseek"){
    b.textContent=(prov.available?"DeepSeek · ":"DeepSeek 未配置 Key · ")+prov.model;
    b.className=prov.available?"":"off";}
  else{b.textContent="未配置 LLM";b.className="off";}
  if(!settingsLoaded){
    const s=await jf("/api/settings");
    $("setProvider").value=s.provider||"auto";
    $("setModel").value=s.model||"";
    $("setBudget").value=s.budget_usd==null?"":s.budget_usd;
    $("setTimeout").value=s.llm_timeout||90;
    $("ollamaUrl").value=s.ollama_base_url||"http://localhost:11434";
    $("deepseekUrl").value=s.deepseek_base_url||"https://api.deepseek.com";
    settingsLoaded=true;
  }
  const jobs=await jf("/api/jobs");
  $("jobs").innerHTML=jobs.length?jobs.map(jobCard).join(""):"<p class='empty'>暂无任务</p>";
  const schedules=await jf("/api/schedules");
  $("schedules").innerHTML=schedules.length?schedules.map(scheduleCard).join(""):"<p class='empty'>暂无定时计划</p>";
  const reports=await jf("/api/reports");
  $("reportList").innerHTML=reports.length?reports.map(r=>`<div class="job"><b>${esc(r.name)}</b><div class="meta">${r.modified}</div><div class="schedule-actions"><button class="ghost" onclick="viewReport('${encodeURIComponent(r.path)}')">预览</button><button class="ghost" onclick="revealReport('${encodeURIComponent(r.path)}')">在文件夹中显示</button><button class="ghost" onclick="deleteReport('${encodeURIComponent(r.path)}')">删除</button></div></div>`).join(""):"<p class='empty'>暂无报告</p>";
  const c=await jf("/api/cost");
  const providers=c.providers||[];
  const cloud=providers.find(p=>p.name==="deepseek")||{calls:0,cost_usd:0};
  const local=providers.find(p=>p.name==="ollama")||{calls:0,cost_usd:0};
  const budget=c.budget_usd;
  const ratio=c.budget_usage_ratio==null?null:Math.max(0,Math.min(1,c.budget_usage_ratio));
  $("cTotal").textContent=usd(c.total_usd);
  $("cTotalNote").textContent=cloud.calls?`DeepSeek ${cloud.calls} 次调用 · 本地调用 ${local.calls} 次免费`:`${local.calls?`本地调用 ${local.calls} 次，未产生 API 费用`:"尚未产生云端费用"}`;
  $("cBudget").textContent=budget==null?"未设置预算":`${usd(budget)} 预算`;
  $("cRemaining").textContent=budget==null?"可自由使用":`剩余 ${usd(Math.max(0,c.budget_remaining))}`;
  $("cUsageText").textContent=budget==null?"设置预算后，每次云端请求都会在发送前预检。":`已使用 ${(Math.max(0,c.budget_usage_ratio||0)*100).toFixed(1)}% · 预算按最保守价格预检`;
  const progress=$("cProgress"),bar=progress.querySelector("i");progress.className="budget-track"+(ratio!=null&&ratio>=1?" danger":ratio!=null&&ratio>=.8?" warn":"");bar.style.width=(ratio==null?0:ratio*100)+"%";
  $("costNotice").innerHTML=budget!=null&&ratio>=1?`<div class="warn">预算已用尽或已超过设置上限；后续 DeepSeek 请求会被自动拦截。可提高预算或切换到 Ollama。</div>`:budget!=null&&ratio>=.8?`<div class="warn">预算已使用 ${(ratio*100).toFixed(1)}%，建议在继续深度研究前核对预算。</div>`:c.rejected?`<div class="warn">已有 ${c.rejected} 次云端请求因预算预检被拦截；本地 Ollama 调用不受此限制。</div>`:"";
  $("cCalls").textContent=c.calls;
  $("cCloudCalls").textContent=cloud.calls;
  $("cLocalCalls").textContent=local.calls;
  $("cRejected").textContent=c.rejected;
  $("costProviders").innerHTML=providers.length?providers.map(p=>`<div class="cost-provider"><b>${providerName(p.name)}</b><small>${p.calls} 次 · 输入 ${p.prompt_tokens.toLocaleString()} · 输出 ${p.completion_tokens.toLocaleString()}</small><div>${p.name==="ollama"?"本地免费":usd(p.cost_usd)}</div></div>`).join(""):"<p class='empty'>暂无模型调用，开始研究后会在这里汇总。</p>";
  const providerFilter=$("costProviderFilter").value,keyword=$("costSearch").value.trim().toLowerCase();
  const entries=(c.entries||[]).filter(e=>(providerFilter==="all"||e.provider===providerFilter)&&(!keyword||`${e.purpose||""} ${e.model||""}`.toLowerCase().includes(keyword)));
  $("costEntries").innerHTML=entries.length?
    `<table><tr><th>时间</th><th>方式</th><th>用途</th><th>模型</th><th>输入</th><th>输出</th><th>费用</th></tr>`+
    entries.map(e=>`<tr><td>${esc(e.time)}</td><td class="${e.provider==="ollama"?"local":"cloud"}">${providerName(e.provider)}</td><td>${esc(e.purpose)||"—"}</td>
      <td>${esc(e.model)}</td><td>${Number(e.prompt_tokens||0).toLocaleString()}</td><td>${Number(e.completion_tokens||0).toLocaleString()}</td>
      <td>${e.provider==="ollama"?"本地免费":usd(e.cost_usd)}</td></tr>`).join("")+"</table>":
    `<p class='empty'>${c.entries.length?"没有符合筛选条件的调用记录":"暂无调用记录"}</p>`;
  const m=await jf("/api/memory?keyword="+encodeURIComponent($("memorySearch").value));
  $("mem").innerHTML=`<p class="empty">记忆条目 <b>${m.entries}</b> · 论文总数 <b>${m.total_papers}</b>${m.keyword?` · 搜索结果 <b>${(m.items||[]).length}</b>`:""}</p>`+
    ((m.items||[]).length?(m.items||[]).map(memoryCard).join(""):"<p class='empty'>暂无匹配的研究记忆</p>");
}catch(err){const b=$("providerBadge");b.textContent="服务检测失败 · 将自动重试";b.className="off";}}
async function restoreSavedKey(){if(secretLoaded||!window.agent?.loadDeepSeekKey)return;secretLoaded=true;const key=await window.agent.loadDeepSeekKey();if(key){await post("/api/settings",{api_key:key});$("settingState").innerHTML='<span class="status-good">已从系统安全存储加载 DeepSeek API Key。</span>'}}
restoreSavedKey().finally(()=>{refresh();setInterval(refresh,2000)});
</script></body></html>"""


class _LogBuffer:
    """线程安全的行缓冲，正确合并 ``print`` 的分片写入。"""

    _ANSI_RE = re.compile(
        r"\x1B(?:[@-_][0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
    _UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")

    def __init__(self) -> None:
        self.lines: List[str] = []
        self._pending = ""
        self.lock = threading.Lock()

    def write(self, text: str) -> int:
        """支持多次碎片写入、ANSI 清理与 UTF-8 容错。"""
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        else:
            text = str(text)
        length = len(text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        with self.lock:
            chunks = (self._pending + text).split("\n")
            self._pending = chunks.pop()
            for chunk in chunks:
                self._append_line(chunk)
            if len(self.lines) > 2000:
                del self.lines[:1000]
        return length

    @classmethod
    def _clean(cls, text: str) -> str:
        # 某些 SDK 会把中文错误消息以 \uXXXX 原样返回。
        text = cls._UNICODE_ESCAPE_RE.sub(
            lambda match: (match.group(0)
                           if 0xD800 <= int(match.group(1), 16) <= 0xDFFF
                           else chr(int(match.group(1), 16))), text)
        text = cls._ANSI_RE.sub("", text)
        text = "".join(ch for ch in text
                       if ch in "\t" or ord(ch) >= 32)
        return unicodedata.normalize("NFC", text).rstrip()

    def _append_line(self, text: str) -> None:
        line = self._clean(text)
        # 保留一个空行分隔阶段，避免进度日志被无限拉长。
        if line or not self.lines or self.lines[-1] != "":
            self.lines.append(line)

    def flush(self) -> None:
        with self.lock:
            if self._pending:
                self._append_line(self._pending)
                self._pending = ""

    def tail(self, n: int = 200) -> List[str]:
        with self.lock:
            lines = list(self.lines)
            if self._pending:
                lines.append(self._clean(self._pending))
            return lines[-n:]


# ----------------------------------------------------------------------
# 线程感知的 print 拦截器（工作线程的 print 进任务日志，主线程不受影响）
# ----------------------------------------------------------------------
class _ThreadLog:
    _local = threading.local()


class _PrintInterceptor:
    def __init__(self) -> None:
        self._orig_print = print

    def install(self) -> None:
        import builtins
        builtins.print = self._dispatch  # type: ignore[assignment]

    def _dispatch(self, *args, **kwargs) -> None:
        buf = getattr(_ThreadLog._local, "buf", None)
        if buf is not None:
            kwargs.pop("file", None)
            kwargs.pop("flush", None)
            self._orig_print(*args, file=buf, **kwargs)
        else:
            self._orig_print(*args, **kwargs)

    def set_thread_buf(self, buf: Optional[_LogBuffer]) -> None:
        _ThreadLog._local.buf = buf


_PRINT_INTERCEPTOR = _PrintInterceptor()
_PRINT_INTERCEPTOR.install()


class JobCancelled(RuntimeError):
    """研究在安全检查点被用户取消。"""


class _JobControl:
    """协作式暂停/取消控制器，不强杀正在执行的网络或文件操作。"""

    def __init__(self) -> None:
        self.paused = False
        self.cancelled = False
        self._condition = threading.Condition()

    def checkpoint(self) -> None:
        with self._condition:
            while self.paused and not self.cancelled:
                self._condition.wait(timeout=0.5)
            if self.cancelled:
                raise JobCancelled("研究已由用户取消")

    def pause(self) -> None:
        with self._condition:
            self.paused = True

    def resume(self) -> None:
        with self._condition:
            self.paused = False
            self._condition.notify_all()

    def cancel(self) -> None:
        with self._condition:
            self.cancelled = True
            self.paused = False
            self._condition.notify_all()


class ResearchWebApp:
    """研究任务 Web 服务（双模式 LLM + 成本控制）。"""

    def __init__(self, runner: Optional[Callable] = None,
                 memory: Optional[ResearchMemory] = None,
                 schedule_path: Optional[str] = None) -> None:
        self.data_dir = resolve_data_dir()
        self.memory = memory or ResearchMemory(
            path=str(self.data_dir / "research_memory.json"))
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()
        # MCP 控制接口使用每次进程启动都不同的凭据，仅写入应用数据目录。
        self._mcp_control_token = secrets.token_urlsafe(32)
        self._mcp_runtime_path = self.data_dir / "mcp_runtime.json"
        # 双角色 MCP：本进程既为外部宿主提供 Server，也可作为
        # Client 连接文献管理、知识库、文件系统和机构数据库。
        self.mcp_clients = MCPClientManager()
        self.mcp_permissions = MCPPermissionBroker()
        #: 全局设置（provider / model / budget），可被 /api/settings 修改
        self.settings: Dict[str, Any] = {
            "provider": "auto",
            "model": "gemma4:e4b",
            "budget_cny": None,
            "llm_timeout": 90,
            "download_interval": 2.0,
            "download_retries": 4,
            "download_timeout": 90,
            "ollama_base_url": "http://localhost:11434",
            "deepseek_base_url": "https://api.deepseek.com",
            # 仅保存在进程内，不写入 .env、报告或 API 响应。
            "api_key": None,
        }
        self.settings_path = self.data_dir / "app_settings.json"
        self._load_settings()
        #: 持久化成本追踪（共享给所有任务，也供只读 MCP 读取）
        self.tracker = CostTracker(
            storage_path=self.data_dir / "cost_ledger.json")
        self.tracker.set_budget(self.settings.get("budget_cny"))
        self.schedule_path = (Path(schedule_path) if schedule_path else
                              self.data_dir / "app_schedules.json")
        self.schedules: Dict[str, Dict[str, Any]] = {}
        self._load_schedules()
        self._schedule_stop = threading.Event()
        self._schedule_thread = threading.Thread(
            target=self._schedule_daemon, daemon=True)
        self._schedule_thread.start()
        self.runner = runner or self._default_runner

    # ------------------------------------------------------------------
    def submit(self, query: str, mode: str = "deep",
               max_results: int = 10, rounds: int = 2,
               branching: int = 1, max_queries: int = 3,
               provider: str = "auto", model: Optional[str] = None,
               budget_cny: Optional[float] = None,
               download: bool = False,
               max_downloads: Optional[int] = None,
               sources: Optional[List[str]] = None,
               year_from: Optional[int] = None,
               summarize_limit: Optional[int] = None,
               analyze_citations: bool = True,
               topics: Optional[List[str]] = None) -> str:
        """提交任务，返回 job_id。"""
        with self.lock:
            job_id = self._new_job_id()
            while job_id in self.jobs:  # 极低概率碰撞保护
                job_id = self._new_job_id()
            mode_label = ({"deep": "深度闭环", "single": "单轮",
                           "compare": "多主题对比"}.get(mode, mode))
            self.jobs[job_id] = {
                "id": job_id,
                "query": query,
                "mode": mode,
                "download": bool(download),
                "max_results": max_results,
                "topics": list(topics or []),
                "desc": (f"{query}（{mode_label}"
                         f"{' · 下载公开 PDF' if download else ''}）"),
                "status": "queued",
                "log": _LogBuffer(),
                "result": None,
                "error": None,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "created_ts": time.time(),
                "started_at": None,
                "started_ts": None,
                "finished_at": None,
                "finished_ts": None,
                "report_path": None,
                "control": _JobControl(),
            }
        opts = {"mode": mode, "max_results": max_results,
                "rounds": rounds, "branching": branching,
                "max_queries": max_queries,
                "provider": provider, "model": model,
                "budget_cny": budget_cny,
                "download": bool(download),
                "max_downloads": max_downloads,
                "sources": list(sources) if sources else None,
                "year_from": year_from,
                "summarize_limit": summarize_limit,
                "analyze_citations": bool(analyze_citations),
                "topics": list(topics or []),
                "download_interval": float(
                    self.settings.get("download_interval", 2.0))}
        t = threading.Thread(target=self._work, args=(job_id, query, opts),
                             daemon=True)
        t.start()
        return job_id

    def submit_comparison(self, topics: List[str], **options: Any) -> str:
        """把多主题对比纳入统一任务队列、暂停恢复和成本追踪。"""
        normalized: List[str] = []
        seen = set()
        for raw in topics:
            topic = str(raw or "").strip()
            key = topic.casefold()
            if topic and key not in seen:
                normalized.append(topic)
                seen.add(key)
        if not 2 <= len(normalized) <= 6:
            raise ValueError("多主题对比需要 2-6 个不同主题")
        if any(len(topic) > 300 for topic in normalized):
            raise ValueError("单个主题不能超过 300 个字符")
        return self.submit(
            " ↔ ".join(normalized), mode="compare", topics=normalized,
            **options)

    def submit_from_mcp(self, payload: Dict[str, Any], *,
                        allow_download: bool = False) -> str:
        """使用应用当前模型设置提交 MCP 研究。

        下载只能由专用的已确认 MCP 端点显式开启，普通研究端点
        即使收到伪造字段也不会下载。
        """
        query = str(payload.get("query") or "").strip()
        if not query:
            raise ValueError("query 不能为空")
        mode = str(payload.get("mode") or "deep")
        if mode not in {"single", "deep"}:
            raise ValueError("mode 仅支持 single 或 deep")

        def bounded(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(payload.get(name, default))
            except (TypeError, ValueError) as err:
                raise ValueError(f"{name} 必须是整数") from err
            return max(minimum, min(maximum, value))

        max_downloads = (bounded("max_downloads", 5, 1, 50)
                         if allow_download else None)
        return self.submit(
            query=query,
            mode=mode,
            max_results=bounded("max_results", 10, 1, 50),
            rounds=bounded("rounds", 2, 1, 5),
            branching=bounded("branching", 1, 1, 3),
            max_queries=bounded("max_queries", 3, 1, 20),
            provider=str(self.settings.get("provider") or "auto"),
            model=self.settings.get("model"),
            budget_cny=self.settings.get("budget_cny"),
            download=allow_download,
            max_downloads=max_downloads,
        )

    def write_memory_from_mcp(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """把一条结构化研究记忆写入当前 Web/App 进程。"""
        from .skills.metadata import Paper

        query = str(payload.get("query") or "").strip()
        if not query or len(query) > 500:
            raise ValueError("query 必须为 1-500 个字符")
        raw_papers = payload.get("papers") or []
        summaries = payload.get("summaries") or []
        analysis = payload.get("analysis")
        if not isinstance(raw_papers, list) or len(raw_papers) > 100:
            raise ValueError("papers 必须是不超过 100 项的列表")
        if not isinstance(summaries, list) or len(summaries) > 100:
            raise ValueError("summaries 必须是不超过 100 项的列表")
        if analysis is not None and not isinstance(analysis, dict):
            raise ValueError("analysis 必须是对象或 null")
        if not raw_papers and not summaries and not analysis:
            raise ValueError("papers、summaries 或 analysis 至少提供一项")

        papers = []
        for position, raw in enumerate(raw_papers, start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"papers[{position}] 必须是对象")
            title = str(raw.get("title") or "").strip()
            if not title:
                raise ValueError(f"papers[{position}].title 不能为空")
            item = dict(raw)
            item.update({
                "title": title,
                "url": str(raw.get("url") or ""),
                "source": str(raw.get("source") or "manual"),
            })
            papers.append(Paper.from_dict(item))
        clean_summaries = []
        for position, summary in enumerate(summaries, start=1):
            if not isinstance(summary, dict):
                raise ValueError(f"summaries[{position}] 必须是对象")
            clean_summaries.append(dict(summary))

        self.memory.add_round(query, papers, clean_summaries, analysis)
        entry = self.memory.get_entry(query) or {}
        return {
            "query": query,
            "timestamp": entry.get("timestamp"),
            "paper_count": len(papers),
            "summary_count": len(clean_summaries),
            "has_analysis": bool(analysis),
        }

    def delete_from_mcp(self, target_type: str, target_id: str,
                        item_index: Optional[int] = None) -> Dict[str, Any]:
        """删除一个经确认的明确目标，不提供批量清空。"""
        target_type = str(target_type or "")
        target_id = str(target_id or "").strip()
        if not target_id:
            raise ValueError("target_id 不能为空")
        if target_type == "report":
            if Path(target_id).name != target_id or not target_id.endswith(".md"):
                raise ValueError("无效的报告 ID")
            deleted = self.delete_report(str(self.data_dir / target_id))
        elif target_type == "library_item":
            if item_index is None:
                raise ValueError("删除文献条目需要 item_index")
            deleted = self.delete_library_item(target_id, int(item_index))
        elif target_type == "library_batch":
            deleted = self.delete_library_batch(target_id)
        elif target_type == "memory":
            deleted = self.memory.delete(target_id)
        elif target_type == "schedule":
            deleted = self.delete_schedule(target_id)
        elif target_type == "research_record":
            outcome = self.delete_job(target_id)
            if outcome == "active":
                raise RuntimeError("研究任务仍在运行或等待，请先暂停或等待结束")
            deleted = outcome == "deleted"
        else:
            raise ValueError("不支持的删除目标类型")
        if not deleted:
            raise LookupError("要删除的目标不存在")
        return {
            "deleted": True,
            "target_type": target_type,
            "target_id": target_id,
            "item_index": item_index,
        }

    @staticmethod
    def _new_job_id() -> str:
        """生成时间可读且删除后无序号断档语义的研究任务 ID。"""
        timestamp = time.strftime("%Y%m%dT%H%M%S")
        return f"research-{timestamp}-{uuid.uuid4().hex[:8]}"

    def control_job(self, job_id: str, action: str) -> Optional[Dict[str, Any]]:
        """暂停、继续或取消任务；执行中的 I/O 会在完成后响应控制。"""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            control: _JobControl = job["control"]
            if action == "pause" and job["status"] in {"queued", "running"}:
                control.pause()
                job["status"] = "paused"
                job["log"].write("[控制] 已请求暂停，将在当前步骤结束后生效\n")
            elif action == "resume" and job["status"] == "paused":
                control.resume()
                job["status"] = "running"
                job["log"].write("[控制] 已继续研究\n")
            elif action == "cancel" and job["status"] in (
                    "queued", "running", "paused"):
                control.cancel()
                job["status"] = "cancelling"
                job["log"].write("[控制] 已请求取消，将在当前步骤结束后停止\n")
            return self._job_view(job)

    def delete_job(self, job_id: str) -> str:
        """移除已结束的任务记录；运行中的任务必须先取消或等待结束。"""
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return "not_found"
            if job["status"] not in {"done", "error", "cancelled"}:
                return "active"
            del self.jobs[job_id]
            changed = False
            for task in self.schedules.values():
                if task.get("last_job") == job_id:
                    task["last_job"] = None
                    changed = True
            if changed:
                self._save_schedules()
            return "deleted"

    def clear_finished_jobs(self) -> int:
        """批量移除所有结束任务，保留仍在运行或等待的任务。"""
        with self.lock:
            finished_ids = [job_id for job_id, job in self.jobs.items()
                            if job["status"] in {"done", "error", "cancelled"}]
            if not finished_ids:
                return 0
            finished = set(finished_ids)
            for job_id in finished_ids:
                del self.jobs[job_id]
            changed = False
            for task in self.schedules.values():
                if task.get("last_job") in finished:
                    task["last_job"] = None
                    changed = True
            if changed:
                self._save_schedules()
            return len(finished_ids)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            return self._job_view(job)

    @staticmethod
    def _job_progress(status: str, lines: List[str]) -> Dict[str, Any]:
        """将原始日志映射为对用户友好的当前阶段和进度。"""
        rules = (
            ("[Round ", "正在执行研究轮次", 10),
            ("[规划]", "正在规划研究路径", 15),
            ("[搜索]", "正在检索并去重文献", 30),
            ("[下载]", "正在限速下载公开 PDF", 48),
            ("资料包完成", "文献下载完成", 58),
            ("[摘要]", "正在生成文献摘要", 65),
            ("[分析]", "正在对比文献与识别盲点", 78),
            ("[主题]", "正在研究对比主题", 34),
            ("[对比]", "正在生成跨主题综合", 88),
            ("[闭环]", "正在评估是否继续研究", 84),
            ("[引用]", "正在分析引用网络", 90),
            ("[报告]", "正在生成最终报告", 96),
            ("=== 深度研究完成", "研究完成", 100),
        )
        stage, progress = "正在准备任务", 5
        for line in lines:
            for marker, label, value in rules:
                if marker in line:
                    stage = label
                    progress = max(progress, value)
            match = re.search(r"\[(\d+)/(\d+)\]", line)
            if match and int(match.group(2)) > 0:
                current, total = map(int, match.groups())
                stage = f"正在处理文献 {current}/{total}"
                progress = max(progress, min(64, 48 + int(16 * current / total)))
        if status == "queued":
            return {"stage": "等待执行", "progress": 0}
        if status == "paused":
            return {"stage": f"已暂停 · {stage}", "progress": progress}
        if status == "cancelling":
            return {"stage": "正在安全取消", "progress": progress}
        if status == "cancelled":
            return {"stage": "已取消", "progress": progress}
        if status == "error":
            return {"stage": "执行失败", "progress": progress}
        if status == "done":
            return {"stage": "研究完成", "progress": 100}
        return {"stage": stage, "progress": progress}

    @classmethod
    def _job_view(cls, job: Dict[str, Any]) -> Dict[str, Any]:
        lines = job["log"].tail()
        stage = cls._job_progress(job["status"], lines)
        started_ts = job.get("started_ts")
        end_ts = job.get("finished_ts") or time.time()
        elapsed = max(0, int(end_ts - started_ts)) if started_ts else 0
        return {
            "id": job["id"], "query": job.get("query", ""),
            "mode": job.get("mode", "single"),
            "topics": job.get("topics", []),
            "download": bool(job.get("download")),
            "max_results": job.get("max_results"),
            "desc": job["desc"], "status": job["status"], "log": lines,
            "report_path": job["report_path"], "error": job["error"],
            "created_at": job.get("created_at"),
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "elapsed_seconds": elapsed,
            **stage,
        }

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self.lock:
            ids = [job["id"] for job in sorted(
                self.jobs.values(), key=lambda item: item.get("created_ts", 0),
                reverse=True)]
        views = []
        for job_id in ids:
            view = self.get_job(job_id)
            if view is not None:
                views.append(view)
        return views

    # ------------------------------------------------------------------
    def _work(self, job_id: str, query: str,
              opts: Dict[str, Any]) -> None:
        with self.lock:
            job = self.jobs[job_id]
            if job["status"] == "cancelling":
                job["status"] = "cancelled"
                job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                job["finished_ts"] = time.time()
                return
            job["status"] = ("paused" if job["control"].paused else "running")
            job["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            job["started_ts"] = time.time()
            buf = job["log"]
        _PRINT_INTERCEPTOR.set_thread_buf(buf)
        try:
            result = self.runner(query, checkpoint=job["control"].checkpoint,
                                 **opts)
            with self.lock:
                job["status"] = "done"
                job["result"] = result
                job["report_path"] = result.get("report_path")
                job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                job["finished_ts"] = time.time()
        except JobCancelled as err:
            with self.lock:
                job["status"] = "cancelled"
                job["error"] = str(err)
                job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                job["finished_ts"] = time.time()
            print(f"\n[取消] {err}")
        except Exception as err:  # noqa: BLE001
            with self.lock:
                job["status"] = "error"
                job["error"] = f"{err}\n{traceback.format_exc()[-600:]}"
                job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                job["finished_ts"] = time.time()
            print(f"\n[错误] {err}")
        finally:
            buf.flush()
            _PRINT_INTERCEPTOR.set_thread_buf(None)

    # ------------------------------------------------------------------
    def _default_runner(self, query: str, mode: str = "deep",
                        max_results: int = 10, rounds: int = 2,
                        branching: int = 1, max_queries: int = 3,
                        provider: str = "auto", model: Optional[str] = None,
                        budget_cny: Optional[float] = None,
                        download: bool = False,
                        max_downloads: Optional[int] = None,
                        sources: Optional[List[str]] = None,
                        year_from: Optional[int] = None,
                        summarize_limit: Optional[int] = None,
                        analyze_citations: bool = True,
                        topics: Optional[List[str]] = None,
                        download_interval: float = 2.0,
                        checkpoint: Optional[Callable[[], None]] = None,
                        **_: Any) -> Dict[str, Any]:
        """默认执行器：按设置构造双模式 LLM + 成本追踪。"""
        provider = provider or self.settings.get("provider", "auto")
        model = model or self.settings.get("model")
        budget = budget_cny if budget_cny is not None \
            else self.settings.get("budget_cny")
        self.tracker.set_budget(budget)

        # 自动模式也应尊重用户填入的 Ollama 自定义端点。
        if provider == "auto":
            local = LLMClient(provider="ollama",
                              base_url=self.settings.get("ollama_base_url"))
            provider = "ollama" if local.available else "deepseek"
        base_url = (self.settings.get("ollama_base_url")
                    if provider == "ollama"
                    else self.settings.get("deepseek_base_url"))
        llm = LLMClient(provider=provider, model=model,
                        base_url=base_url,
                        api_key=self.settings.get("api_key"),
                        timeout=int(self.settings.get("llm_timeout", 90)),
                        cost_tracker=self.tracker)
        from .core import (CrossPaperAnalyzer, LLMPlanner, MultiTopicComparator,
                           PaperSummarizer, ResearchAgent, ResearchLoop)
        from .plugins import DataAcquisitionPipeline
        from .skills import (DownloaderSkill, PaperCompareSkill,
                             PaperSummarizeSkill, ReportWriteSkill)
        downloader = DownloaderSkill(
            timeout=int(self.settings.get("download_timeout", 90)),
            retries=int(self.settings.get("download_retries", 4)),
            min_interval=float(self.settings.get("download_interval", 2.0)))
        report_writer = ReportWriteSkill(base_dir=str(self.data_dir))
        agent = ResearchAgent(
            planner=LLMPlanner(llm=llm),
            # Web 与 CLI 使用同一核心引擎，但 Web 通过标准 Skill 适配层
            # 获取统一的 Schema、进度、超时与结果契约。
            summarizer=PaperSummarizeSkill(
                summarizer=PaperSummarizer(llm=llm)),
            analyzer=PaperCompareSkill(
                analyzer=CrossPaperAnalyzer(llm=llm)),
            acquisition_plugin=DataAcquisitionPipeline(
                downloader=downloader, root_dir=str(self.data_dir)),
            reporter=report_writer,
        )
        overrides = {
            "sources": sources or None,
            "year_from": year_from,
            "summarize_limit": summarize_limit,
            "download": download,
            "max_downloads": max_downloads,
            "download_interval": download_interval,
        }
        if mode == "deep":
            loop = ResearchLoop(agent=agent, max_rounds=rounds,
                                branching=branching, max_queries=max_queries,
                                memory=self.memory,
                                reporter=report_writer,
                                analyze_citations=bool(analyze_citations))
            result = loop.run(query, max_results=max_results,
                              checkpoint=checkpoint,
                              **overrides)
        elif mode == "compare":
            comparison_topics = list(topics or [])
            if len(comparison_topics) < 2:
                raise ValueError("多主题对比缺少有效主题")
            result = MultiTopicComparator(
                agent=agent, llm=llm, reporter=report_writer).compare(
                    comparison_topics,
                    max_results=max_results,
                    checkpoint=checkpoint,
                    **overrides)
        else:
            result = agent.run(query, max_results=max_results,
                               summarize=True, analyze=True,
                               checkpoint=checkpoint,
                               **overrides)
        result["cost"] = self.tracker.to_dict()
        result["provider_status"] = llm.status()
        return result

    def provider_status(self) -> Dict[str, Any]:
        """返回界面当前选择的模型状态，而非只读取 .env 默认值。"""
        provider = self.settings.get("provider") or "auto"
        if provider == "auto":
            local = LLMClient(provider="ollama",
                              base_url=self.settings.get("ollama_base_url"))
            provider = "ollama" if local.available else "deepseek"
        return LLMClient(
            provider=provider,
            model=self.settings.get("model"),
            base_url=(self.settings.get("ollama_base_url")
                      if provider == "ollama"
                      else self.settings.get("deepseek_base_url")),
            api_key=self.settings.get("api_key"),
        ).status()

    def public_settings(self) -> Dict[str, Any]:
        """脱敏后的设置，允许浏览器读取。"""
        session_key = bool(self.settings.get("api_key"))
        environment_key = bool(LLMClient(provider="deepseek").api_key)
        return {
            "provider": self.settings.get("provider", "auto"),
            "model": self.settings.get("model"),
            "budget_cny": self.settings.get("budget_cny"),
            "llm_timeout": self.settings.get("llm_timeout", 90),
            "download_interval": self.settings.get("download_interval", 2.0),
            "download_retries": self.settings.get("download_retries", 4),
            "download_timeout": self.settings.get("download_timeout", 90),
            "ollama_base_url": self.settings.get("ollama_base_url"),
            "deepseek_base_url": self.settings.get("deepseek_base_url"),
            # Never return the secret itself.  The source lets the interface
            # explain persistence accurately: process session vs. environment.
            "has_api_key": session_key or environment_key,
            "api_key_source": ("session" if session_key else
                               "environment" if environment_key else "none"),
        }

    def _load_settings(self) -> None:
        """恢复非敏感的模型设置；API Key 始终由 Electron 安全存储处理。"""
        if not self.settings_path.exists():
            return
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            for key in ("provider", "model", "budget_cny", "llm_timeout",
                        "download_interval", "download_retries", "download_timeout",
                        "ollama_base_url", "deepseek_base_url"):
                if key in data:
                    self.settings[key] = data[key]
        except (OSError, ValueError, TypeError):
            pass

    def _save_settings(self) -> None:
        safe = self.public_settings()
        safe.pop("has_api_key", None)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(safe, ensure_ascii=False,
                                                 indent=2), encoding="utf-8")

    def ollama_models(self) -> Dict[str, Any]:
        """从用户配置的 Ollama 服务读取已安装模型。"""
        client = LLMClient(provider="ollama",
                           base_url=self.settings.get("ollama_base_url"))
        return {"available": client.available, "endpoint": client.base_url,
                "models": client.list_ollama_models()}

    def _skill_llm(self) -> LLMClient:
        """为 Skill 中心构造与研究任务相同的模型、预算和成本环境。"""
        provider = str(self.settings.get("provider") or "auto")
        if provider == "auto":
            local = LLMClient(
                provider="ollama",
                base_url=self.settings.get("ollama_base_url"))
            provider = "ollama" if local.available else "deepseek"
        self.tracker.set_budget(self.settings.get("budget_cny"))
        return LLMClient(
            provider=provider,
            model=self.settings.get("model"),
            base_url=(self.settings.get("ollama_base_url")
                      if provider == "ollama"
                      else self.settings.get("deepseek_base_url")),
            api_key=self.settings.get("api_key"),
            timeout=int(self.settings.get("llm_timeout", 90)),
            cost_tracker=self.tracker,
        )

    def skill_catalog(self) -> Dict[str, Any]:
        """返回可供 Web 检查和受控执行的标准 Skill 清单。"""
        from .skills import BaseSkill

        manifests = BaseSkill.manifests()
        skills = []
        for name in sorted(manifests):
            manifest = dict(manifests[name])
            permissions = list(manifest.get("permissions") or [])
            manifest["confirmation_required"] = bool(
                set(permissions) & _SKILL_CONFIRM_PERMISSIONS)
            manifest["web_invokable"] = True
            skills.append(manifest)
        return {
            "app_version": APP_VERSION,
            "count": len(skills),
            "skills": skills,
            "permission_labels": {
                "network": "访问网络",
                "filesystem.read": "读取应用数据",
                "filesystem.write": "写入应用数据",
                "sensitive_data": "处理敏感数据",
                "paid_api": "可能产生模型费用",
                "external.write": "修改外部服务",
                "destructive": "执行删除操作",
            },
        }

    def _skill_instance(self, name: str):
        from .skills import (BaseSkill, DownloaderSkill, PaperCompareSkill,
                             PaperSummarizeBatchSkill, PaperSummarizeSkill)

        skill_types = BaseSkill.registered_types()
        skill_type = skill_types.get(name)
        if skill_type is None:
            raise ValueError(f"Skill 不存在: {name}")
        if name.startswith("memory_"):
            return skill_type(memory=self.memory)
        if name == "paper_summarize":
            return PaperSummarizeSkill(llm=self._skill_llm())
        if name == "paper_summarize_batch":
            return PaperSummarizeBatchSkill(llm=self._skill_llm())
        if name == "paper_compare":
            return PaperCompareSkill(llm=self._skill_llm())
        if name == "downloader":
            return DownloaderSkill(
                timeout=int(self.settings.get("download_timeout", 90)),
                retries=int(self.settings.get("download_retries", 4)),
                min_interval=float(self.settings.get("download_interval", 2.0)))
        return skill_type()

    def invoke_skill(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """以显式权限、超时和进度边界执行一个标准 Skill。"""
        from .skills import BaseSkill

        name = str(payload.get("name") or "").strip()
        manifests = BaseSkill.manifests()
        manifest = manifests.get(name)
        if manifest is None:
            raise ValueError("Skill 不存在")
        arguments = payload.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("arguments 必须是 JSON 对象")
        if len(json.dumps(arguments, ensure_ascii=False,
                          default=str).encode("utf-8")) > 1024 * 1024:
            raise ValueError("Skill 参数超过 1 MiB")
        permissions = set(manifest.get("permissions") or [])
        needs_confirmation = bool(permissions & _SKILL_CONFIRM_PERMISSIONS)
        if needs_confirmation and not bool(payload.get("confirmed")):
            raise PermissionError(
                "该 Skill 需要用户确认权限: " + ", ".join(sorted(permissions)))

        safe_arguments = dict(arguments)
        data_dir = resolve_data_dir().resolve()
        if name == "downloader":
            safe_arguments["dest_dir"] = str(data_dir / "skill_downloads")
            filename = safe_arguments.get("filename")
            if filename not in (None, ""):
                candidate = Path(str(filename))
                if candidate.name != str(filename):
                    raise ValueError("下载文件名不能包含目录")
        if name == "report_write":
            safe_arguments["base_dir"] = str(data_dir)

        try:
            timeout = float(payload.get("timeout_seconds") or
                            manifest.get("timeout_seconds") or 60)
        except (TypeError, ValueError) as err:
            raise ValueError("timeout_seconds 必须是数字") from err
        timeout = max(1.0, min(1800.0, timeout))
        progress = []
        skill = self._skill_instance(name)
        result = skill.invoke(
            timeout_seconds=timeout,
            progress_callback=lambda event: progress.append(event.to_dict()),
            allowed_permissions=permissions,
            **safe_arguments,
        )
        return {
            "manifest": manifest,
            "progress": progress[-100:],
            "result": result.to_dict(),
        }

    def mcp_server_info(self) -> Dict[str, Any]:
        """说明宿主管理的 stdio Server 及当前 Web 控制通道状态。"""
        frozen = bool(getattr(sys, "frozen", False))
        project_root = Path(__file__).resolve().parent.parent
        data_dir = resolve_data_dir().resolve()
        tools = [
            "search_papers", "search_library", "list_reports", "read_report",
            "get_cost_overview", "estimate_cost", "search_memory", "read_memory",
            "start_research", "start_research_with_download", "write_memory",
            "list_schedules", "save_schedule", "run_schedule_now",
            "delete_content", "get_research_status", "pause_research",
            "resume_research",
        ]
        host_config = {
            "mcpServers": {
                "paper-studio": {
                    "command": sys.executable,
                    "args": (["--mcp-server"] if frozen else
                             ["-B", "-m", "agent.mcp_server"]),
                    "env": ({"PAPER_STUDIO_DATA_DIR": str(data_dir)} if frozen
                            else {
                                "PYTHONPATH": str(project_root),
                                "PAPER_STUDIO_DATA_DIR": str(data_dir),
                            }),
                }
            }
        }
        return {
            "app_version": APP_VERSION,
            "role": "server_and_client",
            "transport": "stdio",
            "lifecycle": "host_managed",
            "web_control_ready": self._mcp_runtime_path.exists(),
            "requires_web_or_app": True,
            "tool_count": len(tools),
            "tools": tools,
            "resources": [
                "paper-studio://library", "paper-studio://reports",
                "paper-studio://cost",
            ],
            "host_config": host_config,
        }

    # ---- 应用内定时任务 ------------------------------------------------
    def list_schedules(self) -> List[Dict[str, Any]]:
        with self.lock:
            return sorted((dict(v) for v in self.schedules.values()),
                          key=lambda x: x["id"])

    def save_schedule(self, data: Dict[str, Any]) -> Dict[str, Any]:
        query = str(data.get("query") or "").strip()
        if not query:
            raise ValueError("定时任务需要研究主题")
        interval = max(1, int(data.get("interval_minutes") or 60))
        raw_sources = data.get("sources")
        if raw_sources in (None, "", "all"):
            sources = None
        elif isinstance(raw_sources, list):
            sources = list(dict.fromkeys(
                str(source) for source in raw_sources
                if str(source) in _RESEARCH_SOURCES)) or None
        else:
            raise ValueError("sources 必须是检索来源数组")
        year_from = data.get("year_from")
        year_from = (max(1800, min(2100, int(year_from)))
                     if year_from not in (None, "") else None)
        summarize_limit = data.get("summarize_limit")
        summarize_limit = (max(1, min(50, int(summarize_limit)))
                           if summarize_limit not in (None, "") else None)
        with self.lock:
            task_id = str(data.get("id") or
                          f"schedule-{int(time.time() * 1000)}")
            old = self.schedules.get(task_id, {})
            task = {
                "id": task_id, "query": query,
                "enabled": bool(data.get("enabled", True)),
                "interval_minutes": interval,
                "mode": "deep" if data.get("mode", "deep") == "deep" else "single",
                "max_results": max(1, min(50, int(data.get("max_results") or 10))),
                "rounds": max(1, min(5, int(data.get("rounds") or 2))),
                "branching": max(1, min(3, int(data.get("branching") or 1))),
                "max_queries": max(1, min(20, int(data.get("max_queries") or 3))),
                "sources": sources,
                "year_from": year_from,
                "summarize_limit": summarize_limit,
                "analyze_citations": bool(data.get("analyze_citations", True)),
                "download": bool(data.get("download", False)),
                "max_downloads": max(
                    1, min(50, int(data.get("max_downloads") or 10))),
                "last_ts": old.get("last_ts"),
                "last_run": old.get("last_run"),
                "last_job": old.get("last_job"),
            }
            self.schedules[task_id] = task
            self._save_schedules()
            return dict(task)

    def delete_schedule(self, task_id: str) -> bool:
        with self.lock:
            if task_id not in self.schedules:
                return False
            del self.schedules[task_id]
            self._save_schedules()
            return True

    def run_schedule_now(self, task_id: str) -> Optional[str]:
        with self.lock:
            task = self.schedules.get(task_id)
            if task is None:
                return None
            task = dict(task)
        return self._launch_schedule(task)

    def _schedule_daemon(self) -> None:
        while not self._schedule_stop.wait(10):
            now = time.time()
            with self.lock:
                due = [dict(task) for task in self.schedules.values()
                       if task.get("enabled") and
                       (not task.get("last_ts") or now - task["last_ts"] >=
                        float(task["interval_minutes"]) * 60)]
            for task in due:
                self._launch_schedule(task)

    def _launch_schedule(self, task: Dict[str, Any]) -> str:
        job_id = self.submit(
            task["query"], mode=task["mode"],
            max_results=task["max_results"], rounds=task["rounds"],
            branching=task["branching"], max_queries=task["max_queries"],
            sources=task.get("sources"),
            year_from=task.get("year_from"),
            summarize_limit=task.get("summarize_limit"),
            analyze_citations=task.get("analyze_citations", True),
            download=task.get("download", False),
            max_downloads=task.get("max_downloads", 10),
            provider=self.settings.get("provider", "auto"),
            model=self.settings.get("model"))
        with self.lock:
            current = self.schedules.get(task["id"])
            if current is not None:
                current.update({"last_ts": time.time(),
                                "last_run": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "last_job": job_id})
                self._save_schedules()
        return job_id

    def _load_schedules(self) -> None:
        if not self.schedule_path.exists():
            return
        try:
            data = json.loads(self.schedule_path.read_text(encoding="utf-8"))
            self.schedules = {str(t["id"]): t for t in data.get("tasks", [])
                              if isinstance(t, dict) and t.get("id")}
        except (OSError, ValueError, TypeError):
            self.schedules = {}

    def _save_schedules(self) -> None:
        self.schedule_path.parent.mkdir(parents=True, exist_ok=True)
        self.schedule_path.write_text(json.dumps(
            {"tasks": list(self.schedules.values())}, ensure_ascii=False,
            indent=2), encoding="utf-8")

    # ---- 本地文献库 ----------------------------------------------------
    def _download_path(self, raw: str,
                       suffixes: Optional[set] = None) -> Optional[Path]:
        root = self.data_dir.resolve()
        candidate = Path(unquote(raw))
        candidates = ([candidate] if candidate.is_absolute() else
                      [root.parent / candidate, root / candidate])
        for value in candidates:
            try:
                path = value.resolve()
                path.relative_to(root)
                if suffixes is not None and path.suffix.lower() not in suffixes:
                    continue
                return path
            except (OSError, ValueError):
                continue
        return None

    def _library_batch_path(self, run_id: str) -> Optional[Path]:
        """解析 downloads 下的直接子目录，拒绝目录穿越和非资料包目录。"""
        if not run_id or Path(run_id).name != run_id:
            return None
        path = self._download_path(str(self.data_dir / run_id))
        if path is None or not (path / "metadata.json").is_file():
            return None
        return path

    def _library_item_path(self, raw: str, batch: Path,
                           suffixes: set) -> Optional[Path]:
        """文献文件必须属于当前批次，防止篡改清单跨批次读删。"""
        path = self._download_path(raw, suffixes)
        if path is None:
            return None
        try:
            path.relative_to(batch.resolve())
            return path
        except ValueError:
            return None

    def list_library(self, keyword: str = "", status: str = "all") -> Dict[str, Any]:
        """汇总所有下载资料包，兼容旧版 metadata.json。"""
        root = self.data_dir
        keyword = keyword.strip().lower()
        allowed_status = {"all", "ok", "downloaded", "failed",
                          "unavailable", "deleted", "missing"}
        status = status if status in allowed_status else "all"
        batches: List[Dict[str, Any]] = []
        totals = {"batches": 0, "items": 0, "downloaded": 0,
                  "failed": 0, "unavailable": 0, "missing": 0}
        if not root.exists():
            return {"batches": batches, "stats": totals}

        manifests = sorted(root.glob("*/metadata.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)[:200]
        for manifest_path in manifests:
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            batch_dir = manifest_path.parent.resolve()
            items = []
            for raw_item in data.get("items", []):
                if not isinstance(raw_item, dict):
                    continue
                item = dict(raw_item)
                pdf = self._library_item_path(
                    str(item.get("pdf_path") or ""), batch_dir, {".pdf"})
                text = self._library_item_path(
                    str(item.get("text_path") or ""), batch_dir, {".txt"})
                pdf_exists = bool(pdf and pdf.is_file())
                text_exists = bool(text and text.is_file())
                item_status = str(item.get("status") or "failed")
                if item_status in ("ok", "downloaded") and not pdf_exists:
                    item_status = "missing"
                haystack = " ".join((str(item.get("title") or ""),
                                     str(item.get("source") or ""))).lower()
                if keyword and keyword not in haystack:
                    continue
                if status != "all" and item_status != status:
                    continue
                item.update({
                    "status": item_status,
                    "pdf_path": str(pdf) if pdf_exists else None,
                    "text_path": str(text) if text_exists else None,
                    "pdf_exists": pdf_exists,
                    "text_exists": text_exists,
                    "size_bytes": pdf.stat().st_size if pdf_exists else 0,
                })
                items.append(item)

            # 搜索或筛选时不显示完全没有匹配项的批次。
            if (keyword or status != "all") and not items:
                continue
            actual = {"total": len(data.get("items", [])), "downloaded": 0,
                      "failed": 0, "unavailable": 0, "missing": 0}
            for raw_item in data.get("items", []):
                raw_status = str(raw_item.get("status") or "failed")
                raw_pdf = self._library_item_path(
                    str(raw_item.get("pdf_path") or ""), batch_dir, {".pdf"})
                exists = bool(raw_pdf and raw_pdf.is_file())
                if raw_status in ("ok", "downloaded") and exists:
                    actual["downloaded"] += 1
                elif raw_status == "unavailable":
                    actual["unavailable"] += 1
                elif raw_status == "deleted":
                    pass
                elif raw_status in ("ok", "downloaded") and not exists:
                    actual["missing"] += 1
                else:
                    actual["failed"] += 1
            batch = {
                "run_id": str(data.get("run_id") or manifest_path.parent.name),
                "generated_at": data.get("generated_at", ""),
                "updated_at": data.get("updated_at", data.get("generated_at", "")),
                "base_dir": str(manifest_path.parent.resolve()),
                "settings": data.get("settings") or {},
                "stats": actual,
                "items": items,
            }
            batches.append(batch)
            totals["batches"] += 1
            # 顶部统计反映当前搜索/筛选结果，批次头部仍保留
            # 完整批次统计，方便用户理解资料包的原始规模。
            totals["items"] += len(items)
            for item in items:
                item_status = item["status"]
                if item_status in ("ok", "downloaded") and item["pdf_exists"]:
                    totals["downloaded"] += 1
                elif item_status == "unavailable":
                    totals["unavailable"] += 1
                elif item_status == "missing":
                    totals["missing"] += 1
                elif item_status not in ("deleted",):
                    totals["failed"] += 1
        return {"batches": batches, "stats": totals,
                "keyword": keyword, "status": status}

    def delete_library_item(self, run_id: str, index: int) -> bool:
        batch = self._library_batch_path(run_id)
        if batch is None:
            return False
        manifest_path = batch / "metadata.json"
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            item = next((it for it in data.get("items", [])
                         if int(it.get("index", -1)) == int(index)), None)
            if item is None:
                return False
            for key, suffixes in (("pdf_path", {".pdf"}),
                                  ("text_path", {".txt"})):
                path = self._library_item_path(
                    str(item.get(key) or ""), batch, suffixes)
                if path is not None and path.is_file():
                    path.unlink()
                item[key] = None
            item["status"] = "deleted"
            item["error"] = "已从本地文献库删除"
            data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            tmp = batch / "metadata.json.tmp"
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(manifest_path)
            return True
        except (OSError, ValueError, TypeError):
            return False

    def delete_library_batch(self, run_id: str) -> bool:
        batch = self._library_batch_path(run_id)
        if batch is None:
            return False
        try:
            shutil.rmtree(batch)
            return True
        except OSError:
            return False

    # ---- 报告 ----------------------------------------------------------
    def _report_path(self, raw: str) -> Optional[Path]:
        return self._download_path(raw, {".md"})

    def list_reports(self) -> List[Dict[str, Any]]:
        root = self.data_dir
        if not root.exists():
            return []
        reports = []
        for path in root.glob("*.md"):
            reports.append({"path": str(path), "name": path.name,
                            "modified": time.strftime(
                                "%Y-%m-%d %H:%M:%S",
                                time.localtime(path.stat().st_mtime))})
        # The browser handles incremental rendering.  Returning the complete
        # metadata list keeps older reports reachable instead of silently
        # hiding everything after the first 100 files.
        return sorted(reports, key=lambda r: r["modified"], reverse=True)

    def read_report(self, raw: str) -> Optional[Dict[str, str]]:
        path = self._report_path(unquote(raw))
        if path is None or not path.exists():
            return None
        content = path.read_text(encoding="utf-8", errors="replace")
        return {"path": str(path), "name": path.name,
                "content": _upgrade_legacy_report_content(content)}

    def delete_report(self, raw: str) -> bool:
        """删除 downloads 中的单个 Markdown 报告，拒绝目录穿越。"""
        path = self._report_path(unquote(raw))
        if path is None or not path.exists():
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    def _handler(self) -> type:
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # noqa: A002
                pass

            def _send(self, code: int, body: str,
                      ctype: str = "application/json; charset=utf-8") -> None:
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)

            def _send_file(self, path: Path) -> None:
                ctype = ("application/pdf" if path.suffix.lower() == ".pdf"
                         else "text/plain; charset=utf-8")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("Content-Disposition", "inline")
                self.end_headers()
                with path.open("rb") as fh:
                    while True:
                        chunk = fh.read(64 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)

            def _mcp_authorized(self) -> bool:
                supplied = self.headers.get("X-Paper-Studio-Control", "")
                expected = app._mcp_control_token
                return bool(supplied and expected and
                            secrets.compare_digest(supplied, expected))

            def _send_mcp_client_error(self, err: BaseException,
                                       code: int = 400) -> None:
                message = " ".join(str(err).split())[:500]
                self._send(code, json.dumps({
                    "error": message or "MCP Client 操作失败"},
                    ensure_ascii=False))

            def do_GET(self):  # noqa: N802
                path = urlparse(self.path).path
                if path.startswith("/api/mcp/") and not self._mcp_authorized():
                    self._send(403, json.dumps({"error": "MCP control forbidden"}))
                    return
                if path == "/":
                    page = _load_index_html() or _PAGE
                    self._send(200, page, "text/html; charset=utf-8")
                elif path == "/favicon.ico":
                    # 避免浏览器把没有图标当成应用错误上报。
                    self._send(204, "", "image/x-icon")
                elif path == "/api/jobs":
                    self._send(200, json.dumps(
                        app.list_jobs(), ensure_ascii=False))
                elif path == "/api/memory":
                    keyword = parse_qs(urlparse(self.path).query).get(
                        "keyword", [""])[0]
                    memory = app.memory.stats()
                    memory["keyword"] = keyword
                    memory["items"] = app.memory.list_entries(keyword)
                    self._send(200, json.dumps(memory, ensure_ascii=False))
                elif path == "/api/memory-entry":
                    query = parse_qs(urlparse(self.path).query).get("query", [""])[0]
                    entry = app.memory.get_entry(query)
                    if entry is None:
                        self._send(404, json.dumps({"error": "memory entry not found"}))
                    else:
                        self._send(200, json.dumps(entry, ensure_ascii=False))
                elif path == "/api/provider":
                    try:
                        st = app.provider_status()
                    except Exception:  # noqa: BLE001
                        st = {"provider": "unknown", "available": False,
                              "reason": "检测失败"}
                    self._send(200, json.dumps(st, ensure_ascii=False))
                elif path == "/api/cost":
                    self._send(200, json.dumps(
                        app.tracker.to_dict(), ensure_ascii=False))
                elif path == "/api/cost/predict":
                    # Cost prediction: estimate cost for research scenarios
                    from .core.billing import price_for, pricing_period
                    model = parse_qs(urlparse(self.path).query).get("model", ["deepseek-v4-flash"])[0]
                    price = price_for(model) or price_for("deepseek-v4-flash")
                    period = pricing_period()
                    # Estimate: ~2k input chars + 1k output tokens per LLM call
                    # Deep research: 1 query + 2 rounds * 1 branch = 3 queries
                    # Each query: ~5 summaries + 1 analysis = 6 LLM calls
                    per_query_calls = 6
                    per_call_input_chars = 4000  # avg per call
                    per_call_output_tokens = 1024  # max output
                    est_per_call = (per_call_input_chars / 4 / 1_000_000 * price["input_miss"]
                                    + per_call_output_tokens / 1_000_000 * price["output"])
                    est_per_query = est_per_call * per_query_calls
                    # Historical: actual avg if calls exist
                    entries = app.tracker.entries
                    cloud_entries = [e for e in entries if e.get("provider") == "deepseek"]
                    total_tokens = sum(e["prompt_tokens"] + e["completion_tokens"] for e in cloud_entries)
                    avg_per_token = (app.tracker.total_cny() / total_tokens * 1_000_000) if total_tokens > 0 else None
                    self._send(200, json.dumps({
                        "model": model,
                        "currency": "CNY",
                        "price_period": period,
                        "price_per_1m_input_hit": price["input_hit"],
                        "price_per_1m_input_miss": price["input_miss"],
                        "price_per_1m_output": price["output"],
                        "est_single_deep": round(est_per_query, 4),
                        "est_10_rounds": round(est_per_query * 10 * 3, 2),  # 3x for branching
                        "avg_per_1m_tokens": round(avg_per_token, 4) if avg_per_token else None,
                        "actual_calls": len(cloud_entries),
                        "actual_cost_cny": app.tracker.total_cny(),
                    }, ensure_ascii=False))
                elif path == "/api/settings":
                    self._send(200, json.dumps(
                        app.public_settings(), ensure_ascii=False))
                elif path == "/api/about":
                    self._send(200, json.dumps({
                        "name": "Paper Studio", "version": APP_VERSION,
                        "role": "server_and_client",
                    }, ensure_ascii=False))
                elif path == "/api/skills":
                    self._send(200, json.dumps(
                        app.skill_catalog(), ensure_ascii=False))
                elif path == "/api/mcp-server/info":
                    self._send(200, json.dumps(
                        app.mcp_server_info(), ensure_ascii=False))
                elif path == "/api/models":
                    self._send(200, json.dumps(app.ollama_models(),
                                               ensure_ascii=False))
                elif path == "/api/schedules":
                    self._send(200, json.dumps(app.list_schedules(),
                                               ensure_ascii=False))
                elif path == "/api/reports":
                    self._send(200, json.dumps(app.list_reports(),
                                               ensure_ascii=False))
                elif path == "/api/library":
                    params = parse_qs(urlparse(self.path).query)
                    library = app.list_library(
                        params.get("keyword", [""])[0],
                        params.get("status", ["all"])[0])
                    self._send(200, json.dumps(library, ensure_ascii=False))
                elif path == "/api/library-file":
                    raw = parse_qs(urlparse(self.path).query).get("path", [""])[0]
                    file_path = app._download_path(raw, {".pdf", ".txt"})
                    if file_path is None or not file_path.is_file():
                        self._send(404, json.dumps({"error": "file not found"}))
                    else:
                        self._send_file(file_path)
                elif path == "/api/report":
                    raw = parse_qs(urlparse(self.path).query).get("path", [""])[0]
                    report = app.read_report(raw)
                    if report is None:
                        self._send(404, json.dumps({"error": "report not found"}))
                    else:
                        self._send(200, json.dumps(report, ensure_ascii=False))
                elif path == "/api/job":
                    jid = parse_qs(urlparse(self.path).query).get("id", [""])[0]
                    job = app.get_job(jid)
                    if job is None:
                        self._send(404, json.dumps({"error": "not found"}))
                    else:
                        self._send(200, json.dumps(job, ensure_ascii=False))
                elif path == "/api/mcp/job":
                    jid = parse_qs(urlparse(self.path).query).get("id", [""])[0]
                    job = app.get_job(jid)
                    if job is None:
                        self._send(404, json.dumps({"error": "研究任务不存在"},
                                                  ensure_ascii=False))
                    else:
                        self._send(200, json.dumps(job, ensure_ascii=False))
                elif path == "/api/mcp/schedules":
                    self._send(200, json.dumps(
                        {"schedules": app.list_schedules()},
                        ensure_ascii=False))
                elif path == "/api/mcp-client/servers":
                    self._send(200, json.dumps({
                        "servers": app.mcp_clients.list_servers(),
                        "role": "server_and_client",
                        "supported_transports": [
                            "stdio", "streamable_http"],
                    }, ensure_ascii=False))
                else:
                    self._send(404, json.dumps({"error": "not found"}))

            def do_POST(self):  # noqa: N802
                path = urlparse(self.path).path
                if path.startswith("/api/mcp/") and not self._mcp_authorized():
                    self._send(403, json.dumps({"error": "MCP control forbidden"}))
                    return
                length = int(self.headers.get("Content-Length", 0))
                if path.startswith("/api/") and length > 1024 * 1024:
                    self._send(413, json.dumps({
                        "error": "请求超过 1 MiB"},
                        ensure_ascii=False))
                    return
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    self._send(400, json.dumps({"error": "bad json"}))
                    return
                if not isinstance(payload, dict):
                    self._send(400, json.dumps({
                        "error": "JSON body 必须是对象"}, ensure_ascii=False))
                    return

                def bounded_int(name: str, default: int,
                                minimum: int, maximum: int) -> int:
                    try:
                        value = int(payload.get(name, default))
                    except (TypeError, ValueError) as err:
                        raise ValueError(f"{name} 必须是整数") from err
                    return max(minimum, min(maximum, value))

                def research_options() -> Dict[str, Any]:
                    raw_sources = payload.get("sources")
                    if raw_sources in (None, "", "all", []):
                        sources = None
                    elif isinstance(raw_sources, list):
                        sources = []
                        for raw_source in raw_sources:
                            source = str(raw_source)
                            if source not in _RESEARCH_SOURCES:
                                raise ValueError(f"不支持的研究来源: {source}")
                            if source not in sources:
                                sources.append(source)
                    else:
                        raise ValueError("sources 必须是数组")
                    raw_year = payload.get("year_from")
                    year_from = None
                    if raw_year not in (None, ""):
                        year_from = int(raw_year)
                        if not 1800 <= year_from <= 2100:
                            raise ValueError("year_from 必须在 1800-2100 之间")
                    raw_limit = payload.get("summarize_limit")
                    summarize_limit = None
                    if raw_limit not in (None, ""):
                        summarize_limit = max(1, min(50, int(raw_limit)))
                    max_downloads = None
                    if payload.get("max_downloads") not in (None, ""):
                        max_downloads = max(
                            1, min(50, int(payload["max_downloads"])))
                    provider = str(payload.get("provider") or "auto")
                    if provider not in {"auto", "ollama", "deepseek"}:
                        raise ValueError("provider 不受支持")
                    return {
                        "max_results": bounded_int("max_results", 10, 1, 50),
                        "rounds": bounded_int("rounds", 2, 1, 5),
                        "branching": bounded_int("branching", 1, 1, 3),
                        "max_queries": bounded_int("max_queries", 3, 1, 20),
                        "provider": provider,
                        "model": payload.get("model"),
                        "budget_cny": payload.get("budget_cny"),
                        "download": bool(payload.get("download", False)),
                        "max_downloads": max_downloads,
                        "sources": sources,
                        "year_from": year_from,
                        "summarize_limit": summarize_limit,
                        "analyze_citations": bool(
                            payload.get("analyze_citations", True)),
                    }

                if path == "/api/mcp/run":
                    try:
                        job_id = app.submit_from_mcp(payload)
                        job = app.get_job(job_id)
                        self._send(200, json.dumps(job, ensure_ascii=False))
                    except (TypeError, ValueError) as err:
                        self._send(400, json.dumps({"error": str(err)},
                                                  ensure_ascii=False))
                    return
                if path == "/api/mcp/run-download":
                    try:
                        job_id = app.submit_from_mcp(
                            payload, allow_download=True)
                        job = app.get_job(job_id)
                        self._send(200, json.dumps(job, ensure_ascii=False))
                    except (TypeError, ValueError) as err:
                        self._send(400, json.dumps({"error": str(err)},
                                                  ensure_ascii=False))
                    return
                if path == "/api/mcp/memory-write":
                    try:
                        result = app.write_memory_from_mcp(payload)
                        self._send(200, json.dumps(result, ensure_ascii=False))
                    except (TypeError, ValueError) as err:
                        self._send(400, json.dumps({"error": str(err)},
                                                  ensure_ascii=False))
                    return
                if path == "/api/mcp/schedule-save":
                    try:
                        task = app.save_schedule(payload)
                        self._send(200, json.dumps(task, ensure_ascii=False))
                    except (TypeError, ValueError) as err:
                        self._send(400, json.dumps({"error": str(err)},
                                                  ensure_ascii=False))
                    return
                if path == "/api/mcp/schedule-run":
                    job_id = app.run_schedule_now(str(payload.get("id") or ""))
                    if job_id is None:
                        self._send(404, json.dumps({
                            "error": "定时任务不存在"}, ensure_ascii=False))
                    else:
                        self._send(200, json.dumps(
                            app.get_job(job_id), ensure_ascii=False))
                    return
                if path == "/api/mcp/delete":
                    try:
                        raw_index = payload.get("item_index")
                        result = app.delete_from_mcp(
                            str(payload.get("target_type") or ""),
                            str(payload.get("target_id") or ""),
                            None if raw_index is None else int(raw_index))
                        self._send(200, json.dumps(result, ensure_ascii=False))
                    except LookupError as err:
                        self._send(404, json.dumps({"error": str(err)},
                                                  ensure_ascii=False))
                    except RuntimeError as err:
                        self._send(409, json.dumps({"error": str(err)},
                                                  ensure_ascii=False))
                    except (TypeError, ValueError) as err:
                        self._send(400, json.dumps({"error": str(err)},
                                                  ensure_ascii=False))
                    return
                if path == "/api/mcp/job-control":
                    action = str(payload.get("action") or "")
                    if action not in {"pause", "resume"}:
                        self._send(400, json.dumps({
                            "error": "action 仅支持 pause 或 resume"},
                            ensure_ascii=False))
                        return
                    job = app.control_job(str(payload.get("id") or ""), action)
                    if job is None:
                        self._send(404, json.dumps({"error": "研究任务不存在"},
                                                  ensure_ascii=False))
                        return
                    target = "paused" if action == "pause" else "running"
                    if job.get("status") != target:
                        self._send(409, json.dumps({
                            "error": f"任务当前为 {job.get('status')}，无法{('暂停' if action == 'pause' else '恢复')}"},
                            ensure_ascii=False))
                        return
                    self._send(200, json.dumps(job, ensure_ascii=False))
                    return

                # ---- 外部 MCP Client 连接中心 ----------------------------
                if path == "/api/mcp-client/server-save":
                    try:
                        server = app.mcp_clients.save_server(payload)
                        self._send(200, json.dumps(server, ensure_ascii=False))
                    except (TypeError, ValueError, MCPClientError) as err:
                        self._send_mcp_client_error(err)
                    return
                if path == "/api/mcp-client/permission-request":
                    try:
                        server_id = str(payload.get("server_id") or "")
                        server = app.mcp_clients.get_server(server_id)
                        operation = str(payload.get("operation") or "")
                        target = (str(payload.get("target") or "")
                                  if operation == "call_tool" else "")
                        arguments = payload.get("arguments") or {}
                        if not isinstance(arguments, dict):
                            raise ValueError("arguments 必须是 JSON 对象")
                        challenge = app.mcp_permissions.request(
                            operation, server_id, target,
                            server_name=server["name"],
                            arguments=arguments if operation == "call_tool" else None)
                        self._send(200, json.dumps(
                            challenge, ensure_ascii=False))
                    except (TypeError, ValueError, MCPClientError) as err:
                        self._send_mcp_client_error(err)
                    return
                if path == "/api/mcp-client/permission-approve":
                    try:
                        result = app.mcp_permissions.approve(
                            str(payload.get("challenge_id") or ""),
                            bool(payload.get("approved")))
                        self._send(200, json.dumps(result, ensure_ascii=False))
                    except MCPClientError as err:
                        self._send_mcp_client_error(err, 409)
                    return
                if path == "/api/mcp-client/trust":
                    try:
                        server_id = str(payload.get("server_id") or "")
                        app.mcp_permissions.consume(
                            str(payload.get("permission_token") or ""),
                            "trust", server_id, "")
                        server = app.mcp_clients.trust_server(server_id)
                        discovery = run_async(
                            app.mcp_clients.discover(server_id))
                        self._send(200, json.dumps({
                            "server": server, "discovery": discovery,
                        }, ensure_ascii=False))
                    except (TypeError, ValueError, MCPClientError) as err:
                        self._send_mcp_client_error(err, 409)
                    return
                if path == "/api/mcp-client/server-delete":
                    try:
                        server_id = str(payload.get("server_id") or "")
                        app.mcp_permissions.consume(
                            str(payload.get("permission_token") or ""),
                            "delete", server_id, "")
                        if not app.mcp_clients.delete_server(server_id):
                            self._send(404, json.dumps({
                                "error": "MCP 连接不存在"}, ensure_ascii=False))
                        else:
                            self._send(200, json.dumps({
                                "deleted": True, "server_id": server_id},
                                ensure_ascii=False))
                    except (TypeError, ValueError, MCPClientError) as err:
                        self._send_mcp_client_error(err, 409)
                    return
                if path == "/api/mcp-client/discover":
                    try:
                        result = run_async(app.mcp_clients.discover(
                            str(payload.get("server_id") or "")))
                        self._send(200, json.dumps(result, ensure_ascii=False))
                    except (TypeError, ValueError, MCPClientError) as err:
                        self._send_mcp_client_error(err, 502)
                    return
                if path == "/api/mcp-client/resource-read":
                    try:
                        result = run_async(app.mcp_clients.read_resource(
                            str(payload.get("server_id") or ""),
                            str(payload.get("uri") or "")))
                        self._send(200, json.dumps(result, ensure_ascii=False))
                    except (TypeError, ValueError, MCPClientError) as err:
                        self._send_mcp_client_error(err, 502)
                    return
                if path == "/api/mcp-client/prompt-get":
                    try:
                        arguments = payload.get("arguments") or {}
                        if not isinstance(arguments, dict):
                            raise ValueError("arguments 必须是 JSON 对象")
                        result = run_async(app.mcp_clients.get_prompt(
                            str(payload.get("server_id") or ""),
                            str(payload.get("prompt_name") or ""),
                            arguments))
                        self._send(200, json.dumps(result, ensure_ascii=False))
                    except (TypeError, ValueError, MCPClientError) as err:
                        self._send_mcp_client_error(err, 502)
                    return
                if path == "/api/mcp-client/tool-call":
                    try:
                        server_id = str(payload.get("server_id") or "")
                        tool_name = str(payload.get("tool_name") or "")
                        arguments = payload.get("arguments") or {}
                        if not isinstance(arguments, dict):
                            raise ValueError("arguments 必须是 JSON 对象")
                        app.mcp_permissions.consume(
                            str(payload.get("permission_token") or ""),
                            "call_tool", server_id, tool_name, arguments)
                        result = run_async(app.mcp_clients.call_tool(
                            server_id, tool_name, arguments))
                        self._send(200, json.dumps(result, ensure_ascii=False))
                    except (TypeError, ValueError, MCPClientError) as err:
                        self._send_mcp_client_error(err, 502)
                    return

                if path == "/api/skills/invoke":
                    try:
                        result = app.invoke_skill(payload)
                        self._send(200, json.dumps(result, ensure_ascii=False))
                    except PermissionError as err:
                        self._send(409, json.dumps({
                            "error": str(err), "confirmation_required": True,
                        }, ensure_ascii=False))
                    except (TypeError, ValueError) as err:
                        self._send(400, json.dumps({"error": str(err)},
                                                  ensure_ascii=False))
                    return

                if path == "/api/memory-write":
                    try:
                        if not bool(payload.get("confirmed")):
                            raise PermissionError("写入研究记忆需要用户确认")
                        clean_payload = dict(payload)
                        clean_payload.pop("confirmed", None)
                        result = app.write_memory_from_mcp(clean_payload)
                        self._send(200, json.dumps(result, ensure_ascii=False))
                    except PermissionError as err:
                        self._send(409, json.dumps({"error": str(err)},
                                                  ensure_ascii=False))
                    except (TypeError, ValueError) as err:
                        self._send(400, json.dumps({"error": str(err)},
                                                  ensure_ascii=False))
                    return

                if path == "/api/compare":
                    try:
                        raw_topics = payload.get("topics") or []
                        if not isinstance(raw_topics, list):
                            raise ValueError("topics 必须是数组")
                        job_id = app.submit_comparison(
                            [str(topic) for topic in raw_topics],
                            **research_options())
                        self._send(200, json.dumps({"job_id": job_id}))
                    except (TypeError, ValueError) as err:
                        self._send(400, json.dumps({"error": str(err)},
                                                  ensure_ascii=False))
                    return

                if path == "/api/settings":
                    for k in ("provider", "model", "budget_cny", "llm_timeout", "api_key",
                              "download_interval", "download_retries", "download_timeout",
                              "ollama_base_url", "deepseek_base_url"):
                        if k in payload:
                            app.settings[k] = payload[k]
                    if app.settings.get("provider") not in (
                            "auto", "ollama", "deepseek"):
                        app.settings["provider"] = "auto"
                    try:
                        budget = app.settings.get("budget_cny")
                        app.settings["budget_cny"] = (
                            None if budget in (None, "") else max(0, float(budget)))
                    except (TypeError, ValueError):
                        app.settings["budget_cny"] = None
                    try:
                        app.settings["llm_timeout"] = max(
                            10, min(600, int(app.settings.get("llm_timeout") or 90)))
                    except (TypeError, ValueError):
                        app.settings["llm_timeout"] = 90
                    try:
                        app.settings["download_interval"] = max(
                            0.5, min(30.0, float(
                                app.settings.get("download_interval") or 2.0)))
                    except (TypeError, ValueError):
                        app.settings["download_interval"] = 2.0
                    try:
                        app.settings["download_retries"] = max(
                            0, min(8, int(app.settings.get("download_retries") or 4)))
                    except (TypeError, ValueError):
                        app.settings["download_retries"] = 4
                    try:
                        app.settings["download_timeout"] = max(
                            30, min(600, int(app.settings.get("download_timeout") or 90)))
                    except (TypeError, ValueError):
                        app.settings["download_timeout"] = 90
                    for endpoint in ("ollama_base_url", "deepseek_base_url"):
                        value = str(app.settings.get(endpoint) or "").strip()
                        if not value.startswith(("http://", "https://")):
                            value = ("http://localhost:11434" if endpoint == "ollama_base_url"
                                     else "https://api.deepseek.com")
                        app.settings[endpoint] = value.rstrip("/")
                    app.tracker.set_budget(app.settings.get("budget_cny"))
                    app._save_settings()
                    self._send(200, json.dumps(app.public_settings(),
                                               ensure_ascii=False))
                    return
                if path == "/api/job-control":
                    job = app.control_job(str(payload.get("id") or ""),
                                          str(payload.get("action") or ""))
                    if job is None:
                        self._send(404, json.dumps({"error": "job not found"}))
                    else:
                        self._send(200, json.dumps(job, ensure_ascii=False))
                    return
                if path == "/api/job-delete":
                    outcome = app.delete_job(str(payload.get("id") or ""))
                    if outcome == "deleted":
                        self._send(200, json.dumps({"ok": True}))
                    elif outcome == "active":
                        self._send(409, json.dumps({
                            "error": "任务仍在运行或等待中，请先取消或等待其结束"},
                            ensure_ascii=False))
                    else:
                        self._send(404, json.dumps({"error": "job not found"}))
                    return
                if path == "/api/jobs-clear":
                    count = app.clear_finished_jobs()
                    self._send(200, json.dumps({"ok": True, "count": count}))
                    return
                if path == "/api/cost-clear":
                    count = app.tracker.clear()
                    self._send(200, json.dumps({"ok": True, "count": count}))
                    return
                if path == "/api/library-item-delete":
                    try:
                        ok = app.delete_library_item(
                            str(payload.get("run_id") or ""),
                            int(payload.get("index")))
                    except (TypeError, ValueError):
                        ok = False
                    self._send(200 if ok else 404, json.dumps({"ok": ok}))
                    return
                if path == "/api/library-batch-delete":
                    ok = app.delete_library_batch(
                        str(payload.get("run_id") or ""))
                    self._send(200 if ok else 404, json.dumps({"ok": ok}))
                    return
                if path == "/api/memory-delete":
                    ok = app.memory.delete(str(payload.get("query") or ""))
                    self._send(200 if ok else 404, json.dumps({"ok": ok}))
                    return
                if path == "/api/memory-clear":
                    count = app.memory.clear()
                    self._send(200, json.dumps({"ok": True, "count": count}))
                    return
                if path == "/api/schedules":
                    try:
                        task = app.save_schedule(payload)
                        self._send(200, json.dumps(task, ensure_ascii=False))
                    except (TypeError, ValueError) as err:
                        self._send(400, json.dumps({"error": str(err)}, ensure_ascii=False))
                    return
                if path == "/api/schedule-delete":
                    ok = app.delete_schedule(str(payload.get("id") or ""))
                    self._send(200 if ok else 404, json.dumps({"ok": ok}))
                    return
                if path == "/api/schedule-run":
                    job_id = app.run_schedule_now(str(payload.get("id") or ""))
                    if job_id is None:
                        self._send(404, json.dumps({"error": "schedule not found"}))
                    else:
                        self._send(200, json.dumps({"job_id": job_id}))
                    return
                if path == "/api/report-delete":
                    ok = app.delete_report(str(payload.get("path") or ""))
                    self._send(200 if ok else 404, json.dumps({"ok": ok}))
                    return
                if path != "/api/run":
                    self._send(404, json.dumps({"error": "not found"}))
                    return
                query = str(payload.get("q") or "").strip()
                if not query:
                    self._send(400, json.dumps({"error": "query 为空"}))
                    return
                mode = str(payload.get("mode", "deep"))
                if mode not in {"single", "deep"}:
                    self._send(400, json.dumps({
                        "error": "mode 仅支持 single 或 deep"},
                        ensure_ascii=False))
                    return
                try:
                    job_id = app.submit(
                        query, mode=mode, **research_options())
                    self._send(200, json.dumps({"job_id": job_id}))
                except (TypeError, ValueError) as err:
                    self._send(400, json.dumps({"error": str(err)},
                                              ensure_ascii=False))

        return Handler

    def _make_server(self, host: str = "127.0.0.1", port: int = 8765):
        return ThreadingHTTPServer((host, port), self._handler())

    def _publish_mcp_runtime(self, port: int) -> None:
        """原子发布本机 MCP 控制入口；文件权限限制为当前用户。"""
        self._mcp_runtime_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "pid": os.getpid(),
            "port": int(port),
            "token": self._mcp_control_token,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        tmp = self._mcp_runtime_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(self._mcp_runtime_path)
        try:
            self._mcp_runtime_path.chmod(0o600)
        except OSError:
            pass

    def _cleanup_mcp_runtime(self) -> None:
        """只清理自己发布的运行时文件，避免误删后启动实例的信息。"""
        try:
            data = json.loads(self._mcp_runtime_path.read_text(encoding="utf-8"))
            if secrets.compare_digest(str(data.get("token") or ""),
                                      self._mcp_control_token):
                self._mcp_runtime_path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            pass

    def serve(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        server = self._make_server(host, port)
        actual_port = server.server_address[1]
        previous_sigterm = None
        if threading.current_thread() is threading.main_thread():
            previous_sigterm = signal.getsignal(signal.SIGTERM)

            def stop_on_sigterm(_signum, _frame):
                raise KeyboardInterrupt

            signal.signal(signal.SIGTERM, stop_on_sigterm)
        try:
            self._publish_mcp_runtime(actual_port)
        except OSError as err:
            print(f"[warn] MCP 控制入口发布失败: {err}", flush=True)
        print(f"Web 界面已启动: http://{host}:{actual_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")
        finally:
            self._cleanup_mcp_runtime()
            server.server_close()
            if previous_sigterm is not None:
                signal.signal(signal.SIGTERM, previous_sigterm)


def main() -> int:
    parser = argparse.ArgumentParser(description="学术研究助理 Agent Web 界面")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    args = parser.parse_args()
    ResearchWebApp().serve(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

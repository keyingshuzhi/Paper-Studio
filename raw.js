
const $=id=>document.getElementById(id);
const esc=s=>String(s||"").replace(/[<>&"']/g,c=>({'"':"&quot;","'":"&#39;","<":"&lt;",">":"&gt;","&":"&amp;"})[c]||c);
const pn=p=>lastPublicSettings?.provider_profiles?.find(item=>item.id===p)?.name||({ollama:"Ollama",deepseek:"DeepSeek"})[p]||p||"?";
document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{document.querySelectorAll(".tab").forEach(x=>x.classList.remove("on"));document.querySelectorAll(".pane").forEach(x=>x.classList.remove("on"));t.classList.add("on");$("p-"+t.dataset.p).classList.add("on");if(t.dataset.p==="skills")loadSkills();if(t.dataset.p==="settings"){loadAbout()}refresh()}); (function(){const p=new URLSearchParams(location.search).get("tab");if(p){const t=document.querySelector('.tab[data-p="'+p+'"]');if(t)t.click();if(p==="settings"){const s=new URLSearchParams(location.search).get("setting");if(s){const sb=document.querySelector('[data-setting="'+s+'"]');if(sb)sb.click()}}}})();
function applyResearchPreset(kind){const deep=kind!=="quick";$("mode").value=deep?"deep":"single";$("mr").value=kind==="quick"?6:kind==="collect"?12:10;$("rd").value=kind==="quick"?1:2;$("br").value=1;$("mq").value=kind==="quick"?1:kind==="collect"?4:3;$("downloadPdf").checked=kind==="collect";$("maxDownloads").value=kind==="collect"?15:10;$("q").focus()}
document.addEventListener("keydown",e=>{const target=e.target,typing=target&&["INPUT","TEXTAREA","SELECT"].includes(target.tagName);if((e.metaKey||e.ctrlKey)&&e.key==="Enter"&&$("p-research").classList.contains("on")){e.preventDefault();$("f").requestSubmit()}if(e.key==="/"&&!typing&&!e.metaKey&&!e.ctrlKey&&!e.altKey){e.preventDefault();$("q").focus()}});
const jf=async(u,o={})=>{const c=new AbortController(),t=setTimeout(()=>c.abort(),o.timeout||30000);try{const options={...o,signal:c.signal};delete options.timeout;const r=await fetch(u,options);let data={};try{data=await r.json()}catch(_){}if(!r.ok)throw new Error(data.error||("HTTP "+r.status));return data}finally{clearTimeout(t)}};
const post=(u,p,o={})=>jf(u,{...o,method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(p)});
let settingsLoaded=false,lastPublicSettings=null,latestJobs=[],latestReports=[],latestLibrary={batches:[],stats:{}},activeReportPath="",reportVisibleLimit=30,reportLoadToken=0;
const selectedLibraryPapers=new Map();
let activeLibraryDocument=null,activeReaderTab="pdf",readerSelectedQuote="";
const reportCache=new Map();
const renderedLists = new WeakMap();
let memoryLoadToken = 0, refreshToken = 0, providerStatusLoaded = false;
function renderStableList(element, html, preserveScroll = true) {
  if (renderedLists.get(element) === html && element.childElementCount) return;
  const top = preserveScroll ? element.scrollTop : 0;
  const focusId = element.contains(document.activeElement) ? document.activeElement.id : '';
  const opened = [...element.querySelectorAll('details[open][id]')].map(item => item.id);
  element.innerHTML = html;
  renderedLists.set(element, html);
  opened.forEach(id => { const details = $(id); if (details && element.contains(details)) details.open = true; });
  element.scrollTop = top;
  if (focusId) $(focusId)?.focus({preventScroll:true});
}
function uiIcon(name) {
  const paths = {
  more:'M5 12h.01M12 12h.01M19 12h.01',
  trash:'M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6',
  book:'M4 4h6a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H4V4Zm16 0h-6a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h7V4Z',
  external:'M14 3h7v7M21 3l-9 9M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5',
  folder:'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z',
  download:'M12 3v12m0 0 4-4m-4 4-4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2',
  tag:'M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9-9-9Z M7 8h.01'
};
  return '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="'+(paths[name]||paths.more)+'"'+(name==='more'?' stroke-width="3.5"':'')+'/></svg>';
}
function syncMemorySelection() {
  document.querySelectorAll('[data-memory-query]').forEach(button => {
    const selected = decodeURIComponent(button.dataset.memoryQuery) === activeMemoryQuery;
    button.classList.toggle('on', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
}
function setReportActions(ready) {
  ['copyReport','snapshotReport','reportTop','revealReport','exportReportMd','exportReportWord','exportReportPdf'].forEach(id => $(id).disabled = !ready);
  document.querySelector('.report-export-menu > summary').setAttribute('aria-disabled', String(!ready));
}
function syncCompareTopics() {
  const topics = $('compareTopics').value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  const unique = new Set(topics.map(value => value.toLowerCase()));
  const valid = topics.length >= 2 && topics.length <= 6 && unique.size === topics.length;
  $('compareTopicCount').textContent = topics.length + ' / 6';
  $('compareTopicCount').dataset.state = valid ? 'ready' : topics.length > 6 || unique.size !== topics.length ? 'invalid' : '';
  $('compareTopicHint').textContent = topics.length > 6 ? '最多支持 6 个主题' : unique.size !== topics.length ? '请移除重复主题' : valid ? '主题已就绪，可以启动对比研究' : '至少填写 2 个不同主题';
  $('compareSubmit').disabled = !valid;
  $('comparePreview').innerHTML = topics.length ? topics.slice(0,6).map((topic,index) => '<li><span>'+(index+1)+'</span><div>'+esc(topic)+'</div></li>').join('') : '<li class="compare-preview-empty">填写主题后，在这里确认研究方向。</li>';
  return valid;
}
const themeStorageKey="paper-studio-theme";
document.querySelector('.settings-nav [data-setting="appearance"]').insertAdjacentHTML("beforebegin",'<button type="button" data-setting="mcp">MCP 连接</button>');
$('setting-appearance').insertAdjacentHTML("beforebegin",`<section class="settings-panel" id="setting-mcp">
 <section class="card mcp-hero">
  <div class="mcp-hero-copy"><span class="page-eyebrow">开放连接</span><h2>让 Paper Studio 接入你的研究生态</h2><p class="settings-note">对外开放可控的研究能力，对内连接文献、知识库与机构数据。所有高风险操作都会在执行前请你确认。</p><div class="mcp-hero-badges"><span>双角色</span><span>最小权限</span><span>本地优先</span></div></div>
  <div class="mcp-role-grid"><article class="mcp-role-card"><div class="mcp-role-head"><span class="mcp-role-icon" aria-hidden="true">S</span><div><small>对外开放</small><h3>MCP Server</h3></div></div><p>由 MCP 宿主按需启动 stdio 进程，并通过受控通道访问 Paper Studio 的研究能力。</p><div id="mcpServerStatus" class="mcp-server-meta"><span class="permission-chip">正在读取状态…</span></div><details class="mcp-config-details"><summary>查看宿主配置</summary><div class="mcp-config-body"><button type="button" class="btn-ghost" id="copyMcpHostConfig">复制 JSON</button><pre id="mcpHostConfig" class="mcp-host-config">正在生成配置…</pre></div></details></article>
   <article class="mcp-role-card"><div class="mcp-role-head"><span class="mcp-role-icon client" aria-hidden="true">C</span><div><small>向内连接</small><h3>MCP Client</h3></div></div><p>使用 stdio 或 Streamable HTTP 连接外部 Tools、Resources、Templates 与 Prompts。</p><div class="mcp-server-meta"><span class="permission-chip">默认未信任</span><span class="permission-chip">Tool 每次确认</span><span class="permission-chip">凭据不写入配置</span></div><button type="button" class="btn-primary mcp-role-cta" onclick="openMcpEditor(true)">＋ 添加外部连接</button></article></div>
 </section>
 <section class="card mcp-connections-card"><div class="mcp-section-head"><div><span class="page-eyebrow">连接管理</span><h2>外部 MCP 服务</h2><p class="settings-note">仅在你信任后启动本地进程或访问网络。</p></div><div class="mcp-toolbar"><button type="button" class="btn-primary" onclick="openMcpEditor(true)">＋ 新建连接</button><button type="button" class="btn-ghost" id="mcpHealthBtn" onclick="runMcpHealthAll()">健康检查</button><button type="button" class="btn-ghost" id="mcpExportBtn" onclick="exportMcpConfig()">导出</button><label class="btn-ghost mcp-import-button"><input type="file" id="mcpImportInput" accept="application/json" onchange="importMcpConfig(event)">导入</label><button type="button" class="btn-ghost" onclick="loadMcpServers()">刷新</button></div></div><div id="mcpServerList" class="mcp-list"><div class="mcp-empty mcp-empty-state"><span aria-hidden="true">+</span><b>正在读取连接</b><p>请稍候…</p></div></div><div id="mcpCapabilityPanel"></div></section>
 <details class="card mcp-editor-shell" id="mcpEditor"><summary class="mcp-editor-summary"><span class="mcp-summary-icon" aria-hidden="true">+</span><span><b>MCP 连接中心</b><small>添加或编辑连接，配置传输方式、超时与最小权限</small></span><i>展开配置</i></summary>
  <form class="mcp-form" id="mcpConnectionForm"><input type="hidden" id="mcpServerId">
   <section class="mcp-form-section"><div class="mcp-form-section-head"><span>01</span><div><b>基本信息</b><small>标记连接用途与等待时间。</small></div></div><div class="setup-grid mcp-form-grid"><label>连接名称<input id="mcpName" type="text" maxlength="100" placeholder="例如：Zotero 文献库" required></label><label>用途<select id="mcpCategory"><option value="literature">文献管理</option><option value="knowledge">知识库</option><option value="filesystem">文件系统</option><option value="institution">机构数据库</option><option value="custom">其他</option></select></label><label>传输方式<select id="mcpTransport"><option value="stdio">stdio · 本地程序</option><option value="streamable_http">Streamable HTTP · 远程服务</option></select></label><label>连接超时（秒）<input id="mcpTimeout" type="number" min="2" max="120" value="20"></label></div></section>
   <section class="mcp-form-section"><div class="mcp-form-section-head"><span>02</span><div><b>传输配置</b><small>参数不经 Shell，凭据通过环境变量引用。</small></div></div><div class="setup-grid mcp-form-grid"><div class="mcp-transport-fields" id="mcpStdioFields"><label>可执行命令<input id="mcpCommand" type="text" placeholder="npx、uvx 或绝对路径"></label><label>工作目录（可选）<input id="mcpCwd" type="text" placeholder="必须为绝对路径"></label><label class="wide">命令参数（每行一项）<textarea id="mcpArgs" placeholder="-y&#10;@modelcontextprotocol/server-filesystem&#10;/path/to/library"></textarea></label><label class="wide">子进程环境映射（每行 CHILD=HOST_ENV）<textarea id="mcpEnvFrom" placeholder="ZOTERO_TOKEN=PAPER_STUDIO_ZOTERO_TOKEN"></textarea></label></div><div class="mcp-transport-fields" id="mcpHttpFields" style="display:none"><label class="wide">MCP Endpoint<input id="mcpUrl" type="text" placeholder="https://knowledge.example.edu/mcp"></label><label class="wide">HTTP Header 环境映射（每行 Header=HOST_ENV）<textarea id="mcpHeadersFrom" placeholder="Authorization=PAPER_STUDIO_INSTITUTION_TOKEN"></textarea></label></div></div></section>
   <section class="mcp-form-section"><div class="mcp-form-section-head"><span>03</span><div><b>权限范围</b><small>从最小权限开始，随时可回到此处调整。</small></div></div><div class="mcp-permission-grid"><label class="mcp-permission-toggle"><input id="mcpResourcePermission" type="checkbox" checked><span><b>读取 Resources</b><small>允许读取外部文献与知识资源。</small></span></label><label class="mcp-permission-toggle"><input id="mcpToolPermission" type="checkbox"><span><b>请求外部 Tools</b><small>实际调用时仍会每次向你确认。</small></span></label></div></section>
   <div class="mcp-security-note"><b>凭据安全</b><span>配置只保存环境变量名，不保存凭据值；信任、删除与 Tool 调用都需二次确认。</span></div><div class="mcp-form-actions"><button type="submit" class="btn-primary">保存连接</button><button type="button" class="btn-ghost" id="mcpFormReset">取消</button><span id="mcpState" class="mcp-state" aria-live="polite"></span></div>
  </form>
 </details>
 <section class="card mcp-integrations-card"><div class="mcp-section-head"><div><span class="page-eyebrow">只读数据源</span><h2>文献与知识连接器</h2><p class="settings-note">以只读方式连接 Zotero、Obsidian、Notion 或机构库；健康检查不会修改外部数据。</p></div></div><div id="datasourceGrid" class="datasource-grid"><div class="datasource-empty">正在读取连接器…</div></div><div id="datasourceForm" class="datasource-form" style="display:none"></div></section>
 <details class="card mcp-audit-card"><summary class="mcp-disclosure-summary"><span class="mcp-summary-icon audit" aria-hidden="true">≡</span><span><b>MCP 调用审计</b><small>查看最近 50 条 Tool 调用、耗时与失败原因</small></span><i>展开记录</i></summary><div class="mcp-disclosure-body"><div class="mcp-toolbar audit"><select id="mcpAuditFilter" aria-label="按 MCP 服务过滤" onchange="loadMcpAudit()"><option value="">所有服务</option></select><button type="button" class="btn-ghost" onclick="loadMcpAudit()">刷新</button><button type="button" class="btn-danger" onclick="clearMcpAudit()">清空日志</button></div><div id="mcpAuditList" class="mcp-list"><div class="mcp-empty">正在读取审计…</div></div></div></details>
</section>`);
document.querySelector('#setting-models .runtime-settings').insertAdjacentHTML("beforebegin",`<details class="card provider-market-card"><summary class="provider-market-summary"><span class="mcp-summary-icon" aria-hidden="true">◈</span><span><b>服务商模板市场</b><small>浏览国内、国际、本地与自部署预设，一键加入模型服务列表</small></span><i>展开预设</i></summary><div class="provider-market-body"><div id="providerMarket" class="provider-market"><div class="datasource-empty">正在读取预设…</div></div></div></details>`);
function resolvedTheme(choice){return choice==="system"?(window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"):choice}
function syncThemeChoices(){const choice=localStorage.getItem(themeStorageKey)||"system";document.querySelectorAll("[data-theme-choice]").forEach(b=>b.classList.toggle("on",b.dataset.themeChoice===choice))}
function applyTheme(choice){const resolved=resolvedTheme(choice);document.documentElement.dataset.theme=resolved;document.documentElement.style.colorScheme=resolved;localStorage.setItem(themeStorageKey,choice);syncThemeChoices()}
applyTheme(localStorage.getItem(themeStorageKey)||"system");
window.matchMedia("(prefers-color-scheme: light)").addEventListener("change",()=>{if((localStorage.getItem(themeStorageKey)||"system")==="system")applyTheme("system")});
document.querySelectorAll("[data-theme-choice]").forEach(b=>b.onclick=()=>applyTheme(b.dataset.themeChoice));
$("navToggle").onclick=()=>{const collapsed=document.body.classList.toggle("nav-collapsed");$("navToggle").textContent=collapsed?"展开侧栏":"收起侧栏";$("navToggle").title=collapsed?"展开导航":"收起导航"};
$("newResearch").onclick=()=>{document.querySelector('[data-p="research"]').click();setTimeout(()=>$("q").focus(),0)};
document.querySelectorAll("[data-setting]").forEach(b=>b.onclick=()=>{document.querySelectorAll("[data-setting]").forEach(x=>x.classList.remove("on"));document.querySelectorAll(".settings-panel").forEach(x=>x.classList.remove("on"));b.classList.add("on");$("setting-"+b.dataset.setting).classList.add("on");if(b.dataset.setting==="models")loadProviderMarket();if(b.dataset.setting==="mcp"){loadMcpServerInfo();loadMcpServers();loadMcpAudit();loadDatasources()}if(b.dataset.setting==="about")loadAbout()});
let mcpServers=[],activeMcpDiscovery=null;
const mcpCategoryName=value=>({literature:"文献管理",knowledge:"知识库",filesystem:"文件系统",institution:"机构数据库",custom:"其他"})[value]||value;
async function mcpPost(url,payload,timeout=135000){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeout);try{const response=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal:controller.signal});let data={};try{data=await response.json()}catch(_){}if(!response.ok)throw new Error(data.error||("HTTP "+response.status));return data}finally{clearTimeout(timer)}}
function mcpMapping(text){const result={};String(text||"").split(/\r?\n/).map(line=>line.trim()).filter(Boolean).forEach(line=>{const at=line.indexOf("=");if(at<1)throw new Error("环境映射必须使用 NAME=HOST_ENV 格式");const key=line.slice(0,at).trim(),value=line.slice(at+1).trim();if(!value)throw new Error("环境变量名不能为空");result[key]=value});return result}
function mcpMappingText(value){return Object.entries(value||{}).map(([key,item])=>key+"="+item).join("\n")}
function syncMcpTransport(){const http=$("mcpTransport").value==="streamable_http";$("mcpStdioFields").style.display=http?"none":"grid";$("mcpHttpFields").style.display=http?"grid":"none"}
$("mcpTransport").onchange=syncMcpTransport;syncMcpTransport();
function resetMcpForm(){$("mcpConnectionForm").reset();$("mcpServerId").value="";$("mcpTimeout").value=20;$("mcpResourcePermission").checked=true;$("mcpToolPermission").checked=false;$("mcpState").textContent="";syncMcpTransport()}
function openMcpEditor(fresh=false){if(fresh)resetMcpForm();$("mcpEditor").open=true;requestAnimationFrame(()=>{$("mcpEditor").scrollIntoView({behavior:"smooth",block:"start"});setTimeout(()=>$("mcpName").focus({preventScroll:true}),260)})}
$("mcpFormReset").onclick=()=>{resetMcpForm();$("mcpEditor").open=false};
function mcpPayload(){const transport=$("mcpTransport").value;return{id:$("mcpServerId").value||undefined,name:$("mcpName").value.trim(),category:$("mcpCategory").value,transport,timeout_seconds:+$("mcpTimeout").value,command:transport==="stdio"?$("mcpCommand").value.trim():"",args:transport==="stdio"?$("mcpArgs").value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean):[],cwd:transport==="stdio"?$("mcpCwd").value.trim():"",env_from:transport==="stdio"?mcpMapping($("mcpEnvFrom").value):{},url:transport==="streamable_http"?$("mcpUrl").value.trim():"",headers_from:transport==="streamable_http"?mcpMapping($("mcpHeadersFrom").value):{},permissions:{resources_read:$("mcpResourcePermission").checked,tools_call:$("mcpToolPermission").checked}}}
$("mcpConnectionForm").onsubmit=async event=>{event.preventDefault();const state=$("mcpState");state.textContent="正在保存…";try{const server=await mcpPost("/api/mcp-client/server-save",mcpPayload(),15000);resetMcpForm();$("mcpEditor").open=false;toast(server.trusted?"连接已保存":"连接已保存，信任后才会访问外部服务","good");await loadMcpServers()}catch(error){state.textContent=error.message||"保存失败"}};
function mcpServerCard(server){const summary=server.last_summary||{},transport=server.transport==="stdio"?"stdio 本地程序":"Streamable HTTP",endpoint=server.transport==="stdio"?[server.command,...(server.args||[]).slice(0,2)].join(" "):server.url,initial=esc((server.name||"M").trim().slice(0,1).toUpperCase()),trust=server.trusted?'<span class="trusted">已信任</span>':'<span class="untrusted">待信任</span>',environment=server.environment_ready?'':'<span class="bad">缺少环境变量</span>',status=server.last_status==="connected"?'<span class="trusted">已连接</span>':server.last_status==="error"?'<span class="bad">连接失败</span>':'';return '<article class="mcp-server"><div class="mcp-server-head"><div class="mcp-server-identity"><span class="mcp-server-icon">'+initial+'</span><div><h3>'+esc(server.name)+'</h3><p>'+esc(mcpCategoryName(server.category))+' · '+esc(transport)+'</p><code>'+esc(endpoint||"未配置地址")+'</code>'+(server.last_error?'<span class="status-failed mcp-server-error">'+esc(server.last_error)+'</span>':"")+'</div></div><div class="mcp-server-actions"><button type="button" class="btn-ghost" onclick="editMcpServer(\''+server.id+'\')">编辑</button>'+(server.trusted?'<button type="button" class="btn-primary" onclick="discoverMcpServer(\''+server.id+'\')">发现能力</button>':'<button type="button" class="btn-primary" onclick="trustMcpServer(\''+server.id+'\')">信任并测试</button>')+'<button type="button" class="btn-danger" onclick="deleteMcpServer(\''+server.id+'\')">删除</button></div></div><div class="mcp-tags">'+trust+status+environment+'<span>Resources '+(server.permissions?.resources_read?"可读":"关闭")+'</span><span>Tools '+(server.permissions?.tools_call?"需确认":"关闭")+'</span>'+(summary.protocol_version?'<span>'+esc(summary.protocol_version)+'</span>':"")+(summary.tools!=null?'<span>'+summary.tools+' Tools</span>':"")+(summary.resources!=null?'<span>'+summary.resources+' Resources</span>':"")+'</div></article>'}
async function loadMcpServers(){const list=$("mcpServerList");try{const data=await jf("/api/mcp-client/servers");mcpServers=data.servers||[];list.innerHTML=mcpServers.length?mcpServers.map(mcpServerCard).join(""):'<div class="mcp-empty mcp-empty-state"><span aria-hidden="true">+</span><b>还没有外部连接</b><p>添加后先保存配置，再按需进行信任与能力发现。</p><button type="button" class="btn-ghost" onclick="openMcpEditor(true)">添加第一个连接</button></div>'}catch(error){list.innerHTML='<div class="mcp-empty mcp-empty-state status-failed"><span aria-hidden="true">!</span><b>连接列表读取失败</b><p>'+esc(error.message||"请稍后重试")+'</p></div>'}}
let latestMcpServerInfo=null;
async function loadMcpServerInfo(){try{const info=await jf("/api/mcp-server/info");latestMcpServerInfo=info;$("mcpServerStatus").innerHTML='<span class="permission-chip '+(info.web_control_ready?'':'risk')+'">'+(info.web_control_ready?'Web 控制通道已就绪':'等待 Web 控制通道')+'</span><span class="permission-chip">'+info.tool_count+' Tools</span><span class="permission-chip">stdio · 宿主管理生命周期</span>';$("mcpHostConfig").textContent=JSON.stringify(info.host_config,null,2)}catch(error){$("mcpServerStatus").innerHTML='<span class="permission-chip risk">状态读取失败</span>';$("mcpHostConfig").textContent=error.message||"无法生成宿主配置"}}
$("copyMcpHostConfig").onclick=async()=>{if(!latestMcpServerInfo)await loadMcpServerInfo();try{await navigator.clipboard.writeText(JSON.stringify(latestMcpServerInfo.host_config,null,2));const button=$("copyMcpHostConfig"),old=button.textContent;button.textContent="已复制";setTimeout(()=>button.textContent=old,1200)}catch(_){alert("复制失败，请手动选择配置文本。")}};
function editMcpServer(id){const server=mcpServers.find(item=>item.id===id);if(!server)return;$("mcpEditor").open=true;$("mcpServerId").value=server.id;$("mcpName").value=server.name;$("mcpCategory").value=server.category;$("mcpTransport").value=server.transport;$("mcpTimeout").value=server.timeout_seconds||20;$("mcpCommand").value=server.command||"";$("mcpArgs").value=(server.args||[]).join("\n");$("mcpCwd").value=server.cwd||"";$("mcpEnvFrom").value=mcpMappingText(server.env_from);$("mcpUrl").value=server.url||"";$("mcpHeadersFrom").value=mcpMappingText(server.headers_from);$("mcpResourcePermission").checked=!!server.permissions?.resources_read;$("mcpToolPermission").checked=!!server.permissions?.tools_call;$("mcpState").textContent="正在编辑 "+server.name;syncMcpTransport();$("mcpEditor").scrollIntoView({behavior:"smooth",block:"start"})}
async function mcpPermission(operation,serverId,target="",arguments={}){const challenge=await mcpPost("/api/mcp-client/permission-request",{operation,server_id:serverId,target,arguments},15000),approved=confirm(challenge.message);const grant=await mcpPost("/api/mcp-client/permission-approve",{challenge_id:challenge.challenge_id,approved},15000);if(!approved||!grant.permission_token)throw new Error("已取消操作");return grant.permission_token}
async function trustMcpServer(id){try{const token=await mcpPermission("trust",id);$("mcpCapabilityPanel").innerHTML='<div class="mcp-capabilities">正在建立连接并发现能力…</div>';const result=await mcpPost("/api/mcp-client/trust",{server_id:id,permission_token:token});await loadMcpServers();renderMcpDiscovery(result.discovery)}catch(error){$("mcpCapabilityPanel").innerHTML='<div class="mcp-capabilities status-failed">'+esc(error.message||"连接失败")+'</div>';await loadMcpServers()}}
async function discoverMcpServer(id){try{$("mcpCapabilityPanel").innerHTML='<div class="mcp-capabilities">正在发现外部能力…</div>';const discovery=await mcpPost("/api/mcp-client/discover",{server_id:id});await loadMcpServers();renderMcpDiscovery(discovery)}catch(error){$("mcpCapabilityPanel").innerHTML='<div class="mcp-capabilities status-failed">'+esc(error.message||"能力发现失败")+'</div>'}}
async function deleteMcpServer(id){try{const token=await mcpPermission("delete",id);await mcpPost("/api/mcp-client/server-delete",{server_id:id,permission_token:token},15000);if(activeMcpDiscovery?.server_id===id)$("mcpCapabilityPanel").innerHTML="";await loadMcpServers()}catch(error){if(error.message!=="已取消操作")alert(error.message||"删除失败")}}

async function runMcpHealthAll(){const btn=$("mcpHealthBtn");if(btn){btn.disabled=true;btn.textContent="检查中…"}try{await post("/api/mcp-client/health",{});toast("MCP 健康检查完成","good");await loadMcpServers()}catch(error){toast(error.message||"健康检查失败","bad")}finally{if(btn){btn.disabled=false;btn.textContent="健康检查"}}}

async function exportMcpConfig(){try{const data=await jf("/api/mcp-client/export");const blob=new Blob([JSON.stringify(data,null,2)],{type:"application/json"});const url=URL.createObjectURL(blob);const a=document.createElement("a");a.href=url;a.download="mcp_connections_"+(data.exported_at||"")+".json";a.click();URL.revokeObjectURL(url);toast("已导出 "+data.server_count+" 个连接","good")}catch(error){toast(error.message||"导出失败","bad")}}

async function importMcpConfig(event){const file=event.target.files?.[0];if(!file)return;try{const text=await file.text();const config=JSON.parse(text);const overwrite=confirm("已存在同名连接时是否覆盖?\n点「确定」=覆盖,「取消」=跳过同名");const result=await post("/api/mcp-client/import",{config,overwrite});toast("导入完成: 新增 "+result.added+" · 覆盖 "+result.replaced+" · 跳过 "+result.skipped,"good");await loadMcpServers()}catch(error){toast(error.message||"导入失败","bad")}finally{event.target.value=""}}

async function loadMcpAudit(){const list=$("mcpAuditList");const filterEl=$("mcpAuditFilter");if(!list)return;try{const data=await jf("/api/mcp-client/audit?limit=50");const items=data.items||[];const filtered=filterEl?.value?items.filter(it=>it.server_id===filterEl.value):items;if(!items.length){list.innerHTML='<div class="mcp-empty">暂无调用记录</div>';return}list.innerHTML=filtered.map(it=>{const time=esc(it.at||"未记录");const cls=it.ok?"status-ok":"status-err";const err=it.error?'<div class="err">'+esc(it.error)+'</div>':"";const args=it.arguments?'<div class="args">'+esc(JSON.stringify(it.arguments).slice(0,140))+'</div>':"";return '<div class="mcp-audit-row"><div class="head"><span class="tool">'+esc(it.tool)+'</span><span class="'+cls+'">'+(it.ok?"✓ 成功":"✗ 失败")+(it.latency_ms?" · "+it.latency_ms+"ms":"")+'</span></div><small>server='+esc(it.server_id)+' · '+time+'</small>'+args+err+'</div>'}).join("");if(filterEl&&filterEl.options.length<=1){for(const it of items){const opt=document.createElement("option");opt.value=it.server_id;opt.textContent=it.server_id;filterEl.appendChild(opt)}}filterEl.value=data.server_id||""}catch(error){list.innerHTML='<div class="mcp-empty status-failed">'+esc(error.message||"读取失败")+'</div>'}}

async function clearMcpAudit(){if(!confirm("确认清空全部调用审计?(不可恢复)"))return;try{await post("/api/mcp-client/audit-clear",{});await loadMcpAudit();toast("已清空调用审计","good")}catch(error){toast(error.message||"清空失败","bad")}}

async function loadProviderMarket(){const box=$("providerMarket");if(!box)return;try{const data=await jf("/api/provider-presets");const groups=data.groups||[];const presets=data.presets||[];box.innerHTML=groups.map(g=>{const items=presets.filter(p=>p.region===g.id);return '<div class="provider-market-group"><h3>'+esc(g.icon)+" "+esc(g.name)+'</h3><p>'+esc(g.blurb)+'</p><div class="provider-market-list">'+items.map(p=>{const tagHtml=(p.tags||[]).slice(0,3).map(t=>'<span class="pm-tag">'+esc(t)+'</span>').join("");const state=p.already_added?"added":"";const btnText=p.already_added?"已添加":(p.requires_api_key?"需 API Key":"免 Key");return '<div class="provider-market-item '+state+'"><h4>'+esc(p.name)+'</h4><small>'+esc(p.base_url)+'</small><small>默认模型: '+esc(p.default_model||"未设置")+"</small><div class=\"pm-tags\">"+tagHtml+'</div><button type="button" class="'+(p.already_added?"btn-ghost":"btn-primary")+'" onclick="quickAddProvider(\''+esc(p.id)+'\')">'+esc(btnText)+'</button></div>'}).join("")+'</div></div>'}).join("")}catch(error){box.innerHTML='<div class="datasource-empty">'+esc(error.message||"读取失败")+'</div>'}}

async function quickAddProvider(pid){try{const result=await post("/api/provider-quick-add",{provider_id:pid});if(result.status==="added"){toast("已添加服务商:"+pid,"good");await refreshSettings()}else{toast("已存在于服务商列表","good")}}catch(error){toast(error.message||"添加失败","bad")}}

async function loadDatasources(){const box=$("datasourceGrid");if(!box)return;try{const data=await jf("/api/datasources");const conns=data.connectors||[],icons={zotero:"Z",obsidian:"O",notion:"N",institutional:"I"};box.innerHTML=conns.map(c=>{const configured=c.configured?'<span class="ds-tag configured">已配置</span>':'<span class="ds-tag">未配置</span>';const keys=(c.config_keys||[]).map(k=>'<span class="ds-tag">'+esc(k)+'</span>').join("");return '<article class="datasource-card"><div class="datasource-card-head"><span class="datasource-icon">'+esc(icons[c.id]||"D")+'</span><div><h3>'+esc(c.name)+'</h3><p>'+esc(c.blurb)+'</p></div></div><div class="ds-config">'+configured+keys+'</div><div class="ds-actions"><button type="button" class="btn-ghost" onclick="editDatasource(\''+esc(c.id)+'\')">'+(c.configured?"编辑配置":"填写配置")+'</button><button type="button" class="btn-ghost" onclick="testDatasource(\''+esc(c.id)+'\')">健康检查</button></div></article>'}).join("")}catch(error){box.innerHTML='<div class="datasource-empty">'+esc(error.message||"读取失败")+'</div>'}}

function editDatasource(connectorId){const form=$("datasourceForm");form.style.display="block";form.innerHTML='<h3 style="margin:0 0 10px">配置: '+esc(connectorId)+'</h3><form id="datasourceFormInner"></form><div style="margin-top:10px"><button type="button" class="btn-primary" id="datasourceSave">保存配置</button><button type="button" class="btn-ghost" onclick="hideDatasourceForm()">收起</button></div>';form.scrollIntoView({behavior:"smooth"});$("datasourceSave").onclick=async()=>{const inputs=document.querySelectorAll("#datasourceFormInner [name]");const config={};for(const inp of inputs)config[inp.name]=inp.value;try{const result=await post("/api/datasource-configure",{connector_id:connectorId,config});toast("已保存配置","good");await loadDatasources()}catch(error){toast(error.message||"保存失败","bad")}};const knownKeys={zotero:["api_key","user_id","library_type"],obsidian:["vault_path"],notion:["integration_token"],institutional:["endpoint","protocol","set_spec","api_key"]};const keys=knownKeys[connectorId]||[];$("datasourceFormInner").innerHTML=keys.map(k=>'<label>'+esc(k)+'<input name="'+esc(k)+'" placeholder="'+esc(k)+'"></label>').join("")}

function hideDatasourceForm(){$("datasourceForm").style.display="none"}

async function testDatasource(connectorId){try{const result=await post("/api/datasource-health",{connector_id:connectorId});if(result.ok){toast(connectorId+" 健康 · "+result.latency_ms+"ms","good")}else{toast(connectorId+" 不可用: "+(result.error||"未知"),"bad")}}catch(error){toast(error.message||"检查失败","bad")}}
function mcpCapabilityItem(item,type,server){const name=item.name||item.uri||item.uriTemplate||item.uri_template||"未命名",description=item.description||item.title||"",encoded=encodeURIComponent(String(name)),canRead=!!server?.permissions?.resources_read;let button="";if(type==="tool"&&server?.permissions?.tools_call)button='<button type="button" class="btn-primary" onclick="callExternalMcpTool(\''+server.id+'\',\''+encoded+'\')">调用（需确认）</button>';if(type==="resource"&&canRead)button='<button type="button" class="btn-ghost" onclick="readExternalMcpResource(\''+server.id+'\',\''+encoded+'\')">读取</button>';if(type==="template"&&canRead)button='<button type="button" class="btn-ghost" onclick="readExternalMcpTemplate(\''+server.id+'\',\''+encoded+'\')">填写参数并读取</button>';if(type==="prompt"&&canRead)button='<button type="button" class="btn-ghost" onclick="getExternalMcpPrompt(\''+server.id+'\',\''+encoded+'\')">获取 Prompt</button>';return '<div class="mcp-cap-item"><b>'+esc(name)+'</b><small>'+esc(description||type)+'</small>'+button+'</div>'}
function renderMcpDiscovery(discovery){activeMcpDiscovery=discovery;const server=mcpServers.find(item=>item.id===discovery.server_id),tools=discovery.tools||[],resources=discovery.resources||[],templates=discovery.resource_templates||[],prompts=discovery.prompts||[];$("mcpCapabilityPanel").innerHTML='<div class="mcp-capabilities"><div><b>'+esc(server?.name||"外部 MCP Server")+'</b><div class="settings-note">'+esc(discovery.protocol_version||"未知协议")+' · '+tools.length+' Tools · '+resources.length+' Resources · '+templates.length+' Templates · '+prompts.length+' Prompts</div></div><div class="mcp-cap-grid"><div class="mcp-cap-column"><h3>Tools</h3>'+(tools.length?tools.map(item=>mcpCapabilityItem(item,"tool",server)).join(""):'<div class="mcp-empty">未开放 Tool</div>')+'<h3>Prompts</h3>'+(prompts.length?prompts.map(item=>mcpCapabilityItem(item,"prompt",server)).join(""):'<div class="mcp-empty">未开放 Prompt</div>')+'</div><div class="mcp-cap-column"><h3>Resources</h3>'+(resources.length?resources.map(item=>mcpCapabilityItem(item,"resource",server)).join(""):'<div class="mcp-empty">无静态 Resource</div>')+(templates.length?'<h3>Resource Templates</h3>'+templates.map(item=>mcpCapabilityItem(item,"template",server)).join(""):"")+'</div></div><pre class="mcp-result" id="mcpResult">选择 Resource、Template 或 Prompt，或调用已授权的 Tool。</pre></div>'}
async function readExternalMcpResource(serverId,encodedUri){const result=$("mcpResult"),uri=decodeURIComponent(encodedUri);result.textContent="正在读取 "+uri+"…";try{const data=await mcpPost("/api/mcp-client/resource-read",{server_id:serverId,uri});result.textContent=JSON.stringify(data,null,2)}catch(error){result.textContent="读取失败："+(error.message||"未知错误")}}
function parseJsonObject(raw,label="参数"){let value;try{value=JSON.parse(raw);if(!value||Array.isArray(value)||typeof value!=="object")throw new Error()}catch(_){throw new Error(label+"必须是 JSON 对象")};return value}
function expandMcpTemplate(template,values){return template.replace(/\{\?([^}]+)\}/g,(_,names)=>{const query=names.split(",").filter(name=>values[name]!=null).map(name=>encodeURIComponent(name)+"="+encodeURIComponent(String(values[name]))).join("&");return query?"?"+query:""}).replace(/\{([^}]+)\}/g,(_,name)=>{if(values[name]==null)throw new Error("缺少模板参数："+name);return encodeURIComponent(String(values[name]))})}
async function readExternalMcpTemplate(serverId,encodedTemplate){const template=decodeURIComponent(encodedTemplate),raw=prompt("输入 Resource Template 参数 JSON：","{}");if(raw===null)return;const result=$("mcpResult");try{const uri=expandMcpTemplate(template,parseJsonObject(raw,"Template 参数"));result.textContent="正在读取 "+uri+"…";const data=await mcpPost("/api/mcp-client/resource-read",{server_id:serverId,uri});result.textContent=JSON.stringify(data,null,2)}catch(error){result.textContent="Template 读取失败："+(error.message||"未知错误")}}
async function getExternalMcpPrompt(serverId,encodedName){const name=decodeURIComponent(encodedName),raw=prompt("输入 Prompt 参数 JSON（值会转换为字符串）：","{}");if(raw===null)return;const result=$("mcpResult");try{const args=parseJsonObject(raw,"Prompt 参数");result.textContent="正在获取 Prompt "+name+"…";const data=await mcpPost("/api/mcp-client/prompt-get",{server_id:serverId,prompt_name:name,arguments:args});result.textContent=JSON.stringify(data,null,2)}catch(error){result.textContent="Prompt 获取失败："+(error.message||"未知错误")}}
async function callExternalMcpTool(serverId,encodedName){const name=decodeURIComponent(encodedName),raw=prompt("输入 Tool 参数 JSON 对象：","{}");if(raw===null)return;let args;try{args=JSON.parse(raw);if(!args||Array.isArray(args)||typeof args!=="object")throw new Error()}catch(_){alert("参数必须是 JSON 对象。");return}const result=$("mcpResult");try{const token=await mcpPermission("call_tool",serverId,name,args);result.textContent="正在调用 "+name+"…";const data=await mcpPost("/api/mcp-client/tool-call",{server_id:serverId,tool_name:name,arguments:args,permission_token:token});result.textContent=JSON.stringify(data,null,2)}catch(error){if(result)result.textContent="调用未完成："+(error.message||"未知错误")}}
let providerProfiles=[],editingProviderId="",providerSecrets={},latestModelConfig=null;
function toast(message,kind="good"){const item=document.createElement("div");item.className="toast "+kind;item.textContent=message;$("toastStack").appendChild(item);setTimeout(()=>{item.style.opacity="0";item.style.transform="translateY(8px)";setTimeout(()=>item.remove(),250)},2800)}
function renderAbout(info={}){
  const version=info.version||"0.1.0",
        buildTime=info.build_time||"2026-09-05",
        stats=info.stats||{skills:30,agent_roles:4,datasources:4,mcp_tools:18},
        caps=info.capabilities||[];
  const capCard=(c)=>'<div class="about-cap" data-cap="'+esc(c.id)+'"><div class="about-cap-icon">'+esc(c.icon||"✦")+'</div><div class="about-cap-body"><b>'+esc(c.name)+'</b><span>'+esc(c.summary)+'</span><div class="about-cap-tags">'+(c.highlights||[]).map(t=>'<em>'+esc(t)+'</em>').join("")+'</div></div></div>';
  $("setting-about").innerHTML='\
<div class="about-shell">\
 <section class="about-hero">\
  <div class="about-hero-glow"></div>\
  <div class="about-logo"><img src="/assets/paper-studio-logo.png" alt="Paper Studio"></div>\
  <div class="about-hero-copy">\
   <div class="about-eyebrow"><span class="about-dot"></span>PAPER STUDIO</div>\
   <h2>让研究从检索走向洞见</h2>\
   <p>本地优先的 AI 研究工作台。问题拆解、文献获取、跨文献分析、报告与记忆一站完成。</p>\
   <div class="about-pills">\
    <span><b>v</b> '+esc(version)+'</span>\
    <span><b>构建</b> '+esc(buildTime)+'</span>\
    <span class="about-pill-accent">'+esc(stats.skills)+' 项能力</span>\
    <span class="about-pill-accent">'+esc(stats.mcp_tools)+' 个工具</span>\
   </div>\
  </div>\
 </section>\

 <section class="about-stats">\
  <div class="about-stat"><b>'+esc(stats.skills)+'</b><span>内置能力</span><small>覆盖检索 / 阅读 / 引用 / 记忆 / 报告</small></div>\
  <div class="about-stat"><b>'+esc(stats.agent_roles)+'</b><span>研究角色</span><small>检索员 · 阅读员 · 核验员 · 编辑员</small></div>\
  <div class="about-stat"><b>'+esc(stats.mcp_tools)+'</b><span>外部工具</span><small>可连接知识库与机构数据</small></div>\
  <div class="about-stat"><b>'+esc(stats.datasources)+'</b><span>数据源</span><small>Zotero · Obsidian · Notion 等</small></div>\
 </section>\

 <section class="card about-section">\
  <div class="about-section-head"><h3>这次更新带来了什么</h3><p>从单点模型配置升级为完整的研究基础设施。</p></div>\
  <div class="about-cap-grid">'+(caps.length?caps.map(capCard).join(""):"<div>暂无数据</div>")+'</div>\
 </section>\

 <section class="card about-section">\
  <div class="about-section-head"><h3>两种使用方式</h3><p>Web 版适合本地试用,桌面版适合长期研究。</p></div>\
  <div class="about-platforms">\
   <div class="about-platform"><div class="about-platform-glyph">🌐</div><b>Web 版</b><span>浏览器打开即用,适合本地调试与短期项目。</span><em>0 部署</em></div>\
   <div class="about-platform"><div class="about-platform-glyph">🖥️</div><b>桌面版</b><span>系统集成,自动管理服务商密钥,可直接打开本地研究文件。</span><em>长期项目</em></div>\
  </div>\
 </section>\

 <section class="card about-section">\
  <div class="about-section-head"><h3>可以接入哪些知识库</h3><p>把你已经在用的研究资产接进来,不必重新整理。</p></div>\
  <div class="about-connectors">\
   <div class="about-connector"><b>📚 Zotero</b><span>个人或小组文献库</span></div>\
   <div class="about-connector"><b>🟣 Obsidian</b><span>本地 Markdown 笔记</span></div>\
   <div class="about-connector"><b>📝 Notion</b><span>团队知识库</span></div>\
   <div class="about-connector"><b>🏛️ 机构库</b><span>企业内部知识库</span></div>\
  </div>\
 </section>\

 <section class="card about-section">\
  <div class="about-section-head"><h3>你的数据由谁保管</h3><p>所有研究资产都留在你的本机。</p></div>\
  <div class="about-facts">\
   <div class="about-fact"><i>⌁</i><div><b>报告与文献在本地</b><span>所有研究资料、报告与笔记都保存在你指定的目录,可随时整理与导出。</span></div></div>\
   <div class="about-fact"><i>▣</i><div><b>API 密钥不外泄</b><span>服务商密钥只保存在本地进程或系统安全存储,不会上传任何服务器。</span></div></div>\
   <div class="about-fact"><i>◐</i><div><b>模型可自由切换</b><span>内置 10 多种主流服务商,也可添加任意 OpenAI 兼容服务;主备模型失败自动切换。</span></div></div>\
   <div class="about-fact"><i>◈</i><div><b>费用透明可查</b><span>每次调用都记录用量与花费,可在成本中心查看与导出。</span></div></div>\
  </div>\
 </section>\

 <section class="about-footnote">\
  <p>Paper Studio v'+esc(version)+' · 本地部署 · 本地优先 · 数据自主</p>\
 </section>\
</div>';
}
async function loadAbout(){
  const target=$("setting-about");
  if(!target)return;
  if(target.dataset.loaded==="1"||target.dataset.loading==="1")return;
  target.dataset.loading="1";
  try{
    const info=await jf("/api/about");
    renderAbout(info);
    target.dataset.loaded="1";
  }catch(error){
    renderAbout({version:"0.1.0",build_time:"-",stats:{skills:0,agent_roles:0,datasources:0,mcp_tools:0},capabilities:[]});
    target.dataset.loaded="err";
    toast("关于信息读取失败: "+(error.message||"未知错误"),"bad");
  }finally{
    target.dataset.loading="";
  }
}
$('compareTopics').addEventListener('input', syncCompareTopics);
$('compareExample').onclick = () => { $('compareTopics').value = 'Transformer 长上下文\nMamba 状态空间模型'; syncCompareTopics(); $('compareTopics').focus(); };
function syncCompareDownload() { $('compareMaxDownloads').disabled = !$('compareDownload').checked; }
$('compareDownload').addEventListener('change', syncCompareDownload);
syncCompareDownload(); syncCompareTopics();
let webSkills=[],skillPermissionLabels={},activeSkillName="";
function schemaValue(schema={},name=""){const rawType=schema.type,type=Array.isArray(rawType)?rawType.find(x=>x!=="null"):rawType;if(schema.enum?.length)return schema.enum[0];if(name==="query")return"agent research";if(name==="title")return"Paper title";if(name==="url")return"https://example.org/paper";if(name==="source")return"manual";if(name==="text"||name==="abstract")return"Paste paper text or abstract here";if(type==="string")return"";if(type==="integer"||type==="number")return schema.minimum??1;if(type==="boolean")return false;if(type==="array")return schema.minItems?[schemaValue(schema.items||{})]:[];if(type==="object"||schema.properties){const out={};(schema.required||[]).forEach(key=>out[key]=schemaValue((schema.properties||{})[key]||{},key));return out}return null}
function renderSkillList(){const keyword=$("skillSearch").value.trim().toLowerCase(),visible=webSkills.filter(skill=>!keyword||(skill.name+" "+skill.description+" "+(skill.permissions||[]).join(" ")).toLowerCase().includes(keyword));$("skillList").innerHTML=visible.length?visible.map(skill=>'<button type="button" class="skill-item '+(skill.name===activeSkillName?'on':'')+'" onclick="selectSkill(\''+skill.name+'\')"><b>'+esc(skill.name)+'</b><span>'+esc(skill.description)+'</span></button>').join(""):'<p class="empty">没有匹配的 Skill</p>'}
async function loadSkills(){if(webSkills.length){renderSkillList();return}try{const data=await jf("/api/skills");webSkills=data.skills||[];skillPermissionLabels=data.permission_labels||{};$("skillCount").textContent=webSkills.length+" 项标准能力 · Schema / 权限 / 超时 / 进度";renderSkillList();if(webSkills.length)selectSkill(webSkills[0].name)}catch(error){$("skillList").innerHTML='<p class="empty status-failed">'+esc(error.message||"Skill 读取失败")+'</p>'}}
function selectSkill(name){const skill=webSkills.find(item=>item.name===name);if(!skill)return;activeSkillName=name;renderSkillList();const permissions=skill.permissions||[],chips=permissions.length?permissions.map(permission=>'<span class="permission-chip '+(skill.confirmation_required?'risk':'')+'">'+esc(skillPermissionLabels[permission]||permission)+'</span>').join(""):'<span class="permission-chip">无需额外权限</span>',example=JSON.stringify(schemaValue(skill.input_schema||{}),null,2);$("skillDetail").innerHTML='<div class="skill-detail-head"><div><h2>'+esc(skill.name)+'</h2><p class="skill-description">'+esc(skill.description)+'</p><div class="permission-row">'+chips+'<span class="permission-chip">默认超时 '+esc(skill.timeout_seconds??"无")+' 秒</span><span class="permission-chip">v'+esc(skill.version)+'</span></div></div></div><div class="schema-grid"><details class="schema-box"><summary>输入 JSON Schema</summary><pre>'+esc(JSON.stringify(skill.input_schema,null,2))+'</pre></details><details class="schema-box"><summary>输出 JSON Schema</summary><pre>'+esc(JSON.stringify(skill.output_schema,null,2))+'</pre></details></div><section class="skill-runner"><div class="skill-runner-head"><div><b>受控调用</b><div class="settings-note">参数先经过 Schema 校验；执行结果使用统一 SkillResult。</div></div><label>超时<input id="skillTimeout" type="number" min="1" max="1800" value="'+esc(skill.timeout_seconds||60)+'" style="width:90px"> 秒</label></div><textarea id="skillArguments" class="text-area">'+esc(example)+'</textarea><div class="row"><button type="button" class="btn-primary" onclick="runSelectedSkill()">执行 Skill</button><span id="skillRunState" class="mcp-state"></span></div><div id="skillProgress" class="skill-progress-list"></div><pre id="skillResult" class="skill-result">尚未执行。</pre></section>'}
async function runSelectedSkill(){const skill=webSkills.find(item=>item.name===activeSkillName),state=$("skillRunState"),result=$("skillResult");if(!skill)return;let args;try{args=parseJsonObject($("skillArguments").value,"Skill 参数")}catch(error){state.textContent=error.message;return}let confirmed=!skill.confirmation_required;if(!confirmed)confirmed=confirm("执行 "+skill.name+" 需要：\n"+(skill.permissions||[]).map(p=>"• "+(skillPermissionLabels[p]||p)).join("\n")+"\n\n确认继续？");if(!confirmed){state.textContent="已取消";return}const timeout=+$("skillTimeout").value||skill.timeout_seconds||60;state.textContent="正在执行…";result.textContent="等待标准结果…";$("skillProgress").innerHTML="";try{const data=await jf("/api/skills/invoke",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:skill.name,arguments:args,timeout_seconds:timeout,confirmed}),timeout:(timeout+15)*1000});state.textContent=data.result?.ok?"执行完成":"执行失败";$("skillProgress").innerHTML=(data.progress||[]).map(event=>'<div class="skill-progress-item"><i>'+Number(event.percent||0).toFixed(0)+'%</i><span>'+esc(event.message||event.stage||"")+'</span></div>').join("");result.textContent=JSON.stringify(data.result,null,2);refresh()}catch(error){state.textContent="执行未完成";result.textContent=error.message||"未知错误"}}
$("skillSearch").oninput=renderSkillList;
function setSettingState(message,kind=""){ $("settingState").innerHTML=kind?'<span class="status-'+kind+'">'+esc(message)+"</span>":esc(message) }
function renderModelConfig(data){latestModelConfig=data;const credentials=data.credentials||[],configured=credentials.filter(item=>item.configured).length,required=credentials.filter(item=>item.required).length,meta=[`<span>${esc(data.file_name||"model_config.json")}</span>`,`<span>${configured}/${required} 组凭据已就绪</span>`,...credentials.map(item=>`<span class="${item.configured?"configured":"missing"}">${esc(item.provider_name||item.provider_id)} · ${esc(item.storage)}</span>`)].join("");$("modelConfigMeta").innerHTML=meta;$("modelConfigView").textContent=data.content||"配置文件为空"}
async function refreshSettings(){try{await syncProviderChoices(await post("/api/settings",runtimePayload()));await loadModelConfig();if(typeof renderProviderGrid==="function")renderProviderGrid();await loadProviderMarket()}catch(error){toast(error.message||"刷新失败","bad")}}
async function loadModelConfig(){try{const data=await jf("/api/model-config");renderModelConfig(data);return data}catch(error){$("modelConfigMeta").textContent="读取模型配置文件失败："+(error.message||"未知错误");$("modelConfigView").textContent="";return null}}
$("refreshModelConfig").onclick=async()=>{const button=$("refreshModelConfig"),old=button.textContent;button.disabled=true;button.textContent="正在读取…";try{await loadModelConfig();toast("模型配置文件已刷新")}finally{button.disabled=false;button.textContent=old}};
$("modelConfigDetails").ontoggle=()=>{if($("modelConfigDetails").open)loadModelConfig()};
$("copyModelConfig").onclick=async()=>{const content=latestModelConfig?.content||$("modelConfigView").textContent;try{await navigator.clipboard.writeText(content);toast("已复制不含 API Key 的安全配置")}catch(_){toast("复制失败，请在配置内容中手动复制","bad")}};

const AGENT_ROLE_FALLBACK=[
  {role_id:"retriever",name:"检索员",icon:"🔍",summary:"覆盖 arXiv / Scholar / 本地文献库的检索与下载",skill_names:["arxiv_search","scholar_search","downloader","library_rag","memory_search"],primary_skills:["arxiv_search","downloader","library_rag"],missing:[]},
  {role_id:"reader",name:"阅读员",icon:"📖",summary:"为单篇或一批论文生成结构化摘要与摘录",skill_names:["paper_summarize","paper_summarize_batch"],primary_skills:["paper_summarize","paper_summarize_batch"],missing:[]},
  {role_id:"citation_checker",name:"引用核验员",icon:"🔗",summary:"追溯引用、补抓 PDF、标记出入文献",skill_names:["citation_scraper","citation_analyze","citation","memory_write"],primary_skills:["citation_scraper","citation_analyze"],missing:[]},
  {role_id:"editor",name:"综述编辑",icon:"🧩",summary:"对比多份结论、输出可读的研究报告",skill_names:["paper_compare","report_render","report_write","memory_write"],primary_skills:["report_render","report_write"],missing:[]}
];
async function loadAgentRoles(){const box=$("roleGrid");if(!box)return;const fallback=AGENT_ROLE_FALLBACK;let roles=null;try{const data=await jf("/api/agent-roles");roles=data.roles||[]}catch(error){console.warn("agent-roles fallback",error);roles=fallback}if(!roles||!roles.length){box.innerHTML='<div class="role-empty">尚未配置研究角色</div>';return}box.innerHTML=roles.map(role=>{const skillsHtml=(role.skill_names||[]).slice(0,4).map(name=>{const isPrimary=(role.primary_skills||[]).indexOf(name)!==-1;return '<span class="role-skill'+(isPrimary?" primary":"")+'">'+esc(name)+'</span>'}).join("");const missing=role.missing&&role.missing.length?'<small style="color:#f7c978">⚠ '+esc(role.missing[0])+'</small>':"";return '<div class="role-card" onclick="openRoleDetail(\''+esc(role.role_id)+'\')"><b><span class="role-icon">'+esc(role.icon||"")+'</span> '+esc(role.name)+'</b><span>'+esc(role.summary)+'</span><div class="role-skills">'+skillsHtml+'</div>'+missing+'</div>'}).join("")}
function openRoleDetail(roleId){toast("正在查看角色："+roleId+"，可在「技能中心」展开其绑定的 Skill 详情。","good")}
const providerById=id=>providerProfiles.find(item=>item.id===id);
const providerInitials=name=>String(name||"AI").replace(/[^A-Za-z0-9\u4e00-\u9fff]/g,"").slice(0,2).toUpperCase()||"AI";
const providerVerifications=new Map();
const providerFingerprint=(profile,model=profile?.default_model||"")=>[profile?.id,profile?.kind,profile?.base_url,model,!!profile?.requires_api_key].join("|");
const providerVerification=profile=>{const saved=providerVerifications.get(profile?.id);return saved&&saved.fingerprint===providerFingerprint(profile)?saved.result:null};
function providerOptions(globalLabel="按全局设置"){return '<option value="auto">'+globalLabel+'</option>'+providerProfiles.map(item=>'<option value="'+esc(item.id)+'">'+esc(item.name)+'</option>').join("")}
function renderProviderGrid(){const active=$("setProvider").value;$("providerGrid").innerHTML=providerProfiles.length?providerProfiles.map(item=>{const result=providerVerification(item),model=item.default_model||"尚未选择模型",state=result?(result.verified?"ready":"failed"):"",label=result?(result.verified?"已真实验证":"验证失败"):(!item.requires_api_key?"待验证本地模型":item.has_api_key?"已填 Key · 待验证":"等待 Key");return '<button type="button" title="单击选中为默认 · 双击编辑" class="provider-profile '+(active===item.id?'selected':'')+'" data-accent="'+esc(item.accent||"blue")+'" onclick="providerCardClick(\''+item.id+'\')" ondblclick="providerCardDblClick(\''+item.id+'\')"><span class="provider-profile-head"><span class="provider-avatar">'+esc(providerInitials(item.name))+'</span><span><b>'+esc(item.name)+'</b><small>'+esc(item.base_url)+'</small></span></span><span class="provider-profile-foot"><span class="provider-status '+state+'">'+esc(label)+'</span><span class="provider-model-count">'+esc(model)+' · '+(item.models||[]).length+' 个模型</span></span></button>'}).join(""):'<div class="provider-empty">还没有模型服务商</div>'}
function syncProviderChoices(settings=lastPublicSettings){providerProfiles=(settings?.provider_profiles||[]).map(item=>({...item,models:[...(item.models||[])]}));const current=settings?.provider||"auto";$("setProvider").innerHTML=providerOptions("智能选择");$("setProvider").value=["auto",...providerProfiles.map(x=>x.id)].includes(current)?current:"auto";$("prov").innerHTML=providerOptions();$("compareProvider").innerHTML=providerOptions();const profile=providerById($("setProvider").value),models=profile?.models||providerProfiles.flatMap(item=>item.models||[]);$("modelList").innerHTML=[...new Set(models)].map(model=>'<option value="'+esc(model)+'">').join("");$("setModel").disabled=!profile;$("setModel").placeholder=profile?"选择或输入模型名称":"由服务商档案自动决定";$("setModel").value=profile?(settings?.model||profile.default_model||""):"";renderProviderGrid()}
function renderTestStatus(result,profile){const box=$("keyStatus"),ok=!!result?.verified,name=result?.provider_name||profile?.name||"模型服务",model=result?.checked_model||result?.model||profile?.default_model||"未选择模型",latency=result?.latency_ms!=null?" · "+result.latency_ms+"ms":"",stages=(result?.stages||[]).map(stage=>'<span class="verification-stage '+esc(stage.state||"")+'" title="'+esc(stage.detail||"")+'">'+esc(stage.label||"验证步骤")+'</span>').join("");box.dataset.state=ok?"ready":"error";box.innerHTML='<i></i><div><b>'+(ok?"已通过真实模型验证":"模型验证未通过")+'</b><small>'+esc((result?.reason||"请检查配置后重试。")+latency)+'</small><small class="verification-meta">'+esc(name+" · "+model+" · 真实推理请求")+'</small>'+(stages?'<div class="verification-stages">'+stages+'</div>':"")+'</div>'}
function renderKeyStatus(profile=providerById(editingProviderId),notice=""){const box=$("keyStatus");if(!profile){box.dataset.state="empty";box.innerHTML='<i></i><div><b>尚未选择服务商</b><small>选择卡片或添加新服务商后配置凭据。</small></div>';return}const verified=providerVerification(profile);if(verified){renderTestStatus(verified,profile);return}const has=!!profile.has_api_key,source=profile.api_key_source||"none";let title,detail,state;if(!profile.requires_api_key){title="本地模型尚未验证";detail=notice||"点击“真实测试”确认本地服务与模型均可推理。";state="empty"}else if(has){title=profile.name+" API Key 已配置，尚未验证";detail=notice||(source==="environment"?"凭据来自环境变量。请执行真实测试确认该模型可调用。":window.agent?.saveProviderSecrets?"凭据已使用系统安全存储加密保存。请执行真实测试确认模型可调用。":"凭据仅在当前 Web 服务进程内生效。请执行真实测试确认模型可调用。");state="empty"}else{title=profile.name+" 尚未配置 API Key";detail="输入后可先真实测试；Key 不会写入普通设置文件。";state="empty"}box.dataset.state=state;box.innerHTML='<i></i><div><b>'+esc(title)+'</b><small>'+esc(detail)+'</small></div>';$("setKey").placeholder=has?"已配置；输入新 Key 可替换":"输入 API Key"}
function showKeyError(message){const box=$("keyStatus");box.dataset.state="error";box.innerHTML='<i></i><div><b>模型配置或凭据有误</b><small>'+esc(message)+'</small></div>'}
function showKeyTesting(profile){const box=$("keyStatus");box.dataset.state="saving";box.innerHTML='<i></i><div><b>正在真实验证 '+esc(profile?.default_model||"模型")+'</b><small>正在发送一次最多 1 个输出 token 的推理请求；请勿关闭此页面。</small></div>'}
function openProviderEditor(profile){editingProviderId=profile?.id||("custom-"+Date.now().toString(36));const custom=!profile;$("providerEditorTitle").textContent=custom?"添加模型服务商":"编辑 "+profile.name;$("providerId").value=editingProviderId;$("providerName").value=profile?.name||"";$("providerKind").value=profile?.kind||"openai";$("providerBaseUrl").value=profile?.base_url||"";$("providerDefaultModel").value=profile?.default_model||"";$("providerEnv").value=profile?.api_key_env||"";$("providerModels").value=(profile?.models||[]).join("\n");$("providerRequiresKey").checked=profile?!!profile.requires_api_key:true;$("setKey").value="";$("deleteProvider").style.display=profile&&!profile.builtin?"inline-flex":"none";$("providerEditor").classList.add("on");renderKeyStatus(profile);setTimeout(()=>$("providerName").focus(),0)}
function editProvider(id){const profile=providerById(id);if(profile)openProviderEditor(profile)}
let providerCardClickTimer=null;
function providerCardClick(id){clearTimeout(providerCardClickTimer);providerCardClickTimer=setTimeout(()=>{providerCardClickTimer=null;selectProviderCard(id)},320)}
function providerCardDblClick(id){clearTimeout(providerCardClickTimer);providerCardClickTimer=null;editProvider(id)}
async function selectProviderCard(id){const profile=providerById(id);if(!profile)return;$("setProvider").value=id;$("setModel").value=profile.default_model||profile.models?.[0]||"";try{const settings=await post("/api/settings",runtimePayload());lastPublicSettings=settings;syncProviderChoices(settings);await loadModelConfig();setSettingState("已将 "+profile.name+"设为默认服务商","good")}catch(error){toast(error.message||"选择服务商失败","bad")}}
function closeProviderEditor(){$("providerEditor").classList.remove("on");editingProviderId="";$("setKey").value=""}
$("newProvider").onclick=()=>openProviderEditor(null);$("closeProviderEditor").onclick=closeProviderEditor;
$("providerKind").onchange=()=>{if($("providerKind").value==="ollama"){$("providerRequiresKey").checked=false;if(!$("providerBaseUrl").value)$("providerBaseUrl").value="http://localhost:11434/v1"}};
function runtimePayload(extra={}){return{provider:$("setProvider").value,model:$("setModel").value.trim(),provider_profiles:providerProfiles,llm_timeout:+$("setTimeout").value,download_interval:+$("downloadInterval").value,download_retries:+$("downloadRetries").value,download_timeout:+$("downloadTimeout").value,...extra}}
async function storeDesktopSecrets(){if(!window.agent?.saveProviderSecrets)return true;return await window.agent.saveProviderSecrets(providerSecrets)}
function providerDraft(){const id=$("providerId").value.trim().toLowerCase(),name=$("providerName").value.trim(),models=$("providerModels").value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean),defaultModel=$("providerDefaultModel").value.trim(),existing=providerById(id);if(!name)throw new Error("请填写服务商名称");if(!$("providerBaseUrl").value.trim())throw new Error("请填写 API Base URL");if(defaultModel&&!models.some(item=>item.toLowerCase()===defaultModel.toLowerCase()))models.unshift(defaultModel);return{id,name,kind:$("providerKind").value,base_url:$("providerBaseUrl").value.trim(),default_model:defaultModel,models,api_key_env:$("providerEnv").value.trim(),requires_api_key:$("providerRequiresKey").checked,builtin:!!existing?.builtin,accent:existing?.accent||"blue"}}
async function persistProvider(keepOpen=true){const profile=providerDraft(),id=profile.id,name=profile.name,next=providerProfiles.filter(item=>item.id!==id);next.push(profile);providerProfiles=next;$("setProvider").innerHTML=providerOptions("智能选择");$("setProvider").value=id;$("setModel").value=profile.default_model||"";const key=$("setKey").value.trim(),verified=providerVerifications.get(id),extra={};if(key){extra.api_keys={[id]:key};if(window.agent?.saveProviderSecrets)extra.credential_storages={[id]:"electron_safe_storage"};providerSecrets[id]=key;if(!(verified&&verified.usesEnteredKey&&verified.fingerprint===providerFingerprint(profile)))providerVerifications.delete(id)}const settings=await post("/api/settings",runtimePayload(extra));if(key&&!await storeDesktopSecrets()){delete providerSecrets[id];await post("/api/settings",{api_keys:{[id]:""}});throw new Error("系统安全存储不可用，未保留该 API Key")};lastPublicSettings=settings;$("setKey").value="";syncProviderChoices(settings);await loadModelConfig();if(keepOpen)editProvider(id);else closeProviderEditor();toast("已保存 "+name+" 的模型配置");return settings}
$("providerEditor").onsubmit=async event=>{event.preventDefault();const button=$("saveProvider"),old=button.textContent;button.disabled=true;button.textContent="正在保存…";try{await persistProvider(true)}catch(error){showKeyError(error.message||"保存失败");toast(error.message||"保存失败","bad")}finally{button.disabled=false;button.textContent=old}};
async function saveSettings(){const button=$("saveSettings"),old=button.textContent;button.disabled=true;button.textContent="正在保存…";try{const settings=await post("/api/settings",runtimePayload());lastPublicSettings=settings;syncProviderChoices(settings);await loadModelConfig();setSettingState("运行设置已保存","good");toast("运行设置已保存");await refresh();return true}catch(error){setSettingState(error.message||"保存失败","bad");toast(error.message||"保存失败","bad");return false}finally{button.disabled=false;button.textContent=old}}
$("saveSettings").onclick=saveSettings;
$("setProvider").onchange=()=>{const profile=providerById($("setProvider").value);if(profile)$("setModel").value=profile.default_model||profile.models?.[0]||"";syncProviderChoices({...lastPublicSettings,provider:$("setProvider").value,model:$("setModel").value})};
$("clearKey").onclick=async()=>{const profile=providerById(editingProviderId);if(!profile||!confirm("清除 "+profile.name+" 的 API Key？"))return;try{delete providerSecrets[profile.id];providerVerifications.delete(profile.id);if(!await storeDesktopSecrets())throw new Error("系统安全存储不可用");const settings=await post("/api/settings",{api_keys:{[profile.id]:""}});lastPublicSettings=settings;syncProviderChoices(settings);await loadModelConfig();editProvider(profile.id);toast("已清除 "+profile.name+" 的 API Key")}catch(error){showKeyError(error.message||"清除失败")}};
$("refreshModels").onclick=async()=>{const profile=providerById(editingProviderId);if(!profile){toast("请先保存服务商","bad");return}const button=$("refreshModels"),old=button.textContent;button.disabled=true;button.textContent="正在读取…";try{if($("setKey").value.trim())await persistProvider(true);const result=await jf("/api/models?provider="+encodeURIComponent(profile.id),{timeout:15000}),names=(result.models||[]).map(item=>item.name).filter(Boolean);if(!names.length)throw new Error("服务未返回模型列表，请手动填写模型名称");const current=providerById(profile.id);current.models=[...new Set([...(current.models||[]),...names])];if(!current.default_model)current.default_model=current.models[0];const settings=await post("/api/settings",runtimePayload());lastPublicSettings=settings;syncProviderChoices(settings);await loadModelConfig();editProvider(profile.id);toast("已读取 "+names.length+" 个模型")}catch(error){toast(error.message||"模型读取失败","bad")}finally{button.disabled=false;button.textContent=old}};
$("deleteProvider").onclick=async()=>{const profile=providerById(editingProviderId);if(!profile||profile.builtin||!confirm("删除自定义服务商 “"+profile.name+"”？"))return;try{providerProfiles=providerProfiles.filter(item=>item.id!==profile.id);delete providerSecrets[profile.id];await storeDesktopSecrets();const provider=$("setProvider").value===profile.id?"auto":$("setProvider").value,settings=await post("/api/settings",runtimePayload({provider}));lastPublicSettings=settings;syncProviderChoices(settings);await loadModelConfig();closeProviderEditor();toast("已删除 "+profile.name)}catch(error){toast(error.message||"删除失败","bad")}};
let providerCheckSequence=0;
const providerCheckTimeout=()=>Math.max(30000,Math.min(1805000,(Number($("setTimeout").value)||90)*1000+5000));
async function checkProviderConnection(button,target){const checkId=++providerCheckSequence,old=button.textContent;button.disabled=true;button.textContent="真实验证中…";try{const checkModel=(target.model||"").trim(),tempKey=String(target.tempKey||"").trim();let provider;if(tempKey&&target.id&&target.id!=="auto"){const profile=providerById(target.id);if(!profile)throw new Error("未找到该服务商配置");const draft={id:profile.id,name:profile.name,kind:profile.kind||"openai",base_url:profile.base_url,default_model:checkModel||profile.default_model||"",models:profile.models||[],api_key_env:profile.api_key_env||"",requires_api_key:profile.requires_api_key!==false,builtin:!!profile.builtin,accent:profile.accent||"blue"};provider=await post("/api/provider-test",{profile:draft,model:draft.default_model,api_key:tempKey},{timeout:providerCheckTimeout()})}else{const checkUrl="/api/provider?id="+encodeURIComponent(target.id)+"&verify=1"+(checkModel?"&model="+encodeURIComponent(checkModel):"");provider=await jf(checkUrl,{timeout:providerCheckTimeout()})}if(checkId!==providerCheckSequence)return;const returnedId=provider.profile||provider.provider;if(target.id!=="auto"&&returnedId!==target.id)throw new Error("检测结果与当前服务商不一致，请重试");const checkedModel=provider.checked_model||provider.model||checkModel||"未选择模型",name=provider.provider_name||target.name||pn(provider.provider),latency=provider.latency_ms!=null?" · "+provider.latency_ms+"ms":"",ok=provider.available&&provider.verified;let message=ok?"已通过真实模型验证："+name+" · "+checkedModel+latency:(provider.reason||"配置未就绪");if(!ok&&tempKey&&/未配置|api key|无效|过期|未识别/i.test(provider.reason||"")){message="临时 Key 验证未通过："+(provider.reason||"请检查 Key 拼写与服务商权限")}const profile=providerById(returnedId);if(profile)providerVerifications.set(profile.id,{fingerprint:providerFingerprint(profile,checkedModel),result:provider,usesEnteredKey:!!tempKey});renderProviderGrid();setSettingState(message,ok?"good":"bad");toast(message,ok?"good":"bad")}catch(error){if(checkId!==providerCheckSequence)return;const message=error.message||"状态检查失败";setSettingState(message,"bad");toast(message,"bad")}finally{if(checkId===providerCheckSequence){button.disabled=false;button.textContent=old}}}
async function testEditedProvider(){const button=$("checkEditedProvider"),old=button.textContent;let draft;try{draft=providerDraft()}catch(error){showKeyError(error.message||"请完善配置");return}const key=$("setKey").value.trim(),payload={profile:draft,model:draft.default_model};if(key)payload.api_key=key;button.disabled=true;button.textContent="真实验证中…";showKeyTesting(draft);try{const result=await post("/api/provider-test",payload,{timeout:providerCheckTimeout()});providerVerifications.set(draft.id,{fingerprint:providerFingerprint(draft),result,usesEnteredKey:!!key});renderTestStatus(result,draft);renderProviderGrid();const message=result.verified?"模型已通过真实推理验证":"模型验证未通过："+(result.reason||"请检查配置");toast(message,result.verified?"good":"bad")}catch(error){showKeyError(error.message||"真实测试失败");toast(error.message||"真实测试失败","bad")}finally{button.disabled=false;button.textContent=old}}
$("checkProvider").onclick=()=>{const id=$("setProvider").value;const tempKey=($("providerEditor")?.classList.contains("on")&&editingProviderId===id)?$("setKey").value.trim():"";checkProviderConnection($("checkProvider"),{id,model:$("setModel").value,name:providerById(id)?.name||"智能选择",tempKey})};
$("checkEditedProvider").onclick=testEditedProvider;
const jobStatusName=s=>({queued:"排队中",running:"运行中",paused:"已暂停",interrupted:"待恢复",restarting:"重新调度中",cancelling:"取消中",cancelled:"已取消",done:"已完成",error:"失败"})[s]||s;
const fmtDuration=s=>{s=Math.max(0,Number(s||0));if(s<60)return Math.floor(s)+"秒";const m=Math.floor(s/60),r=Math.floor(s%60);return m+"分"+(r?r+"秒":"")};
function traceItem(event){const data=event.data&&Object.keys(event.data).length?`<pre>${esc(JSON.stringify(event.data,null,2))}</pre>`:"",detail=event.detail?`<p>${esc(event.detail)}</p>`:"";return `<details class="trace-item"><summary><b>${esc(event.title||"执行事件")}</b><span>${esc(event.kind||"event")}</span><time>${esc(event.at||"")}</time></summary>${detail}${data}</details>`}
function interventionForm(j){if(!["paused","interrupted","queued"].includes(j.status))return"";const id=esc(j.id),x=j.intervention||{},titles=(x.exclude_titles||[]).join("\n");return `<details class="job-intervention" ${j.status==="interrupted"?"open":""}><summary>人工介入后继续</summary><p class="intervention-note">可修改查询、排除已命中文献、补充检索方向。修改查询或排除文献会重置旧检查点，确保最终报告不会混入已排除或旧问题的证据；补充方向会在下一个安全边界生效。</p><div class="intervention-grid" id="intervention-${id}"><label>研究查询<input data-field="query" type="text" maxlength="500" value="${esc(j.query||"")}"></label><label>补充研究方向<input data-field="direction" type="text" maxlength="2000" value="${esc(x.research_direction||"")}" placeholder="例如：重点关注可复现实验与近两年证据"></label><label class="wide">排除论文（每行一个标题）<textarea data-field="exclude" placeholder="输入需要排除的论文标题">${esc(titles)}</textarea></label></div><div class="row"><button class="btn-ghost" type="button" onclick="saveJobIntervention('${id}')" style="padding:5px 10px;font-size:11px">保存调整</button>${j.status!=="queued"?`<button class="btn-primary" type="button" onclick="controlJob('${id}','resume')" style="padding:5px 10px;font-size:11px">${j.status==="interrupted"?"从检查点恢复":"应用调整并继续"}</button>`:""}</div></details>`}
function jobCard(j){const id=j.id,active=["running","paused","cancelling","restarting"].includes(j.status),finished=["done","error","cancelled","interrupted"].includes(j.status);let ctrl="";if(j.status==="running")ctrl=`<button class="btn-ghost btn-sm" onclick="controlJob('${id}','pause')">暂停</button><button class="btn-danger btn-sm" onclick="controlJob('${id}','cancel')">取消</button>`;else if(j.status==="paused")ctrl=`<button class="btn-ghost btn-sm" onclick="controlJob('${id}','resume')">继续</button><button class="btn-danger btn-sm" onclick="controlJob('${id}','cancel')">取消</button>`;else if(j.status==="interrupted")ctrl=`<button class="btn-primary btn-sm" onclick="controlJob('${id}','resume')">恢复任务</button><button class="btn-danger btn-sm" onclick="controlJob('${id}','cancel')">取消</button>`;const view=j.report_path?`<button class="btn-ghost btn-sm" onclick="viewReport('${encodeURIComponent(j.report_path)}')">查看报告</button>`:"";const del=finished?`<button class="btn-ghost btn-sm" onclick="deleteJob('${id}')">删除</button>`:"";const logs=(j.log||[]).map(String),logText=logs.join("\n"),latest=[...logs].reverse().find(Boolean)||"等待输出…",events=j.events||[];const error=j.error?`<div class="job-error">${esc(String(j.error).split("\n")[0])}</div>`:"";const report=j.report_path?esc(String(j.report_path).split(/[\\/]/).pop()):"尚未生成",recovery=j.status==="interrupted"?`<div class="recovery-note">应用关闭前的进度、输入、检索结果、模型输出与失败记录已保存。恢复会从最后一个安全检查点重新调度。</div>`:"";return `<div class="job ${esc(j.status)}"><div class="head"><span class="badge b-${esc(j.status)} status-label">${jobStatusName(j.status)}</span><b style="font-size:13px">${esc(id)}</b><span style="font-size:12px;color:var(--text3)">${esc(j.desc)}</span><span class="job-actions">${ctrl}${view}<button class="btn-ghost btn-sm" onclick="copyJobLog('${id}',event)">复制日志</button>${del}</span></div><div class="job-stage"><strong>${esc(j.stage||"准备中")}</strong><span>${Number(j.progress||0)}%</span></div><div class="task-progress"><i style="width:${Math.max(0,Math.min(100,Number(j.progress||0)))}%"></i></div><div class="meta"><span>开始：${esc(j.started_at||j.created_at||"等待中")}</span><span>耗时：${fmtDuration(j.elapsed_seconds)}</span><span>恢复：${Number(j.resume_count||0)} 次</span><span>报告：${report}</span></div>${recovery}${error}${interventionForm(j)}<details class="job-trace"><summary>完整执行轨迹 · ${events.length} 个事件</summary><div class="trace-list">${events.length?events.map(traceItem).join(""):'<p class="empty">任务开始后会记录输入、检索结果、模型输出、失败与重试。</p>'}</div></details><details class="job-log" ${active?"open":""}><summary>过程日志 · ${logs.length} 行 <span>${esc(latest)}</span></summary><pre>${esc(logText||"(等待输出…)")}</pre></details></div>`}
function renderJobs(jobs=latestJobs){latestJobs=jobs||[];const counts={active:0,queued:0,recovery:0,done:0,issue:0};latestJobs.forEach(j=>{if(["running","paused","cancelling","restarting"].includes(j.status))counts.active++;else if(j.status==="queued")counts.queued++;else if(j.status==="interrupted")counts.recovery++;else if(j.status==="done")counts.done++;else counts.issue++});$("jobSummary").innerHTML=`<div><span>执行中</span><b class="status-good">${counts.active}</b></div><div><span>待恢复 / 排队</span><b>${counts.recovery+counts.queued}</b></div><div><span>已完成</span><b class="status-good">${counts.done}</b></div><div><span>异常 / 取消</span><b class="status-failed">${counts.issue}</b></div>`;const filter=$("jobFilter").value,keyword=$("jobSearch").value.trim().toLowerCase(),activeStates=["queued","running","paused","cancelling","restarting","interrupted"];const visible=latestJobs.filter(j=>(filter==="all"||(filter==="active"&&activeStates.includes(j.status))||(filter==="finished"&&["done","error","cancelled"].includes(j.status))||j.status===filter)&&(!keyword||`${j.id} ${j.query||""} ${j.desc||""}`.toLowerCase().includes(keyword)));$("jobs").innerHTML=visible.length?visible.map(jobCard).join(""):`<p class="empty">暂无${latestJobs.length?"匹配的":""}任务</p>`}
async function copyJobLog(id,event){const cached=latestJobs.find(x=>x.id===id);try{const j=await jf("/api/job?id="+encodeURIComponent(id));const text=(j.log||cached?.log||[]).join("\n");await navigator.clipboard.writeText(text);const button=event&&event.currentTarget;if(button){const old=button.textContent;button.textContent="已复制最新日志";setTimeout(()=>button.textContent=old,1200)}}catch(_){alert("复制失败，请展开日志手动复制。")}}
function scheduleCard(s){const st=s.enabled?"已启用":"已停用",id=esc(s.id),source=!s.sources?"全部来源":s.sources.includes("arxiv_search")?"arXiv":"Scholar / Crossref",scope=[source,s.year_from?"· "+s.year_from+" 年起":"",s.download?"· 下载 ≤"+(s.max_downloads||10):""].join("");return '<div class="job"><div class="head"><span class="badge b-'+(s.enabled?"done":"queued")+'">'+st+'</span><b>'+esc(s.query)+'</b></div><div class="meta">每 '+s.interval_minutes+' 分钟 \u00b7 '+(s.mode==="deep"?"深度":"单轮")+' · '+esc(scope)+' · 上次：'+(s.last_run||"未执行")+'</div><div class="schedule-actions"><button class="btn-ghost" onclick="runSchedule(&#39;'+id+'&#39;)" style="padding:4px 10px;font-size:11px">立即执行</button><button class="btn-ghost" onclick="toggleSchedule(&#39;'+id+'&#39;,'+(!s.enabled)+')" style="padding:4px 10px;font-size:11px">'+(s.enabled?"停用":"启用")+'</button><button class="btn-danger" onclick="deleteSchedule(&#39;'+id+'&#39;)" style="padding:4px 10px;font-size:11px">删除计划</button></div></div>'}
let activeMemoryQuery="",memorySearchTimer=0;
function memoryCard(m){const q=encodeURIComponent(m.query),state=(m.pinned?'<span class="memory-pill pin">已固定</span>':"")+(m.archived?'<span class="memory-pill archive">已归档</span>':"")+(m.expired?'<span class="memory-pill expired">待整理</span>':""),score=m.score!=null?'<span class="memory-score">相关 '+Math.round(Number(m.score)*100)+'%</span>':"",terms=(m.matched_terms||[]).slice(0,3).map(term=>'<span>'+esc(term)+'</span>').join("");return '<button class="memory-item'+(activeMemoryQuery===m.query?' on':'')+'" data-memory-query="'+q+'" aria-pressed="'+(activeMemoryQuery===m.query)+'" type="button" onclick="viewMemory(&#39;'+q+'&#39;)"><div class="memory-item-head"><b>'+esc(m.query)+'</b><span>'+state+score+'</span></div><div class="memory-item-meta">'+esc(m.updated_at||m.timestamp)+" · "+Number(m.paper_count||0)+" 篇论文 · 复用 "+Number(m.reuse_count||0)+" 次</div>"+(terms?'<div class="memory-terms">'+terms+'</div>':"")+"</button>"}
async function controlJob(id,action){await post("/api/job-control",{id,action});refresh()}
async function saveJobIntervention(id){const root=$("intervention-"+id);if(!root)return;const query=root.querySelector('[data-field="query"]').value.trim(),research_direction=root.querySelector('[data-field="direction"]').value.trim(),exclude_titles=root.querySelector('[data-field="exclude"]').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);try{await post("/api/job-intervention",{id,query,research_direction,exclude_titles});toast("人工调整已保存；继续后将在安全检查点生效","good");refresh()}catch(error){toast(error.message||"保存调整失败","bad")}}
async function deleteJob(id){if(!confirm("删除任务记录？"))return;await post("/api/job-delete",{id});refresh()}
async function clearFinishedJobs(){if(!confirm("清空已完成任务？"))return;await post("/api/jobs-clear",{});refresh()}
async function runSchedule(id){await post("/api/schedule-run",{id});document.querySelector('[data-p="jobs"]').click()}
async function deleteSchedule(id){if(!confirm("删除计划？"))return;await post("/api/schedule-delete",{id});refresh()}
async function toggleSchedule(id,enabled){const all=await jf("/api/schedules"),s=all.find(x=>x.id===id);if(s)await post("/api/schedules",{...s,enabled});refresh()}
$("scheduleForm").onsubmit=async e=>{e.preventDefault();await post("/api/schedules",{query:$("sq").value,mode:$("smode").value,interval_minutes:+$("sinterval").value,max_results:+$("smr").value,rounds:+$("srounds").value,branching:+$("sbranch").value,max_queries:+$("squeries").value,sources:selectedSources("ssource"),year_from:$("syear").value||null,summarize_limit:$("ssummary").value||null,analyze_citations:$("scitations").checked,download:$("sdownload").checked,max_downloads:+$("smaxDownloads").value,enabled:true});$("sq").value="";refresh()};
function reportTime(r){return String(r.modified||"")}
function reportLabel(r){const n=String(r.name||"").replace(/\.md$/i,"");if(n.startsWith("deep_report_"))return "深度研究报告";if(n.startsWith("compare_"))return "多主题对比报告";if(n.startsWith("report_"))return "研究报告";return n.replace(/[_-]+/g," ")}
function reportInline(s){return esc(s).replace(/`([^`]+)`/g,"<code>$1</code>").replace(/\*\*([^*]+)\*\*/g,"<strong>$1</strong>")}
function reportDocument(content){const lines=String(content||"").replace(/\r\n/g,"\n").split("\n");let seenTitle=false,inTable=false,headingIndex=0;const toc=[];const html=lines.map(line=>{const t=line.trim();if(!t){inTable=false;return""}if(/^\|.*\|$/.test(t)){if(/^\|[\s:|-]+\|$/.test(t))return"";const cls=inTable?"doc-table-row":"doc-table-row doc-table-head";inTable=true;return'<div class="'+cls+'">'+t.split("|").slice(1,-1).map(cell=>"<span>"+reportInline(cell.trim())+"</span>").join("")+"</div>"}inTable=false;if(/^---+$/.test(t))return'<div class="doc-divider"></div>';if(/^>\s?/.test(t))return'<div class="doc-quote">'+reportInline(t.replace(/^>\s?/,""))+"</div>";const h=t.match(/^(#{1,3})\s+(.+)$/);if(h){const level=h[1].length,plain=h[2].replace(/[*`]/g,"").trim(),text=reportInline(h[2]),id="report-section-"+(++headingIndex);if(level===1&&!seenTitle){seenTitle=true;return'<div class="doc-kicker">深度研究报告</div><h1 id="'+id+'">'+text+"</h1>"}toc.push({id,text:plain,level:Math.min(2,level)});return"<h"+(level===1?2:level)+' id="'+id+'">'+text+"</h"+(level===1?2:level)+">"}const bullet=t.match(/^[-*+]\s+(.+)$/);if(bullet)return'<div class="doc-bullet"><i>•</i><div>'+reportInline(bullet[1])+"</div></div>";const number=t.match(/^(\d+)\.\s+(.+)$/);if(number)return'<div class="doc-number"><i>'+number[1]+".</i><div>"+reportInline(number[2])+"</div></div>";return"<p>"+reportInline(t)+"</p>"}).join("");return {html:html?html+'<div class="doc-end"><span>报告结束</span></div>':'<div class="doc-empty"><div><b>报告内容为空</b>请返回任务中心检查研究执行情况。</div></div>',toc}}
function reportItem(r) {
  const raw = encodeURIComponent(r.path), selected = activeReportPath === r.path, label = reportLabel(r), versions = Number(r.version_count || 0);
  return '<div class="report-item'+(selected?' on':'')+'"><button class="report-item-open" type="button" id="report-open-'+raw+'" title="'+esc(r.name)+'" aria-pressed="'+selected+'" data-report-path="'+raw+'"><b>'+esc(label)+'</b><span>'+esc(r.modified||'未知时间')+(versions?' · '+versions+' 个版本':'')+'</span></button><div class="report-item-actions"><button class="btn-ghost report-delete" type="button" id="report-delete-'+raw+'" title="删除报告" data-report-delete="'+raw+'" aria-label="删除 '+esc(label)+'">'+uiIcon('trash')+'</button></div></div>';
}
function renderReports(reports=latestReports,preserveScroll=true){const list=$("reportList"),previousTop=preserveScroll?list.scrollTop:0;latestReports=reports||[];const query=$("reportSearch").value.trim().toLowerCase(),sort=$("reportSort").value;const visible=latestReports.filter(r=>!query||(String(r.name||"")+" "+reportLabel(r)).toLowerCase().includes(query)).sort((a,b)=>sort==="name"?reportLabel(a).localeCompare(reportLabel(b),"zh"):sort==="old"?reportTime(a).localeCompare(reportTime(b)):reportTime(b).localeCompare(reportTime(a)));const shown=visible.slice(0,reportVisibleLimit);$("reportCount").textContent=latestReports.length?"共 "+latestReports.length+" 份报告 · 已显示 "+shown.length+" 份":"尚未生成报告";const markup=visible.length?shown.map(reportItem).join("")+(visible.length>shown.length?'<button class="report-load-more" type="button" onclick="loadMoreReports()">继续向下滚动 · 剩余 '+(visible.length-shown.length)+' 份</button>':""):'<p class="empty">'+(latestReports.length?"没有匹配的报告":"暂无报告")+"</p>";renderStableList(list,markup,preserveScroll);if(preserveScroll)requestAnimationFrame(()=>{list.scrollTop=Math.min(previousTop,Math.max(0,list.scrollHeight-list.clientHeight))})}
function loadMoreReports(){const list=$("reportList");if(!list.querySelector(".report-load-more"))return;const previousTop=list.scrollTop;reportVisibleLimit+=30;renderReports(latestReports,true);requestAnimationFrame(()=>{list.scrollTop=previousTop})}
function reportLoading() {
  setReportActions(false);
  document.querySelectorAll('#p-reports details[open]').forEach(menu => menu.open = false);
  $('reportTitle').textContent = '正在打开报告…'; $('reportMeta').textContent = '正在从本地加载完整正文，请稍候。';
  $('reportBody').innerHTML = '<div class="report-loading"><div><i></i>正在加载报告内容</div></div>';
  $('reportProgress').style.width = '0'; $('reportToc').innerHTML = '<span>正在读取章节…</span>';
  $('reportSourceJumps').innerHTML = '<span>正在匹配关联文献…</span>'; $('reportVersionsSummary').textContent = '版本历史';
}
async function fetchReport(path){const ctl=new AbortController(),timer=setTimeout(()=>ctl.abort(),30000);try{const response=await fetch("/api/report?path="+encodeURIComponent(path),{signal:ctl.signal});if(!response.ok)throw new Error("HTTP "+response.status);return await response.json()}finally{clearTimeout(timer)}}
function reportLibraryMatches(content){const lower=String(content||"").toLowerCase(),matches=[];for(const batch of latestLibrary.batches||[]){for(const item of batch.items||[]){const title=String(item.title||"").trim();if(title.length>3&&lower.includes(title.toLowerCase()))matches.push({run_id:batch.run_id,index:item.index,title})}}return matches.slice(0,8)}
function renderReportSourceJumps(content){const matches=reportLibraryMatches(content);$("reportSourceJumps").innerHTML=matches.length?'<span>已下载文献</span>'+matches.map(item=>'<button class="btn-ghost" type="button" data-report-source-run="'+esc(item.run_id)+'" data-report-source-index="'+Number(item.index)+'">'+esc(item.title)+'</button>').join(""):'<span>报告中的文献尚未下载到本地库</span>'}
async function loadReportVersions(path){const box=$("reportVersions");box.innerHTML='<span class="empty">正在读取版本…</span>';try{const versions=await jf("/api/report-versions?path="+encodeURIComponent(path));if(path!==activeReportPath)return;$("reportVersionsSummary").textContent="版本历史 · "+versions.length;box.innerHTML=versions.length?versions.map(v=>'<div class="report-version-row"><b>'+esc(v.label||"版本快照")+'</b><span>'+esc(v.created_at||"")+' · '+Number(v.size||0).toLocaleString()+' 字符</span><button class="btn-ghost" type="button" data-version-restore="'+esc(v.id)+'">恢复此版本</button></div>').join(""):'<span class="empty">还没有版本快照</span>'}catch(error){box.innerHTML='<span class="empty">版本历史读取失败</span>'}}
function exportActiveReport(format){if(!activeReportPath){toast("请先选择一份报告","bad");return}window.open("/api/report-export?path="+encodeURIComponent(activeReportPath)+"&format="+encodeURIComponent(format),"_blank")}
async function snapshotActiveReport(){if(!activeReportPath){toast("请先选择一份报告","bad");return}const label=prompt("为这个版本添加备注（可选）：","手动快照");if(label===null)return;try{await post("/api/report-version",{path:activeReportPath,label});toast("报告版本已保存","good");await loadReportVersions(activeReportPath);refresh()}catch(error){toast(error.message||"保存版本失败","bad")}}
async function viewReport(raw){const path=decodeURIComponent(raw),token=++reportLoadToken;activeReportPath=path;if(!$("p-reports").classList.contains("on"))document.querySelector('[data-p="reports"]').click();$("p-reports").classList.add("asset-detail-open");reportLoading();renderReports(latestReports,true);try{const r=reportCache.get(path)||await fetchReport(path);if(token!==reportLoadToken)return;if(r.error)throw new Error(r.error);reportCache.set(path,r);setReportActions(true);$("reportTitle").textContent=reportLabel({name:r.name});$("reportTitle").title=r.name;$("reportMeta").textContent="本地报告 · "+(latestReports.find(x=>x.path===path)?.modified||"已载入")+" · "+String(r.content||"").length.toLocaleString()+" 字符";const documentView=reportDocument(r.content);requestAnimationFrame(()=>{if(token!==reportLoadToken)return;$("reportBody").innerHTML=documentView.html;$("reportToc").innerHTML=documentView.toc.length?'<span>目录</span>'+documentView.toc.map(item=>'<button class="btn-ghost" type="button" data-report-anchor="'+item.id+'" data-level="'+item.level+'">'+esc(item.text)+'</button>').join(""):'<span>此报告没有可导航的章节</span>';renderReportSourceJumps(r.content);reportScroller.scrollTop=0;updateReportProgress()});loadReportVersions(path);renderReports(latestReports,true)}catch(e){if(token!==reportLoadToken)return;setReportActions(false);$("reportTitle").textContent="报告未能加载";$("reportMeta").textContent="请确认文件仍在本地报告目录中。";$("reportBody").innerHTML='<div class="report-load-error"><div>加载失败：'+esc(e.name==="AbortError"?"文件读取超时，请稍后重试。":e.message||"未知错误")+'</div></div>'}}
async function deleteReport(raw){if(!confirm("删除报告？"))return;const path=decodeURIComponent(raw);await post("/api/report-delete",{path});reportCache.delete(path);if(activeReportPath===path){activeReportPath="";reportLoadToken++;setReportActions(false);$("p-reports").classList.remove("asset-detail-open");$("reportToc").innerHTML="<span>选择报告后查看目录</span>";$("reportSourceJumps").innerHTML="<span>选择报告后查看文献</span>";$("reportVersions").innerHTML="<span>选择报告后查看版本</span>";$("reportTitle").textContent="选择一份报告开始阅读";$("reportMeta").textContent="报告会保存在本地，可在左侧搜索和管理。";$("reportBody").innerHTML='<div class="doc-empty"><div><b>你的研究报告会在这里呈现</b>从左侧选择已有报告，或启动一项新的研究。</div></div>'}refresh()}
$("reportList").onclick=e=>{const del=e.target.closest("[data-report-delete]");if(del){e.stopPropagation();deleteReport(del.dataset.reportDelete);return}const item=e.target.closest("[data-report-path]");if(item)viewReport(item.dataset.reportPath)};
$("reportList").onkeydown = event => { if (event.target.closest('[data-report-delete]')) return; const item = event.target.closest('[data-report-path]'); if (item && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); viewReport(item.dataset.reportPath); } };
$("reportList").addEventListener("scroll",()=>{const list=$("reportList");if(list.scrollTop+list.clientHeight>=list.scrollHeight-96)loadMoreReports()},{passive:true});
$("reportSearch").oninput=()=>{reportVisibleLimit=30;renderReports(latestReports,false)};$("reportSort").onchange=()=>{reportVisibleLimit=30;renderReports(latestReports,false)};
const reportScroller=$("reportBody").closest(".report");
function updateReportProgress(){const max=reportScroller.scrollHeight-reportScroller.clientHeight;$("reportProgress").style.width=(max>0?Math.min(100,reportScroller.scrollTop/max*100):0)+"%"}
reportScroller.addEventListener("scroll",updateReportProgress,{passive:true});
$("reportTop").onclick=()=>reportScroller.scrollTo({top:0,behavior:"smooth"});
$("copyReport").onclick=async()=>{try{await navigator.clipboard.writeText($("reportBody").textContent);const old=$("copyReport").textContent;$("copyReport").textContent="已复制";setTimeout(()=>$("copyReport").textContent=old,1200)}catch(_){alert("复制失败，请手动选择正文。")}};
$("revealReport").onclick=async()=>{if(!activeReportPath){alert("请先选择一份报告。");return}if(!window.agent?.revealReport){alert("浏览器模式下报告已在页面中打开；桌面版可在系统文件夹中定位该文件。");return}const ok=await window.agent.revealReport(activeReportPath);if(!ok)alert("报告文件不存在或已被移动。")};
$("reportToc").onclick=e=>{const button=e.target.closest("[data-report-anchor]");if(!button)return;const target=$(button.dataset.reportAnchor);if(target)target.scrollIntoView({behavior:"smooth",block:"start"})};
$("reportSourceJumps").onclick=e=>{const button=e.target.closest("[data-report-source-run]");if(button)openLibraryReader(button.dataset.reportSourceRun,Number(button.dataset.reportSourceIndex))};
$("reportVersions").onclick=async e=>{const button=e.target.closest("[data-version-restore]");if(!button||!activeReportPath)return;if(!confirm("恢复会覆盖当前报告，但系统会先保存恢复前自动备份。继续吗？"))return;try{await post("/api/report-version-restore",{path:activeReportPath,version_id:button.dataset.versionRestore});reportCache.delete(activeReportPath);await viewReport(encodeURIComponent(activeReportPath));toast("已恢复报告版本","good")}catch(error){toast(error.message||"恢复失败","bad")}};
$("exportReportMd").onclick=()=>exportActiveReport("markdown");$("exportReportWord").onclick=()=>exportActiveReport("docx");$("exportReportPdf").onclick=()=>exportActiveReport("pdf");$("snapshotReport").onclick=snapshotActiveReport;
$("newMemory").onclick = () => { $('memoryCreateDialog').showModal(); $('memoryQuery').focus(); };
$("cancelMemory").onclick = () => { $('memoryCreateDialog').close(); $('memoryState').textContent = ''; };
$("memoryCreate").onsubmit=async event=>{event.preventDefault();const query=$("memoryQuery").value.trim(),notes=$("memoryNotes").value.trim();let papers;try{papers=$("memoryPapers").value.split(/\r?\n/).map(line=>line.trim()).filter(Boolean).map(line=>{const [title,url,year]=line.split("|").map(x=>x.trim());if(!title)throw new Error("每条论文线索都需要标题");if(year&&(!/^\d{4}$/.test(year)||Number(year)<1800||Number(year)>2100))throw new Error("论文年份必须是 1800-2100 之间的四位数");return{title,url:url||"",source:"manual",year:year?Number(year):null}})}catch(error){$("memoryState").textContent=error.message;return}if(!papers.length&&!notes){$("memoryState").textContent="请至少填写论文线索或研究备注";return}if(!confirm("保存这条研究记忆？同名主题会被更新。"))return;$("memoryState").textContent="正在保存…";try{await post("/api/memory-write",{query,papers,summaries:[],analysis:notes?{summary:notes,gaps:[]}:null,confirmed:true});$("memoryCreate").reset();$("memoryCreateDialog").close();$("memoryState").textContent="";await refresh();viewMemory(encodeURIComponent(query))}catch(error){$("memoryState").textContent=error.message||"保存失败"}};
function memoryGraph(graph){
  const allNodes=(graph.nodes||[]);
  const nodes=allNodes.slice(0,32);
  const nodeIds=new Set(nodes.map(n=>n.id));
  const edges=(graph.edges||[]).filter(e=>nodeIds.has(e.source)&&nodeIds.has(e.target)).slice(0,80);
  if(!nodes.length)return '<p>此主题尚未有足够的结构化信息生成图谱。</p>';
  // 按类型分组并排成 4 列(主题 / 论文 / 作者-方法 / 结论-盲点)
  const groups={topic:[],paper:[],author:[],method:[],conclusion:[],gap:[]};
  nodes.forEach(n=>(groups[n.type]||groups.conclusion).push(n));
  const columns=[["topic"],["paper"],["author","method"],["conclusion","gap"]];
  const positions={};
  const colW=170,rowH=54,padX=18,padY=22,labelW=110;
  let maxRows=0;
  columns.forEach(col=>{
    const total=col.reduce((sum,t)=>sum+Math.max(groups[t]?.length||0,1),0);
    if(total>maxRows)maxRows=total;
  });
  const width=padX*2+columns.length*colW+labelW;
  const height=Math.max(240,padY*2+maxRows*rowH+30);
  columns.forEach((types,col)=>{
    let row=0;
    types.forEach(type=>{
      (groups[type]||[]).forEach(node=>{
        const x=padX+col*colW+24;
        const y=padY+row*rowH+24;
        positions[node.id]={x,y};
        row++;
      });
    });
  });
  const palette={topic:"#4f8ee8",paper:"#8b70e8",author:"#38a88a",method:"#e89a48",conclusion:"#d8619a",gap:"#e05656"};
  const typeNames={topic:"主题",paper:"论文",author:"作者",method:"方法",conclusion:"结论",gap:"盲点"};
  const lineSvg=edges.map(edge=>{
    const a=positions[edge.source],b=positions[edge.target];
    if(!a||!b)return "";
    return '<line x1="'+a.x+'" y1="'+a.y+'" x2="'+b.x+'" y2="'+b.y+'"/>';
  }).join("");
  const nodeSvg=nodes.map(node=>{
    const p=positions[node.id];
    if(!p)return "";
    const label=String(node.label||"");
    const display=label.length>20?label.slice(0,19)+"…":label;
    const fill=palette[node.type]||"#7992af";
    return '<g class="memory-node '+esc(node.type)+'"><circle cx="'+p.x+'" cy="'+p.y+'" r="9" fill="'+fill+'"></circle><text x="'+(p.x+15)+'" y="'+(p.y+1)+'" title="'+esc(label)+'">'+esc(display)+'</text></g>';
  }).join("");
  // 列头
  const colHeaders=columns.map((types,col)=>{
    const x=padX+col*colW+24;
    const typeName=types.map(t=>typeNames[t]||t).join("·");
    const sampleFill=palette[types[0]]||"#7992af";
    return '<g class="memory-col-head"><circle cx="'+(x-2)+'" cy="'+(padY-2)+'" r="4" fill="'+sampleFill+'"/><text x="'+(x+10)+'" y="'+(padY+2)+'">'+esc(typeName)+'</text></g>';
  }).join("");
  return '<svg viewBox="0 0 '+width+' '+height+'" preserveAspectRatio="xMidYMid meet" role="img" aria-label="知识图谱">'+
    '<g class="memory-edges">'+lineSvg+'</g>'+colHeaders+nodeSvg+'</svg>';
}
function setMemoryActions(m){["memoryPin","memoryArchive","memoryMerge","memoryDelete"].forEach(id=>$(id).disabled=!m);if(!m)return;$("memoryPin").textContent=m.pinned?"取消固定":"固定";$("memoryArchive").textContent=m.archived?"恢复":"归档"}
async function viewMemory(raw){const token=++memoryLoadToken;$("p-memory").classList.add("asset-detail-open");setMemoryActions(null);$("memoryTitle").textContent="正在读取记忆…";try{const m=await jf("/api/memory-entry?query="+raw);if(token!==memoryLoadToken)return;if(m.error){toast(m.error||"读取记忆失败","bad");return}activeMemoryQuery=m.query;setMemoryActions(m);syncMemorySelection();const nl="\n",analysis=m.analysis||{},papers=(m.papers||[]).map((p,i)=>(i+1)+". "+(p.title||"?")+(p.year?" ("+p.year+")":"")+(p.source?" · "+p.source:"")+(p.url?nl+"   "+p.url:"")).join(nl)||"无";const gaps=Array.isArray(analysis.gaps)?analysis.gaps.map((g,i)=>(i+1)+". "+(g.gap||g.suggested_query||JSON.stringify(g))).join(nl):"无";const summaries=(m.summaries||[]).map((s,i)=>{const x=s.summary||s;return (i+1)+". "+[x.method&&"方法："+x.method,x.contribution&&"贡献："+x.contribution,x.limitation&&"局限："+x.limitation].filter(Boolean).join("；")}).filter(Boolean).join(nl)||"无";const usage=m.usage||{},state=[m.pinned?"已固定":"未固定",m.archived?"已归档":"活跃",m.expires_at?"过期："+m.expires_at:"永不过期","复用 "+Number(usage.reuse_count||0)+" 次"].join(" · ");$("memoryTitle").textContent=m.query;$("memoryTitle").title=m.query;$("memoryContent")?.scrollTo(0,0);$("memoryMeta").textContent=state;const note=analysis.summary?"已沉淀结论"+nl+analysis.summary+nl+nl:"";$("memoryDetail").textContent=[note+"论文（"+(m.papers||[]).length+"）",papers,"","结构化摘要",summaries,"","研究盲点",gaps,m.merged_from?.length?nl+"合并来源："+m.merged_from.join("、"):""].join(nl);try{const graph=await jf("/api/memory-graph?query="+encodeURIComponent(m.query));if(token!==memoryLoadToken)return;$("memoryGraph").innerHTML=memoryGraph(graph)}catch(_){if(token!==memoryLoadToken)return;$("memoryGraph").innerHTML="<p>图谱暂时无法读取。</p>"}}catch(error){if(token!==memoryLoadToken)return;$("memoryTitle").textContent="记忆暂时无法打开";toast(error.message||"读取记忆失败","bad")}}
async function deleteMemory(raw){const query=decodeURIComponent(raw||encodeURIComponent(activeMemoryQuery));if(!query||!confirm("永久删除这条研究记忆？此操作不可恢复。"))return;await post("/api/memory-delete",{query});activeMemoryQuery="";memoryLoadToken++;$("p-memory").classList.remove("asset-detail-open");syncMemorySelection();setMemoryActions(null);$("memoryTitle").textContent="已删除";$("memoryDetail").textContent="记忆已删除。";$("memoryGraph").innerHTML="<p>选择记忆后生成关联图谱。</p>";refresh()}
$("memoryPin").onclick=async()=>{if(!activeMemoryQuery)return;try{const current=$("memoryPin").textContent!=="取消固定";await post("/api/memory-pin",{query:activeMemoryQuery,pinned:current,confirmed:true});await viewMemory(encodeURIComponent(activeMemoryQuery));refresh();toast(current?"已固定研究记忆":"已取消固定","good")}catch(error){toast(error.message||"操作失败","bad")}};
$("memoryArchive").onclick=async()=>{if(!activeMemoryQuery)return;const archive=$("memoryArchive").textContent!=="恢复";if(!confirm(archive?"归档后不会自动参与后续研究复用，仍可随时恢复。继续吗？":"恢复这条研究记忆？"))return;try{await post("/api/memory-archive",{query:activeMemoryQuery,archived:archive,confirmed:true});await viewMemory(encodeURIComponent(activeMemoryQuery));refresh();toast(archive?"已归档":"已恢复","good")}catch(error){toast(error.message||"操作失败","bad")}};
$("memoryMerge").onclick=async()=>{if(!activeMemoryQuery)return;const raw=prompt("输入要合并到当前主题的其他研究主题，每行或英文逗号一个。来源主题会归档，论文与摘要会去重保留。","");if(raw===null)return;const source_queries=raw.split(/[\n,，]/).map(x=>x.trim()).filter(Boolean);if(!source_queries.length)return;try{await post("/api/memory-merge",{target_query:activeMemoryQuery,source_queries,confirmed:true});await viewMemory(encodeURIComponent(activeMemoryQuery));refresh();toast("记忆已合并，来源主题已归档","good")}catch(error){toast(error.message||"合并失败","bad")}};
$("memoryDelete").onclick=()=>deleteMemory(encodeURIComponent(activeMemoryQuery));
$("memoryCleanup").onclick=async()=>{const raw=prompt("将未固定且长期未复用的记忆归档。输入保留天数（不会删除数据）：","180");if(raw===null)return;const max_age_days=Number(raw);if(!Number.isInteger(max_age_days)||max_age_days<1){toast("请输入至少 1 天","bad");return}if(!confirm("整理会归档超过 "+max_age_days+" 天的未固定记忆，仍可恢复。继续吗？"))return;try{const result=await post("/api/memory-cleanup",{max_age_days,confirmed:true});toast(result.count?"已归档 "+result.count+" 条长期未复用记忆":"没有需要整理的记忆","good");refresh()}catch(error){toast(error.message||"整理失败","bad")}};
$("memoryExportMd").onclick=()=>window.open("/api/memory-export?format=markdown","_blank");$("memoryExportJson").onclick=()=>window.open("/api/memory-export?format=json","_blank");
$("memorySearch").oninput=()=>{clearTimeout(memorySearchTimer);memorySearchTimer=setTimeout(refresh,220)};$("memorySearch").onsearch=()=>refresh();
$("jobFilter").onchange=()=>renderJobs();$("jobSearch").oninput=()=>renderJobs();
const fileSize=n=>n>=1048576?(n/1048576).toFixed(1)+" MB":n>=1024?(n/1024).toFixed(0)+" KB":"--";
const libraryStatus=s=>({ok:"已下载并解析",downloaded:"PDF 已下载",failed:"下载失败",unavailable:"无公开 PDF",missing:"文件缺失",deleted:"已删除"})[s]||s;
const libraryKey=(run,index)=>String(run)+":"+Number(index);
const qualityLevel=score=>score>=75?"high":score>=52?"mid":"low";
function qualityBadge(item){const q=item.quality||{},score=Number(q.score||0),tip=(q.explanation||[]).join("；");return '<span class="quality-badge '+qualityLevel(score)+'" title="'+esc(tip)+'">质量 '+score+'</span>'}
function libraryItem(runId, it) {
  const run = encodeURIComponent(runId), idx = Number(it.index || 0), raw = encodeURIComponent(it.pdf_path || ''), key = libraryKey(runId, idx);
  const checked = selectedLibraryPapers.has(key) ? ' checked' : '', menu = 'paper-menu-'+run+'-'+idx;
  const citation = Number(it.quality?.citation_count || 0), explanation = (it.quality?.explanation || []).join(' · ');
  const noPdf = it.pdf_exists ? '' : ' disabled title="此文献尚无本地 PDF"';
  const noFile = it.pdf_exists || it.text_exists ? '' : ' disabled title="没有可删除的本地文件"';
  return '<article class="paper-row"><input class="library-select" id="paper-select-'+run+'-'+idx+'" type="checkbox" data-library-select-run="'+run+'" data-library-select-index="'+idx+'" aria-label="选择 '+esc(it.title||'文献')+'"'+checked+'><div class="paper-main"><div class="paper-title">'+esc(it.title||'未命名文献')+'</div><div class="paper-meta"><span class="status-'+esc(it.status)+'">'+esc(libraryStatus(it.status))+'</span><span>'+esc(it.source||'未知来源')+'</span>'+(it.year?'<span>'+esc(it.year)+'</span>':'')+(citation?'<span>被引 '+citation+'</span>':'')+(it.pdf_exists?'<span>'+fileSize(it.size_bytes)+'</span>':'')+qualityBadge(it)+'</div>'+((explanation||it.error)?'<details class="paper-extra" id="paper-extra-'+run+'-'+idx+'"><summary>'+(it.error?'查看失败原因与评分':'查看质量评分详情')+'</summary><span class="quality-detail">'+esc(explanation||'暂无评分详情')+'</span>'+(it.error?'<div class="paper-error">'+esc(it.error)+'</div>':'')+'</details>':'')+'</div><div class="paper-actions"><button type="button" class="btn-primary btn-sm paper-action-read" id="paper-read-'+run+'-'+idx+'" onclick="openLibraryReader(\''+run+'\','+idx+')" title="阅读与批注">'+uiIcon('book')+'<span>阅读与批注</span></button><button type="button" class="icon-button paper-action-pdf"'+noPdf+' onclick="openLibraryFile(\''+raw+'\')" aria-label="外部打开 PDF" title="外部打开 PDF">'+uiIcon('external')+'</button><button type="button" class="icon-button paper-action-folder"'+noPdf+' onclick="revealLibraryFile(\''+raw+'\')" aria-label="所在文件夹" title="所在文件夹">'+uiIcon('folder')+'</button><button type="button" class="icon-button paper-action-delete"'+noFile+' onclick="deleteLibraryItem(\''+run+'\','+idx+')" aria-label="删除本地文件" title="删除本地文件">'+uiIcon('trash')+'</button></div></article>';
}
function libraryBatch(batch){const run=encodeURIComponent(batch.run_id),s=batch.stats||{},items=[...(batch.items||[])];if($("librarySort").value==="quality")items.sort((a,b)=>Number(b.quality?.score||0)-Number(a.quality?.score||0));return '<div class="library-batch"><div class="library-batch-head"><div><b title="'+esc(batch.run_id)+'">'+esc(batch.run_id)+'</b><div class="paper-meta">'+esc(batch.generated_at||"")+' · 下载 '+(s.downloaded||0)+'/'+(s.total||0)+' · 无公开 PDF '+(s.unavailable||0)+' · 失败 '+(s.failed||0)+'</div></div><div class="actions"><button type="button" title="删除整批文献" aria-label="删除整批文献" class="btn-ghost icon-button" onclick="deleteLibraryBatch(\''+run+'\')">' + uiIcon('trash') + '</button></div></div>'+(items.length?items.map(it=>libraryItem(batch.run_id,it)).join(""):'<p class="empty">当前筛选下没有文献</p>')+'</div>'}
async function openLibraryFile(raw){const path=decodeURIComponent(raw);if(window.agent?.openLibraryFile){const ok=await window.agent.openLibraryFile(path);if(!ok)alert("文件不存在或无法打开");return}window.open("/api/library-file?path="+encodeURIComponent(path),"_blank")}
async function revealLibraryFile(raw){const path=decodeURIComponent(raw);if(!window.agent?.revealLibraryFile){alert("桌面版支持在系统文件夹中定位；浏览器模式可直接打开 PDF。");return}const ok=await window.agent.revealLibraryFile(path);if(!ok)alert("文件不存在或已被移动")}
async function deleteLibraryItem(rawRun,index){if(!confirm("删除这篇文献的本地 PDF 和抽取文本？文献元数据会保留。"))return;await post("/api/library-item-delete",{run_id:decodeURIComponent(rawRun),index});refresh()}
async function deleteLibraryBatch(rawRun){if(!confirm("删除整个下载批次及其中全部 PDF、文本和清单？此操作不可恢复。"))return;await post("/api/library-batch-delete",{run_id:decodeURIComponent(rawRun)});refresh()}
function renderLibrary(){const lib=latestLibrary,ls=lib.stats||{};$("librarySummary").innerHTML='<div><span>下载批次</span><b>'+(ls.batches||0)+'</b></div><div><span>文献记录</span><b>'+(ls.items||0)+'</b></div><div><span>本地 PDF</span><b class="status-ok">'+(ls.downloaded||0)+'</b></div><div><span>无公开 PDF</span><b class="status-unavailable">'+(ls.unavailable||0)+'</b></div><div><span>下载失败</span><b class="status-failed">'+(ls.failed||0)+'</b></div>';const markup=(lib.batches||[]).length?lib.batches.map(libraryBatch).join(""):'<p class="empty">暂无下载资料。发起研究时勾选“下载公开 PDF”。</p>';renderStableList($("libraryList"),markup);updateLibrarySelection()}
function updateLibrarySelection() {
  const count = selectedLibraryPapers.size, bar = $('librarySelectionBar');
  bar.classList.toggle('on', count > 0); $('librarySelectionCount').textContent = '已选 '+count+' 篇';
  $('clearLibrarySelection').disabled = !count; $('continueLibraryResearch').disabled = !count;
  const inputs = [...$('libraryList').querySelectorAll('[data-library-select-run]')];
  let checked = 0;
  inputs.forEach(input => { input.checked = selectedLibraryPapers.has(libraryKey(decodeURIComponent(input.dataset.librarySelectRun), input.dataset.librarySelectIndex)); if(input.checked) checked++; });
  const all = $('selectAllLibrary'); all.disabled = !inputs.length; all.checked = inputs.length > 0 && checked === inputs.length; all.indeterminate = checked > 0 && checked < inputs.length;
}
$('selectAllLibrary').onchange = event => {
  $('libraryList').querySelectorAll('[data-library-select-run]').forEach(input => {
    const run = decodeURIComponent(input.dataset.librarySelectRun), index = Number(input.dataset.librarySelectIndex), key = libraryKey(run,index);
    if(event.target.checked) selectedLibraryPapers.set(key,{run_id:run,index}); else selectedLibraryPapers.delete(key);
  }); updateLibrarySelection();
};
$("libraryList").onchange=e=>{const input=e.target.closest("[data-library-select-run]");if(!input)return;const run=decodeURIComponent(input.dataset.librarySelectRun),index=Number(input.dataset.librarySelectIndex),key=libraryKey(run,index);if(input.checked)selectedLibraryPapers.set(key,{run_id:run,index});else selectedLibraryPapers.delete(key);updateLibrarySelection()};
$("clearLibrarySelection").onclick=()=>{selectedLibraryPapers.clear();renderLibrary()};$("continueLibraryResearch").onclick=async()=>{const query=prompt("基于已选文献继续研究什么？","请基于这些已有文献进行综合分析，提出共识、分歧、局限与下一步研究方向。");if(query===null)return;try{const result=await post("/api/library-continue",{query,selection:[...selectedLibraryPapers.values()]});selectedLibraryPapers.clear();updateLibrarySelection();toast("已创建已有文献续研任务","good");document.querySelector('[data-p="jobs"]').click();refresh()}catch(error){toast(error.message||"创建续研任务失败","bad")}};
$("librarySearch").onsearch=()=>refresh();$("libraryStatus").onchange=()=>refresh();$("librarySort").onchange=()=>renderLibrary();
let librarySearchTimer;$("librarySearch").oninput=()=>{clearTimeout(librarySearchTimer);librarySearchTimer=setTimeout(refresh,250)};
function readerTextHtml(text,annotations=[],find=""){const source=String(text||"");const ranges=[];for(const note of annotations){const quote=String(note.quote||"").trim();if(quote.length<2)continue;const start=source.indexOf(quote);if(start>=0)ranges.push({start,end:start+quote.length,color:note.color||"yellow",find:false})}const query=String(find||"").trim();if(query.length>1){let start=source.toLowerCase().indexOf(query.toLowerCase());if(start>=0)ranges.push({start,end:start+query.length,color:"blue",find:true})}ranges.sort((a,b)=>a.start-b.start||b.end-a.end);let end=0,html="";for(const range of ranges){if(range.start<end)continue;html+=esc(source.slice(end,range.start))+'<mark data-color="'+esc(range.color)+'"'+(range.find?' data-find-hit="true"':'')+'>'+esc(source.slice(range.start,range.end))+"</mark>";end=range.end}return html+esc(source.slice(end))}
function renderReaderText(){const doc=activeLibraryDocument,text=doc?.text_content||"",notes=doc?.annotations||[],find=$("readerFind").value;$("readerText").innerHTML=text?readerTextHtml(text,notes,find):'<div class="reader-empty">此文献没有可用的抽取文本。下载或重新解析后即可批注。</div>';const hit=$("readerText").querySelector("[data-find-hit]");if(hit)setTimeout(()=>hit.scrollIntoView({block:"center",behavior:"smooth"}),0)}
function renderReaderAnnotations(){const notes=activeLibraryDocument?.annotations||[];$("readerAnnotations").innerHTML=notes.length?notes.slice().reverse().map(note=>'<article class="reader-annotation"><blockquote>'+esc(note.quote||"仅批注 / 标签")+'</blockquote>'+(note.note?'<p>'+esc(note.note)+'</p>':"")+'<div class="reader-tags">'+(note.page?'<span>第 '+Number(note.page)+' 页</span>':"")+(note.tags||[]).map(tag=>'<span>'+esc(tag)+'</span>').join("")+'</div><div class="reader-annotation-actions"><button class="btn-ghost" type="button" data-reader-copy="'+esc(note.id)+'">复制摘录</button><button class="btn-danger" type="button" data-reader-delete="'+esc(note.id)+'">删除</button></div></article>').join(""):'<p class="empty">尚未添加高亮、批注或摘录。</p>'}
function showReaderTab(tab){activeReaderTab=tab;document.querySelectorAll("[data-reader-tab]").forEach(button=>button.classList.toggle("on",button.dataset.readerTab===tab));$("readerPdf").hidden=tab!=="pdf";$("readerText").hidden=tab!=="text";if(tab==="text")renderReaderText()}
async function openLibraryReader(rawRun,index){const run=decodeURIComponent(rawRun);try{const doc=await jf("/api/library-document?run_id="+encodeURIComponent(run)+"&index="+encodeURIComponent(index),{timeout:30000});activeLibraryDocument=doc;readerSelectedQuote="";$("libraryReaderTitle").textContent=doc.title||"未命名文献";$("libraryReaderMeta").textContent=(doc.source||"本地文献")+(doc.year?" · "+doc.year:"")+(doc.pdf_exists?" · 已保存 PDF":" · 未保存 PDF")+(doc.text_exists?" · 可批注文本":" · 无可批注文本");$("readerSelection").textContent="在“可批注文本”中选择一段文字，即可保存为高亮或摘录。";$("readerNote").value="";$("readerTags").value=(doc.tags||[]).join(", ");$("readerPage").value="";$("readerFind").value="";$("readerPdf").innerHTML=doc.pdf_exists?'<iframe title="PDF 预览" src="/api/library-file?path='+encodeURIComponent(doc.pdf_path)+'"></iframe>':'<div class="reader-empty">此记录未保存公开 PDF。你仍可阅读元数据和抽取文本，并添加标签或批注。</div>';renderReaderAnnotations();renderReaderText();$("libraryReaderModal").classList.add("open");showReaderTab(doc.text_exists?"text":"pdf")}catch(error){toast(error.message||"无法打开文献","bad")}}
$("closeLibraryReader").onclick=()=>{$("libraryReaderModal").classList.remove("open");activeLibraryDocument=null};
document.querySelectorAll("[data-reader-tab]").forEach(button=>button.onclick=()=>showReaderTab(button.dataset.readerTab));
$("readerText").addEventListener("mouseup",()=>{const selection=window.getSelection(),text=selection?.toString().trim()||"";if(!text||!selection?.anchorNode||!$("readerText").contains(selection.anchorNode))return;readerSelectedQuote=text.slice(0,4000);$("readerSelection").textContent="已选 "+readerSelectedQuote.length+" 个字符："+readerSelectedQuote});
$("readerFind").oninput=()=>renderReaderText();
$("saveReaderAnnotation").onclick=async()=>{if(!activeLibraryDocument)return;const tags=$("readerTags").value.split(/[,，]/).map(tag=>tag.trim()).filter(Boolean);try{const result=await post("/api/library-annotation",{run_id:activeLibraryDocument.run_id,index:activeLibraryDocument.index,quote:readerSelectedQuote,note:$("readerNote").value.trim(),tags,color:$("readerColor").value,page:$("readerPage").value||null});const old=activeLibraryDocument.annotations||[],incoming=result.annotation;activeLibraryDocument.annotations=old.filter(item=>item.id!==incoming.id).concat(incoming);activeLibraryDocument.tags=result.tags||activeLibraryDocument.tags;readerSelectedQuote="";$("readerSelection").textContent="已保存。继续选择文本，或留下新的阅读笔记。";$("readerNote").value="";renderReaderAnnotations();renderReaderText();toast("阅读笔记已保存到本地","good")}catch(error){toast(error.message||"保存批注失败","bad")}};
$("readerAnnotations").onclick=async e=>{const copy=e.target.closest("[data-reader-copy]"),del=e.target.closest("[data-reader-delete]");if(copy){const note=(activeLibraryDocument?.annotations||[]).find(item=>item.id===copy.dataset.readerCopy);try{await navigator.clipboard.writeText([note?.quote,note?.note].filter(Boolean).join("\n"));toast("摘录已复制","good")}catch(_){toast("复制失败，请手动选择","bad")}return}if(!del||!activeLibraryDocument||!confirm("删除这条本地批注？"))return;try{await post("/api/library-annotation-delete",{run_id:activeLibraryDocument.run_id,index:activeLibraryDocument.index,id:del.dataset.readerDelete});activeLibraryDocument.annotations=(activeLibraryDocument.annotations||[]).filter(item=>item.id!==del.dataset.readerDelete);renderReaderAnnotations();renderReaderText();toast("批注已删除","good")}catch(error){toast(error.message||"删除失败","bad")}};
async function refresh(){
  const currentRefresh = ++refreshToken;
  // 进入刷新态:banner 立刻显示「正在连接」的视觉态,避免停在过期的旧数据上
  if (!providerStatusLoaded) applyProviderStatus(null, null, true);
  const results = await Promise.allSettled([
    jf("/api/provider"),
    jf("/api/settings"),
    jf("/api/jobs"),
    jf("/api/schedules"),
    jf("/api/reports"),
    jf("/api/library?keyword="+encodeURIComponent($("librarySearch")?.value||"")
        +"&status="+encodeURIComponent($("libraryStatus")?.value||"all")),
    jf("/api/memory?keyword="+encodeURIComponent($("memorySearch")?.value||"")),
  ]);
  if (currentRefresh !== refreshToken) return;
  const [provR, settingsR, jobsR, schedulesR, reportsR, libR, meR] = results;
  providerStatusLoaded = true;

  // 1) 服务商状态 — 决定顶部 banner。失败不让其他面板卡住
  if (provR.status === "fulfilled") {
    applyProviderStatus(provR.value);
  } else {
    console.warn("provider status failed", provR.reason);
    applyProviderStatus(null, provR.reason);
  }

  // 2) 公共设置
  if (settingsR.status === "fulfilled") {
    applySettings(settingsR.value);
  } else {
    console.warn("settings load failed", settingsR.reason);
  }

  // 3) 任务队列
  if (jobsR.status === "fulfilled") {
    renderJobs(jobsR.value);
    const activeJobs = (jobsR.value||[]).filter(j=>["running","paused","cancelling"].includes(j.status)).length;
    const queuedJobs = (jobsR.value||[]).filter(j=>j.status==="queued").length;
    $("agentTaskStatus").textContent = activeJobs ? activeJobs+" 项正在执行"
        : queuedJobs ? queuedJobs+" 项等待执行" : "队列空闲";
    $("agentTaskHint").textContent = activeJobs ? "另有 "+queuedJobs+" 项等待；可随时到任务中心控制"
        : "新的研究会在此排队并显示完整过程";
  } else {
    console.warn("jobs load failed", jobsR.reason);
    $("jobs").innerHTML = renderErrorPanel("任务加载失败", jobsR.reason);
    $("agentTaskStatus").textContent = "任务状态暂不可用";
    $("agentTaskHint").textContent = "可稍候自动重试，或切换至任务中心手动刷新";
  }

  // 4) 定时计划
  if (schedulesR.status === "fulfilled") {
    const list = schedulesR.value || [];
    $("schedules").innerHTML = list.length ? list.map(scheduleCard).join("")
        : '<p class="empty">暂无计划</p>';
  } else {
    console.warn("schedules load failed", schedulesR.reason);
    $("schedules").innerHTML = renderErrorPanel("计划加载失败", schedulesR.reason);
  }

  // 5) 报告
  if (reportsR.status === "fulfilled") {
    renderReports(reportsR.value);
  } else {
    console.warn("reports load failed", reportsR.reason);
    $("reportList").innerHTML = renderErrorPanel("报告加载失败", reportsR.reason);
  }

  // 6) 文献库
  if (libR.status === "fulfilled") {
    latestLibrary = libR.value || {batches:[], stats:{}};
    renderLibrary();
  } else {
    console.warn("library load failed", libR.reason);
    latestLibrary = {batches:[], stats:{}};
    $("libraryList").innerHTML = renderErrorPanel("文献库加载失败", libR.reason);
  }

  // 7) 研究记忆
  if (meR.status === "fulfilled") {
    const me = meR.value || {};
    $("memoryStats").textContent = "本地语义检索 · 活跃 "
        + Number(me.active_entries ?? me.entries ?? 0) + " 条 · 已归档 "
        + Number(me.archived_entries || 0) + " 条 · 论文 "
        + Number(me.total_papers || 0) + " 篇";
    const items = me.items || [];
    const memoryMarkup = items.length ? items.map(memoryCard).join("")
        : '<p class="empty">暂无匹配的研究记忆</p>';
    renderStableList($("mem"), memoryMarkup); syncMemorySelection();
  } else {
    console.warn("memory load failed", meR.reason);
    $("memoryStats").textContent = "研究记忆暂不可用";
    $("mem").innerHTML = renderErrorPanel("记忆加载失败", meR.reason);
  }
}

// 将 badge 的渲染逻辑集中,便于从多处调用（refresh、初始启动、checkProvider）
function applyProviderStatus(prov, error, loading) {
  const b = $("providerBadge");
  if (!b) return;
  if (loading) {
    b.innerHTML = '<span class="live-dot"></span>正在连接';
    b.className = "loading";
    $("agentProvider") && ($("agentProvider").textContent = "正在连接模型服务…");
    $("agentProviderHint") && ($("agentProviderHint").textContent = "正在向本地服务发起握手…");
    return;
  }
  if (error || !prov) {
    b.innerHTML = '<span class="live-dot"></span>服务暂不可用';
    b.className = "off";
    $("agentProvider") && ($("agentProvider").textContent = "服务暂不可用");
    $("agentProviderHint") && ($("agentProviderHint").textContent =
        "无法连接到本地服务： " + (error?.message || "请检查 Web 服务进程"));
    $("agentConnection") && ($("agentConnection").textContent = "离线");
    $("agentConnectionHint") && ($("agentConnectionHint").textContent = "服务不可用，其它面板可能仍可使用");
    return;
  }
  const providerName = prov.provider_name || pn(prov.provider);
  const providerLabel = (providerName || "未配置") + (prov.model ? " · " + prov.model : "");
  b.innerHTML = '<span class="live-dot"></span>' + esc(providerLabel);
  b.className = prov.available ? "ok" : "off";
  $("agentProvider") && ($("agentProvider").textContent = providerLabel || "尚未配置模型");
  $("agentProviderHint") && ($("agentProviderHint").textContent = prov.available
      ? "当前服务已就绪，可开始研究" : (prov.reason || "请到设置页面完成模型连接"));
  $("agentConnection") && ($("agentConnection").textContent = prov.provider_type === "ollama"
      ? "本地 · Ollama 协议" : "云端 / 网关 · OpenAI 兼容");
  $("agentConnectionHint") && ($("agentConnectionHint").textContent = prov.automatic
      ? "当前由智能选择自动路由" : "当前使用固定服务商档案");
}

// 从公共设置中同步各面板（设置页、模型卡片网格等）
function applySettings(publicSettings) {
  lastPublicSettings = publicSettings;
  if (!settingsLoaded) {
    $("setTimeout").value = publicSettings.llm_timeout || 90;
    $("downloadInterval").value = publicSettings.download_interval || 2;
    $("downloadRetries").value = publicSettings.download_retries == null
        ? 4 : publicSettings.download_retries;
    $("downloadTimeout").value = publicSettings.download_timeout || 90;
    syncProviderChoices(publicSettings);
    settingsLoaded = true;
  } else {
    providerProfiles = (publicSettings.provider_profiles || providerProfiles)
        .map(item => ({...item, models: [...(item.models || [])]}));
    renderProviderGrid();
  }
}

// 为某个面板提供轻量错误占位，避免 Promise.allSettled 错误被静默吞掉
function renderErrorPanel(title, error) {
  const detail = (error && (error.message || String(error))) || "请稍候自动重试";
  return '<div class="empty"><b>' + esc(title) + '</b><br><small>'
      + esc(detail) + '</small></div>';
}
async function restoreProviderSecrets(){try{if(window.agent?.loadProviderSecrets){providerSecrets=await window.agent.loadProviderSecrets()||{};if(Object.keys(providerSecrets).length){const storages=Object.fromEntries(Object.keys(providerSecrets).map(id=>[id,"electron_safe_storage"])),settings=await post("/api/settings",{api_keys:providerSecrets,credential_storages:storages});lastPublicSettings=settings;syncProviderChoices(settings)}}else if(window.agent?.loadDeepSeekKey){const key=await window.agent.loadDeepSeekKey();if(key){providerSecrets={deepseek:key};await post("/api/settings",{api_keys:providerSecrets,credential_storages:{deepseek:"electron_safe_storage"}})}}}catch(error){toast("安全凭据恢复失败，请在设置中重新检查","bad")}}
restoreProviderSecrets().finally(()=>{loadModelConfig();loadAgentRoles();refresh();setInterval(()=>{const active=document.querySelector(".pane.on")?.id;if(["p-research","p-jobs","p-schedules"].includes(active))refresh()},3000)});

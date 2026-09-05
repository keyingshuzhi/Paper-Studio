// Run with: playwright-cli -s=paper-ui run-code --filename=examples/verify_workspace_ui.js
// All research, library, report and memory data below are browser-only fixtures.
// POST requests are intercepted: no real research, deletion or configuration writes.
async (page) => {
  const checks = [], errors = [], writes = [];
  const check = (name, ok, detail = '') => {
    checks.push({name, ok, detail});
    if (!ok) throw new Error(name + ': ' + JSON.stringify(detail));
  };
  page.on('pageerror', error => errors.push(error.message));
  const topics = ['长上下文模型与检索增强生成在学术研究中的证据追溯、可靠性评估与跨文献推理方法对比', 'Agent 可靠性'];
  const papers = Array.from({length:24}, (_, i) => ({index:i+1, title: i%3===0 ? 'Evidence-grounded Research Agents: A Comprehensive Study of Long-context Reasoning, Retrieval Augmentation and Reproducible Academic Workflows' : i%3===1 ? 'Retrieval-Augmented Generation for Research' : '多模型协作中的知识复用与研究质量评估', source:'arXiv', year:2026, status:i%4===3?'failed':'ok', pdf_exists:i%4!==3, text_exists:i%4!==3, pdf_path:'/ui-fixtures/paper-'+i+'.pdf', size_bytes:2050000, error:i%4===3?'下载暂时失败：上游服务返回 429，请稍候重试。':'', quality:{score:82-i, citation_count:42+i, explanation:['来源可核验','近两年文献','全文可用时纳入证据分析']}}));
  const library = {stats:{batches:1,items:24,downloaded:18,unavailable:0,failed:6},batches:[{run_id:'研究批次-20260905-8f3a',generated_at:'2026-09-05 10:00:00',stats:{total:24,downloaded:18,failed:6},items:papers}]};
  const reports = Array.from({length:72},(_,i)=>({path:'/ui-fixtures/report-'+i+'.md',name:(i===0?topics[0]:i===1?'简短报告':'研究报告 '+String(i+1).padStart(2,'0'))+'.md',modified:'2026-09-05 10:'+String(59-i%60).padStart(2,'0')+':00',version_count:2}));
  const memoryItems = Array.from({length:40},(_,i)=>({query:topics[i%2]+(i>1?' · '+i:''),timestamp:'2026-09-05 09:00:00',updated_at:'2026-09-05 09:00:00',paper_count:12,pinned:i===0,reuse_count:i,matched_terms:['证据','可靠性']}));
  const content = '# '+topics[0]+'\n\n'+papers[0].title+'\n\n'+Array.from({length:40},(_,i)=>'## '+(i+1)+'. 研究发现与证据核验\n\n'+('研究结论应回到原始文献，比较实验方法、适用场景与局限，并记录可复现条件。'.repeat(8))+'\n\n- 证据来源与研究假设\n- 对比结果与待验证问题').join('\n\n');
  await page.unroute('**/api/**');
  await page.route('**/api/**', async route => {
    const request = route.request(), [base, query = ''] = request.url().split('?');
    const params = Object.fromEntries(query.split('&').filter(Boolean).map(pair => { const at=pair.indexOf('='); return [decodeURIComponent(pair.slice(0,at)),decodeURIComponent(pair.slice(at+1))]; }));
    const url = {pathname:base.replace(/^https?:\/\/[^/]+/,''),searchParams:{get:key=>params[key]}};
    const send = data => route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
    if (request.method() !== 'GET') { writes.push({path:url.pathname,body:request.postDataJSON()}); return send({id:'ui-fixture-task'}); }
    if (url.pathname==='/api/jobs'||url.pathname==='/api/schedules') return send([]);
    if (url.pathname==='/api/library') return send(library);
    if (url.pathname==='/api/reports') return send(reports);
    if (url.pathname==='/api/report') { const r=reports.find(item=>item.path===url.searchParams.get('path'))||reports[0]; return send({name:r.name,content}); }
    if (url.pathname==='/api/report-versions') return send([{id:'v1',label:'初始版本',created_at:'2026-09-05 09:00:00',size:6000}]);
    if (url.pathname==='/api/memory') return send({entries:40,active_entries:38,archived_entries:2,total_papers:480,items:memoryItems});
    if (url.pathname==='/api/memory-entry') return send({query:url.searchParams.get('query'),pinned:url.searchParams.get('query')===topics[0],papers,analysis:{summary:'历史研究结论。'.repeat(150),gaps:[{gap:'需要更多可复现实验。'}]},summaries:papers.map(()=>({method:'对照实验',contribution:'可追溯的研究证据',limitation:'需要扩大验证范围'}))});
    if (url.pathname==='/api/memory-graph') return send({nodes:[{id:'a',type:'topic',label:'研究问题'},{id:'b',type:'paper',label:'代表文献'},{id:'c',type:'conclusion',label:'可追溯结论'}],edges:[{source:'a',target:'b'},{source:'b',target:'c'}]});
    if (url.pathname==='/api/library-document') return send({run_id:library.batches[0].run_id,index:1,title:papers[0].title,source:'arXiv',year:2026,pdf_exists:false,text_exists:true,text_content:'研究文献的可批注原文。\n'.repeat(180),tags:[],annotations:[]});
    return route.continue();
  });
  await page.emulateMedia({reducedMotion:'reduce',colorScheme:'light'});
  await page.setViewportSize({width:1440,height:1000});
  await page.goto('http://127.0.0.1:8765');
  await page.waitForFunction(()=>document.body.classList.contains('ui-ready') && !document.body.classList.contains('booting') && document.querySelectorAll('.paper-row').length===24);
  check('启动动画结束后释放页面交互',await page.locator('body').getAttribute('aria-busy')==='false' && await page.locator('#appBoot').count()===0);
  await page.evaluate(()=>applyTheme('light'));
  const nav = async name => {
    if (await page.locator('#mobileNavToggle').isVisible() && !(await page.locator('.tabs').isVisible() && await page.locator('.tabs').getAttribute('aria-hidden')!=='true')) await page.locator('#mobileNavToggle').click();
    await page.getByRole('tab',{name,exact:true}).click();
  };
  const shot = name => page.screenshot({path:'output/playwright/'+name+'.png',animations:'disabled'});
  const box = selector => page.locator(selector).first().boundingBox();
  await shot('home-light');
  await page.locator('#q').fill('验证研究提交交互');
  await page.locator('.agent-options > summary').click();
  await page.locator('#rd').fill('3');
  await page.locator('#f button[type="submit"]').click();
  await page.waitForFunction(()=>document.querySelector('#p-jobs').classList.contains('on'));
  check('首页研究表单提交后进入任务中心并保留轮次',writes.some(item=>item.path==='/api/run'&&item.body.q==='验证研究提交交互'&&item.body.rounds===3));
  await nav('研究');
  await page.locator('#q').fill('');
  if(await page.locator('.agent-options').evaluate(el=>el.open)) await page.locator('.agent-options > summary').click();
  if(await page.evaluate(()=>document.body.classList.contains('nav-collapsed'))) await page.getByRole('button',{name:'收起或展开导航'}).click();
  await page.getByRole('button',{name:'收起或展开导航'}).click();
  const collapsedChrome=await page.evaluate(()=>{
    const tabs=document.querySelector('.tabs'),active=document.querySelector('.tab.on');
    const frame=getComputedStyle(tabs),before=getComputedStyle(active,'::before'),after=getComputedStyle(active,'::after');
    return {borderRight:frame.borderRight,tabBefore:before.display,tabAfter:after.display};
  });
  check('收起侧栏没有边框或活动项竖线',collapsedChrome.borderRight.startsWith('0px')&&collapsedChrome.tabBefore==='none'&&collapsedChrome.tabAfter==='none',collapsedChrome);
  await shot('sidebar-collapsed-light');
  await page.getByRole('button',{name:'收起或展开导航'}).click();
  await page.locator('.agent-options > summary').click();
  const plus = await page.locator('.agent-options > summary').evaluate(el => {const s=getComputedStyle(el,'::before');return {content:s.content,width:s.width,height:s.height,background:s.backgroundPosition};});
  check('展开图标使用等宽高、居中的绘制区域',plus.width===plus.height && plus.background.includes('50%'),plus);
  await page.locator('.agent-options > summary').click();
  await nav('对比研究');
  const compareCardTopGap=await page.evaluate(()=>{
    const card=document.querySelector('.compare-form').getBoundingClientRect();
    const heading=document.querySelector('.compare-form .compare-section-heading').getBoundingClientRect();
    return heading.top-card.top;
  });
  check('对比研究 01 标题与卡片顶部留有舒适间距',compareCardTopGap>=25&&compareCardTopGap<=29,compareCardTopGap);
  check('未输入两个主题时不会误提交',await page.locator('#compareSubmit').isDisabled());
  await page.locator('#compareExample').click();
  check('示例同步为两个独立主题',await page.locator('#comparePreview li').count()===2 && await page.locator('#compareSubmit').isEnabled());
  await page.locator('#compareTopics').fill('主题 A\n主题 A');
  check('重复主题有明确反馈',await page.locator('#compareSubmit').isDisabled() && (await page.locator('#compareTopicHint').textContent()).includes('重复'));
  await page.locator('#compareTopics').fill(topics.join('\n'));
  await shot('compare-light');
  await page.locator('#compareSubmit').click();
  await page.waitForFunction(()=>document.querySelector('#p-jobs').classList.contains('on'));
  check('对比表单保留原研究接口及两个完整主题',writes.some(item=>item.path==='/api/compare' && item.body.topics.length===2 && item.body.topics[0]===topics[0]));
  await nav('文献库');
  await page.waitForTimeout(400);
  const actions = await page.locator('.paper-actions').evaluateAll(els=>els.slice(0,4).map(el=>({x:el.getBoundingClientRect().x,offset:el.getBoundingClientRect().y-el.closest('.paper-row').getBoundingClientRect().y})));
  check('长标题和失败文献的按钮固定同列同偏移',actions.every(a=>a.x===actions[0].x && a.offset===actions[0].offset),actions);
  const before=await box('#librarySelectionBar');
  await page.locator('.library-select').first().check();
  const after=await box('#librarySelectionBar');
  check('选择文献不改变操作栏位置与高度',Math.abs(before.y-after.y)<1 && Math.abs(before.height-after.height)<1 && await page.locator('#continueLibraryResearch').isEnabled(),{before,after});
  check('文献操作按钮固定为阅读、打开、定位和删除四项',await page.locator('.paper-actions').first().locator('button').count()===4);
  await page.locator('#clearLibrarySelection').click();
  await shot('library-light');
  await page.locator('.paper-actions > button').first().click();
  await page.waitForSelector('#libraryReaderModal.open');
  check('阅读面板打开并展示完整文本',await page.locator('#readerText').isVisible());
  await page.locator('#closeLibraryReader').click();
  await page.locator('#libraryList').evaluate(el=>el.scrollTop=el.scrollHeight);
  const libraryTop=await page.locator('#libraryList').evaluate(el=>el.scrollTop);
  await page.evaluate(()=>refresh());
  check('刷新保留文献库滚动位置',Math.abs(await page.locator('#libraryList').evaluate(el=>el.scrollTop)-libraryTop)<2);
  const bottomActions=await page.locator('.paper-actions').last().boundingBox(),libraryViewport=await box('#libraryList');
  check('底部卡片操作区保持在文献滚动视口内',bottomActions.y>=libraryViewport.y && bottomActions.y+bottomActions.height<=libraryViewport.y+libraryViewport.height+1,{bottomActions,libraryViewport});
  check('缺失文件的操作保持位置并显示禁用',await page.locator('.paper-actions').last().locator('button:disabled').count()===3);
  await nav('研究报告');
  const reportSortLayout=await page.locator('#reportSort').evaluate(el=>{const style=getComputedStyle(el),rect=el.getBoundingClientRect();return{width:rect.width,paddingRight:style.paddingRight,backgroundPosition:style.backgroundPosition};});
  check('报告排序内容与下拉箭头保留充足空间',reportSortLayout.width>=112&&parseFloat(reportSortLayout.paddingRight)>=38&&reportSortLayout.backgroundPosition.includes('50%'),reportSortLayout);
  const searchDecoration=await page.locator('.report-toolbar').evaluate(el=>{
    const pseudo=getComputedStyle(el,'::before'),input=getComputedStyle(el.querySelector('input'));
    return {display:pseudo.display,content:pseudo.content,paddingLeft:input.paddingLeft};
  });
  check('报告搜索框没有额外搜索图标',searchDecoration.display==='none'&&searchDecoration.content==='none'&&searchDecoration.paddingLeft==='12px',searchDecoration);
  for(const target of [60,72]){await page.locator('#reportList').evaluate(el=>el.scrollTop=el.scrollHeight);await page.waitForFunction(expected=>document.querySelectorAll('.report-item').length>=expected,target);}
  check('大量报告可继续加载至完整列表',await page.locator('.report-item').count()===72);
  const reportTop=await page.locator('#reportList').evaluate(el=>el.scrollTop);
  await page.evaluate(()=>refresh());
  check('刷新不会将报告列表跳回顶部',Math.abs(await page.locator('#reportList').evaluate(el=>el.scrollTop)-reportTop)<2);
  await page.locator('.report-item-open').first().click();
  await page.waitForSelector('#reportBody .doc-end');
  await page.locator('.report-export-menu > summary').click();
  const exportMenuUi=await page.locator('.report-export-menu').evaluate(el=>{const summary=el.querySelector('summary'),panel=el.querySelector('.report-export-popover'),arrow=getComputedStyle(summary,'::after'),box=panel.getBoundingClientRect();return{arrow:{content:arrow.content,width:arrow.width,height:arrow.height,top:arrow.top,mask:arrow.maskImage||arrow.webkitMaskImage},panel:{width:box.width,height:box.height,right:box.right,bottom:box.bottom},viewport:{width:innerWidth,height:innerHeight}};});
  check('报告导出箭头居中且菜单内容完整可见',exportMenuUi.arrow.content==='""'&&exportMenuUi.arrow.width==='12px'&&exportMenuUi.arrow.height==='12px'&&exportMenuUi.arrow.mask!=='none'&&exportMenuUi.panel.width>100&&exportMenuUi.panel.height>100&&exportMenuUi.panel.right<=exportMenuUi.viewport.width&&exportMenuUi.panel.bottom<=exportMenuUi.viewport.height,exportMenuUi);
  await page.keyboard.press('Escape');
  await page.locator('.report-reader-actions > .workspace-menu > summary').click();
  const moreArrow=await page.locator('.report-reader-actions > .workspace-menu > summary').evaluate(el=>{const style=getComputedStyle(el,'::after');return{content:style.content,width:style.width,height:style.height,mask:style.maskImage||style.webkitMaskImage};});
  check('报告更多按钮箭头使用居中矢量图标',moreArrow.content==='""'&&moreArrow.width==='12px'&&moreArrow.height==='12px'&&moreArrow.mask!=='none',moreArrow);
  await page.keyboard.press('Escape');
  const toolbarLong=await box('.report-reader .workspace-actionbar');
  await page.locator('.report-item-open').nth(1).click();
  await page.waitForSelector('#reportBody .doc-end');
  const toolbarShort=await box('.report-reader .workspace-actionbar');
  check('报告标题长短不改变操作栏位置',toolbarLong.y===toolbarShort.y && toolbarLong.height===toolbarShort.height);
  await shot('reports-light');
  await page.locator('.outline-menu > summary').click();
  check('所有40个报告章节都可访问',await page.locator('#reportToc button').count()===40 && await page.locator('#reportToc button').last().isVisible());
  await page.keyboard.press('Escape');
  await page.locator('.report-reader .report').evaluate(el=>el.scrollTop=el.scrollHeight);
  const end=await box('#reportBody .doc-end'), scroll=await box('.report-reader .report');
  check('报告可滚动至末尾且操作栏固定',end.y+end.height<=scroll.y+scroll.height+1 && (await box('.report-reader .workspace-actionbar')).y===toolbarShort.y);
  await nav('知识记忆');
  await page.locator('.memory-head-actions .workspace-menu > summary').click();
  const memoryManageUi=await page.locator('.memory-head-actions .workspace-menu').evaluate(el=>{const summary=el.querySelector('summary'),panel=el.querySelector('.workspace-menu-panel'),arrow=getComputedStyle(summary,'::after'),box=panel.getBoundingClientRect();return{arrow:{content:arrow.content,width:arrow.width,height:arrow.height,mask:arrow.maskImage||arrow.webkitMaskImage},panel:{width:box.width,height:box.height,right:box.right,bottom:box.bottom},viewport:{width:innerWidth,height:innerHeight}};});
  check('记忆管理箭头居中且菜单内容完整可见',memoryManageUi.arrow.content==='""'&&memoryManageUi.arrow.width==='12px'&&memoryManageUi.arrow.height==='12px'&&memoryManageUi.arrow.mask!=='none'&&memoryManageUi.panel.width>=180&&memoryManageUi.panel.height>100&&memoryManageUi.panel.right<=memoryManageUi.viewport.width&&memoryManageUi.panel.bottom<=memoryManageUi.viewport.height,memoryManageUi);
  await page.keyboard.press('Escape');
  await page.locator('.memory-item').first().click();
  await page.waitForFunction(()=>document.getElementById('memoryTitle').textContent!=='正在读取记忆…');
  const memoryToolbar=await box('.memory-reader-actions'), pinWidth=(await box('#memoryPin')).width;
  await shot('memory-light');
  await page.locator('.memory-item').nth(1).click();
  await page.waitForFunction(()=>document.getElementById('memoryTitle').textContent==='Agent 可靠性');
  check('固定状态和标题变化不会挤动记忆按钮',(await box('#memoryPin')).width===pinWidth && (await box('.memory-reader-actions')).y===memoryToolbar.y);
  await page.locator('.memory-content').evaluate(el=>el.scrollTop=el.scrollHeight);
  check('记忆正文滚动时操作栏固定',(await box('.memory-reader-actions')).y===memoryToolbar.y);
  await page.locator('#newMemory').click();
  check('新建记忆独立弹窗不挤动详情',await page.locator('#memoryCreateDialog').isVisible() && (await box('.memory-reader-actions')).y===memoryToolbar.y);
  await page.keyboard.press('Escape');
  const pages=[['研究','home'],['对比研究','compare'],['文献库','library'],['研究报告','reports'],['知识记忆','memory']];
  for(const size of [{width:1280,height:820},{width:900,height:640},{width:390,height:812}]) {
    await page.setViewportSize(size);
    for(const theme of ['light','dark']) {
      await page.evaluate(theme=>applyTheme(theme),theme);
      for(const [name,key] of pages) {
        await nav(name);
        const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
        check(key+' '+size.width+' '+theme+' 无页面横向溢出',!overflow);
        if(size.width===390 && ['reports','memory'].includes(key)) {
          const back=page.locator('#p-'+key+' [data-asset-back]');
          if(await back.isVisible()) await back.click();
          await page.locator(key==='reports'?'.report-item-open':'.memory-item').first().click();
          await page.waitForSelector(key==='reports'?'#reportBody .doc-end':'.memory-item.on',{state:'attached'});
          check(key+' 手机列表与详情分离',await back.isVisible());
        }
        if(size.width===1280&&theme==='dark'||size.width===390&&['library','reports','memory','compare'].includes(key)) await shot(key+'-'+theme+'-'+size.width);
      }
    }
  }
  await page.emulateMedia({colorScheme:'dark'}); await page.evaluate(()=>applyTheme('system'));
  check('随系统切换深色',await page.locator('html').getAttribute('data-theme')==='dark');
  await page.emulateMedia({colorScheme:'light'});
  await page.evaluate(()=>applyTheme('system'));
  check('随系统切换浅色',await page.locator('html').getAttribute('data-theme')==='light');
  check('浏览器没有脚本异常',errors.length===0,errors);
  return {checks:checks.length,passed:checks.filter(c=>c.ok).length,writes: writes.map(w=>w.path),errors};
}

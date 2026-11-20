(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const labels = {running:'正在调用',reviewing:'Codex 正在审查',changing:'正在应用变更',validating:'正在执行验证',awaiting_review:'等待 Codex 审查',reviewed:'已审查 · 待验收',completed:'本轮调用已完成',failed:'调用未成功',needs_revision:'需要返工',validation_failed:'验证未通过',historical:'历史记录 · 过程不完整'};
  const stages = [
    ['handoff','任务交接','Codex → 调用脚本'],['request','发送上下文','调用脚本 → DeepSeek'],
    ['response','模型回复','DeepSeek'],['review','Codex 审查','Codex'],
    ['changes','代码变更','Codex'],['validation','执行验证','本机命令 / Codex']
  ];
  let selectedId = null, selectedRun = null, selectedStep = 2, selectedTab = 'process';
  let summaries = [], listSignature = '', detailSignature = '', busy = false;
  let mode = null, controlToken = '', controlBusy = false;
  let followLatest = true;
  const number = value => typeof value === 'number' ? value.toLocaleString('zh-CN') : '未记录';
  const time = value => value ? new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value)) : '未记录';
  const latest = kind => selectedRun?.events?.filter(event => event.kind === kind).at(-1);
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function stateFor(index) {
    const observed = selectedRun?.progress?.stages?.[index]?.state;
    if (observed) {
      const names = {done:['done',index === 3 ? '已审查' : index === 4 ? '已应用' : index === 5 ? '已通过' : '已记录','✓'],skipped:['done',index === 4 ? '未修改' : '无需执行','—'],active:['active','正在进行','◉'],failed:['error','需要处理','!'],unknown:['unknown','未记录','·'],pending:['unknown','等待前序步骤','·']};
      return names[observed];
    }
    const event = latest(stages[index][0]);
    if (index === 5) {
      const checks = new Map();
      for (const check of selectedRun?.events || []) if (check.kind === 'validation') checks.set(JSON.stringify(check.command || check.summary),check);
      if ([...checks.values()].some(check => check.outcome === 'failed')) return ['error','存在未通过项','!'];
    }
    if (index === 2 && selectedRun?.result) return selectedRun.result.ok ? ['done','已返回','✓'] : ['error','失败 / 截断','!'];
    if (event) {
      if (event.outcome === 'not_applicable') return ['done',index === 4 ? '未修改' : '无需执行','—'];
      if (['rejected','failed'].includes(event.outcome)) return ['error',event.outcome === 'rejected' ? '需返工' : '未通过','!'];
      return ['done',index === 3 ? '已审查' : index === 4 ? '已应用' : index === 5 ? '已通过' : '已记录','✓'];
    }
    if (selectedRun?.source === 'legacy') return ['unknown','未记录','·'];
    if (index === 2 && selectedRun?.state === 'running') return ['wait','等待返回','◷'];
    return ['unknown','尚未记录','·'];
  }
  function renderMode() {
    const active = mode?.status === 'active';
    $('mode-title').textContent = active ? '双模型协作已开启' : 'Codex 独立模式';
    $('mode-badge').textContent = active ? '仅限本次任务' : 'DeepSeek 调用关闭';
    $('mode-badge').className = 'badge'+(active ? ' success' : '');
    document.querySelector('.mode-panel').classList.toggle('mode-on',active);
    $('mode-description').textContent = active ? 'Codex 主导规划和验收，DeepSeek 按需处理局部任务。' : '常规任务由 Codex 独立完成。只有明确开启后，才允许调用 DeepSeek。';
    const session = mode?.current;
    $('mode-session').textContent = active ? '本次任务：'+session.title+' · 最迟 '+time(session.expires_at)+' 自动失效' : session ? '上次结束：'+(session.end_reason || '已结束') : '尚未开启协作任务';
    $('mode-start-form').hidden = active;
    $('mode-active-actions').hidden = !active;
  }
  async function loadMode() {
    const response = await fetch('/api/mode',{cache:'no-store'});
    if (!response.ok) throw new Error('mode unavailable');
    const data = await response.json(); mode = data.mode; controlToken = data.control_token; renderMode();
  }
  async function changeMode(action, payload) {
    if (controlBusy) return;
    controlBusy = true;
    $('mode-start').disabled = true; $('mode-end').disabled = true; $('mode-error').hidden = true;
    try {
      if (!controlToken) await loadMode();
      const response = await fetch('/api/mode/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Collaboration-Token':controlToken},body:JSON.stringify(payload)});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '模式切换失败');
      mode = data.mode; renderMode();
      if (action === 'start') $('mode-task').value = '';
      await refresh();
    } catch (error) {
      $('mode-error').textContent = error.message; $('mode-error').hidden = false;
      try { await loadMode(); } catch { /* preserve the actionable error */ }
    } finally { controlBusy = false; $('mode-start').disabled = false; $('mode-end').disabled = false; }
  }
  $('mode-start-form').addEventListener('submit',event => {event.preventDefault(); changeMode('start',{title:$('mode-task').value.trim()});});
  $('mode-end').addEventListener('click',() => changeMode('end',{session_id:mode?.current?.id}));
  function renderProgress() {
    const observed = selectedRun?.progress;
    if (!observed) return;
    $('progress-label').textContent = observed.label;
    $('progress-owner').textContent = '当前负责：'+observed.owner;
    $('progress-count').textContent = observed.resolved+' / '+observed.total;
    $('progress-caption').textContent = selectedRun.source === 'legacy' ? '历史调用 · 非当前活动' : '当前选中调用 · 依据实际事件';
    const focus = document.querySelector('.progress-focus');
    focus.classList.toggle('in-progress',observed.active_index !== null);
    focus.classList.toggle('is-complete',selectedRun.state === 'completed');
    $('milestone-track').replaceChildren();
    for (const stage of observed.stages) {
      const mark = element('span','milestone '+stage.state);
      mark.setAttribute('aria-label',stage.name+'：'+stage.state);
      $('milestone-track').append(mark);
    }
    const pending = observed.stages.find(stage => stage.state === 'pending');
    $('progress-next').textContent = selectedRun.state === 'completed' ? '验收已结束 · 可查看各阶段证据' : selectedRun.source === 'legacy' ? '缺失步骤不计为已完成' : observed.active_index === 2 ? '模型生成进度不可测；正在等待实际返回' : pending ? '下一节点：'+pending.name : '阶段计数不代表耗时百分比';
    renderTimer();
  }
  function renderTimer() {
    const since = selectedRun?.progress?.active_since;
    if (!since) { $('progress-timer').textContent = '阶段计数，不是时间估算'; return; }
    const seconds = Math.max(0,Math.floor((Date.now()-new Date(since).getTime())/1000));
    $('progress-timer').textContent = '此阶段已等待 '+(seconds >= 60 ? Math.floor(seconds/60)+' 分 '+seconds%60+' 秒' : seconds+' 秒');
  }
  function selectTab(name) {
    selectedTab = name;
    for (const key of ['process','request','events']) {
      $('tab-'+key).setAttribute('aria-selected',String(key === name));
      $('panel-'+key).hidden = key !== name;
    }
  }
  for (const key of ['process','request','events']) $('tab-'+key).addEventListener('click',() => selectTab(key));
  document.querySelector('.tabs').addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const names = ['process','request','events'];
    let index = names.indexOf(selectedTab);
    index = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : -1) + 3) % 3;
    selectTab(names[index]); $('tab-'+names[index]).focus();
  });
  function renderList() {
    $('follow-latest').setAttribute('aria-pressed',String(followLatest));
    $('follow-latest').textContent = followLatest ? '跟随最新' : '返回最新';
    $('run-list').replaceChildren();
    for (const run of summaries) {
      const button = element('button','run-button'); button.type = 'button'; button.setAttribute('aria-pressed',String(run.id === selectedId));
      button.append(element('span','run-name',run.title));
      const meta = element('span','run-meta');
      meta.append(element('span','',time(run.created_at)),element('span','',number(run.usage?.total_tokens)+' tokens'));
      button.append(meta,element('span','state-label',run.progress?.label || labels[run.state] || run.state));
      button.addEventListener('click',async () => {
        followLatest = false;
        selectedId = run.id; detailSignature = ''; renderList();
        try { await loadDetail(); } catch { setOffline(); }
      });
      $('run-list').append(button);
    }
  }
  $('follow-latest').addEventListener('click',async () => {
    followLatest = true;
    if (summaries.length) selectedId = summaries[0].id;
    detailSignature = ''; renderList();
    try { await loadDetail(); } catch { setOffline(); }
  });
  function renderPipeline() {
    $('pipeline').replaceChildren();
    stages.forEach((stage,index) => {
      const [state,label,mark] = stateFor(index);
      const button = element('button','stage '+state); button.type = 'button';
      button.setAttribute('aria-pressed',String(index === selectedStep));
      button.setAttribute('aria-label',stage[1]+'：'+label);
      const top = element('span','stage-top');
      top.append(element('span','stage-index',String(index+1).padStart(2,'0')),element('span','stage-mark',mark));
      button.append(top,element('span','stage-title',stage[1]),element('span','stage-state',label),element('span','stage-owner',stage[2]));
      button.addEventListener('click',() => { selectedStep = index; selectTab('process'); renderPipeline(); renderStep(); });
      $('pipeline').append(button);
    });
  }
  function renderStep() {
    const [kind,title,owner] = stages[selectedStep];
    const event = latest(kind); const result = selectedRun.result;
    $('step-title').textContent = title;
    $('step-owner').textContent = owner;
    $('step-metrics').replaceChildren();
    let summary = event?.summary || (selectedRun.source === 'legacy' ? '这一阶段没有保存在历史记录中。' : '这一阶段尚未记录，不能据此认定已经完成。');
    let content = event?.detail || '';
    if (selectedStep === 0 && selectedRun.request) {
      content = selectedRun.request.messages.find(message => message.role === 'user')?.content || '';
      // The exact source context is available under Actual request; task is the first event detail.
      content = event?.detail || content;
    }
    if (selectedStep === 1 && selectedRun.request) {
      const req = selectedRun.request;
      content = JSON.stringify({model:req.model,context_files:req.context_files,max_tokens:req.max_tokens,timeout_seconds:req.timeout},null,2);
      summary = event?.summary || summary;
    }
    if (selectedStep === 2 && result) {
      content = result.content || result.error || '未返回内容';
      summary = result.ok ? 'API 已正常返回。审查、代码变更和测试状态分别记录。' : 'API 返回失败或内容被截断。';
      for (const [label,value] of [['输入',number(result.usage?.prompt_tokens)+' tokens'],['输出',number(result.usage?.completion_tokens)+' tokens'],['耗时',result.elapsed_seconds === undefined ? '未记录' : result.elapsed_seconds+' 秒'],['模型',result.model || result.requested_model || '未记录']]) {
        const metric = element('span'); metric.append(element('span','muted',label+' '),element('b','',value)); $('step-metrics').append(metric);
      }
    }
    if (event?.command) content = '$ '+event.command.join(' ')+'\n退出码：'+event.exit_code+'\n\n'+content;
    $('step-summary').textContent = summary;
    $('step-content').textContent = content || '暂无可展示的证据。';
  }
  function renderRequest() {
    $('messages').replaceChildren();
    const req = selectedRun.request;
    $('request-meta').textContent = req ? `${req.model} · 输出上限 ${req.max_tokens} tokens · 超时 ${req.timeout} 秒 · 不显示鉴权请求头` : '历史记录没有保存实际发送内容。';
    for (const message of req?.messages || []) {
      const section = element('section','message');
      section.append(element('h4','',message.role === 'system' ? '工作约束 · SYSTEM' : '任务与源码上下文 · USER'),element('pre','',message.content));
      $('messages').append(section);
    }
  }
  function renderEvents() {
    $('events').replaceChildren();
    for (const event of selectedRun.events || []) {
      const item = element('li'); const top = element('div','event-top');
      top.append(element('span','',event.summary),element('time','event-time',time(event.timestamp)));
      item.append(top,element('div','event-actor',event.actor+(event.outcome ? ' · '+event.outcome : '')));
      if (event.detail || event.command) {
        const details = element('details');
        details.append(element('summary','','展开记录'),element('pre','',(event.command ? '$ '+event.command.join(' ')+'\n退出码：'+event.exit_code+'\n\n' : '')+(event.detail || '')));
        item.append(details);
      }
      $('events').append(item);
    }
  }
  function renderDetail() {
    if (!selectedRun) return;
    $('empty').hidden = true; $('run-view').hidden = false;
    $('task-title').textContent = selectedRun.title;
    $('run-label').textContent = selectedRun.source === 'legacy' ? '历史调用 / 过程未完整记录' : '真实协作 / 可核对记录';
    const result = selectedRun.result;
    const files = selectedRun.request?.context_files || result?.context_files || [];
    $('task-meta').textContent = time(selectedRun.created_at)+' 北京时间 · '+(files.length ? '上下文：'+files.join('、') : '无源码上下文');
    $('task-state').textContent = labels[selectedRun.state] || selectedRun.state;
    $('task-state').className = 'badge'+(selectedRun.state === 'completed' ? ' success' : ['failed','validation_failed','needs_revision'].includes(selectedRun.state) ? ' error' : '');
    $('legacy-note').hidden = selectedRun.source !== 'legacy';
    const completion = latest('complete');
    $('run-conclusion').textContent = completion?.summary || (selectedRun.state === 'historical' ? '审查和验收状态未知。' : '以各阶段实际记录为准。');
    $('run-id').textContent = selectedRun.id;
    renderProgress(); renderPipeline(); renderStep(); renderRequest(); renderEvents();
  }
  async function loadDetail() {
    if (!selectedId) return;
    const id = selectedId;
    const response = await fetch('/api/runs/'+encodeURIComponent(id),{cache:'no-store'});
    if (!response.ok) throw new Error('record unavailable');
    const data = await response.json();
    if (selectedId !== id) return;
    const signature = JSON.stringify(data);
    if (signature !== detailSignature) { selectedRun = data; detailSignature = signature; renderDetail(); }
  }
  function setOffline() {
    $('sync-dot').className = 'dot failed';
    $('sync-status').textContent = '同步中断 · 保留上次数据';
  }
  async function refresh() {
    if (busy) return; busy = true;
    try {
      const response = await fetch('/api/runs',{cache:'no-store'});
      if (!response.ok) throw new Error('server unavailable');
      const data = await response.json(); summaries = data.runs; mode = data.mode; renderMode();
      $('total-runs').textContent = number(summaries.filter(run => run.api_attempted !== false).length);
      $('total-tokens').textContent = number(summaries.reduce((sum,run) => sum+(run.usage?.total_tokens || 0),0));
      const running = summaries.filter(run => run.state === 'running').length;
      const awaiting = summaries.filter(run => run.state === 'awaiting_review').length;
      const working = summaries.find(run => ['reviewing','changing','validating'].includes(run.state));
      $('activity').textContent = running ? running+' 个调用等待模型返回' : working ? working.progress.label : awaiting ? awaiting+' 个调用等待审查' : mode?.status === 'active' ? '已开启 · 等待分配任务' : '独立模式 · 无新调用';
      $('last-sync').textContent = '最近同步 '+time(data.server_time)+' 北京时间';
      $('sync-dot').className = 'dot connected'; $('sync-status').textContent = '本地同步 · 每 2 秒';
      if (summaries.length && (!selectedId || (followLatest && selectedId !== summaries[0].id))) {
        selectedId = summaries[0].id; detailSignature = ''; listSignature = '';
      }
      const signature = JSON.stringify(summaries);
      if (signature !== listSignature) { listSignature = signature; renderList(); }
      await loadDetail();
    } catch { setOffline(); } finally { busy = false; }
  }
  loadMode().catch(setOffline); refresh(); window.setInterval(refresh,2000); window.setInterval(renderTimer,1000);
})();

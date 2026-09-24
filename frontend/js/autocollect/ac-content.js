/**
 * 自动采集 · 采集内容配置子页
 * 两部分：①文档管理内容的采集配置（哪些文档参与采集 = 文档上架状态）；
 *         ②题库管理内容的采集配置（每轮入库预算 / AI 自评 / 套卷采集标记与溯源）。
 * 另保留未拆分出去的全局运行控制项（开关/周期/资源/时段/通知）。
 */
'use strict';

let _acDocTeacher = '';

async function renderAcContent() {
  acShell('ac-content', '文档库采集范围（上架即参与）· 题库入库预算与套卷采集标记 · 全局运行控制');
  const body = $('#ac-page-body');
  try {
    await acLoadConfig();
  } catch (e) { body.innerHTML = `<p class="err-tip">加载失败：${esc(e.message)}</p>`; return; }
  const cfg = _acCfg || {};
  body.innerHTML = `
  ${acControlPanelHtml()}

  <!-- ① 文档管理内容的采集配置 -->
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap"><b>📚 文档管理内容的采集配置</b>
        <span class="hint" style="margin-left:8px">文档处于「已上架」即参与自动采集轮询（内容更新自动重采，失败 6h 退避重试）；下架即停止采集</span></div>
    </div>
    <div class="panel-body">
      <div class="toolbar" style="margin-bottom:10px">
        <label for="ac-doc-teacher">老师：</label><select id="ac-doc-teacher" onchange="acDocLoadList()" aria-label="选择老师"></select>
        <select id="ac-doc-state" onchange="acDocLoadList()" aria-label="按上下架状态筛选">
          <option value="">全部状态</option><option value="enabled">已上架（参与采集）</option><option value="disabled">已下架</option>
        </select>
        <button class="btn ghost sm" onclick="acDocLoadList()">⟳ 刷新</button>
        <button class="btn sm primary" onclick="acDocPathOpen()" title="从服务器文件路径导入文档入库（导入白名单目录内）">📂 按路径上传</button>
        <span class="hint">也可到 <a href="/admin.html#documents" target="_blank">文档管理</a> 上传/预览/批量管理</span>
      </div>
      <div id="ac-doc-list" class="tbl-wrap"><div class="loading">加载中…</div></div>
    </div>
  </div>

  <!-- ② 题库管理内容的采集配置 -->
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap"><b>📝 题库管理内容的采集配置</b>
        <span class="hint" style="margin-left:8px">入库预算 · 质量自评 · 每套题链接的采集标记</span></div>
      <button class="btn primary sm" onclick="acSaveConfig()">💾 保存配置</button>
    </div>
    <div style="padding:14px">
      <div style="display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end">
        <div class="field" style="min-width:120px"><label>每轮预算（题）</label><input id="ac-budget" type="number" min="1" max="500" value="${cfg.max_per_cycle != null ? cfg.max_per_cycle : ''}"><div class="hint">单轮最多入库题数</div></div>
        <div class="field" style="min-width:150px"><label>AI 自评抽样率（0~1）</label><input id="ac-quality-rate" type="number" min="0" max="1" step="0.05" value="${cfg.quality_sample_rate != null ? cfg.quality_sample_rate : ''}" placeholder="0.1"><div class="hint">抽到的批次交给 LLM 评分来源健康度；0=关闭</div></div>
      </div>
      <div style="margin-top:12px">
        <div class="toolbar" style="flex-wrap:wrap">
          <b style="font-size:13px">套卷采集标记</b>
          <span class="hint">complete=已采完整永久跳过，需先重置才会重采；溯源可直查本站已入库同套题定位问题</span>
          <input id="ac-sets-q" placeholder="按来源页 URL 精确查…" style="min-width:260px;padding:7px 12px;border:1px solid var(--line-2);border-radius:8px;font-size:13px">
          <button class="btn ghost sm" onclick="acSetsLoad()">⟳ 刷新</button>
          <button class="btn ghost sm" onclick="acSetsReset('')" title="清空全部套卷标记，下次采集重新提取（慎用）">♻ 重置全部</button>
        </div>
        <div id="ac-sets" style="padding:10px 0">加载中…</div>
      </div>
    </div>
  </div>

  <!-- ③ 其余全局运行控制 -->
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap"><b>⚙️ 运行控制（全局）</b>
        <span class="hint" style="margin-left:8px">开关 · 周期 · 资源门槛 · 时段 · 通知 · 保存即热生效</span></div>
      <button class="btn primary sm" onclick="acSaveConfig()">💾 保存配置</button>
    </div>
    <div style="padding:14px">
      <div style="display:flex;flex-wrap:wrap;gap:14px">
        <div class="field" style="min-width:120px"><label>采集开关</label><select id="ac-en"><option value="1"${cfg.enabled ? ' selected' : ''}>启用</option><option value="0"${cfg.enabled ? '' : ' selected'}>停用</option></select></div>
        <div class="field" style="min-width:130px"><label>周期（分钟）</label><input id="ac-interval" type="number" min="1" max="1440" step="1" value="${cfg.interval_secs ? Math.round(cfg.interval_secs / 60) : ''}"></div>
        <div class="field" style="min-width:130px"><label>CPU 上限 %</label><input id="ac-cpu" type="number" min="1" max="99" value="${cfg.cpu_max != null ? cfg.cpu_max : ''}"></div>
        <div class="field" style="min-width:130px"><label>内存上限 %</label><input id="ac-mem" type="number" min="1" max="99" value="${cfg.mem_max != null ? cfg.mem_max : ''}"></div>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end;margin-top:4px">
        <div class="field" style="min-width:150px"><label>采集时间窗口</label><select id="ac-schedule-en"><option value="0"${cfg.schedule_enabled ? '' : ' selected'}>不限时段</option><option value="1"${cfg.schedule_enabled ? ' selected' : ''}>仅在指定时段</option></select><div class="hint">值守轮询到开始小时才采集，到结束小时自动停止（适合凌晨低峰）</div></div>
        <div class="field" style="min-width:120px"><label>起始小时</label><select id="ac-schedule-start">${hourOpts(cfg.schedule_start != null ? cfg.schedule_start : 2)}</select></div>
        <div class="field" style="min-width:120px"><label>结束小时</label><select id="ac-schedule-end">${hourOpts(cfg.schedule_end != null ? cfg.schedule_end : 6)}</select><div class="hint">结束 &lt; 起始表示跨夜窗口（如 22→6）</div></div>
        <div class="field" style="min-width:140px"><label>自动补采失败来源</label><select id="ac-retry-auto"><option value="0"${cfg.retry_failed_auto ? '' : ' selected'}>关闭</option><option value="1"${cfg.retry_failed_auto ? ' selected' : ''}>开启</option></select><div class="hint">每轮常规采集完成后，自动对上一轮失败来源补采一次</div></div>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end;margin-top:4px">
        <div class="field" style="flex:1.4;min-width:300px"><label>Webhook 通知 URL</label><input id="ac-webhook" value="${esc(cfg.webhook_url || '')}" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/…"><div class="hint">留空=暂停外发。采集完成/连续失败按右侧事件推送 JSON</div></div>
        <div class="field" style="min-width:190px"><label>通知事件</label>
          <div style="display:flex;gap:14px"><label class="chk" style="display:flex;align-items:center;gap:4px"><input type="checkbox" id="ac-notify-summary"${(cfg.notify_on || []).indexOf('summary') >= 0 ? ' checked' : ''}> 每轮摘要</label><label class="chk" style="display:flex;align-items:center;gap:4px"><input type="checkbox" id="ac-notify-fail"${(cfg.notify_on || []).indexOf('fail_alert') >= 0 ? ' checked' : ''}> 连续失败告警</label></div>
          <button class="btn ghost sm" type="button" onclick="acNotifyTest()" style="margin-top:6px">✉ 发送测试通知</button>
        </div>
        <div class="field" style="min-width:150px"><label>连续失败告警阈值</label><input id="ac-fail-threshold" type="number" min="1" step="1" value="${cfg.fail_alert_threshold != null ? cfg.fail_alert_threshold : ''}" placeholder="3"><div class="hint">连续 N 轮入库为 0 且有错误时触发 fail_alert</div></div>
      </div>
    </div>
  </div>`;
  acRefreshControls();
  acSetsLoad();
  acDocInit();
}

// ---------- 文档采集范围（上架=参与采集） ----------
async function acDocInit() {
  const sel = $('#ac-doc-teacher');
  if (!sel) return;
  try {
    const r = await admin('/admin/teachers');
    const teachers = (r.teachers || []).filter(t => t.teacher_id !== '__sys__' && t.enabled !== false);
    sel.innerHTML = teachers.map(t => `<option value="${esc(t.teacher_id)}">${esc(t.teacher_name)}（${esc(t.teacher_id)}）</option>`).join('');
    _acDocTeacher = sel.value || '';
    if (teachers.length) acDocLoadList();
    else $('#ac-doc-list').innerHTML = '<div class="empty">无在线老师</div>';
  } catch (e) { $('#ac-doc-list').innerHTML = `<div class="err-tip">读取老师失败：${esc(e.message)}</div>`; }
}

async function acDocLoadList() {
  const box = $('#ac-doc-list');
  if (!box) return;
  const tid = ($('#ac-doc-teacher') && $('#ac-doc-teacher').value) || _acDocTeacher;
  _acDocTeacher = tid;
  if (!tid) { box.innerHTML = '<div class="empty">无可选老师</div>'; return; }
  const state = ($('#ac-doc-state') && $('#ac-doc-state').value) || '';
  box.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const r = await admin(`/admin/documents/registry?teacher_id=${encodeURIComponent(tid)}&with_chunks=true`);
    let docs = r.documents || [];
    if (state === 'enabled') docs = docs.filter(d => d.enabled !== false);
    else if (state === 'disabled') docs = docs.filter(d => d.enabled === false);
    if (!docs.length) { box.innerHTML = `<div class="empty">${state ? '无匹配文档' : '该老师暂无文档'}</div>`; return; }
    box.innerHTML = `<table class="tbl"><thead><tr><th>文档名</th><th>扩展</th><th>分类</th><th>字数</th><th>采集状态</th><th style="width:150px">操作</th></tr></thead><tbody>` +
      docs.map(d => {
        const off = d.enabled === false;
        return `<tr style="${off ? 'opacity:.6' : ''}">
          <td title="${esc(d.doc_name)}">${esc((d.doc_name || '').slice(0, 46))}${(d.doc_name || '').length > 46 ? '…' : ''}</td>
          <td><code>${esc(((d.file_ext || d.doc_name || '').split('.').pop() || '').toUpperCase())}</code></td>
          <td>${d.category ? `<span class="badge blue">${esc(d.category)}</span>` : '<span class="badge gray">未分类</span>'}</td>
          <td class="mono">${(d.chars ?? 0).toLocaleString()}</td>
          <td>${off ? '<span class="badge gray">未参与采集</span>' : '<span class="badge green">采集中</span>'}</td>
          <td>${off
            ? `<button class="btn sm primary" onclick="acDocSetEnabled('${esc(d.doc_name)}', true)">上架并参与采集</button>`
            : `<button class="btn sm ghost warn" onclick="acDocSetEnabled('${esc(d.doc_name)}', false)">下架停止采集</button>`}</td>
        </tr>`;
      }).join('') + '</tbody></table>';
  } catch (e) { box.innerHTML = `<div class="err-tip">加载失败：${esc(e.message)}</div>`; }
}

async function acDocSetEnabled(docName, enabled) {
  try {
    await admin('/admin/documents/meta', { method: 'POST',
      body: JSON.stringify({ teacher_id: _acDocTeacher, doc_name: docName, enabled }) });
    toast(enabled ? `「${docName}」已上架，将参与采集` : `「${docName}」已下架，不再参与采集`, 'ok');
    acDocLoadList();
  } catch (e) { toast('操作失败：' + e.message, 'err'); }
}

// ---------- 按服务器路径导入文档（#36：上传文件路径 + 浏览选择路径） ----------
let _acImpDir = '';   // 浏览器当前目录

function acDocPathOpen() {
  _acImpDir = '';
  openModal('ac-fs-modal', `
    <div>
      <h3>📂 按路径上传文档入库</h3>
      <p class="hint">填写服务器（容器内）文件路径，或点「选择上传内容的路径」在白名单目录内浏览；入库后在列表勾选上架即参与采集。</p>
      <div class="field"><label>上传文件路径</label>
        <input id="ac-imp-path" placeholder="/data/import/真题汇编.pdf"></div>
      <div class="field" style="max-width:280px"><label>归类（可选）</label><input id="ac-imp-cat" placeholder="如：行测真题"></div>
      <div id="ac-imp-browse" style="display:none;margin-top:6px;border:1px solid var(--line);border-radius:10px;max-height:320px;overflow:auto"></div>
      <p class="hint" id="ac-imp-hint"></p>
      <div class="modal-actions">
        <button class="btn ghost" onclick="closeModal('ac-fs-modal')">关闭</button>
        <button class="btn ghost" onclick="acDocBrowse('')">📁 选择上传内容的路径</button>
        <button class="btn primary" onclick="acDocImportPath()">⬆ 导入入库</button>
      </div>
    </div>`);
}

async function acDocBrowse(path) {
  const box = $('#ac-imp-browse');
  if (!box) return;
  box.style.display = '';
  box.innerHTML = '<div class="loading">读取目录…</div>';
  try {
    const r = await admin('/admin/documents/browse' + (path ? '?path=' + encodeURIComponent(path) : ''));
    _acImpDir = r.current || '';
    const rows = (r.entries || []).map(e => `<tr>
      <td>${e.is_dir ? '📁' : '📄'} <a href="javascript:void(0)" data-p="${esc(e.path)}" data-d="${e.is_dir ? 1 : 0}">${esc(e.name)}</a></td>
      <td class="mono" style="width:90px;text-align:right">${e.is_dir ? '' : fmtSize(e.size)}</td>
      <td class="hint" style="width:130px">${e.mtime || ''}</td></tr>`).join('');
    box.innerHTML = `<div class="toolbar" style="padding:8px 10px;border-bottom:1px solid var(--line)">
        ${r.at_root ? '' : `<button class="btn ghost sm" data-p="${esc(r.parent || '')}">⬆ 上级</button>`}
        <span class="hint" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.current || ('根目录：' + (r.roots || []).join(' · ')))}</span>
        <span class="hint">点目录进入 · 点文件填入路径</span></div>
      <table class="tbl">${rows || '<tr><td class="empty" colspan="3">（无子目录/可导入文件）</td></tr>'}</table>`;
    box.querySelectorAll('a[data-p], button[data-p]').forEach(a => {
      a.onclick = () => {
        if (a.dataset.d === '1' || a.tagName === 'BUTTON') acDocBrowse(a.dataset.p);
        else { const el = $('#ac-imp-path'); if (el) el.value = a.dataset.p; $('#ac-imp-hint').textContent = '已选择：' + a.dataset.p; }
      };
    });
  } catch (e) { box.innerHTML = `<div class="err-tip" style="padding:10px">${esc(e.message)}</div>`; }
}

async function acDocImportPath() {
  const p = ($('#ac-imp-path') && $('#ac-imp-path').value || '').trim();
  const hint = $('#ac-imp-hint');
  if (!p) { hint.textContent = '请先填写或选择文件路径'; return; }
  hint.textContent = '导入中（解析入库需数秒～数十秒）…';
  try {
    const r = await admin('/admin/documents/import-path', { method: 'POST', body: JSON.stringify({
      teacher_id: _acDocTeacher || 'T001', path: p, category: ($('#ac-imp-cat') && $('#ac-imp-cat').value || '').trim() }) });
    hint.className = 'hint ok';
    hint.textContent = `✅ ${r.filename}：${r.chunks} 块入库，累计 ${r.total} 块`;
    toast('文档已按路径入库', 'ok');
    acDocLoadList();
    setTimeout(() => closeModal('ac-fs-modal'), 1200);
  } catch (e) { hint.className = 'hint err'; hint.textContent = '❌ 导入失败：' + e.message; }
}

// ---------- Webhook 测试 ----------
async function acNotifyTest() {
  try {
    const r = await admin('/admin/autocollect/notify-test', { method: 'POST' });
    if (r.ok) toast('✅ 测试通知已发送', 'ok');
    else toast('❌ 发送失败：' + (r.error || '请先保存 webhook URL'), 'err');
  } catch (e) { toast('发送测试通知失败：' + (e.message || e), 'err'); }
}

// ---------- 套卷采集标记（迁移自原页面） ----------
async function acSetsLoad() {
  const box = $('#ac-sets');
  if (!box) return;
  const q = ($('#ac-sets-q') && $('#ac-sets-q').value || '').trim();
  let data;
  try {
    data = await admin('/admin/autocollect/sets?limit=100' + (q ? '&url=' + encodeURIComponent(q) : ''));
  } catch (e) {
    box.innerHTML = '<span class="hint err">获取套卷标记失败：' + esc(e.message || e) + '</span>';
    return;
  }
  if (q) {
    const m = data.mark;
    box.innerHTML = `<div class="hint">标记查询：<b>${esc(q)}</b></div>` + (m
      ? `<div style="margin-top:6px">状态：<b>${esc(m.status)}</b> · 入库题数：${esc(m.added || 0)} · 提取题数：${esc(m.n || 0)} · 时间：${esc(m.ts || '')}
         <button class="btn ghost sm" style="margin-left:10px" onclick="acSetTrace('${esc(q)}')">🔍 溯源回查本题</button>
         <button class="btn ghost sm" onclick="acSetsReset('${esc(q)}')">♻ 重置该套卷</button></div>`
      : '<div class="empty" style="padding:8px 0">该 URL 尚无采集标记（未采过或已重置）</div>');
    return;
  }
  box.innerHTML = acSetsHtml(data);
}

function acSetsHtml(data) {
  const sum = data.summary || {};
  const items = data.items || [];
  const badge = { complete: '<span style="color:var(--ok,#2e7d32)">● 已采完</span>',
                  incomplete: '<span style="color:#e65100">◐ 待续采</span>',
                  failed: '<span style="color:#c62828">✕ 失败重试中</span>',
                  gated: '<span style="color:#9e9e9e">— 杂页跳过</span>' };
  const rows = items.map(x => `<tr>
      <td style="max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(x.url)}">${esc(x.url)}</td>
      <td>${badge[x.status] || esc(x.status)}</td>
      <td>${x.added || 0}</td>
      <td>${esc((x.ts || '').replace('T', ' ').replace('Z', ''))}</td>
      <td><button class="btn ghost sm" onclick="acSetTrace('${esc(x.url)}')">🔍 回查</button>
          <button class="btn ghost sm" onclick="acSetsReset('${esc(x.url)}')">♻</button></td>
    </tr>`).join('');
  return `<div class="hint" style="margin-bottom:8px">共 ${data.total || 0} 套标记 —
      已采完 <b style="color:var(--ok,#2e7d32)">${sum.complete || 0}</b> ·
      待续采 <b style="color:#e65100">${sum.incomplete || 0}</b> ·
      失败 <b style="color:#c62828">${sum.failed || 0}</b> ·
      杂页 ${sum.gated || 0} ·
      累计入库 ${sum.questions || 0} 题（已采完且内容未变的套卷不再重复采集）</div>` +
    (items.length ? `<div style="max-height:300px;overflow:auto"><table class="tbl" style="width:100%"><thead><tr>
      <th>套卷链接</th><th>状态</th><th>入库题</th><th>时间(UTC)</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`
      : '<div class="empty">尚无套卷标记（采集运行后生成）</div>');
}

async function acSetTrace(url) {
  try {
    const r = await admin('/admin/autocollect/sets/questions?url=' + encodeURIComponent(url));
    const qs = r.questions || [];
    const rows = qs.map(q => `<tr>
      <td>${q.id}</td><td>${esc(q.qtype)}</td>
      <td style="max-width:340px">${esc((q.question || '').slice(0, 80))}</td>
      <td style="max-width:120px">${esc((q.answer || '').slice(0, 30))}</td>
      <td>${esc(q.category || '')}</td><td>${esc(q.source || '').slice(0, 40)}</td></tr>`).join('');
    openModal('ac-modal', `<div style="min-width:560px;max-width:920px">
      <h3 style="margin:0 0 6px">套卷溯源 · ${qs.length} 题</h3>
      <p class="hint" style="margin:0 0 8px;word-break:break-all">${esc(url)}</p>
      ${r.mark ? `<p class="hint" style="margin:0 0 8px">采集标记：${esc(r.mark.status)} · 入库 ${r.mark.added} · 时间 ${esc(r.mark.ts || '')}</p>` : '<p class="hint" style="color:#e65100">该页无采集标记（历史题源或已重置）</p>'}
      <div style="max-height:420px;overflow:auto"><table class="tbl" style="width:100%"><thead><tr>
        <th>ID</th><th>题型</th><th>题干</th><th>答案</th><th>分类</th><th>出处</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6" class="empty">未查到本题（题已被删/未带溯源 URL 入库）</td></tr>'}</tbody></table></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px">
        <button class="btn ghost sm" onclick="closeModal('ac-modal')">关闭</button>
        <button class="btn primary sm" onclick="acSetsReset('${esc(url)}');closeModal('ac-modal')">♻ 重置并重采该套卷</button>
      </div></div>`);
  } catch (e) { toast('溯源失败：' + (e.message || e), 'err'); }
}

async function acSetsReset(url) {
  if (!confirm(url ? `重置该套卷的「已采集完整」标记？\n下次采集将重新提取：\n${url}`
                   : '清空全部套卷采集标记？\n所有已采完页面将在下轮重新提取（消耗 LLM 预算），确认继续？')) return;
  try {
    const r = await admin('/admin/autocollect/sets/reset', {
      method: 'POST', body: JSON.stringify({ urls: url ? [url] : [] }),
    });
    toast(`已重置 ${r.removed} 个套卷标记`, 'ok');
    acSetsLoad();
  } catch (e) { toast('重置失败：' + (e.message || e), 'err'); }
}

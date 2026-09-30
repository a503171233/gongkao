/**
 * 自动采集 · 多通道配置子页
 * 采集 LLM 通道（多平台并行提取）· 多线程分配 · 备用模型 · LLM 预算/价格。
 * 与原「管理多通道」弹窗同一套接口与逻辑，仅由弹窗迁移为独立子页。
 */
'use strict';

let _acChannelEditIdx = -1;   // 正在编辑的通道下标，-1=新增
const _KEY_SRC = { channel: '通道密钥', registry: '注册表密钥', global: '全局密钥', none: '无密钥' };

async function renderAcChannels() {
  acShell('ac-channels', '采集提取用的多模型通道管理 · 片段轮询并行分配 · 备用容错模型与 LLM 预算成本');
  const body = $('#ac-page-body');
  try {
    await acLoadConfig();
  } catch (e) { body.innerHTML = `<p class="err-tip">加载失败：${esc(e.message)}</p>`; return; }
  const cfg = _acCfg || {};
  body.innerHTML = `
  ${acControlPanelHtml()}
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap"><b>多线程采集管理</b><span class="hint" style="margin-left:8px">并发线程并行提取 · 多模型通道轮询分配</span></div>
      <button class="btn ghost sm" onclick="renderAcChannels()">⟳ 刷新</button>
    </div>
    <div style="padding:14px">
      <div style="display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end">
        <div class="field" style="min-width:130px"><label>并发线程（并行采集）</label><input id="ac-threads" type="number" min="1" max="6" value="${cfg.threads != null ? cfg.threads : 1}"><div class="hint">&gt;1 时整卷/多篇按片段轮询分配到不同模型通道，多模型并行提取</div></div>
        <div class="field" style="min-width:150px"><label>LLM 每轮字数配额（预算墙）</label><input id="ac-llm-budget" type="number" min="0" step="1000" value="${cfg.llm_budget_chars != null ? cfg.llm_budget_chars : ''}" placeholder="0=不限"><div class="hint">本轮累计下发 LLM 的输入字符达上限即停；0=不限</div></div>
        <div class="field" style="min-width:150px"><label>价格（元 / 百万token）</label><input id="ac-llm-price" type="number" min="0" step="0.1" value="${cfg.llm_price_per_1m != null ? cfg.llm_price_per_1m : ''}" placeholder="2.0"><div class="hint">仅用于成本估算（中文粗估 1 字符 ≈ 0.7 token）</div></div>
        <button class="btn primary sm" onclick="acSaveConfig()">💾 保存配置</button>
      </div>
      <div class="hint" style="margin-top:8px">采集时片段按轮询分配到各通道，多模型并行提取。当前通道数：<b id="ac-ch-count">—</b>；备用模型：<span id="ac-ch-fb">${esc((cfg.fallback_models || []).join(' · ') || '未配置')}</span></div>
    </div>
  </div>
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap"><b>采集模型通道</b><span class="hint" style="margin-left:8px">第 1 条为主通道；⚡=测活（最小请求验证连通与密钥）</span></div>
      <div style="display:flex;gap:8px">
        <button class="btn ghost sm" onclick="acChannelTestAll()">⚡ 一键测活全部</button>
        <span class="hint" id="ac-test-state"></span>
      </div>
    </div>
    <div style="padding:14px">
      <div id="ac-ch-tbl">加载中…</div>
      <div style="margin-top:12px;border-top:1px solid var(--line);padding-top:12px">
        <div class="toolbar"><b id="ac-ch-form-title">新增通道</b>
          ${_acChannelEditIdx >= 0 ? '<button class="btn ghost sm" onclick="acChannelCancelEdit()">✕ 取消编辑</button>' : ''}
        </div>
        <div class="field" style="margin-top:8px">
          <label>选择已有平台（自动填入 Base URL；密钥留空即复用注册表/全局密钥）</label>
          <select id="ac-new-platform" style="max-width:520px" onchange="acChannelPlatformPick()"><option value="">— 选择已有平台 / 自定义 —</option></select>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:8px">
          <div class="field" style="flex:2;min-width:220px"><label>Base URL</label><input id="ac-new-base" placeholder="https://provider/v1" oninput="acNewBaseEdited()"></div>
          <div class="field" style="flex:1;min-width:180px"><label>API Key（留空=注册表/全局密钥）</label><input id="ac-new-key" type="password" placeholder="sk-…" autocomplete="off"></div>
        </div>
        <div class="field" style="margin-top:8px">
          <label>模型（新增可多选批量添加；编辑单选）</label>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <select id="ac-new-model" style="flex:2;min-width:220px"><option value="">— 先拉取模型列表 —</option></select>
            <button class="btn ghost sm" onclick="acChannelFetch()">🔗 拉取模型</button>
            <button class="btn ghost sm" onclick="acChannelPickAll(true)" type="button">全选</button>
            <button class="btn ghost sm" onclick="acChannelPickAll(false)" type="button">清空</button>
            <button class="btn ghost sm" onclick="acChannelBatchAdd()" type="button">＋ 一键多选(批量添加)</button>
            <span class="hint" style="align-self:center" id="ac-model-pick-state"></span>
          </div>
          <div id="ac-model-pick" style="margin-top:6px;max-height:150px;overflow:auto;border:1px solid var(--line);border-radius:8px;padding:8px;display:none"></div>
        </div>
        <div style="margin-top:12px;display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap">
          <button class="btn sm primary" onclick="acChannelAdd()">${_acChannelEditIdx >= 0 ? '💾 保存修改' : '＋ 添加通道'}</button>
        </div>
      </div>
    </div>
  </div>
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap"><b>备用模型（同通道容错）</b><span class="hint" style="margin-left:8px">主模型失败时按序尝试；留空 = 仅主模型</span></div>
      <div style="display:flex;gap:8px">
        <button class="btn ghost sm" onclick="acFallbackFetch()">⇣ 从通道拉取模型</button>
        <button class="btn primary sm" onclick="acSaveConfig()">💾 保存配置</button>
      </div>
    </div>
    <div style="padding:14px">
      <div class="field"><textarea id="ac-fallback" rows="3" placeholder="例如：glm-5.2&#10;deepseek-v4-pro">${esc((cfg.fallback_models || []).join('\n'))}</textarea></div>
      <div class="hint" id="ac-fallback-state"></div>
    </div>
  </div>
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap"><b>粉笔题库直采（行测真题）</b><span class="hint" style="margin-left:8px">登录态 Cookie 走粉笔题库 API · 整卷结构化入库 · 守候窗口自动采集</span></div>
      <div style="display:flex;gap:8px;align-items:center">
        <span class="hint" id="ac-fb-run"></span>
        <button class="btn ghost sm" onclick="acFenbiLoad()">⟳</button>
      </div>
    </div>
    <div style="padding:14px" id="ac-fenbi-box">加载中…</div>
  </div>
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap"><b>公考真题库直采（gkzhenti.cn）</b><span class="hint" style="margin-left:8px">站方官方接口免凭据 · 728 卷行测真题 · 含答案/图片 · 解析页有验证码故不入库（AI 老师可补讲）</span></div>
      <div style="display:flex;gap:8px;align-items:center">
        <span class="hint" id="ac-gzt-run"></span>
        <button class="btn ghost sm" onclick="acGkztLoad()">⟳</button>
      </div>
    </div>
    <div style="padding:14px" id="ac-gkzt-box">加载中…</div>
  </div>`;
  acRefreshControls();
  acChLoadTable();
  acChFillPlatform();
  acFenbiLoad();
  acGkztLoad();
}

// ---- 通道表格（含使用统计列，逻辑迁移自原「管理多通道」弹窗） ----
async function acChLoadTable() {
  const box = $('#ac-ch-tbl');
  if (!box) return;
  let info, stats;
  try {
    [info, stats] = await Promise.all([
      admin('/admin/autocollect/llm/channels'),
      admin('/admin/autocollect/llm/channels/stats'),
    ]);
  } catch (e) { box.innerHTML = '<span class="hint err">读取通道失败：' + esc(e.message || e) + '</span>'; return; }
  const chs = info.channels || [];
  const cnt = $('#ac-ch-count'); if (cnt) cnt.textContent = chs.length;
  const statBy = {};
  (stats && stats.rows || []).forEach(r => { statBy[r.idx] = r; });
  const statCell = (i) => {
    const s = statBy[i] || {};
    if (!chs[i]) return '<td>—</td>';
    const sc = s.success_count || 0, fc = s.fail_count || 0;
    const avg = s.avg_ms ? s.avg_ms + 'ms' : '—';
    const last = s.last_success_ts ? s.last_success_ts.slice(5, 16) : '无';
    const flag = s.flag || 'none';
    const flagTxt = { ok: '', none: '未使用', high_fail: '⚠️ 从未成功', inactive: '🔴 失活' }[flag] || '';
    const color = (flag === 'inactive') ? '#b71c1c' : (flag === 'high_fail' ? '#b26a00' : 'var(--ink-3)');
    const costLine = s.cost >= 0.0001 ? `≈¥${s.cost.toFixed(4)}（${(s.tokens || 0).toLocaleString()} tok）` : `${(s.chars || 0).toLocaleString()} 字`;
    const row = `<span style="color:${color}">${flagTxt ? flagTxt + '<br>' : ''}${sc}✓/${fc}✗&nbsp;·&nbsp;${avg}</span>`;
    return `<td title="${(s.last_error || '').split('\n')[0].slice(0, 120)}">${row}<div class="hint" style="font-size:11px">最近成功 ${esc(last)}<br>${esc(costLine)}</div></td>`;
  };
  box.innerHTML = `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>模型</th><th>Base URL</th><th>API Key</th><th>使用统计</th><th style="width:120px"></th></tr></thead><tbody>` +
    (chs.map((c, i) => `<tr>
      <td><code>${esc(c.model)}</code>${i === 0 ? ' <span class="tag">主</span>' : ''}</td>
      <td><code>${esc(c.base_url)}</code></td>
      <td>${esc(c.api_key_masked || '全局/注册表密钥')}</td>
      ${statCell(i)}
      <td style="white-space:nowrap">
        <button class="btn sm ghost" title="发一次最小请求验证连通与密钥" onclick="acChannelTest(${i})">⚡</button>
        <button class="btn sm ghost" onclick="acChannelEdit(${i})">✏️</button>
        <button class="btn sm ghost" onclick="acChannelDel(${i})">🗑</button>
      </td>
    </tr>`).join('') || '<tr><td colspan="5" class="empty">尚未配置专用通道；采集回落平台默认模型</td></tr>') +
    '</tbody></table></div>' +
    `<div class="hint" style="margin-top:6px">统计列为历史成/失、平均延迟、累计字数与估算成本；超过 ${(stats && stats.inactive_days) || 14} 天无成功的通道标红提示清理。${esc('（编辑时 Key 留空保持原值）')}</div>`;
}

async function acChFillPlatform() {
  const pf = $('#ac-new-platform');
  if (!pf) return;
  const platforms = await acLlmPlatformOptions();
  pf.innerHTML = '<option value="">— 选择已有平台 / 自定义 —</option>'
    + platforms.map(o => `<option value="${esc(o.base_url)}">${o.label}</option>`).join('');
  // 编辑态回填
  if (_acChannelEditIdx >= 0) {
    try {
      const r = await admin('/admin/autocollect/llm/channels');
      const cur = (r.channels || [])[_acChannelEditIdx];
      if (cur) {
        $('#ac-new-base').value = cur.base_url;
        $('#ac-new-model').innerHTML = `<option value="${esc(cur.model)}">${esc(cur.model)}</option>`;
        [...pf.options].forEach(o => { if (o.value === cur.base_url) pf.value = o.value; });
      }
    } catch (e) { /* 忽略回填失败 */ }
  }
}

async function acChannelPlatformPick() {
  const pf = $('#ac-new-platform');
  const bu = (pf && pf.value) || '';
  if ($('#ac-new-base')) $('#ac-new-base').value = bu;
  if ($('#ac-new-model')) $('#ac-new-model').innerHTML = '<option value="">— 先拉取模型列表 —</option>';
  acChannelPickAll(false);
}
function acNewBaseEdited() {
  const base = $('#ac-new-base').value || '';
  const pf = $('#ac-new-platform');
  if (pf) { [...pf.options].forEach(o => { if (o.value === base) { pf.value = o.value; return; } }); }
}
async function acChannelResolveKey() {
  const bu = ($('#ac-new-base').value || '').trim();
  return { keyFromRegistry: !!bu, keyExplicit: $('#ac-new-key') ? $('#ac-new-key').value || '' : '' };
}
function acChannelPickAll(v) {
  const box = $('#ac-model-pick');
  if (!box) return;
  Array.from(box.querySelectorAll('input[type=checkbox]')).forEach(i => i.checked = !!v);
  acChannelUpdateCount();
}
function acChannelUpdateCount() {
  const box = $('#ac-model-pick');
  const st = $('#ac-model-pick-state');
  if (!box || !st) return;
  const n = Array.from(box.querySelectorAll('input[type=checkbox]:checked')).length;
  st.textContent = n ? `已选 ${n} 个模型` : '';
}
async function acChannelBatchAdd() {
  const box = $('#ac-model-pick');
  if (!box) return;
  const base = ($('#ac-new-base').value || '').trim();
  const key = ($('#ac-new-key').value || '').trim();
  if (!base) { toast('请先选择平台或填写 Base URL', 'err'); return; }
  const models = Array.from(box.querySelectorAll('input[type=checkbox]:checked')).map(i => i.value.trim()).filter(Boolean);
  if (!models.length) { toast('请先「拉取模型」并勾选要添加的模型', 'err'); return; }
  let ok = 0, skipped = 0, firstErr = '';
  for (const m of models) {
    const payload = { model: m, base_url: base };
    if (key && key.indexOf('****') < 0) payload.api_key = key;
    try { await admin('/admin/autocollect/llm/channels', { method: 'POST', body: JSON.stringify(payload) }); ok++; }
    catch (e) { if (String(e).indexOf('已存在') >= 0) skipped++; else if (!firstErr) firstErr = e.message || e; }
  }
  toast(`批量添加完成：新增 ${ok} 个，跳过重复 ${skipped}${firstErr ? ('，失败：' + firstErr) : ''}`, ok ? 'ok' : 'err');
  _acChannelEditIdx = -1;
  renderAcChannels();
}
async function acChannelFetch() {
  const base = ($('#ac-new-base').value || '').trim();
  const key = ($('#ac-new-key').value || '').trim();
  const sel = $('#ac-new-model');
  const box = $('#ac-model-pick');
  const mst = $('#ac-model-pick-state');
  if (!base) { toast('请先选择平台或填写 Base URL', 'err'); return; }
  if (sel) sel.innerHTML = '<option value="">加载中…</option>';
  acChannelPickAll(false);
  try {
    const r = await admin('/admin/ai-models/list-remote', {
      method: 'POST', body: JSON.stringify({ base_url: base, provider: '', api_key: key }),
    });
    const models = r.models || [];
    if (box) {
      box.style.display = '';
      box.innerHTML = models.length
        ? models.map(id => `<label class="chk" style="display:block;padding:3px 2px"><input type="checkbox" value="${esc(id)}"><span style="margin-left:4px">${esc(id)}</span></label>`).join('')
        : '<div class="empty">未获取到任何模型</div>';
    }
    if (sel) sel.innerHTML = '<option value="">— 请选择模型 —</option>'
      + models.map(id => `<option value="${esc(id)}">${esc(id)}</option>`).join('');
    const msg = r.ok ? `✅ 拉取 ${models.length} 个模型（密钥：${_KEY_SRC[r.key_source] || '默认'}），可勾选批量添加` : ('⚠️ 已展示兜底：' + (r.error || ''));
    if (mst) { mst.className = 'hint ' + (r.ok ? 'ok' : 'err'); mst.textContent = msg; }
    toast(msg, r.ok ? 'ok' : 'err');
  } catch (e) { if (mst) { mst.className = 'hint err'; mst.textContent = '拉取失败：' + (e.message || e); } toast('拉取失败：' + (e.message || e), 'err'); }
}
async function acChannelEdit(idx) {
  _acChannelEditIdx = idx;
  renderAcChannels();   // 重渲染：表单区切换为「编辑通道 / 保存修改 / 取消编辑」
}
function acChannelCancelEdit() {
  _acChannelEditIdx = -1;
  renderAcChannels();
}
async function acChannelAdd() {
  const model = ($('#ac-new-model').value || '').trim();
  const base = ($('#ac-new-base').value || '').trim();
  const key = ($('#ac-new-key').value || '').trim();
  if (!model || !base) { toast('请选择模型并填写 Base URL', 'err'); return; }
  const payload = { model, base_url: base };
  if (key && key.indexOf('****') < 0) payload.api_key = key;
  try {
    if (_acChannelEditIdx >= 0) {
      await admin(`/admin/autocollect/llm/channels/${_acChannelEditIdx}`, { method: 'PUT', body: JSON.stringify(payload) });
      toast('已保存通道：' + model, 'ok');
    } else {
      await admin('/admin/autocollect/llm/channels', { method: 'POST', body: JSON.stringify(payload) });
      toast('已添加采集通道：' + model, 'ok');
    }
    _acChannelEditIdx = -1;
    renderAcChannels();
  } catch (e) { toast('保存失败：' + (e.message || e), 'err'); }
}
async function acChannelDel(idx) {
  if (!confirm('删除该采集通道？')) return;
  try {
    await admin(`/admin/autocollect/llm/channels/${idx}`, { method: 'DELETE' });
    if (_acChannelEditIdx === idx) _acChannelEditIdx = -1;
    toast('已删除采集通道', 'ok');
    renderAcChannels();
  } catch (e) { toast('删除失败：' + (e.message || e), 'err'); }
}
async function acChannelTest(idx) {
  const btn = document.querySelector(`#ac-ch-tbl button[onclick="acChannelTest(${idx})"]`);
  if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
  try {
    const r = await admin(`/admin/autocollect/llm/channels/${idx}/test`, { method: 'POST' });
    if (r.ok) toast(`✅ ${r.model} 连通正常 ${r.latency_ms}ms（密钥：${_KEY_SRC[r.key_source] || r.key_source}）`, 'ok');
    else toast(`❌ ${r.model}：${r.error}`, 'err');
  } catch (e) { toast('测活失败：' + (e.message || e), 'err'); }
  if (btn) { btn.disabled = false; btn.textContent = '⚡'; }
}
async function acChannelTestAll() {
  const st = $('#ac-test-state');
  if (st) { st.className = 'hint'; st.textContent = '测活中…（逐条发最小请求，稍候）'; }
  try {
    const r = await admin('/admin/autocollect/llm/channels/test', { method: 'POST' });
    const bad = (r.results || []).filter(x => !x.ok);
    const line = (r.results || []).map(x => `${x.ok ? '✅' : '❌'}${x.model}`).join('  ');
    if (st) { st.className = 'hint ' + (bad.length ? 'err' : 'ok'); st.textContent = `${r.ok_count}/${r.total} 正常  ${line}`; }
    toast(bad.length
      ? `测活 ${r.ok_count}/${r.total} 正常；异常：` + bad.map(x => `${x.model}：${(x.error || '').split('\n')[0].slice(0, 90)}`).join('；')
      : `✅ 全部 ${r.total} 条通道测活通过`, bad.length ? 'err' : 'ok');
  } catch (e) {
    if (st) { st.className = 'hint err'; st.textContent = '测活失败：' + (e.message || e); }
    toast('测活失败：' + (e.message || e), 'err');
  }
}

// 备用模型拉取（写入本页 ac-fallback；弹窗宿主 ac-modal 由 acShell 提供）
async function acFallbackFetch() {
  const state = $('#ac-fallback-state');
  if (state) { state.className = 'hint'; state.textContent = '正在从已配置的采集通道拉取模型列表…'; }
  try {
    const r = await admin('/admin/autocollect/llm/models', { method: 'POST', body: '{}' });
    const models = r.models || [];
    if (!r.ok && !models.length) {
      if (state) { state.className = 'hint err'; state.textContent = r.error || '拉取失败'; }
      return;
    }
    const el = $('#ac-fallback');
    const exist = new Set(el && el.value ? el.value.split(/\n/).map(x => x.trim()).filter(Boolean) : []);
    const items = models.map(m =>
      `<label class="chk" style="display:block;padding:4px 2px"><input type="checkbox" value="${esc(m)}"${exist.has(m) ? ' checked' : ''}> ${esc(m)}</label>`).join('');
    const fbNote = r.used_fallback ? `（远端获取失败，已展示内置兜底目录：${esc(r.error || '')}）` : '';
    openModal('ac-modal', `
      <div style="min-width:340px;max-width:540px">
        <h3 style="margin:0 0 6px">选择备用模型</h3>
        <p class="hint" style="margin:0 0 10px">通道 @ ${esc(r.base_url || '')}${fbNote}。勾选后写入「备用模型」列表，保存配置后生效。</p>
        <div style="max-height:300px;overflow:auto;border:1px solid var(--line);border-radius:8px;padding:8px">${items || '<div class="empty">未获取到任何模型</div>'}</div>
        <div style="margin-top:10px;display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap">
          <button class="btn ghost sm" type="button" onclick="Array.from(document.querySelectorAll('#ac-modal .chk input')).forEach(i=>i.checked=true)">全选</button>
          <button class="btn ghost sm" type="button" onclick="Array.from(document.querySelectorAll('#ac-modal .chk input')).forEach(i=>i.checked=false)">清空</button>
          <button class="btn ghost sm" type="button" onclick="closeModal('ac-modal')">取消</button>
          <button class="btn sm" type="button" onclick="acFallbackPick()">✓ 写入备用模型</button>
        </div>
      </div>`);
    if (state) { state.className = 'hint ok'; state.textContent = `✅ 已拉取 ${models.length} 个模型，请勾选后写入备用模型`; }
  } catch (e) {
    if (state) { state.className = 'hint err'; state.textContent = '拉取模型失败：' + (e.message || e); }
  }
}
function acFallbackPick() {
  const checked = Array.from(document.querySelectorAll('#ac-modal .chk input:checked')).map(i => i.value.trim()).filter(Boolean);
  const el = $('#ac-fallback');
  closeModal('ac-modal');
  if (!el) return;
  const exist = el.value ? el.value.split(/\n/).map(x => x.trim()).filter(Boolean) : [];
  const merged = [];
  checked.concat(exist).forEach(m => { if (m && merged.indexOf(m) < 0) merged.push(m); });
  el.value = merged.join('\n');
  const state = $('#ac-fallback-state');
  if (state) { state.className = 'hint ok'; state.textContent = `✅ 已写入 ${checked.length} 个备用模型（共 ${merged.length} 个），点击「保存配置」生效`; }
}

// ---- 粉笔题库直采（批次9：登录态 Cookie → 题库 API → 整卷入库） ----
let _fenbiPollTimer = null;

function _fenbiFunnelHtml(lr) {
  if (!lr || !lr.at) return '<span class="hint">尚未运行过</span>';
  const err = (lr.papers_error || []).length
    ? `<div class="hint err" style="margin-top:4px">⚠️ 失败卷：${esc(lr.papers_error.map(x => String(x).slice(0, 80)).join('；'))}</div>` : '';
  return `<div class="hint ok" style="margin-top:4px">最近一轮 ${esc(String(lr.at).slice(0, 16))}：扫描 <b>${lr.papers_scanned || 0}</b> 卷 → 拉取 <b>${lr.papers_pulled || 0}</b> 卷 → 入库 <b>${lr.questions_added || 0}</b> 题（重复 ${lr.duplicates || 0} · 无效 ${lr.invalid || 0} · 过滤 ${lr.filtered || 0}）</div>${err}`;
}

async function acFenbiLoad() {
  const box = $('#ac-fenbi-box');
  const run = $('#ac-fb-run');
  if (!box) return;
  let d;
  try { d = await admin('/admin/fenbi/config'); }
  catch (e) { box.innerHTML = `<span class="hint err">读取失败：${esc(e.message || e)}</span>`; return; }
  if (run) {
    run.className = 'hint ' + (d.running ? 'ok' : '');
    run.textContent = d.running ? '⏳ 采集中…' : (d.enabled ? '🟢 守候已开启' : '⚪ 未启用');
  }
  box.innerHTML = `
  <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end">
    <div class="field" style="min-width:150px"><label>采集开关（守候窗口自动跑）</label>
      <select id="ac-fb-enabled"><option value="0"${d.enabled ? '' : ' selected'}>关闭</option><option value="1"${d.enabled ? ' selected' : ''}>开启</option></select></div>
    <div class="field" style="min-width:110px"><label>单轮卷数上限</label><input id="ac-fb-papers" type="number" min="1" max="50" value="${d.max_papers_per_run || 3}"></div>
    <div class="field" style="min-width:130px"><label>单轮题量预算</label><input id="ac-fb-questions" type="number" min="1" max="2000" value="${d.max_questions_per_run || 200}"></div>
    <div class="field" style="min-width:130px"><label>请求间隔（秒）</label><input id="ac-fb-interval" type="number" min="1" max="30" value="${d.min_interval_secs || 2}"><div class="hint">≥2s 防风控</div></div>
    <button class="btn primary sm" onclick="acFenbiSave()">💾 保存配置</button>
  </div>
  <div style="margin-top:10px">
    <label class="field" style="margin:0"><label>粉笔 Cookie（浏览器 F12 → Console 输入 document.cookie 复制整段粘贴；留空 = 保持不变）</label>
      <input id="ac-fb-cookie" type="password" placeholder="${d.cookie_set ? '已配置（' + esc(d.cookie_masked) + '），粘贴新值即覆盖' : '粘贴 document.cookie 整段内容'}" autocomplete="off"></label>
    <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn ghost sm" onclick="acFenbiTest()" id="ac-fb-test-btn">🔗 试拉验证 Cookie</button>
      <button class="btn ghost sm" onclick="acFenbiCollect()" id="ac-fb-col-btn">▶ 立即采集一轮</button>
      <button class="btn ghost sm" onclick="acFenbiClear()">🗑 清除 Cookie</button>
    </div>
    <div class="hint" id="ac-fb-state" style="margin-top:6px"></div>
    <div style="margin-top:8px;border-top:1px solid var(--line);padding-top:8px" id="ac-fb-funnel">${_fenbiFunnelHtml(d.last_run)}</div>
  </div>`;
}

async function acFenbiSave() {
  const payload = {
    enabled: ($('#ac-fb-enabled') || {}).value === '1',
    max_papers_per_run: parseInt(($('#ac-fb-papers') || {}).value || '3', 10),
    max_questions_per_run: parseInt(($('#ac-fb-questions') || {}).value || '200', 10),
    min_interval_secs: parseInt(($('#ac-fb-interval') || {}).value || '2', 10),
  };
  const ck = ($('#ac-fb-cookie') || {}).value || '';
  if (ck.trim()) payload.cookie = ck.trim();   // 留空=不变，不覆盖
  const st = $('#ac-fb-state');
  try {
    await admin('/admin/fenbi/config', { method: 'POST', body: JSON.stringify(payload) });
    if (st) { st.className = 'hint ok'; st.textContent = '✅ 已保存' + (ck.trim() ? '（Cookie 已更新）' : ''); }
    toast('粉笔采集配置已保存', 'ok');
    $('#ac-fb-cookie').value = '';
    acFenbiLoad();
  } catch (e) { if (st) { st.className = 'hint err'; st.textContent = '保存失败：' + (e.message || e); } }
}

async function acFenbiTest() {
  const btn = $('#ac-fb-test-btn'), st = $('#ac-fb-state');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 试拉中…'; }
  if (st) { st.className = 'hint'; st.textContent = '正在请求粉笔 API（约 5~10 秒）…'; }
  try {
    const r = await admin('/admin/fenbi/test', { method: 'POST' });
    const sp = r.sample_paper || {};
    const items = (r.sample_items || []).filter(Boolean);
    const preview = items[0] ? `${String(items[0].question || '').slice(0, 60)}…` : '';
    if (st) {
      st.className = 'hint ok';
      st.textContent = `✅ Cookie 有效：最近卷「${esc(sp.name || '')}」${sp.questionNums || 0} 题；样本第 1 题：${esc(preview)}`;
    }
    toast('✅ 粉笔 Cookie 验证通过', 'ok');
  } catch (e) {
    if (st) { st.className = 'hint err'; st.textContent = '❌ ' + (e.message || e); }
    toast('粉笔试拉失败：' + (e.message || e), 'err');
  }
  if (btn) { btn.disabled = false; btn.textContent = '🔗 试拉验证 Cookie'; }
}

async function acFenbiCollect() {
  const btn = $('#ac-fb-col-btn'), st = $('#ac-fb-state');
  try {
    const r = await admin('/admin/fenbi/collect', { method: 'POST' });
    if (!r.started) { toast('已有粉笔采集在后台运行', 'err'); return; }
    if (st) { st.className = 'hint ok'; st.textContent = '⏳ 采集已在后台启动（单轮按卷数/题量预算运行，数分钟内完成）…'; }
    toast('▶ 粉笔采集已启动', 'ok');
    let n = 0;
    clearInterval(_fenbiPollTimer);
    _fenbiPollTimer = setInterval(async () => {
      n++;
      try {
        const d = await admin('/admin/fenbi/config');
        const f = $('#ac-fb-funnel');
        if (f) f.innerHTML = _fenbiFunnelHtml(d.last_run);
        const run = $('#ac-fb-run');
        if (run) { run.className = 'hint ' + (d.running ? 'ok' : ''); run.textContent = d.running ? '⏳ 采集中…' : (d.enabled ? '🟢 守候已开启' : '⚪ 未启用'); }
        if (!d.running || n > 60) {
          clearInterval(_fenbiPollTimer);
          if (st && !d.running) { st.className = 'hint ok'; st.textContent = '✅ 采集结束，漏斗见下方统计'; }
        }
      } catch (e) { if (n > 3) clearInterval(_fenbiPollTimer); }
    }, 5000);
  } catch (e) { if (st) { st.className = 'hint err'; st.textContent = '启动失败：' + (e.message || e); } }
}

async function acFenbiClear() {
  if (!confirm('清除已保存的粉笔 Cookie？（守候将自动跳过粉笔源）')) return;
  try {
    await admin('/admin/fenbi/config', { method: 'POST', body: JSON.stringify({ cookie: '__CLEAR__' }) });
    toast('已清除粉笔 Cookie', 'ok');
    acFenbiLoad();
  } catch (e) { toast('清除失败：' + (e.message || e), 'err'); }
}

// ---- 公考真题库直采（批次13：gwy.gkzhenti.cn 官方接口，免凭据） ----
let _gkztPollTimer = null;

function _gkztFunnelHtml(lr) {
  if (!lr || !lr.at) return '<span class="hint">尚未运行过</span>';
  const f = lr.funnel || {};
  const err = (f.errors || []).length
    ? `<div class="hint err" style="margin-top:4px">⚠️ 失败卷：${esc(f.errors.map(x => String(x).slice(0, 80)).join('；'))}</div>` : '';
  return `<div class="hint ok" style="margin-top:4px">最近一轮 ${esc(String(lr.at).slice(0, 16))}：扫描 <b>${f.papers_scanned || 0}</b> 卷 → 解析 <b>${f.questions_parsed || 0}</b> 题 → 有答案 <b>${f.with_answer || 0}</b> 题 → 入库 <b>${f.added || 0}</b> 题（重复 ${f.skipped || 0} · 无效 ${f.invalid || 0} · 过滤 ${f.filtered || 0} · 缺答案 ${f.missing_answer || 0}）</div>${err}`;
}

async function acGkztLoad() {
  const box = $('#ac-gkzt-box');
  const run = $('#ac-gzt-run');
  if (!box) return;
  let d;
  try { d = await admin('/admin/gkzt/config'); }
  catch (e) { box.innerHTML = `<span class="hint err">读取失败：${esc(e.message || e)}</span>`; return; }
  if (run) {
    run.className = 'hint ' + (d.running ? 'ok' : '');
    run.textContent = d.running ? '⏳ 采集中…' : (d.enabled ? '🟢 守候已开启' : '⚪ 未启用');
  }
  box.innerHTML = `
  <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end">
    <div class="field" style="min-width:150px"><label>采集开关（守候窗口自动跑）</label>
      <select id="ac-gzt-enabled"><option value="0"${d.enabled ? '' : ' selected'}>关闭</option><option value="1"${d.enabled ? ' selected' : ''}>开启</option></select></div>
    <div class="field" style="min-width:110px"><label>单轮卷数上限</label><input id="ac-gzt-papers" type="number" min="1" max="50" value="${d.max_papers_per_run || 3}"></div>
    <div class="field" style="min-width:130px"><label>请求间隔（秒）</label><input id="ac-gzt-interval" type="number" min="1" max="30" value="${d.min_interval_secs || 2}"><div class="hint">≥2s 轻节流</div></div>
    <div class="field" style="min-width:130px"><label>公式/图形图片</label>
      <select id="ac-gzt-img"><option value="0"${d.with_images ? '' : ' selected'}>降级占位</option><option value="1"${d.with_images ? ' selected' : ''}>下载落盘</option></select></div>
    <button class="btn primary sm" onclick="acGkztSave()">💾 保存配置</button>
  </div>
  <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
    <button class="btn ghost sm" onclick="acGkztTest()" id="ac-gzt-test-btn">🔗 试拉（解析 2 卷，不入库）</button>
    <button class="btn ghost sm" onclick="acGkztCollect()" id="ac-gzt-col-btn">▶ 立即采集一轮</button>
  </div>
  <div class="hint" id="ac-gzt-state" style="margin-top:6px"></div>
  <div style="margin-top:8px;border-top:1px solid var(--line);padding-top:8px" id="ac-gzt-funnel">${_gkztFunnelHtml(d.last_run)}</div>`;
}

async function acGkztSave() {
  const payload = {
    enabled: ($('#ac-gzt-enabled') || {}).value === '1',
    max_papers_per_run: parseInt(($('#ac-gzt-papers') || {}).value || '3', 10),
    min_interval_secs: parseInt(($('#ac-gzt-interval') || {}).value || '2', 10),
    with_images: ($('#ac-gzt-img') || {}).value === '1',
  };
  const st = $('#ac-gzt-state');
  try {
    await admin('/admin/gkzt/config', { method: 'POST', body: JSON.stringify(payload) });
    if (st) { st.className = 'hint ok'; st.textContent = '✅ 已保存'; }
    toast('公考真题库采集配置已保存', 'ok');
    acGkztLoad();
  } catch (e) { if (st) { st.className = 'hint err'; st.textContent = '保存失败：' + (e.message || e); } }
}

async function acGkztTest() {
  const btn = $('#ac-gzt-test-btn'), st = $('#ac-gzt-state');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 试拉中…'; }
  if (st) { st.className = 'hint'; st.textContent = '正在请求 gkzhenti.cn 官方接口（约 10~30 秒）…'; }
  try {
    const r = await admin('/admin/gkzt/test', { method: 'POST' });
    const lines = (r.papers || []).map(p => `「${(p.paper || '').slice(0, 30)}」${p.parsed} 题/带图 ${p.with_images}`);
    if (st) {
      st.className = 'hint ok';
      st.textContent = `✅ 接口正常：共 ${r.papers_total} 卷可采；样本：${esc(lines.join('；'))}`;
    }
    toast('✅ 公考真题库接口验证通过', 'ok');
  } catch (e) {
    if (st) { st.className = 'hint err'; st.textContent = '❌ ' + (e.message || e); }
    toast('公考真题库试拉失败：' + (e.message || e), 'err');
  }
  if (btn) { btn.disabled = false; btn.textContent = '🔗 试拉（解析 2 卷，不入库）'; }
}

async function acGkztCollect() {
  const btn = $('#ac-gzt-col-btn'), st = $('#ac-gzt-state');
  try {
    const r = await admin('/admin/gkzt/collect', { method: 'POST' });
    if (!r.started) { toast('已有公考真题库采集在后台运行', 'err'); return; }
    if (st) { st.className = 'hint ok'; st.textContent = '⏳ 采集已在后台启动（单轮按卷数预算运行，每卷 3 页 + 图片，约 2~5 分钟）…'; }
    toast('▶ 公考真题库采集已启动', 'ok');
    let n = 0;
    clearInterval(_gkztPollTimer);
    _gkztPollTimer = setInterval(async () => {
      n++;
      try {
        const d = await admin('/admin/gkzt/config');
        const f = $('#ac-gzt-funnel');
        if (f) f.innerHTML = _gkztFunnelHtml(d.last_run);
        const run = $('#ac-gzt-run');
        if (run) { run.className = 'hint ' + (d.running ? 'ok' : ''); run.textContent = d.running ? '⏳ 采集中…' : (d.enabled ? '🟢 守候已开启' : '⚪ 未启用'); }
        if (!d.running || n > 60) {
          clearInterval(_gkztPollTimer);
          if (st && !d.running) { st.className = 'hint ok'; st.textContent = '✅ 采集结束，漏斗见下方统计'; }
        }
      } catch (e) { if (n > 3) clearInterval(_gkztPollTimer); }
    }, 5000);
  } catch (e) { if (st) { st.className = 'hint err'; st.textContent = '启动失败：' + (e.message || e); } }
}

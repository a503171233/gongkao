/**
 * 书山公考 · 管理后台 · 自动采集功能目录 · 共享层
 * ------------------------------------------------------------
 * 「自动采集」拆分为多个子页面（多通道 / 采集类型 / 采集内容 / 采集日志 / 总览）。
 * 各子页复用本文件：统一子页导航、配置全量合并保存、状态卡、失败详情、通道列表。
 *
 * 关键约定（防止拆分引入回归）：
 *   后端 PUT /admin/autocollect/config 为「全量覆盖」——省略的列表字段会被清空。
 *   因此每个子页保存时，都以全局缓存 _acCfg（最近一次 GET config 的完整生效值，扁平化
 *   为 payload 结构）为基底，仅用「本页实际渲染出来的输入项」覆盖对应键，再整体 PUT。
 */
'use strict';

// ---------- 子页注册表（供侧边栏二级目录 + 页内子页签 + hash 定位） ----------
const AC_SUBPAGES = [
  { id: 'ac-overview', ico: '🧭', label: '采集总览' },
  { id: 'ac-types',    ico: '🔌', label: '采集类型' },
  { id: 'ac-channels', ico: '🛰️', label: '通道配置' },
  { id: 'ac-content',  ico: '📦', label: '采集内容' },
  { id: 'ac-logs',     ico: '📜', label: '采集日志' },
];
const AC_TITLE_BY_ID = {};
AC_SUBPAGES.forEach(p => { AC_TITLE_BY_ID[p.id] = p.label; });

// 当前配置全量缓存（payload 扁平结构）。任何子页保存前都以此为基础做局部覆盖。
let _acCfg = null;
// 管理端自定义站点详情页规则，随「保存配置」一并提交
let _acSiteRules = [];
// 最近一次拉取的失败来源（含错误详情），供「失败详情」展示
let _acLastFailed = {};

// 由 GET /config 的响应构造 payload 基底（与后端 config_admin 字段一一对应）
function acCfgBase(cfg) {
  const _prios = (cfg.priorities && typeof cfg.priorities === 'object') ? cfg.priorities : {};
  const urls = Array.isArray(cfg.urls) ? cfg.urls.slice() : [];
  const nf = cfg.notify || {};
  return {
    enabled: !!cfg.enabled,
    interval_secs: Math.max(60, (cfg.interval_secs | 0) || 60),
    cpu_max: cfg.cpu_max != null ? cfg.cpu_max : 80,
    mem_max: cfg.mem_max != null ? cfg.mem_max : 80,
    max_per_cycle: cfg.max_per_cycle != null ? cfg.max_per_cycle : 100,
    max_site_pages: cfg.max_site_pages != null ? cfg.max_site_pages : 30,
    threads: cfg.threads != null ? cfg.threads : 1,
    autonomous: !!cfg.autonomous,
    autonomous_queries: cfg.autonomous_queries != null ? cfg.autonomous_queries : 12,
    schedule_enabled: !!cfg.schedule_enabled,
    schedule_start: cfg.schedule_start != null ? cfg.schedule_start : 2,
    schedule_end: cfg.schedule_end != null ? cfg.schedule_end : 6,
    retry_failed_auto: !!cfg.retry_failed_auto,
    llm_budget_chars: cfg.llm_budget_chars != null ? cfg.llm_budget_chars : 0,
    llm_price_per_1m: cfg.llm_price_per_1m != null ? cfg.llm_price_per_1m : 2.0,
    site_rules: Array.isArray(cfg.site_rules) ? cfg.site_rules.slice() : [],
    urls,
    search_queries: Array.isArray(cfg.search_queries) ? cfg.search_queries.slice() : [],
    site_whitelist: Array.isArray(cfg.site_whitelist) ? cfg.site_whitelist.slice() : [],
    site_pages_map: (cfg.site_pages_map && typeof cfg.site_pages_map === 'object')
      ? JSON.parse(JSON.stringify(cfg.site_pages_map)) : {},
    fallback_models: Array.isArray(cfg.fallback_models) ? cfg.fallback_models.slice() : [],
    priorities: _prios,
    quality_sample_rate: cfg.quality_sample_rate != null ? cfg.quality_sample_rate : 0.1,
    webhook_url: nf.webhook_url || '',
    notify_on: Array.isArray(nf.notify_on) ? nf.notify_on.slice() : [],
    fail_alert_threshold: nf.fail_alert_threshold != null ? nf.fail_alert_threshold : 3,
  };
}

// 拉取配置并刷新全量缓存；返回 {status, cfg}
async function acLoadConfig() {
  let st, cfg;
  try {
    [st, cfg] = await Promise.all([
      admin('/admin/autocollect/status'),
      admin('/admin/autocollect/config'),
    ]);
  } catch (e) { toast('获取自动采集配置失败：' + (e.message || e), 'err'); throw e; }
  _acCfg = acCfgBase(cfg);
  _acSiteRules = Array.isArray(cfg.site_rules) ? cfg.site_rules.slice() : [];
  return { st, cfg };
}

// 读取「本页实际存在」的输入并覆盖到基底；不存在的键保持 _acCfg 原值（避免全量清空）
function acBuildPayload() {
  const $ = (id) => document.getElementById(id);
  const has = (id) => !!$(id);
  const listOf = (id) => $(id).value ? $(id).value.split(/\n/).map(x => x.trim()).filter(Boolean) : [];
  const num = (id, dft) => { const v = parseInt($(id).value, 10); return isNaN(v) ? dft : v; };
  const fnum = (id, dft, low) => { const v = parseFloat($(id).value); const n = isNaN(v) ? dft : v; return low != null && n < low ? low : n; };
  const out = JSON.parse(JSON.stringify(_acCfg || acCfgBase({})));

  if (has('ac-en')) out.enabled = $('ac-en').value === '1';
  if (has('ac-interval')) out.interval_secs = Math.max(60, num('ac-interval', 60) * 60);
  if (has('ac-cpu')) out.cpu_max = Math.min(99, Math.max(1, num('ac-cpu', 80)));
  if (has('ac-mem')) out.mem_max = Math.min(99, Math.max(1, num('ac-mem', 80)));
  if (has('ac-budget')) out.max_per_cycle = Math.min(500, Math.max(1, num('ac-budget', 100)));
  if (has('ac-threads')) out.threads = Math.min(6, Math.max(1, num('ac-threads', 1)));
  if (has('ac-autonomous')) out.autonomous = $('ac-autonomous').value === '1';
  if (has('ac-auto-queries')) out.autonomous_queries = Math.min(100, Math.max(1, num('ac-auto-queries', 12)));
  if (has('ac-schedule-en')) out.schedule_enabled = $('ac-schedule-en').value === '1';
  if (has('ac-schedule-start')) out.schedule_start = Math.min(23, Math.max(0, num('ac-schedule-start', 2)));
  if (has('ac-schedule-end')) out.schedule_end = Math.min(23, Math.max(0, num('ac-schedule-end', 6)));
  if (has('ac-retry-auto')) out.retry_failed_auto = $('ac-retry-auto').value === '1';
  if (has('ac-llm-budget')) out.llm_budget_chars = Math.max(0, num('ac-llm-budget', 0));
  if (has('ac-llm-price')) out.llm_price_per_1m = fnum('ac-llm-price', 2.0, 0);
  if (has('ac-urls')) out.urls = listOf('ac-urls');
  if (has('ac-max-pages')) out.max_site_pages = Math.min(500, Math.max(1, num('ac-max-pages', 30)));
  if (has('ac-queries')) out.search_queries = listOf('ac-queries');
  if (has('ac-whitelist')) out.site_whitelist = listOf('ac-whitelist');
  if (has('ac-site-pages')) out.site_pages_map = acPageMap();
  if (has('ac-fallback')) out.fallback_models = listOf('ac-fallback');
  if (has('ac-quality-rate')) out.quality_sample_rate = Math.min(1, Math.max(0, fnum('ac-quality-rate', 0.1, 0)));
  if (has('ac-webhook')) out.webhook_url = ($('ac-webhook').value || '').trim();
  if (has('ac-fail-threshold')) out.fail_alert_threshold = Math.max(1, num('ac-fail-threshold', 3));
  if (has('ac-priorities')) {
    const priorMap = {};
    listOf('ac-priorities').forEach(line => {
      const i = line.lastIndexOf('|');
      if (i > 0) priorMap[line.slice(0, i).trim()] = line.slice(i + 1).trim();
      else priorMap[line.trim()] = 'medium';
    });
    out.priorities = priorMap;
  }
  if (has('ac-notify-summary') || has('ac-notify-fail')) {
    const on = [];
    if (has('ac-notify-summary') && $('ac-notify-summary').checked) on.push('summary');
    if (has('ac-notify-fail') && $('ac-notify-fail').checked) on.push('fail_alert');
    out.notify_on = on;
  }
  out.site_rules = _acSiteRules;
  return out;
}

async function acSaveConfig() {
  try {
    const r = await admin('/admin/autocollect/config', {
      method: 'PUT', body: JSON.stringify(acBuildPayload()),
    });
    _acCfg = acCfgBase(r);   // 用后端回读的生效值刷新基底
    toast('自动采集配置已保存并热生效' + (r.enabled ? '（守护已运行）' : '（已停用）'), 'ok');
    return r;
  } catch (e) { toast('保存失败：' + (e.message || e), 'err'); throw e; }
}

function acPageMap() {   // 解析"域名|页数"多行输入 → 对象
  const el = document.getElementById('ac-site-pages');
  const out = {};
  ((el && el.value) || '').split('\n').forEach(line => {
    const p = line.split('|').map(s => s.trim());
    const n = parseInt(p[1], 10);
    if (p[0] && !isNaN(n) && n > 0) out[p[0]] = n;
  });
  return out;
}

// ---------- 通用小工具 ----------
function hourOpts(defaultVal = 0) {
  let s = '';
  for (let h = 0; h < 24; h++) s += `<option value="${h}"${h === defaultVal ? ' selected' : ''}>${String(h).padStart(2, '0')}时</option>`;
  return s;
}

// 把 _acCfg 灌入本页存在的输入项（各子页渲染后调用；只填 DOM 里有的字段）
function acFillFromCfg() {
  const cfg = _acCfg || {};
  const $ = (id) => document.getElementById(id);
  const setVal = (id, v) => { const el = $(id); if (el) el.value = v == null ? '' : v; };
  const setSel = (id, v) => { const el = $(id); if (el) el.value = v ? '1' : '0'; };
  setSel('ac-en', cfg.enabled);
  setVal('ac-interval', cfg.interval_secs ? Math.round(cfg.interval_secs / 60) : '');
  setVal('ac-cpu', cfg.cpu_max); setVal('ac-mem', cfg.mem_max);
  setVal('ac-budget', cfg.max_per_cycle); setVal('ac-threads', cfg.threads);
  setSel('ac-autonomous', cfg.autonomous); setVal('ac-auto-queries', cfg.autonomous_queries);
  setSel('ac-schedule-en', cfg.schedule_enabled);
  setVal('ac-schedule-start', cfg.schedule_start); setVal('ac-schedule-end', cfg.schedule_end);
  setSel('ac-retry-auto', cfg.retry_failed_auto);
  setVal('ac-llm-budget', cfg.llm_budget_chars); setVal('ac-llm-price', cfg.llm_price_per_1m);
  setVal('ac-urls', (cfg.urls || []).join('\n'));
  setVal('ac-max-pages', cfg.max_site_pages);
  setVal('ac-queries', (cfg.search_queries || []).join('\n'));
  setVal('ac-whitelist', (cfg.site_whitelist || []).join('\n'));
  setVal('ac-site-pages', Object.entries(cfg.site_pages_map || {}).map(([k, v]) => `${k}|${v}`).join('\n'));
  setVal('ac-fallback', (cfg.fallback_models || []).join('\n'));
  setVal('ac-quality-rate', cfg.quality_sample_rate);
  const _prios = cfg.priorities || {};
  setVal('ac-priorities', (cfg.urls || []).map(u => {
    const p = _prios[u]; return p && p !== 'medium' ? u + '|' + p : u;
  }).join('\n'));
  setVal('ac-webhook', cfg.webhook_url || '');
  const on = cfg.notify_on || [];
  if ($('ac-notify-summary')) $('ac-notify-summary').checked = on.indexOf('summary') >= 0;
  if ($('ac-notify-fail')) $('ac-notify-fail').checked = on.indexOf('fail_alert') >= 0;
  setVal('ac-fail-threshold', cfg.fail_alert_threshold);
  const sr = $('ac-sr-count'); if (sr) sr.textContent = _acSiteRules.length;
}

// ---------- 页壳：页头 + 统一子页导航 + 正文容器 + 两个模态宿主 ----------
function acShell(activeId, desc) {
  const c = $('#content');
  const nav = AC_SUBPAGES.map(p =>
    `<button class="btn sm subnav-btn ${p.id === activeId ? 'active' : ''}" onclick="acSubTab('${p.id}')">${p.ico} ${esc(p.label)}</button>`
  ).join('');
  c.innerHTML = `
  <div class="page-head">
    <h2>自动采集 · ${esc(AC_TITLE_BY_ID[activeId] || '')}</h2>
    <p>${desc || ''}</p>
    <div class="ac-subnav" style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">${nav}</div>
  </div>
  <div id="ac-page-body"><div class="loading">加载中…</div></div>
  <div id="ac-modal" class="modal-mask"></div>
  <div id="ac-fs-modal" class="modal-mask"></div>`;
}
function acSubTab(id) { location.hash = '#/' + id; }

// ---------- 状态卡 ----------
function acStatusCards(r) {
  const lr = r.last_result;
  const lastAdded = lr ? (lr.added || 0) : 0;
  const es = (lr && lr.error_stats) || {};
  const errCats = Object.keys(es).filter(k => es[k] > 0);
  const cells = [
    ['采集开关', r.enabled ? '✅ 已启用' : '⚠️ 未启用',
      r.enabled ? '后台守候已按周期自动执行' : '采集开关保存即可启用（无需重建）'],
    ['运行状态', r.running ? '🟦 运行中' : '空闲',
      r.running ? '本轮采集进行中，稍后刷新' : '当前无自动采集任务在执行'],
    ['上次运行', fmtTime(r.last_run), r.last_run ? '最近一轮完成时间' : '从未运行'],
    ['本轮新增', (lastAdded) + ' 题',
      (lr && lr.skipped) ? '本轮被跳过，见运行历史' : '最近一轮成功入库题目数'],
    ['本轮失败分类', errCats.length ? errCats.map(k => `${k} ${es[k]}`).join(' · ') : '✅ 无失败',
      errCats.length ? '限速/超时/解析/网络/无题/其他 分类统计' : '最近一轮无失败记录'],
  ];
  const llu = (lr && lr.llm_usage) || {};
  const fu = (lr && lr.funnel) || {};
  if ((fu.search || fu.site) !== undefined) {
    const cand = (fu.site || 0) + (fu.search || 0), detail = fu.detail || 0, parse = fu.parse || 0,
      addedF = fu.added || 0, blockedF = fu.qualify || 0, pct = cand ? Math.round((addedF / cand) * 100) : 0;
    cells.push(['命中漏斗(本轮)', `${cand}候 → ${detail}拉到 → ${parse}抽取 → +${addedF}`,
      `搜索候选 ${fu.search || 0} · 站点 ${fu.site || 0} · 噪音/门控拦截 ${blockedF} · 入题率 ${pct}%（搜/站 ${cand} 算基）`]);
  }
  const ft = r.funnel_total || {};
  if ((ft.search || ft.site) !== undefined && (ft.search || ft.site || 0) > 0) {
    const tCand = (ft.site || 0) + (ft.search || 0), tAdd = ft.added || 0,
      tPct = tCand ? Math.round((tAdd / tCand) * 100) : 0;
    cells.push(['累计漏斗(长期)', `${tCand}候 → ${ft.detail || 0}拉到 → ${ft.parse || 0}抽取 → +${tAdd}`,
      `累计入题率 ${tPct}% · 搜索 ${ft.search || 0} · 站点 ${ft.site || 0} · 噪音/门控拦截 ${ft.qualify || 0}`]);
  }
  if (llu.jobs || llu.chars) {
    const price = llu.price_per_1m || 0;
    const cost = typeof llu.cost === 'number' ? llu.cost : (llu.tokens / 1000000 * price);
    const bud = (llu.budget_chars && llu.budget_chars > 0)
      ? `（预算 ${(llu.budget_chars / 1000).toFixed(0)}k，剩 ${llu.tokens_left != null ? (llu.tokens_left / 1000).toFixed(0) + 'k字' : '—'}）` : '（未设预算）';
    cells.push(['最近一轮 LLM', `${(llu.jobs || 0)} 次 · ${(llu.chars || 0).toLocaleString()} 字`,
      `估算 ${(cost || 0).toFixed(3)} 元 · ${(llu.tokens || 0).toLocaleString()} tok ${bud}`]);
  }
  return cells.map(([t, v, d]) =>
    `<div class="card"><div class="card-label">${esc(t)}</div><div class="card-n" style="font-size:16px">${v}</div><div class="card-sub">${esc(d)}</div></div>`).join('');
}

function acLastFailed() { return _acLastFailed || {}; }
function acFailedTip(fs) {
  const parts = [];
  if ((fs.urls || []).length) parts.push(`网址 ${fs.urls.length} 个`);
  if ((fs.docs || []).length) parts.push(`文档 ${fs.docs.length} 个`);
  if ((fs.queries || []).length) parts.push(`搜索词 ${fs.queries.length} 个`);
  return '待补采：' + (parts.join(' · ') || '无');
}
function acFailedDetail(fs) {
  const det = fs.errors_detail || {};
  const mapErr = (name) => (det[name] ? ` — <span style="color:var(--err,#c62828)">${esc(det[name].msg || '')}</span> <span class="hint">@${esc(det[name].ts || '')}</span>` : '');
  const rows = (arr) => (arr || []).length
    ? arr.map(x => `<li>${esc(x)}${mapErr(x)}</li>`).join('') : '<li class="empty">无</li>';
  openModal('ac-fs-modal', `
    <div style="min-width:460px;max-width:700px">
      <h3 style="margin:0 0 10px">失败来源 · 错误详情明细</h3>
      <h4 style="margin:8px 0 4px">网址（${(fs.urls || []).length}）</h4><ul>${rows(fs.urls)}</ul>
      <h4 style="margin:8px 0 4px">文档（${(fs.docs || []).length}）</h4><ul>${rows(fs.docs)}</ul>
      <h4 style="margin:8px 0 4px">搜索词（${(fs.queries || []).length}）</h4><ul>${rows(fs.queries)}</ul>
      <h4 style="margin:8px 0 4px">指数退避剩余（${(fs.backoff || []).length}）</h4>
      <ul>${(fs.backoff || []).length ? fs.backoff.map(b => `<li>${esc(b.key)} — 第${b.fails}次失败，约 ${Math.ceil((b.remaining_secs || 0) / 60)} 分钟后重试${b.msg ? ` <span class="hint">${esc(b.msg)}</span>` : ''}</li>`).join('') : '<li class="empty">无（未处于退避）</li>'}</ul>
      <h4 style="margin:8px 0 4px">AI 自评来源健康分（${Object.keys(fs.quality || {}).length}）</h4>
      <ul>${Object.keys(fs.quality || {}).length ? Object.entries(fs.quality).map(([k, v]) => `<li>${esc(k)} — 最近 <b>${esc(v.last_score)}</b>/100，抽查 ${esc(v.checks)} 次${(v.last_issue || []).length ? `，问题：${esc(v.last_issue.join('；'))}` : ''}</li>`).join('') : '<li class="empty">尚无抽检记录（抽样率>0 时逐轮将来源交给 LLM 评分）</li>'}</ul>
      <div class="hint" style="margin-top:10px">选中后再点一次「一键补采」即可仅重跑这些来源。</div>
    </div>`);
}

// 站点详情页规则管理：编辑 _acSiteRules，随「保存配置」一并提交
function acSiteRulesOpen() {
  const rows = _acSiteRules.map((r, i) => `
    <tr data-i="${i}">
      <td><input class="sr-domain" value="${esc(r.domain || '')}" placeholder="example.com"></td>
      <td><input class="sr-name" value="${esc(r.name || '')}" placeholder="题库名"></td>
      <td><input class="sr-re" value="${esc(r.detail_regex || '')}" placeholder="/detail/\\d+$"></td>
      <td><input class="sr-maxp" type="number" min="1" max="500" value="${esc(r.max_pages || 30)}"></td>
      <td><input type="checkbox" class="sr-en"${r.enabled !== false ? ' checked' : ''}></td>
      <td><button class="btn ghost sm" onclick="acSrDel(${i})">🗑</button></td>
    </tr>`).join('');
  const modal = `<div style="min-width:520px;max-width:820px">
    <h3 style="margin:0 0 4px">站点详情页规则 · 手动管理</h3>
    <p class="hint" style="margin:0 0 10px">命中域名后，本地上级优先于内建规则：只有命中「详情页 URL 正则」的页面会被送 LLM 提取，其余页面仅用于链接发现。改完在下方「更新到配置」，再点子页「保存配置」提交生效。</p>
    <button class="btn ghost sm" style="margin-bottom:8px" onclick="acSrAdd()">➕ 添加规则</button>
    <div class="tbl-wrap"><table class="tbl"><thead><tr>
      <th style="width:150px">域名</th><th style="width:120px">名称</th><th>详情页 URL 正则</th>
      <th style="width:80px">页上限</th><th style="width:50px">启用</th><th style="width:46px"></th>
    </tr></thead><tbody id="ac-sr-body">${rows || '<tr><td colspan="6" class="empty">尚无自定义规则</td></tr>'}</tbody></table></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
      <button class="btn primary sm" onclick="acSrApply()">✅ 更新到配置</button>
      <button class="btn ghost sm" onclick="closeModal('ac-modal')">关闭</button>
    </div>
  </div>`;
  openModal('ac-modal', modal);
}
function acSrAdd() { _acSiteRules.push({ domain: '', name: '', detail_regex: '', max_pages: 30, enabled: true }); acSiteRulesOpen(); }
function acSrDel(i) { _acSiteRules.splice(i, 1); acSiteRulesOpen(); }
function acSrApply() {
  const rowsEl = document.querySelectorAll('#ac-sr-body > tr');
  _acSiteRules = Array.from(rowsEl).map(tr => ({
    domain: (tr.querySelector('.sr-domain').value || '').trim(),
    name: (tr.querySelector('.sr-name').value || '').trim(),
    detail_regex: (tr.querySelector('.sr-re').value || '').trim(),
    max_pages: Math.max(1, parseInt(tr.querySelector('.sr-maxp').value, 10) || 30),
    enabled: tr.querySelector('.sr-en').checked,
  })).filter(r => r.domain && r.detail_regex);
  const c = document.getElementById('ac-sr-count'); if (c) c.textContent = _acSiteRules.length;
  toast('规则已更新到本地（' + _acSiteRules.length + ' 条），请点「保存配置」提交', 'ok');
}

// ---------- 运行控制（各子页复用；按钮宿主可选） ----------
async function acRunOnce() {
  if (!confirm('立即运行一轮自动采集？\n\n将绕过资源门槛真实调用 LLM 提取并写入题库（受每轮预算与并发锁保护）。\n开关关闭状态下也可手动试跑，请确认服务器负载与成本可接受。')) return;
  const btn = $('#ac-run-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 启动中…'; }
  try {
    const r = await admin('/admin/autocollect/run', { method: 'POST' });
    if (!r.started) { toast('未能启动：' + (r.reason || '已有采集在运行'), 'info'); return; }
    toast('已开始自动采集，正在后台运行，可通过采集日志页跟踪进度…', 'info');
    await acPollUntilDone(btn, '运行');
  } catch (e) { toast('启动自动采集失败：' + (e.message || e), 'err'); }
  finally { acRefreshControls(); }
}
async function acRetryFailed() {
  if (!confirm('仅重跑上一轮失败的采集来源？\n\n将跳过正常增量，只对失败的网址/文档/搜索词重新采集（受并发锁保护，真实调用 LLM）。\n请确认服务器负载与成本可接受。')) return;
  const btn = $('#ac-retry-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 补采启动中…'; }
  try {
    const r = await admin('/admin/autocollect/retry-failed', { method: 'POST' });
    if (!r.started) { toast('未能启动：' + (r.reason || '已有采集在运行'), 'info'); return; }
    toast('已开始补采失败来源，正在后台运行…', 'info');
    await acPollUntilDone(btn, '补采');
  } catch (e) { toast('启动补采失败：' + (e.message || e), 'err'); }
  finally { acRefreshControls(); }
}
// 轮询 status 直至 running=false；期间刷新可见的状态卡/日志
async function acPollUntilDone(btn, label) {
  let guard = null;
  if (btn) guard = setInterval(() => { if (btn) btn.textContent = '⏳ ' + label + '中…'; }, 5000);
  try {
    const MAX = 180;
    for (let i = 0; i < MAX; i++) {
      let st = {};
      try { st = await admin('/admin/autocollect/status') || {}; } catch (e) {}
      acRefreshControls();
      if (typeof acLogsLoad === 'function') acLogsLoad(false);
      if (!st.running) break;
      await new Promise(res => setTimeout(res, 5000));
    }
  } finally { if (guard) clearInterval(guard); }
}

// 刷新所有子页顶部的「状态卡 + 运行/补采按钮」区（宿主元素存在才更新）
async function acRefreshControls() {
  let st, cfg;
  try {
    [st, cfg] = await Promise.all([
      admin('/admin/autocollect/status'),
      admin('/admin/autocollect/config'),
    ]);
  } catch (e) { return; }
  _acCfg = acCfgBase(cfg);
  _acSiteRules = Array.isArray(cfg.site_rules) ? cfg.site_rules.slice() : [];
  const cards = $('#ac-cards');
  if (cards) cards.innerHTML = acStatusCards(st);
  const btn = $('#ac-run-btn');
  if (btn) { btn.disabled = !!st.running; btn.textContent = st.running ? '⏳ 运行中…' : '▶ 立即运行一轮'; }
  const rbtn = $('#ac-retry-btn');
  if (rbtn) {
    const fs = st.failed_sources || {};
    _acLastFailed = fs;
    const n = fs.count || 0;
    rbtn.disabled = !!st.running || n === 0;
    rbtn.textContent = n > 0 ? `🔁 一键补采失败（${n}）` : '🔁 一键补采失败';
    rbtn.title = n > 0 ? acFailedTip(fs) : '当前无失败来源';
  }
}

// 统一的状态总览面板 HTML（各子页顶部嵌一份，保持功能可见一致）
function acControlPanelHtml() {
  return `
  <div class="panel">
    <div class="panel-head">
      <div class="toolbar" style="flex:1"><b>状态总览</b></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn ghost sm" onclick="acRefreshControls()">⟳ 刷新状态</button>
        <button class="btn ghost sm" id="ac-retry-btn" onclick="acRetryFailed()">🔁 一键补采失败</button>
        <button class="btn ghost sm" id="ac-reterr-btn" onclick="acFailedDetail(acLastFailed())" title="查看每个失败来源的错误原因/时间">🔍 失败详情</button>
        <button class="btn primary sm" id="ac-run-btn" onclick="acRunOnce()">▶ 立即运行一轮</button>
      </div>
    </div>
    <div class="cards" id="ac-cards" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));padding:0 14px 14px"></div>
  </div>`;
}

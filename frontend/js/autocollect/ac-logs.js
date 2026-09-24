/**
 * 自动采集 · 采集日志子页
 * 模块一：运行历史与统计（近 100 轮，含网址/文档/搜索分渠道入库统计）
 * 模块二：实时任务日志（4s 轮询，滑动滚动，追最新/防打断）
 */
'use strict';

let acLogsVisible = false;   // 仅本页激活时轮询日志（acPollUntilDone 据此决定是否刷日志）
let _acLogsTimer = null;
let _acLogsAuto = true;

function startAcLogsPoll() {
  stopAcLogsPoll();
  _acLogsAuto = true;
  _acLogsTimer = setInterval(() => { if (_acLogsAuto && acLogsVisible) acLogsLoad(false); }, 4000);
}
function stopAcLogsPoll() {
  acLogsVisible = false;
  if (_acLogsTimer) { clearInterval(_acLogsTimer); _acLogsTimer = null; }
}

async function renderAcLogs() {
  acShell('ac-logs', '每轮运行的历史统计（网址/文档/搜索分渠道入库）· 模型实时任务日志');
  const body = $('#ac-page-body');
  body.innerHTML = `
  ${acControlPanelHtml()}
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap"><b>运行历史与统计</b><span id="ac-hstats" style="margin-left:8px"></span></div>
      <button class="btn ghost sm" onclick="acHistoryLoad()">⟳ 刷新历史</button>
    </div>
    <div id="ac-history" class="empty" style="padding:14px">加载中…</div>
  </div>
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap">
        <b>实时任务日志</b>
        <span class="hint" style="margin-left:8px">模型做的每个采集任务实时滚动展示（最近 80 条，进程内存环形缓冲）</span>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn ghost sm" onclick="acLogsLoad(true)">⟳ 刷新</button>
        <button class="btn ghost sm" onclick="acLogsAuto()" id="ac-logs-auto">⏸ 暂停自动</button>
      </div>
    </div>
    <div class="ac-logs" id="ac-logs" style="margin:14px"><div class="empty" style="padding:14px">加载中…</div></div>
  </div>`;
  acLogsVisible = true;
  acRefreshControls();
  acHistoryLoad();
  acLogsLoad(false);
  startAcLogsPoll();
}

function acLogsAuto() {
  _acLogsAuto = !_acLogsAuto;
  const btn = $('#ac-logs-auto');
  if (btn) btn.textContent = _acLogsAuto ? '⏸ 暂停自动' : '▶ 恢复自动';
  toast(_acLogsAuto ? '实时日志自动刷新已开启（每 4 秒）' : '实时日志自动刷新已暂停', 'info');
}

async function acLogsLoad(scroll) {
  const box = $('#ac-logs');
  if (!box) return;
  let logs;
  try {
    const r = await admin('/admin/autocollect/logs?limit=80');
    logs = r.logs || [];
  } catch (e) {
    if (scroll) toast('获取实时日志失败：' + (e.message || e), 'err');
    return;
  }
  if (!logs.length) {
    box.innerHTML = '<div class="empty" style="padding:14px">暂无任务日志；点击「立即运行一轮」后，模型做的每个任务会在这里实时滚动展示</div>';
    return;
  }
  // 滑动显示：仅在用户位于顶部（正在追最新）时自动吸附到顶部；
  // 用户已向下滚动阅读历史时，保留其相对位置，避免每 4 秒被强制回顶打断阅读。
  const wasTop = box.scrollTop <= 4;
  const prevH = box.scrollHeight;
  const prevTop = box.scrollTop;
  box.innerHTML = logs.map(acLogRowHtml).join('');
  box.scrollTop = (box.scrollHeight - prevH) + prevTop;  // 锚住用户正在看的位置
  if ((_acLogsAuto && wasTop) || scroll) box.scrollTop = 0;  // 追最新（顶部）
}

function acLogRowHtml(l) {
  const ok = !!l.ok;
  const color = ok ? 'var(--ok,#2e7d32)' : '#b71c1c';
  return `<div class="ac-log ${ok ? '' : 'ac-log-err'}">
      <span class="ac-log-ts">${esc(l.ts || '')}</span>
      <span class="ac-log-model">[${esc(l.model || '')}]</span>
      <span class="ac-log-action" style="color:${color}">${ok ? '✓' : '✗'} ${esc(l.action || '')}</span>
      ${l.base_url ? `<span class="ac-log-base">@${esc(l.base_url)}</span>` : ''}
      ${l.detail ? `<span class="ac-log-detail">${esc(l.detail)}</span>` : ''}
    </div>`;
}

async function acHistoryLoad() {
  let runs = [];
  try {
    const r = await admin('/admin/autocollect/history?limit=100');
    runs = r.runs || [];
  } catch (e) {
    const box = $('#ac-history');
    if (box) box.innerHTML = '<span class="hint err">获取历史失败：' + esc(e.message || e) + '</span>';
    return;
  }
  const stats = $('#ac-hstats');
  if (stats) {
    const totalAdd = runs.reduce((s, x) => s + (x.added || 0), 0);
    const urlAdd = runs.reduce((s, x) => s + ((x.urls || {}).added || 0), 0);
    const docAdd = runs.reduce((s, x) => s + ((x.docs || {}).added || 0), 0);
    const sAdd = runs.reduce((s, x) => s + ((x.search || {}).added || 0), 0);
    const dupN = runs.reduce((s, x) => s + ((x.filtered || {}).duplicates || 0), 0);
    const invN = runs.reduce((s, x) => s + ((x.filtered || {}).invalid || 0), 0);
    const offN = runs.reduce((s, x) => s + ((x.filtered || {}).offdomain || 0), 0);
    stats.innerHTML = `<span class="hint">近 ${runs.length} 轮 · 累计入库 <b style="color:var(--ok,#2e7d32)">${totalAdd}</b> 题（网址 ${urlAdd} · 文档 ${docAdd} · 搜索 ${sAdd}）· 过滤重复 ${dupN} · 残缺 ${invN}${offN ? ` · 剔除非公考 ${offN}` : ''}</span>`;
  }
  const box = $('#ac-history');
  if (box) box.innerHTML = acHistoryHtml(runs);
}

function acHistoryHtml(runs) {
  if (!runs.length) return '<span class="hint">暂无运行记录（手动点「立即运行一轮」后生成）</span>';
  const SHOW = 10;  // 只展示最近 10 条，多余隐藏于滚动区
  const visible = runs.slice(0, SHOW);
  const hidden = runs.length - visible.length;
  const rows = visible.map(x => {
    const u = x.urls || {}, d = x.docs || {}, s = x.search || {}, f = x.filtered || {};
    const errN = (x.errors && x.errors.length) ? x.errors.length : 0;
    const errTip = errN ? `<span class="hint" style="color:#b71c1c">⚠ ${errN}</span>` : '<span class="hint" style="color:var(--ok,#2e7d32)">✓</span>';
    return `<tr>
      <td>${esc((x.ts || '').replace('T', ' ').replace('Z', ''))}</td>
      <td>${(x.added || 0) > 0 ? '<b style="color:var(--ok,#2e7d32)">+' + (x.added || 0) + '</b>' : '0'}</td>
      <td>${u.attempted || 0}/${u.added || 0}</td>
      <td>${d.scanned || 0}·${d.processed || 0}/${d.added || 0}</td>
      <td>${s.attempted || 0}/${s.added || 0}</td>
      <td>${f.duplicates || 0}/${f.invalid || 0}</td>
      <td>${errTip}</td>
    </tr>`;
  }).join('');
  return `<div style="max-height:330px;overflow:auto">
      <table class="tbl" style="width:100%"><thead><tr><th>时间(UTC)</th><th>新增</th><th>网址 尝试/入</th><th>文档 扫·处/入</th><th>搜索 试/入</th><th>过滤 重/残</th><th>错误</th></tr></thead><tbody>${rows}</tbody></table>
    </div>` +
    (hidden > 0 ? `<div class="hint" style="padding:6px 2px 0">… 共 ${runs.length} 条，仅展示最近 ${SHOW} 条（${hidden} 条已隐藏，滚动查看）</div>` : '') +
    (runs[0] && runs[0].errors && runs[0].errors.length
      ? `<div style="margin-top:8px;color:#b71c1c;font-size:12.5px">最近一轮错误：${runs[0].errors.map(e => esc(e)).join('<br>')}</div>` : '');
}

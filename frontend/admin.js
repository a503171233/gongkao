/**
 * 书山公考 · 管理后台（#16 全面重构）
 * - 书卷主题 · 工业实用风格，与前台官网统一视觉
 * - 纯原生 JS，无框架，依赖 GK.a4Request
 * - 鉴权：Bearer token 存 localStorage['admin_token']
 * - 页面：/admin.html → admin.js
 */
'use strict';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// ---------- 状态 ----------
let token = localStorage.getItem('admin_token') || '';
let currentTab = 'dashboard';

// ---------- 工具 ----------
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function fmtDate(s) { return s ? String(s).slice(0, 10) : '—'; }
function fmtTime(s) { return s ? String(s).slice(0, 19) : '—'; }

// #33 R2 题干图片渲染：题干中的 ![图N](/api/qimg/...) 标记 → <img>
// URL 白名单与后端 ingest.QIMG_URL_RE 一致（仅 /api/qimg/{tid}/{sha1前12}.{ext}），防 XSS
const _QIMG_URL_RE = /^\/api\/qimg\/[A-Za-z0-9_-]+\/[a-f0-9]{12}\.(png|jpe?g|gif|webp)$/;
const _QIMG_MARK_RE = /!\[([^\]]*)\]\(([^)\s]+)\)/g;
function qText(text, opts = {}) {
  return esc(text).replace(_QIMG_MARK_RE, (m, alt, url) => {
    if (!_QIMG_URL_RE.test(url)) return '';
    const st = opts.max ? ` style="max-height:${parseInt(opts.max, 10) || 200}px"` : '';
    return `<img class="q-img"${st} loading="lazy" alt="${alt}" src="${url}" onclick="window.open('${url}','_blank')" title="点击查看大图">`;
  });
}
// 纯文本版（列表预览/导出用）：图片标记折叠为 [图]
function qTextPlain(text) {
  return String(text || '').replace(_QIMG_MARK_RE, '[图]');
}
function fmtNum(n) {
  const v = Number(n || 0);
  if (v >= 10000) return (v / 10000).toFixed(1) + 'w';
  return String(v);
}
// 文件大小人类可读（AI 采集上传提示用）
function fmtSize(bytes) {
  const b = Number(bytes || 0);
  if (b >= 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + 'MB';
  if (b >= 1024) return (b / 1024).toFixed(0) + 'KB';
  return b + 'B';
}
function debounce(fn, ms = 400) {
  let t = null;
  return function (...args) { clearTimeout(t); t = setTimeout(() => fn.apply(this, args), ms); };
}

// ---------- Toast ----------
function toast(msg, type = 'info', ms = 2600) {
  let box = $('#toast');
  if (!box) { box = document.createElement('div'); box.id = 'toast'; document.body.appendChild(box); }
  const el = document.createElement('div');
  el.className = 'toast-item ' + type;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; setTimeout(() => el.remove(), 320); }, ms);
}

// ---------- API 封装 ----------
function admin(path, opts = {}) {
  if (!token) throw new Error('未登录');
  return GK.api(path, {
    ...opts,
    headers: { Authorization: `Bearer ${token}`, ...(opts.headers || {}) },
  });
}

// ---------- 登录 ----------
async function doLogin(username, password) {
  const r = await GK.api('/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  if (r.role !== 'admin') throw new Error('非管理员账号，无权登录后台');
  token = r.token;
  localStorage.setItem('admin_token', token);
  if (window.GK && GK.store) GK.store.token = token;  // 让 GK.api 自动带 Bearer（刷新后 /me 需用它验证）
  showApp();
}
async function doLogout() {
  try { await admin('/me/logout', { method: 'POST' }); } catch (_) {}
  token = '';
  localStorage.removeItem('admin_token');
  if (window.GK && GK.store) GK.store.token = '';
  showLogin();
}
function adminLogin() {
  const u = $('#lu').value.trim();
  const p = $('#lp').value;
  $('#lg-hint').textContent = '登录中…';
  doLogin(u, p).catch(e => {
    const h = $('#lg-hint');
    h.textContent = e.message || '登录失败';
    h.className = 'lg-hint err';
  });
}

// ---------- 视图 ----------
const TABS = [
  { id: 'dashboard',  ico: '📊', label: '仪表盘' },
  { id: 'teachers',   ico: '👨‍🏫', label: '老师管理' },
  { id: 'users',      ico: '👥', label: '用户管理' },
  { id: 'recharge',   ico: '🔑', label: '充值码' },
  { id: 'forummod',   ico: '🛡️', label: '论坛治理' },
  { id: 'tracking',   ico: '📈', label: '运营统计' },
  { id: 'messages',   ico: '📨', label: '消息中心' },
  { id: 'qbankfiles', ico: '📁', label: '题库文件', children: [
      { id: 'documents', ico: '📚', label: '文档管理' },
      { id: 'questions', ico: '📝', label: '题库管理' },
      { id: 'knowledge', ico: '🌳', label: '知识体系' },
      { id: 'feedback',  ico: '💬', label: '答案反馈' },
  ]},
  { id: 'autocollect', ico: '⚙️', label: '自动采集', children: [
      { id: 'ac-overview',  ico: '🧭', label: '采集总览' },
      { id: 'ac-types',     ico: '🔌', label: '采集类型' },
      { id: 'ac-channels',  ico: '🛰️', label: '通道配置' },
      { id: 'ac-content',   ico: '📦', label: '采集内容' },
      { id: 'ac-logs',      ico: '📜', label: '采集日志' },
  ]},
  { id: 'settings',   ico: '🛠️', label: '系统设置', children: [
      { id: 'aimodels', ico: '🤖', label: 'AI模型' },
      { id: 'banners',  ico: '🏷️', label: '轮播管理' },
      { id: 'backups',  ico: '💾', label: '数据备份' },
  ]},
];
const TITLES = {
  dashboard: '仪表盘', teachers: '老师管理', aimodels: 'AI模型', users: '用户管理',
  documents: '文档管理', questions: '题库管理', knowledge: '知识体系',
  recharge: '充值码', feedback: '答案反馈', forummod: '论坛治理', tracking: '运营统计',
  banners: '轮播管理', messages: '消息中心', backups: '数据备份', autocollect: '自动采集',
  'ac-overview': '自动采集 · 采集总览', 'ac-types': '自动采集 · 采集类型',
  'ac-channels': '自动采集 · 通道配置', 'ac-content': '自动采集 · 采集内容',
  'ac-logs': '自动采集 · 采集日志',
};

function showLogin(msg) {
  $('#app').innerHTML = `
  <div class="login-page">
    <div class="login-card">
      <div class="lg-mark">书</div>
      <h2>书山公考 · 管理后台</h2>
      <p class="lg-sub">多老师专属智能助教平台 · 运营管理中心</p>
      <div class="field"><label for="lu">用户名</label><input id="lu" placeholder="管理员用户名" autocomplete="off"></div>
      <div class="field"><label for="lp">密码</label><input id="lp" type="password" placeholder="密码"></div>
      <button class="btn primary" onclick="adminLogin()">登 录</button>
      <p class="lg-hint ${msg ? 'err' : ''}" id="lg-hint">${msg ? esc(msg) : ''}</p>
    </div>
  </div>`;
  const onEnter = (e) => { if (e.key === 'Enter') adminLogin(); };
  const lu = $('#lu'), lp = $('#lp');
  if (lu) lu.addEventListener('keydown', onEnter);
  if (lp) lp.addEventListener('keydown', onEnter);
}

function showApp() {
  $('#app').innerHTML = `
  <div class="layout">
    <aside class="sidebar">
      <div class="side-brand">
        <div class="mark">书</div>
        <div><div class="t1">书山公考</div><div class="t2">ADMIN CONSOLE</div></div>
      </div>
      <nav class="side-nav" id="sideNav">
        ${TABS.map(t => t.children ? (`
          <div class="nav-group">
            <button class="nav-item nav-parent" id="nav-${t.id}" onclick="toggleNavGroup('${t.id}')">
              <span class="ico">${t.ico}</span><span>${t.label}</span><span class="caret">▸</span>
            </button>
            <div class="nav-children" id="nav-kids-${t.id}" style="display:none">
              ${t.children.map(k => `<button class="nav-item nav-child" id="nav-${k.id}" onclick="switchTab('${k.id}')">
                <span class="ico">${k.ico}</span><span>${k.label}</span></button>`).join('')}
            </div>
          </div>`) : (`
          <button class="nav-item" id="nav-${t.id}" onclick="switchTab('${t.id}')">
            <span class="ico">${t.ico}</span><span>${t.label}</span></button>`)).join('')}
      </nav>
      <div class="side-foot">
        <div class="admin-chip"><span>🔐</span><b>管理员</b></div>
        <button class="btn ghost sm" style="width:100%;justify-content:center" onclick="doLogout()">退出登录</button>
      </div>
    </aside>
    <div class="main">
      <div class="topbar">
        <h1 id="pageTitle">仪表盘</h1>
        <span class="crumb" id="pageCrumb"></span>
        <div class="top-actions">
          <button class="refresh-btn" onclick="switchTab(currentTab)">⟳ 刷新</button>
          <a class="refresh-btn" href="/" target="_blank">🏠 官网</a>
          <a class="refresh-btn" href="/chat.html" target="_blank">🎓 学习中心</a>
        </div>
      </div>
      <div id="content"></div>
    </div>
  </div>`;
  // 首屏深链：#tab 或 #/tab 命中导航项则直达，否则进仪表盘
  const h0 = (location.hash || '').replace(/^#\/?/, '');
  switchTab(NAV_IDS[h0] ? h0 : 'dashboard');
}

function navParentOf(tab) {
  for (const t of TABS) {
    if (t.children && t.children.some(k => k.id === tab)) return t.id;
  }
  return null;
}

// ---------- hash 路由：#tab 或 #/tab（自动采集子页 #/ac-logs 等），浏览器前进/后退可用 ----------
function tabFromHash() {
  const m = (location.hash || '').match(/^#\/?([A-Za-z0-9_-]+)/);
  return m ? m[1] : null;
}
window.addEventListener('hashchange', () => {
  const h = tabFromHash();
  if (h && NAV_IDS[h] && h !== currentTab) switchTab(h);
});
window.addEventListener('popstate', () => {
  const h = tabFromHash() || 'dashboard';
  if (NAV_IDS[h] && h !== currentTab) switchTab(h);
});
const NAV_IDS = (() => {
  const set = {};
  TABS.forEach(t => { set[t.id] = 1; (t.children || []).forEach(k => set[k.id] = 1); });
  return set;
})();

function openNavGroup(id) {
  const kids = $('#nav-kids-' + id);
  if (kids) kids.style.display = '';
  const p = $('#nav-' + id);
  if (p) p.classList.add('open');
}

function toggleNavGroup(id) {
  const kids = $('#nav-kids-' + id);
  const p = $('#nav-' + id);
  if (!kids) return;
  const open = kids.style.display !== 'none';
  kids.style.display = open ? 'none' : '';
  if (p) p.classList.toggle('open', !open);
}

async function switchTab(tab) {
  if (NAV_IDS[tab] && ('#/' + tab) !== (location.hash || '') && tab !== 'autocollect') {
    try { history.pushState(null, '', '#/' + tab); } catch (e) { location.hash = '#/' + tab; }
  }
  stopAcLogsPoll();
  currentTab = tab;
  $$('.nav-item').forEach(b => b.classList.remove('active'));
  const parent = navParentOf(tab);
  if (parent) openNavGroup(parent);
  const nav = $('#nav-' + tab);
  if (nav) nav.classList.add('active');
  const t = $('#pageTitle');
  if (t) t.textContent = TITLES[tab] || '管理后台';
  const c = $('#content');
  c.innerHTML = `<div class="loading">加载中…</div>`;
  try {
    if (tab === 'dashboard') await loadDashboard();
    else if (tab === 'teachers') await renderTeachers();
    else if (tab === 'aimodels') await renderAiModels();
    else if (tab === 'users') await renderUsers();
    else if (tab === 'documents') await renderDocuments();
    else if (tab === 'questions') await renderQuestions();
    else if (tab === 'knowledge') await renderKnowledge();
    else if (tab === 'recharge') await renderRecharge();
    else if (tab === 'feedback') await renderFeedback();
    else if (tab === 'forummod') await renderForumMod();
    else if (tab === 'tracking') await renderTracking();
    else if (tab === 'incentive') await renderIncentive();
    else if (tab === 'banners') await renderBanners();
    else if (tab === 'messages') await renderMessages();
    else if (tab === 'backups') await renderBackups();
    else if (tab === 'qbackups') await renderBackups();
    else if (tab === 'autocollect') { currentTab = ''; await switchTab('ac-overview'); return; }   // 旧入口兼容 → 采集总览（直接分发，不依赖 hashchange）
    else if (tab === 'ac-overview')  await renderAcOverview();
    else if (tab === 'ac-types')     await renderAcTypes();
    else if (tab === 'ac-channels')  await renderAcChannels();
    else if (tab === 'ac-content')   await renderAcContent();
    else if (tab === 'ac-logs')      await renderAcLogs();
  } catch (e) {
    c.innerHTML = `<p class="err-tip">加载失败：${esc(e.message)}</p>`;
  }
}

// ============================================================
// 仪表盘（#16 新增：核心数据 + 图表 + 刷新）
// ============================================================
let _charts = {};  // 保存 Chart 实例便于重绘销毁

function destroyCharts() {
  Object.keys(_charts).forEach(k => {
    try { _charts[k].destroy(); } catch (e) {}
    delete _charts[k];
  });
}

async function loadDashboard() {
  const c = $('#content');
  destroyCharts();
  c.innerHTML = `
  <div class="page-head"><h2>平台仪表盘</h2><p>核心运营数据一览 · 自动聚合自 auth.db / chat.db / 运行指标</p></div>
  <div class="cards" id="dashCards"></div>
  <div class="chart-grid" style="margin-top:18px">
    <div class="chart-box"><h4>今日新增用户趋势</h4><canvas id="chNewUsers" height="120"></canvas></div>
    <div class="chart-box"><h4>老师请求热度</h4><canvas id="chTeachers" height="120"></canvas></div>
    <div class="chart-box"><h4>额度消耗分布（按日）</h4><canvas id="chQuota" height="120"></canvas></div>
    <div class="chart-box"><h4>题库结构</h4><canvas id="chQbank" height="120"></canvas></div>
  </div>
  <div class="panel">
    <div class="panel-head"><h3>📈 运行指标</h3><button class="btn ghost sm" onclick="loadDashboard()">⟳ 刷新数据</button></div>
    <div class="panel-body" id="dashMetrics"></div>
  </div>`;

  // 并行拉取
  const [stats, overview, quota, teacherSt, qbStats] = await Promise.all([
    admin('/admin/stats').catch(() => null),
    admin('/admin/stats/overview').catch(() => null),
    admin('/admin/stats/quota').catch(() => null),
    admin('/admin/stats/teachers').catch(() => null),
    admin('/admin/questions/stats').catch(() => null),
  ]);

  // 统计卡片
  const s = stats || { users: {}, teachers: {}, knowledge: {} };
  const ov = overview || {};
  $('#dashCards').innerHTML = `
    <div class="card"><div class="card-label">总用户</div><div class="card-n">${fmtNum(s.users.total)}</div><div class="card-sub">今日新增 ${ov.today_new_users ?? '—'}</div></div>
    <div class="card"><div class="card-label">会员用户</div><div class="card-n green">${fmtNum(s.users.members)}</div><div class="card-sub">${(s.users.total ? (s.users.members / s.users.total * 100).toFixed(1) : 0)}% 会员率</div></div>
    <div class="card"><div class="card-label">在线老师</div><div class="card-n gold">${s.teachers.enabled ?? '—'}</div><div class="card-sub">共 ${s.teachers.total ?? '—'} 位</div></div>
    <div class="card"><div class="card-label">知识切片</div><div class="card-n">${fmtNum(s.knowledge.total_chunks)}</div><div class="card-sub">${s.knowledge.total_documents ?? 0} 部文档</div></div>
    <div class="card"><div class="card-label">题库题目</div><div class="card-n">${fmtNum((qbStats || {}).total)}</div><div class="card-sub">AI 采集可批量入库</div></div>
    <div class="card"><div class="card-label">今日提问</div><div class="card-n green">${ov.today_ask ?? '—'}</div><div class="card-sub">累计 ${ov.ask_total ?? 0} 次</div></div>`;

  // 图表：需要 Chart.js
  await ensureChartJs();
  const chartData = [];

  // 1) 今日新增用户：用 overview 的今日数值 + 额度 by_day 做近似趋势
  const byDay = (quota && quota.by_day) || [];
  chartData.push({ id: 'chNewUsers', type: 'bar', label: '新增/活跃', data: byDay.slice(0, 14).map(d => ({ x: d.date.slice(5), y: d.used })) });

  // 2) 老师请求热度
  const tSt = (teacherSt && teacherSt.teachers) || [];
  chartData.push({
    id: 'chTeachers', type: 'bar', label: '请求数',
    data: tSt.slice(0, 8).map(t => ({ x: (t.teacher_name || t.teacher_id).slice(0, 8), y: t.requests })),
  });

  // 3) 额度消耗分布
  chartData.push({ id: 'chQuota', type: 'line', label: '使用量', data: byDay.slice(0, 30).map(d => ({ x: d.date.slice(5), y: d.used })) });

  // 4) 题库结构（题型分布）
  const byType = (qbStats && qbStats.by_type) || [];
  const typeMap = { choice: '选择', judge: '判断', essay: '简答' };
  chartData.push({
    id: 'chQbank', type: 'doughnut', label: '题量',
    data: byType.map(t => ({ x: typeMap[t.qtype] || t.qtype, y: t.n })),
  });

  chartData.forEach(cd => {
    const cv = $('#' + cd.id);
    if (!cv || typeof Chart === 'undefined') return;
    const isDonut = cd.type === 'doughnut';
    const labels = cd.data.map(d => d.x);
    const vals = cd.data.map(d => d.y);
    const colors = ['#8b5e3c', '#c9a05a', '#7a9e6d', '#b45a4a', '#6f4a2d', '#e6d3ac', '#a5813f'];
    _charts[cd.id] = new Chart(cv, {
      type: cd.type,
      data: {
        labels,
        datasets: [{
          label: cd.label,
          data: vals,
          backgroundColor: isDonut ? colors.slice(0, vals.length) : 'rgba(139,94,60,.72)',
          borderColor: isDonut ? '#fffaf0' : 'rgba(139,94,60,.9)',
          borderWidth: isDonut ? 2 : 1,
          tension: .35,
          fill: cd.type === 'line',
        }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: isDonut, position: 'right', labels: { color: '#6b655a', font: { size: 11 } } },
          tooltip: { backgroundColor: '#3d3a33', titleColor: '#fffaf0', bodyColor: '#f6f1e7' },
        },
        scales: isDonut ? {} : {
          x: { grid: { color: 'rgba(226,217,198,.5)' }, ticks: { color: '#98917f', font: { size: 10.5 } } },
          y: { beginAtZero: true, grid: { color: 'rgba(226,217,198,.5)' }, ticks: { color: '#98917f', font: { size: 10.5 } } },
        },
      },
    });
  });

  // 运行指标面板
  const g = ov.guard_total ?? 0;
  const latency = ov.avg_latency_ms ?? 0;
  const uptime = ov.uptime_seconds ?? 0;
  const uptimeStr = uptime > 86400 ? (uptime / 86400).toFixed(1) + ' 天'
    : uptime > 3600 ? (uptime / 3600).toFixed(1) + ' 小时'
    : uptime + ' 秒';
  const memberRate = (s.users.total ? (s.users.members / s.users.total * 100) : 0).toFixed(1);
  $('#dashMetrics').innerHTML = `
    <div class="cards" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
      <div class="card"><div class="card-label">累计提问</div><div class="card-n">${fmtNum(ov.ask_total)}</div><div class="card-sub">失败 ${ov.ask_fail ?? 0}</div></div>
      <div class="card"><div class="card-label">总会话数</div><div class="card-n">${fmtNum(ov.total_sessions)}</div><div class="card-sub">历史会话</div></div>
      <div class="card"><div class="card-label">幻觉拦截</div><div class="card-n red">${fmtNum(g)}</div><div class="card-sub">进程内累计</div></div>
      <div class="card"><div class="card-label">平均时延</div><div class="card-n gold">${Number(latency).toFixed(0)}<small style="font-size:13px">ms</small></div><div class="card-sub">LLM 响应</div></div>
      <div class="card"><div class="card-label">运行时长</div><div class="card-n">${uptimeStr}</div><div class="card-sub">会员率 ${memberRate}%</div></div>
    </div>`;
}

// 按需加载 Chart.js（CDN）
let _chartJsLoaded = null;
function ensureChartJs() {
  if (window.Chart) return Promise.resolve();
  if (_chartJsLoaded) return _chartJsLoaded;
  _chartJsLoaded = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js';
    s.onload = () => resolve();
    s.onerror = () => { _chartJsLoaded = null; reject(new Error('图表库加载失败')); };
    document.head.appendChild(s);
  });
  return _chartJsLoaded;
}

// ============================================================
// 老师管理（#16 完整增删改查 + 搜索分页 + 分类筛选 + 排序模式）
// ============================================================
let teacherPage = { cur: 1, size: 10, keyword: '', total: 0 };
let _teacherCache = [];
let _teacherQbCounts = {};   // #26 R3 每老师题目数速览
let _tStatus = '';
let _tCourse = '';           // 课程分类筛选
let _tCompany = '';          // 公司分类筛选
let _tSortMode = false;      // 排序模式开关
// 与后端 admin.py 枚举保持一致
const COURSE_CATEGORIES = ['言语理解', '判断推理', '数量关系', '资料分析', '常识判断', '申论', '面试', '综合'];
const COMPANIES = ['本机构', '华图', '粉笔', '中公', '其他'];
const _enumOptions = (arr, cur) => arr.map(x => `<option value="${esc(x)}" ${x === cur ? 'selected' : ''}>${esc(x)}</option>`).join('');

async function renderTeachers() {
  const c = $('#content');
  c.innerHTML = `
  <div class="page-head"><h2>老师管理</h2><p>老师注册表 · 增删改查 / 上下线 / 分类筛选 / 排序</p></div>
  <div class="panel">
    <div class="panel-head">
      <div class="toolbar" style="flex:1">
        <input type="search" id="t-kw" class="grow" placeholder="搜索名称 / 科目 / ID…" aria-label="搜索老师" value="${esc(teacherPage.keyword)}">
        <select id="t-status" aria-label="按状态筛选老师">
          <option value="">全部状态</option>
          <option value="1">在线</option>
          <option value="0">已下线</option>
        </select>
        <select id="t-course" aria-label="按课程分类筛选">
          <option value="">全部课程分类</option>${_enumOptions(COURSE_CATEGORIES, _tCourse)}
        </select>
        <select id="t-company" aria-label="按公司分类筛选">
          <option value="">全部公司</option>${_enumOptions(COMPANIES, _tCompany)}
        </select>
        <button class="btn ghost" onclick="searchTeachers()">搜索</button>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn ghost ${_tSortMode ? 'primary' : ''}" onclick="toggleTeacherSortMode()">↕️ 排序</button>
        <button class="btn primary" onclick="showTeacherForm()">＋ 新增老师</button>
      </div>
    </div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr><th>ID</th><th>名称</th><th>科目</th><th>课程分类</th><th>公司</th><th>模型</th><th>参数</th><th>题目数</th><th>状态</th><th>${_tSortMode ? '排序（↑↓ 调整）' : '操作'}</th></tr></thead>
        <tbody id="t-body"><tr><td colspan="10" class="loading">加载中…</td></tbody>
      </table>
    </div>
    <div class="panel-head" style="border-top:1px solid var(--line);${_tSortMode ? '' : 'display:none'}" id="t-sort-bar">
      <div class="toolbar" style="flex:1">
        <span class="hint">排序模式：↑↓ 调整顺序（保存后按 sort_order 升序展示）</span>
      </div>
      <button class="btn primary sm" onclick="saveTeacherSort()">💾 保存排序</button>
    </div>
    <div class="pager" id="t-pager"></div>
  </div>
  <div id="t-modal" class="modal-mask"></div>`;

  // 加载全量老师，前端本地分页+筛选（老师数一般 < 50，简单可靠）
  const r = await admin('/admin/teachers');
  _teacherCache = (r.teachers || []).filter(t => t.teacher_id !== '__sys__');
  try {
    const q = await admin('/admin/teachers/qb-stats');
    _teacherQbCounts = q.counts || {};
  } catch (e) { _teacherQbCounts = {}; }  // 题目数速览失败不阻断
  applyTeacherFilter();
}

function searchTeachers() {
  teacherPage.keyword = ($('#t-kw') && $('#t-kw').value || '').trim();
  _tStatus = ($('#t-status') && $('#t-status').value) || '';
  _tCourse = ($('#t-course') && $('#t-course').value) || '';
  _tCompany = ($('#t-company') && $('#t-company').value) || '';
  teacherPage.cur = 1;
  renderTeacherPage();
}

function applyTeacherFilter() {
  renderTeacherPage();
}

function filteredTeachers() {
  const kw = teacherPage.keyword.toLowerCase();
  let list = _teacherCache;
  if (kw) {
    list = list.filter(t =>
      (t.teacher_name || '').toLowerCase().includes(kw) ||
      (t.teacher_subject || '').toLowerCase().includes(kw) ||
      (t.teacher_id || '').toLowerCase().includes(kw));
  }
  if (_tStatus === '1') list = list.filter(t => t.enabled !== false);
  if (_tStatus === '0') list = list.filter(t => t.enabled === false);
  if (_tCourse) list = list.filter(t => (t.course_category || '') === _tCourse);
  if (_tCompany) list = list.filter(t => (t.company || '') === _tCompany);
  return list;
}

// 排序模式：交换相邻两项 sort_order 并本地重排（保存前不落库）
function toggleTeacherSortMode() {
  _tSortMode = !_tSortMode;
  renderTeachers();
}

function moveTeacher(tid, dir) {
  const list = filteredTeachers();
  const i = list.findIndex(t => t.teacher_id === tid);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= list.length) return;
  const a = list[i], b = list[j];
  const t = Number(a.sort_order) || 0;
  a.sort_order = Number(b.sort_order) || 0;
  b.sort_order = t;
  _teacherCache.sort((x, y) =>
    (Number(x.sort_order) || 0) - (Number(y.sort_order) || 0) ||
    (x.teacher_id || '').localeCompare(y.teacher_id || ''));
  renderTeacherPage();
}

async function saveTeacherSort() {
  const items = _teacherCache.map(t => ({ teacher_id: t.teacher_id, sort_order: Number(t.sort_order) || 0 }));
  try {
    const r = await admin('/admin/teachers/sort', { method: 'PUT', body: JSON.stringify({ items }) });
    toast(`排序已保存（${r.updated} 位老师）`, 'ok');
    _tSortMode = false;
    renderTeachers();
  } catch (e) {
    toast('保存排序失败：' + e.message, 'err');
  }
}

function renderTeacherPage() {
  const tbody = $('#t-body');
  if (!tbody) return;
  const list = filteredTeachers();
  teacherPage.total = list.length;
  const totalPages = Math.max(1, Math.ceil(list.length / teacherPage.size));
  if (teacherPage.cur > totalPages) teacherPage.cur = totalPages;
  const start = (teacherPage.cur - 1) * teacherPage.size;
  const page = list.slice(start, start + teacherPage.size);

  if (!page.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty">' + (teacherPage.keyword ? '无匹配结果' : '暂无老师，点击「新增老师」创建') + '</td></tr>';
  } else {
    tbody.innerHTML = page.map((t, pi) => `
      <tr>
        <td><code>${esc(t.teacher_id)}</code></td>
        <td><b>${esc(t.teacher_name)}</b></td>
        <td><span class="badge blue">${esc(t.teacher_subject)}</span></td>
        <td>${t.course_category ? `<span class="badge gold">${esc(t.course_category)}</span>` : '<span style="color:var(--ink-3)">—</span>'}</td>
        <td>${t.company ? `<span class="badge">${esc(t.company)}</span>` : '<span style="color:var(--ink-3)">—</span>'}</td>
        <td><code>${esc(t.llm_model || '—')}</code></td>
        <td><span class="mono" style="font-size:11.5px;color:var(--ink-2)">t=${Number(t.temperature).toFixed(1)} · top${t.top_n} · th${Number(t.threshold).toFixed(2)}</span></td>
        <td>${_teacherQbCounts[t.teacher_id] != null
          ? `<span class="mono" style="color:var(--ink-2)">${_teacherQbCounts[t.teacher_id] || 0} 题</span>`
          : '<span class="mono" style="color:var(--ink-3)">—</span>'}</td>
        <td>${t.enabled !== false ? '<span class="badge green">在线</span>' : '<span class="badge red">已下线</span>'}</td>
        ${_tSortMode
          ? `<td><div class="ops">
              <button class="btn sm ghost" ${pi === 0 ? 'disabled' : ''} onclick="moveTeacher('${esc(t.teacher_id)}', -1)">↑</button>
              <button class="btn sm ghost" ${pi === page.length - 1 ? 'disabled' : ''} onclick="moveTeacher('${esc(t.teacher_id)}', 1)">↓</button>
             </div></td>`
          : `<td><div class="ops">
          <button class="btn sm ghost" onclick='showTeacherDetail(${JSON.stringify(t).replace(/'/g, "&#39;")})'>查看</button>
          <button class="btn sm ghost" onclick='showTeacherForm(${JSON.stringify(t).replace(/'/g, "&#39;")})'>编辑</button>
          ${t.enabled !== false
            ? `<button class="btn sm warn" onclick="toggleTeacher('${esc(t.teacher_id)}', false)">下线</button>`
            : `<button class="btn sm ok" onclick="toggleTeacher('${esc(t.teacher_id)}', true)">上线</button>`}
        </div></td>`}
      </tr>`).join('');
  }
  renderPager($('#t-pager'), teacherPage.cur, totalPages, (p) => { teacherPage.cur = p; renderTeacherPage(); }, teacherPage.total);
}

function renderPager(el, cur, totalPages, onGo, total) {
  if (!el) return;
  if (totalPages <= 1) { el.innerHTML = `<span class="pg-info">共 ${total || 0} 条</span>`; return; }
  let btns = '';
  const r = (p) => `<button class="pg-btn ${p === cur ? 'active' : ''}" onclick="(${onGo.toString()})(${p})">${p}</button>`;
  if (cur > 1) btns += `<button class="pg-btn" onclick="(${onGo.toString()})(${cur - 1})">‹</button>`;
  if (totalPages <= 7) {
    for (let i = 1; i <= totalPages; i++) btns += r(i);
  } else {
    const win = [1, 2, 3, totalPages - 2, totalPages - 1, totalPages];
    let last = 0;
    for (let i = 1; i <= totalPages; i++) {
      if (win.includes(i) || Math.abs(i - cur) <= 1) {
        if (i - last > 1) btns += '<span class="pg-btn" style="border:none;background:none;cursor:default">…</span>';
        btns += r(i);
        last = i;
      }
    }
  }
  if (cur < totalPages) btns += `<button class="pg-btn" onclick="(${onGo.toString()})(${cur + 1})">›</button>`;
  el.innerHTML = `<span class="pg-info">共 ${total || 0} 条 · 第 ${cur}/${totalPages} 页</span>${btns}`;
}

// 老师详情（查看）
function showTeacherDetail(t) {
  openModal('t-modal', `
    <h3>老师详情 · ${esc(t.teacher_id)}</h3>
    <div class="detail-row"><span class="k">名称</span><span class="v">${esc(t.teacher_name)}</span></div>
    <div class="detail-row"><span class="k">科目</span><span class="v">${esc(t.teacher_subject)}</span></div>
    <div class="detail-row"><span class="k">课程分类</span><span class="v">${esc(t.course_category || '未分类')}</span></div>
    <div class="detail-row"><span class="k">公司</span><span class="v">${esc(t.company || '未分类')}</span></div>
    <div class="detail-row"><span class="k">排序</span><span class="v">#${t.sort_order ?? 0}</span></div>
    <div class="detail-row"><span class="k">模型</span><span class="v"><code>${esc(t.llm_model || '—')}</code></span></div>
    <div class="detail-row"><span class="k">温度</span><span class="v">${t.temperature}</span></div>
    <div class="detail-row"><span class="k">召回数</span><span class="v">${t.top_n}</span></div>
    <div class="detail-row"><span class="k">阈值</span><span class="v">${t.threshold}</span></div>
    <div class="detail-row"><span class="k">风格</span><span class="v">${esc(t.prompt_style || 'classroom')}</span></div>
    <div class="detail-row"><span class="k">状态</span><span class="v">${t.enabled !== false ? '<span class="badge green">在线</span>' : '<span class="badge red">已下线</span>'}</span></div>
    <div class="modal-actions"><button class="btn ghost sm" onclick="closeModal('t-modal')">关闭</button>
      <button class="btn primary sm" onclick='closeModal("t-modal");showTeacherForm(${JSON.stringify(t).replace(/'/g, "&#39;")})'>编辑</button></div>
  `);
}

// 新增 / 编辑表单
function showTeacherForm(t) {
  const isNew = !t;
  openModal('t-modal', `
    <h3>${isNew ? '新增老师' : '编辑老师 · ' + esc(t.teacher_id)}</h3>
    <div class="form-grid">
      <div class="field"><label for="tf-id">ID *</label><input id="tf-id" value="${esc(t ? t.teacher_id : '')}" ${isNew ? '' : 'readonly style="background:var(--paper-2)"'} placeholder="如 T004"></div>
      <div class="field"><label for="tf-name">名称 *</label><input id="tf-name" value="${esc(t ? t.teacher_name : '')}"></div>
      <div class="field"><label for="tf-subject">科目 *</label><input id="tf-subject" value="${esc(t ? t.teacher_subject : '')}"></div>
      <div class="field"><label for="tf-course">课程分类</label><select id="tf-course">${_enumOptions(COURSE_CATEGORIES, t ? (t.course_category || '') : '')}</select></div>
      <div class="field"><label for="tf-company">公司</label><select id="tf-company">${_enumOptions(COMPANIES, t ? (t.company || '') : '')}</select></div>
      <div class="field"><label for="tf-model">模型</label><input id="tf-model" value="${esc(t ? (t.llm_model || 'gpt-5.6-luna') : 'gpt-5.6-luna')}"></div>
      <div class="field"><label for="tf-temp">温度 (0~2)</label><input id="tf-temp" type="number" step="0.1" min="0" max="2" value="${t ? Number(t.temperature).toFixed(1) : 0.3}"></div>
      <div class="field"><label for="tf-topn">召回数 (≥1)</label><input id="tf-topn" type="number" min="1" value="${t ? t.top_n : 6}"></div>
      <div class="field"><label for="tf-thresh">阈值 (0~1)</label><input id="tf-thresh" type="number" step="0.05" min="0" max="1" value="${t ? Number(t.threshold).toFixed(2) : 0.5}"></div>
      <div class="field"><label><input id="tf-enabled" type="checkbox" ${t ? (t.enabled !== false ? 'checked' : '') : 'checked'}> 启用该老师</label></div>
    </div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('t-modal')">取消</button>
      <button class="btn primary" onclick="submitTeacherForm(${isNew})">保存</button>
    </div>
    <p class="hint" id="tf-hint"></p>
  `);
}

async function submitTeacherForm(isNew) {
  const payload = {
    teacher_id: $('#tf-id').value.trim(),
    teacher_name: $('#tf-name').value.trim(),
    teacher_subject: $('#tf-subject').value.trim(),
    course_category: $('#tf-course').value,
    company: $('#tf-company').value,
    llm_model: $('#tf-model').value.trim() || 'gpt-5.6-luna',
    temperature: parseFloat($('#tf-temp').value),
    top_n: parseInt($('#tf-topn').value),
    threshold: parseFloat($('#tf-thresh').value),
    enabled: $('#tf-enabled').checked,
  };
  const hint = $('#tf-hint');
  hint.textContent = '保存中…';
  try {
    if (isNew) {
      await admin('/admin/teachers', { method: 'POST', body: JSON.stringify(payload) });
      toast(`老师 ${payload.teacher_id} 已创建`, 'ok');
    } else {
      await admin(`/admin/teachers/${payload.teacher_id}`, { method: 'PUT', body: JSON.stringify(payload) });
      toast(`老师 ${payload.teacher_id} 已更新`, 'ok');
    }
    closeModal('t-modal');
    searchTeachers();
  } catch (e) {
    hint.textContent = '保存失败：' + e.message;
    hint.className = 'hint err';
  }
}

async function toggleTeacher(tid, enable) {
  const path = enable ? `/admin/teachers/${tid}/enable` : `/admin/teachers/${tid}`;
  const method = enable ? 'POST' : 'DELETE';
  try {
    await admin(path, { method });
    toast(`${tid} 已${enable ? '上线' : '下线'}`, 'ok');
    renderTeacherPage();
  } catch (e) {
    toast('操作失败：' + e.message, 'err');
  }
}

// ============================================================
// 模态框通用（修复 #16 BUG：用户模态框无法关闭）
// ============================================================
function openModal(id, html) {
  const m = $('#' + id);
  if (!m) return;
  m.innerHTML = `<div class="modal-box">${html}</div>`;
  m.classList.add('show');
  m.onclick = (e) => { if (e.target === m) closeModal(id); };
}
function closeModal(id) {
  const m = $('#' + id);
  if (m) m.classList.remove('show');
}

// ============================================================
// #25 问题2：AI 模型管理（注册表 + 默认模型 + 连通性测试）
// ============================================================
async function renderAiModels() {
  const c = $('#content');
  c.innerHTML = `
  <div class="page-head"><h2>AI 模型管理</h2><p>注册/启停模型 · 设置平台默认模型 · OpenAI 兼容协议 · 获取模型列表（失败自动兜底内置目录）</p></div>
  <div class="cards" id="aim-effective-cards" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:14px"></div>
  <div class="panel" style="margin-bottom:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap">
        <b>⚙️ 自动采集专用通道（多平台）</b>
        <span class="hint" style="margin-left:8px">仅作用于「题库管理 → 自动采集」的 LLM 提取，独立于平台默认模型；可配置多个平台，采集时按并发线程轮询并行提取</span>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn ghost sm" id="ac-llm-clear" onclick="acLlmClear()" style="display:none">🗑 清空全部</button>
        <button class="btn ghost sm" id="ac-llm-cancel" onclick="acLlmCancelEdit()" style="display:none">✕ 取消编辑</button>
        <button class="btn primary sm" id="ac-llm-save-btn" onclick="acLlmSave()">＋ 添加平台</button>
      </div>
    </div>
    <div style="padding:14px 14px 4px">
      <div id="ac-llm-list" style="margin-bottom:12px"></div>
      <div class="field">
        <label>选择已有平台（从模型注册表选取，自动填入 Base URL；或选"自定义"手填）</label>
        <select id="ac-llm-platform" style="max-width:520px" onchange="acLlmPlatformPick()"><option value="">— 加载平台列表… —</option></select>
      </div>
      <div style="display:flex;gap:14px;flex-wrap:wrap">
        <div class="field" style="flex:2;min-width:300px"><label>Base URL（OpenAI 兼容协议）</label><input id="ac-llm-base" placeholder="https://provider/v1"></div>
        <div class="field" style="flex:1;min-width:260px"><label>API Key（留空=全局密钥；编辑时留空=保持原值）</label><input id="ac-llm-key" type="password" placeholder="sk-…" autocomplete="off"></div>
      </div>
      <div class="field">
        <label>模型（可多选批量添加）</label>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <select id="ac-llm-model" style="flex:2;max-width:520px"><option value="">— 先选平台再「拉取模型列表」 —</option></select>
          <button class="btn ghost sm" onclick="acLlmFetch()">🔗 拉取模型列表</button>
          <button class="btn ghost sm" onclick="acLlmPickAll(true)" type="button">全选</button>
          <button class="btn ghost sm" onclick="acLlmPickAll(false)" type="button">清空</button>
        </div>
        <div id="ac-llm-pick" style="margin-top:6px;max-height:140px;overflow:auto;border:1px solid var(--line,#e5e7eb);border-radius:8px;padding:8px;display:none"></div>
        <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <input id="ac-llm-model-count" class="hint" value="" readonly style="border:none;background:transparent;width:auto;flex:1">
          <button class="btn ghost sm" onclick="acLlmBatchAdd()" type="button">＋ 一键多选(批量添加)</button>
          <button class="btn primary sm" onclick="acLlmSave()" type="button">💾 保存通道</button>
        </div>
        <div class="hint" id="ac-llm-state">加载中…</div>
      </div>
    </div>
  </div>
  <div class="panel">
    <div class="panel-head">
      <div class="toolbar" style="flex:1"><b>模型注册表</b></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn ghost sm" onclick="aimFetchModal()">📥 获取模型</button>
        <button class="btn ghost sm" onclick="aimTestAll()" id="aim-testall-btn">⚡ 一键测速</button>
        <button class="btn primary sm" onclick="aimCreateModal()">➕ 新增模型</button>
      </div>
    </div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr>
          <th>名称</th><th>模型标识</th><th>提供方</th><th>Base URL</th><th>API Key</th>
          <th>温度</th><th>输出上限</th><th>状态</th><th>默认</th><th>备注</th><th style="width:190px">操作</th>
        </tr></thead>
        <tbody id="aim-body"><tr><td colspan="11" class="loading">加载中…</td></tr></tbody>
      </table>
    </div>
  </div>
  <div class="panel" style="margin-top:16px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1"><b>🪄 内置模型目录</b><span style="font-size:11.5px;color:var(--ink-3)">OpenAI 兼容协议 · 一键登记进注册表，或点击「获取模型」从远端拉取</span></div>
      <button class="btn ghost sm" onclick="aimLoadBuiltin()">⟳ 刷新内置</button>
    </div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr><th>模型标识</th><th>名称</th><th>提供方</th><th>Base URL</th><th>操作</th></tr></thead>
        <tbody id="aim-builtin-body"><tr><td colspan="5" class="loading">加载中…</td></tr></tbody>
      </table>
    </div>
  </div>
  <div id="aim-modal" class="modal-mask"></div>`;
  await aimLoad();
  acLlmLoad();
}

let _aimCache = [];
let _aimBuiltin = [];

async function aimLoad() {
  const [mr, er] = await Promise.all([
    admin('/admin/ai-models'),
    admin('/admin/ai-models/effective'),
  ]);
  _aimCache = mr.models || [];
  const eff = er.effective || {};
  const activeName = (() => { const m = _aimCache.find(x => x.is_default); return m ? m.name : (eff.model_id || '—'); })();
  const baseUrlHint = eff.base_url || '—';
  $('#aim-effective-cards').innerHTML = `
    <div class="card"><div class="card-label">平台级默认模型</div><div class="card-n">${esc(activeName)}</div><div class="card-sub">题库采集/知识建树等 AI 使用</div></div>
    <div class="card"><div class="card-label">模型标识</div><div class="card-n" style="font-size:13px">${esc(eff.model_id || '—')}</div><div class="card-sub">有效回退 .env</div></div>
    <div class="card"><div class="card-label">Base URL</div><div class="card-n" style="font-size:12px;overflow:hidden;text-overflow:ellipsis">${esc(baseUrlHint)}</div><div class="card-sub">当前通道</div></div>
    <div class="card"><div class="card-label">温度</div><div class="card-n">${eff.temperature ?? '—'}</div><div class="card-sub">sampling</div></div>
    <div class="card"><div class="card-label">输出上限</div><div class="card-n">${eff.max_tokens ?? '—'}</div><div class="card-sub">tokens</div></div>
    <div class="card"><div class="card-label">已注册</div><div class="card-n">${_aimCache.length}</div><div class="card-sub">个候选模型</div></div>`;
  renderAiRows(_aimCache);
  aimLoadBuiltin();
}

// 加载内置模型目录并渲染（含一键登记）
async function aimLoadBuiltin() {
  const tbody = $('#aim-builtin-body');
  if (!tbody) return;
  try {
    const r = await admin('/admin/ai-models/builtin');
    _aimBuiltin = (r.models || []);
    tbody.innerHTML = _aimBuiltin.map(m => `
      <tr>
        <td><code>${esc(m.model_id)}</code></td>
        <td><b>${esc(m.name)}</b></td>
        <td>${esc(m.provider)}</td>
        <td style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(m.base_url)}">${esc(m.base_url)}</td>
        <td>${m.registered
          ? '<span class="badge green">已登记</span>'
          : `<button class="btn ghost sm" onclick="aimRegisterBuiltin('${esc(m.model_id)}')">一键登记</button>`}</td>
      </tr>`).join('');
  } catch (e) { tbody.innerHTML = `<tr><td colspan="5" class="err-tip">加载失败：${esc(e.message)}</td></tr>`; }
}

// 一键登记内置模型
async function aimRegisterBuiltin(modelId) {
  const m = _aimBuiltin.find(x => x.model_id === modelId);
  if (!m) return;
  try {
    await admin('/admin/ai-models', { method: 'POST', body: JSON.stringify({
      name: m.name, model_id: m.model_id, provider: m.provider, base_url: m.base_url,
      api_key: '', temperature: 0.3, max_tokens: 2000, remark: '内置目录一键登记',
    })});
    toast(`已登记 ${modelId}`, 'ok'); aimLoad();
  } catch (e) { toast('登记失败：' + e.message, 'err'); }
}

// 获取模型：弹窗 → 填 Base URL → 调远端拉取，失败自动兜底内置目录
function aimFetchModal() {
  const effBase = (_aimCache.find(x => x.is_default) || {}).base_url || '';
  openModal('aim-modal', `
    <h3>📥 获取模型</h3>
    <p style="font-size:12.5px;color:var(--ink-3);margin-bottom:12px">按 <b>OpenAI 兼容协议</b> 请求 <code>{Base URL}/models</code> 拉取可用模型；<br>跨域/网络/鉴权失败时自动<b>降级展示内置目录</b>并给出原因。<br>⚠️ 填写 API Key 后，<b>一键登记的模型将保存该密钥</b>（留空 = 平台全局密钥）；不同密钥可见/可用的模型不同。</p>
    <div class="field"><label>Base URL</label><input id="aimf-base" value="${esc(effBase || 'https://ai.anyyds.cn/v1')}" placeholder="https://provider/v1"></div>
    <div class="field"><label>提供方（可选）</label><input id="aimf-provider" placeholder="anyyds / openai / deepseek …"></div>
    <div class="field"><label>API Key（可选，读取平台 .env 缺省）</label><input id="aimf-key" type="password" placeholder="sk-…"></div>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px">
      <button class="btn primary" onclick="aimFetchList()">拉取模型列表</button>
      <span class="hint" id="aimf-hint"></span>
    </div>
    <div id="aimf-result">
      <p style="font-size:12.5px;color:var(--ink-3)">点击「拉取模型列表」后，这里会展示可用模型并可一键登记。</p>
    </div>
    <div class="modal-actions"><button class="btn ghost" onclick="closeModal('aim-modal')">关闭</button></div>
  `);
}

async function aimFetchList() {
  const base = ($('#aimf-base').value || '').trim();
  const provider = ($('#aimf-provider').value || '').trim();
  const key = ($('#aimf-key').value || '').trim();
  const hint = $('#aimf-hint');
  const box = $('#aimf-result');
  hint.textContent = '获取中…'; hint.className = 'hint';
  box.innerHTML = '<div class="loading">请求远端模型列表…</div>';
  try {
    const r = await admin('/admin/ai-models/list-remote', {
      method: 'POST', body: JSON.stringify({ base_url: base, provider, api_key: key }),
    });
    const isFallback = r.used_fallback === true;
    // 记录 base_url / 密钥用于一键登记（r.base_url 是 API 根地址，不含 /models）
    window._aimLast = { base: r.base_url || base, provider, key };
    box.innerHTML = (isFallback
      ? `<div class="err-tip" style="margin-bottom:10px">⚠️ 远端获取失败，已降级展示内置目录：${esc(r.error || '')}</div>`
      : `<p class="hint ok" style="margin-bottom:10px">✅ 获取到 ${r.models.length} 个模型（${esc(r.base_url || base)}）</p>`)
      + `<table class="tbl"><thead><tr><th>模型标识</th><th>操作</th></tr></thead><tbody>`
      + r.models.map(id => {
          const already = _aimCache.some(x => x.model_id === id);
          return `<tr><td><code>${esc(id)}</code></td><td>${already
            ? '<span class="badge green">已登记</span>'
            : `<button class="btn ghost sm" onclick="aimRegisterRemote('${esc(id)}', '${esc(window._aimLast.provider)}')">一键登记</button>`}</td></tr>`;
        }).join('') + `</tbody></table>`;
    hint.textContent = isFallback ? `${r.models.length} 个（内置兜底）` : `${r.models.length} 个（远端）`;
    hint.className = isFallback ? 'hint err' : 'hint ok';
  } catch (e) {
    box.innerHTML = `<div class="err-tip">拉取失败：${esc(e.message)}</div>`;
    hint.textContent = ''; hint.className = 'hint err';
  }
}

async function aimRegisterRemote(modelId, provider) {
  const last = window._aimLast || { base: '', provider: 'anyyds', key: '' };
  try {
    await admin('/admin/ai-models', { method: 'POST', body: JSON.stringify({
      name: modelId, model_id: modelId, provider: provider || last.provider || 'anyyds',
      base_url: last.base || '', api_key: last.key || '', temperature: 0.3, max_tokens: 2000, remark: '远端获取',
    })});
    toast(`已登记 ${modelId}`, 'ok');
    aimLoad();
    aimFetchList(); // 刷新弹窗内已登记状态
  } catch (e) { toast('登记失败：' + e.message, 'err'); }
}

function renderAiRows(list) {
  const tbody = $('#aim-body');
  if (!tbody) return;
  if (!list.length) { tbody.innerHTML = '<tr><td colspan="11" class="empty">暂无模型，点击「新增模型」注册第一个候选模型</td></tr>'; return; }
  tbody.innerHTML = list.map(m => {
    const hasKey = m.api_key && m.api_key.length > 0;
    return `<tr style="${m.enabled ? '' : 'opacity:.55'}">
      <td><b>${esc(m.name)}</b></td>
      <td><code style="font-size:11px">${esc(m.model_id)}</code></td>
      <td>${esc(m.provider || 'anyyds')}</td>
      <td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(m.base_url)}">${esc(m.base_url || '—')}</td>
      <td><span class="badge ${hasKey ? 'blue' : 'gray'}">${hasKey ? '独立Key' : '全局'}</span></td>
      <td>${m.temperature ?? 0.3}</td>
      <td>${m.max_tokens ?? 2000}</td>
      <td><span class="badge ${m.enabled ? 'green' : 'gray'}">${m.enabled ? '启用' : '停用'}</span></td>
      <td>${m.is_default ? '<span class="badge blue">默认</span>' : `<button class="btn ghost sm" onclick="aimSetDefault('${esc(m.model_id)}')">设为默认</button>`}</td>
      <td title="${esc(m.remark || '')}" style="max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(m.remark || '—')}</td>
      <td>
        <button class="btn ghost sm" onclick="aimTest('${esc(m.model_id)}')" id="aim-test-${esc(m.model_id)}">⚡测速</button>
        <button class="btn ghost sm" onclick="aimEditModal('${esc(m.model_id)}')">✏️</button>
        <button class="btn danger sm" onclick="aimDelete('${esc(m.model_id)}', '${esc(m.name)}')">🗑</button>
      </td>
    </tr>`;
  }).join('');
}

function aimCreateModal() {
  openModal('aim-modal', `
    <div class="modal"><h3>➕ 新增模型</h3>${aimFormHtml('', {}, 'aimCreateSubmit()', '创建')}
    <p class="hint" id="aim-f-hint"></p></div>`);
}

async function aimEditModal(model_id) {
  // 从详情端获取完整 api_key（masked 不回显到编辑框）
  let m = _aimCache.find(x => x.model_id === model_id);
  if (!m) return;
  try {
    const r = await admin('/admin/ai-models/' + encodeURIComponent(model_id));
    const detail = r.model || {};
    openModal('aim-modal', `
      <div class="modal"><h3>✏️ 编辑模型 ${esc(m.model_id)}</h3>${aimFormHtml(m.model_id, detail, 'aimEditSubmit()', '保存')}
      <p class="hint" id="aim-f-hint"></p></div>`);
  } catch (e) {
    // 降级：用缓存数据
    openModal('aim-modal', `
      <div class="modal"><h3>✏️ 编辑模型 ${esc(m.model_id)}</h3>${aimFormHtml(m.model_id, m, 'aimEditSubmit()', '保存')}
      <p class="hint" id="aim-f-hint"></p></div>`);
  }
}

function aimFormHtml(model_id, m, onSubmit, btn) {
  const hasKey = !!(m.api_key && m.api_key.trim());
  return `
    <div class="field"><label>名称 *</label><input id="aim-name" value="${esc(m.name || '')}" placeholder="如：Luna 主模型"></div>
    <div class="field"><label>模型标识 *</label><input id="aim-modelid" value="${esc(m.model_id || '')}" ${model_id ? 'disabled' : ''} placeholder="如：gpt-5.6-luna"></div>
    <div class="field"><label>提供方</label><input id="aim-provider" value="${esc(m.provider || 'anyyds')}" placeholder="anyyds / openai / 自定义"></div>
    <div class="field"><label>Base URL</label><input id="aim-baseurl" value="${esc(m.base_url || '')}" placeholder="https://api.example.com/v1"></div>
    <div class="field"><label>API Key ${hasKey ? '<span class="hint" style="font-weight:400">(已配置，留空不修改)</span>' : '<span class="hint" style="font-weight:400">(留空 = 使用全局 .env 密钥)</span>'}</label>
      <input id="aim-apikey" type="password" placeholder="${hasKey ? '已配置密钥，留空=不修改' : 'sk-…'}" autocomplete="off"></div>
    <div class="row">
      <div class="field"><label>温度</label><input id="aim-temp" type="number" step="0.1" value="${m.temperature ?? 0.3}"></div>
      <div class="field"><label>输出上限</label><input id="aim-maxtok" type="number" value="${m.max_tokens ?? 2000}"></div>
    </div>
    <div class="field"><label>备注</label><input id="aim-remark" value="${esc(m.remark || '')}" placeholder="用途/联系人等"></div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('aim-modal')">取消</button>
      <button class="btn ghost sm" onclick="aimTestForm()" style="margin-right:auto">🔌 测试连接</button>
      <button class="btn primary" onclick="${onSubmit}">${btn}</button>
    </div>`;
}

async function aimCreateSubmit() {
  const name = ($('#aim-name').value || '').trim();
  const model_id = ($('#aim-modelid').value || '').trim();
  if (!name || !model_id) { toast('名称与模型标识必填', 'err'); return; }
  try {
    await admin('/admin/ai-models', { method: 'POST', body: JSON.stringify({
      name, model_id, provider: $('#aim-provider').value, base_url: $('#aim-baseurl').value,
      api_key: $('#aim-apikey').value,
      temperature: parseFloat($('#aim-temp').value) || 0.3,
      max_tokens: parseInt($('#aim-maxtok').value) || 2000,
      remark: $('#aim-remark').value,
    })});
    closeModal('aim-modal'); toast('模型已新增', 'ok'); aimLoad();
  } catch (e) { $('#aim-f-hint').textContent = '创建失败：' + e.message; }
}

async function aimEditSubmit() {
  const model_id = $('#aim-modelid').value;
  const apiKey = ($('#aim-apikey').value || '').trim();
  // 只传 api_key 当用户输入了新内容（留空 = 不修改）
  const body = {
    name: $('#aim-name').value, provider: $('#aim-provider').value, base_url: $('#aim-baseurl').value,
    temperature: parseFloat($('#aim-temp').value) || 0.3,
    max_tokens: parseInt($('#aim-maxtok').value) || 2000, remark: $('#aim-remark').value,
  };
  if (apiKey) body.api_key = apiKey;
  try {
    await admin('/admin/ai-models/' + encodeURIComponent(model_id), { method: 'PATCH', body: JSON.stringify(body) });
    closeModal('aim-modal'); toast('模型已保存', 'ok'); aimLoad();
  } catch (e) { $('#aim-f-hint').textContent = '保存失败：' + e.message; }
}

// 在新增/编辑弹窗中测试当前填写的连接
async function aimTestForm() {
  const model_id = ($('#aim-modelid').value || '').trim();
  const base_url = ($('#aim-baseurl').value || '').trim();
  const api_key = ($('#aim-apikey').value || '').trim();
  if (!model_id) { toast('请先填写模型标识', 'err'); return; }
  if (!base_url) { toast('请先填写 Base URL', 'err'); return; }
  const hint = $('#aim-f-hint');
  hint.textContent = '⏳ 测试中…'; hint.className = 'hint';
  try {
    const r = await admin('/admin/ai-models/test', { method: 'POST', body: JSON.stringify({ model_id, base_url, api_key }) });
    if (r.ok) hint.innerHTML = `✅ 连通正常（${r.latency}s）：${esc(r.reply || '')}`;
    else hint.innerHTML = `❌ 测试失败（${r.latency}s）：${esc(r.error || '')}`;
    hint.className = r.ok ? 'hint ok' : 'hint err';
  } catch (e) { hint.textContent = '接口异常：' + e.message; hint.className = 'hint err'; }
}

async function aimSetDefault(model_id) {
  try {
    await admin('/admin/ai-models/' + encodeURIComponent(model_id) + '/default', { method: 'POST' });
    toast('已设为平台默认模型', 'ok'); aimLoad();
  } catch (e) { toast('设置失败：' + e.message, 'err'); }
}

async function aimDelete(model_id, name) {
  if (!confirm(`确认删除模型「${name}」？若为默认模型将同时清除默认设置。`)) return;
  try { await admin('/admin/ai-models/' + encodeURIComponent(model_id), { method: 'DELETE' }); toast('已删除', 'ok'); aimLoad(); }
  catch (e) { toast('删除失败：' + e.message, 'err'); }
}

async function aimTest(model_id) {
  const btn = document.getElementById('aim-test-' + model_id);
  if (btn) { btn.textContent = '⏳…'; btn.disabled = true; }
  try {
    const r = await admin('/admin/ai-models/test', { method: 'POST', body: JSON.stringify({ model_id }) });
    if (r.ok) toast(`✅ ${model_id} 连通正常，延迟 ${r.latency}s，回复：${r.reply || ''}`, 'ok');
    else toast(`❌ ${model_id} 测试失败（${r.latency}s）：${r.error}`, 'err');
  } catch (e) { toast('测试接口异常：' + e.message, 'err'); }
  if (btn) { btn.textContent = '⚡测速'; btn.disabled = false; }
  aimLoad(); // 刷新状态
}

async function aimTestAll() {
  const btn = $('#aim-testall-btn');
  const orig = btn.innerHTML;
  btn.innerHTML = '⏳ 测速中…'; btn.disabled = true;
  try {
    const r = await admin('/admin/ai-models/test-all', { method: 'POST' });
    const { results, ok_count, total } = r;
    toast(`✅ 批量测速完成：${ok_count}/${total} 个模型连通正常`, ok_count === total ? 'ok' : 'err');
  } catch (e) { toast('批量测速失败：' + e.message, 'err'); }
  btn.innerHTML = orig; btn.disabled = false;
  aimLoad(); // 刷新表格
}

// ============================================================
// 用户管理（#16 修复 BUG + 新增/编辑/删除/查看）
// ============================================================
let userPage = { cur: 1, size: 10, keyword: '', total: 0 };
let _userCache = [];

async function renderUsers() {
  const c = $('#content');
  c.innerHTML = `
  <div class="page-head"><h2>用户管理</h2><p>用户账号 · 角色 / 会员 / 状态 / 统计 / 密码管理</p></div>
  <div class="cards" id="u-stats-cards" style="grid-template-columns:repeat(auto-fit,minmax(140px,1fr))"></div>
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap">
        <input type="search" id="u-kw" class="grow" placeholder="搜索用户名或 ID…" aria-label="搜索用户" value="${esc(userPage.keyword)}">
        <select id="u-role" aria-label="按角色筛选用户">
          <option value="">全部角色</option>
          <option value="admin">管理员</option>
          <option value="member">会员</option>
          <option value="free">免费</option>
        </select>
        <select id="u-status" aria-label="按状态筛选用户">
          <option value="">全部状态</option>
          <option value="active">正常</option>
          <option value="disabled">已禁用</option>
        </select>
        <button class="btn ghost" onclick="searchUsers()">搜索</button>
      </div>
      <button class="btn primary" onclick="showUserForm()">＋ 新增用户</button>
    </div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr><th>用户</th><th>角色</th><th>状态</th><th>今日已用</th><th>会员到期</th><th>创建时间</th><th>操作</th></tr></thead>
        <tbody id="u-body"><tr><td colspan="7" class="loading">加载中…</td></tr></tbody>
      </table>
    </div>
    <div class="pager" id="u-pager"></div>
  </div>
  <div id="u-modal" class="modal-mask"></div>`;

  const r = await admin('/admin/users?limit=500');
  _userCache = r.users || [];
  try {
    const s = await admin('/admin/users/stats');
    const roleMap = { admin: '管理员', member: '会员', free: '免费' };
    const byRole = (s.by_role || []).map(x => `${roleMap[x.role] || x.role} ${x.n}`).join(' · ');
    $('#u-stats-cards').innerHTML = `
      <div class="card"><div class="card-label">用户总数</div><div class="card-n">${s.total || 0}</div><div class="card-sub">${byRole || '—'}</div></div>
      <div class="card"><div class="card-label">当前会员</div><div class="card-n gold">${s.member_now || 0}</div><div class="card-sub">有效期内的会员</div></div>
      <div class="card"><div class="card-label">今日活跃</div><div class="card-n green">${s.active_today || 0}</div><div class="card-sub">今天有消费记录</div></div>
      <div class="card"><div class="card-label">已禁用</div><div class="card-n red">${(s.by_status || []).filter(x => x.status === 'disabled').reduce((a, x) => a + x.n, 0)}</div><div class="card-sub">无法登录的账号</div></div>`;
  } catch (e) {}
  applyUserFilter();
}

let _uRole = '';
let _uStatus = '';
function searchUsers() {
  userPage.keyword = ($('#u-kw') && $('#u-kw').value || '').trim();
  _uRole = ($('#u-role') && $('#u-role').value) || '';
  _uStatus = ($('#u-status') && $('#u-status').value) || '';
  userPage.cur = 1;
  applyUserFilter();
}
function applyUserFilter() {
  const kw = userPage.keyword.toLowerCase();
  let list = _userCache;
  if (kw) list = list.filter(u => (u.username || '').toLowerCase().includes(kw) || (u.user_id || '').toLowerCase().includes(kw));
  if (_uRole) list = list.filter(u => u.role === _uRole);
  if (_uStatus) list = list.filter(u => (u.status || 'active') === _uStatus);
  userPage.total = list.length;
  const totalPages = Math.max(1, Math.ceil(list.length / userPage.size));
  if (userPage.cur > totalPages) userPage.cur = totalPages;
  const start = (userPage.cur - 1) * userPage.size;
  const page = list.slice(start, start + userPage.size);
  renderUserRows(page);
  renderPager($('#u-pager'), userPage.cur, totalPages, (p) => { userPage.cur = p; applyUserFilter(); }, userPage.total);
}

function renderUserRows(users) {
  const tbody = $('#u-body');
  if (!tbody) return;
  if (!users.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">' + (userPage.keyword ? '无匹配用户' : '暂无用户') + '</td></tr>';
    return;
  }
  tbody.innerHTML = users.map(u => {
    const isAdmin = u.role === 'admin';
    const isMember = u.role === 'member';
    const status = (u.status || 'active') === 'active' ? 'active' : 'disabled';
    const roleBadge = isAdmin ? '<span class="badge purple">管理员</span>'
      : isMember ? '<span class="badge green">会员</span>'
      : '<span class="badge gray">免费</span>';
    const statusBadge = status === 'active'
      ? '<span class="badge green">正常</span>'
      : '<span class="badge red">已禁用</span>';
    const expireStr = u.member_expire_at
      ? (u.member_expire_at.slice(0, 10) + (u.days_remaining > 0 ? ` <span class="badge green">剩${u.days_remaining}天</span>` : ' <span class="badge red">已到期</span>'))
      : '—';
    return `<tr style="${status === 'disabled' ? 'opacity:.55' : ''}">
      <td><b>${esc(u.username)}</b><br><small style="color:var(--ink-3)">${esc(u.user_id)}</small></td>
      <td>${roleBadge}</td>
      <td>${statusBadge}
        ${isAdmin ? '' : `<button class="btn sm ghost" onclick="toggleUserStatus('${esc(u.user_id)}','${status === 'active' ? 'disabled' : 'active'}','${esc(u.username)}')" title="${status === 'active' ? '禁用该账号，禁用后无法登录' : '恢复登录'}">${status === 'active' ? '禁用' : '启用'}</button>`}
      </td>
      <td><span class="mono">${u.today_count ?? 0}</span></td>
      <td>${expireStr}</td>
      <td>${fmtDate(u.created_at)}</td>
      <td><div class="ops">
        <button class="btn sm ghost" onclick="showUserDetail('${esc(u.user_id)}')">查看</button>
        ${isAdmin ? '' : `<button class="btn sm ghost" onclick="showUserRoleModal('${esc(u.user_id)}')">角色</button>`}
        ${isAdmin ? '' : `<button class="btn sm warn" onclick="resetUserPassword('${esc(u.user_id)}')">重置密码</button>`}
        ${isAdmin ? '' : `<button class="btn sm danger" onclick="deleteUser('${esc(u.user_id)}', '${esc(u.username)}')">删除</button>`}
      </div></td>
    </tr>`;
  }).join('');
}

async function toggleUserStatus(userId, target, username) {
  if (target === 'disabled' && !confirm(`确认禁用用户「${esc(username)}」？禁用后该账号将无法登录。`)) return;
  try {
    await admin(`/admin/users/${encodeURIComponent(userId)}/status`, { method: 'POST', body: JSON.stringify(target) });
    toast(target === 'disabled' ? '已禁用该账号' : '已启用该账号', 'ok');
    renderUsers();
  } catch (e) { toast('操作失败：' + e.message, 'err'); }
}

// 新增用户
function showUserForm() {
  openModal('u-modal', `
    <h3>新增用户</h3>
    <div class="field"><label for="uf-username">用户名 *</label><input id="uf-username" placeholder="登录用户名"></div>
    <div class="field"><label for="uf-password">密码 *（至少 6 位）</label><input id="uf-password" type="password" placeholder="初始密码"></div>
    <div class="field"><label for="uf-role">角色</label><select id="uf-role">
      <option value="free">免费用户</option>
      <option value="member">会员（默认 30 天）</option>
      <option value="admin">管理员</option>
    </select></div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('u-modal')">取消</button>
      <button class="btn primary" onclick="submitUserForm()">创建</button>
    </div>
    <p class="hint" id="uf-hint"></p>
  `);
}
async function submitUserForm() {
  const username = $('#uf-username').value.trim();
  const password = $('#uf-password').value;
  const role = $('#uf-role').value;
  const hint = $('#uf-hint');
  if (!username || !password) { hint.textContent = '用户名和密码必填'; hint.className = 'hint err'; return; }
  if (password.length < 6) { hint.textContent = '密码至少 6 位'; hint.className = 'hint err'; return; }
  hint.textContent = '创建中…';
  try {
    await admin('/admin/users', { method: 'POST', body: JSON.stringify({ username, password, role }) });
    toast(`用户 ${username} 已创建`, 'ok');
    closeModal('u-modal');
    renderUsers();
  } catch (e) {
    hint.textContent = '创建失败：' + e.message;
    hint.className = 'hint err';
  }
}

// 查看详情
async function showUserDetail(userId) {
  openModal('u-modal', `<h3>用户详情</h3><div class="loading">加载中…</div>`);
  try {
    const u = await admin(`/admin/users/${userId}`);
    const box = $('#u-modal .modal-box');
    box.innerHTML = `
      <h3>用户详情 · ${esc(u.username)}</h3>
      <div class="detail-row"><span class="k">用户 ID</span><span class="v"><code>${esc(u.user_id)}</code></span></div>
      <div class="detail-row"><span class="k">用户名</span><span class="v">${esc(u.username)}</span></div>
      <div class="detail-row"><span class="k">角色</span><span class="v">${u.role === 'admin' ? '<span class="badge purple">管理员</span>' : u.role === 'member' ? '<span class="badge green">会员</span>' : '<span class="badge gray">免费</span>'}</span></div>
      <div class="detail-row"><span class="k">今日已用</span><span class="v">${u.today_count ?? 0} 次</span></div>
      <div class="detail-row"><span class="k">会员到期</span><span class="v">${u.member_expire_at ? fmtTime(u.member_expire_at) : '—'}</span></div>
      <div class="detail-row"><span class="k">剩余天数</span><span class="v">${u.days_remaining ?? 0} 天</span></div>
      <div class="detail-row"><span class="k">创建时间</span><span class="v">${fmtTime(u.created_at)}</span></div>
      <div class="modal-actions"><button class="btn ghost sm" onclick="closeModal('u-modal')">关闭</button></div>
    `;
  } catch (e) {
    $('#u-modal .modal-box').innerHTML = `<h3>加载失败</h3><p class="err-tip">${esc(e.message)}</p>
      <div class="modal-actions"><button class="btn ghost sm" onclick="closeModal('u-modal')">关闭</button></div>`;
  }
}

// 角色设置（原 showUserActions 修复版）
function showUserRoleModal(userId) {
  openModal('u-modal', `
    <h3>设置用户角色 · ${esc(userId)}</h3>
    <div class="field"><label for="ua-role">角色</label><select id="ua-role">
      <option value="free">免费</option>
      <option value="member">会员</option>
    </select></div>
    <div class="field"><label for="ua-days">会员天数（仅会员生效）</label><input id="ua-days" type="number" value="30" min="1" max="3650"></div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('u-modal')">取消</button>
      <button class="btn primary" onclick="submitUserRole('${esc(userId)}')">确认</button>
    </div>
    <p class="hint" id="ua-hint"></p>
  `);
}
async function submitUserRole(userId) {
  const role = $('#ua-role').value;
  const days = parseInt($('#ua-days').value) || 30;
  const hint = $('#ua-hint');
  hint.textContent = '保存中…';
  try {
    await admin(`/admin/users/${userId}/set_role`, { method: 'POST', body: JSON.stringify({ role, days }) });
    toast('角色已更新', 'ok');
    closeModal('u-modal');
    renderUsers();
  } catch (e) {
    hint.textContent = '操作失败：' + e.message;
    hint.className = 'hint err';
  }
}

// 重置密码
function resetUserPassword(userId) {
  openModal('u-modal', `
    <h3>重置密码 · ${esc(userId)}</h3>
    <div class="field"><label for="rp-pass">新密码（至少 6 位）</label><input id="rp-pass" type="password" placeholder="新密码"></div>
    <div class="field"><label for="rp-pass2">再次输入</label><input id="rp-pass2" type="password" placeholder="确认密码"></div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('u-modal')">取消</button>
      <button class="btn primary" onclick="submitResetPassword('${esc(userId)}')">重置</button>
    </div>
    <p class="hint" id="rp-hint"></p>
  `);
}
async function submitResetPassword(userId) {
  const p1 = $('#rp-pass').value;
  const p2 = $('#rp-pass2').value;
  const hint = $('#rp-hint');
  if (p1.length < 6) { hint.textContent = '密码至少 6 位'; hint.className = 'hint err'; return; }
  if (p1 !== p2) { hint.textContent = '两次输入不一致'; hint.className = 'hint err'; return; }
  hint.textContent = '重置中…';
  try {
    await admin(`/admin/users/${userId}/reset-password`, { method: 'POST', body: JSON.stringify(p1) });
    toast('密码已重置', 'ok');
    closeModal('u-modal');
  } catch (e) {
    hint.textContent = '重置失败：' + e.message;
    hint.className = 'hint err';
  }
}

// 删除用户
function deleteUser(userId, username) {
  openModal('u-modal', `
    <h3>删除用户</h3>
    <div class="err-tip" style="margin-bottom:12px">⚠️ 确认删除用户「${esc(username)}」（${esc(userId)}）？<br>该操作会同时删除其 OAuth 绑定，<b>不可恢复</b>。</div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('u-modal')">取消</button>
      <button class="btn danger" onclick="submitDeleteUser('${esc(userId)}')">确认删除</button>
    </div>
    <p class="hint" id="ud-hint"></p>
  `);
}
async function submitDeleteUser(userId) {
  const hint = $('#ud-hint');
  hint.textContent = '删除中…';
  try {
    await admin(`/admin/users/${userId}`, { method: 'DELETE' });
    toast('用户已删除', 'ok');
    closeModal('u-modal');
    renderUsers();
  } catch (e) {
    hint.textContent = '删除失败：' + e.message;
    hint.className = 'hint err';
  }
}

// ============================================================
// 文档管理（#16 上传/预览/搜索/删除）
// ============================================================
let docPage = { keyword: '', category: '' };
let _docTeacher = '';
let _docMeta = {};  // #24 R5: {doc_name: {category, tags}} 文档元数据映射（避免属性内联转义）

async function renderDocuments() {
  const c = $('#content');
  c.innerHTML = `
  <div class="page-head"><h2>文档管理</h2><p>知识库课件 · 上传入库 / 分类浏览 / 预览 / 内容检索 / 上下架 / 权限控制</p></div>
  <div class="cards" id="doc-stats-cards" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr))"></div>
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap">
        <label for="doc-teacher">老师：</label>
        <select id="doc-teacher" onchange="docSwitch()" aria-label="选择老师"></select>
        <select id="doc-cat" onchange="loadDocuments()" aria-label="按分类筛选"><option value="">全部分类</option></select>
        <select id="doc-state" onchange="loadDocuments()" aria-label="按上下架状态筛选">
          <option value="">全部状态</option><option value="enabled">已上架</option><option value="disabled">已下架</option>
        </select>
        <input type="search" id="doc-kw" class="grow" placeholder="按文档名/标签筛选…" aria-label="按文档名/标签筛选" value="${esc(docPage.keyword)}" onkeydown="if(event.key==='Enter')loadDocuments()">
        <button class="btn ghost" onclick="loadDocuments()">⟳ 刷新</button>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn ghost" onclick="docSearchModal()">🔍 内容检索</button>
        <button class="btn primary" onclick="openUploadDoc()">📤 上传文档</button>
      </div>
    </div>
    <div class="panel-head" style="border-top:1px solid var(--line)">
      <div class="toolbar" style="flex:1;flex-wrap:wrap" id="doc-batch-bar" style="min-height:30px">
        <span class="hint" id="doc-sel-hint"></span>
        <button class="btn ghost sm" onclick="docBatchSet(true)">⬆ 批量上架</button>
        <button class="btn ghost sm warn" onclick="docBatchSet(false)">⬇ 批量下架</button>
      </div>
    </div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr><th style="width:34px"><input type="checkbox" onchange="docCheckAll(this.checked)" aria-label="全选本页"></th><th>文档名</th><th>扩展</th><th>分类</th><th>块数</th><th>字数</th><th>状态</th><th>操作</th></tr></thead>
        <tbody id="doc-body"><tr><td colspan="8" class="loading">加载中…</td></tr></tbody>
      </table>
    </div>
  </div>
  <div id="doc-modal" class="modal-mask"></div>`;

  const r = await admin('/admin/teachers');
  const teachers = (r.teachers || []).filter(t => t.teacher_id !== '__sys__' && t.enabled !== false);
  const sel = $('#doc-teacher');
  sel.innerHTML = teachers.map(t => `<option value="${esc(t.teacher_id)}">${esc(t.teacher_name)}（${esc(t.teacher_id)}）</option>`).join('');
  _docTeacher = sel.value || 'T001';
  _docChecked.clear(); renderDocBatchBar();
  if (teachers.length) { await loadDocCategories(); loadDocuments(); }
  else $('#doc-body').innerHTML = '<tr><td colspan="8" class="empty">无在线老师</td></tr>';
}

// #25 R3：批量勾选状态
const _docChecked = new Set();
function renderDocBatchBar() {
  const hint = $('#doc-sel-hint');
  if (hint) hint.textContent = _docChecked.size ? `已选 ${_docChecked.size} 个文档` : '勾选文档后可批量上架/下架';
  const bar = $('#doc-batch-bar');
  if (bar) bar.style.opacity = _docChecked.size ? '1' : '.55';
}
function docCheck(name, on) { on ? _docChecked.add(name) : _docChecked.delete(name); renderDocBatchBar(); }
function docCheckAll(on) {
  $$('#doc-body input[type=checkbox][data-doc]').forEach(cb => { cb.checked = on; docCheck(cb.dataset.doc, on); });
}
async function docBatchSet(enabled) {
  const names = [..._docChecked];
  if (!names.length) { toast('请先勾选要操作的文档', 'err'); return; }
  if (!confirm(`确认${enabled ? '批量上架' : '批量下架'} ${names.length} 个文档？`)) return;
  try {
    const r = await admin('/admin/documents/batch', { method: 'POST', body: JSON.stringify(
      { teacher_id: _docTeacher, doc_names: names, enabled }) });
    toast(`已${enabled ? '上架' : '下架'} ${r.applied} 个${r.missing.length ? '，未登记 ' + r.missing.length + ' 个' : ''}`, 'ok');
    _docChecked.clear(); renderDocBatchBar(); loadDocuments();
  } catch (e) { toast('批量操作失败：' + e.message, 'err'); }
}

// #24 R5：切换老师 → 刷新分类下拉再拉文档
async function docSwitch() {
  await loadDocCategories();
  loadDocuments();
}

// #24 R5：分类下拉（来自注册表聚合）+ 统计卡
async function loadDocCategories() {
  try {
    const tid = ($('#doc-teacher') && $('#doc-teacher').value) || _docTeacher || 'T001';
    const r = await admin(`/admin/documents/registry?teacher_id=${encodeURIComponent(tid)}&with_chunks=true`);
    const cats = r.categories || [];
    const sel = $('#doc-cat');
    if (sel) {
      const cur = docPage.category;
      sel.innerHTML = '<option value="">全部分类（' + (r.documents || []).length + '）</option>' + cats.map(c =>
        `<option value="${esc(c.category)}">${esc(c.category || '未分类')}（${c.count}）</option>`).join('');
      if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
      else docPage.category = '';
    }
    $('#doc-stats-cards').innerHTML = `
      <div class="card"><div class="card-label">文档数</div><div class="card-n">${(r.documents || []).length}</div><div class="card-sub">已上传课件</div></div>
      <div class="card"><div class="card-label">分类数</div><div class="card-n gold">${cats.length}</div><div class="card-sub">可动态打标归档</div></div>
      <div class="card"><div class="card-label">知识块</div><div class="card-n green">${(r.documents || []).reduce((s, d) => s + (d.chunks || 0), 0)}</div><div class="card-sub">向量库切块</div></div>`;
  } catch (e) { /* 静默 */ }
}

async function loadDocuments() {
  const tbody = $('#doc-body');
  if (!tbody) return;
  const tid = ($('#doc-teacher') && $('#doc-teacher').value) || _docTeacher || 'T001';
  _docTeacher = tid;
  docPage.keyword = ($('#doc-kw') && $('#doc-kw').value || '').trim();
  docPage.category = ($('#doc-cat') && $('#doc-cat').value) || '';
  const state = ($('#doc-state') && $('#doc-state').value) || '';
  tbody.innerHTML = '<tr><td colspan="7" class="loading">加载中…</td></tr>';
  try {
    const r = await admin(`/admin/documents/registry?teacher_id=${encodeURIComponent(tid)}&with_chunks=true&category=${encodeURIComponent(docPage.category)}&keyword=${encodeURIComponent(docPage.keyword)}`);
    let docs = r.documents || [];
    if (state === 'enabled') docs = docs.filter(d => d.enabled !== false);
    else if (state === 'disabled') docs = docs.filter(d => d.enabled === false);
    if (!docs.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="empty">${docPage.keyword || docPage.category || state ? '无匹配文档' : '该老师暂无文档，点击「上传文档」入库'}</td></tr>`;
      return;
    }
    tbody.innerHTML = docs.map(d => {
      _docMeta[d.doc_name] = { category: d.category || '', tags: d.tags || '' };
      const off = d.enabled === false;
      return `
      <tr style="${off ? 'opacity:.55' : ''}">
        <td><input type="checkbox" data-doc="${esc(d.doc_name)}" ${_docChecked.has(d.doc_name) ? 'checked' : ''} onchange="docCheck('${esc(d.doc_name)}', this.checked)" aria-label="选择文档"></td>
        <td><b title="${esc(d.doc_name)}">${esc((d.doc_name || '').slice(0, 40))}${(d.doc_name || '').length > 40 ? '…' : ''}</b></td>
        <td><code>${esc(((d.file_ext || d.doc_name || '').split('.').pop() || '').toUpperCase())}</code></td>
        <td>${d.category ? `<span class="badge blue">${esc(d.category)}</span>` : '<span class="badge gray">未分类</span>'}</td>
        <td><span class="mono">${d.chunks ?? 0}</span></td>
        <td><span class="mono">${(d.chars ?? 0).toLocaleString()}</span></td>
        <td>${off
            ? '<span class="badge gray" title="已下架：不再参与检索与问答">已下架</span>'
            : '<span class="badge green">已上架</span>'}</td>
        <td><div class="ops">
          <button class="btn sm ghost" onclick="previewDoc('${esc(d.doc_name)}')">预览</button>
          ${off
            ? `<button class="btn sm primary" onclick="docSetEnabled('${esc(d.doc_name)}', true)">上架</button>`
            : `<button class="btn sm ghost warn" onclick="docSetEnabled('${esc(d.doc_name)}', false)">下架</button>`}
          <button class="btn sm ghost" onclick="docMetaModal('${esc(d.doc_name)}')">分类/标签</button>
          <button class="btn sm danger" onclick="deleteDoc('${esc(d.doc_name)}')">删除</button>
        </div></td>
      </tr>`;
    }).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="8" class="err-tip">加载失败：${esc(e.message)}</td></tr>`;
  }
}

// #24 R5：上下架（下架即时生效：检索过滤 + 缓存失效）
async function docSetEnabled(docName, enabled) {
  try {
    await admin('/admin/documents/meta', { method: 'POST',
      body: JSON.stringify({ teacher_id: _docTeacher, doc_name: docName, enabled }) });
    toast(enabled ? `「${docName}」已上架` : `「${docName}」已下架，不再参与问答`, 'ok');
    loadDocuments();
  } catch (e) { toast('操作失败：' + e.message, 'err'); }
}

// #24 R5：编辑分类/标签
function docMetaModal(docName) {
  const m = _docMeta[docName] || { category: '', tags: '' };
  openModal('doc-modal', `
    <h3>文档元数据 · ${esc(docName)}</h3>
    <div class="field"><label for="dm-cat">分类（用于分类浏览与归档）</label>
      <input id="dm-cat" value="${esc(m.category)}" maxlength="30" placeholder="如：行测-判断推理"></div>
    <div class="field"><label for="dm-tags">标签（逗号分隔，用于检索）</label>
      <input id="dm-tags" value="${esc(m.tags)}" maxlength="80" placeholder="如：图形推理,黑白块"></div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('doc-modal')">取消</button>
      <button class="btn primary" onclick="docMetaSave('${esc(docName)}')">保存</button>
    </div>
    <p class="hint" id="dm-hint"></p>`);
}
async function docMetaSave(docName) {
  const hint = $('#dm-hint');
  try {
    await admin('/admin/documents/meta', { method: 'POST', body: JSON.stringify({
      teacher_id: _docTeacher, doc_name: docName,
      category: $('#dm-cat').value.trim(), tags: $('#dm-tags').value.trim() }) });
    hint.textContent = '已保存';
    hint.className = 'hint ok';
    toast('文档元数据已更新', 'ok');
    setTimeout(() => { closeModal('doc-modal'); loadDocCategories(); loadDocuments(); }, 700);
  } catch (e) { hint.textContent = '保存失败：' + e.message; hint.className = 'hint err'; }
}

// #24 R5：语义内容检索（跨全部文档，聚合命中片段）
function docSearchModal() {
  openModal('doc-modal', `
    <h3>🔍 文档内容检索</h3>
    <p style="font-size:12.5px;color:var(--ink-3);margin-bottom:10px">按语义在全量（已上架）文档块中检索，聚合到文档展示命中片段。支持模糊/近义表达。</p>
    <div class="field"><label for="ds-q">检索内容 *</label>
      <input id="ds-q" maxlength="80" placeholder="如：假言命题的翻译规则" onkeydown="if(event.key==='Enter')docDoSearch()"></div>
    <div id="ds-result" style="margin-top:10px;max-height:48vh;overflow:auto"></div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('doc-modal')">取消</button>
      <button class="btn primary" onclick="docDoSearch()">检索</button>
    </div>
    <p class="hint" id="ds-hint"></p>`);
}
async function docDoSearch() {
  const q = ($('#ds-q') && $('#ds-q').value || '').trim();
  const box = $('#ds-result'), hint = $('#ds-hint');
  if (!q) { hint.textContent = '请输入检索内容'; hint.className = 'hint err'; return; }
  box.innerHTML = '<div class="loading">检索中…</div>';
  hint.textContent = '';
  try {
    const r = await admin('/admin/documents/search', { method: 'POST', body: JSON.stringify(
      { teacher_id: _docTeacher, query: q, top_n: 15 }) });
    const results = r.results || [];
    box.innerHTML = !results.length
      ? '<div class="empty">未检索到相关内容</div>'
      : results.map(d => `
        <div style="padding:10px 12px;border:1px solid var(--line);border-radius:8px;margin-bottom:8px">
          <b>📄 ${esc(d.doc_name)}</b> <span class="badge green">${d.hits.length} 处命中</span>
          ${d.hits.map(h => `<div style="font-size:12.5px;color:var(--ink-3);margin-top:4px;padding-left:8px;border-left:2px solid var(--line)"><span class="mono" style="color:var(--gold-deep)">${h.score}</span> ${esc(h.content)}</div>`).join('')}
        </div>`).join('');
  } catch (e) {
    box.innerHTML = `<p class="err-tip">检索失败：${esc(e.message)}</p>`;
  }
}

// 上传文档（复用 /upload，走 admin 后需 teacher_id；上传接口无需 Bearer，但需要 X-Upload-Secret 若配置）
function openUploadDoc() {
  openModal('doc-modal', `
    <h3>上传文档入库</h3>
    <div class="upload-zone" id="doc-upload-zone" role="button" tabindex="0" aria-label="点击选择或拖拽上传文档">
      <span class="uz-ico">📄</span>
      <div class="uz-tip">点击选择或拖拽文件到此处</div>
      <div class="uz-sub" id="doc-uz-sub">支持 md / txt / docx / pptx / pdf / xlsx · 上限 50MB（暂不支持 .xls，请另存为 .xlsx）</div>
    </div>
    <input type="file" id="doc-file" accept=".md,.txt,.docx,.pptx,.pdf,.xlsx" style="display:none">
    <p class="hint" id="doc-up-hint"></p>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('doc-modal')">关闭</button>
    </div>
  `);
  const zone = $('#doc-upload-zone');
  const fileInput = $('#doc-file');
  zone.onclick = () => fileInput.click();
  zone.ondragover = (e) => { e.preventDefault(); zone.classList.add('drag'); };
  zone.ondragleave = () => zone.classList.remove('drag');
  zone.ondrop = (e) => { e.preventDefault(); zone.classList.remove('drag'); if (e.dataTransfer.files[0]) doUploadDoc(e.dataTransfer.files[0]); };
  fileInput.onchange = () => { if (fileInput.files[0]) doUploadDoc(fileInput.files[0]); };
}

async function doUploadDoc(file) {
  const hint = $('#doc-up-hint');
  hint.textContent = `上传中：${file.name} …`;
  hint.className = 'hint';
  const fd = new FormData();
  fd.append('file', file);
  const tid = _docTeacher || 'T001';
  try {
    // 直接 fetch 带 token（upload 接口本身不需 Bearer，但需可能存在的 X-Upload-Secret）
    const headers = { Authorization: `Bearer ${token}` };
    const secret = localStorage.getItem('uploadSecret');
    if (secret) headers['X-Upload-Secret'] = secret;
    let r = await fetch(`/api/upload?teacher_id=${encodeURIComponent(tid)}`, { method: 'POST', body: fd, headers });
    if (r.status === 403) {
      // 需要密钥
      const s = prompt('该平台上传需密钥（请向管理员索取）：');
      if (!s) { hint.textContent = '已取消'; return; }
      localStorage.setItem('uploadSecret', s);
      const h2 = { Authorization: `Bearer ${token}`, 'X-Upload-Secret': s };
      r = await fetch(`/api/upload?teacher_id=${encodeURIComponent(tid)}`, { method: 'POST', body: fd, headers: h2 });
    }
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || ('HTTP ' + r.status)); }
    const d = await r.json();
    hint.textContent = `✅ ${d.filename}：${d.chunks} 块入库，累计 ${d.total} 块`;
    hint.className = 'hint ok';
    toast('文档已入库', 'ok');
    setTimeout(() => { closeModal('doc-modal'); loadDocuments(); }, 900);
  } catch (e) {
    hint.textContent = '❌ 上传失败：' + e.message;
    hint.className = 'hint err';
  }
}

// 预览文档
async function previewDoc(docName) {
  openModal('doc-modal', `<h3>文档预览</h3><div class="loading">加载中…</div>`);
  try {
    const tid = _docTeacher || 'T001';
    const d = await admin(`/admin/documents/preview?teacher_id=${encodeURIComponent(tid)}&doc_name=${encodeURIComponent(docName)}`);
    const box = $('#doc-modal .modal-box');
    box.innerHTML = `
      <h3>文档预览 · ${esc(d.doc_name)}</h3>
      <div class="detail-row"><span class="k">总字符</span><span class="v">${d.chars}${d.truncated ? '（仅显示前 4000 字）' : ''}</span></div>
      <div style="max-height:46vh;overflow:auto;background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:14px;font-size:13px;line-height:1.8;white-space:pre-wrap;word-break:break-word;margin-top:10px">${esc(d.preview)}</div>
      <div class="modal-actions"><button class="btn ghost sm" onclick="closeModal('doc-modal')">关闭</button></div>
    `;
  } catch (e) {
    $('#doc-modal .modal-box').innerHTML = `<h3>预览失败</h3><p class="err-tip">${esc(e.message)}</p>
      <div class="modal-actions"><button class="btn ghost sm" onclick="closeModal('doc-modal')">关闭</button></div>`;
  }
}

// 删除文档
function deleteDoc(docName) {
  openModal('doc-modal', `
    <h3>删除文档</h3>
    <div class="err-tip" style="margin-bottom:12px">⚠️ 确认删除「${esc(docName)}」？<br>将移除其全部知识块并失效缓存，<b>不可恢复</b>。</div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('doc-modal')">取消</button>
      <button class="btn danger" onclick="submitDeleteDoc('${esc(docName)}')">确认删除</button>
    </div>
    <p class="hint" id="doc-del-hint"></p>
  `);
}
async function submitDeleteDoc(docName) {
  const tid = _docTeacher || 'T001';
  const hint = $('#doc-del-hint');
  hint.textContent = '删除中…';
  try {
    const r = await admin(`/admin/documents/${encodeURIComponent(docName)}?teacher_id=${encodeURIComponent(tid)}`, { method: 'DELETE' });
    toast(`已删除 ${r.deleted ?? 0} 个知识块`, 'ok');
    closeModal('doc-modal');
    loadDocuments();
  } catch (e) {
    hint.textContent = '删除失败：' + e.message;
    hint.className = 'hint err';
  }
}

// ============================================================
// 题库管理（#16 优化 + AI 采集）
// ============================================================
let qbPage = { cur: 1, size: 20, qtype: '', difficulty: '', kp: '', keyword: '', total: 0 };
let _qbSubTab = 'list';   // 题库管理子页：list 列表 / cat 分类
let _qbCatPaths = {};   // #24 R2: {node_id: '路径'} 题型分类路径映射
let _qbCatOptions = ''; // #24 R2: 分类下拉 options（缓存）

// #35 R1: 课程分类 8 大类（与后端 study.QUESTION_CATEGORIES 枚举一字面对齐）
const QB_CATEGORIES = ['言语理解', '判断推理', '数量关系', '资料分析', '常识判断', '申论', '面试', '综合'];
const QB_CAT_OPTIONS_HTML = '<option value="">全部课程分类</option><option value="__uncat__">（未分类）</option>' +
  QB_CATEGORIES.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
const QB_CAT_BADGE_COLOR = {
  '言语理解': 'blue', '判断推理': 'purple', '数量关系': 'green', '资料分析': 'gold',
  '常识判断': 'blue', '申论': 'purple', '面试': 'green', '综合': 'gray'
};
// 题目来源枚举（与后端 study.QUESTION_SOURCE_TYPES 对齐）
const QB_SOURCE_TYPES = ['AI采集', '用户提供', '公开题库', '文档库', '互联网搜索'];
const QB_SOURCE_BADGE_COLOR = {
  'AI采集': 'blue', '用户提供': 'green', '公开题库': 'purple', '文档库': 'gold', '互联网搜索': 'gray'
};
const QB_DIFF_OPTIONS = [1, 2, 3, 4, 5].map(d => `<option value="${d}">${'★'.repeat(d)}</option>`).join('');

async function renderQuestions() {
  const c = $('#content');
  c.innerHTML = `
  <div class="page-head">
    <h2>题库管理</h2><p>真题题库 · 录入 / 筛选 / AI 采集入库 · 题型分类管理</p>
    <div class="qb-subnav" style="margin-top:10px;display:flex;gap:8px">
      <button class="btn sm subnav-btn ${_qbSubTab === 'list' ? 'active' : ''}" onclick="qbSubTab('list')">📝 题库列表</button>
      <button class="btn sm subnav-btn ${_qbSubTab === 'cat' ? 'active' : ''}" onclick="qbSubTab('cat')">🗂 题库分类</button>
    </div>
  </div>
  <div id="qb-body-wrap"></div>
  <div id="qb-modal" class="modal-mask"></div>`;
  if (_qbSubTab === 'cat') { renderQCat(); return; }
  renderQList();
}

function qbSubTab(t) { _qbSubTab = t; renderQuestions(); }

async function renderQList() {
  const c = $('#qb-body-wrap');
  c.innerHTML = `
  <div class="cards" id="qb-stats-cards" style="grid-template-columns:repeat(auto-fit,minmax(140px,1fr))"></div>
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap">
        <label for="qb-teacher">老师：</label><select id="qb-teacher" onchange="qbSwitchTeacher()" aria-label="选择老师"></select>
        <select id="qb-qtype" onchange="loadQuestions()" aria-label="按题型筛选">
          <option value="">全部题型</option>
          <option value="choice">选择题</option>
          <option value="judge">判断题</option>
          <option value="essay">简答题</option>
        </select>
        <select id="qb-difficulty" onchange="loadQuestions()" aria-label="按难度筛选">
          <option value="">全部难度</option>
          ${QB_DIFF_OPTIONS}
        </select>
        <select id="qb-kp" onchange="loadQuestions()" aria-label="按知识点筛选"><option value="">全部知识点</option></select>
        <label for="qb-category">课程分类：</label><select id="qb-category" onchange="loadQuestions()" aria-label="按课程分类筛选">${QB_CAT_OPTIONS_HTML}</select>
        <label for="qb-cat">题型分类：</label><select id="qb-cat" onchange="loadQuestions()" aria-label="按题型分类筛选（含子树）">
          <option value="">全部题型分类</option><option value="__uncat__">（未关联分类）</option>${_qbCatOptions}
        </select>
        <label for="qb-source">来源：</label><select id="qb-source" onchange="loadQuestions()" aria-label="按来源筛选">
          <option value="">全部来源</option>${QB_SOURCE_TYPES.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('')}
        </select>
        <input type="search" id="qb-kw" class="grow" placeholder="题目/解析关键词…" aria-label="搜索题目" value="${esc(qbPage.keyword)}">
        <button class="btn ghost" onclick="loadQuestions()">搜索</button>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn primary" onclick="openQuestionImport()">🤖 AI 采集</button>
        <button class="btn ghost" onclick="showQuestionForm()">＋ 手动录入</button>
      </div>
    </div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr>
          <th style="width:34px"><input type="checkbox" onchange="qbCheckAll(this.checked)" aria-label="全选本页"></th>
          <th>ID</th><th>题型</th><th>题目</th><th>答案</th><th>分类</th><th>知识点</th><th>题型分类</th><th>来源</th><th>难度</th><th>操作</th>
        </tr></thead>
        <tbody id="qb-body"><tr><td colspan="11" class="loading">加载中…</td></tr></tbody>
      </table>
    </div>
    <div class="panel-head" style="border-top:1px solid var(--line)" id="qb-batch-bar">
      <div class="toolbar" style="flex:1;flex-wrap:wrap">
        <span class="hint" id="qb-sel-hint">勾选题目后可批量删除</span>
        <button class="btn ghost sm" onclick="qbBatchDelete()">🗑 批量删除</button>
        <button class="btn sm" style="background:var(--gold-soft);border-color:var(--gold)" onclick="qbAiCategorize()">🏷 AI 补充分类</button>
        <button class="btn sm" style="background:var(--gold-soft);border-color:var(--gold)" onclick="qbAiAnalysisBatch()">✨ 批量 AI 解析</button>
      </div>
      <button class="btn ghost sm" onclick="exportQuestions()">📤 导出当前筛选(CSV)</button>
    </div>
    <div class="pager" id="qb-pager"></div>
  </div>`;

  const r = await admin('/admin/teachers');
  const teachers = (r.teachers || []).filter(t => t.teacher_id !== '__sys__');
  const sel = $('#qb-teacher');
  sel.innerHTML = teachers.map(t => `<option value="${esc(t.teacher_id)}">${esc(t.teacher_name)}（${esc(t.teacher_id)}）</option>`).join('');
  if (teachers.length) { await loadCatOptions(); await loadQuestions(); await loadQbStats(); await loadKpOptions(); }
  else $('#qb-body').innerHTML = '<tr><td colspan="11" class="empty">无老师</td></tr>';
}

// ============================================================
// 自动采集专用通道（AI 模型管理页面板 · ac-llm-*）
// ============================================================
let _acLlm = null;
let _acLlmRemote = [];
let _acLlmEditIdx = -1;  // 正在编辑的通道下标，-1 表示新增

// 从模型注册表抽取去重平台列表（base_url -> 示例模型/提供方）
async function acLlmPlatformOptions() {
  let models = [];
  try { const r = await admin('/admin/ai-models'); models = r.models || []; } catch (e) { models = _aimCache || []; }
  const seen = {}, out = [];
  (models.length ? models : (_aimCache || [])).forEach(m => {
    const bu = (m.base_url || '').trim();
    if (!bu || seen[bu]) return;
    seen[bu] = 1;
    out.push({ base_url: bu, label: `${m.provider || 'anyyds'} · ${esc(m.model_id)}`, model: m.model_id });
  });
  return out;
}

async function acLlmLoad() {
  const list = $('#ac-llm-list');
  const state = $('#ac-llm-state');
  const clearBtn = $('#ac-llm-clear');
  if (!list && !state) return;
  try {
    const r = await admin('/admin/autocollect/llm/channels');
    const chs = r.channels || [];
    _acLlm = { channels: chs, configured: chs.length > 0 };
    const baseEl = $('#ac-llm-base'), keyEl = $('#ac-llm-key'), sel = $('#ac-llm-model');
    if (clearBtn) clearBtn.style.display = chs.length ? '' : 'none';

    // 平台下拉（注册表已有平台 + 自定义）
    const pf = $('#ac-llm-platform');
    if (pf) {
      const opts = await acLlmPlatformOptions();
      pf.innerHTML = '<option value="">— 自定义（下方手填 Base URL） —</option>'
        + opts.map(o => `<option value="${esc(o.base_url)}">${o.label}</option>`).join('');
    }

    if (list) {
      list.innerHTML = chs.length
        ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>#</th><th>模型</th><th>Base URL</th><th>API Key</th><th style="width:130px">操作</th></tr></thead><tbody>` +
          chs.map((c, i) => `<tr>
              <td>${i === 0 ? '主' : (i + 1)}</td>
              <td><code>${esc(c.model)}</code></td>
              <td><code>${esc(c.base_url)}</code></td>
              <td>${esc(c.api_key_masked || '全局密钥')}</td>
              <td>
                <button class="btn sm ghost" onclick="acLlmEdit(${i})">✏️</button>
                <button class="btn sm ghost" onclick="acLlmDel(${i})">🗑</button>
              </td>
            </tr>`).join('') +
          `</tbody></table></div>`
        : '<div class="hint">尚未配置专用通道；回落 AUTOCOLLECT_* 环境变量 / 平台默认模型</div>';
    }
    if (_acLlmEditIdx >= 0) {
      // 编辑态：回填当前正在编辑的通道到表单
      const cur = chs[_acLlmEditIdx];
      if (cur) {
        if (baseEl) baseEl.value = cur.base_url;
        if (sel) { sel.innerHTML = `<option value="${esc(cur.model)}">${esc(cur.model)}</option>`; }
      }
    } else {
      if (baseEl && !baseEl.value) baseEl.value = '';
      if (keyEl) keyEl.value = '';
      if (sel && !sel.value) sel.innerHTML = '<option value="">— 先选平台再「拉取模型列表」 —</option>';
    }
    if (state) {
      state.className = chs.length ? 'hint ok' : 'hint';
      state.textContent = chs.length
        ? `✅ 已配置 ${chs.length} 个平台：${chs.map(c => c.model).join(' · ')}`
        : '未配置（自动采集将回落 AUTOCOLLECT_* / 平台默认模型）';
    }
  } catch (e) {
    if (state) { state.className = 'hint err'; state.textContent = '读取专用通道失败：' + (e.message || e); }
  }
}

async function acLlmPlatformPick() {
  const pf = $('#ac-llm-platform');
  const baseEl = $('#ac-llm-base'), sel = $('#ac-llm-model');
  const bu = (pf && pf.value) || '';
  if (baseEl) baseEl.value = bu;
  if (sel) sel.innerHTML = '<option value="">— 选择平台后「拉取模型列表」 —</option>';
  $('#ac-llm-key').value = '';
}

async function acLlmEdit(idx) {
  const chs = (_acLlm && _acLlm.channels) || [];
  const c = chs[idx];
  if (!c) return;
  _acLlmEditIdx = idx;
  $('#ac-llm-base').value = c.base_url;
  $('#ac-llm-key').value = '';
  const sel = $('#ac-llm-model');
  sel.innerHTML = `<option value="${esc(c.model)}">${esc(c.model)}</option>`;
  // 平台下拉尽量匹配当前 base_url
  const pf = $('#ac-llm-platform');
  if (pf) {
    [...pf.options].forEach(o => { if (o.value === c.base_url) pf.value = o.value; });
    if (pf.value !== c.base_url) pf.value = '';
  }
  $('#ac-llm-save-btn').textContent = '💾 保存修改';
  $('#ac-llm-cancel').style.display = '';
  $('#ac-llm-state').className = 'hint';
  $('#ac-llm-state').textContent = '正在编辑第 ' + (idx + 1) + ' 个通道：' + c.model;
}

function acLlmCancelEdit() {
  _acLlmEditIdx = -1;
  $('#ac-llm-save-btn').textContent = '＋ 添加平台';
  $('#ac-llm-cancel').style.display = 'none';
  $('#ac-llm-base').value = '';
  $('#ac-llm-key').value = '';
  $('#ac-llm-platform').value = '';
  $('#ac-llm-model').innerHTML = '<option value="">— 先选平台再「拉取模型列表」 —</option>';
  $('#ac-llm-state').className = 'hint';
  $('#ac-llm-state').textContent = '已取消编辑';
}

async function acLlmFetch() {
  const base = ($('#ac-llm-base').value || '').trim();
  const key = ($('#ac-llm-key').value || '').trim();
  const state = $('#ac-llm-state');
  const sel = $('#ac-llm-model');
  const pick = $('#ac-llm-pick');
  if (!base) { state.className = 'hint err'; state.textContent = '请先在下方选择平台或填写 Base URL'; return; }
  state.className = 'hint';
  state.textContent = '正在请求远端模型列表…';
  if (sel) { sel.innerHTML = ''; sel.options.add(new Option('加载中…', '')); }
  try {
    const r = await admin('/admin/ai-models/list-remote', {
      method: 'POST', body: JSON.stringify({ base_url: base, provider: '', api_key: key }),
    });
    _acLlmRemote = r.models || [];
    const current = ($('#ac-llm-model').value || '').trim();
    if (sel) {
      sel.innerHTML = '';
      if (!current) sel.options.add(new Option('— 请选择模型 —', ''));
      _acLlmRemote.forEach(id => sel.options.add(new Option(id, id, false, id === current)));
      if (current && _acLlmRemote.indexOf(current) < 0) sel.options.add(new Option(current + '（当前）', current));
    }
    if (pick) {
      pick.style.display = '';
      pick.innerHTML = _acLlmRemote.length
        ? _acLlmRemote.map(id => `<label class="chk" style="display:block;padding:3px 2px"><input type="checkbox" value="${esc(id)}"><span style="margin-left:4px">${esc(id)}</span></label>`).join('')
        : '<div class="empty">未获取到任何模型</div>';
      const cnt = $('#ac-llm-model-count'); if (cnt) cnt.value = `共 ${_acLlmRemote.length} 个模型，勾选后点击「一键多选(批量添加)」`;
    }
    state.className = r.ok ? 'hint ok' : 'hint err';
    state.textContent = (r.ok ? `✅ 拉取成功：${_acLlmRemote.length} 个模型（${esc(r.base_url || base)}），可勾选批量添加或单选保存`
      : `⚠️ 远端获取失败，已展示内置兜底目录：${esc(r.error || '')}`);
  } catch (e) {
    state.className = 'hint err';
    state.textContent = '拉取失败：' + (e.message || e);
  }
}

function acLlmPickAll(v) {
  const pick = $('#ac-llm-pick');
  if (!pick) return;
  Array.from(pick.querySelectorAll('input[type=checkbox]')).forEach(i => i.checked = !!v);
}

async function acLlmBatchAdd() {
  const pick = $('#ac-llm-pick');
  if (!pick) return;
  const base = ($('#ac-llm-base').value || '').trim();
  const key = ($('#ac-llm-key').value || '').trim();
  const state = $('#ac-llm-state');
  if (!base) { state.className = 'hint err'; state.textContent = '请先选择平台或填写 Base URL'; return; }
  const models = Array.from(pick.querySelectorAll('input[type=checkbox]:checked')).map(i => i.value.trim()).filter(Boolean);
  if (!models.length) { state.className = 'hint err'; state.textContent = '请先「拉取模型列表」并勾选要添加的模型'; return; }
  state.className = 'hint'; state.textContent = `批量添加 ${models.length} 个模型…`;
  let ok = 0, skipped = 0, firstErr = '';
  try {
    for (const m of models) {
      const payload = { model: m, base_url: base };
      if (key && key.indexOf('****') < 0) payload.api_key = key;
      try { await admin('/admin/autocollect/llm/channels', { method: 'POST', body: JSON.stringify(payload) }); ok++; }
      catch (e) { if (String(e).indexOf('已存在') >= 0) skipped++; else if (!firstErr) firstErr = e.message || e; }
    }
    state.className = 'hint ok';
    state.textContent = `✅ 批量添加完成：新增 ${ok}，跳过重复 ${skipped}${firstErr ? ('，失败：' + firstErr) : ''}`;
    toast(`批量添加完成：新增 ${ok}，跳过重复 ${skipped}`, ok ? 'ok' : 'err');
    $('#ac-llm-base').value = ''; $('#ac-llm-key').value = ''; $('#ac-llm-platform').value = '';
    const sel = $('#ac-llm-model'); if (sel) sel.innerHTML = '<option value="">— 先选平台再「拉取模型列表」 —</option>';
    if (pick) { pick.style.display = 'none'; pick.innerHTML = ''; }
    acLlmLoad();
  } catch (e) {
    state.className = 'hint err'; state.textContent = '批量添加失败：' + (e.message || e);
  }
}

async function acLlmSave() {
  const base = ($('#ac-llm-base').value || '').trim();
  const key = ($('#ac-llm-key').value || '').trim();
  const model = ($('#ac-llm-model').value || '').trim();
  const state = $('#ac-llm-state');
  if (!base) { state.className = 'hint err'; state.textContent = '请填写 Base URL'; return; }
  if (!model) { state.className = 'hint err'; state.textContent = '请先「拉取模型列表」并选择一个模型'; return; }
  const payload = { model, base_url: base };
  if (key && key.indexOf('****') < 0) payload.api_key = key;
  state.className = 'hint';
  state.textContent = _acLlmEditIdx >= 0 ? '保存修改中…' : '添加中…';
  try {
    let info;
    if (_acLlmEditIdx >= 0) {
      info = await admin(`/admin/autocollect/llm/channels/${_acLlmEditIdx}`, {
        method: 'PUT', body: JSON.stringify(payload),
      });
      toast(`已保存通道：${model}`, 'ok');
    } else {
      info = await admin('/admin/autocollect/llm/channels', {
        method: 'POST', body: JSON.stringify(payload),
      });
      toast(`已添加采集平台：${model}（现共 ${(info.channels || []).length} 个平台）`, 'ok');
    }
    _acLlmEditIdx = -1;
    $('#ac-llm-save-btn').textContent = '＋ 添加平台';
    $('#ac-llm-cancel').style.display = 'none';
    const baseEl = $('#ac-llm-base'); if (baseEl) baseEl.value = '';
    const keyEl = $('#ac-llm-key'); if (keyEl) keyEl.value = '';
    const pf = $('#ac-llm-platform'); if (pf) pf.value = '';
    const sel = $('#ac-llm-model'); if (sel) sel.innerHTML = '<option value="">— 先选平台再「拉取模型列表」 —</option>';
    acLlmLoad();
  } catch (e) {
    state.className = 'hint err';
    state.textContent = (e.message || e);
    toast('保存失败：' + (e.message || e), 'err');
  }
}

async function acLlmDel(idx) {
  if (!confirm('删除该自动采集通道？')) return;
  const state = $('#ac-llm-state');
  try {
    await admin(`/admin/autocollect/llm/channels/${idx}`, { method: 'DELETE' });
    if (_acLlmEditIdx === idx) acLlmCancelEdit();
    toast('已删除采集通道', 'ok');
    acLlmLoad();
  } catch (e) {
    if (state) { state.className = 'hint err'; state.textContent = '删除失败：' + (e.message || e); }
    toast('删除失败：' + (e.message || e), 'err');
  }
}

async function acLlmClear() {
  const chs = (_acLlm && _acLlm.channels) || [];
  if (!chs.length) return;
  if (!confirm(`清空全部 ${chs.length} 个自动采集专用通道？\n\n后续自动采集将回落 AUTOCOLLECT_* / 平台默认模型。`)) return;
  const state = $('#ac-llm-state');
  try {
    for (let i = chs.length - 1; i >= 0; i--) {
      await admin(`/admin/autocollect/llm/channels/${i}`, { method: 'DELETE' });
    }
    toast('已清空自动采集专用通道', 'ok');
    acLlmLoad();
  } catch (e) {
    if (state) { state.className = 'hint err'; state.textContent = '清空失败：' + (e.message || e); }
    toast('清空失败：' + (e.message || e), 'err');
  }
}

// ============================================================
// 题库分类管理（子页 · question_category）
// ============================================================
let qcat = { list: [], teacher: '' };

async function renderQCat() {
  const c = $('#qb-body-wrap');
  c.innerHTML = `
  <div class="cards" id="qc-stats-cards" style="grid-template-columns:repeat(auto-fit,minmax(140px,1fr))"></div>
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap">
        <label for="qc-teacher">老师：</label>
        <select id="qc-teacher" onchange="qcLoad()" aria-label="选择老师"></select>
        <button class="btn ghost sm" onclick="qcLoad()">⟳ 刷新</button>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn ghost sm" onclick="qcCreateTemplate()">⚡ 生成标准分类</button>
        <button class="btn ghost sm" onclick="qcApplySort()">↕️ 保存排序</button>
        <button class="btn primary" onclick="qcForm()">＋ 新增分类</button>
      </div>
    </div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr><th>层级</th><th>分类名称</th><th>描述</th><th>序号</th><th>状态</th><th style="width:230px">操作</th></tr></thead>
        <tbody id="qc-body"><tr><td colspan="6" class="loading">加载中…</td></tr></tbody>
      </table>
    </div>
  </div>
  <div id="qc-modal" class="modal-mask"></div>`;

  const r = await admin('/admin/teachers');
  const teachers = (r.teachers || []).filter(t => t.teacher_id !== '__sys__');
  const sel = $('#qc-teacher');
  sel.innerHTML = teachers.map(t => `<option value="${esc(t.teacher_id)}">${esc(t.teacher_name)}（${esc(t.teacher_id)}）</option>`).join('');
  if (teachers.length) qcLoad();
}

function qcTid() { return ($('#qc-teacher') && $('#qc-teacher').value) || qcat.teacher || 'T001'; }

// #29 R3：一键生成公考标准题型分类（幂等：已存在的标识跳过）
const QC_TEMPLATE = [
  ['gk01', '常识判断', '政治、经济、法律、人文、科技等常识性考点'],
  ['gk02', '言语理解与表达', '选词填空、片段阅读、语句表达'],
  ['gk03', '数量关系', '数学运算、数字推理'],
  ['gk04', '判断推理', '图形推理、定义判断、类比推理、逻辑判断'],
  ['gk05', '资料分析', '文字材料、表格、图形数据分析'],
  ['gk06', '申论', '归纳概括、综合分析、对策、应用文、大作文'],
  ['gk07', '公共基础知识', '行政法、宪法、民法、管理、公文写作等'],
];
async function qcCreateTemplate() {
  if (!confirm('将按公考通用题型生成 7 个一级分类（常识判断/言语/数量/判断推理/资料分析/申论/公共基础知识）？\n已存在的分类标识会自动跳过。')) return;
  const tid = qcTid();
  const made = [], skip = [];
  for (const [cid, name, desc] of QC_TEMPLATE) {
    try {
      await admin('/admin/questions/categories', { method: 'POST', body: JSON.stringify({ teacher_id: tid, category_id: cid, name, description: desc, sort_order: made.length + 1 }) });
      made.push(name);
    } catch (e) {
      if (/已存在|exists/i.test(e.message || '')) skip.push(name);
      else { toast('「' + name + '」创建失败：' + e.message, 'err'); }
    }
  }
  toast(`已生成 ${made.length} 个分类` + (skip.length ? `，跳过已存在 ${skip.length} 个` : ''), made.length ? 'ok' : 'info');
  qcLoad();
}

async function qcLoad() {
  const tbody = $('#qc-body');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="6" class="loading">加载中…</td></tr>';
  try {
    const r = await admin(`/admin/questions/categories?teacher_id=${encodeURIComponent(qcTid())}`);
    qcat.list = r.categories || [];
    qcat.teacher = qcTid();
    qcRenderStats(r.stats || {});
    qcRenderRows();
  } catch (e) { tbody.innerHTML = `<tr><td colspan="6" class="err-tip">加载失败：${esc(e.message)}</td></tr>`; }
}

function qcRenderStats(s) {
  $('#qc-stats-cards').innerHTML = `
    <div class="card"><div class="card-label">分类总数</div><div class="card-n">${s.total || 0}</div><div class="card-sub">一级 ${s.roots || 0} 组</div></div>
    <div class="card"><div class="card-label">启用</div><div class="card-n green">${s.enabled || 0}</div><div class="card-sub">正常使用中</div></div>
    <div class="card"><div class="card-label">停用</div><div class="card-n gold">${s.disabled || 0}</div><div class="card-sub">向下暂时隐藏</div></div>`;
}

function qcBytid(cid) { return qcat.list.find(x => x.category_id === cid); }

function qcRenderRows() {
  const tbody = $('#qc-body');
  if (!tbody) return;
  if (!qcat.list.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">暂无分类。点击「＋ 新增分类」创建第一层级（如：常识判断），再逐级细分。</td></tr>';
    return;
  }
  // 扁平列表按 path 排序以免乱跳
  const by_depth = new Map(); // parent_id -> children list
  qcat.list.forEach(x => { const k = x.parent_id || ''; if (!by_depth.has(k)) by_depth.set(k, []); by_depth.get(k).push(x); });
  const render = (pid, depth, acc) => {
    const kids = (by_depth.get(pid) || []).slice();
    kids.sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0) || (a.category_id < b.category_id ? -1 : 1));
    kids.forEach(k => {
      const kids2 = (by_depth.get(k.category_id) || []).length;
      acc.push({ nod: k, depth, hasKids: kids2 > 0 });
      render(k.category_id, depth + 1, acc);
    });
  };
  const flat = [];
  render('', 0, flat);

  tbody.innerHTML = flat.map(({ nod, depth, hasKids }) => `
    <tr style="${nod.enabled ? '' : 'opacity:.55'}">
      <td><span style="color:var(--ink-3)">${'—'.repeat(depth)}${depth + 1}</span></td>
      <td>
        <b style="${depth ? '' : 'font-size:14px'}">${esc('　'.repeat(depth))}${esc(nod.name)}</b>
        ${nod.path && nod.path !== nod.name ? `<br><small style="color:var(--ink-3)">${esc(nod.path)}</small>` : ''}
      </td>
      <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(nod.description || '')}">${esc(nod.description || '—')}</td>
      <td><input type="number" style="width:56px;padding:4px 6px;border:1px solid var(--line-2);border-radius:6px" value="${nod.sort_order || 0}" data-cid="${esc(nod.category_id)}" onchange="qcMarkSort(this)" aria-label="排序序号"></td>
      <td><span class="badge ${nod.enabled ? 'green' : 'gray'}">${nod.enabled ? '启用' : '停用'}</span></td>
      <td><div class="ops">
        <button class="btn sm ghost" onclick="qcForm('${esc(nod.category_id)}')">✏️ 编辑</button>
        <button class="btn sm ghost" onclick="qcForm(null,'${esc(nod.category_id)}')">＋子级</button>
        ${hasKids ? '' : `<button class="btn sm ghost" onclick="qcToggleEnable('${esc(nod.category_id)}')">${nod.enabled ? '停用' : '启用'}</button>`}
        <button class="btn sm danger" onclick="qcDelete('${esc(nod.category_id)}','${esc(nod.name)}')">🗑</button>
      </div></td>
    </tr>`).join('');
}

let _qcSortDirty = false;
function qcMarkSort(input) {
  _qcSortDirty = true;
  const cid = input.dataset.cid;
  const v = parseInt(input.value) || 0;
  const n = qcBytid(cid);
  if (n) n.sort_order = v;
  toggleSortBtn(true);
}
function toggleSortBtn(show) {
  const b = [...document.querySelectorAll('#qb-body-wrap .panel-head .btn')].find(x => x.textContent.includes('保存排序'));
  if (b) b.style.background = show ? 'var(--gold)' : '';
}
async function qcApplySort() {
  if (!_qcSortDirty) { toast('排序无改动', 'info'); return; }
  try {
    await admin('/admin/questions/categories/reorder', {
      method: 'POST', body: JSON.stringify({ teacher_id: qcTid(), ordered: qcat.list.map(x => ({ category_id: x.category_id, sort_order: x.sort_order || 0 })) }),
    });
    _qcSortDirty = false; toast('排序已保存', 'ok'); qcLoad();
  } catch (e) { toast('保存失败：' + e.message, 'err'); }
}

function qcParentOptions(exclude) {
  const banned = new Set();
  if (exclude) {
    const stack = [exclude];
    while (stack.length) {
      const id = stack.pop(); banned.add(id);
      qcat.list.filter(x => x.parent_id === id).forEach(c => stack.push(c.category_id));
    }
  }
  return '<option value="">（顶级）</option>' + qcat.list.filter(x => !banned.has(x.category_id)).sort((a, b) => ((a.path || a.name) < (b.path || b.name) ? -1 : 1)).map(x => `<option value="${esc(x.category_id)}">${esc(x.path || x.name)}</option>`).join('');
}

function qcForm(cid, asChildOf) {
  const n = cid ? qcBytid(cid) : null;
  openModal('qc-modal', `
    <h3>${n ? '✏️ 编辑分类' : '＋ 新增分类'}</h3>
    <div class="field"><label>分类标识 *</label><input id="qcf-cid" value="${esc(n ? n.category_id : '')}" ${n ? 'disabled' : ''} placeholder="如 subject01（新建后不可改）"></div>
    <div class="field"><label>分类名称 *</label><input id="qcf-name" value="${esc(n ? n.name : '')}" placeholder="如 常识判断"></div>
    <div class="field"><label>上级分类</label><select id="qcf-parent">${qcParentOptions(n ? n.category_id : '')}</select></div>
    <div class="field"><label>描述</label><input id="qcf-desc" value="${esc(n ? n.description || '' : '')}" placeholder="可选"></div>
    <div class="field"><label>排序序号</label><input id="qcf-sort" type="number" value="${n ? n.sort_order || 0 : 0}"></div>
    ${n ? `<div class="field"><label>状态</label><select id="qcf-enabled"><option value="1" ${n.enabled ? 'selected' : ''}>启用</option><option value="0" ${n.enabled ? '' : 'selected'}>停用</option></select></div>` : ''}
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('qc-modal')">取消</button>
      <button class="btn primary" onclick="qcSubmit('${esc(cid || '')}')">保存</button>
    </div>
    <p class="hint" id="qcf-hint"></p>
  `);
  // 预设上级
  const set = (excludeNode) => {
    const sel = $('#qcf-parent');
    if (asChildOf && excludeNode !== asChildOf) setTimeout(() => { if (sel) sel.value = asChildOf; }, 0);
  };
  set(n ? n.category_id : '');
}

async function qcSubmit(cid) {
  const hint = $('#qcf-hint');
  try {
    const payload = { teacher_id: qcTid() };
    if (cid) {
      payload.name = $('#qcf-name').value.trim();
      payload.parent_id = $('#qcf-parent').value;
      payload.description = $('#qcf-desc').value.trim();
      payload.sort_order = parseInt($('#qcf-sort').value) || 0;
      const en = $('#qcf-enabled');
      if (en) payload.enabled = en.value === '1';
      await admin('/admin/questions/categories/' + encodeURIComponent(cid) + '?teacher_id=' + encodeURIComponent(qcTid()), { method: 'PATCH', body: JSON.stringify(payload) });
      toast('分类已更新', 'ok');
    } else {
      payload.category_id = $('#qcf-cid').value.trim();
      payload.name = $('#qcf-name').value.trim();
      payload.parent_id = $('#qcf-parent').value;
      payload.description = $('#qcf-desc').value.trim();
      payload.sort_order = parseInt($('#qcf-sort').value) || 0;
      if (!payload.category_id || !payload.name) { hint.textContent = '标识与名称必填'; hint.className = 'hint err'; return; }
      await admin('/admin/questions/categories', { method: 'POST', body: JSON.stringify(payload) });
      toast('分类已新增', 'ok');
    }
    closeModal('qc-modal'); qcLoad();
  } catch (e) { hint.textContent = '保存失败：' + e.message; hint.className = 'hint err'; }
}

async function qcToggleEnable(cid) {
  const n = qcBytid(cid);
  try {
    await admin('/admin/questions/categories/' + encodeURIComponent(cid) + '?teacher_id=' + encodeURIComponent(qcTid()), { method: 'PATCH', body: JSON.stringify({ enabled: !!(!(n && n.enabled)) }) });
    toast(n.enabled ? '已停用' : '已启用', 'ok'); qcLoad();
  } catch (e) { toast('操作失败：' + e.message, 'err'); }
}

function qcDelete(cid, name) {
  openModal('qc-modal', `
    <h3>删除分类</h3>
    <div class="err-tip" style="margin-bottom:12px">⚠️ 确认删除分类「${esc(name)}」？<br>若其下有子分类，需<b>级联删除</b>（连同子分类一并删除）。</div>
    <div class="field"><label><input type="checkbox" id="qcd-cascade"> 级联删除子分类</label></div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('qc-modal')">取消</button>
      <button class="btn danger" onclick="qcDoDelete('${esc(cid)}')">确认删除</button>
    </div>
    <p class="hint" id="qcd-hint"></p>
  `);
}
async function qcDoDelete(cid) {
  const hint = $('#qcd-hint');
  try {
    const r = await admin('/admin/questions/categories/' + encodeURIComponent(cid) + '?teacher_id=' + encodeURIComponent(qcTid()) + '&cascade=' + ($('#qcd-cascade').checked ? 'true' : 'false'), { method: 'DELETE' });
    toast(`已删除 ${r.deleted} 个分类`, 'ok'); closeModal('qc-modal'); qcLoad();
  } catch (e) { hint.textContent = '删除失败：' + e.message; hint.className = 'hint err'; }
}

// #24 R2：切换老师 → 分类与知识点下拉随老师刷新，再拉题目
async function qbSwitchTeacher() {
  await loadCatOptions();
  loadQuestions();
  loadQbStats();
  loadKpOptions();
}

async function loadQbStats() {
  try {
    const tid = ($('#qb-teacher') && $('#qb-teacher').value) || 'T001';
    const s = await admin('/admin/questions/stats?teacher_id=' + encodeURIComponent(tid));
    const typeMap = { choice: '选择题', judge: '判断题', essay: '简答题' };
    const byType = (s.by_type || []).map(x => `${typeMap[x.qtype] || x.qtype} ${x.n}`).join(' · ');
    const byDiff = (s.by_difficulty || []).map(x => `${'★'.repeat(x.difficulty)} ${x.n}`).join(' · ');
    const kpCount = (s.by_knowledge_point || []).length;
    const byCat = (s.by_category || []).filter(x => x.category);
    const uncat = (s.by_category || []).find(x => !x.category);
    const catTxt = byCat.map(x => `${x.category} ${x.n}`).join(' · ') + (uncat && byCat.length ? ` · 未分类 ${uncat.n}` : '');
    $('#qb-stats-cards').innerHTML = `
      <div class="card"><div class="card-label">题目总数</div><div class="card-n">${s.total}</div><div class="card-sub">${byType || '暂无'}</div></div>
      <div class="card"><div class="card-label">难度分布</div><div class="card-n gold">${(s.by_difficulty || []).length}</div><div class="card-sub">${byDiff || '暂无'}</div></div>
      <div class="card"><div class="card-label">知识点数</div><div class="card-n green">${kpCount}</div><div class="card-sub">AI 采集自动归类</div></div>
      <div class="card" style="grid-column:span 2"><div class="card-label">分类分布</div><div class="card-n">${byCat.length}</div><div class="card-sub">${catTxt || (s.total ? `全部未分类（${uncat ? uncat.n : s.total} 题，可点「AI 补充分类」）` : '暂无')}</div></div>`;
  } catch (e) { $('#qb-stats-cards').innerHTML = ''; }
}

async function loadKpOptions() {
  const tid = ($('#qb-teacher') && $('#qb-teacher').value) || 'T001';
  try {
    const r = await admin(`/admin/questions/knowledge-points?teacher_id=${encodeURIComponent(tid)}`);
    const sel = $('#qb-kp');
    const cur = qbPage.kp;
    sel.innerHTML = '<option value="">全部知识点</option>' + (r.points || []).map(p => `<option value="${esc(p)}" ${p === cur ? 'selected' : ''}>${esc(p)}</option>`).join('');
  } catch (e) {}
}

// #24 R2：加载题型分类下拉（路径形式展示：行测/判断推理/图形推理）
async function loadCatOptions() {
  const tid = ($('#qb-teacher') && $('#qb-teacher').value) || 'T001';
  try {
    const r = await admin(`/admin/knowledge/paths?teacher_id=${encodeURIComponent(tid)}&tree_type=qtype`);
    _qbCatPaths = r.paths || {};
    const entries = Object.entries(_qbCatPaths).sort((a, b) => a[1].localeCompare(b[1], 'zh'));
    _qbCatOptions = entries.map(([id, path]) =>
      `<option value="${esc(id)}">${esc(path.length > 30 ? '…' + path.slice(-29) : path)}</option>`).join('');
    const sel = $('#qb-cat');
    if (sel) {
      const cur = sel.value;
      sel.innerHTML = '<option value="">全部题型分类</option><option value="__uncat__">（未关联分类）</option>' + _qbCatOptions;
      if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
    }
  } catch (e) { /* 题型树不可用时静默降级（无分类筛选） */ }
}

// ---------- 论坛治理（F3 运营面板） ----------
const FM = {
  status: '', kw: '', page: 1, size: 20, total: 0,
  repPage: 1, repSize: 50, repTotal: 0, notice: '',
};

async function renderForumMod() {
  try {
    const n = await admin('/public/forum/notice');
    FM.notice = (n && n.content) || '';
  } catch (e) { FM.notice = ''; }
  const c = $('#content');
  c.innerHTML = `
  <style>
    .fm-seg{display:inline-flex;border:1px solid var(--line-2);border-radius:9px;overflow:hidden}
    .fm-seg button{border:none;background:var(--card);color:var(--ink-2);padding:6px 12px;font-size:12px;font-weight:600;cursor:pointer;font-family:inherit}
    .fm-seg button+button{border-left:1px solid var(--line-2)}
    .fm-seg button.on{background:var(--wood);color:#fffaf0}
    .fm-tag{display:inline-block;padding:1px 9px;border-radius:999px;font-size:11px;font-weight:700}
    .fm-tag.ok{background:rgba(122,158,109,.16);color:#5c7a50}
    .fm-tag.warn{background:rgba(201,160,90,.22);color:#8a6a2f}
    .fm-tag.bad{background:rgba(180,90,74,.16);color:#b45a4a}
  </style>
  <div class="panel"><div class="panel-body">
    <div class="toolbar">
      <b>📢 论坛公告</b>
      <input id="fm-notice" style="flex:1;min-width:220px" maxlength="300" placeholder="展示在学员论坛顶部的公告（留空则不展示）" value="${esc(FM.notice)}">
      <button class="btn primary sm" onclick="fmSaveNotice()">保存公告</button>
    </div>
  </div></div>
  <div style="height:12px"></div>
  <div class="panel">
    <div class="panel-head"><h3>📩 待处理举报（<span id="fm-rep-count">0</span>）</h3>
      <div class="toolbar"><button class="btn ghost sm" onclick="fmRepGo(1)">⟳ 刷新</button></div></div>
    <div id="fm-rep-body"><div class="empty">加载中…</div></div>
  </div>
  <div style="height:12px"></div>
  <div class="panel">
    <div class="panel-head">
      <h3>📝 帖子管理</h3>
      <div class="toolbar" id="fm-post-tools">
        <div class="fm-seg">
          <button data-v="" class="on" onclick="fmSetStatus('')">全部</button>
          <button data-v="open" onclick="fmSetStatus('open')">正常</button>
          <button data-v="hidden" onclick="fmSetStatus('hidden')">已隐藏</button>
        </div>
        <input id="fm-kw" style="width:200px" placeholder="标题 / 作者" value="${esc(FM.kw)}">
        <button class="btn ghost sm" onclick="fmSearch()">搜索</button>
      </div>
    </div>
    <div id="fm-post-body"><div class="empty">加载中…</div></div>
  </div>`;
  await Promise.all([fmRepGo(1), fmPostGo(1)]);
}

function fmSetStatus(v) {
  FM.status = v;
  document.querySelectorAll('#fm-post-tools .fm-seg button')
    .forEach(b => b.classList.toggle('on', b.dataset.v === v));
  fmPostGo(1);
}

function fmSearch() {
  FM.kw = ($('#fm-kw') && $('#fm-kw').value || '').trim();
  fmPostGo(1);
}

async function fmPostGo(page) {
  FM.page = Math.max(1, page || 1);
  const body = $('#fm-post-body');
  if (!body) return;
  body.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const q = new URLSearchParams({
      status: FM.status, keyword: FM.kw,
      page: String(FM.page), size: String(FM.size),
    });
    const d = await admin('/forum/mod/posts?' + q.toString());
    FM.total = d.total || 0;
    const items = d.items || [];
    if (!items.length) {
      body.innerHTML = '<div class="empty">暂无帖子</div>';
      return;
    }
    const rows = items.map(p => {
      const st = p.status === 'hidden'
        ? '<span class="fm-tag bad">已隐藏</span>' : '<span class="fm-tag ok">正常</span>';
      const pinCls = p.pinned ? '取消置顶' : '置顶';
      const pinVal = p.pinned ? 0 : 1;
      const stBtn = p.status === 'hidden' ? '恢复' : '隐藏';
      const stVal = p.status === 'hidden' ? 'open' : 'hidden';
      return '<tr>' +
        '<td style="max-width:260px"><div style="font-weight:600;word-break:break-word">' + esc(p.title) + '</div></td>' +
        '<td>' + esc(p.username) + '</td><td>' + esc(p.category) + '</td>' +
        '<td>' + st + '</td><td>' + (p.pinned ? '📌 是' : '—') + '</td>' +
        '<td>' + p.views + ' / ' + p.reply_count + ' / ' + p.likes + '</td>' +
        '<td>' + (p.report_count || 0) + '</td>' +
        '<td>' + fmtTime(p.created_at) + '</td>' +
        '<td><div class="ops">' +
        '<button class="btn ghost sm" onclick="fmPin(\'' + p.post_id + '\',' + pinVal + ')">' + pinCls + '</button>' +
        '<button class="btn ghost sm" onclick="fmState(\'' + p.post_id + '\',\'' + stVal + '\')">' + stBtn + '</button>' +
        '<button class="btn danger sm" onclick="fmDel(\'' + p.post_id + '\')">删除</button>' +
        '</div></td></tr>';
    }).join('');
    const last = FM.page * FM.size >= FM.total;
    body.innerHTML = '<div class="tbl-wrap"><table class="tbl"><thead><tr>' +
      '<th>标题</th><th>作者</th><th>分类</th><th>状态</th><th>置顶</th><th>览/评/赞</th>' +
      '<th>举报</th><th>时间</th><th>操作</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
      '<div class="pager"><span class="pg-info">共 ' + FM.total + ' 条 · 第 ' + FM.page + ' 页</span>' +
      '<button class="btn ghost sm" onclick="fmPostGo(' + (FM.page - 1) + ')"' + (FM.page <= 1 ? ' disabled' : '') + '>上一页</button>' +
      '<button class="btn ghost sm" onclick="fmPostGo(' + (FM.page + 1) + ')"' + (last ? ' disabled' : '') + '>下一页</button></div>';
  } catch (e) {
    body.innerHTML = '<div class="err-tip">加载失败：' + esc(e.message) + '</div>';
  }
}

async function fmRepGo(page) {
  FM.repPage = Math.max(1, page || 1);
  const body = $('#fm-rep-body');
  if (!body) return;
  body.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const q = new URLSearchParams({ status: 'pending', page: String(FM.repPage), size: String(FM.repSize) });
    const d = await admin('/forum/mod/reports?' + q.toString());
    FM.repTotal = d.total || 0;
    const cnt = $('#fm-rep-count');
    if (cnt) cnt.textContent = FM.repTotal;
    const items = d.items || [];
    if (!items.length) {
      body.innerHTML = '<div class="empty">暂无待处理举报 🎉</div>';
      return;
    }
    const rows = items.map(r => {
      const target = r.target_kind === 'post'
        ? '帖「' + esc(r.post_title || '已删除') + '」（' + esc(r.post_username || '') + '）'
        : '回复 @' + esc(r.reply_username || '') + '：' + esc((r.reply_snippet || '').slice(0, 60));
      const postBtns = r.target_kind === 'post'
        ? '<button class="btn ghost sm" onclick="fmState(\'' + r.target_id + '\',\'hidden\')">隐藏帖子</button>'
        : '';
      return '<tr>' +
        '<td style="max-width:240px">' + target + '</td>' +
        '<td style="max-width:200px;color:var(--ink-3)">' + esc(r.reason) + '</td>' +
        '<td>' + esc(r.user_id) + '</td><td>' + fmtTime(r.created_at) + '</td>' +
        '<td><div class="ops">' + postBtns +
        '<button class="btn ok sm" onclick="fmResolve(\'' + r.target_kind + '\',\'' + r.target_id + '\')">标记处理</button>' +
        '</div></td></tr>';
    }).join('');
    body.innerHTML = '<div class="tbl-wrap"><table class="tbl"><thead><tr>' +
      '<th>举报对象</th><th>原因</th><th>举报人</th><th>时间</th><th>处理</th></tr></thead><tbody>' +
      rows + '</tbody></table></div>';
  } catch (e) {
    body.innerHTML = '<div class="err-tip">加载失败：' + esc(e.message) + '</div>';
  }
}

async function fmResolve(kind, id) {
  try {
    await admin('/forum/mod/reports/' + encodeURIComponent(kind) + '/' + encodeURIComponent(id) + '/resolve', { method: 'POST' });
    toast('已标记处理', 'success');
    fmRepGo(FM.repPage);
    if (kind === 'post') fmPostGo(FM.page);
  } catch (e) { toast('操作失败：' + e.message, 'error'); }
}

async function fmPin(id, v) {
  try {
    await admin('/forum/mod/posts/' + id + '/pin', { method: 'POST', body: { pinned: !!v } });
    toast(v ? '已置顶' : '已取消置顶', 'success');
    fmPostGo(FM.page);
  } catch (e) { toast('操作失败：' + e.message, 'error'); }
}

async function fmState(id, st) {
  try {
    await admin('/forum/mod/posts/' + id + '/state', { method: 'POST', body: { status: st } });
    toast(st === 'hidden' ? '已隐藏，前台不可见' : '已恢复展示', 'success');
    fmPostGo(FM.page);
  } catch (e) { toast('操作失败：' + e.message, 'error'); }
}

async function fmDel(id) {
  if (!window.confirm('确认删除该帖及其全部回复？不可恢复。')) return;
  try {
    await admin('/forum/posts/' + id, { method: 'DELETE' });
    toast('已删除', 'success');
    fmPostGo(FM.page);
  } catch (e) { toast('删除失败：' + e.message, 'error'); }
}

async function fmSaveNotice() {
  const v = ($('#fm-notice') && $('#fm-notice').value || '').trim();
  try {
    await admin('/forum/mod/notice', { method: 'POST', body: { content: v } });
    FM.notice = v;
    toast('公告已保存', 'success');
  } catch (e) { toast('保存失败：' + e.message, 'error'); }
}

// ---------- 运营统计（宣传入口点击，服务端 tracking.db） ----------
async function renderTracking() {
  const c = $('#content');
  c.innerHTML = '<div class="panel"><div class="panel-body"><div class="empty">加载中…</div></div></div>';
  let d;
  try {
    d = await admin('/admin/tracking/promo');
  } catch (e) {
    c.innerHTML = '<div class="panel"><div class="panel-body"><div class="err-tip">加载失败：' + esc(e.message) + '</div></div></div>';
    return;
  }
  const labels = { mock: '📝 智能模考', mind: '🧠 知识导图', forum: '💬 学员论坛', articles: '📄 备考文章' };
  const by = {};
  (d.by_target || []).forEach(x => { by[x.target] = x; });
  const rows = ['mock', 'mind', 'forum', 'articles'].map(t => {
    const it = by[t] || { total: 0, last7: 0 };
    return '<tr><td>' + (labels[t] || t) + '</td><td>' + it.total + '</td><td>' + it.last7 + '</td></tr>';
  }).join('');
  const trend = d.trend || [];
  const max = Math.max(1, ...trend.map(x => x.count));
  const bars = trend.map(x =>
    '<div style="display:flex;align-items:center;gap:8px;margin:3px 0">' +
    '<span style="width:88px;flex-shrink:0;font-size:11.5px;color:var(--ink-3)">' + x.day.slice(5) + '</span>' +
    '<div style="flex:1;background:var(--line-2);border-radius:5px;height:12px;overflow:hidden">' +
    '<div style="width:' + Math.max(2, Math.round(x.count / max * 100)) + '%;height:100%;' +
    'background:linear-gradient(90deg,var(--wood),var(--gold));border-radius:5px"></div></div>' +
    '<span style="width:26px;text-align:right;font-size:11.5px">' + x.count + '</span></div>').join('');
  c.innerHTML =
    '<div class="panel"><div class="panel-body">' +
    '<p style="margin:0 0 12px;color:var(--ink-2);font-size:13px">首页「备考工具箱」宣传卡点击（服务端 tracking.db，匿名低频上报）。</p>' +
    '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>宣传入口</th><th>累计点击</th><th>近 7 天</th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table></div></div></div>' +
    '<div style="height:12px"></div>' +
    '<div class="panel"><div class="panel-head"><h3>近 14 天点击趋势（合计 ' + (d.total || 0) + '）</h3></div>' +
    '<div class="panel-body">' + (bars || '<div class="empty">暂无数据，去首页点一次宣传卡试试</div>') + '</div></div>';
}

// ============================================================
// #23 轮播管理（首页 Banner）
// ============================================================
let _banList = [];

async function renderBanners() {
  const c = $('#content');
  c.innerHTML = `
  <div class="page-head"><h2>轮播管理</h2><p>首页顶部轮播 Banner · 按排序值升序展示 · 停用项前台即时隐藏 · 图片可为空（自动渐变底）</p></div>
  <div class="panel">
    <div class="panel-head">
      <div class="toolbar" style="flex:1"><b>Banner 列表</b><span id="ban-count" style="font-size:11.5px;color:var(--ink-3)"></span></div>
      <button class="btn primary sm" onclick="banCreate()">➕ 新增 Banner</button>
    </div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr>
          <th style="width:44px">ID</th><th>标题</th><th>图片</th><th>跳转链接</th>
          <th style="width:52px">排序</th><th style="width:60px">状态</th>
          <th style="width:130px">创建时间</th><th style="width:228px">操作</th>
        </tr></thead>
        <tbody id="ban-body"><tr><td colspan="8" class="loading">加载中…</td></tr></tbody>
      </table>
    </div>
  </div>
  <div id="ban-modal" class="modal-mask"></div>`;
  let list;
  try {
    list = await admin('/admin/banners');
  } catch (e) {
    $('#ban-body').innerHTML = '<tr><td colspan="8" class="err-tip">加载失败：' + esc(e.message) + '</td></tr>';
    return;
  }
  _banList = (list && list.banners) || [];
  $('#ban-count').textContent = '共 ' + _banList.length + ' 条';
  const body = $('#ban-body');
  if (!_banList.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty">暂无 Banner，点击右上角「新增 Banner」发布第一条</td></tr>';
    return;
  }
  body.innerHTML = _banList.map(b => {
    const st = b.enabled ? '<span style="color:var(--good);font-weight:700">启用</span>' : '<span style="color:var(--ink-3)">停用</span>';
    const img = b.image
      ? '<a href="' + esc(b.image) + '" target="_blank" rel="noopener">查看</a>'
      : '<span style="color:var(--ink-3);font-size:11.5px">无图</span>';
    const ts = (b.created_at || '').replace('T', ' ').slice(0, 16);
    return '<tr>' +
      '<td>' + b.id + '</td>' +
      '<td style="font-weight:600">' + esc(b.title || '—') + '</td>' +
      '<td>' + img + '</td>' +
      '<td style="max-width:190px;word-break:break-all">' + esc(b.link || '—') + '</td>' +
      '<td>' + b.sort + '</td>' +
      '<td>' + st + '</td>' +
      '<td style="color:var(--ink-3);font-size:11.5px">' + esc(ts) + '</td>' +
      '<td style="white-space:nowrap">' +
        '<button class="btn ghost sm" onclick="banMove(' + b.id + ',-1)" title="上移">↑</button> ' +
        '<button class="btn ghost sm" onclick="banMove(' + b.id + ',1)" title="下移">↓</button> ' +
        '<button class="btn ghost sm" onclick="banEdit(' + b.id + ')">✏️ 编辑</button> ' +
        '<button class="btn ghost sm" onclick="banToggle(' + b.id + ')">' + (b.enabled ? '⏸ 停用' : '▶ 启用') + '</button> ' +
        '<button class="btn danger sm" onclick="banDel(' + b.id + ')" title="删除">🗑</button>' +
      '</td></tr>';
  }).join('');
}

function banFormHtml(b) {
  b = b || {};
  return '<div class="modal-box">' +
    '<h3>' + (b.id ? '编辑 Banner #' + b.id : '新增 Banner') + '</h3>' +
    '<input type="hidden" id="ban-id" value="' + (b.id || '') + '">' +
    '<div class="field"><label>标题 *</label><input id="ban-title" value="' + esc(b.title || '') + '" placeholder="如：2026 省考冲刺班 · 名师领学"></div>' +
    '<div class="field"><label>图片 URL（可空，留空自动渐变底）</label><input id="ban-image" value="' + esc(b.image || '') + '" placeholder="https://… 或 /…"></div>' +
    '<div class="field"><label>跳转链接（可空）</label><input id="ban-link" value="' + esc(b.link || '') + '" placeholder="https://… 或 /chat.html?…"></div>' +
    '<div class="field"><label>排序（数字越小越靠前）</label><input id="ban-sort" type="number" value="' + (b.sort != null ? b.sort : 0) + '"></div>' +
    '<div class="field"><label style="display:flex;align-items:center;gap:8px;cursor:pointer">' +
    '<input id="ban-enabled" type="checkbox" style="width:auto" ' + (b.enabled === 0 ? '' : 'checked') + '> 启用展示</label></div>' +
    '<div class="modal-actions">' +
    '<button class="btn ghost sm" onclick="closeModal(\'ban-modal\')">取消</button>' +
    '<button class="btn primary sm" onclick="banSave()">保存</button>' +
    '</div></div>';
}

function banCreate() {
  openModal('ban-modal', banFormHtml());
  const t = $('#ban-title');
  if (t) t.focus();
}

function banEdit(id) {
  const b = _banList.find(x => x.id === id);
  if (!b) return;
  openModal('ban-modal', banFormHtml(b));
}

async function banSave() {
  const title = ($('#ban-title').value || '').trim();
  const image = ($('#ban-image').value || '').trim();
  const link = ($('#ban-link').value || '').trim();
  const sort = parseInt($('#ban-sort').value || '0', 10);
  const enabled = $('#ban-enabled').checked ? 1 : 0;
  const editing = $('#ban-id').value;
  if (!title) { toast('请填写标题', 'err'); return; }
  const payload = { title, image, link, sort, enabled };
  try {
    if (editing) {
      await admin('/admin/banners/' + editing, { method: 'PUT', body: JSON.stringify(payload) });
      toast('Banner 已更新', 'ok');
    } else {
      await admin('/admin/banners', { method: 'POST', body: JSON.stringify(payload) });
      toast('Banner 已创建', 'ok');
    }
    closeModal('ban-modal');
    renderBanners();
  } catch (e) { toast('保存失败：' + e.message, 'err'); }
}

async function banToggle(id) {
  const b = _banList.find(x => x.id === id);
  if (!b) return;
  try {
    await admin('/admin/banners/' + id, { method: 'PUT', body: JSON.stringify({ enabled: b.enabled ? 0 : 1 }) });
    toast(b.enabled ? '已停用' : '已启用', 'ok');
    renderBanners();
  } catch (e) { toast('操作失败：' + e.message, 'err'); }
}

async function banDel(id) {
  if (!confirm('确认删除 Banner #' + id + '？')) return;
  try {
    await admin('/admin/banners/' + id, { method: 'DELETE' });
    toast('已删除', 'ok');
    renderBanners();
  } catch (e) { toast('删除失败：' + e.message, 'err'); }
}

function banMove(id, dir) {
  const idx = _banList.findIndex(x => x.id === id);
  const j = idx + dir;
  if (idx < 0 || j < 0 || j >= _banList.length) return;
  const ids = _banList.map(x => x.id);
  [ids[idx], ids[j]] = [ids[j], ids[idx]];
  banReorder(ids);
}

async function banReorder(ids) {
  try {
    await admin('/admin/banners/reorder', { method: 'POST', body: JSON.stringify({ ids }) });
    toast('排序已更新', 'ok');
    renderBanners();
  } catch (e) { toast('排序失败：' + e.message, 'err'); }
}

// ============================================================
// #19 消息中心（站内信 / 系统通知）
// ============================================================
let _msgList = [];

async function renderMessages() {
  const c = $('#content');
  c.innerHTML = `
  <div class="page-head"><h2>消息中心</h2><p>站内信 / 系统通知 · 群发全体用户或定向单个用户 · 用户端右上角铃铛查看与已读</p></div>
  <div class="panel">
    <div class="panel-head">
      <div class="toolbar" style="flex:1"><b>消息列表</b><span id="msg-count" style="font-size:11.5px;color:var(--ink-3)"></span></div>
      <button class="btn primary sm" onclick="msgCreate()">➕ 发送消息</button>
    </div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr>
          <th style="width:44px">ID</th><th>标题</th><th>内容</th><th>范围</th>
          <th style="width:80px">送达</th><th style="width:96px">已读</th>
          <th style="width:130px">发送时间</th><th style="width:64px">操作</th>
        </tr></thead>
        <tbody id="msg-body"><tr><td colspan="8" class="loading">加载中…</td></tr></tbody>
      </table>
    </div>
  </div>
  <div id="msg-modal" class="modal-mask"></div>`;
  let list;
  try {
    list = await admin('/admin/messages');
  } catch (e) {
    $('#msg-body').innerHTML = '<tr><td colspan="8" class="err-tip">加载失败：' + esc(e.message) + '</td></tr>';
    return;
  }
  _msgList = (list && list.messages) || [];
  $('#msg-count').textContent = '共 ' + _msgList.length + ' 条';
  const body = $('#msg-body');
  if (!_msgList.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty">暂无消息，点击右上角「发送消息」群发通知</td></tr>';
    return;
  }
  body.innerHTML = _msgList.map(m => {
    const scope = m.target === 'user'
      ? '<span style="color:#d97706">定向 · ' + esc(m.target_user_id || '') + '</span>'
      : '<span style="color:var(--ink-3)">全体用户</span>';
    const ts = (m.created_at || '').replace('T', ' ').slice(0, 16);
    const rate = m.delivered ? '（' + Math.round(m.read / m.delivered * 100) + '%）' : '';
    return '<tr>' +
      '<td>' + m.id + '</td>' +
      '<td style="font-weight:600;max-width:180px">' + esc(m.title || '—') + '</td>' +
      '<td style="max-width:280px;color:var(--ink-2)">' + esc(m.content || '—') + '</td>' +
      '<td>' + scope + '</td>' +
      '<td>' + m.delivered + '</td>' +
      '<td>' + m.read + rate + '</td>' +
      '<td style="color:var(--ink-3);font-size:11.5px">' + esc(ts) + '</td>' +
      '<td style="white-space:nowrap">' +
        '<button class="btn danger sm" onclick="msgDel(' + m.id + ')" title="删除">🗑</button>' +
      '</td></tr>';
  }).join('');
}

function msgFormHtml() {
  return '<div class="modal-box">' +
    '<h3>发送站内消息</h3>' +
    '<div class="field"><label>发送范围 *</label>' +
    '<select id="msg-target"><option value="all" selected>全体用户</option><option value="user">定向用户</option></select></div>' +
    '<div class="field" id="msg-user-field" style="display:none"><label>目标用户 ID *</label><input id="msg-user" placeholder="如：u-a，需与用户 ID 完全一致"></div>' +
    '<div class="field"><label>标题 *</label><input id="msg-title" placeholder="如：系统升级通知"></div>' +
    '<div class="field"><label>内容 *</label><textarea id="msg-content" rows="4" placeholder="通知正文…"></textarea></div>' +
    '<div class="modal-actions">' +
    '<button class="btn ghost sm" onclick="closeModal(\'msg-modal\')">取消</button>' +
    '<button class="btn primary sm" onclick="msgSave()">发送</button>' +
    '</div></div>';
}

function msgCreate() {
  openModal('msg-modal', msgFormHtml());
  const sel = $('#msg-target');
  if (sel) sel.onchange = () => { $('#msg-user-field').style.display = sel.value === 'user' ? '' : 'none'; };
  const t = $('#msg-title');
  if (t) t.focus();
}

async function msgSave() {
  const target = $('#msg-target').value;
  const user_id = ($('#msg-user').value || '').trim();
  const title = ($('#msg-title').value || '').trim();
  const content = ($('#msg-content').value || '').trim();
  if (!title) { toast('请填写标题', 'err'); return; }
  if (!content) { toast('请填写内容', 'err'); return; }
  if (target === 'user' && !user_id) { toast('定向发送需填写目标用户 ID', 'err'); return; }
  const payload = { title, content, target, user_id };
  try {
    await admin('/admin/messages', { method: 'POST', body: JSON.stringify(payload) });
    toast('消息已发送', 'ok');
    closeModal('msg-modal');
    renderMessages();
  } catch (e) { toast('发送失败：' + e.message, 'err'); }
}

async function msgDel(id) {
  if (!confirm('确认删除消息 #' + id + '？已读记录将一并清除。')) return;
  try {
    await admin('/admin/messages/' + id, { method: 'DELETE' });
    toast('已删除', 'ok');
    renderMessages();
  } catch (e) { toast('删除失败：' + e.message, 'err'); }
}

async function loadQuestions() {
  const tbody = $('#qb-body');
  if (!tbody) return;
  const tid = ($('#qb-teacher') && $('#qb-teacher').value) || 'T001';
  qbPage.qtype = ($('#qb-qtype') && $('#qb-qtype').value) || '';
  qbPage.difficulty = ($('#qb-difficulty') && $('#qb-difficulty').value) || '';
  qbPage.kp = ($('#qb-kp') && $('#qb-kp').value) || '';
  qbPage.keyword = ($('#qb-kw') && $('#qb-kw').value || '').trim();
  const cat = ($('#qb-cat') && $('#qb-cat').value) || '';
  const category = ($('#qb-category') && $('#qb-category').value) || '';
  const source = ($('#qb-source') && $('#qb-source').value) || '';
  tbody.innerHTML = '<tr><td colspan="11" class="loading">加载中…</td></tr>';
  try {
    const params = new URLSearchParams({ teacher_id: tid });
    if (qbPage.qtype) params.set('qtype', qbPage.qtype);
    if (qbPage.difficulty) params.set('difficulty', qbPage.difficulty);
    if (qbPage.kp) params.set('knowledge_point', qbPage.kp);
    if (qbPage.keyword) params.set('keyword', qbPage.keyword);
    if (cat === '__uncat__') params.set('uncategorized', 'true');
    else if (cat) params.set('category_id', cat);
    if (category) params.set('category', category);
    if (source) params.set('source_type', source);
    params.set('limit', '200');
    const r = await admin(`/admin/questions?${params.toString()}`);
    const qs = r.questions || [];
    qbPage.total = qs.length;
    const totalPages = Math.max(1, Math.ceil(qs.length / qbPage.size));
    if (qbPage.cur > totalPages) qbPage.cur = totalPages;
    const start = (qbPage.cur - 1) * qbPage.size;
    const page = qs.slice(start, start + qbPage.size);
    if (!page.length) {
      tbody.innerHTML = '<tr><td colspan="11" class="empty">' + (qbPage.keyword ? '无匹配题目' : '题库为空，可用「AI 采集」批量导入') + '</td></tr>';
    } else {
      const typeMap = { choice: '选择', judge: '判断', essay: '简答' };
      const typeBadge = { choice: 'blue', judge: 'purple', essay: 'green' };
      tbody.innerHTML = page.map(q => {
        const catPath = _qbCatPaths[q.category_id] || '';
        const catName = q.category || '';
        return `
        <tr>
          <td><input type="checkbox" data-qid="${q.id}" ${_qbChecked.has(q.id) ? 'checked' : ''} onchange="qbCheck(${q.id}, this.checked)" aria-label="选择题目"></td>
          <td>${q.id}</td>
          <td><span class="badge ${typeBadge[q.qtype] || 'gray'}">${typeMap[q.qtype] || q.qtype}</span></td>
          <td style="max-width:280px" title="${esc(qTextPlain(q.question))}">${esc(qTextPlain(q.question).slice(0, 48))}${qTextPlain(q.question).length > 48 ? '…' : ''}${(q.images && q.images.length) ? ` <span class="badge gold" title="题干含配图">图×${q.images.length}</span>` : ''}</td>
          <td><code>${esc((q.answer || '').slice(0, 20))}</code></td>
          <td>${catName ? `<span class="badge ${QB_CAT_BADGE_COLOR[catName] || 'gray'}">${esc(catName)}</span>` : '<span class="badge gray">未分类</span>'}</td>
          <td>${q.knowledge_point ? `<span class="badge blue">${esc(q.knowledge_point)}</span>` : '<span class="badge gray">—</span>'}</td>
          <td>${catPath ? `<span class="badge purple" title="${esc(catPath)}">${esc(catPath.length > 18 ? '…' + catPath.slice(-17) : catPath)}</span>` : '<span class="badge gray">—</span>'}</td>
          <td>${q.source_type ? `<span class="badge ${QB_SOURCE_BADGE_COLOR[q.source_type] || 'gray'}" title="${esc(q.source || '')}">${esc(q.source_type)}${q.source ? ` · ${esc(q.source)}` : ''}</span>` : '<span class="badge gray">—</span>'}</td>
          <td>${'★'.repeat(q.difficulty || 1)}</td>
          <td><div class="ops">
            <button class="btn sm ghost" onclick="showQuestionDetail(${q.id})">查看</button>
            <button class="btn sm ghost" onclick="showQuestionEdit(${q.id})">编辑</button>
            <button class="btn sm ghost" onclick="copyQuestion(${q.id})">复制</button>
            <button class="btn sm danger" onclick="deleteQuestion(${q.id})">删除</button>
          </div></td>
        </tr>`;
      }).join('');
    }
    renderPager($('#qb-pager'), qbPage.cur, totalPages, (p) => { qbPage.cur = p; loadQuestions(); }, qbPage.total);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="11" class="err-tip">加载失败：${esc(e.message)}</td></tr>`;
  }
}

// 查看题目详情
// #35 修复：改用单题端点（原实现拉 /admin/questions?limit=500 不带 teacher_id，
// 后端默认 T001 —— 切换其他老师后查看详情必报“题目不存在”）。
function showQuestionDetail(qid) {
  openModal('qb-modal', `<h3>题目详情 #${qid}</h3><div class="loading">加载中…</div>`);
  admin(`/admin/questions/${qid}`).then(q => {
    if (!q || !q.id) throw new Error('题目不存在');
    const typeMap = { choice: '选择题', judge: '判断题', essay: '简答题' };
    const opts = (q.options || []).map(o => qText(o, { max: 200 })).join('<br>');
    $('#qb-modal .modal-box').innerHTML = `
      <h3>题目详情 #${q.id}</h3>
      <div class="detail-row"><span class="k">题型</span><span class="v">${typeMap[q.qtype] || q.qtype}</span></div>
      <div class="detail-row"><span class="k">分类</span><span class="v">${q.category ? `<span class="badge ${QB_CAT_BADGE_COLOR[q.category] || 'gray'}">${esc(q.category)}</span>` : '<span class="badge gray">未分类</span>'}</span></div>
      <div class="detail-row"><span class="k">题目</span><span class="v" style="white-space:pre-wrap">${qText(q.question, { max: 320 })}</span></div>
      ${opts ? `<div class="detail-row"><span class="k">选项</span><span class="v">${opts}</span></div>` : ''}
      <div class="detail-row"><span class="k">答案</span><span class="v"><b style="color:var(--good)">${esc(q.answer)}</b></span></div>
      <div class="detail-row"><span class="k">解析</span><span class="v" id="qd-analysis" style="white-space:pre-wrap">${esc(q.analysis || '—')}</span></div>
      <div class="detail-row"><span class="k">知识点</span><span class="v">${esc(q.knowledge_point || '—')}</span></div>
      <div class="detail-row"><span class="k">难度</span><span class="v">${'★'.repeat(q.difficulty || 1)}</span></div>
      <div class="detail-row"><span class="k">来源</span><span class="v">${q.source_type ? `${esc(q.source_type)}${q.source ? ` · ${esc(q.source)}` : ''}` : '—'}</span></div>
      <p class="hint" id="qd-hint"></p>
      <div class="modal-actions">
        <button class="btn ghost sm" onclick="closeModal('qb-modal')">关闭</button>
        <button class="btn sm" style="background:var(--gold-soft);border-color:var(--gold)" id="qd-ai-btn" onclick="qbAiAnalysis(${q.id})">✨ AI 解析（多角度）</button>
      </div>`;
  }).catch(e => {
    $('#qb-modal .modal-box').innerHTML = `<h3>加载失败</h3><p class="err-tip">${esc(e.message)}</p>
      <div class="modal-actions"><button class="btn ghost sm" onclick="closeModal('qb-modal')">关闭</button></div>`;
  });
}

// #35 R3 单题生成多角度 AI 解析（覆盖原解析前确认）
async function qbAiAnalysis(qid) {
  const btn = $('#qd-ai-btn');
  const hint = $('#qd-hint');
  const box = $('#qd-analysis');
  const existing = (box && box.textContent || '').trim();
  if (existing && existing !== '—' &&
      !confirm('该题已有解析，AI 解析将覆盖原解析。\n是否继续？')) return;
  if (btn) { btn.disabled = true; btn.textContent = 'AI 解析生成中…'; }
  if (hint) { hint.textContent = '调用 AI 生成多角度解析（约 10~30 秒）…'; hint.className = 'hint'; }
  try {
    const r = await admin(`/admin/questions/${qid}/ai-analysis`, { method: 'POST', timeout: 180000 });
    if (box) box.textContent = r.analysis || '';
    if (hint) { hint.textContent = '✅ AI 解析已生成并保存'; hint.className = 'hint ok'; }
    toast('AI 解析已生成', 'ok');
  } catch (e) {
    if (hint) { hint.textContent = '生成失败：' + e.message; hint.className = 'hint err'; }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '✨ AI 解析（多角度）'; }
  }
}

// #35 R1 批量 AI 补充分类（未分类题目）
async function qbAiCategorize() {
  const tid = ($('#qb-teacher') && $('#qb-teacher').value) || 'T001';
  if (!confirm('将对当前老师的未分类题目批量调用 AI 判定课程分类（≤50 题/次）。\n是否继续？')) return;
  toast('AI 分类中（约 10~30 秒）…', 'info');
  try {
    const r = await admin('/admin/questions/ai-categorize', {
      method: 'POST', timeout: 180000,
      body: JSON.stringify({ teacher_id: tid, limit: 50 })
    });
    if (r.total === 0) { toast(r.message || '没有待分类的题目', 'info'); return; }
    toast(`AI 分类完成：成功 ${r.updated} 题${r.failed ? `，失败 ${r.failed} 题` : ''}`, r.failed ? 'info' : 'ok');
    loadQuestions(); loadQbStats();
  } catch (e) {
    toast('AI 分类失败：' + e.message, 'err');
  }
}

// #35 R3 批量 AI 解析（无解析题目）
async function qbAiAnalysisBatch() {
  const tid = ($('#qb-teacher') && $('#qb-teacher').value) || 'T001';
  if (!confirm('将对当前老师无解析的题目批量生成多角度 AI 解析（≤20 题/次，约 2~5 分钟）。\n是否继续？')) return;
  toast('批量 AI 解析中，请勿关闭页面…', 'info');
  try {
    const r = await admin('/admin/questions/ai-analysis-batch', {
      method: 'POST', timeout: 600000,
      body: JSON.stringify({ teacher_id: tid, limit: 20 })
    });
    if (r.total === 0) { toast(r.message || '没有待生成解析的题目', 'info'); return; }
    toast(`批量 AI 解析完成：生成 ${r.generated} 题${r.failed ? `，失败 ${r.failed} 题` : ''}`, r.failed ? 'info' : 'ok');
    loadQuestions();
  } catch (e) {
    toast('批量 AI 解析失败：' + e.message, 'err');
  }
}

// 手动录入题目
function showQuestionForm() {
  const tid = ($('#qb-teacher') && $('#qb-teacher').value) || 'T001';
  openModal('qb-modal', `
    <h3>手动录入题目</h3>
    <div class="form-grid">
      <div class="field"><label for="qf-type">题型</label><select id="qf-type" onchange="toggleQfOptions()">
        <option value="choice">选择题</option>
        <option value="judge">判断题</option>
        <option value="essay">简答题</option>
      </select></div>
      <div class="field"><label for="qf-difficulty">难度</label><select id="qf-difficulty">
        ${QB_DIFF_OPTIONS}
      </select></div>
      <div class="field"><label for="qf-source-type">来源类型</label>
        <select id="qf-source-type">${QB_SOURCE_TYPES.map(s => `<option value="${esc(s)}" ${s === '用户提供' ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select></div>
      <div class="field full"><label for="qf-source">来源出处（可选）</label><input id="qf-source" placeholder="如：2025年国考行测执法卷第25题"></div>
      <div class="field full"><label for="qf-cat">题型分类（关联题型树节点，可选）</label>
        <select id="qf-cat"><option value="">（不关联）</option>${_qbCatOptions}</select></div>
      <div class="field full"><label for="qf-category">课程分类 *</label>
        <select id="qf-category">${QB_CATEGORIES.map(c => `<option value="${esc(c)}" ${c === '综合' ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></div>
      <div class="field full"><label for="qf-question">题目 *</label><textarea id="qf-question" rows="3" onblur="checkQfDup('qf-question')"></textarea></div>
      <div class="field full" id="qf-dup-tip" style="display:none"></div>
      <div class="field full" id="qf-options-wrap"><label for="qf-options">选项（每行一个，如：A. 选项一）</label><textarea id="qf-options" rows="4" placeholder="A. 选项一&#10;B. 选项二"></textarea></div>
      <div class="field full"><label for="qf-answer">答案 *</label><input id="qf-answer" placeholder="选择题填 A/B/C；判断题填 对/错；简答填参考要点"></div>
      <div class="field full"><label for="qf-analysis">解析（可选）</label><textarea id="qf-analysis" rows="2"></textarea></div>
      <div class="field full"><label for="qf-kp">知识点（可选）</label><input id="qf-kp" placeholder="如：主旨概括"></div>
    </div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('qb-modal')">取消</button>
      <button class="btn primary" onclick="submitQuestion('${esc(tid)}')">保存</button>
    </div>
    <p class="hint" id="qf-hint"></p>
  `);
}
function toggleQfOptions() {
  const t = ($('#qf-type') && $('#qf-type').value);
  const w = $('#qf-options-wrap');
  if (w) w.style.display = (t === 'choice') ? '' : 'none';
}
async function submitQuestion(tid) {
  const qtype = $('#qf-type').value;
  const question = $('#qf-question').value.trim();
  const answer = $('#qf-answer').value.trim();
  const analysis = $('#qf-analysis').value.trim();
  const difficulty = parseInt($('#qf-difficulty').value) || 1;
  const knowledge_point = $('#qf-kp').value.trim();
  const category_id = ($('#qf-cat') && $('#qf-cat').value) || '';
  const category = ($('#qf-category') && $('#qf-category').value) || '';
  const source_type = ($('#qf-source-type') && $('#qf-source-type').value) || '用户提供';
  const source = ($('#qf-source') && $('#qf-source').value.trim()) || '';
  const options = (qtype === 'choice') ? $('#qf-options').value.split('\n').map(s => s.trim()).filter(Boolean) : [];
  const hint = $('#qf-hint');
  if (!question || !answer) { hint.textContent = '题目和答案必填'; hint.className = 'hint err'; return; }
  hint.textContent = '保存中…';
  try {
    await admin('/admin/questions', { method: 'POST', body: JSON.stringify({ teacher_id: tid, qtype, question, options, answer, analysis, difficulty, knowledge_point, category_id, category, source_type, source }) });
    toast('题目已录入', 'ok');
    closeModal('qb-modal');
    loadQuestions(); loadQbStats();
  } catch (e) {
    hint.textContent = '保存失败：' + e.message;
    hint.className = 'hint err';
  }
}
async function deleteQuestion(qid) {
  openModal('qb-modal', `
    <h3>删除题目</h3>
    <div class="err-tip" style="margin-bottom:12px">确认删除题目 #${qid}？不可恢复。</div>
    <div class="modal-actions"><button class="btn ghost" onclick="closeModal('qb-modal')">取消</button>
    <button class="btn danger" onclick="submitDeleteQuestion(${qid})">删除</button></div>`);
}

// #26 R2：题干查重（录入/编辑时失焦触发）。ownerId 为题干输入框 id，tipId 为提示容器 id。
async function checkQfDup(ownerId, tipId) {
  const src = $(ownerId), tip = $(tipId);
  if (!src) return;
  const q = src.value.trim();
  if (q.length < 4) { if (tip) tip.style.display = 'none'; return; }
  const tid = ($('#qb-teacher') && $('#qb-teacher').value) || 'T001';
  try {
    const r = await admin(`/admin/questions/dup?teacher_id=${encodeURIComponent(tid)}&text=${encodeURIComponent(q)}`);
    const n = r.count || 0;
    if (tip) {
      if (n) {
        const ex = (r.duplicates || []).map(d => `#${d.id}`).join('、');
        tip.innerHTML = `<span class="badge warn">⚠ 库中已有 ${n} 道相同/相似题干</span><code class="hint" style="margin-left:6px">${esc(ex)}</code>`;
        tip.style.display = '';
      } else { tip.style.display = 'none'; }
    }
  } catch (e) { /* 查重失败静默，不影响录入 */ }
}

// #26 R2：编辑题目（预填 → 保存 PUT）
async function showQuestionEdit(qid) {
  openModal('qb-modal', `<h3>编辑题目 #${qid}</h3><div class="loading">加载中…</div>`);
  try {
    const q = await admin(`/admin/questions/${qid}`);
    openModal('qb-modal', `
      <h3>编辑题目 #${q.id}</h3>
      <div class="form-grid">
        <div class="field"><label for="qe-type">题型</label><select id="qe-type" onchange="toggleQeOptions()">
          <option value="choice" ${q.qtype === 'choice' ? 'selected' : ''}>选择题</option>
          <option value="judge" ${q.qtype === 'judge' ? 'selected' : ''}>判断题</option>
          <option value="essay" ${q.qtype === 'essay' ? 'selected' : ''}>简答题</option>
        </select></div>
        <div class="field"><label for="qe-difficulty">难度</label><select id="qe-difficulty">
          ${[1, 2, 3, 4, 5].map(d => `<option value="${d}" ${q.difficulty === d ? 'selected' : ''}>${'★'.repeat(d)}</option>`).join('')}
        </select></div>
        <div class="field"><label for="qe-source-type">来源类型</label>
          <select id="qe-source-type"><option value="">（无）</option>${QB_SOURCE_TYPES.map(s => `<option value="${esc(s)}" ${(q.source_type || '') === s ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select></div>
        <div class="field full"><label for="qe-source">来源出处</label><input id="qe-source" value="${esc(q.source || '')}" placeholder="如：2025年国考行测执法卷第25题"></div>
        <div class="field full"><label for="qe-cat">题型分类（题型树节点，可选）</label>
          <select id="qe-cat"><option value="">（不关联）</option>${_qbCatOptions}</select></div>
        <div class="field full"><label for="qe-category">课程分类</label>
          <select id="qe-category"><option value="">（未分类）</option>${QB_CATEGORIES.map(c => `<option value="${esc(c)}" ${c === (q.category || '') ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></div>
        <div class="field full"><label for="qe-question">题目 *</label><textarea id="qe-question" rows="3" onblur="checkQfDup('qe-question','qe-dup-tip')">${esc(q.question)}</textarea></div>
        <div class="field full" id="qe-dup-tip" style="display:none"></div>
        <div class="field full" id="qe-options-wrap" style="${q.qtype === 'choice' ? '' : 'display:none'}"><label for="qe-options">选项（每行一个）</label><textarea id="qe-options" rows="4"></textarea></div>
        <div class="field full"><label for="qe-answer">答案 *</label><input id="qe-answer" value="${esc(q.answer)}"></div>
        <div class="field full"><label for="qe-analysis">解析</label><textarea id="qe-analysis" rows="2">${esc(q.analysis || '')}</textarea></div>
        <div class="field full"><label for="qe-kp">知识点</label><input id="qe-kp" value="${esc(q.knowledge_point || '')}" placeholder="如：主旨概括"></div>
      </div>
      <div class="modal-actions">
        <button class="btn ghost" onclick="closeModal('qb-modal')">取消</button>
        <button class="btn primary" onclick="submitEditQuestion(${q.id})">保存</button>
      </div>
      <p class="hint" id="qe-hint"></p>`);
    $('#qe-options').value = (q.options || []).join('\n');
    if ($('#qe-cat') && [...$('#qe-cat').options].some(o => o.value === (q.category_id || ''))) $('#qe-cat').value = q.category_id || '';
  } catch (e) {
    $('#qb-modal .modal-box').innerHTML = `<h3>加载失败</h3><p class="err-tip">${esc(e.message)}</p>
      <div class="modal-actions"><button class="btn ghost sm" onclick="closeModal('qb-modal')">关闭</button></div>`;
  }
}
function toggleQeOptions() {
  const t = ($('#qe-type') && $('#qe-type').value);
  const w = $('#qe-options-wrap');
  if (w) w.style.display = (t === 'choice') ? '' : 'none';
}
async function submitEditQuestion(qid) {
  const qtype = $('#qe-type').value;
  const question = $('#qe-question').value.trim();
  const answer = $('#qe-answer').value.trim();
  const analysis = $('#qe-analysis').value.trim();
  const difficulty = parseInt($('#qe-difficulty').value) || 1;
  const knowledge_point = $('#qe-kp').value.trim();
  const category_id = ($('#qe-cat') && $('#qe-cat').value) || '';
  const category = ($('#qe-category') && $('#qe-category').value) || '';
  const source_type = ($('#qe-source-type') && $('#qe-source-type').value) || '';
  const source = ($('#qe-source') && $('#qe-source').value.trim()) || '';
  const options = (qtype === 'choice') ? $('#qe-options').value.split('\n').map(s => s.trim()).filter(Boolean) : [];
  const hint = $('#qe-hint');
  if (!question || !answer) { hint.textContent = '题目和答案必填'; hint.className = 'hint err'; return; }
  hint.textContent = '保存中…';
  try {
    await admin(`/admin/questions/${qid}`, { method: 'PUT', body: JSON.stringify({ qtype, question, options, answer, analysis, difficulty, knowledge_point, category_id, category, source_type, source }) });
    toast('题目已更新', 'ok');
    closeModal('qb-modal');
    loadQuestions(); loadQbStats();
  } catch (e) {
    hint.textContent = '保存失败：' + e.message; hint.className = 'hint err';
  }
}

// #26 R2：复制题目
async function copyQuestion(qid) {
  try {
    const r = await admin(`/admin/questions/${qid}/copy`, { method: 'POST' });
    toast(`已复制为 #${r.id}`, 'ok');
    loadQuestions(); loadQbStats();
  } catch (e) { toast('复制失败：' + e.message, 'err'); }
}
async function submitDeleteQuestion(qid) {
  try {
    await admin(`/admin/questions/${qid}`, { method: 'DELETE' });
    toast('题目已删除', 'ok');
    closeModal('qb-modal');
    loadQuestions(); loadQbStats();
  } catch (e) { toast('删除失败：' + e.message, 'err'); }
}

// ============================================================
// #25 题库批量操作：勾选 · 批量删除 · 导出 CSV
// ============================================================
const _qbChecked = new Set();
function qbCheck(qid, on) {
  on ? _qbChecked.add(qid) : _qbChecked.delete(qid);
  const hint = $('#qb-sel-hint');
  if (hint) hint.textContent = _qbChecked.size ? `已选 ${_qbChecked.size} 题` : '勾选题目后可批量删除';
}
function qbCheckAll(on) {
  $$('#qb-body input[type=checkbox][data-qid]').forEach(cb => {
    cb.checked = on; qbCheck(parseInt(cb.dataset.qid, 10), on);
  });
}
async function qbBatchDelete() {
  if (!_qbChecked.size) { toast('请先勾选要删除的题目', 'err'); return; }
  if (!confirm(`确认批量删除勾选的 ${_qbChecked.size} 道题目？不可恢复。`)) return;
  try {
    const r = await admin('/admin/questions/batch', { method: 'POST',
      body: JSON.stringify({ qids: [..._qbChecked] }) });
    toast(`已删除 ${r.deleted} 道${(r.missing || []).length ? '，未找到 ' + r.missing.length + ' 道' : ''}`, 'ok');
    _qbChecked.clear(); loadQuestions(); loadQbStats();
  } catch (e) { toast('批量删除失败：' + e.message, 'err'); }
}
async function exportQuestions() {
  const tid = ($('#qb-teacher') && $('#qb-teacher').value) || 'T001';
  const params = new URLSearchParams({ teacher_id: tid });
  if (qbPage.qtype) params.set('qtype', qbPage.qtype);
  if (qbPage.difficulty) params.set('difficulty', qbPage.difficulty);
  if (qbPage.kp) params.set('knowledge_point', qbPage.kp);
  if (qbPage.keyword) params.set('keyword', qbPage.keyword);
  const _cat = ($('#qb-category') && $('#qb-category').value) || '';
  if (_cat) params.set('category', _cat);
  const _src = ($('#qb-source') && $('#qb-source').value) || '';
  if (_src) params.set('source_type', _src);
  params.set('limit', '1000');
  try {
    const r = await admin(`/admin/questions?${params.toString()}`);
    const qs = r.questions || [];
    if (!qs.length) { toast('当前筛选无题目可导出', 'err'); return; }
    const typeMap = { choice: '选择题', judge: '判断题', essay: '简答题' };
    const esc = s => '"' + String(s == null ? '' : s).replace(/"/g, '""') + '"';
    const head = ['ID', '题型', '分类', '题目', '选项', '答案', '解析', '知识点', '难度', '来源类型', '来源出处'];
    const rows = qs.map(q => [q.id, typeMap[q.qtype] || q.qtype, q.category || '', q.question,
      (q.options || []).join(' | '), q.answer, q.analysis || '', q.knowledge_point || '', q.difficulty || 1,
      q.source_type || '', q.source || '']);
    const csv = [head, ...rows.map(r => r.map(esc).join(','))].join('\r\n');
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `题库_${tid}_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a); a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 400);
    toast(`已导出 ${qs.length} 道题目`, 'ok');
  } catch (e) { toast('导出失败：' + e.message, 'err'); }
}

// ============================================================
// AI 采集题库（#16 新增）
// ============================================================
let _extractedQuestions = [];

function openQuestionImport() {
  openModal('qb-modal', `
    <h3>AI 采集题库</h3>
    <p style="font-size:13px;color:var(--ink-3);margin-bottom:12px">上传或粘贴文档，AI 扫描内容自动提取题目，并按题型 / 知识点 / 难度分类。</p>
    <div class="field">
      <label>采集方式</label>
      <div style="display:flex;gap:10px">
        <button class="btn ghost sm" id="imp-tab-file" onclick="switchImportMode('file')" style="flex:1">📄 上传文档</button>
        <button class="btn ghost sm" id="imp-tab-text" onclick="switchImportMode('text')" style="flex:1">✏️ 粘贴文本</button>
      </div>
    </div>
    <div id="imp-file-pane">
      <div class="upload-zone" id="imp-upload-zone" role="button" tabindex="0" aria-label="点击选择或拖拽上传文档，支持 md、txt、docx、pptx、pdf，单文件不超过 50MB">
        <span class="uz-ico">📄</span>
        <div class="uz-tip">点击选择或拖拽文档到此处</div>
        <div class="uz-sub">
          <span class="badge gray">md</span> <span class="badge gray">txt</span> 本地秒解析 ·
          <span class="badge gray">docx</span> <span class="badge gray">pptx</span> <span class="badge gray">pdf</span> 云端解析
        </div>
        <div class="uz-sub" style="color:var(--ink-3)">单文件 ≤ 50MB · docx / pptx / pdf 上传解析大文件可能需 1–2 分钟</div>
      </div>
      <input type="file" id="imp-file" accept=".md,.txt,.text,.docx,.pptx,.pdf" style="display:none">
    </div>
    <div id="imp-text-pane" style="display:none">
      <label for="imp-text" style="display:block;font-size:12.5px;color:var(--ink-2);font-weight:600;margin-bottom:5px">文档内容</label>
      <textarea id="imp-text" rows="8" style="width:100%;padding:10px;border:1px solid var(--line-2);border-radius:8px" placeholder="粘贴题目文档内容…&#10;（超 1.2 万字自动分批提取，单次上限 9.6 万字）"></textarea>
    </div>
    <div id="imp-result" style="margin-top:12px"></div>
    <div class="modal-actions" style="margin-top:12px">
      <button class="btn ghost" onclick="closeModal('qb-modal')">关闭</button>
      <button class="btn primary" id="imp-import-btn" disabled onclick="importExtracted()">批量入库（0）</button>
      <button class="btn primary" id="imp-extract-btn" onclick="extractQuestions()">开始 AI 提取</button>
    </div>
    <p class="hint" id="imp-hint"></p>
  `);
  _extractedQuestions = [];
  switchImportMode('file');
  const zone = $('#imp-upload-zone');
  const fi = $('#imp-file');
  const zoneTip = () => zone.querySelector('.uz-tip');
  const ZONE_TIP_DEFAULT = '点击选择或拖拽文档到此处';
  zone.onclick = () => fi.click();
  zone.ondragover = (e) => { e.preventDefault(); zone.classList.add('drag'); zoneTip().textContent = '松开鼠标即可上传（≤ 50MB）'; };
  zone.ondragleave = () => { zone.classList.remove('drag'); zoneTip().textContent = ZONE_TIP_DEFAULT; };
  zone.ondrop = (e) => {
    e.preventDefault(); zone.classList.remove('drag'); zoneTip().textContent = ZONE_TIP_DEFAULT;
    if (e.dataTransfer.files[0]) handleImpFile(e.dataTransfer.files[0]);
  };
  fi.onchange = () => { if (fi.files[0]) handleImpFile(fi.files[0]); };
}

function switchImportMode(mode) {
  const filePane = $('#imp-file-pane'), textPane = $('#imp-text-pane');
  const tf = $('#imp-tab-file'), tt = $('#imp-tab-text');
  if (!filePane) return;
  if (mode === 'file') { filePane.style.display = ''; textPane.style.display = 'none'; tf.className = 'btn primary sm'; tt.className = 'btn ghost sm'; }
  else { filePane.style.display = 'none'; textPane.style.display = ''; tf.className = 'btn ghost sm'; tt.className = 'btn primary sm'; }
}

// 处理上传文件 → 提取文本
// md/txt 本地直读（省一次上传）；docx/pptx/pdf 上传后端解析（#19，与文档入库同源链路）
// 提示原则：每一步都带文件名+实际大小；错误提示带实际值与允许范围（#23 交互提示补强）
const IMP_MAX_BYTES = 50 * 1024 * 1024;  // 与后端 AICOLLECT_MAX_BYTES、nginx client_max_body_size 对齐
async function handleImpFile(file) {
  const hint = $('#imp-hint');
  const sizeTxt = fmtSize(file.size);
  const ext = (file.name.includes('.') ? file.name.split('.').pop() : '').toLowerCase();
  try {
    if (file.size > IMP_MAX_BYTES) {
      hint.textContent = `「${file.name}」大小 ${sizeTxt}，超过 50MB 上限 — 请压缩、拆分文档或改用粘贴文本`;
      hint.className = 'hint err';
      return;
    }
    let text = '';
    let nImg = 0;
    if (/\.(md|txt|text)$/i.test(file.name)) {
      hint.textContent = `本地解析中：${file.name}（${sizeTxt}）…`;
      hint.className = 'hint';
      text = await file.text();
    } else if (/\.(docx|pptx|pdf)$/i.test(file.name)) {
      const tid = ($('#qb-teacher') && $('#qb-teacher').value) || 'T001';
      const fd = new FormData();
      fd.append('file', file, file.name);
      fd.append('teacher_id', tid);
      hint.textContent = `云端解析中：${file.name}（${sizeTxt}）— 请保持窗口打开，大文件可能需 1–2 分钟…`;
      hint.className = 'hint';
      const r = await admin('/admin/questions/parse-file', { method: 'POST', body: fd, timeout: 300000 });
      text = r.text || '';
      nImg = r.n_img || 0;
    } else {
      hint.textContent = `不支持 .${ext || '未知'} 格式（${file.name} · ${sizeTxt}）— 仅支持 md / txt / text / docx / pptx / pdf`;
      hint.className = 'hint err';
      return;
    }
    if (!text.trim() && !nImg) {
      hint.textContent = `「${file.name}」（${sizeTxt}）未解析出文本 — 文件内容为空，或为扫描版 PDF（图片无文字层），建议改用文字版或粘贴文本`;
      hint.className = 'hint err';
      return;
    }
    hint.textContent = `已读取 ${text.length.toLocaleString()} 字${nImg ? ` + ${nImg} 张图` : ''}（${file.name} · ${sizeTxt}），点击「开始 AI 提取」`;
    hint.className = 'hint ok';
    $('#imp-file-pane').innerHTML = `<div class="upload-zone" style="border-color:var(--good)">✅ 已加载：${esc(file.name)}（${sizeTxt} · ${text.length.toLocaleString()} 字${nImg ? ` · ${nImg} 图` : ''}）</div>`;
    $('#imp-text-pane').style.display = 'none';
    $('#imp-text').value = text;
  } catch (e) {
    hint.textContent = `解析失败（${file.name} · ${sizeTxt}）：` + e.message;
    hint.className = 'hint err';
  }
}

// 长文分批提示参数（与后端 admin._split_batches 的 BATCH=2500 / MAX_BATCHES=60 对齐）
// #35 R2：单请求容量提升至 10 万字（后端内部 2500/批 × 60 批 + 3 并发提取），整套试卷可单请求采集
const IMP_BATCH_CHARS = 100000;
const IMP_BATCH_MAX = 4;       // 总容量 40 万字（10 万 × 4），超出部分显式确认丢弃

let _impCancel = false;         // 分批采集取消标志

// 与后端 _split_batches 对齐：按段落边界切批，不拦腰截断题目
function _splitQbBatches(text, batch = IMP_BATCH_CHARS, max = IMP_BATCH_MAX) {
  const t = text.trim();
  if (t.length <= batch) return [t];
  const out = [];
  let rest = t;
  while (rest && out.length < max) {
    if (rest.length <= batch) { out.push(rest); break; }
    let cut = rest.lastIndexOf('\n', batch - 1);
    if (cut < batch * 0.5) cut = batch;          // 找不到段落边界则硬切
    if (cut <= 0) cut = batch;
    out.push(rest.slice(0, cut));
    rest = rest.slice(cut).replace(/^\n+/, '');
  }
  return out;
}

// 分批复采进度条（内联样式，不依赖类名）
function _setImpProgress(done, total, label) {
  const box = $('#imp-progress');
  if (!box) return;
  const pct = total ? Math.round(done / total * 100) : 0;
  box.innerHTML = `<div style="margin:10px 0 4px;font-size:12.5px;color:var(--ink-2);font-weight:600">${label}</div>
    <div style="height:8px;background:var(--line-2);border-radius:999px;overflow:hidden">
      <div style="width:${pct}%;height:100%;background:var(--good);transition:width .35s"></div>
    </div>
    <div style="font-size:11.5px;color:var(--ink-3);margin-top:5px">已完成 ${done}/${total} 批
      <button class="btn sm ghost" style="margin-left:8px" onclick="_impCancel=true">取消采集</button></div>`;
}

async function extractQuestions() {
  const hint = $('#imp-hint');
  const text = ($('#imp-text') && $('#imp-text').value || '').trim();
  const tid = ($('#qb-teacher') && $('#qb-teacher').value) || 'T001';
  if (!text) { hint.textContent = '请先上传文档或粘贴文本（支持 md / txt / text / docx / pptx / pdf，≤ 50MB）'; hint.className = 'hint err'; return; }
  if (text.length > IMP_BATCH_CHARS * IMP_BATCH_MAX) {
    const over = text.length - IMP_BATCH_CHARS * IMP_BATCH_MAX;
    if (!confirm(`内容约 ${(text.length / 10000).toFixed(1)} 万字，超过单次提取上限 40 万字，\n超出约 ${over.toLocaleString()} 字将不参与提取。\n\n建议拆分文档分次采集。是否仍继续？`)) {
      hint.textContent = '已取消 — 请拆分文档或删减内容后重试';
      hint.className = 'hint';
      return;
    }
  }
  const btn = $('#imp-extract-btn');
  btn.disabled = true;
  hint.className = 'hint hintProgressRow';
  // 动态插入进度区（位于结果区上方）
  let prog = $('#imp-progress');
  if (!prog) {
    prog = document.createElement('div');
    prog.id = 'imp-progress';
    const resBox = $('#imp-result');
    resBox.parentNode.insertBefore(prog, resBox);
  }
  const batches = _splitQbBatches(text);
  _impCancel = false;
  const acc = [];
  const accWarnings = [];
  let accExpected = 0;
  try {
    for (let i = 0; i < batches.length; i++) {
      if (_impCancel) throw new Error('已取消');
      _setImpProgress(i, batches.length, `AI 提取第 ${i + 1}/${batches.length} 批（本批约 ${batches[i].length.toLocaleString()} 字）…`);
      const r = await admin('/admin/questions/extract', { method: 'POST', timeout: 600000, body: JSON.stringify({ teacher_id: tid, text: batches[i] }) });
      acc.push(...(r.questions || []));
      (r.warnings || []).forEach(w => accWarnings.push(w));
      accExpected += (r.n_expected || 0);
    }
    _setImpProgress(batches.length, batches.length, '全部提取完成');
    _extractedQuestions = acc;
    renderExtracted(accWarnings, accExpected);
    hint.textContent = `共 ${batches.length} 批提取完成，识别 ${acc.length} 道题${accExpected ? `（检测约 ${accExpected} 道）` : ''}，请核对后入库`;
    hint.className = 'hint';
  } catch (e) {
    if (_impCancel) { hint.textContent = '已取消采集（已完成部分未入库）'; hint.className = 'hint'; }
    else if (acc.length) {
      // #29 R3：部分批次失败时保留已提取题目，仍可入库
      _extractedQuestions = acc;
      renderExtracted();
      hint.textContent = `部分批次提取失败（${e.message}），已保留已识别 ${acc.length} 道题，可入库或重试剩余部分`;
      hint.className = 'hint';
    } else { hint.textContent = '提取失败：' + e.message; hint.className = 'hint err'; }
  } finally {
    if (prog) prog.remove();
    btn.disabled = false;
  }
}

function renderExtracted(warnings, nExpected) {
  const box = $('#imp-result');
  const hint = $('#imp-hint');
  const ib = $('#imp-import-btn');
  if (!_extractedQuestions.length) {
    box.innerHTML = '<div class="empty">未提取到题目，请确认文档含题目内容</div>';
    if (ib) { ib.disabled = true; ib.textContent = '批量入库（0）'; }
    hint.textContent = '未识别到题目';
    hint.className = 'hint';
    return;
  }
  const typeMap = { choice: '选择', judge: '判断', essay: '简答' };
  const typeBadge = { choice: 'blue', judge: 'purple', essay: 'green' };
  const nWithImg = _extractedQuestions.filter(q => q.images && q.images.length).length;
  // #35 R1：分类分布统计
  const catCount = {};
  _extractedQuestions.forEach(q => { const c = q.category || '综合'; catCount[c] = (catCount[c] || 0) + 1; });
  const catSummary = Object.entries(catCount).sort((a, b) => b[1] - a[1])
    .map(([c, n]) => `<span class="badge ${QB_CAT_BADGE_COLOR[c] || 'gray'}" style="margin-right:6px">${esc(c)} ×${n}</span>`).join('');
  const warnHtml = (warnings && warnings.length)
    ? `<div style="margin-bottom:8px;padding:10px 12px;border:1px solid #e0a800;border-radius:8px;background:#fff8e6;font-size:12.5px;color:#8a6100">
        ${warnings.map(w => `<div style="margin:2px 0">⚠ ${esc(w)}</div>`).join('')}</div>` : '';
  box.innerHTML = `
    ${warnHtml}
    <div style="font-size:13px;font-weight:700;margin-bottom:8px">已识别 ${_extractedQuestions.length} 道题${nExpected ? `（AI 检测约 ${nExpected} 道）` : ''}${nWithImg ? `，含图 ${nWithImg} 题` : ''}，请核对后批量入库：</div>
    <div style="margin-bottom:8px;font-size:12px">${catSummary}</div>
    <div class="field" style="margin-bottom:8px"><label for="imp-cat">批量关联题型分类（可选，全部题目挂到该题型树节点）</label>
      <select id="imp-cat"><option value="">（不关联）</option>${_qbCatOptions}</select></div>
    <div style="max-height:240px;overflow:auto;border:1px solid var(--line);border-radius:8px">
      ${_extractedQuestions.map((q, i) => `
        <div style="padding:9px 12px;border-bottom:1px solid var(--line);font-size:12.5px">
          <span class="badge ${typeBadge[q.qtype] || 'gray'}">${typeMap[q.qtype] || q.qtype}</span>
          ${q.category ? `<span class="badge ${QB_CAT_BADGE_COLOR[q.category] || 'gray'}">${esc(q.category)}</span>` : ''}
          <b>${esc(qTextPlain(q.question).slice(0, 40))}</b>
          ${(q.images && q.images.length) ? `<span class="badge gold" title="题干含配图">图×${q.images.length}</span>` : ''}
          ${q.knowledge_point ? `<span class="badge blue">${esc(q.knowledge_point)}</span>` : ''}
          <span style="color:var(--ink-3)">答：${esc((q.answer || '').slice(0, 15))}</span>
          <span style="color:var(--gold-deep)">${'★'.repeat(q.difficulty || 1)}</span>
        </div>`).join('')}
    </div>`;
  if (ib) { ib.disabled = false; ib.textContent = `批量入库（${_extractedQuestions.length}）`; }
  hint.textContent = '';
}

async function importExtracted() {
  const tid = ($('#qb-teacher') && $('#qb-teacher').value) || 'T001';
  const hint = $('#imp-hint');
  if (!hint) return;
  if (!_extractedQuestions.length) { hint.textContent = '暂无可入库的题目'; hint.className = 'hint'; return; }
  const catId = ($('#imp-cat') && $('#imp-cat').value) || '';
  const items = catId
    ? _extractedQuestions.map(q => ({ ...q, category_id: catId }))
    : _extractedQuestions;
  hint.textContent = '入库中…';
  hint.className = 'hint';
  try {
    const r = await admin('/admin/questions/import', { method: 'POST', body: JSON.stringify({ teacher_id: tid, questions: items }) });
    const msg = `已入库 ${r.added} 题，跳过重复 ${r.skipped} 题` + (r.invalid ? `，校验拒收 ${r.invalid} 题（选择题缺选项/缺答案）` : '');
    hint.textContent = msg;
    hint.className = 'hint ok';
    toast(msg, r.invalid ? 'info' : 'ok');
    _extractedQuestions = [];
    closeModal('qb-modal');
    loadQuestions(); loadQbStats(); loadKpOptions();
  } catch (e) {
    hint.textContent = '入库失败：' + e.message;
    hint.className = 'hint err';
  }
}

// ============================================================
// 知识体系管理（#20：树 CRUD/检索/批量 + #21 AI 思维导图
//   #24 R2：knowledge/qtype 双树切换（题型多级细分）
//   #24 R3：文档建树 / 联网搜索建树）
// ============================================================
let _knTree = [];            // 当前树数据
let _knCollapsed = new Set(); // 折叠的节点 id
let _knChecked = new Set();   // 批量勾选的节点 id
let _knTreeType = 'knowledge'; // 当前树类型：knowledge=知识树 | qtype=题型树（#24 R2）
let _knSearchOn = null;       // 联网搜索可用性（null=未探测，#24 R3）

function knTypeLabel() { return _knTreeType === 'qtype' ? '题型树' : '知识体系树'; }

function knSwitchType() {
  const v = ($('#kn-tree-type') && $('#kn-tree-type').value) || 'knowledge';
  if (v === _knTreeType) return;
  _knTreeType = v;
  _knCollapsed.clear(); _knChecked.clear();
  loadKnTree();
}

async function renderKnowledge() {
  $('#content').innerHTML = `
  <div class="cards" id="kn-stats-cards"></div>
  <div class="panel">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap">
        <label for="kn-teacher">老师：</label><select id="kn-teacher" onchange="loadKnTree()" aria-label="选择老师"></select>
        <select id="kn-tree-type" onchange="knSwitchType()" aria-label="切换树类型（知识体系 / 题型细分）">
          <option value="knowledge">🌳 知识体系树</option>
          <option value="qtype">🗂 题型细分树</option>
        </select>
        <input type="search" id="kn-kw" class="grow" placeholder="搜索节点名/分类/说明…" onkeydown="if(event.key==='Enter')loadKnTree(true)" aria-label="搜索知识节点">
        <button class="btn primary sm" onclick="knShowForm()">＋ 新增节点</button>
        <button class="btn ghost sm" onclick="knBatchDelete()">🗑 批量删除</button>
        <button class="btn ghost sm" onclick="knBatchMove()">📦 批量移动</button>
        <button class="btn sm" style="background:var(--gold-soft);border-color:var(--gold)" onclick="knMindmap()">🧠 AI 思维导图</button>
        <button class="btn ghost sm" onclick="knBuildFromDoc()">📄 文档建树</button>
        <button class="btn ghost sm" id="kn-search-build-btn" onclick="knBuildFromSearch()">🌐 联网搜索建树</button>
      </div>
    </div>
    <div id="kn-tree-box" style="padding:8px 14px;min-height:200px"><div class="loading">加载中…</div></div>
  </div>
  <div id="kn-modal" class="modal-mask"></div>`;
  $('#kn-tree-type').value = _knTreeType;
  const r = await admin('/admin/teachers');
  const teachers = (r.teachers || []).filter(t => t.teacher_id !== '__sys__');
  const sel = $('#kn-teacher');
  sel.innerHTML = teachers.map(t => `<option value="${esc(t.teacher_id)}">${esc(t.teacher_name)}（${esc(t.teacher_id)}）</option>`).join('');
  // #24 R3：探测联网搜索可用性（禁用时按钮降灰提示）
  admin('/admin/knowledge/search-status').then(s => {
    _knSearchOn = !!s.available;
    const b = $('#kn-search-build-btn');
    if (b && !s.available) { b.disabled = true; b.title = `服务器未启用联网搜索（provider=${s.provider || 'none'}）`; }
  }).catch(() => {});
  if (teachers.length) await loadKnTree();
  else $('#kn-tree-box').innerHTML = '<div class="empty">无老师</div>';
}

function knTid() { return ($('#kn-teacher') && $('#kn-teacher').value) || 'T001'; }

async function loadKnTree(search) {
  const kw = search === true ? (($('#kn-kw') && $('#kn-kw').value) || '').trim()
                             : (($('#kn-kw') && $('#kn-kw').value) || '').trim();
  try {
    const r = await admin(`/admin/knowledge/tree?teacher_id=${encodeURIComponent(knTid())}&tree_type=${_knTreeType}&keyword=${encodeURIComponent(kw)}`);
    _knTree = r.nodes || [];
    if (!search) _knCollapsed.clear();
    _knChecked.clear();
    knRenderTree();
    knLoadStats();
  } catch (e) {
    $('#kn-tree-box').innerHTML = `<p class="err-tip">加载失败：${esc(e.message)}</p>`;
  }
}

function knFlatten(nodes, depth, out) {
  out = out || []; depth = depth || 0;
  (nodes || []).forEach(n => { out.push({ ...n, _depth: depth }); knFlatten(n.children, depth + 1, out); });
  return out;
}

function knRenderTree() {
  const box = $('#kn-tree-box');
  if (!box) return;
  if (!_knTree.length) {
    box.innerHTML = _knTreeType === 'qtype'
      ? '<div class="empty">题型树为空。点击「＋ 新增节点」创建第一层级（如「行测」），再逐级细分（判断推理 → 图形推理 → 黑白块），支持任意多级。</div>'
      : '<div class="empty">知识体系为空，点击「＋ 新增节点」创建第一个知识节点（如"言语理解"），或用「📄 文档建树 / 🌐 联网搜索建树」一键生成</div>';
    return;
  }
  const render = (nodes, depth) => nodes.map(n => {
    const hasKids = (n.children || []).length > 0;
    const collapsed = _knCollapsed.has(n.node_id);
    const checked = _knChecked.has(n.node_id);
    return `
    <div class="kn-row" data-id="${esc(n.node_id)}" style="display:flex;align-items:center;gap:6px;padding:6px ${6 + depth * 22}px;border-bottom:1px solid var(--line)">
      ${hasKids
        ? `<button class="btn ghost sm" style="padding:2px 7px" onclick="knToggle('${esc(n.node_id)}')" aria-label="展开折叠">${collapsed ? '▸' : '▾'}</button>`
        : '<span style="width:30px"></span>'}
      <input type="checkbox" ${checked ? 'checked' : ''} onchange="knCheck('${esc(n.node_id)}', this.checked)" aria-label="选择节点">
      <b style="font-size:13.5px">${esc(n.name)}</b>
      ${n.category ? `<span class="badge blue">${esc(n.category)}</span>` : ''}
      ${hasKids ? `<span style="font-size:11.5px;color:var(--ink-3)">（${n.children.length}）</span>` : ''}
      ${n.description ? `<span style="font-size:12px;color:var(--ink-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:340px" title="${esc(n.description)}">${esc(n.description)}</span>` : ''}
      <span style="flex:1"></span>
      <button class="btn ghost sm" onclick="knShowForm(null,'${esc(n.node_id)}')" title="在此节点下新增子节点">＋子级</button>
      <button class="btn ghost sm" onclick="knShowForm('${esc(n.node_id)}')">编辑</button>
      <button class="btn ghost sm" onclick="knDeleteNode('${esc(n.node_id)}','${esc(n.name)}')">删除</button>
    </div>
    ${(!collapsed && hasKids) ? render(n.children, depth + 1) : ''}`;
  }).join('');
  box.innerHTML = render(_knTree, 0);
}

function knToggle(id) { _knCollapsed.has(id) ? _knCollapsed.delete(id) : _knCollapsed.add(id); knRenderTree(); }
function knCheck(id, on) { on ? _knChecked.add(id) : _knChecked.delete(id); }

async function knLoadStats() {
  try {
    const s = await admin(`/admin/knowledge/stats?teacher_id=${encodeURIComponent(knTid())}&tree_type=${_knTreeType}`);
    const cats = (s.categories || []).slice(0, 4).map(c => `${esc(c.category)} ${c.count}`).join(' · ');
    $('#kn-stats-cards').innerHTML = _knTreeType === 'qtype' ? `
      <div class="card"><div class="card-label">题型节点</div><div class="card-n">${s.total}</div><div class="card-sub">多级细分 · 如 行测/判断推理/图形推理/黑白块</div></div>
      <div class="card"><div class="card-label">分类数</div><div class="card-n gold">${(s.categories || []).length}</div><div class="card-sub">${cats || '暂无分类'}</div></div>
      <div class="card"><div class="card-label">题目关联</div><div class="card-n green">🔗</div><div class="card-sub">题库列表可按题型树节点筛选（含子树）</div></div>` : `
      <div class="card"><div class="card-label">知识节点</div><div class="card-n">${s.total}</div><div class="card-sub">${cats || '暂无分类'}</div></div>
      <div class="card"><div class="card-label">分类数</div><div class="card-n gold">${(s.categories || []).length}</div><div class="card-sub">category 标签</div></div>
      <div class="card"><div class="card-label">AI 导图</div><div class="card-n green">🧠</div><div class="card-sub">知识树一键生成思维导图</div></div>`;
  } catch (e) { $('#kn-stats-cards') && ($('#kn-stats-cards').innerHTML = ''); }
}

// 父级下拉 options（排除 excludeId 自身及子孙，防环）
function knParentOptions(excludeId) {
  const banned = new Set();
  if (excludeId) {
    const walkBan = n => { banned.add(n.node_id); (n.children || []).forEach(walkBan); };
    _knTree.forEach(n => { if (n.node_id === excludeId) walkBan(n); });
    // 深层也可能有（搜索过滤后的树挂在根）——全树扫描兜底
    knFlatten(_knTree).forEach(f => { if (f.node_id === excludeId) { banned.add(excludeId); } });
  }
  const opts = [];
  const walk = (nodes, prefix) => nodes.forEach(n => {
    if (!banned.has(n.node_id)) {
      opts.push(`<option value="${esc(n.node_id)}">${prefix}${esc(n.name)}</option>`);
      walk(n.children || [], prefix + '　');
    }
  });
  walk(_knTree, '');
  return opts;
}

// 新增/编辑节点模态框（editId 空=新增；presetParent 指定新节点的父级）
function knShowForm(editId, presetParent) {
  const editing = !!editId;
  const flat = knFlatten(_knTree);
  const node = editing ? flat.find(n => n.node_id === editId) : null;
  const parentVal = editing ? node.parent_id : (presetParent || '');
  openModal('kn-modal', `
    <h3>${editing ? '编辑知识节点' : '新增知识节点'}</h3>
    <div class="field"><label for="knf-name">节点名称 *</label>
      <input id="knf-name" value="${editing ? esc(node.name) : ''}" maxlength="60" placeholder="如：言语理解 / 转折关系"></div>
    <div class="field"><label for="knf-parent">父级节点（空 = 根级）</label>
      <select id="knf-parent"><option value="">（根级）</option>${knParentOptions(editId)}</select></div>
    <div class="field"><label for="knf-category">分类标签</label>
      <input id="knf-category" value="${editing ? esc(node.category || '') : ''}" maxlength="30" placeholder="如：行测 / 申论 / 面试"></div>
    <div class="field"><label for="knf-desc">考点说明</label>
      <textarea id="knf-desc" rows="3" maxlength="500" placeholder="一句话考点/易错点说明（AI 导图增强会自动补全空缺）">${editing ? esc(node.description || '') : ''}</textarea></div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('kn-modal')">取消</button>
      <button class="btn primary" onclick="knSaveForm('${editing ? esc(editId) : ''}')">保存</button>
    </div>`);
  $('#knf-parent').value = parentVal;
}

async function knSaveForm(editId) {
  const name = ($('#knf-name').value || '').trim();
  if (!name) { toast('节点名称不能为空', 'err'); return; }
  const body = {
    name, category: $('#knf-category').value.trim(), description: $('#knf-desc').value.trim(),
    parent_id: $('#knf-parent').value,
  };
  try {
    if (editId) {
      await admin(`/admin/knowledge/nodes/${editId}`, { method: 'PUT', body: JSON.stringify(body) });
    } else {
      await admin('/admin/knowledge/nodes', { method: 'POST', body: JSON.stringify({ teacher_id: knTid(), tree_type: _knTreeType, ...body }) });
    }
    closeModal('kn-modal');
    toast(editId ? '节点已更新' : '节点已创建', 'ok');
    await loadKnTree();
  } catch (e) { toast('保存失败：' + e.message, 'err'); }
}

async function knDeleteNode(id, name) {
  if (!confirm(`确认删除「${name}」？其全部子节点将一并删除。`)) return;
  try {
    const r = await admin(`/admin/knowledge/nodes/${id}`, { method: 'DELETE' });
    toast(`已删除 ${r.deleted} 个节点`, 'ok');
    await loadKnTree();
  } catch (e) { toast('删除失败：' + e.message, 'err'); }
}

async function knBatchDelete() {
  if (!_knChecked.size) { toast('请先勾选要删除的节点', 'err'); return; }
  if (!confirm(`确认批量删除勾选的 ${_knChecked.size} 个节点（含子树）？`)) return;
  try {
    const r = await admin('/admin/knowledge/batch-delete', {
      method: 'POST', body: JSON.stringify({ node_ids: [..._knChecked] }) });
    toast(`已删除 ${r.deleted} 个节点`, 'ok');
    await loadKnTree();
  } catch (e) { toast('批量删除失败：' + e.message, 'err'); }
}

async function knBatchMove() {
  if (!_knChecked.size) { toast('请先勾选要移动的节点', 'err'); return; }
  openModal('kn-modal', `
    <h3>批量移动 ${_knChecked.size} 个节点</h3>
    <div class="field"><label for="knm-target">移动到（空 = 根级）</label>
      <select id="knm-target"><option value="">（根级）</option>${knParentOptions('')}</select></div>
    <p class="hint">环检测会阻止把节点移动到其自身子节点之下。</p>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('kn-modal')">取消</button>
      <button class="btn primary" onclick="knDoBatchMove()">移动</button>
    </div>`);
}

async function knDoBatchMove() {
  const target = $('#knm-target').value;
  try {
    const r = await admin('/admin/knowledge/batch-move', {
      method: 'POST', body: JSON.stringify({ node_ids: [..._knChecked], target_parent_id: target }) });
    closeModal('kn-modal');
    toast(`已移动 ${r.moved} 个节点`, 'ok');
    await loadKnTree();
  } catch (e) { toast('批量移动失败：' + e.message, 'err'); }
}

// ---------- #24 R3：文档建树 / 联网搜索建树 ----------
function knPreviewTreeHtml(arr, depth = 0) {
  return (arr || []).map(it => {
    if (!it || !it.name) return '';
    return `<div style="padding:3px 0 3px ${depth * 18}px;border-bottom:1px dashed var(--line)">
      <b style="font-size:13px">${esc(it.name)}</b>
      ${it.description ? `<span style="font-size:12px;color:var(--ink-3)"> — ${esc(it.description)}</span>` : ''}
      ${(it.children || []).length ? knPreviewTreeHtml(it.children, depth + 1) : ''}
    </div>`;
  }).join('');
}

async function knBuildFromDoc() {
  const tid = knTid();
  let docs = [];
  try {
    const r = await admin(`/admin/documents?teacher_id=${encodeURIComponent(tid)}`);
    docs = r.documents || [];
  } catch (e) { /* 文档列表加载失败不阻断弹窗 */ }
  if (!docs.length) { toast('该老师暂无已上传文档，请先到「文档管理」上传', 'err'); return; }
  openModal('kn-modal', `
    <h3>📄 文档建树</h3>
    <p style="font-size:12.5px;color:var(--ink-3);margin-bottom:10px">
      读取该老师已上传的文档全文（Word / PDF / Excel / md / txt / pptx）→ AI 整理成知识点清单 →
      写入当前${knTypeLabel()}。支持多级结构。</p>
    <div class="field"><label for="bfd-doc">选择文档 *</label>
      <select id="bfd-doc">${docs.map(d => `<option value="${esc(d.doc_name)}">${esc(d.doc_name)}（${d.chunks ?? 0} 块）</option>`).join('')}</select></div>
    <div class="field"><label for="bfd-root">根节点名称（留空 = 不建根，直接挂到挂载位置下）</label>
      <input id="bfd-root" maxlength="50" placeholder="如：判断推理讲义"></div>
    <div class="field"><label for="bfd-parent">挂载到（留空 = 根级）</label>
      <select id="bfd-parent"><option value="">（根级）</option>${knParentOptions('')}</select></div>
    <label style="display:flex;gap:6px;align-items:center;font-weight:600;font-size:13px">
      <input type="checkbox" id="bfd-preview" checked> 先预览 AI 生成结果（推荐，确认后再落库）</label>
    <div id="bfd-result" style="margin-top:10px"></div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('kn-modal')">取消</button>
      <button class="btn primary" id="bfd-btn" onclick="knDoBuildFromDoc()">开始生成</button>
    </div>
    <p class="hint" id="bfd-hint">文档较长时 AI 整理需数十秒至数分钟，请保持窗口打开</p>`);
}

async function knDoBuildFromDoc() {
  const docName = ($('#bfd-doc') && $('#bfd-doc').value) || '';
  if (!docName) { toast('请选择文档', 'err'); return; }
  const preview = $('#bfd-preview').checked;
  const btn = $('#bfd-btn'), hint = $('#bfd-hint'), box = $('#bfd-result');
  btn.disabled = true;
  hint.textContent = 'AI 正在阅读文档并整理知识点…（可能需 1–3 分钟）';
  hint.className = 'hint';
  try {
    const body = {
      teacher_id: knTid(), doc_name: docName,
      root_name: ($('#bfd-root').value || '').trim(),
      parent_id: ($('#bfd-parent') && $('#bfd-parent').value) || '',
      tree_type: _knTreeType, save: !preview,
    };
    const r = await admin('/admin/knowledge/build-from-doc', { method: 'POST', timeout: 600000, body: JSON.stringify(body) });
    if (r.saved) {
      hint.textContent = `✅ 已创建 ${r.created} 个知识节点`;
      hint.className = 'hint ok';
      toast(`文档建树完成：${r.created} 个节点已入库`, 'ok');
      setTimeout(() => { closeModal('kn-modal'); loadKnTree(); }, 800);
    } else {
      hint.textContent = `AI 生成 ${r.would_create} 个节点，请确认后落库`;
      hint.className = 'hint';
      box.innerHTML = `
        <div style="font-size:13px;font-weight:700;margin-bottom:6px">预览（${r.would_create} 节点）：</div>
        <div style="max-height:300px;overflow:auto;border:1px solid var(--line);border-radius:8px;padding:10px">${knPreviewTreeHtml(r.preview_tree)}</div>
        <div class="modal-actions" style="margin-top:10px">
          <button class="btn ghost sm" onclick="knBuildFromDoc()">重新选择</button>
          <button class="btn primary sm" onclick="knConfirmBuildFromDoc('${esc(docName)}')">确认落库（${r.would_create}）</button>
        </div>`;
    }
  } catch (e) {
    hint.textContent = '建树失败：' + e.message;
    hint.className = 'hint err';
  } finally { btn.disabled = false; }
}

async function knConfirmBuildFromDoc(docName) {
  const hint = $('#bfd-hint');
  hint.textContent = '落库中…';
  hint.className = 'hint';
  try {
    const body = {
      teacher_id: knTid(), doc_name: docName,
      root_name: ($('#bfd-root').value || '').trim(),
      parent_id: ($('#bfd-parent') && $('#bfd-parent').value) || '',
      tree_type: _knTreeType, save: true,
    };
    const r = await admin('/admin/knowledge/build-from-doc', { method: 'POST', timeout: 600000, body: JSON.stringify(body) });
    hint.textContent = `✅ 已创建 ${r.created} 个知识节点`;
    hint.className = 'hint ok';
    toast(`文档建树完成：${r.created} 个节点已入库`, 'ok');
    setTimeout(() => { closeModal('kn-modal'); loadKnTree(); }, 800);
  } catch (e) {
    hint.textContent = '落库失败：' + e.message;
    hint.className = 'hint err';
  }
}

async function knBuildFromSearch() {
  if (_knSearchOn === false) {
    toast('服务器未启用联网搜索（SEARCH_PROVIDER=none），请联系管理员配置', 'err');
    return;
  }
  openModal('kn-modal', `
    <h3>🌐 联网搜索建树</h3>
    <p style="font-size:12.5px;color:var(--ink-3);margin-bottom:10px">
      AI 联网搜索公考资料 → 归纳成多级知识点树 → 写入当前${knTypeLabel()}，自动编写完善知识体系。</p>
    <div class="field"><label for="bfs-q">搜索关键词 *</label>
      <input id="bfs-q" maxlength="60" placeholder="如：图形推理 十大考点体系"></div>
    <div class="field"><label for="bfs-root">根节点名称（留空 = 用关键词命名）</label>
      <input id="bfs-root" maxlength="50"></div>
    <div class="field"><label for="bfs-parent">挂载到（留空 = 根级）</label>
      <select id="bfs-parent"><option value="">（根级）</option>${knParentOptions('')}</select></div>
    <div style="display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:600">
      <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="bfs-preview" checked> 先预览 AI 生成结果（推荐）</label>
      <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="bfs-fallback" checked> 搜索失败时降级为纯 AI 生成（不联网）</label>
    </div>
    <div id="bfs-result" style="margin-top:10px"></div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('kn-modal')">取消</button>
      <button class="btn primary" id="bfs-btn" onclick="knDoBuildFromSearch()">开始搜索并生成</button>
    </div>
    <p class="hint" id="bfs-hint">搜索 + AI 归纳约需 30 秒–2 分钟</p>`);
}

async function knDoBuildFromSearch() {
  const q = ($('#bfs-q') && $('#bfs-q').value || '').trim();
  if (!q) { toast('请输入搜索关键词', 'err'); return; }
  const preview = $('#bfs-preview').checked;
  const btn = $('#bfs-btn'), hint = $('#bfs-hint'), box = $('#bfs-result');
  btn.disabled = true;
  hint.textContent = '联网搜索资料中…';
  hint.className = 'hint';
  try {
    const body = {
      teacher_id: knTid(), query: q,
      root_name: ($('#bfs-root').value || '').trim(),
      root_id: ($('#bfs-parent') && $('#bfs-parent').value) || '',
      tree_type: _knTreeType, save: !preview,
      allow_fallback: $('#bfs-fallback').checked,
    };
    hint.textContent = 'AI 归纳知识点中…';
    const r = await admin('/admin/knowledge/build-from-search', { method: 'POST', timeout: 600000, body: JSON.stringify(body) });
    const warn = r.warning ? `（⚠️ ${r.warning}）` : '';
    if (r.saved) {
      hint.textContent = `✅ 基于搜索资料创建 ${r.created} 个节点${warn}`;
      hint.className = r.warning ? 'hint' : 'hint ok';
      toast(`联网搜索建树完成：${r.created} 个节点${warn}`, 'ok');
      setTimeout(() => { closeModal('kn-modal'); loadKnTree(); }, 900);
    } else {
      hint.textContent = `AI 生成 ${r.would_create} 个节点${warn}，请确认后落库`;
      hint.className = 'hint';
      box.innerHTML = `
        <div style="font-size:13px;font-weight:700;margin-bottom:6px">预览（${r.would_create} 节点）：</div>
        <div style="max-height:300px;overflow:auto;border:1px solid var(--line);border-radius:8px;padding:10px">${knPreviewTreeHtml(r.preview_tree)}</div>
        <div class="modal-actions" style="margin-top:10px">
          <button class="btn ghost sm" onclick="knBuildFromSearch()">重新搜索</button>
          <button class="btn primary sm" onclick="knConfirmBuildFromSearch('${esc(q)}')">确认落库（${r.would_create}）</button>
        </div>`;
    }
  } catch (e) {
    hint.textContent = '建树失败：' + e.message;
    hint.className = 'hint err';
  } finally { btn.disabled = false; }
}

async function knConfirmBuildFromSearch(q) {
  const hint = $('#bfs-hint');
  hint.textContent = '落库中…';
  hint.className = 'hint';
  try {
    const body = {
      teacher_id: knTid(), query: q,
      root_name: ($('#bfs-root').value || '').trim(),
      root_id: ($('#bfs-parent') && $('#bfs-parent').value) || '',
      tree_type: _knTreeType, save: true,
      allow_fallback: $('#bfs-fallback').checked,
    };
    const r = await admin('/admin/knowledge/build-from-search', { method: 'POST', timeout: 600000, body: JSON.stringify(body) });
    const warn = r.warning ? `（⚠️ ${r.warning}）` : '';
    hint.textContent = `✅ 已创建 ${r.created} 个节点${warn}`;
    hint.className = r.warning ? 'hint' : 'hint ok';
    toast(`联网搜索建树完成：${r.created} 个节点${warn}`, 'ok');
    setTimeout(() => { closeModal('kn-modal'); loadKnTree(); }, 900);
  } catch (e) {
    hint.textContent = '落库失败：' + e.message;
    hint.className = 'hint err';
  }
}

// ---------- AI 思维导图（#21） ----------
function knMindmap() {
  const opts = knParentOptions('');
  openModal('kn-modal', `
    <h3>🧠 AI 思维导图</h3>
    <p style="font-size:12.5px;color:var(--ink-3);margin-bottom:10px">
      将知识体系树转化为思维导图。AI 会为缺失说明的节点生成一句话考点；导出 Markdown 大纲（.md）、
      FreeMind 标准导图（.mm）或 PNG 图片。</p>
    <div class="field"><label for="kmm-root">导图范围</label>
      <select id="kmm-root"><option value="">（全树）</option>${opts}</select></div>
    <div class="field" style="display:flex;gap:18px;align-items:center">
      <label style="display:flex;gap:6px;align-items:center;font-weight:600"><input type="checkbox" id="kmm-enhance" checked> AI 增强节点说明</label>
      <label style="display:flex;gap:6px;align-items:center;font-weight:600"><input type="checkbox" id="kmm-save"> 增强结果回写知识库</label>
    </div>
    <div id="kmm-result" style="margin-top:10px"></div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('kn-modal')">关闭</button>
      <button class="btn primary" id="kmm-btn" onclick="knGenMindmap()">生成</button>
    </div>
    <p class="hint" id="kmm-hint"></p>`);
}

async function knGenMindmap() {
  const btn = $('#kmm-btn');
  const hint = $('#kmm-hint');
  const enhance = $('#kmm-enhance').checked;
  const rootId = $('#kmm-root').value;
  btn.disabled = true;
  hint.textContent = enhance ? '生成中（AI 增强可能需数十秒）…' : '生成中…';
  try {
    const r = await admin('/admin/knowledge/mindmap', {
      method: 'POST', timeout: 300000,
      body: JSON.stringify({ teacher_id: knTid(), root_id: rootId, save_back: $('#kmm-save').checked }),
    });
    window._kmm = r;  // 缓存供导出用
    hint.textContent = `已生成 ${r.total_nodes} 个节点${enhance ? `（AI 增强 ${r.enhanced} 个说明）` : ''}`;
    $('#kmm-result').innerHTML = `
      <div style="border:1px solid var(--line);border-radius:8px;max-height:220px;overflow:auto;padding:10px;background:var(--paper)" id="kmm-preview"></div>
      <div class="modal-actions" style="margin-top:10px">
        <button class="btn ghost sm" onclick="knExportMm('md')">⬇ Markdown (.md)</button>
        <button class="btn ghost sm" onclick="knExportMm('mm')">⬇ FreeMind (.mm)</button>
        <button class="btn primary sm" onclick="knExportMm('png')">⬇ PNG 图片</button>
      </div>`;
    knRenderPreview(r.tree);
  } catch (e) {
    hint.textContent = '生成失败：' + e.message;
    hint.className = 'hint err';
  } finally { btn.disabled = false; }
}

// 缩进树预览（轻量 DOM，不做 SVG 预览——PNG 导出走独立 SVG 渲染）
function knRenderPreview(tree) {
  const box = $('#kmm-preview');
  if (!box) return;
  const walk = (nodes, depth) => (nodes || []).map(n => `
    <div style="padding:3px 0 3px ${depth * 18}px;font-size:12.5px">
      <b>${esc(n.name)}</b>${n.description ? `<span style="color:var(--ink-3)"> — ${esc(n.description)}</span>` : ''}
    </div>${walk(n.children, depth + 1)}`).join('');
  box.innerHTML = tree.name && tree.node_id
    ? `<div style="font-size:13.5px;font-weight:700;padding:4px 0">🌳 ${esc(tree.name)}</div>${walk(tree.children, 1)}`
    : walk(tree.children, 0) || '<div class="empty">该范围暂无节点</div>';
}

function knExportMm(fmt) {
  const r = window._kmm;
  if (!r) return;
  const safe = (r.title || '知识体系').replace(/[\\/:*?"<>|]/g, '_');
  if (fmt === 'md') knDownload(`${safe}.md`, r.markdown, 'text/markdown;charset=utf-8');
  else if (fmt === 'mm') knDownload(`${safe}.mm`, r.freemind, 'application/xml;charset=utf-8');
  else knExportPng(r.tree, safe);
}

function knDownload(filename, content, mime) {
  const blob = new Blob([content], { type: mime });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

// SVG 思维导图渲染（右侧展开经典布局）→ PNG 导出
// 布局：叶子自上而下顺序分配 y，父节点 y = 子节点 y 均值；层级向右展开
function knBuildSvg(tree) {
  const LH = 30, GAP = 8, COL = 200;
  let leafY = 20;

  const place = (n, depth) => {
    const it = { name: (n.name || '').slice(0, 14), desc: (n.description || '').slice(0, 18), depth, kids: [] };
    it.kids = (n.children || []).map(c => place(c, depth + 1));
    if (!it.kids.length) { it.y = leafY; leafY += LH + GAP; }
    else {
      const ys = it.kids.map(k => k.y);
      it.y = (Math.min(...ys) + Math.max(...ys)) / 2;
    }
    return it;
  };

  const root = { name: tree.name || '知识体系', description: '', children: tree.children };
  const full = place(root, 0);
  const H = Math.max(leafY + 30, 220);
  const maxDepth = Math.max(...(function all(items) {
    return items.reduce((acc, i) => acc.concat(i.depth, all(i.kids)), []);
  })([full]));
  const W = (maxDepth + 1) * COL + 240;

  let out = '';
  const emit = (it) => {
    const x = 20 + it.depth * COL;
    const w = it.depth === 0 ? 130 : 165;
    const isRoot = it.depth === 0;
    out += `<rect x="${x}" y="${it.y - 14}" width="${w}" height="28" rx="6" fill="${isRoot ? '#b8860b' : '#f5efe0'}" stroke="#c9b98a" stroke-width="1"/>` +
      `<text x="${x + 8}" y="${it.y + 4}" font-size="${isRoot ? 13 : 12}" font-weight="${isRoot ? 700 : 500}" fill="${isRoot ? '#fff' : '#4a3f2f'}" font-family="Microsoft YaHei,sans-serif">${esc(it.name)}</text>`;
    if (it.desc) out += `<text x="${x + 8}" y="${it.y + 22}" font-size="9.5" fill="#8a7d63" font-family="Microsoft YaHei,sans-serif">${esc(it.desc)}</text>`;
    it.kids.forEach(k => {
      const kx = 20 + k.depth * COL;
      out += `<path d="M ${x + w} ${it.y} C ${x + w + 40} ${it.y}, ${kx - 40} ${k.y}, ${kx} ${k.y}" fill="none" stroke="#c9b98a" stroke-width="1.2"/>`;
      emit(k);
    });
  };
  emit(full);
  return { svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}"><rect width="${W}" height="${H}" fill="#fffdf6"/>${out}</svg>`, width: W, height: H };
}

function knExportPng(tree, title) {
  try {
    const { svg } = knBuildSvg(tree);
    const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = function () {
      const canvas = document.createElement('canvas');
      canvas.width = img.width || 1200;
      canvas.height = img.height || 800;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#fffdf6';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0);
      canvas.toBlob(b => {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(b);
        a.download = `${title}.png`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      }, 'image/png');
      URL.revokeObjectURL(url);
    };
    img.onerror = () => { toast('PNG 导出失败，请改用 .md / .mm 格式', 'err'); };
    img.src = url;
  } catch (e) { toast('PNG 导出失败：' + e.message, 'err'); }
}

// ============================================================
// 充值码管理（#9 全量交付，适配新主题）
// ============================================================
// ============================================================
// 充值码管理（A3 · #24 R4 扩展：批次/渠道/作废/导入/统计/导出）
// ============================================================
let rcPage = { cur: 1, size: 20, status: 'all', batch: '', channel: '', keyword: '', total: 0 };
let _rcChecked = new Set();
let _rcBatches = [];  // 从统计接口取批次列表
let _rcChannels = [];

async function renderRecharge() {
  const c = $('#content');
  c.innerHTML = `
  <div class="page-head"><h2>充值码管理</h2><p>批量生成 / 导入导出 / 状态筛选与作废 / 渠道与批次统计</p></div>
  <div class="cards" id="rc-stats-cards" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr))"></div>
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap">
        <label>套餐：</label>
        <select id="rc-plan">
          <option value="month">月度 ¥29</option>
          <option value="quarter">季度 ¥79</option>
          <option value="year">年度 ¥299</option>
        </select>
        <label>数量：</label>
        <input id="rc-count" type="number" value="10" min="1" max="500" style="width:70px">
        <input id="rc-batch" placeholder="批次号（可选，如 202609-双11）" style="width:180px" maxlength="40">
        <input id="rc-channel" placeholder="渠道（可选，如 抖音/地推）" style="width:150px" maxlength="20">
        <button class="btn primary" onclick="generateRechargeCodes()">生成充值码</button>
        <span class="hint" id="rc-hint"></span>
      </div>
    </div>
  </div>
  <div class="panel" style="margin-top:14px">
    <div class="panel-head">
      <div class="toolbar" style="flex:1;flex-wrap:wrap">
        <select id="rc-status" onchange="rcFilter()">
          <option value="all">全部状态</option>
          <option value="unused">未使用</option>
          <option value="used">已使用</option>
          <option value="voided">已作废</option>
        </select>
        <select id="rc-batch-f" onchange="rcFilter()"><option value="">全部批次</option></select>
        <select id="rc-channel-f" onchange="rcFilter()"><option value="">全部渠道</option></select>
        <input type="search" id="rc-kw" class="grow" placeholder="按充值码/使用者/备注搜索…" onkeydown="if(event.key==='Enter')rcFilter()">
        <button class="btn ghost" onclick="rcFilter()">搜索</button>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn ghost sm" onclick="rcVoidSelected()">🚫 作废选中</button>
        <button class="btn ghost sm" onclick="rcImportModal()">📥 批量导入</button>
        <button class="btn ghost sm" onclick="rcExportCsv()">📤 导出 CSV</button>
      </div>
    </div>
    <div class="tbl-wrap">
      <table class="tbl">
        <thead><tr>
          <th style="width:34px"><input type="checkbox" onchange="rcCheckAll(this.checked)" aria-label="全选本页"></th>
          <th>充值码</th><th>套餐</th><th>金额(元)</th><th>状态</th><th>批次</th><th>渠道</th><th>使用者</th><th>使用时间</th><th>生成时间</th>
        </tr></thead>
        <tbody id="rc-body"><tr><td colspan="10" class="loading">加载中…</td></tr></tbody>
      </table>
    </div>
    <div class="pager" id="rc-pager"></div>
  </div>
  <div id="rc-modal" class="modal-mask"></div>`;
  $('#rc-status').value = rcPage.status;
  await rcLoadStats();
  await rcLoadCodes();
}

function rcFilter() {
  rcPage.status = $('#rc-status').value;
  rcPage.batch = $('#rc-batch-f').value;
  rcPage.channel = $('#rc-channel-f').value;
  rcPage.keyword = ($('#rc-kw').value || '').trim();
  rcPage.cur = 1;
  _rcChecked.clear();
  rcLoadCodes();
}

async function rcLoadStats() {
  try {
    const s = await admin('/admin/recharge-codes/stats');
    const o = s.overall || {};
    const rate = o.total ? Math.round((o.used / o.total) * 100) : 0;
    $('#rc-stats-cards').innerHTML = `
      <div class="card"><div class="card-label">总量</div><div class="card-n">${o.total ?? 0}</div><div class="card-sub">使用率 ${rate}%</div></div>
      <div class="card"><div class="card-label">已使用</div><div class="card-n green">${o.used ?? 0}</div><div class="card-sub">激活成功</div></div>
      <div class="card"><div class="card-label">未使用</div><div class="card-n gold">${o.unused ?? 0}</div><div class="card-sub">可流通</div></div>
      <div class="card"><div class="card-label">已作废</div><div class="card-n red">${o.voided ?? 0}</div><div class="card-sub">防泄漏止损</div></div>
      <div class="card"><div class="card-label">批次</div><div class="card-n">${(s.by_batch || []).length}</div><div class="card-sub">按批次投放</div></div>
      <div class="card"><div class="card-label">渠道</div><div class="card-n">${(s.by_channel || []).length}</div><div class="card-sub">按渠道分发</div></div>`;
    // 批次/渠道下拉（筛选项来自统计聚合）
    _rcBatches = s.by_batch || [];
    _rcChannels = s.by_channel || [];
    const bSel = $('#rc-batch-f');
    if (bSel) {
      const cur = rcPage.batch;
      bSel.innerHTML = '<option value="">全部批次</option>' + _rcBatches.map(b =>
        `<option value="${esc(b.batch_id)}">${esc(b.batch_id)}（${b.total}）</option>`).join('');
      if ([...bSel.options].some(o => o.value === cur)) bSel.value = cur;
    }
    const cSel = $('#rc-channel-f');
    if (cSel) {
      const cur = rcPage.channel;
      cSel.innerHTML = '<option value="">全部渠道</option>' + _rcChannels.map(ch =>
        `<option value="${esc(ch.channel)}">${esc(ch.channel || '未标记')}（${ch.total}）</option>`).join('');
      if ([...cSel.options].some(o => o.value === cur)) cSel.value = cur;
    }
  } catch (e) { $('#rc-stats-cards') && ($('#rc-stats-cards').innerHTML = ''); }
}

async function rcLoadCodes() {
  const tbody = $('#rc-body');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="10" class="loading">加载中…</td></tr>';
  try {
    const params = new URLSearchParams({
      limit: String(rcPage.size), offset: String((rcPage.cur - 1) * rcPage.size),
      status: rcPage.status,
    });
    if (rcPage.batch) params.set('batch_id', rcPage.batch);
    if (rcPage.channel) params.set('channel', rcPage.channel);
    if (rcPage.keyword) params.set('keyword', rcPage.keyword);
    const r = await admin(`/admin/recharge-codes?${params.toString()}`);
    rcPage.total = r.total ?? (r.codes || []).length;
    const totalPages = Math.max(1, Math.ceil(rcPage.total / rcPage.size));
    if (rcPage.cur > totalPages) rcPage.cur = totalPages;
    renderRechargeRows(r.codes || []);
    renderPager($('#rc-pager'), rcPage.cur, totalPages, (p) => { rcPage.cur = p; rcLoadCodes(); }, rcPage.total);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="10" class="err-tip">加载失败：${esc(e.message)}</td></tr>`;
  }
}

function rcCheckAll(on) {
  $$('#rc-body input[type=checkbox][data-code]').forEach(cb => { cb.checked = on; rcCheck(cb.dataset.code, on); });
}
function rcCheck(code, on) { on ? _rcChecked.add(code) : _rcChecked.delete(code); }

function renderRechargeRows(codes) {
  const tbody = $('#rc-body');
  if (!tbody) return;
  if (!codes.length) { tbody.innerHTML = '<tr><td colspan="10" class="empty">暂无充值码，点击「生成充值码」创建或「批量导入」既有码</td></tr>'; return; }
  const planNames = { month: '月度', quarter: '季度', year: '年度' };
  tbody.innerHTML = codes.map(c => {
    const st = c.used_by ? ['used', 'red', '已使用'] : (c.voided ? ['voided', 'gray', '已作废'] : ['unused', 'green', '未使用']);
    return `
    <tr style="${st[0] === 'voided' ? 'opacity:.55' : ''}">
      <td>${st[0] === 'unused' ? `<input type="checkbox" data-code="${esc(c.code)}" ${_rcChecked.has(c.code) ? 'checked' : ''} onchange="rcCheck('${esc(c.code)}', this.checked)" aria-label="选择充值码">` : ''}</td>
      <td><code class="rc-code" style="cursor:pointer" title="点击复制" onclick="copyCode('${esc(c.code)}')">${esc(c.code)}</code></td>
      <td>${esc(planNames[c.plan] || c.plan)}</td>
      <td>${(c.amount / 100).toFixed(2)}</td>
      <td><span class="badge ${st[1]}">${st[2]}</span></td>
      <td>${c.batch_id ? `<span class="badge blue" title="${esc(c.batch_id)}">${esc(c.batch_id.length > 14 ? c.batch_id.slice(0, 13) + '…' : c.batch_id)}</span>` : '—'}</td>
      <td>${c.channel ? esc(c.channel) : '—'}</td>
      <td>${esc(c.used_by || '—')}</td>
      <td>${c.used_at ? esc(c.used_at.slice(0, 19)) : '—'}</td>
      <td>${c.created_at ? esc(c.created_at.slice(0, 19)) : '—'}</td>
    </tr>`;
  }).join('');
}

async function generateRechargeCodes() {
  const plan = $('#rc-plan').value;
  const count = parseInt($('#rc-count').value) || 10;
  const batch_id = ($('#rc-batch').value || '').trim();
  const channel = ($('#rc-channel').value || '').trim();
  const hint = $('#rc-hint');
  hint.textContent = '生成中…';
  hint.className = 'hint';
  try {
    const r = await admin('/admin/recharge-codes/generate', {
      method: 'POST',
      body: JSON.stringify({ plan, count, batch_id, channel }),
    });
    hint.textContent = `已生成 ${r.count} 个充值码${batch_id ? `（批次 ${batch_id}）` : ''}`;
    renderRecharge();
    const firstCodes = (r.codes || []).slice(0, 10).join('\n');
    if (confirm(`已生成 ${r.count} 个充值码。前 10 个：\n\n${firstCodes}\n\n是否复制全部到剪贴板？`)) {
      copyText((r.codes || []).join('\n'));
      toast('已复制全部充值码', 'ok');
    }
  } catch (e) {
    hint.textContent = '生成失败：' + e.message;
    hint.className = 'hint err';
  }
}

async function rcVoidSelected() {
  const codes = [..._rcChecked];
  if (!codes.length) { toast('请先勾选要作废的未使用充值码', 'err'); return; }
  openModal('rc-modal', `
    <h3>作废充值码</h3>
    <div class="err-tip" style="margin-bottom:12px">确认作废勾选的 ${codes.length} 个充值码？<br>仅未使用码可作废；作废后不可激活、不可恢复。</div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('rc-modal')">取消</button>
      <button class="btn danger" onclick="rcDoVoid(${JSON.stringify(codes).replace(/"/g, '&quot;')}, '')">确认作废</button>
    </div>`);
}

async function rcDoVoid(codesJson, batchId) {
  const codes = typeof codesJson === 'string' ? JSON.parse(codesJson.replace(/&quot;/g, '"')) : codesJson;
  try {
    const r = await admin('/admin/recharge-codes/void', {
      method: 'POST', body: JSON.stringify({ codes: codes || [], batch_id: batchId || '' }) });
    closeModal('rc-modal');
    toast(`已作废 ${r.voided} 个，跳过 ${r.skipped} 个（已使用/不存在）`, 'ok');
    _rcChecked.clear();
    rcLoadStats(); rcLoadCodes();
  } catch (e) { toast('作废失败：' + e.message, 'err'); }
}

function rcImportModal() {
  openModal('rc-modal', `
    <h3>📥 批量导入充值码</h3>
    <p style="font-size:12.5px;color:var(--ink-3);margin-bottom:10px">
      粘贴 CSV 文本，每行一条：<code>充值码,套餐,金额(分,可选)</code>。套餐：month / quarter / year。<br>
      示例：<code>GK-AB12CD,month,2900</code>。重复码自动跳过，格式错误会逐条报明。</p>
    <div class="field"><label for="rci-batch">归入批次（可选）</label><input id="rci-batch" maxlength="40" placeholder="如 202609-外部采购"></div>
    <div class="field"><label for="rci-csv">CSV 内容 *</label>
      <textarea id="rci-csv" rows="8" style="width:100%;font-family:monospace" placeholder="GK-AB12CD,month,2900&#10;GK-EF34GH,quarter"></textarea></div>
    <div class="modal-actions">
      <button class="btn ghost" onclick="closeModal('rc-modal')">取消</button>
      <button class="btn primary" onclick="rcDoImport()">导入</button>
    </div>
    <p class="hint" id="rci-hint"></p>`);
}

async function rcDoImport() {
  const csv = ($('#rci-csv').value || '').trim();
  const batch = ($('#rci-batch').value || '').trim();
  const hint = $('#rci-hint');
  if (!csv) { hint.textContent = '请粘贴 CSV 内容'; hint.className = 'hint err'; return; }
  hint.textContent = '导入中…';
  hint.className = 'hint';
  try {
    const r = await admin('/admin/recharge-codes/import', {
      method: 'POST', body: JSON.stringify({ csv, batch_id: batch }) });
    hint.textContent = `✅ 导入 ${r.imported} 条，重复 ${r.duplicated} 条${(r.invalid || []).length ? '，非法 ' + r.invalid.length + ' 条' : ''}`;
    hint.className = 'hint ok';
    toast(`导入 ${r.imported} 条，重复 ${r.duplicated} 条${(r.invalid || []).length ? '，非法 ' + r.invalid.length + ' 条' : ''}`, 'ok');
    setTimeout(() => { closeModal('rc-modal'); rcLoadStats(); rcLoadCodes(); }, 900);
  } catch (e) {
    hint.textContent = '导入失败：' + e.message;
    hint.className = 'hint err';
  }
}

async function rcExportCsv() {
  try {
    const params = new URLSearchParams({ status: rcPage.status });
    if (rcPage.batch) params.set('batch_id', rcPage.batch);
    if (rcPage.channel) params.set('channel', rcPage.channel);
    const resp = await fetch(`/api/admin/recharge-codes/export?${params.toString()}`, {
      headers: { Authorization: `Bearer ${token}` } });
    if (!resp.ok) { const j = await resp.json().catch(() => ({})); throw new Error(j.detail || ('HTTP ' + resp.status)); }
    const text = await resp.text();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(['\ufeff' + text], { type: 'text/csv;charset=utf-8' }));
    a.download = `充值码_${rcPage.status}_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast('CSV 已导出', 'ok');
  } catch (e) { toast('导出失败：' + e.message, 'err'); }
}

function copyCode(code) {
  copyText(code);
  toast(`已复制：${code}`, 'ok');
}

function copyText(text) {
  if (navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {});
  else {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(ta);
  }
}

// ============================================================
// 答案反馈统计（#9 全量交付，适配新主题）
// ============================================================
async function renderFeedback() {
  const c = $('#content');
  // 先读筛选值再重绘（innerHTML 会销毁 #fb-status，提前取值否则恒为空）
  const sel = ($('#fb-status') && $('#fb-status').value) || '';
  c.innerHTML = `<div class="page-head"><h2>答案反馈</h2><p>用户对回答的评价与建议</p></div><div class="loading">加载中…</div>`;
  try {
    const r = await admin(`/admin/stats/feedback?status=${encodeURIComponent(sel)}`);
    const s = r.stats || {};
    c.innerHTML = `
    <div class="page-head"><h2>答案反馈</h2><p>用户对回答的评价与建议</p></div>
    <div class="cards">
      <div class="card"><div class="card-n">${s.total ?? 0}</div><div class="card-l">总反馈</div></div>
      <div class="card"><div class="card-n green">${s.up ?? 0}</div><div class="card-l">👍 好评</div></div>
      <div class="card"><div class="card-n red">${s.down ?? 0}</div><div class="card-l">👎 差评</div></div>
      <div class="card"><div class="card-n blue">${s.up_pct ?? 0}%</div><div class="card-l">好评率</div></div>
      <div class="card"><div class="card-n ${(s.pending || 0) > 0 ? 'red' : 'green'}">${s.pending ?? 0}</div><div class="card-l">⏳ 待处理</div></div>
    </div>
    <div class="panel">
      <div class="panel-head"><h3>最近反馈</h3>
        <select id="fb-status" onchange="renderFeedback()">
          <option value="" ${sel === '' ? 'selected' : ''}>全部</option>
          <option value="new" ${sel === 'new' ? 'selected' : ''}>待处理</option>
          <option value="resolved" ${sel === 'resolved' ? 'selected' : ''}>已处理</option>
        </select>
      </div>
      <div class="tbl-wrap">
        <table class="tbl">
          <thead><tr><th>评分</th><th>问题</th><th>老师</th><th>原因</th><th>状态</th><th>时间</th><th>操作</th></tr></thead>
          <tbody>${(r.recent || []).map(f => `
            <tr ${f.status === 'new' ? 'style="background:rgba(180,90,74,.06)"' : ''}>
              <td>${f.rating === 'up' ? '👍' : '👎'}</td>
              <td title="${esc(f.answer || '')}">${esc((f.question || '').slice(0, 40))}</td>
              <td><code>${esc(f.teacher_id)}</code></td>
              <td title="${esc(f.reason || '')}">${esc((f.reason || '').slice(0, 40))}</td>
              <td>${f.status === 'resolved'
                ? `<span class="badge green">已处理${f.handler_note ? ` · ${esc((f.handler_note || '').slice(0, 16))}` : ''}</span>`
                : '<span class="badge red">待处理</span>'}</td>
              <td>${f.created_at ? esc(f.created_at.slice(0, 19)) : '—'}</td>
              <td>${f.status === 'new'
                ? `<button class="btn sm ok" onclick="resolveFeedback(${f.id})">标记已处理</button>`
                : `<button class="btn sm ghost" onclick="reopenFeedback(${f.id})">重开</button>`}</td>
            </tr>`).join('') || '<tr><td colspan="7">暂无反馈</td></tr>'}</tbody>
        </table>
      </div>
    </div>`;
  } catch (e) {
    c.innerHTML = `<p class="err">加载失败：${esc(e.message)}</p>`;
  }
}

// #26 R3 标记反馈为已处理（可选备注）
async function resolveFeedback(fid) {
  const note = (window.prompt('处理备注（可选，留空跳过）：', '') || '').trim();
  try {
    await admin(`/admin/feedback/${fid}/status`, { method: 'POST', body: JSON.stringify({ status: 'resolved', note }) });
    toast(`反馈 #${fid} 已标记处理`, 'ok');
    renderFeedback();
  } catch (e) { toast('操作失败：' + e.message, 'err'); }
}

async function reopenFeedback(fid) {
  try {
    await admin(`/admin/feedback/${fid}/status`, { method: 'POST', body: JSON.stringify({ status: 'new' }) });
    toast(`反馈 #${fid} 已重开为待处理`, 'ok');
    renderFeedback();
  } catch (e) { toast('操作失败：' + e.message, 'err'); }
}

// ============================================================
// C4 数据备份 / 恢复
// ============================================================
async function renderBackups() {
  const c = $('#content');
  c.innerHTML = `<div class="page-head"><h2>数据备份</h2><p>打包 raw 源文件 + 数据库为 tar.gz，可下载留档或恢复源文件</p></div><div class="loading">加载中…</div>`;
  try {
    const r = await admin('/admin/backups');
    const u = r.usage || {};
    const list = r.backups || [];
    const total = fmtSize(u.total_bytes || 0);
    c.innerHTML = `
    <div class="page-head"><h2>数据备份</h2><p>打包 raw 源文件 + 数据库（auth/teachers/questions/pay）为 tar.gz；向量库与模型缓存不打包（可重建）</p></div>
    <div class="cards">
      <div class="card"><div class="card-n">${u.count || 0}</div><div class="card-l">归档数量</div></div>
      <div class="card"><div class="card-n">${total}</div><div class="card-l">归档总大小</div></div>
      <div class="card"><div class="card-n green">${esc(u.newest || '—')}</div><div class="card-l">最近归档</div></div>
      <div class="card"><div class="card-n">${esc(u.oldest || '—')}</div><div class="card-l">最早归档</div></div>
    </div>
    <div class="panel">
      <div class="panel-head">
        <h3>归档列表</h3>
        <div class="panel-actions">
          <button class="btn primary" onclick="createBackup()">＋ 立即备份</button>
        </div>
      </div>
      <div class="tbl-wrap">
        <table class="tbl">
          <thead><tr><th>归档文件</th><th>大小</th><th>创建时间</th><th>操作</th></tr></thead>
          <tbody>${list.map(b => `
            <tr>
              <td><code>${esc(b.name)}</code></td>
              <td>${fmtSize(b.size)}</td>
              <td>${fmtTime(b.mtime)}</td>
              <td>
                <button class="btn sm" onclick="downloadBackup('${esc(b.name)}')">⬇ 下载</button>
                <button class="btn sm ghost" onclick="restoreBackup('${esc(b.name)}')">↩ 恢复源文件</button>
              </td>
            </tr>`).join('') || '<tr><td colspan="4">暂无备份，点击「立即备份」创建</td></tr>'}</tbody>
        </table>
      </div>
    </div>
    <div class="hint" style="margin-top:8px;color:#888;font-size:12.5px">⚠ 恢复仅解压 raw 源文件（同名覆盖），不改动在线数据库/向量库，避免数据不一致；如需重建向量库请在文档管理重新入库。</div>`;
  } catch (e) {
    c.innerHTML = `<p class="err">加载失败：${esc(e.message)}</p>`;
  }
}

async function createBackup() {
  if (!window.confirm('创建完整数据备份？（raw 源文件 + 全部数据库，耗时数秒）')) return;
  try {
    const r = await admin('/admin/backups', { method: 'POST' });
    toast(`备份完成：${r.name}（${fmtSize(r.size)}）`, 'ok', 4000);
    renderBackups();
  } catch (e) { toast('备份失败：' + e.message, 'err'); }
}

async function downloadBackup(name) {
  try {
    const resp = await fetch(`/api/admin/backups/${encodeURIComponent(name)}/download`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!resp.ok) { toast('下载失败 HTTP ' + resp.status, 'err'); return; }
    const blob = await resp.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 800);
    toast('已开始下载 ' + name, 'ok');
  } catch (e) { toast('下载失败：' + e.message, 'err'); }
}

async function restoreBackup(name) {
  if (!window.confirm(`恢复 ${name} 的 raw 源文件？\n仅恢复源文件，数据库/向量库不受影响（同名文件将被覆盖）。`)) return;
  try {
    const r = await admin('/admin/backups/restore', { method: 'POST', body: JSON.stringify({ name }) });
    toast(`已恢复 ${r.restored_files} 个源文件（跳过 ${r.skipped}）`, 'ok', 4000);
    renderBackups();
  } catch (e) { toast('恢复失败：' + e.message, 'err'); }
}

// ============================================================
// 激励体系（#18：配置 / 统计 / 排行榜 / 重算）
// 数据源：GET /admin/incentive/stats，写操作 POST /admin/incentive/config|recompute
// ============================================================
const INC_EVENT_NAMES = {
  answer: '答题', correct: '答对', daily_first: '今日首练', review_item: '复习巩固',
  mistake_clear: '攻克错题', streak_3: '连续3天', streak_7: '连续7天', streak_30: '连续30天',
};

async function renderIncentive() {
  const c = $('#content');
  c.innerHTML = `<div class="page-head"><h2>🏆 激励体系</h2><p>积分/等级/连续学习/成就 · 独立 incentive.db，事件源 study.db（增量记账 + 幂等重算）</p></div><div class="loading">加载中…</div>`;
  try {
    const d = await admin('/admin/incentive/stats');
    const cfg = d.config || {};
    const st = d.stats || {};
    const ev = cfg.events || {};

    const eventRows = Object.keys(ev).map(k => `
      <div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px dashed #eef0f5">
        <label for="ev-${esc(k)}" style="min-width:110px;font-size:13px">${esc(INC_EVENT_NAMES[k] || k)}</label>
        <input id="ev-${esc(k)}" type="number" min="0" max="1000" value="${Number(ev[k]) || 0}"
          style="width:72px;padding:4px 6px;border:1px solid #d0d4de;border-radius:6px;font-size:13px">
        <code style="font-size:11px;color:#999">${esc(k)}</code>
      </div>`).join('');

    const topRows = (st.top || []).map((t, i) => `
      <tr>
        <td>${i + 1}</td>
        <td><code>${esc(t.user_id)}</code></td>
        <td><b>${fmtNum(t.points)}</b></td>
        <td>Lv.${t.level ?? 1}</td>
        <td>${t.streak_days ?? 0} 天</td>
        <td>${t.total_answers ?? 0} 题</td>
      </tr>`).join('');

    const boardRows = (d.board || []).map((b, i) => `
      <tr>
        <td>${i + 1}</td>
        <td><code>${esc(b.user_id)}</code></td>
        <td><b>${fmtNum(b.points)}</b></td>
        <td>Lv.${b.level ?? 1}</td>
        <td>${b.streak_days ?? 0} 天</td>
        <td>${b.total_answers ?? 0} 题</td>
        <td>${b.solved_mistakes ?? 0}</td>
        <td>${b.updated_at ? esc(b.updated_at.slice(0, 19)) : '—'}</td>
        <td><button class="btn sm ghost" onclick="recomputeIncentive('${esc(b.user_id)}')">重算</button></td>
      </tr>`).join('') || '<tr><td colspan="9">暂无用户积分数据</td></tr>';

    c.innerHTML = `
    <div class="page-head"><h2>🏆 激励体系</h2><p>积分/等级/连续学习/成就 · 独立 incentive.db，事件源 study.db（增量记账 + 幂等重算）</p></div>
    <div class="cards">
      <div class="card"><div class="card-n">${st.users_with_points ?? 0}</div><div class="card-l">有积分用户</div></div>
      <div class="card"><div class="card-n blue">${fmtNum(st.total_points ?? 0)}</div><div class="card-l">总发放积分</div></div>
      <div class="card"><div class="card-n green">${st.avg_points ?? 0}</div><div class="card-l">人均积分</div></div>
      <div class="card"><div class="card-n gold">${st.achievements_granted ?? 0}</div><div class="card-l">已颁发成就</div></div>
      <div class="card"><div class="card-n ${cfg.enabled ? 'green' : 'red'}">${cfg.enabled ? '开启' : '关闭'}</div><div class="card-l">体系总开关</div></div>
    </div>
    <div class="panel">
      <div class="panel-head"><h3>⚙️ 配置</h3>
        <div class="panel-actions">
          <label style="font-size:13px;display:flex;align-items:center;gap:6px;margin-right:10px">
            <input id="incEnabled" type="checkbox" ${cfg.enabled ? 'checked' : ''}> 启用激励
          </label>
          <button class="btn primary" onclick="saveIncentiveConfig()">💾 保存配置</button>
        </div>
      </div>
      <div class="panel-body">
        <div style="font-size:12px;color:#888;margin-bottom:6px">各事件积分（0-1000，保存时仅合法的键/越界值被采纳，其余保持默认）</div>
        <div style="max-width:560px">${eventRows}</div>
        <div style="font-size:11.5px;color:#aaa;margin-top:6px">配置更新时间：${cfg.updated_at ? esc(cfg.updated_at.slice(0, 19)) : '—'}</div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-head"><h3>👑 积分榜 TOP5</h3></div>
      <div class="tbl-wrap">
        <table class="tbl">
          <thead><tr><th>#</th><th>用户</th><th>积分</th><th>等级</th><th>连续</th><th>答题</th></tr></thead>
          <tbody>${topRows}</tbody>
        </table>
      </div>
    </div>
    <div class="panel">
      <div class="panel-head"><h3>📊 排行榜 TOP20</h3>
        <div class="panel-actions">
          <button class="btn ghost" onclick="recomputeIncentive('')">⟳ 全量重算</button>
        </div>
      </div>
      <div class="tbl-wrap">
        <table class="tbl">
          <thead><tr><th>#</th><th>用户</th><th>积分</th><th>等级</th><th>连续</th><th>答题</th><th>攻克错题</th><th>更新时间</th><th>操作</th></tr></thead>
          <tbody>${boardRows}</tbody>
        </table>
      </div>
    </div>
    <div class="hint" style="margin-top:8px;color:#888;font-size:12.5px">⚠ 重算会清空用户激励数据后按练习时间序重放（幂等），用于历史数据纠偏；有积分的用户数很大时全量重算可能较慢。</div>`;
  } catch (e) {
    c.innerHTML = `<p class="err">加载失败：${esc(e.message)}</p>`;
  }
}

async function saveIncentiveConfig() {
  const enabled = !!(document.getElementById('incEnabled') && document.getElementById('incEnabled').checked);
  const event_points = {};
  Object.keys(INC_EVENT_NAMES).forEach(k => {
    const el = document.getElementById('ev-' + k);
    if (el) event_points[k] = parseInt(el.value, 10) || 0;
  });
  try {
    await admin('/admin/incentive/config', { method: 'POST', body: JSON.stringify({ enabled, event_points }) });
    toast('激励配置已保存', 'ok');
    renderIncentive();
  } catch (e) { toast('保存失败：' + e.message, 'err'); }
}

async function recomputeIncentive(userId) {
  const scope = userId ? `用户 ${userId}` : '全部用户';
  if (!window.confirm(`按练习记录重算 ${scope} 的激励数据？（清空后幂等重放）`)) return;
  try {
    const r = await admin('/admin/incentive/recompute', {
      method: 'POST',
      body: JSON.stringify({ user_id: userId || '' }),
    });
    const detail = (r.results || []).slice(0, 5).map(x => `${x.user_id} → ${x.points}分/${x.events}条`).join('；');
    toast(`重算完成：${r.recomputed} 人。${detail}${(r.results || []).length > 5 ? '…' : ''}`, 'ok', 5000);
    renderIncentive();
  } catch (e) { toast('重算失败：' + e.message, 'err'); }
}

// ============================================================
// 启动引导
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
  if (!token) { showLogin(); return; }
  if (window.GK && GK.store) GK.store.token = token;  // 持久化 token 注入传输层，/me 验证才不会 401
  const boot = (attempt) => {
    GK.api('/me').then(r => {
      if (r && r.role === 'admin') { showApp(); }
      else { token = ''; localStorage.removeItem('admin_token'); showLogin('登录状态已失效，请重新登录。'); }
    }).catch(err => {
      const st = err && err.status;
      if (st === 401 || st === 403) {
        token = ''; localStorage.removeItem('admin_token'); showLogin('登录已过期或未授权，请重新登录。');
        return;
      }
      if (attempt < 2) {
        setTimeout(() => boot(attempt + 1), 1200);
      } else {
        showLogin('服务连接异常，登录状态已保留。请检查网络后刷新页面重试。');
      }
    });
  };
  const holder = $('#app');
  if (holder) holder.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;color:var(--ink-3,#888)">正在验证登录…</div>';
  boot(0);
});

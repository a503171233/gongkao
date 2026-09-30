// ============================================================
// chat-boot.js · 页面引导层（批次27-J 自 chat.html 内联拆出，逻辑零改动）
// 内容：知识库上传(B2) / 全局薄封装 / 登录态启动编排(?panel ?kp ?tab 直达)
//      / 侧栏书卷信息。依赖：GK(a2/a4) 与 C4~C12（defer 顺序保证先于本文件）。
// ============================================================
// ---------- 知识库管理（B2/上传入库，保留原逻辑；会话/问答由 A3/A5 接管） ----------
document.getElementById('uploadBtn').onclick = async () => {
  const f = document.getElementById('fileInput').files[0];
  if (!f) return;
  const st = document.getElementById('uploadStatus');
  st.textContent = '上传中…';
  const fd = new FormData();
  fd.append('file', f);

  const doUpload = async (secret) => {
    const headers = {};
    if (secret) headers['X-Upload-Secret'] = secret;
    const r = await fetch(`/api/upload?teacher_id=${GK.store.teacherId}`, {
      method: 'POST', body: fd, headers
    });
    if (r.status === 403 && !secret) return null;  // 需要密钥
    if (r.status === 403) throw new Error('密钥无效');
    return r.json();
  };

  try {
    let d = await doUpload(localStorage.getItem('uploadSecret') || '');
    if (d === null) {
      // 后端要求密钥：询问并记住
      const secret = prompt('该平台上传需密钥（请向管理员索取）：');
      if (!secret) { st.textContent = '已取消'; return; }
      localStorage.setItem('uploadSecret', secret);
      d = await doUpload(secret);
    }
    st.textContent = `✅ ${d.filename}: ${d.chunks} 块入库，共 ${d.total} 块`;
    GK.loadInfo();  // 刷新当前老师知识库规模（A2）
  } catch (e) {
    if (e.message === '密钥无效') localStorage.removeItem('uploadSecret');
    st.textContent = '❌ 上传失败: ' + e.message;
  }
};

// ---------- 全局薄封装（兼容 a1-auth.js logout 的联动调用） ----------
function newSession() { if (window.GK && GK.newSession) GK.newSession(); }
function loadSessions() { if (window.GK && GK.loadSessions) GK.loadSessions(); }

// ================= 登录/注册/账号（A1 模块：js/a1-auth.js） =================
// index.html 只保留模态框事件委托与启动调用；业务逻辑在 GK.* 方法内。
// 兼容旧调用名：保留薄封装，保证行内历史代码/内联 onclick 仍可用。
function openAuthModal(mode) { if (window.GK && GK.openAuthModal) GK.openAuthModal(mode); }
function closeAuthModal() { if (window.GK && GK.closeAuthModal) GK.closeAuthModal(); }
function authAction() { if (window.GK && GK.authAction) GK.authAction(); }
function giteeLogin() { if (window.GK && GK.giteeLogin) GK.giteeLogin(); }
function loadAuthUser() { if (window.GK && GK.loadAuthUser) GK.loadAuthUser(); }
function renderAuthArea() { if (window.GK && GK.renderAuthArea) GK.renderAuthArea(); }
function logout() { if (window.GK && GK.logout) GK.logout(); }

// 模态框事件绑定（业务在 GK）
// 批次27-M1 去重：提交统一收敛到 form.onsubmit 一处——原先按钮 onclick +
// form onsubmit（按钮默认 type=submit，点击会再次触发 submit）+ 密码框
// Enter onkeydown 三路叠加，单击发 2~3 次 /api/login（注册场景有重复建号风险）。
const authSubmitBtn = document.getElementById('authSubmitBtn');
const authSwitchEl = document.getElementById('authSwitch');
const authModalEl = document.getElementById('authModal');
const authFormEl = document.getElementById('authForm');
if (authFormEl) authFormEl.onsubmit = (e) => {
  e.preventDefault();                       // 拦截原生刷新
  if (authFormEl.dataset.busy === '1') return;   // 提交中防抖（连点/回车均吞掉）
  authFormEl.dataset.busy = '1';
  if (authSubmitBtn) authSubmitBtn.disabled = true;  // 请求期间禁用按钮
  Promise.resolve(window.GK && GK.authAction ? GK.authAction() : null).finally(function () {
    authFormEl.dataset.busy = '';
    if (authSubmitBtn) authSubmitBtn.disabled = false;
  });
};
if (authSwitchEl) authSwitchEl.onclick = () => openAuthModal(
  (authSwitchEl.textContent || '').indexOf('去注册') !== -1 ? 'register' : 'login');
if (authModalEl) authModalEl.addEventListener('click', e => {
  if (e.target.id === 'authModal') closeAuthModal();
});

// 启动编排（A2/A3/A5 defer 已先执行，DOM 就绪后绑定）
document.addEventListener('DOMContentLoaded', function () {
  if (window.GK && GK.initTeacher) GK.initTeacher();  // A2 自举兜底，此处幂等
  if (window.GK && GK.initSession) GK.initSession();  // A3 编排（幂等）
  if (window.C7 && typeof C7.init === 'function') { try { C7.init(); } catch (e) {} }  // #19 消息角标轮询
  try {
    var hasToken = !!(localStorage.getItem('gk_token') || localStorage.getItem('authToken'));
    // #6 管理员入口不暴露给未登录学员（接口本有权限拦截，仅修 UI 越权展示）
    var uploadbar = document.getElementById('uploadbar');
    if (uploadbar) uploadbar.style.display = hasToken ? '' : 'none';
    if (hasToken && new URLSearchParams(location.search).get('mt') === 'mock' &&
        window.C5 && typeof C5.openMockExam === 'function') {
      setTimeout(function () { try { C5.openMockExam(); } catch (e) {} }, 900);
    }
    // 批次19/21：知识页「去练习」→ /chat.html?kp=<node_id> 自动抽题；
    // 申论/面试叶子 → ?essay=1 / ?interview=1 直接打开对应练习面板
    var sp = new URLSearchParams(location.search);
    var kpNode = hasToken ? sp.get('kp') : null;
    if (kpNode && window.C4 && typeof C4.practiceByKnowledge === 'function') {
      setTimeout(function () {
        try {
          C4.practiceByKnowledge(kpNode);
          history.replaceState(null, '', location.pathname);  // 清参数，刷新不重放
        } catch (e) {}
      }, 900);
    } else if (hasToken && sp.get('essay') === '1' && window.C11 &&
               typeof C11.openEssay === 'function') {
      setTimeout(function () {
        try {
          C11.openEssay();
          history.replaceState(null, '', location.pathname);
        } catch (e) {}
      }, 900);
    } else if (hasToken && sp.get('interview') === '1' && window.C12 &&
               typeof C12.openInterview === 'function') {
      setTimeout(function () {
        try {
          C12.openInterview();
          history.replaceState(null, '', location.pathname);
        } catch (e) {}
      }, 900);
    }
    // 批次27-D：学员中心统一入口 —— /chat.html?panel=<key> 直开对应功能浮层
    var PANEL_MAP = {
      practice:   function () { C4.openPractice(); },
      stats:      function () { C4.openReport(); },
      favorites:  function () { C4.openFavorites(); },
      mistakes:   function () { C4.openMistakes(); },
      mock:       function () { C5.openMockExam(); },
      smart:      function () { C6.openSmartExam(); },
      incentive:  function () { C8.openIncentive(); },
      report:     function () { C9.openReport(); },
      fenbi:      function () { C10.openFenbi(); },
      essay:      function () { C11.openEssay(); },
      interview:  function () { C12.openInterview(); },
      messages:   function () { C7.openMessages(); }
    };
    var pk = hasToken ? sp.get('panel') : null;
    if (pk && PANEL_MAP[pk] && !kpNode) {   // kp 参数优先（知识页直达抽题场景）
      setTimeout(function () {
        try {
          PANEL_MAP[pk]();
          history.replaceState(null, '', location.pathname);  // 清参数，刷新不重放
        } catch (e) {}
      }, 900);
    }
    // 批次27-I4：底部 tab 跨页直达 —— forum.html 等页面 tabbar 跳 /chat.html?tab=bank|exam|me|forum
    // 批次27-I5：去掉 300ms 延迟——body class 已在 <body> 后同步预置（首帧即目标页），
    // 这里同步执行 TAB.go 只做幂等确认+触发数据加载+tabbar 高亮，无闪烁
    var tb = sp.get('tab');
    if (tb === 'forum') { location.replace('/forum.html'); return; }
    if (tb === 'bank' || tb === 'exam' || tb === 'me') {
      try {
        TAB.go(tb);
        history.replaceState(null, '', location.pathname);  // 清参数，刷新不重放
      } catch (e) {}
    }
  } catch (e) {}
});

// ============ 侧栏书卷信息（官网→主站联动） ============
// 监听 teacher:changed，拉取当前老师 /info + /documents，渲染 #sideVol 书卷区。
(function () {
  'use strict';
  var svName = document.getElementById('svName');
  var svBody = document.getElementById('svBody');
  if (!svName || !svBody) return;  // 元素缺失则不启用（幂等）

  function shortName(n) {
    var s = String(n || '');
    return s.replace(/^【[^】]*】/, '').replace(/\[副本\]/g, '').replace(/\.(pdf|md|docx?|pptx?)$/i, '').slice(0, 22);
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  async function renderSideVol(tid) {
    var teacherId = tid || (window.GK && GK.store.teacherId) || 'T001';
    svName.textContent = '书卷加载中…';
    svBody.innerHTML = '';
    try {
      // 并行拉老师信息与书卷列表
      var infoP = window.GK && GK.api
        ? GK.api('/info?teacher_id=' + encodeURIComponent(teacherId))
        : Promise.reject(new Error('no api'));
      var docsP = window.GK && GK.api
        ? GK.api('/documents?teacher_id=' + encodeURIComponent(teacherId))
        : Promise.reject(new Error('no api'));
      var info = null, docs = [];
      try { info = await infoP; } catch (e) {}
      try { var d = await docsP; docs = (d && d.documents) || []; } catch (e) {}

      svName.textContent = info && info.teacher_name
        ? info.teacher_name + ' · ' + (info.teacher_subject || '')
        : teacherId;
      svName.title = teacherId;

      if (!docs.length) {
        svBody.innerHTML = '<div class="sv-empty">该老师书卷编目中，先来提问吧。</div>';
        return;
      }
      var html = '';
      var shown = 0;
      docs.forEach(function (doc) {
        if (!doc || !doc.doc_name) return;
        if (shown >= 8) return;
        shown++;
        html +=
          '<div class="sv-vol">' +
            '<span class="sv-vname" title="' + esc(doc.doc_name) + '">' + esc(shortName(doc.doc_name)) + '</span>' +
            '<span class="sv-vchunks">' + (doc.chunks || 0) + ' 块</span>' +
          '</div>';
      });
      if (docs.length > shown) {
        html += '<div class="sv-more">… 等 ' + docs.length + ' 部书卷</div>';
      }
      svBody.innerHTML = html;
    } catch (e) {
      console.warn('[sideVol] 渲染失败:', e && e.message ? e.message : e);
      svBody.innerHTML = '<div class="sv-empty">书卷加载失败，请稍后刷新。</div>';
    }
  }

  // 监听切换事件（A2 在 switchTeacher 成功后广播）
  if (window.GK && GK.bus) {
    GK.bus.addEventListener('teacher:changed', function (ev) {
      renderSideVol(ev && ev.detail && ev.detail.teacherId);
    });
  }
  // 页面就绪后渲染一次（对应默认老师 / URL 直达老师）
  setTimeout(function () { renderSideVol(); }, 50);
})();

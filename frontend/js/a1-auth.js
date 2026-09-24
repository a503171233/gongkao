/* =====================================================================
 * A1 · 用户管理模块（网站一 前端 账号身份管理）
 * ---------------------------------------------------------------------
 * 职责：本站密码注册/登录、Gitee 第三方登录、token 持久化、
 *       当前用户信息与剩余额度展示、登出。
 *       未登录时允许匿名体验（匿名不限额度，既有策略）。
 *
 * 强制约束（违反就重做）：
 *   - 匿名不限额是既有策略，本模块不得要求匿名登录/打断匿名体验。
 *   - Authorization 头由 A4 (GK.api/GK.sse) 统一加，本模块只负责存 token，
 *     不直接发带 token 的业务请求（只调 GK.api）。
 *   - Gitee 登录是整页跳转（307/302），不是 fetch；回调地址带 /api。
 *   - 纯原生 JS，无第三方依赖，无构建步骤。
 *   - 不修改 A2/A3/A4/A5 的模块文件。
 *
 * 共享约定：
 *   - 写入 GK.store.token（A4 加 Authorization: Bearer 的唯一来源）。
 *   - token 变化后广播：
 *       GK.bus.dispatchEvent(new CustomEvent('auth:changed', {detail:{token}}))
 *   - localStorage key：gk_token（兼容旧 key authToken）。
 *
 * 对外能力（与 index.html 调用点一致）：
 *   GK.openAuthModal(mode)  —— 打开登录/注册模态框
 *   GK.closeAuthModal()     —— 关闭模态框
 *   GK.authAction()         —— 提交登录/注册
 *   GK.giteeLogin()         —— Gitee 整页跳转（enabled=false 提示"暂未开放"）
 *   GK.loadAuthUser()       —— 启动恢复登录态：token → /me → 渲染
 *   GK.renderAuthArea()     —— 渲染右上角账号区（未登录/已登录两态）
 *   GK.logout()             —— 登出：清 token/localStorage + 广播 + 联动 A3
 * ===================================================================== */
(function (global) {
  'use strict';

  // -----------------------------------------------------------------
  // 1. 命名空间兜底（与 A2/A4 一致，避免依赖加载顺序）
  // -----------------------------------------------------------------
  if (!global.GK) global.GK = {};
  if (!global.GK.store) {
    global.GK.store = { token: '', teacherId: '', sessionId: '' };
  }
  if (!global.GK.bus) {
    global.GK.bus = (typeof EventTarget !== 'undefined')
      ? new EventTarget()
      : null;
  }

  // localStorage key（可兼容旧 key authToken，迁移时读旧写新）
  var TOKEN_KEY = 'gk_token';
  var LEGACY_KEY = 'authToken';

  var authMode = 'login';   // login | register
  var authUser = null;      // { username, role, quota_left }（来自 /me）

  // -----------------------------------------------------------------
  // 2. token 读写（统一入口：写 localStorage + GK.store + 广播）
  // -----------------------------------------------------------------
  function readToken() {
    var t = global.localStorage.getItem(TOKEN_KEY);
    if (!t) t = global.localStorage.getItem(LEGACY_KEY);  // 兼容旧 key
    return t || '';
  }

  function setToken(t, user) {
    t = t || '';
    user = user || null;
    if (t) {
      global.localStorage.setItem(TOKEN_KEY, t);
      global.localStorage.removeItem(LEGACY_KEY);  // 迁移：清旧 key
    } else {
      global.localStorage.removeItem(TOKEN_KEY);
      global.localStorage.removeItem(LEGACY_KEY);
    }
    global.GK.store.token = t;
    // 同步当前用户 id（A3 会话列表归属用；匿名为 undefined）
    if (user && user.user_id) global.GK.store.userId = user.user_id;
    else delete global.GK.store.userId;
    // 广播 auth:changed（A3 监听后按 user_id 刷新会话列表）
    if (global.GK.bus) {
      try {
        global.GK.bus.dispatchEvent(new CustomEvent('auth:changed', {
          detail: { token: t, user: user }
        }));
      } catch (e) { /* 事件失败不影响主流程 */ }
    }
  }

  // -----------------------------------------------------------------
  // 3. 模态框开关
  // -----------------------------------------------------------------
  function getModal() { return global.document.getElementById('authModal'); }
  function getErrEl() { return global.document.getElementById('authErr'); }
  function getOkEl() { return global.document.getElementById('authOk'); }

  function openAuthModal(mode) {
    authMode = (mode === 'register') ? 'register' : 'login';
    var titleEl = global.document.getElementById('authTitle');
    var submitBtn = global.document.getElementById('authSubmitBtn');
    var switchEl = global.document.getElementById('authSwitch');
    if (titleEl) titleEl.textContent = authMode === 'login' ? '登录' : '注册';
    if (submitBtn) submitBtn.textContent = authMode === 'login' ? '登录' : '注册';
    if (switchEl) switchEl.textContent = authMode === 'login' ? '没有账号？去注册' : '已有账号？去登录';
    var errEl = getErrEl(); if (errEl) errEl.textContent = '';
    var okEl = getOkEl(); if (okEl) okEl.classList.add('hidden');
    var modal = getModal();
    if (modal) modal.classList.remove('hidden');
    var uEl = global.document.getElementById('authUsername');
    if (uEl) uEl.focus();
  }

  function closeAuthModal() {
    var modal = getModal();
    if (modal) modal.classList.add('hidden');
  }

  // -----------------------------------------------------------------
  // 4. Gitee 第三方登录：探测 status → enabled 整页跳转；否则提示
  //    （整页跳转是 307/302，不是 fetch；回调带回 ?token= 由 OAuth 回调处理）
  // -----------------------------------------------------------------
  async function giteeLogin() {
    var errEl = getErrEl();
    if (errEl) errEl.textContent = '';
    try {
      var d = await global.GK.api('/auth/gitee/status');
      if (d && d.enabled) {
        global.window.location.href = '/api/auth/gitee/login';
      } else {
        if (errEl) errEl.textContent = 'Gitee 登录暂未开放';
      }
    } catch (e) {
      if (errEl) errEl.textContent = '网络错误：' + (e && e.message ? e.message : e);
    }
  }

  // -----------------------------------------------------------------
  // 5. 提交登录/注册（走 GK.api，只调注册/登录接口；业务请求由 A4 带头）
  // -----------------------------------------------------------------
  async function authAction() {
    var u = global.document.getElementById('authUsername').value.trim();
    var p = global.document.getElementById('authPassword').value;
    var errEl = getErrEl();
    var okEl = getOkEl();
    if (errEl) errEl.textContent = '';
    if (!u || !p) {
      if (errEl) errEl.textContent = '请输入用户名和密码';
      return;
    }
    if (p.length < 6) {
      if (errEl) errEl.textContent = '密码至少 6 位';
      return;
    }
    var path = authMode === 'login' ? '/login' : '/register';
    try {
      var d = await global.GK.api(path, {
        method: 'POST',
        body: { username: u, password: p }
      });
      // 成功：存 token（setToken 会写 store + 广播 auth:changed + 同步 userId）
      authUser = { user_id: d.user_id, username: d.username || u, role: d.role || 'free',
                   quota_left: (d.quota_left !== undefined ? d.quota_left : null) };
      setToken(d.token, authUser);
      if (authMode === 'register') {
        // 注册成功自动登录：显示提示 → 短暂延迟关模态 + 渲染
        // 注意：POST /register 契约响应不含 quota_left，需补一次 /me 拿额度
        if (okEl) {
          okEl.textContent = '注册成功！已自动登录';
          okEl.classList.remove('hidden');
        }
        global.GK.api('/me').then(function (me) {
          if (me && !me.anonymous) {
            authUser = { user_id: me.user_id, username: me.username, role: me.role,
                         quota_left: me.quota_left };
            if (me.user_id) global.GK.store.userId = me.user_id;
          }
        }).catch(function () { /* 静默：保留注册响应里的基础信息 */ });
        global.setTimeout(function () {
          closeAuthModal();
          renderAuthArea();
          checkMembershipReminder();
        }, 900);
      } else {
        closeAuthModal();
        renderAuthArea();
        checkMembershipReminder();
      }
    } catch (e) {
      // 错误提示（错密码 401 detail、用户名重复 400 detail 由 A4 透传）
      var msg = (e && e.message) ? e.message : '操作失败';
      if (errEl) errEl.textContent = msg;
    }
  }

  // -----------------------------------------------------------------
  // 6. OAuth 回调：启动时解析 URL ?token= → 存 → 清地址栏 → 刷新
  // -----------------------------------------------------------------
  function applyOauthToken() {
    try {
      var params = new URLSearchParams(global.window.location.search);
      var t = params.get('token');
      if (t) {
        setToken(t);
        // 清除地址栏 token，防泄漏
        global.history.replaceState(null, '', global.window.location.pathname);
      }
    } catch (e) { /* 忽略 */ }
  }

  // -----------------------------------------------------------------
  // 7. 恢复登录态：有 token → GET /me 校验 → 渲染；无效则清 token
  // -----------------------------------------------------------------
  async function loadAuthUser() {
    var token = readToken();
    if (token) setToken(token, null);  // 先同步 store（user 未知，等 /me 回填）
    if (!global.GK.store.token) { renderAuthArea(); return; }
    try {
      // /me 需要 Bearer 头，A4 会自动从 GK.store.token 加
      var d = await global.GK.api('/me');
      if (d && d.anonymous) {
        // token 无效/已过期：清空回匿名
        setToken('');
        authUser = null;
      } else if (d) {
        authUser = { user_id: d.user_id, username: d.username, role: d.role, quota_left: d.quota_left };
        // 回填 userId 到 store（A3 会话列表归属）——只更新 store 不重复广播
        if (d.user_id) global.GK.store.userId = d.user_id;
      }
    } catch (e) {
      // 后端未连：静默（保留本地 token，下次再试）
    }
    renderAuthArea();
    // #14 到期提醒：登录态恢复后检查会员到期（≤7 天提醒）
    checkMembershipReminder();
  }

  // -----------------------------------------------------------------
  // 8. 渲染账号区：已登录（用户名+额度+退出）/ 未登录（登录+免费注册）
  // -----------------------------------------------------------------
  function renderAuthArea() {
    var area = global.document.getElementById('authArea');
    if (!area) return;
    area.innerHTML = '';
    if (authUser && !authUser.anonymous) {
      var badge = global.document.createElement('span');
      badge.className = 'user-badge';
      var quotaHtml = '';
      // 额度为 0 时给升级提示
      if (authUser.quota_left === 0) {
        quotaHtml = ' <span class="quota" style="color:#ffe08a">· 额度已用完，请升级会员</span>';
      } else if (authUser.quota_left !== null && authUser.quota_left !== undefined) {
        quotaHtml = ' <span class="quota">· 今日剩余 ' + authUser.quota_left + '</span>';
      }
      badge.innerHTML = '👤 ' + escapeHtml(authUser.username) + quotaHtml;
      // 会员状态徽章
      var memberBadge = '';
      if (authUser.role === 'member') {
        var daysRem = authUser.days_remaining || 0;
        memberBadge = ' <span class="quota" style="color:#ffd700">· VIP · ' + daysRem + '天</span>';
      }
      badge.innerHTML += memberBadge;
      var logoutBtn = global.document.createElement('button');
      logoutBtn.className = 'auth-btn';
      logoutBtn.textContent = '退出';
      logoutBtn.onclick = logout;
      area.appendChild(badge);
      // C4 购买入口加强：免费用户常驻「开通会员」按钮（不再仅额度<5 时显示）
      if (authUser.role === 'free') {
        var upgradeBtn = global.document.createElement('button');
        upgradeBtn.className = 'auth-btn';
        upgradeBtn.style.background = '#ffd700';
        upgradeBtn.style.color = '#333';
        upgradeBtn.textContent = '开通会员';
        upgradeBtn.onclick = function () { openMembershipModal(); };
        area.appendChild(upgradeBtn);
      }
      // #15 密保设置入口（登录后自助找回通道）
      var secBtn = global.document.createElement('button');
      secBtn.className = 'auth-btn';
      secBtn.textContent = '密保';
      secBtn.onclick = function () { openSecuritySetup(); };
      area.appendChild(secBtn);
      area.appendChild(logoutBtn);
    } else {
      var loginBtn = global.document.createElement('button');
      loginBtn.className = 'auth-btn';
      loginBtn.textContent = '登录';
      loginBtn.onclick = function () { openAuthModal('login'); };
      var regBtn = global.document.createElement('button');
      regBtn.className = 'auth-btn primary';
      regBtn.textContent = '免费注册';
      regBtn.onclick = function () { openAuthModal('register'); };
      area.appendChild(loginBtn);
      area.appendChild(regBtn);
      // C4：匿名用户也提供「开通会员」入口（点击引导登录）
      var anonUpgradeBtn = global.document.createElement('button');
      anonUpgradeBtn.className = 'auth-btn';
      anonUpgradeBtn.style.background = '#ffd700';
      anonUpgradeBtn.style.color = '#333';
      anonUpgradeBtn.textContent = '开通会员';
      anonUpgradeBtn.onclick = function () { openAuthModal('login'); };
      area.appendChild(anonUpgradeBtn);
    }
  }

  // -----------------------------------------------------------------
  // 9. 登出：清 token + 清 authUser + 广播 + 触发 A3 会话区清空
  //    通过 dispatch 'auth:changed'（token=''）通知 A3；
  //    并尝试调用 window 上的 newSession/loadSessions（A3 挂载）
  // -----------------------------------------------------------------
  function logout() {
    authUser = null;
    setToken('');   // 清 store + localStorage + 广播 auth:changed(token='')
    renderAuthArea();
    // 联动 A3：清空会话区（A3 在 index.html 提供 newSession/loadSessions）
    try {
      if (typeof global.newSession === 'function') global.newSession();
      else if (typeof global.loadSessions === 'function') global.loadSessions();
    } catch (e) { /* 忽略 */ }
  }

  // 简易 HTML 转义（用户名防注入）
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // -----------------------------------------------------------------
  // 10. 会员到期提醒（#14）：登录/恢复登录态后检查；≤7 天弹提示
  // -----------------------------------------------------------------
  function checkMembershipReminder() {
    if (!authUser || authUser.anonymous || authUser.role !== 'member') return;
    global.GK.api('/me/membership').then(function (d) {
      if (!d || !d.is_member) return;
      var days = d.days_remaining;
      if (typeof days !== 'number') return;
      if (days <= 0) {
        showReminderToast('您的会员已到期，续费可继续享受会员权益');
      } else if (days <= 7) {
        showReminderToast('您的会员将在 ' + days + ' 天后到期，记得及时续费');
      }
    }).catch(function () { /* 静默：网络失败不打扰 */ });
  }

  function showReminderToast(msg) {
    var toast = global.document.createElement('div');
    toast.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);' +
      'background:#4a6cf7;color:#fff;padding:10px 20px;border-radius:10px;font-size:13.5px;' +
      'z-index:99999;box-shadow:0 4px 16px rgba(0,0,0,.18);display:flex;align-items:center;gap:12px;';
    var txt = global.document.createElement('span');
    txt.textContent = msg;
    var btn = global.document.createElement('button');
    btn.textContent = '去续费';
    btn.style.cssText = 'background:#ffd700;color:#333;border:none;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12.5px;font-weight:600;';
    btn.onclick = function () { toast.remove(); openMembershipModal(); };
    var close = global.document.createElement('button');
    close.textContent = '✕';
    close.style.cssText = 'background:transparent;border:none;color:rgba(255,255,255,.8);cursor:pointer;font-size:14px;padding:0 2px;';
    close.onclick = function () { toast.remove(); };
    toast.appendChild(txt);
    toast.appendChild(btn);
    toast.appendChild(close);
    global.document.body.appendChild(toast);
    // 8 秒后自动消失（未操作）
    global.setTimeout(function () {
      if (toast.parentNode) toast.remove();
    }, 8000);
  }

  // -----------------------------------------------------------------
  // 11. 会员购买模态框
  // -----------------------------------------------------------------
  function openMembershipModal() {
    // 拉取定价方案
    global.GK.api('/plans').then(function (d) {
      var plans = d.plans || [];
      if (plans.length === 0) return;
      
      // 创建模态框
      var mask = global.document.createElement('div');
      mask.className = 'modal-mask';
      mask.innerHTML = '<div class="modal">' +
        '<h2>升级会员</h2>' +
        '<div class="err" id="membershipErr"></div>' +
        '<div id="planList"></div>' +
        '<div class="divider"><span>或输入充值码</span></div>' +
        '<input id="rechargeCode" type="text" placeholder="充值码">' +
        '<div class="row" style="margin-top:12px">' +
        '<button class="cancel" onclick="this.closest(\'.modal-mask\').remove()">取消</button>' +
        '<button class="submit" id="activateBtn">激活</button>' +
        '</div>' +
        '</div>';
      
      global.document.body.appendChild(mask);
      
      // 渲染定价方案
      var planList = mask.querySelector('#planList');
      plans.forEach(function (p) {
        var div = global.document.createElement('div');
        div.style.cssText = 'padding:12px;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:8px;cursor:pointer;';
        div.innerHTML = '<strong>' + p.name + '</strong><br>' +
          '<span style="color:#4a6cf7;font-size:18px">¥' + (p.price/100).toFixed(2) + '</span> · ' +
          p.duration_days + '天 · ' + p.benefits;
        div.onclick = function () {
          // 选择该方案，创建订单
          mask.querySelector('#membershipErr').textContent = '';
          global.GK.api('/orders', { method: 'POST', body: { plan: p.plan } })
            .then(function (order) {
              mask.querySelector('#membershipErr').textContent = '订单已创建，请输入充值码激活';
              mask.querySelector('#activateBtn').dataset.orderId = order.order_id;
            })
            .catch(function (e) {
              mask.querySelector('#membershipErr').textContent = '创建订单失败: ' + e.message;
            });
        };
        planList.appendChild(div);
      });
      
      // 充值码激活
      mask.querySelector('#activateBtn').onclick = function () {
        var code = mask.querySelector('#rechargeCode').value.trim();
        if (!code) {
          mask.querySelector('#membershipErr').textContent = '请输入充值码';
          return;
        }
        var btn = mask.querySelector('#activateBtn');
        btn.disabled = true;
        btn.textContent = '激活中...';
        global.GK.api('/orders/recharge-code/activate', { method: 'POST', body: { code: code } })
          .then(function (d) {
            mask.querySelector('#membershipErr').textContent = '激活成功！会员有效期至 ' + d.membership.member_expire_at;
            mask.querySelector('#membershipErr').style.color = '#30a46c';
            setTimeout(function () {
              mask.remove();
              GK.loadAuthUser(); // 刷新用户信息
            }, 1500);
          })
          .catch(function (e) {
            mask.querySelector('#membershipErr').textContent = '激活失败: ' + e.message;
            mask.querySelector('#membershipErr').style.color = '#e5484d';
            btn.disabled = false;
            btn.textContent = '激活';
          });
      };
      
      // 点击遮罩关闭
      mask.addEventListener('click', function (e) {
        if (e.target === mask) mask.remove();
      });
    }).catch(function (e) {
      console.error('加载定价失败:', e);
    });
  }
  global.GK.openMembershipModal = openMembershipModal;

  // -----------------------------------------------------------------
  // 12. #15 自助找回密码（密保问题通道）+ 密保设置
  // -----------------------------------------------------------------
  // 找回三步骤：username → 密保问题答案 → 新密码
  function openForgotPassword() {
    closeAuthModal();
    var mask = global.document.createElement('div');
    mask.className = 'modal-mask';
    mask.innerHTML =
      '<div class="modal" style="max-width:420px">' +
        '<h2>找回密码</h2>' +
        '<div class="err" id="fpErr"></div>' +
        '<div id="fpBody">' +
          '<p style="font-size:13px;color:#555;margin:4px 0 8px">第 1 步 · 输入用户名</p>' +
          '<input id="fpUsername" type="text" placeholder="用户名">' +
        '</div>' +
        '<div class="row" style="margin-top:12px">' +
          '<button class="cancel" onclick="this.closest(\'.modal-mask\').remove()">取消</button>' +
          '<button class="submit" id="fpNextBtn">下一步</button>' +
        '</div>' +
      '</div>';
    global.document.body.appendChild(mask);
    mask.addEventListener('click', function (e) { if (e.target === mask) mask.remove(); });

    function err(msg) { mask.querySelector('#fpErr').textContent = msg; }
    var btn = mask.querySelector('#fpNextBtn');

    // 第 1 步：取密保问题
    btn.onclick = function () {
      var u = mask.querySelector('#fpUsername').value.trim();
      if (!u) { err('请输入用户名'); return; }
      btn.disabled = true; btn.textContent = '查询中...';
      global.GK.api('/password/forgot/question', { method: 'POST', body: { username: u } })
        .then(function (d) {
          if (!d.can_recover) {
            err('该账号未设置密保问题，请联系管理员重置');
            btn.disabled = false; btn.textContent = '下一步';
            return;
          }
          // 第 2 步：答密保问题
          mask.querySelector('#fpBody').innerHTML =
            '<p style="font-size:13px;color:#555;margin:4px 0 8px">第 2 步 · 回答密保问题</p>' +
            '<div style="font-size:14px;font-weight:600;margin-bottom:8px">' + escapeHtml(d.security_question) + '</div>' +
            '<input id="fpAnswer" type="text" placeholder="密保答案">';
          btn.textContent = '验证';
          btn.disabled = false;
          btn.onclick = function () {
            var a = mask.querySelector('#fpAnswer').value.trim();
            if (!a) { err('请输入密保答案'); return; }
            btn.disabled = true; btn.textContent = '验证中...';
            global.GK.api('/password/forgot/verify', { method: 'POST', body: { username: u, answer: a } })
              .then(function (v) {
                // 第 3 步：设新密码
                mask.querySelector('#fpBody').innerHTML =
                  '<p style="font-size:13px;color:#555;margin:4px 0 8px">第 3 步 · 设置新密码</p>' +
                  '<input id="fpNewPass" type="password" placeholder="新密码（至少 6 位）">' +
                  '<input id="fpNewPass2" type="password" placeholder="再次输入新密码">';
                btn.textContent = '重置密码';
                btn.disabled = false;
                btn.onclick = function () {
                  var p1 = mask.querySelector('#fpNewPass').value;
                  var p2 = mask.querySelector('#fpNewPass2').value;
                  if (!p1 || p1.length < 6) { err('新密码至少 6 位'); return; }
                  if (p1 !== p2) { err('两次输入的密码不一致'); return; }
                  btn.disabled = true; btn.textContent = '提交中...';
                  global.GK.api('/password/forgot/reset', { method: 'POST', body: { ticket: v.ticket, new_password: p1 } })
                    .then(function () {
                      mask.querySelector('#fpBody').innerHTML =
                        '<p style="color:#30a46c;font-size:14px;margin:8px 0">✅ 密码已重置，请用新密码登录</p>';
                      var cancelBtn = mask.querySelector('.cancel');
                      cancelBtn.textContent = '去登录';
                      cancelBtn.onclick = function () { mask.remove(); openAuthModal('login'); };
                      var nb = mask.querySelector('#fpNextBtn'); if (nb) nb.remove();
                    })
                    .catch(function (e) { err('重置失败: ' + (e && e.message ? e.message : e)); btn.disabled = false; btn.textContent = '重置密码'; });
                };
              })
              .catch(function (e) { err('答案错误: ' + (e && e.message ? e.message : e)); btn.disabled = false; btn.textContent = '验证'; });
          };
        })
        .catch(function (e) { err('查询失败: ' + (e && e.message ? e.message : e)); btn.disabled = false; btn.textContent = '下一步'; });
    };
  }

  // 密保设置（登录后）：查询当前是否已设 → 未设则引导设置
  function openSecuritySetup() {
    if (!authUser || authUser.anonymous) { openAuthModal('login'); return; }
    global.GK.api('/me/security-question').then(function (d) {
      var qs = d.premade || [];
      var cur = d.security_question || '';
      var options = qs.map(function (q) {
        return '<option value="' + escapeHtml(q) + '"' + (q === cur ? ' selected' : '') + '>' + escapeHtml(q) + '</option>';
      }).join('');
      var mask = global.document.createElement('div');
      mask.className = 'modal-mask';
      mask.innerHTML =
        '<div class="modal" style="max-width:420px">' +
          '<h2>设置密保问题</h2>' +
          '<div class="err" id="secErr"></div>' +
          '<p style="font-size:13px;color:#555;margin:4px 0 8px">用于忘记密码时自助找回，请牢记答案</p>' +
          '<select id="secQuestion">' + options + '<option value="">自定义…</option></select>' +
          '<input id="secQuestionCustom" type="text" placeholder="自定义密保问题（选自定义时填写）" style="display:none">' +
          '<input id="secAnswer" type="text" placeholder="密保答案">' +
          '<div class="row" style="margin-top:12px">' +
            '<button class="cancel" onclick="this.closest(\'.modal-mask\').remove()">取消</button>' +
            '<button class="submit" id="secSaveBtn">保存</button>' +
          '</div>' +
        '</div>';
      global.document.body.appendChild(mask);
      mask.addEventListener('click', function (e) { if (e.target === mask) mask.remove(); });
      mask.querySelector('#secQuestion').onchange = function () {
        mask.querySelector('#secQuestionCustom').style.display =
          (this.value === '' || this.value === '自定义…') ? 'block' : 'none';
      };
      mask.querySelector('#secSaveBtn').onclick = function () {
        var sel = mask.querySelector('#secQuestion').value;
        var custom = mask.querySelector('#secQuestionCustom').value.trim();
        var q = (sel && sel !== '自定义…') ? sel : custom;
        var a = mask.querySelector('#secAnswer').value.trim();
        if (!q) { mask.querySelector('#secErr').textContent = '请选择或填写密保问题'; return; }
        if (!a || a.length < 2) { mask.querySelector('#secErr').textContent = '密保答案至少 2 个字符'; return; }
        global.GK.api('/me/security-question', { method: 'POST', body: { question: q, answer: a } })
          .then(function () { mask.remove(); showReminderToast('密保问题已设置'); })
          .catch(function (e) { mask.querySelector('#secErr').textContent = '设置失败: ' + (e && e.message ? e.message : e); });
      };
    }).catch(function (e) {
      console.error('加载密保状态失败:', e);
    });
  }

  // -----------------------------------------------------------------
  // 13. 对外接口 + 启动自检
  // -----------------------------------------------------------------
  global.GK.openAuthModal = openAuthModal;
  global.GK.closeAuthModal = closeAuthModal;
  global.GK.authAction = authAction;
  global.GK.giteeLogin = giteeLogin;
  global.GK.loadAuthUser = loadAuthUser;
  global.GK.renderAuthArea = renderAuthArea;
  global.GK.logout = logout;
  global.GK.openForgotPassword = openForgotPassword;
  global.GK.openSecuritySetup = openSecuritySetup;

  // 启动：解析 OAuth 回调 token → 恢复登录态
  if (typeof global.document !== 'undefined') {
    if (global.document.readyState === 'loading') {
      global.document.addEventListener('DOMContentLoaded', function () {
        applyOauthToken();
        loadAuthUser();
      });
    } else {
      applyOauthToken();
      loadAuthUser();
    }
  }

  global.GK.a1Ready = true;
})(typeof window !== 'undefined' ? window : globalThis);
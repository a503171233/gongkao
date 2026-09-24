/* =====================================================================
 * A3 · 会话交互模块（网站一 前端 · 会话编排层）
 * ---------------------------------------------------------------------
 * 职责：会话全流程编排 —— 列表 / 新建 / 删除 / 历史加载 / 输入发送 /
 *       维护 GK.store.sessionId。不做渲染（A5）、不做传输（A4）。
 *
 * 强制约束（违反就重做）：
 *   - 一切 HTTP 走 GK.api / GK.sse（A4），本模块不直接 fetch。
 *   - 不拼 DOM 渲染消息：渲染一律通过事件广播交给渲染层（A5）。
 *   - user_id 语义：登录用户用 GK.store.userId（A1 在 auth:changed
 *     时写入），匿名用 'anonymous'，不混用。
 *   - 切老师（teacher:changed）必须主动新建会话，不带旧 session_id。
 *   - 多轮上下文由后端控制（HISTORY_LIMIT=10 轮），前端不私自透传
 *     自定义历史字段。
 *   - 纯原生 JS，无第三方依赖，无构建步骤。
 *
 * 渲染事件协议（A3 → 渲染层；A5 交付前由 index.html 过渡层订阅）：
 *   render:clear      {hint?}              清空渲染区（可选提示文案）
 *   history:loaded    {messages, sessionId} 回放历史消息数组（A3 不拼 DOM）
 *   ask:user          {text}                用户发言上屏
 *   ask:start         {sessionId}           回答开始（后端已建/复用会话）
 *   ask:delta         {text}                增量文本
 *   ask:refs          {refs, sessionId}     引用列表
 *   ask:done          {sessionId}           正常结束
 *   ask:guard_block   {fails, reason}       幻觉硬拦截
 *   ask:guard_warn    {level, notice}       分级守卫 warn/confirm 轻提示
 *   ask:error         {message, status}     错误提示（detail 透传）
 *
 * 对外能力：
 *   GK.initSession()   —— 绑定输入区/按钮 + 事件订阅 + 首拉会话列表
 *   GK.newSession()    —— 主动新建（登出/清空场景复用）
 * ===================================================================== */
(function (global) {
  'use strict';

  // -----------------------------------------------------------------
  // 1. 命名空间兜底（与 A2/A4 一致，避免依赖加载顺序）
  // -----------------------------------------------------------------
  if (!global.GK) global.GK = {};
  if (!global.GK.store) {
    global.GK.store = { token: '', teacherId: '', sessionId: '', userId: '' };
  }

  // -----------------------------------------------------------------
  // 2. 元素缓存（defer 执行时 DOM 已就绪）
  // -----------------------------------------------------------------
  var sessionListEl = document.getElementById('sessionList');
  var queryEl       = document.getElementById('query');
  var sendBtn       = document.getElementById('send');
  var newSessionBtn = document.getElementById('newSessionBtn');

  var _sending = false;   // 防连点
  var _pageSize = 30;     // 会话分页：每页 30 条
  var _loadedAll = false; // 是否已加载全部

  // -----------------------------------------------------------------
  // 3. 内部工具
  // -----------------------------------------------------------------
  function broadcast(type, detail) {
    if (!global.GK.bus) return;
    try {
      global.GK.bus.dispatchEvent(new CustomEvent(type, { detail: detail || {} }));
    } catch (e) { /* 事件失败不影响主流程 */ }
  }

  function currentUserId() {
    // A1 在 auth:changed 时写入 GK.store.userId；无则匿名
    return global.GK.store.userId || 'anonymous';
  }

  function errMsg(e) {
    return (e && e.message) ? e.message : String(e);
  }

  // -----------------------------------------------------------------
  // 4. 会话列表：GET /api/sessions?user_id=&limit=&offset= （分页）
  // -----------------------------------------------------------------
  async function loadSessions(reset) {
    try {
      if (reset || !sessionListEl || !sessionListEl.dataset.offset) {
        if (sessionListEl) sessionListEl.dataset.offset = '0';
      }
      var uid = currentUserId();
      var off = sessionListEl ? (parseInt(sessionListEl.dataset.offset, 10) || 0) : 0;
      var d = await global.GK.api('/sessions?user_id=' + encodeURIComponent(uid) +
        '&limit=' + _pageSize + '&offset=' + off);
      var list = (d && d.sessions) || [];
      if (off === 0) _loadedAll = list.length < _pageSize;
      renderSessionList(list, off);
    } catch (e) {
      console.warn('[A3] 会话列表加载失败:', errMsg(e));
    }
  }

  function renderSessionList(sessions, offset) {
    if (!sessionListEl) return;
    // 分页：第一页重建，后续追加
    var isFirstPage = !offset || offset === 0;
    if (isFirstPage) {
      sessionListEl.innerHTML = '';
      sessionListEl.dataset.offset = '0';
    }
    var cur = global.GK.store.sessionId;
    var frag = document.createDocumentFragment();
    sessions.forEach(function (s) {
      var item = document.createElement('div');
      item.className = 'session-item' + (s.session_id === cur ? ' active' : '');
      item.title = s.session_id;

      var label = document.createElement('span');
      // 展示老师 + 会话短 id + 消息数
      var teacher = s.teacher_id || '';
      label.textContent = (teacher ? teacher + ' · ' : '') +
        '会话 ' + String(s.session_id).slice(0, 8) + ' · ' +
        (s.message_count || 0) + '条';
      label.style.flex = '1';
      item.appendChild(label);

      var del = document.createElement('span');
      del.className = 'del';
      del.textContent = '✕';
      del.title = '删除会话';
      del.onclick = function (e) {
        e.stopPropagation();
        if (!global.confirm('删除该会话？')) return;
        deleteSession(s.session_id);
      };
      item.appendChild(del);

      item.onclick = function () { loadSession(s.session_id); };
      frag.appendChild(item);
    });
    sessionListEl.appendChild(frag);

    // 分页加载更多按钮
    removeLoadMoreBtn();
    if (sessions.length >= _pageSize) {
      var more = document.createElement('div');
      more.className = 'session-more';
      more.textContent = '加载更多…';
      more.onclick = function () {
        var nextOff = (parseInt(sessionListEl.dataset.offset || '0', 10) || 0) + _pageSize;
        sessionListEl.dataset.offset = String(nextOff);
        loadSessions();
      };
      sessionListEl.appendChild(more);
      if (sessionListEl.dataset.offset && parseInt(sessionListEl.dataset.offset, 10) > 0) {
        // 追加页：滚动到旧位置
        sessionListEl.scrollTop = sessionListEl.scrollHeight;
      }
    } else {
      _loadedAll = true;
    }
  }

  function removeLoadMoreBtn() {
    if (!sessionListEl) return;
    var old = sessionListEl.querySelector('.session-more');
    if (old) old.parentNode.removeChild(old);
  }

  // -----------------------------------------------------------------
  // 5. 历史加载：GET /sessions/{id}/messages → 交 A5 渲染
  // -----------------------------------------------------------------
  async function loadSession(sid) {
    try {
      var d = await global.GK.api('/sessions/' + encodeURIComponent(sid) + '/messages');
      // 会话归属老师与当前不符 → 切老师（A2 会广播 teacher:changed，
      // 本模块随即 newSession 清旧 session_id；下方再回填当前会话）
      if (d && d.teacher_id && d.teacher_id !== global.GK.store.teacherId &&
          typeof global.GK.switchTeacher === 'function') {
        await global.GK.switchTeacher(d.teacher_id);
      }
      global.GK.store.sessionId = sid;
      broadcast('render:clear', {});
      // A3 不渲染：把消息数组交给渲染层（A5 / 过渡层）
      broadcast('history:loaded', {
        messages: (d && d.messages) || [],
        sessionId: sid,
      });
      loadSessions(true);   // 刷新高亮（重置分页）
    } catch (e) {
      console.warn('[A3] 加载会话失败:', errMsg(e));
      broadcast('ask:error', { message: '加载会话失败：' + errMsg(e), status: 0 });
    }
  }

  // -----------------------------------------------------------------
  // 6. 新建会话：清空 session_id，下次 ask 由后端自动创建
  // -----------------------------------------------------------------
  function newSession(hint) {
    global.GK.store.sessionId = '';
    broadcast('render:clear', { hint: hint || '新会话已开始，请提问。' });
    if (queryEl) queryEl.focus();
    loadSessions(true);
  }

  // -----------------------------------------------------------------
  // 7. 删除会话：DELETE /sessions/{id} → 刷新列表
  //    删的是当前会话 → 清空 sessionId + 通知渲染层清空
  // -----------------------------------------------------------------
  async function deleteSession(sid) {
    try {
      await global.GK.api('/sessions/' + encodeURIComponent(sid), { method: 'DELETE' });
      if (sid === global.GK.store.sessionId) {
        global.GK.store.sessionId = '';
        broadcast('render:clear', { hint: '会话已删除，开始新对话吧。' });
      }
    } catch (e) {
      console.warn('[A3] 删除会话失败:', errMsg(e));
      broadcast('ask:error', { message: '删除会话失败：' + errMsg(e), status: 0 });
    }
    loadSessions(true);
  }

  // -----------------------------------------------------------------
  // 8. 发送：组装 {query, teacher_id, stream:true, session_id} → GK.sse
  //    start 事件回填 session_id；输入为空不发送；请求中禁用按钮
  // -----------------------------------------------------------------
  function send() {
    var q = queryEl ? queryEl.value.trim() : '';
    if (!q || _sending) return;

    _sending = true;
    if (sendBtn) sendBtn.disabled = true;
    if (queryEl) queryEl.value = '';

    // 用户发言上屏（渲染层负责拼 DOM）
    broadcast('ask:user', { text: q });

    var body = {
      query: q,
      teacher_id: global.GK.store.teacherId || 'T001',
      stream: true,
      session_id: global.GK.store.sessionId || '',
    };

    var finished = false;
    function finishSend() {
      if (finished) return;
      finished = true;
      _sending = false;
      if (sendBtn) sendBtn.disabled = false;
      if (queryEl) queryEl.focus();
      loadSessions(true);   // 刷新历史栏（含后端新建/复用的会话）
    }

    // A4 传输（本模块不直接 fetch）；错误 detail 由 A4 抛到 error 回调
    global.GK.sse('/ask', body, {
      start: function (d) {
        // 收到 start → 后端已建/复用会话 → 回填 session_id
        if (d && d.session_id) {
          global.GK.store.sessionId = d.session_id;
        }
        broadcast('ask:start', {
          sessionId: (d && d.session_id) || global.GK.store.sessionId,
        });
      },
      delta: function (d) {
        broadcast('ask:delta', { text: (d && d.text) || '' });
      },
      refs: function (d) {
        broadcast('ask:refs', {
          refs: (d && d.references) || [],
          sessionId: (d && d.session_id) || global.GK.store.sessionId,
        });
      },
      done: function (d) {
        broadcast('ask:done', {
          sessionId: (d && d.session_id) || global.GK.store.sessionId,
        });
        finishSend();
      },
      guard_block: function (d) {
        // 幻觉拦截：渲染层替换已显示文本；后端不再发 done
        broadcast('ask:guard_block', {
          fails: (d && d.fails) || [],
          reason: (d && d.reason) || '',
        });
        finishSend();
      },
      guard_warn: function (d) {
        // 分级守卫 warn/confirm：内容照常展示，渲染层附提示条；
        // 后端仍会发 refs/done，此处不提前结束发送态
        broadcast('ask:guard_warn', {
          level: (d && d.level) || 'warn',
          notice: (d && d.notice) || '',
          reasons: (d && d.reasons) || [],
        });
      },
      error: function (d) {
        // 429/403/5xx 等：detail 已由 A4 透出
        broadcast('ask:error', {
          message: (d && d.message) || '服务异常',
          status: d && d.status,
        });
        finishSend();
      },
    });
  }

  // -----------------------------------------------------------------
  // 9. 事件订阅：teacher:changed → 主动新建（防旧 session_id 串老师）
  //              auth:changed → 用户切换 → 防串号 + 刷新列表
  // -----------------------------------------------------------------
  function subscribe() {
    if (!global.GK.bus) return;
    global.GK.bus.addEventListener('teacher:changed', function () {
      newSession('已切换老师，开始新会话吧。');
    });
    global.GK.bus.addEventListener('auth:changed', function (e) {
      var d = (e && e.detail) || {};
      // A1 契约：auth:changed 的 detail = { token, user:{user_id,...} }
      // 同步 userId（登录写入；登出 user 为 null → 删除回匿名）
      var user = d.user;
      if (user && user.user_id) {
        global.GK.store.userId = user.user_id;
      } else if (!d.token) {
        delete global.GK.store.userId;   // 登出回匿名
      }
      // 用户身份变化（登录/登出）→ 清 session 防串号 + 刷新列表
      if (global.GK.store.sessionId) newSession();
      loadSessions(true);
    });
  }

  // -----------------------------------------------------------------
  // 10. 初始化（由 index.html boot 调用；此时 GK.store.userId 已恢复）
  // -----------------------------------------------------------------
  function initSession() {
    sessionListEl = document.getElementById('sessionList');
    queryEl       = document.getElementById('query');
    sendBtn       = document.getElementById('send');
    newSessionBtn = document.getElementById('newSessionBtn');

    if (sendBtn)       sendBtn.onclick = send;
    if (queryEl) {
      queryEl.onkeydown = function (e) { if (e.key === 'Enter') send(); };
    }
    if (newSessionBtn) newSessionBtn.onclick = function () { newSession(); };

    subscribe();
    loadSessions(true);
  }

  // -----------------------------------------------------------------
  // 11. 对外接口
  // -----------------------------------------------------------------
  global.GK.initSession = initSession;
  global.GK.newSession  = newSession;
  global.GK.loadSessions = loadSessions;
  global.GK.loadSessionById = loadSession;
  global.GK.deleteSession  = deleteSession;

  global.GK.a3Ready = true;
})(typeof window !== 'undefined' ? window : globalThis);

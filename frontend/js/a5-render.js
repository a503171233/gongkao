/* =====================================================================
 * A5 · 结果渲染模块（网站一 前端）
 * ---------------------------------------------------------------------
 * 职责：问答结果的一切视觉呈现——流式增量、引用来源、幻觉拦截提示
 *       （guard_block）、拒答话术、历史消息回放。不含任何请求逻辑
 *       （fetch/SSE 归 A4，编排归 A3，本模块只做 DOM 渲染）。
 *
 * 强制约束（违反就重做）：
 *   - 纯原生 JS，无第三方依赖，无构建步骤。
 *   - 拦截原因中文映射必须与 guard.py 的 fails 枚举一致：
 *       reference=引用不符 / fact=数字不符 / out_of_scope=超纲
 *   - guard_block 后：不再追加 delta、不渲染 refs，独立结束渲染态
 *     （后端此后不再发 refs/done）。
 *   - 增量渲染：delta 逐段 append（textNode），禁止整段 textContent 重绘。
 *   - score 展示 3 位小数（toFixed(3)），无引用时隐藏。
 *   - 新内容自动滚动到底部；切会话/老师前清空渲染区。
 *   - 错误提示可见且不破坏已有内容（追加式，不覆盖）。
 *
 * 对外 API（任务书契约，A4 回调 / A3 直连可用）：
 *   A5.appendDelta(text)        增量追加文本片段
 *   A5.showRefs(refs)           渲染引用列表（无引用隐藏）
 *   A5.finish()                 标记完成，停止渲染态
 *   A5.block(reason)            幻觉拦截：替换回答为提示并结束
 *   A5.warn(detail)             分级守卫 warn/confirm：追加提示条（不动正文）
 *   A5.renderHistory(messages)  按 role 正序渲染历史气泡
 *   A5.newBubble()              新建 bot 回答气泡（start 事件）
 *   A5.showError(msg[, status]) 错误提示（追加，不破坏已有内容）
 *
 * 事件订阅（A3 渲染协议，见 a3-session.js 头部）：
 *   render:clear -> 清空（可选 hint）
 *   history:loaded -> renderHistory
 *   ask:user / ask:start / ask:delta / ask:refs /
 *   ask:done / ask:guard_block / ask:guard_warn / ask:error
 * ===================================================================== */
(function (global) {
  'use strict';

  // -----------------------------------------------------------------
  // 1. 命名空间兜底（与 A2/A3/A4 一致）
  // -----------------------------------------------------------------
  if (!global.GK) global.GK = {};
  if (!global.GK.store) {
    global.GK.store = { token: '', teacherId: '', sessionId: '' };
  }

  // -----------------------------------------------------------------
  // 2. 常量与渲染状态
  // -----------------------------------------------------------------
  // 拦截原因中文映射（必须与 guard.py fails 枚举一致，禁止漂移）
  var REASON_CN = {
    reference: '引用不符',
    fact: '数字不符',
    out_of_scope: '超纲'
  };
  // 拒答话术标志（与 answer.REJECT_TEXT 文案匹配）
  var REJECT_MARK = '暂未录入资料库';

  var state = {
    bubble: null,    // 当前 bot 气泡（.bubble 元素）
    answer: '',      // 累积回答文本（拒答检测用）
    blocked: false,  // guard_block 后置 true：拒绝一切追加
    done: false      // finish 后置 true：拒绝追加（防迟到 delta）
  };

  function chatEl() { return document.getElementById('chat'); }

  function scrollToBottom() {
    var c = chatEl();
    if (c) c.scrollTop = c.scrollHeight;
  }

  function removeThinking(bubble) {
    var t = bubble && bubble.querySelector('.thinking');
    if (t) t.parentNode.removeChild(t);
  }

  // -----------------------------------------------------------------
  // 3. 气泡创建 / 增量渲染
  // -----------------------------------------------------------------
  function newBubble() {
    var c = chatEl();
    if (!c) return null;
    var m = document.createElement('div');
    m.className = 'msg bot';
    var avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = '师';
    var bubble = document.createElement('div');
    bubble.className = 'bubble';
    // 思考占位：首个 delta 到达时移除（非整段重绘）
    var thinking = document.createElement('span');
    thinking.className = 'thinking';
    thinking.textContent = '思考中…';
    bubble.appendChild(thinking);
    m.appendChild(avatar);
    m.appendChild(bubble);
    c.appendChild(m);
    state.bubble = bubble;
    state.answer = '';
    state.blocked = false;
    state.done = false;
    scrollToBottom();
    return bubble;
  }

  function appendDelta(text) {
    if (!text || state.blocked || state.done) return;  // 双闸门：拦截/完成后不追加
    if (!state.bubble) newBubble();
    removeThinking(state.bubble);
    state.answer += text;
    // 增量渲染：textNode 逐段追加，避免整段重绘闪烁
    state.bubble.appendChild(document.createTextNode(text));
    scrollToBottom();
  }

  // -----------------------------------------------------------------
  // 4. 引用列表（score 3 位小数；无引用隐藏）
  // -----------------------------------------------------------------
  function showRefs(refs) {
    if (state.blocked || !state.bubble) return;  // 拦截后不渲染 refs
    removeThinking(state.bubble);
    var list = Array.isArray(refs) ? refs : [];
    if (!list.length) return;                    // 无引用：隐藏
    // 防重复调用产生多份 refs
    var old = state.bubble.querySelector('.refs');
    if (old) old.parentNode.removeChild(old);
    var refsDiv = document.createElement('div');
    refsDiv.className = 'refs';
    // 折叠头：一行摘要，点击展开/收起明细（任务书「引用资料折叠展示」）
    var head = document.createElement('div');
    head.className = 'refs-head';
    head.textContent = '📎 引用 ' + list.length + ' 条来源（点击展开）';
    var body = document.createElement('div');
    body.className = 'refs-body';
    body.style.display = 'none';
    list.forEach(function (r) {
      var span = document.createElement('span');
      span.className = 'ref';
      var score = (typeof r.score === 'number') ? r.score.toFixed(3) : String(r.score || '');
      span.textContent = '📄 ' + (r.doc_name || '未知来源') + ' (' + score + ')';
      body.appendChild(span);
    });
    head.onclick = function () {
      var isHidden = body.style.display === 'none';
      body.style.display = isHidden ? 'block' : 'none';
      head.textContent = isHidden
        ? '📎 引用 ' + list.length + ' 条来源（点击收起）'
        : '📎 引用 ' + list.length + ' 条来源（点击展开）';
    };
    refsDiv.appendChild(head);
    refsDiv.appendChild(body);
    state.bubble.appendChild(refsDiv);
    scrollToBottom();
  }

  // -----------------------------------------------------------------
  // 5. 完成 / 拦截 / 错误
  // -----------------------------------------------------------------
  function finish() {
    state.done = true;
    if (state.bubble) removeThinking(state.bubble);
    // 拒答检测：完整回答含拒答标志 → 浅橙拒答样式（现状 .rejected 类）
    if (state.bubble && !state.blocked && state.answer.indexOf(REJECT_MARK) !== -1) {
      state.bubble.classList.add('rejected');
    }
    // A1 答案反馈：正常完成的回答（非拦截/非拒答）挂 👍/👎 按钮
    if (state.bubble && !state.blocked
        && state.answer && state.answer.indexOf(REJECT_MARK) === -1) {
      attachFeedback(state.bubble, state.answer);
    }
  }

  // A1 答案反馈：赞/踩按钮（匿名也可提交）
  function attachFeedback(bubble, answer) {
    // 防重复挂载
    if (bubble.querySelector('.fb-bar')) return;
    var bar = document.createElement('div');
    bar.className = 'fb-bar';
    bar.style.cssText = 'margin-top:10px;display:flex;gap:8px;align-items:center;';
    var makeBtn = function (label, rating) {
      var b = document.createElement('button');
      b.className = 'fb-btn';
      b.textContent = label;
      b.style.cssText = 'border:1px solid #d0d4de;background:#fff;border-radius:6px;' +
        'padding:3px 10px;font-size:12px;cursor:pointer;color:#666;';
      b.onclick = function () {
        if (b.disabled) return;
        b.disabled = true;
        b.style.opacity = '.5';
        var payload = {
          rating: rating,
          question: state.lastQuestion || '',
          answer: answer,
          reason: '',
          session_id: global.GK.store.sessionId || '',
          teacher_id: global.GK.store.teacherId || 'T001'
        };
        global.GK.api('/feedback', { method: 'POST', body: payload })
          .then(function () {
            b.textContent = label + ' ✓';
            b.style.color = '#30a46c';
            b.style.borderColor = '#30a46c';
          })
          .catch(function (e) {
            b.disabled = false;
            b.style.opacity = '1';
            b.textContent = label + ' 失败';
            setTimeout(function () { b.textContent = label; }, 1200);
          });
      };
      return b;
    };
    bar.appendChild(makeBtn('👍 有帮助', 'up'));
    bar.appendChild(makeBtn('👎 需改进', 'down'));
    // C4 入口：收藏当前问答（收藏面板在 chat.html 顶部有独立入口）
    var fav = document.createElement('button');
    fav.className = 'fb-btn';
    fav.textContent = '📌 收藏';
    fav.style.cssText = 'border:1px solid #d0d4de;background:#fff;border-radius:6px;' +
      'padding:3px 10px;font-size:12px;cursor:pointer;color:#666;';
    fav.onclick = function () {
      if (global.C4 && global.C4.bookmarkCurrent) global.C4.bookmarkCurrent();
      else alert('请先登录后使用收藏功能');
    };
    bar.appendChild(fav);
    bubble.appendChild(bar);
  }

  function block(reason) {
    if (!state.bubble) newBubble();
    state.blocked = true;
    state.done = true;
    removeThinking(state.bubble);
    // reason 兼容单值/数组/空；未知枚举原样透出
    var list = Array.isArray(reason) ? reason : (reason ? [reason] : []);
    var names = list.map(function (r) { return REASON_CN[r] || r; }).filter(Boolean).join('、');
    // 替换当前回答内容（清空增量文本与 refs，避免残留）
    state.bubble.textContent = '';
    var tip = '⚠️ 回答被幻觉检测拦截：未能通过教学资料校验';
    if (names) tip += '（' + names + '）';
    state.bubble.appendChild(document.createTextNode(tip));
    state.bubble.classList.add('rejected');
    scrollToBottom();
  }

  // 分级守卫 warn/confirm：正文照常，气泡底部追加提示条（不替换、不阻断）
  function warn(detail) {
    if (!state.bubble || state.blocked) return;
    removeThinking(state.bubble);
    if (state.bubble.querySelector('.notice-tip')) return;  // 防重复
    var d = detail || {};
    var isConfirm = d.level === 'confirm';
    var tip = document.createElement('div');
    tip.className = 'notice-tip';
    tip.style.cssText = 'margin-top:8px;padding:6px 10px;border-radius:6px;' +
      'font-size:12px;line-height:1.6;white-space:normal;' +
      (isConfirm
        ? 'background:#fff7ec;border:1px solid #f0c987;color:#8a5a00;'
        : 'background:#eef4ff;border:1px solid #b6c8f2;color:#3b5bdb;');
    tip.textContent = d.notice ||
      (isConfirm ? '⚠️ 本回答与讲义匹配度一般，请以讲义为准。'
                 : '💡 核心内容已通过资料校验，部分为助教补充讲解。');
    state.bubble.appendChild(tip);
    scrollToBottom();
  }

  function showError(msg, status) {
    if (!state.bubble) newBubble();
    removeThinking(state.bubble);
    // 不破坏已有内容：追加错误提示条（独立 .error-tip 样式）
    var errDiv = document.createElement('div');
    errDiv.className = 'error-tip';
    var text = '⚠️ ' + (msg || '服务异常');
    if (status) text += ' (HTTP ' + status + ')';
    errDiv.textContent = text;
    state.bubble.appendChild(errDiv);
    state.done = true;   // 错误后不再接受迟到 delta
    scrollToBottom();
  }

  // -----------------------------------------------------------------
  // 6. 用户气泡 / 历史回放 / 清空
  // -----------------------------------------------------------------
  function addUserMsg(text) {
    var c = chatEl();
    if (!c) return;
    var m = document.createElement('div');
    m.className = 'msg user';
    var avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = '我';
    var bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = text || '';
    m.appendChild(avatar);
    m.appendChild(bubble);
    c.appendChild(m);
    scrollToBottom();
  }

  function renderHistory(messages) {
    clear();
    var list = Array.isArray(messages) ? messages : [];
    if (!list.length) {
      showHint('空会话，开始提问吧。');
      return;
    }
    list.forEach(function (m) {
      if (!m || typeof m !== 'object') return;
      var content = (m.content == null) ? '' : String(m.content);
      if (m.role === 'user') {
        addUserMsg(content);
      } else if (m.role === 'assistant') {
        newBubble().textContent = '·';   // 临时占位，随后整段填充
        var b = state.bubble;
        b.textContent = '';
        b.appendChild(document.createTextNode(content));
        // 历史拒答话术加样式（不走 finish 检测，直接标记）
        if (content.indexOf(REJECT_MARK) !== -1) b.classList.add('rejected');
      }
    });
    scrollToBottom();
  }

  function clear() {
    var c = chatEl();
    if (c) c.innerHTML = '';
    state.bubble = null;
    state.answer = '';
    state.blocked = false;
    state.done = false;
    state.lastQuestion = '';
  }

  function showHint(text) {
    var c = chatEl();
    if (!c) return;
    var h = document.createElement('div');
    h.className = 'hint';
    h.textContent = text || '';
    c.appendChild(h);
  }

  // -----------------------------------------------------------------
  // 7. A3 渲染协议事件订阅（index.html 过渡层迁出后归 A5 接管）
  // -----------------------------------------------------------------
  function handleClear(detail) {
    clear();
    var hint = (detail && detail.hint) || '';
    if (hint) showHint(hint);
  }

  function subscribeA3() {
    if (!global.GK.bus || typeof global.GK.bus.addEventListener !== 'function') return;
    var bus = global.GK.bus;
    bus.addEventListener('render:clear', function (e) {
      handleClear((e && e.detail) || {});
    });
    bus.addEventListener('history:loaded', function (e) {
      var d = (e && e.detail) || {};
      renderHistory(d.messages || []);
    });
    bus.addEventListener('ask:user', function (e) {
      var d = (e && e.detail) || {};
      state.lastQuestion = d.text || '';
      addUserMsg(state.lastQuestion);
    });
    bus.addEventListener('ask:start', function () { newBubble(); });
    bus.addEventListener('ask:delta', function (e) {
      var d = (e && e.detail) || {};
      appendDelta(d.text || '');
    });
    bus.addEventListener('ask:refs', function (e) {
      var d = (e && e.detail) || {};
      showRefs(d.refs || []);
    });
    bus.addEventListener('ask:done', function () { finish(); });
    bus.addEventListener('ask:guard_block', function (e) {
      var d = (e && e.detail) || {};
      // A3 协议 detail={fails:[...], reason}；优先完整 fails 数组（多枚举全映射），
      // 无 fails 时回退单值 reason。
      var fails = d.fails || [];
      block(fails.length ? fails : (d.reason || ''));
    });
    bus.addEventListener('ask:guard_warn', function (e) {
      warn((e && e.detail) || {});
    });
    bus.addEventListener('ask:error', function (e) {
      var d = (e && e.detail) || {};
      showError(d.message || '服务异常', d.status);
    });
  }

  // -----------------------------------------------------------------
  // 8. 挂载对外 API + 订阅
  // -----------------------------------------------------------------
  global.A5 = {
    appendDelta: appendDelta,
    showRefs: showRefs,
    finish: finish,
    block: block,
    warn: warn,
    renderHistory: renderHistory,
    newBubble: newBubble,
    showError: showError
  };
  global.GK.a5Ready = true;

  // defer 脚本执行时 DOM 已就绪，直接订阅（A3 的 initSession 在 DOMContentLoaded 后广播，不丢事件）
  subscribeA3();
})(typeof window !== 'undefined' ? window : globalThis);
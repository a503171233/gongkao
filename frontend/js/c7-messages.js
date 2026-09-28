/* =====================================================================
 * C7 · 消息中心模块（网站一 前端）
 * ---------------------------------------------------------------------
 * 职责：#19 站内信/系统通知——未读角标轮询 + 消息列表抽屉 + 单条/全部已读
 * 复用 A4 (GK.api) 传输层；依赖 chat.html 中 #c7MsgBtn / #msgBadge 元素。
 *
 * 对外能力：
 *   C7.init()             —— 启动：token 存在则轮询未读角标（60s + 聚焦刷新）
 *   C7.openMessages()     —— 打开消息抽屉
 *   C7.readOne(id)        —— 标记单条已读
 *   C7.readAll()          —— 全部已读
 * ===================================================================== */
(function (global) {
  'use strict';

  if (!global.GK) global.GK = {};

  var _timer = null;
  var _loading = false;

  // -----------------------------------------------------------------
  // 1. 工具函数
  // -----------------------------------------------------------------
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtTime(s) {
    var t = String(s || '');
    return t.replace('T', ' ').replace(/Z$/, '').slice(0, 16);
  }

  function hasToken() {
    return !!(global.GK.store && global.GK.store.token);
  }

  function getBadge() {
    return document.getElementById('msgBadge');
  }

  // -----------------------------------------------------------------
  // 2. 未读角标
  // -----------------------------------------------------------------
  function refreshBadge() {
    if (!hasToken()) {
      setBadge(0);
      return;
    }
    global.GK.api('/messages/unread').then(function (d) {
      setBadge(d && d.count ? d.count : 0);
    }).catch(function () {
      setBadge(0);
    });
  }

  function setBadge(n) {
    var b = getBadge();
    if (!b) return;
    if (!n) { b.style.display = 'none'; b.textContent = ''; return; }
    b.style.display = '';
    b.textContent = n > 99 ? '99+' : String(n);
  }

  function startPolling() {
    if (_timer) return;
    refreshBadge();
    _timer = setInterval(refreshBadge, 60000);
    if (document.addEventListener) {
      document.addEventListener('visibilitychange', function () {
        if (!document.hidden) refreshBadge();
      });
    }
  }

  // -----------------------------------------------------------------
  // 3. 消息抽屉
  // -----------------------------------------------------------------
  function ensurePanel() {
    var p = document.getElementById('c7Panel');
    if (!p) {
      p = document.createElement('div');
      p.id = 'c7Panel';
      p.style.cssText = 'position:fixed;right:0;top:0;bottom:0;width:420px;max-width:92vw;' +
        'background:#fff;box-shadow:-2px 0 14px rgba(0,0,0,.12);z-index:200;' +
        'display:flex;flex-direction:column;font-family:inherit;';
      document.body.appendChild(p);
    }
    p.innerHTML =
      '<div style="display:flex;align-items:center;gap:8px;padding:14px 16px;border-bottom:1px solid var(--line)">' +
        '<span style="font-size:16px;font-weight:700">🔔 消息中心</span>' +
        '<span id="c7Unread" style="font-size:11.5px;color:var(--ink-3)"></span>' +
        '<span style="flex:1"></span>' +
        '<button onclick="C7.readAll()" style="padding:5px 10px;border:1px solid var(--line-2);border-radius:6px;background:#fff;cursor:pointer;font-size:12px">全部已读</button>' +
        '<button onclick="C7.close()" style="padding:5px 10px;border:none;border-radius:6px;background:var(--paper-2);cursor:pointer;font-size:12px">✕</button>' +
      '</div>' +
      '<div id="c7List" style="flex:1;overflow-y:auto;padding:10px 14px"></div>';
    return p;
  }

  function renderList() {
    if (_loading) return;
    _loading = true;
    var listEl = document.getElementById('c7List');
    var unreadEl = document.getElementById('c7Unread');
    if (!listEl) return;
    listEl.innerHTML = '<div style="padding:24px 0;text-align:center;color:var(--ink-3);font-size:13px">加载中…</div>';
    global.GK.api('/messages?page=1&size=50').then(function (d) {
      _loading = false;
      var msgs = (d && d.messages) || [];
      var unread = (d && d.unread) || 0;
      if (unreadEl) unreadEl.textContent = unread ? ('未读 ' + unread + ' 条') : '暂无未读';
      if (!msgs.length) {
        listEl.innerHTML = '<div style="padding:36px 0;text-align:center;color:var(--ink-3);font-size:13px">暂无消息，保持关注～</div>';
        return;
      }
      var html = '';
      msgs.forEach(function (m) {
        var read = m.read === 1;
        var dot = read
          ? '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--line-2);margin-right:6px"></span>'
          : '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--bad);margin-right:6px"></span>';
        var unreadBg = read ? '' : 'background:var(--card-2);border-color:var(--wood)';
        html +=
          '<div style="border:1px solid var(--line-2);border-radius:10px;padding:12px 14px;margin-bottom:10px;' + unreadBg + '">' +
            '<div style="display:flex;align-items:center;gap:6px">' + dot +
              '<b style="font-size:14px;flex:1">' + escapeHtml(m.title) + '</b>' +
              '<span style="font-size:11px;color:var(--ink-3)">' + escapeHtml(fmtTime(m.created_at)) + '</span>' +
            '</div>' +
            '<div style="font-size:13px;color:#444;line-height:1.7;margin:8px 0 10px;white-space:pre-wrap">' + escapeHtml(m.content) + '</div>' +
            (read
              ? '<span style="font-size:11px;color:var(--ink-3)">✓ 已读</span>'
              : '<button onclick="C7.readOne(' + m.id + ')" style="padding:4px 12px;border:1px solid var(--wood);color:var(--wood);border-radius:6px;background:#fff;cursor:pointer;font-size:12px">标记已读</button>') +
          '</div>';
      });
      listEl.innerHTML = html;
    }).catch(function (e) {
      _loading = false;
      listEl.innerHTML = '<div style="padding:24px 0;text-align:center;color:var(--bad);font-size:13px">加载失败：' +
        escapeHtml(e.message || '网络异常') + '</div>';
    });
  }

  function openMessages() {
    if (!hasToken()) {
      global.GK.promptLogin('消息中心');
      return;
    }
    ensurePanel();
    renderList();
    refreshBadge();
  }

  function readOne(id) {
    global.GK.api('/messages/' + id + '/read', { method: 'POST', body: '{}' })
      .then(function () {
        renderList();
        refreshBadge();
      })
      .catch(function (e) {
        alert('操作失败：' + (e.message || '网络异常'));
      });
  }

  function readAll() {
    if (!confirm('确认将全部消息标记为已读？')) return;
    global.GK.api('/messages/read_all', { method: 'POST', body: '{}' })
      .then(function () {
        renderList();
        refreshBadge();
      })
      .catch(function (e) {
        alert('操作失败：' + (e.message || '网络异常'));
      });
  }

  function close() {
    var p = document.getElementById('c7Panel');
    if (p) p.remove();
  }

  function init() {
    startPolling();
    var btn = document.getElementById('c7MsgBtn');
    if (btn) btn.onclick = openMessages;
  }

  // -----------------------------------------------------------------
  // 4. 挂载对外 API
  // -----------------------------------------------------------------
  global.C7 = {
    init: init,
    openMessages: openMessages,
    readOne: readOne,
    readAll: readAll,
    close: close,
    refreshBadge: refreshBadge
  };
  global.GK.c7Ready = true;

})(typeof window !== 'undefined' ? window : globalThis);

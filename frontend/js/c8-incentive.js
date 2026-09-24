/* =====================================================================
 * C8 · 激励体系模块（网站一 前端）
 * ---------------------------------------------------------------------
 * 职责：#18 积分/等级/连续学习/成就/流水 面板
 * 复用 A4 (GK.api) 传输层；数据来自 GET /me/incentive。
 *
 * 对外能力：
 *   C8.openIncentive() —— 打开我的激励面板
 *   C8.close()         —— 关闭面板
 * ===================================================================== */
(function (global) {
  'use strict';

  if (!global.GK) global.GK = {};

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

  function currentUserId() {
    return global.GK.store.userId || 'anonymous';
  }

  function requireLogin() {
    if (currentUserId() === 'anonymous') {
      alert('请先登录后查看激励面板');
      return false;
    }
    return true;
  }

  function showToast(msg, type) {
    var div = document.createElement('div');
    div.style.cssText = 'position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;color:#fff;font-size:14px;z-index:9999;box-shadow:0 2px 12px rgba(0,0,0,.15);';
    div.style.background = type === 'error' ? '#e5484d' : (type === 'success' ? '#30a46c' : '#4a6cf7');
    div.textContent = msg;
    document.body.appendChild(div);
    setTimeout(function () { div.remove(); }, 3000);
  }

  // 事件码 → 中文名（未知事件原样展示）
  var EVENT_NAMES = {
    answer: '答题',
    correct: '答对',
    daily_first: '今日首练',
    review_item: '复习巩固',
    mistake_clear: '攻克错题',
    streak_3: '连续 3 天',
    streak_7: '连续 7 天',
    streak_30: '连续 30 天'
  };

  function ensurePanel() {
    var p = document.getElementById('c8Panel');
    if (!p) {
      p = document.createElement('div');
      p.id = 'c8Panel';
      p.style.cssText = 'position:fixed;right:0;top:0;bottom:0;width:420px;max-width:92vw;' +
        'background:#fff;box-shadow:-2px 0 14px rgba(0,0,0,.12);z-index:200;' +
        'display:flex;flex-direction:column;font-family:inherit;';
      document.body.appendChild(p);
    }
    return p;
  }

  function close() {
    var p = document.getElementById('c8Panel');
    if (p) p.remove();
  }

  // -----------------------------------------------------------------
  // 2. 激励面板
  // -----------------------------------------------------------------
  function openIncentive() {
    if (!requireLogin()) return;
    var panel = ensurePanel();
    panel.innerHTML =
      '<div style="display:flex;align-items:center;gap:8px;padding:14px 16px;border-bottom:1px solid #eceef3">' +
        '<span style="font-size:16px;font-weight:700">🏆 我的激励</span>' +
        '<span style="flex:1"></span>' +
        '<button onclick="C8.close()" style="padding:5px 10px;border:none;border-radius:6px;background:#f0f2f7;cursor:pointer;font-size:12px">✕</button>' +
      '</div>' +
      '<div id="c8Body" style="flex:1;overflow-y:auto;padding:14px 16px;color:#333;font-size:13.5px;line-height:1.6">' +
        '<p style="color:#999;text-align:center">加载中…</p>' +
      '</div>';

    global.GK.api('/me/incentive').then(function (s) {
      var body = document.getElementById('c8Body');
      if (!body) return;
      if (!s.enabled) {
        body.innerHTML = '<p style="color:#888;text-align:center;padding:40px 0">激励体系当前未开启，敬请期待～</p>';
        return;
      }

      var points = s.points || 0;
      var progress = s.next_level
        ? Math.max(0, Math.min(100, Math.round(points / s.next_level * 100)))
        : 100;

      var stats = s.stats || {};
      var acc = stats.accuracy == null ? '—' : Math.round(stats.accuracy * 100) + '%';

      var achievHtml = (s.achievements || []).map(function (a) {
        var got = !!a.achieved_at;
        return '<div style="flex:0 0 33.33%;padding:6px;text-align:center">' +
          '<div style="font-size:26px;opacity:' + (got ? 1 : 0.28) + ';' + (got ? '' : 'filter:grayscale(1)') + '">' + escapeHtml(a.icon) + '</div>' +
          '<div style="font-size:12px;font-weight:600;color:' + (got ? '#4a6cf7' : '#999') + '">' + escapeHtml(a.title) + '</div>' +
          '<div style="font-size:11px;color:#aaa;margin-top:2px" title="' + escapeHtml(a.desc) + '">' +
            (got ? '✓ ' + fmtTime(a.achieved_at) : '未达成') + '</div>' +
        '</div>';
      }).join('');

      var ledgerHtml = (s.ledger || []).map(function (l) {
        var name = EVENT_NAMES[l.event] || l.event;
        var sign = l.points > 0 ? '+' + l.points : String(l.points);
        var color = l.points > 0 ? '#30a46c' : '#e5484d';
        return '<div style="display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px dashed #eef0f5">' +
          '<span style="font-weight:600;color:#333;min-width:76px">' + escapeHtml(name) + '</span>' +
          '<span style="font-weight:700;color:' + color + ';min-width:34px">' + sign + '</span>' +
          '<span style="flex:1;font-size:12px;color:#777">' + escapeHtml(l.note || '') + '</span>' +
          '<span style="font-size:11px;color:#aaa">' + fmtTime(l.created_at) + '</span>' +
        '</div>';
      }).join('');

      body.innerHTML =
        '<div style="display:flex;align-items:center;gap:14px;padding:14px;background:linear-gradient(135deg,#4a6cf7,#7c5cf0);border-radius:12px;color:#fff;margin-bottom:12px">' +
          '<div style="font-size:40px">' + (s.level_name === '大师' ? '👑' : '🏅') + '</div>' +
          '<div style="flex:1">' +
            '<div style="font-size:20px;font-weight:800">' + points + ' 分</div>' +
            '<div style="font-size:12.5px;opacity:.92">' + escapeHtml(s.level_name) + ' Lv.' + s.level +
              (s.next_level ? ' · 距 ' + escapeHtml(s.next_level_name) + ' 还差 ' + (s.next_level - points) + ' 分' : ' · 已满级') + '</div>' +
            '<div style="height:6px;background:rgba(255,255,255,.28);border-radius:3px;margin-top:6px;overflow:hidden">' +
              '<div style="height:100%;width:' + progress + '%;background:#ffd76a;border-radius:3px"></div>' +
            '</div>' +
          '</div>' +
        '</div>' +

        '<div style="display:flex;gap:10px;margin-bottom:12px">' +
          '<div style="flex:1;padding:10px;background:#fff7ed;border-radius:10px;text-align:center">' +
            '<div style="font-size:20px;font-weight:800;color:#ea580c">' + (s.streak_days || 0) + '</div>' +
            '<div style="font-size:11.5px;color:#c2703d">🔥 连续学习</div>' +
          '</div>' +
          '<div style="flex:1;padding:10px;background:#f0fdf4;border-radius:10px;text-align:center">' +
            '<div style="font-size:20px;font-weight:800;color:#16a34a">' + (stats.total || 0) + '</div>' +
            '<div style="font-size:11.5px;color:#4a8f5f">📖 累计答题</div>' +
          '</div>' +
          '<div style="flex:1;padding:10px;background:#f0f4ff;border-radius:10px;text-align:center">' +
            '<div style="font-size:20px;font-weight:800;color:#4a6cf7">' + acc + '</div>' +
            '<div style="font-size:11.5px;color:#5a74b8">✅ 客观正确率</div>' +
          '</div>' +
        '</div>' +

        '<div style="font-size:13px;font-weight:700;color:#333;margin:4px 0 8px">🎖️ 成就</div>' +
        '<div style="display:flex;flex-wrap:wrap;background:#fafbfe;border:1px solid #eef0f5;border-radius:10px;padding:8px 4px;margin-bottom:12px">' +
          achievHtml +
        '</div>' +

        '<div style="font-size:13px;font-weight:700;color:#333;margin:4px 0 8px">🧾 最近积分流水</div>' +
        (ledgerHtml || '<p style="color:#999">暂无记录，快去答题赚积分吧</p>');
    }).catch(function (e) {
      var body = document.getElementById('c8Body');
      if (body) body.innerHTML = '<p style="color:#e5484d;text-align:center;padding:40px 0">加载失败: ' + escapeHtml(e && e.message ? e.message : e) + '</p>';
    });
  }

  // -----------------------------------------------------------------
  // 3. 挂载对外 API
  // -----------------------------------------------------------------
  global.C8 = {
    openIncentive: openIncentive,
    close: close
  };
  global.GK.c8Ready = true;

})(typeof window !== 'undefined' ? window : globalThis);

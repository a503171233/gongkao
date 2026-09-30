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
      global.GK.promptLogin('激励面板');
      return false;
    }
    return true;
  }

  function showToast(msg, type) {
    var div = document.createElement('div');
    div.style.cssText = 'position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;color:#fff;font-size:14px;z-index:9999;box-shadow:0 2px 12px rgba(0,0,0,.15);';
    div.style.background = type === 'error' ? 'var(--bad)' : (type === 'success' ? 'var(--good)' : 'var(--wood)');
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
      '<div style="display:flex;align-items:center;gap:8px;padding:14px 16px;border-bottom:1px solid var(--line)">' +
        '<span style="font-size:16px;font-weight:700">🏆 我的激励</span>' +
        '<span style="flex:1"></span>' +
        '<button onclick="C8.close()" style="padding:5px 10px;border:none;border-radius:6px;background:var(--paper-2);cursor:pointer;font-size:12px">✕</button>' +
      '</div>' +
      '<div id="c8Body" style="flex:1;overflow-y:auto;padding:14px 16px;color:var(--ink);font-size:13.5px;line-height:1.6">' +
        '<p style="color:var(--ink-3);text-align:center">加载中…</p>' +
      '</div>';

    global.GK.api('/me/incentive').then(function (s) {
      var body = document.getElementById('c8Body');
      if (!body) return;
      if (!s.enabled) {
        body.innerHTML = '<p style="color:var(--ink-3);text-align:center;padding:40px 0">激励体系当前未开启，敬请期待～</p>';
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
          '<div style="font-size:12px;font-weight:600;color:' + (got ? 'var(--wood)' : 'var(--ink-3)') + '">' + escapeHtml(a.title) + '</div>' +
          '<div style="font-size:11px;color:var(--ink-3);margin-top:2px" title="' + escapeHtml(a.desc) + '">' +
            (got ? '✓ ' + fmtTime(a.achieved_at) : '未达成') + '</div>' +
        '</div>';
      }).join('');

      var ledgerHtml = (s.ledger || []).map(function (l) {
        var name = EVENT_NAMES[l.event] || l.event;
        var sign = l.points > 0 ? '+' + l.points : String(l.points);
        var color = l.points > 0 ? 'var(--good)' : 'var(--bad)';
        return '<div style="display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px dashed var(--line)">' +
          '<span style="font-weight:600;color:var(--ink);min-width:76px">' + escapeHtml(name) + '</span>' +
          '<span style="font-weight:700;color:' + color + ';min-width:34px">' + sign + '</span>' +
          '<span style="flex:1;font-size:12px;color:#777">' + escapeHtml(l.note || '') + '</span>' +
          '<span style="font-size:11px;color:var(--ink-3)">' + fmtTime(l.created_at) + '</span>' +
        '</div>';
      }).join('');

      body.innerHTML =
        '<div style="display:flex;align-items:center;gap:14px;padding:14px;background:linear-gradient(135deg,var(--wood),var(--gold-deep));border-radius:12px;color:#fff;margin-bottom:12px">' +
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
            '<div style="font-size:20px;font-weight:800;color:#5f7d52">' + (stats.total || 0) + '</div>' +
            '<div style="font-size:11.5px;color:var(--good)">📖 累计答题</div>' +
          '</div>' +
          '<div style="flex:1;padding:10px;background:var(--gold-soft);border-radius:10px;text-align:center">' +
            '<div style="font-size:20px;font-weight:800;color:var(--wood)">' + acc + '</div>' +
            '<div style="font-size:11.5px;color:var(--wood)">✅ 客观正确率</div>' +
          '</div>' +
        '</div>' +

        '<div style="font-size:13px;font-weight:700;color:var(--ink);margin:4px 0 8px">🎖️ 成就</div>' +
        '<div style="display:flex;flex-wrap:wrap;background:#fafbfe;border:1px solid var(--line);border-radius:10px;padding:8px 4px;margin-bottom:12px">' +
          achievHtml +
        '</div>' +

        '<div id="c8Board" style="margin-bottom:12px"><p style="color:var(--ink-3);font-size:11.5px;margin:4px 0 8px">排行榜加载中…</p></div>' +

        '<div style="font-size:13px;font-weight:700;color:var(--ink);margin:4px 0 8px">🧾 最近积分流水</div>' +
        (ledgerHtml || '<p style="color:var(--ink-3)">暂无记录，快去答题赚积分吧</p>');
    }).catch(function (e) {
      var body = document.getElementById('c8Body');
      if (body) body.innerHTML = '<p style="color:var(--bad);text-align:center;padding:40px 0">加载失败: ' + escapeHtml(e && e.message ? e.message : e) + '</p>';
    });

    // 排行榜异步填充（失败静默，不影响主面板）
    global.GK.api('/me/incentive/board').then(function (b) {
      var box = document.getElementById('c8Board');
      if (!box || !b || !b.board) return;
      var rows = (b.board || []).map(function (r, i) {
        var medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : String(i + 1);
        return '<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px dashed var(--line);font-size:12.5px">' +
          '<span style="min-width:26px;text-align:center;font-weight:700;color:' + (i < 3 ? '#d97706' : 'var(--ink-3)') + '">' + medal + '</span>' +
          '<span style="flex:1;font-weight:600;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escapeHtml(r.username) + '</span>' +
          '<span style="font-size:11.5px;color:var(--ink-3)">Lv.' + escapeHtml(r.level) + ' · ' + (r.streak_days || 0) + ' 天</span>' +
          '<span style="min-width:56px;text-align:right;font-weight:800;color:var(--wood)">' + (r.points || 0) + ' 分</span>' +
        '</div>';
      }).join('');
      box.innerHTML =
        '<div style="font-size:13px;font-weight:700;color:var(--ink);margin:4px 0 8px">🏆 排行榜' +
          (b.my_rank ? '<span style="font-weight:400;color:var(--ink-3);font-size:11.5px">（我第 ' + b.my_rank + ' 名）</span>' : '') + '</div>' +
        '<div style="background:#fafbfe;border:1px solid var(--line);border-radius:10px;padding:8px 10px">' +
          (rows || '<p style="color:var(--ink-3)">暂无上榜数据，答题即上榜</p>') +
        '</div>';
    }).catch(function () {
      var box = document.getElementById('c8Board');
      if (box) box.innerHTML = '';
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

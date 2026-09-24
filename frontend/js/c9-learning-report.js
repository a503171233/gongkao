/* =====================================================================
 * C9 · 学习报告模块（网站一 前端）
 * ---------------------------------------------------------------------
 * 职责：#20 学习报告面板 —— 模考成绩趋势 + 学情周报 + 复习/收藏/错题概览
 * 复用 A4 (GK.api) 传输层；数据来自 GET /me/learning-report。
 *
 * 对外能力：
 *   C9.openReport() —— 打开学习报告面板
 *   C9.close()       —— 关闭面板
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

  function fmtDay(s) {
    var d = String(s || '');
    return d.length >= 10 ? d.slice(5, 10) : d;
  }

  function currentUserId() {
    return global.GK.store.userId || 'anonymous';
  }

  function requireLogin() {
    if (currentUserId() === 'anonymous') {
      alert('请先登录后查看学习报告');
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

  function ensurePanel() {
    var p = document.getElementById('c9Panel');
    if (!p) {
      p = document.createElement('div');
      p.id = 'c9Panel';
      p.style.cssText = 'position:fixed;right:0;top:0;bottom:0;width:460px;max-width:94vw;' +
        'background:#fff;box-shadow:-2px 0 14px rgba(0,0,0,.12);z-index:200;' +
        'display:flex;flex-direction:column;font-family:inherit;';
      document.body.appendChild(p);
    }
    return p;
  }

  function close() {
    var p = document.getElementById('c9Panel');
    if (p) p.remove();
  }

  function pct(v) {
    if (v == null) return '—';
    return Math.round(v * 100) + '%';
  }

  function bar(color, widthPct, bg) {
    return '<div style="height:7px;background:' + (bg || '#eef0f5') + ';border-radius:4px;overflow:hidden">' +
      '<div style="height:100%;width:' + Math.max(0, Math.min(100, widthPct)) + '%;background:' + color + ';border-radius:4px"></div>' +
      '</div>';
  }

  // -----------------------------------------------------------------
  // 2. 渲染
  // -----------------------------------------------------------------
  function card(title, icon) {
    return '<div style="font-size:13px;font-weight:700;color:#333;margin:16px 0 8px;display:flex;align-items:center;gap:6px">' +
      '<span>' + icon + '</span><span>' + title + '</span></div>';
  }

  function renderTrend(trend) {
    if (!trend || !trend.length) {
      return '<p style="color:#999;text-align:center;padding:14px 0">暂无已评分模考记录，快去组卷刷一套吧～</p>';
    }
    var maxPct = 100;
    return '<div style="display:flex;align-items:flex-end;gap:6px;height:110px;padding:6px 2px">' +
      trend.map(function (t) {
        var h = t.avg_score == null ? 0 : Math.round(t.avg_score * 100);
        return '<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%">' +
          '<div style="font-size:10px;color:#4a6cf7;font-weight:700">' + (t.avg_score == null ? '—' : Math.round(t.avg_score * 100)) + '</div>' +
          '<div style="width:70%;background:linear-gradient(180deg,#7c5cf0,#4a6cf7);border-radius:4px 4px 0 0;height:' + h + '%"></div>' +
          '<div style="font-size:10px;color:#999;margin-top:3px;white-space:nowrap">' + escapeHtml(fmtDay(t.d)) + '</div>' +
        '</div>';
      }).join('') +
      '</div>';
  }

  function renderWeeklyTrend(days) {
    if (!days || !days.length) {
      return '<p style="color:#999;text-align:center;padding:14px 0">近 7 天暂无练习记录</p>';
    }
    var maxN = Math.max.apply(null, days.map(function (d) { return d.n || 0; })) || 1;
    return '<div style="display:flex;align-items:flex-end;gap:6px;height:96px;padding:6px 2px">' +
      days.map(function (d) {
        var n = d.n || 0;
        var ok = d.ok || 0;
        var h = Math.round(n / maxN * 100);
        return '<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%">' +
          '<div style="font-size:10px;color:#16a34a;font-weight:700">' + ok + '/' + n + '</div>' +
          '<div style="width:70%;height:' + h + '%;background:linear-gradient(180deg,#34d399,#16a34a);border-radius:4px 4px 0 0"></div>' +
          '<div style="font-size:10px;color:#999;margin-top:3px;white-space:nowrap">' + escapeHtml(fmtDay(d.d)) + '</div>' +
        '</div>';
      }).join('') +
      '</div>';
  }

  function renderCategories(cats) {
    if (!cats || !cats.length) {
      return '<p style="color:#999;text-align:center;padding:10px 0">暂无分类统计</p>';
    }
    return cats.map(function (c) {
      var acc = c.total ? Math.round(c.accuracy * 100) : 0;
      var color = acc >= 80 ? '#16a34a' : (acc >= 60 ? '#f59e0b' : '#e5484d');
      return '<div style="margin-bottom:9px">' +
        '<div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:3px">' +
          '<span style="color:#333;font-weight:600">' + escapeHtml(c.category) + '</span>' +
          '<span style="color:#888">' + c.correct + '/' + c.total + ' · <b style="color:' + color + '">' + acc + '%</b></span>' +
        '</div>' +
        bar(color, acc) +
      '</div>';
    }).join('');
  }

  function renderExam(exam) {
    var s = exam && exam.summary ? exam.summary : {};
    return '<div style="display:flex;gap:10px;margin-bottom:4px">' +
      '<div style="flex:1;padding:10px;background:#f5f3ff;border-radius:10px;text-align:center">' +
        '<div style="font-size:20px;font-weight:800;color:#7c3aed">' + (s.papers || 0) + '</div>' +
        '<div style="font-size:11.5px;color:#8b6cc4">📝 模考次数</div>' +
      '</div>' +
      '<div style="flex:1;padding:10px;background:#f0f4ff;border-radius:10px;text-align:center">' +
        '<div style="font-size:20px;font-weight:800;color:#4a6cf7">' + pct(s.avg_score) + '</div>' +
        '<div style="font-size:11.5px;color:#5a74b8">🎯 平均得分</div>' +
      '</div>' +
      '<div style="flex:1;padding:10px;background:#f0fdf4;border-radius:10px;text-align:center">' +
        '<div style="font-size:20px;font-weight:800;color:#16a34a">' + pct(s.best_score) + '</div>' +
        '<div style="font-size:11.5px;color:#4a8f5f">🏅 最佳成绩</div>' +
      '</div>' +
    '</div>' +
    card('📈 成绩趋势', '') +
    renderTrend(exam && exam.trend);
  }

  function renderWeekly(weekly) {
    if (!weekly) return '';
    var weakHtml = (weekly.weak || []).length
      ? '<div style="display:flex;flex-wrap:wrap;gap:6px">' +
        weekly.weak.map(function (w) {
          return '<span style="padding:3px 10px;background:#fef2f2;color:#dc2626;border-radius:12px;font-size:12px">' + escapeHtml(w) + '</span>';
        }).join('') + '</div>'
      : '<p style="color:#999;font-size:12.5px">暂无薄弱分类，继续保持 💪</p>';

    var essay = weekly.essay || {};
    var essayHtml = '<div style="display:flex;justify-content:space-between;font-size:12.5px;color:#666">' +
      '<span>待批改：<b style="color:#f59e0b">' + (essay.pending || 0) + '</b> 篇</span>' +
      '<span>主观题均分：<b style="color:#4a6cf7">' + (essay.avg_score == null ? '—' : essay.avg_score) + '</b></span>' +
      '</div>';

    return card('📊 学情周报', '') +
      '<div style="display:flex;gap:10px;margin-bottom:4px">' +
        '<div style="flex:1;padding:10px;background:#f8fafc;border-radius:10px;text-align:center">' +
          '<div style="font-size:20px;font-weight:800;color:#333">' + (weekly.graded || 0) + '</div>' +
          '<div style="font-size:11.5px;color:#888">已批改题</div>' +
        '</div>' +
        '<div style="flex:1;padding:10px;background:#f8fafc;border-radius:10px;text-align:center">' +
          '<div style="font-size:20px;font-weight:800;color:#16a34a">' + pct(weekly.accuracy) + '</div>' +
          '<div style="font-size:11.5px;color:#888">总体正确率</div>' +
        '</div>' +
        '<div style="flex:1;padding:10px;background:#f8fafc;border-radius:10px;text-align:center">' +
          '<div style="font-size:20px;font-weight:800;color:#4a6cf7">' + (weekly.correct || 0) + '</div>' +
          '<div style="font-size:11.5px;color:#888">答对题数</div>' +
        '</div>' +
      '</div>' +
      card('📅 近 7 天练习量', '') +
      renderWeeklyTrend(weekly.recent_days) +
      card('🧩 分类正确率', '') +
      renderCategories(weekly.by_category) +
      card('⚠️ 薄弱分类', '') +
      weakHtml +
      card('✍️ 主观题批改', '') +
      essayHtml;
  }

  function renderReview(review) {
    if (!review) return '';
    var nxt = review.next_review_at ? fmtTime(review.next_review_at) : '无待复习';
    return card('🔁 错题复习', '') +
      '<div style="display:flex;gap:10px;margin-bottom:4px">' +
        '<div style="flex:1;padding:10px;background:#fff7ed;border-radius:10px;text-align:center">' +
          '<div style="font-size:20px;font-weight:800;color:#ea580c">' + (review.due || 0) + '</div>' +
          '<div style="font-size:11.5px;color:#c2703d">今日待复习</div>' +
        '</div>' +
        '<div style="flex:1;padding:10px;background:#f0f4ff;border-radius:10px;text-align:center">' +
          '<div style="font-size:20px;font-weight:800;color:#4a6cf7">' + (review.in_queue || 0) + '</div>' +
          '<div style="font-size:11.5px;color:#5a74b8">复习队列</div>' +
        '</div>' +
        '<div style="flex:1;padding:10px;background:#f0fdf4;border-radius:10px;text-align:center">' +
          '<div style="font-size:20px;font-weight:800;color:#16a34a">' + (review.mastered || 0) + '</div>' +
          '<div style="font-size:11.5px;color:#4a8f5f">已掌握</div>' +
        '</div>' +
      '</div>' +
      '<div style="font-size:12px;color:#888;margin-top:6px">下次复习：<b style="color:#666">' + escapeHtml(nxt) + '</b></div>';
  }

  function renderInventory(inv) {
    if (!inv) return '';
    return card('📚 学习资产', '') +
      '<div style="display:flex;gap:10px">' +
        '<div style="flex:1;padding:10px;background:#fefce8;border-radius:10px;text-align:center">' +
          '<div style="font-size:20px;font-weight:800;color:#ca8a04">' + (inv.favorites || 0) + '</div>' +
          '<div style="font-size:11.5px;color:#a08c3a">📌 收藏</div>' +
        '</div>' +
        '<div style="flex:1;padding:10px;background:#fef2f2;border-radius:10px;text-align:center">' +
          '<div style="font-size:20px;font-weight:800;color:#dc2626">' + (inv.mistakes || 0) + '</div>' +
          '<div style="font-size:11.5px;color:#b05a5a">❌ 错题</div>' +
        '</div>' +
      '</div>';
  }

  function renderBody(s) {
    return renderExam(s.exam) +
      renderWeekly(s.weekly) +
      renderReview(s.review) +
      renderInventory(s.inventory);
  }

  // -----------------------------------------------------------------
  // 3. 面板入口
  // -----------------------------------------------------------------
  function openReport() {
    if (!requireLogin()) return;
    var panel = ensurePanel();
    panel.innerHTML =
      '<div style="display:flex;align-items:center;gap:8px;padding:14px 16px;border-bottom:1px solid #eceef3">' +
        '<span style="font-size:16px;font-weight:700">📈 学习报告</span>' +
        '<span style="flex:1"></span>' +
        '<button onclick="C9.close()" style="padding:5px 10px;border:none;border-radius:6px;background:#f0f2f7;cursor:pointer;font-size:12px">✕</button>' +
      '</div>' +
      '<div id="c9Body" style="flex:1;overflow-y:auto;padding:14px 16px;color:#333;font-size:13.5px;line-height:1.6">' +
        '<p style="color:#999;text-align:center">加载中…</p>' +
      '</div>';

    global.GK.api('/me/learning-report').then(function (s) {
      var body = document.getElementById('c9Body');
      if (!body) return;
      body.innerHTML = renderBody(s);
    }).catch(function (e) {
      var body = document.getElementById('c9Body');
      if (body) body.innerHTML = '<p style="color:#e5484d;text-align:center;padding:40px 0">加载失败: ' + escapeHtml(e && e.message ? e.message : e) + '</p>';
    });
  }

  // -----------------------------------------------------------------
  // 4. 挂载对外 API
  // -----------------------------------------------------------------
  global.C9 = {
    openReport: openReport,
    close: close
  };
  global.GK.c9Ready = true;

})(typeof window !== 'undefined' ? window : globalThis);

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
      global.GK.promptLogin('学习报告');
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
    // 批次26：高光渐变条 + scaleX 生长动画（动画在 .bar-fill CSS 类）
    return '<div style="height:7px;background:' + (bg || 'var(--line)') + ';border-radius:4px;overflow:hidden">' +
      '<div class="bar-fill" style="height:100%;width:' + Math.max(0, Math.min(100, widthPct)) + '%;background:' +
      'linear-gradient(90deg,rgba(255,255,255,.22),rgba(255,255,255,0) 65%),' + color + '"></div>' +
      '</div>';
  }

  // -----------------------------------------------------------------
  // 2. 渲染
  // -----------------------------------------------------------------
  function card(title, icon) {
    return '<div style="font-size:13px;font-weight:700;color:var(--ink);margin:16px 0 8px;display:flex;align-items:center;gap:6px">' +
      '<span>' + icon + '</span><span>' + title + '</span></div>';
  }

  function renderTrend(trend) {
    if (!trend || !trend.length) {
      return '<p style="color:var(--ink-3);text-align:center;padding:14px 0">暂无已评分模考记录，快去组卷刷一套吧～</p>';
    }
    var maxPct = 100;
    return '<div style="display:flex;align-items:flex-end;gap:6px;height:110px;padding:6px 2px">' +
      trend.map(function (t) {
        var h = t.avg_score == null ? 0 : Math.round(t.avg_score * 100);
        return '<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%">' +
          '<div style="font-size:10px;color:var(--wood);font-weight:700">' + (t.avg_score == null ? '—' : Math.round(t.avg_score * 100)) + '</div>' +
          '<div style="width:70%;background:linear-gradient(180deg,var(--gold-deep),var(--wood));border-radius:4px 4px 0 0;height:' + h + '%"></div>' +
          '<div style="font-size:10px;color:var(--ink-3);margin-top:3px;white-space:nowrap">' + escapeHtml(fmtDay(t.d)) + '</div>' +
        '</div>';
      }).join('') +
      '</div>';
  }

  function renderWeeklyTrend(days) {
    if (!days || !days.length) {
      return '<p style="color:var(--ink-3);text-align:center;padding:14px 0">近 7 天暂无练习记录</p>';
    }
    var maxN = Math.max.apply(null, days.map(function (d) { return d.n || 0; })) || 1;
    return '<div style="font-size:11.5px;color:var(--ink-3);margin:0 0 6px">柱上数字 = 当天答对 / 答题数</div>' +
      '<div style="display:flex;align-items:flex-end;gap:6px;height:96px;padding:6px 2px">' +
      days.map(function (d) {
        var n = d.n || 0;
        var ok = d.ok || 0;
        var h = Math.round(n / maxN * 100);
        return '<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%">' +
          '<div style="font-size:10px;color:#5f7d52;font-weight:700">' + ok + '/' + n + '</div>' +
          '<div style="width:70%;height:' + h + '%;background:linear-gradient(180deg,var(--good),#5f7d52);border-radius:4px 4px 0 0"></div>' +
          '<div style="font-size:10px;color:var(--ink-3);margin-top:3px;white-space:nowrap">' + escapeHtml(fmtDay(d.d)) + '</div>' +
        '</div>';
      }).join('') +
      '</div>';
  }

  function renderCategories(cats) {
    if (!cats || !cats.length) {
      return '<p style="color:var(--ink-3);text-align:center;padding:10px 0">暂无分类统计</p>';
    }
    return cats.map(function (c) {
      var acc = c.total ? Math.round(c.accuracy * 100) : 0;
      var color = acc >= 80 ? '#5f7d52' : (acc >= 60 ? '#f59e0b' : 'var(--bad)');
      return '<div style="margin-bottom:9px">' +
        '<div style="display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:3px">' +
          '<span style="color:var(--ink);font-weight:600">' + escapeHtml(c.category) + '</span>' +
          '<span style="color:var(--ink-3)">' + c.correct + '/' + c.total + ' · <b style="color:' + color + '">' + acc + '%</b></span>' +
        '</div>' +
        bar(color, acc) +
      '</div>';
    }).join('');
  }

  function renderExam(exam) {
    var s = exam && exam.summary ? exam.summary : {};
    return '<div style="display:flex;gap:10px;margin-bottom:4px">' +
      '<div style="flex:1;padding:10px;background:var(--gold-soft);border-radius:10px;text-align:center">' +
        '<div style="font-size:20px;font-weight:800;color:var(--gold-deep)">' + (s.papers || 0) + '</div>' +
        '<div style="font-size:11.5px;color:var(--gold-deep)">📝 模考次数</div>' +
      '</div>' +
      '<div style="flex:1;padding:10px;background:var(--gold-soft);border-radius:10px;text-align:center">' +
        '<div style="font-size:20px;font-weight:800;color:var(--wood)">' + pct(s.avg_score) + '</div>' +
        '<div style="font-size:11.5px;color:var(--wood)">🎯 平均得分</div>' +
      '</div>' +
      '<div style="flex:1;padding:10px;background:#f0fdf4;border-radius:10px;text-align:center">' +
        '<div style="font-size:20px;font-weight:800;color:#5f7d52">' + pct(s.best_score) + '</div>' +
        '<div style="font-size:11.5px;color:var(--good)">🏅 最佳成绩</div>' +
      '</div>' +
    '</div>' +
    card('📈 成绩趋势', '') +
    renderTrend(exam && exam.trend);
  }

  function renderWeekly(weekly) {
    if (!weekly) return '';
    var lowSample = (weekly.by_category || []).some(function (c) {
      return c.total > 0 && c.total < 3 && c.accuracy !== null && c.accuracy < 0.6;
    });
    var weakHtml = (weekly.weak || []).length
      ? '<div style="display:flex;flex-wrap:wrap;gap:6px">' +
        weekly.weak.map(function (w) {
          return '<span style="padding:3px 10px;background:#fef2f2;color:#dc2626;border-radius:12px;font-size:12px">' + escapeHtml(w) + '</span>';
        }).join('') + '</div>'
      : (lowSample
        ? '<p style="color:var(--ink-3);font-size:12.5px">低正确率的分类题量尚不足 3 题，暂不判定薄弱；多刷几题即可纳入分析 📊</p>'
        : '<p style="color:var(--ink-3);font-size:12.5px">暂无薄弱分类，继续保持 💪</p>');

    var essay = weekly.essay || {};
    var essayHtml = '<div style="display:flex;justify-content:space-between;font-size:12.5px;color:var(--ink-2)">' +
      '<span>待批改：<b style="color:#f59e0b">' + (essay.pending || 0) + '</b> 篇</span>' +
      '<span>主观题均分：<b style="color:var(--wood)">' + (essay.avg_score == null ? '—' : essay.avg_score) + '</b></span>' +
      '</div>';

    return card('📊 学情周报', '') +
      '<div style="display:flex;gap:10px;margin-bottom:4px">' +
        '<div style="flex:1;padding:10px;background:var(--paper-2);border-radius:10px;text-align:center">' +
          '<div style="font-size:20px;font-weight:800;color:var(--ink)">' + (weekly.graded || 0) + '</div>' +
          '<div style="font-size:11.5px;color:var(--ink-3)">已批改题</div>' +
        '</div>' +
        '<div style="flex:1;padding:10px;background:var(--paper-2);border-radius:10px;text-align:center">' +
          '<div style="font-size:20px;font-weight:800;color:#5f7d52">' + pct(weekly.accuracy) + '</div>' +
          '<div style="font-size:11.5px;color:var(--ink-3)">总体正确率</div>' +
        '</div>' +
        '<div style="flex:1;padding:10px;background:var(--paper-2);border-radius:10px;text-align:center">' +
          '<div style="font-size:20px;font-weight:800;color:var(--wood)">' + (weekly.correct || 0) + '</div>' +
          '<div style="font-size:11.5px;color:var(--ink-3)">答对题数</div>' +
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
        '<div style="flex:1;padding:10px;background:var(--gold-soft);border-radius:10px;text-align:center">' +
          '<div style="font-size:20px;font-weight:800;color:var(--wood)">' + (review.in_queue || 0) + '</div>' +
          '<div style="font-size:11.5px;color:var(--wood)">复习队列</div>' +
        '</div>' +
        '<div style="flex:1;padding:10px;background:#f0fdf4;border-radius:10px;text-align:center">' +
          '<div style="font-size:20px;font-weight:800;color:#5f7d52">' + (review.mastered || 0) + '</div>' +
          '<div style="font-size:11.5px;color:var(--good)">已掌握</div>' +
        '</div>' +
      '</div>' +
      '<div style="font-size:12px;color:var(--ink-3);margin-top:6px">下次复习：<b style="color:var(--ink-2)">' + escapeHtml(nxt) + '</b></div>';
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
      '<div style="display:flex;align-items:center;gap:8px;padding:14px 16px;border-bottom:1px solid var(--line)">' +
        '<span style="font-size:16px;font-weight:700">📈 学习报告</span>' +
        '<span style="flex:1"></span>' +
        '<button onclick="C9.printReport()" class="no-print" style="padding:5px 10px;border:1px solid var(--wood);color:var(--wood);border-radius:6px;background:#fff;cursor:pointer;font-size:12px">🖨 导出 PDF</button>' +
        '<button onclick="C9.docxReport()" class="no-print" style="padding:5px 10px;border:1px solid var(--wood);color:var(--wood);border-radius:6px;background:#fff;cursor:pointer;font-size:12px">📄 导出 DOCX</button>' +
        '<button onclick="C9.close()" style="padding:5px 10px;border:none;border-radius:6px;background:var(--paper-2);cursor:pointer;font-size:12px">✕</button>' +
      '</div>' +
      '<div id="c9Body" style="flex:1;overflow-y:auto;padding:14px 16px;color:var(--ink);font-size:13.5px;line-height:1.6">' +
        '<p style="color:var(--ink-3);text-align:center">加载中…</p>' +
      '</div>';

    global.GK.api('/me/learning-report').then(function (s) {
      var body = document.getElementById('c9Body');
      if (!body) return;
      body.innerHTML = renderBody(s);
    }).catch(function (e) {
      var body = document.getElementById('c9Body');
      if (body) body.innerHTML = '<p style="color:var(--bad);text-align:center;padding:40px 0">加载失败: ' + escapeHtml(e && e.message ? e.message : e) + '</p>';
    });
  }

  // -----------------------------------------------------------------
  // 4. 打印/导出 PDF（GK.printExport：新窗口 + 浏览器打印，零依赖）
  // -----------------------------------------------------------------
  function printReport() {
    var body = document.getElementById('c9Body');
    if (!body || !body.innerHTML.trim() || body.innerHTML.indexOf('加载中') >= 0) {
      return;  // 报告未加载完成时不导出
    }
    global.GK.printExport('学习报告', body.innerHTML);
  }

  function docxReport() {
    var body = document.getElementById('c9Body');
    if (!body || !body.innerHTML.trim()) {
      if (global.GK.toast) global.GK.toast('报告未加载完成，暂不能导出', 'error');
      return;
    }
    global.GK.docxExport('学习报告', body.innerHTML);
  }

  // -----------------------------------------------------------------
  // 5. 挂载对外 API
  // -----------------------------------------------------------------
  global.C9 = {
    openReport: openReport,
    printReport: printReport,
    docxReport: docxReport,
    close: close
  };
  global.GK.c9Ready = true;

})(typeof window !== 'undefined' ? window : globalThis);

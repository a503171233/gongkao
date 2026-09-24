/* =====================================================================
 * C4 · 学员增值功能模块（网站一 前端）
 * ---------------------------------------------------------------------
 * 职责：收藏/错题本/练习入口与交互
 * 复用 A4 (GK.api) 传输层，不重复造轮子
 *
 * 对外能力：
 *   GK.openFavorites()    —— 打开我的收藏面板
 *   GK.openMistakes()     —— 打开错题本面板
 *   GK.openPractice()     —— 开始练习
 *   GK.bookmarkCurrent()  —— 收藏当前问答（由 A5 回调调用）
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
      .replace(/"/g, '&quot;');
  }

  function currentUserId() {
    return global.GK.store.userId || 'anonymous';
  }

  // #33 R2 题干配图渲染：![图N](/api/qimg/...) → <img>（URL 白名单防 XSS，与后端一致）
  var QIMG_URL_RE = /^\/api\/qimg\/[A-Za-z0-9_-]+\/[a-f0-9]{12}\.(png|jpe?g|gif|webp)$/;
  var QIMG_MARK_RE = /!\[([^\]]*)\]\(([^)\s]+)\)/g;
  function renderQText(text) {
    return escapeHtml(text).replace(QIMG_MARK_RE, function (m, alt, url) {
      if (!QIMG_URL_RE.test(url)) return '';
      return '<img src="' + url + '" alt="' + alt + '" loading="lazy" ' +
        'style="max-width:100%;max-height:300px;margin:4px 0;display:block;cursor:zoom-in" ' +
        'onclick="window.open(\'' + url + '\', \'_blank\')" title="点击查看大图">';
    });
  }

  function requireLogin() {
    if (currentUserId() === 'anonymous') {
      alert('请先登录后使用收藏/错题本/练习功能');
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

  // CSV 导出（GK.api 对非 JSON 响应返回原始文本；带 BOM，Excel 可直接打开）
  function exportCsv(path, filename) {
    global.GK.api(path, { timeout: 30000 }).then(function (csv) {
      var blob = new Blob([String(csv)], { type: 'text/csv;charset=utf-8' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
    }).catch(function (e) {
      showToast('导出失败: ' + (e && e.message ? e.message : e), 'error');
    });
  }

  // 练习统计面板（C1 拓展：总量/正确率/按老师/近 7 天）
  function showPracticeStats() {
    if (!requireLogin()) return;
    global.GK.api('/practice/stats').then(function (s) {
      var box = document.getElementById('practiceArea');
      if (!box) return;
      var acc = (s.accuracy == null) ? '—' : Math.round(s.accuracy * 100) + '%';
      var byT = (s.by_teacher || []).map(function (t) {
        return '<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px dashed #e5e7eb;font-size:13px">' +
          '<span>' + escapeHtml(t.teacher_id) + '</span>' +
          '<span>' + t.n + ' 题 · 正确 ' + (t.ok || 0) + (t.pending ? ' · ' + t.pending + ' 待批' : '') + '</span></div>';
      }).join('');
      var days = (s.recent_days || []).map(function (d) {
        return '<span style="display:inline-block;margin:2px 6px 0 0;padding:3px 8px;border-radius:8px;background:#f0f4ff;font-size:12px">' +
          escapeHtml(d.d.slice(5)) + ' · ' + d.n + '题</span>';
      }).join('');
      box.innerHTML =
        '<div style="padding:12px;background:#f8faff;border-radius:8px;margin-bottom:10px">' +
          '<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px">' +
            '<div><div style="font-size:12px;color:#888">累计练习</div><div style="font-size:22px;font-weight:700;color:#4a6cf7">' + s.total + '</div></div>' +
            '<div><div style="font-size:12px;color:#888">客观正确率</div><div style="font-size:22px;font-weight:700;color:#30a46c">' + acc + '</div></div>' +
            '<div><div style="font-size:12px;color:#888">已批改</div><div style="font-size:22px;font-weight:700">' + s.graded + '</div></div>' +
          '</div>' +
          '<div style="font-size:12px;color:#888;margin-bottom:4px">按老师</div>' + (byT || '<div style="font-size:13px;color:#999">暂无练习记录</div>') +
          (days ? '<div style="font-size:12px;color:#888;margin:8px 0 4px">近 7 天</div><div>' + days + '</div>' : '') +
        '</div>' +
        '<button onclick="C4.openPractice()" style="padding:8px 16px;border:1px solid #d0d4de;border-radius:6px;background:#fff;cursor:pointer">返回练习</button>';
    }).catch(function (e) {
      showToast('统计加载失败: ' + (e && e.message ? e.message : e), 'error');
    });
  }

  // -----------------------------------------------------------------
  // 2. 收藏功能
  // -----------------------------------------------------------------
  function openFavorites() {
    if (!requireLogin()) return;

    var container = document.getElementById('c4Panel');
    if (!container) {
      container = document.createElement('div');
      container.id = 'c4Panel';
      container.style.cssText = 'position:fixed;right:0;top:60px;bottom:0;width:400px;background:#fff;box-shadow:-2px 0 12px rgba(0,0,0,.1);z-index:100;overflow-y:auto;padding:20px;';
      document.body.appendChild(container);
    }

    container.innerHTML = '<h3>📌 我的收藏</h3>' +
      '<div style="margin:8px 0"><button onclick="C4.exportFavorites()" style="padding:5px 12px;border:1px solid #4a6cf7;color:#4a6cf7;border-radius:6px;background:#fff;cursor:pointer;font-size:12px">⬇ 导出 CSV</button></div>' +
      '<div id="favList" style="margin-top:8px"></div>' +
      '<button onclick="document.getElementById(\'c4Panel\').remove()" style="margin-top:12px;padding:8px 16px;border:none;border-radius:6px;background:#f0f2f7;cursor:pointer">关闭</button>';

    global.GK.api('/favorites').then(function (d) {
      var list = d.favorites || [];
      var el = document.getElementById('favList');
      if (!list.length) {
        el.innerHTML = '<p style="color:#999">暂无收藏</p>';
        return;
      }
      list.forEach(function (f) {
        var div = document.createElement('div');
        div.style.cssText = 'padding:12px;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:8px;';
        div.innerHTML = '<div style="font-weight:600;margin-bottom:4px;white-space:pre-wrap">' + renderQText(f.question) + '</div>' +
          '<div style="font-size:13px;color:#666;margin-bottom:6px">' + escapeHtml(f.answer.substring(0, 100)) + '...</div>' +
          '<button onclick="C4.deleteFavorite(' + f.id + ')" style="padding:4px 10px;border:1px solid #e5484d;color:#e5484d;border-radius:4px;background:#fff;cursor:pointer;font-size:12px">取消收藏</button>';
        el.appendChild(div);
      });
    }).catch(function (e) {
      document.getElementById('favList').innerHTML = '<p style="color:#e5484d">加载失败: ' + escapeHtml(e.message) + '</p>';
    });
  }

  function deleteFavorite(id) {
    global.GK.api('/favorites/' + id, { method: 'DELETE' }).then(function () {
      showToast('已取消收藏', 'success');
      openFavorites();
    }).catch(function (e) {
      showToast('删除失败: ' + e.message, 'error');
    });
  }

  function bookmarkCurrent() {
    if (!requireLogin()) return;
    // 从全局状态获取当前问答内容
    var question = GK.store.lastQuestion || '';
    var answer = GK.store.lastAnswer || '';
    var refs = GK.store.lastRefs || [];
    var sessionId = GK.store.sessionId || '';
    var teacherId = GK.store.teacherId || 'T001';

    if (!question || !answer) {
      showToast('没有可收藏的内容', 'error');
      return;
    }

    global.GK.api('/favorites', {
      method: 'POST',
      body: { question, answer, teacher_id: teacherId, refs, session_id: sessionId }
    }).then(function () {
      showToast('已收藏', 'success');
    }).catch(function (e) {
      showToast('收藏失败: ' + e.message, 'error');
    });
  }

  // -----------------------------------------------------------------
  // 3. 错题本功能
  // -----------------------------------------------------------------
  function openMistakes() {
    if (!requireLogin()) return;

    var container = document.getElementById('c4Panel');
    if (!container) {
      container = document.createElement('div');
      container.id = 'c4Panel';
      container.style.cssText = 'position:fixed;right:0;top:60px;bottom:0;width:400px;background:#fff;box-shadow:-2px 0 12px rgba(0,0,0,.1);z-index:100;overflow-y:auto;padding:20px;';
      document.body.appendChild(container);
    }

    container.innerHTML = '<h3>❌ 错题本</h3>' +
      '<div style="margin:8px 0"><button onclick="C4.exportMistakes()" style="padding:5px 12px;border:1px solid #4a6cf7;color:#4a6cf7;border-radius:6px;background:#fff;cursor:pointer;font-size:12px">⬇ 导出 CSV</button></div>' +
      '<div id="mistakeList" style="margin-top:8px"></div>' +
      '<button onclick="document.getElementById(\'c4Panel\').remove()" style="margin-top:12px;padding:8px 16px;border:none;border-radius:6px;background:#f0f2f7;cursor:pointer">关闭</button>';

    global.GK.api('/mistakes').then(function (d) {
      var list = d.mistakes || [];
      var el = document.getElementById('mistakeList');
      if (!list.length) {
        el.innerHTML = '<p style="color:#999">暂无错题</p>';
        return;
      }
      list.forEach(function (m) {
        var div = document.createElement('div');
        div.style.cssText = 'padding:12px;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:8px;background:#fff8f0;';
        div.innerHTML = '<div style="font-weight:600;margin-bottom:4px;white-space:pre-wrap">问：' + renderQText(m.question) + '</div>' +
          '<div style="font-size:13px;color:#666;margin-bottom:4px">我的答案：' + escapeHtml(m.user_answer || '无') + '</div>' +
          '<div style="font-size:13px;color:#30a46c;margin-bottom:6px">正确讲解：' + escapeHtml(m.correct_note) + '</div>' +
          (m.question_id
            ? '<button onclick="C4.openPractice(' + m.question_id + ')" style="padding:4px 10px;margin-right:6px;border:1px solid #4a6cf7;color:#4a6cf7;border-radius:4px;background:#fff;cursor:pointer;font-size:12px">🔁 重练</button>'
            : '') +
          '<button onclick="C4.deleteMistake(' + m.id + ')" style="padding:4px 10px;border:1px solid #e5484d;color:#e5484d;border-radius:4px;background:#fff;cursor:pointer;font-size:12px">已掌握</button>';
        el.appendChild(div);
      });
    }).catch(function (e) {
      document.getElementById('mistakeList').innerHTML = '<p style="color:#e5484d">加载失败: ' + escapeHtml(e.message) + '</p>';
    });
  }

  function deleteMistake(id) {
    global.GK.api('/mistakes/' + id, { method: 'DELETE' }).then(function () {
      showToast('已移除错题', 'success');
      openMistakes();
    }).catch(function (e) {
      showToast('删除失败: ' + e.message, 'error');
    });
  }

  function recordMistake(question, userAnswer, correctNote, refs, sessionId, questionId) {
    if (!requireLogin()) return;
    var teacherId = GK.store.teacherId || 'T001';

    global.GK.api('/mistakes', {
      method: 'POST',
      body: { question, user_answer: userAnswer, correct_note: correctNote, teacher_id: teacherId, refs, session_id: sessionId, question_id: questionId || null }
    }).then(function () {
      showToast('已记录到错题本', 'success');
    }).catch(function (e) {
      showToast('记录失败: ' + e.message, 'error');
    });
  }

  // -----------------------------------------------------------------
  // 4. 练习功能（题库客观题/判断题/简答题 + #36 分类专项/只刷错题/AI 批改）
  // -----------------------------------------------------------------
  var practiceState = { category: '', mode: '' };

  // #36 AI 批改报告渲染（四维进度条 + 总评 + 建议 + 参考范文）
  function renderGradeReport(box, g) {
    if (!box) return;
    var r = g.report || {};
    var dims = r.scores || {};
    var dimHtml = ['立意', '结构', '论证', '语言'].map(function (k) {
      var v = Number(dims[k]) || 0;
      var color = v >= 80 ? '#30a46c' : (v >= 60 ? '#f0a020' : '#e5484d');
      return '<div style="margin-bottom:6px">' +
        '<div style="display:flex;justify-content:space-between;font-size:12px"><span>' + k + '</span><span style="color:' + color + ';font-weight:600">' + v + '</span></div>' +
        '<div style="height:6px;background:#eef0f4;border-radius:3px;overflow:hidden"><div style="height:100%;width:' + v + '%;background:' + color + '"></div></div></div>';
    }).join('');
    box.innerHTML = '<div style="padding:10px;background:#f5f0ff;border-radius:8px;margin-top:8px">' +
      '<div style="font-weight:600;margin-bottom:6px">🤖 AI 批改 · 总分 <span style="color:#7c3aed;font-size:18px">' + (r.total != null ? r.total : '—') + '</span>/100' + (g.cached ? '（已有报告）' : '') + '</div>' +
      dimHtml +
      (r.comment ? '<div style="font-size:13px;color:#444;margin-top:6px">📝 ' + escapeHtml(r.comment) + '</div>' : '') +
      (r.suggestions && r.suggestions.length ? '<div style="font-size:12px;color:#666;margin-top:6px">💡 ' + r.suggestions.map(escapeHtml).join('；') + '</div>' : '') +
      (r.model_answer ? '<div style="font-size:12px;color:#30a46c;margin-top:6px;background:#f0faf4;padding:6px;border-radius:6px">📄 参考范文：' + escapeHtml(r.model_answer) + '</div>' : '') +
      '</div>';
  }

  // #36 批改失败重试
  function regrade(logId) {
    var box = document.getElementById('gradeBox');
    if (!box) return;
    box.innerHTML = '🤖 AI 批改中…（约需 10~30 秒）';
    global.GK.api('/practice/grade', { method: 'POST', body: { log_id: logId }, timeout: 120000 })
      .then(function (g) { renderGradeReport(box, g); })
      .catch(function (e) {
        box.innerHTML = '<span style="color:#e5484d">批改失败：' + escapeHtml(e.message) + '</span> ' +
          '<button onclick="C4.regrade(' + logId + ')" style="padding:3px 10px;border:1px solid #7c3aed;color:#7c3aed;border-radius:4px;background:#fff;cursor:pointer;font-size:12px">重试</button>';
      });
  }

  // #36 学情报告 → 一键专项练习
  function practiceCategory(cat) {
    practiceState.category = cat || '';
    practiceState.mode = '';
    openPractice();
  }

  // -----------------------------------------------------------------
  // #16 错题强化重练：艾宾浩斯「今日巩固」+ 知识点归组
  // -----------------------------------------------------------------
  function openReview() {
    if (!requireLogin()) return;
    var teacherId = GK.store.teacherId || 'T001';

    var container = document.getElementById('c4Panel');
    if (!container) {
      container = document.createElement('div');
      container.id = 'c4Panel';
      container.style.cssText = 'position:fixed;right:0;top:60px;bottom:0;width:400px;background:#fff;box-shadow:-2px 0 12px rgba(0,0,0,.1);z-index:100;overflow-y:auto;padding:20px;';
      document.body.appendChild(container);
    }

    container.innerHTML = '<h3>📅 错题强化 · 今日巩固</h3>' +
      '<div style="font-size:12px;color:#888;margin-bottom:8px">艾宾浩斯记忆节奏：1 → 2 → 4 → 7 → 15 → 30 天，连续答对 6 次即掌握</div>' +
      '<div id="reviewStats" style="margin-bottom:10px"><p style="color:#999">加载中...</p></div>' +
      '<div id="reviewList"></div>' +
      '<div id="reviewGroups" style="margin-top:12px"></div>' +
      '<button onclick="C4.openPractice()" style="margin:12px 6px 0 0;padding:8px 16px;border:1px solid #d0d4de;border-radius:6px;background:#fff;cursor:pointer">✏️ 去练习</button>' +
      '<button onclick="document.getElementById(\'c4Panel\').remove()" style="margin-top:12px;padding:8px 16px;border:none;border-radius:6px;background:#f0f2f7;cursor:pointer">关闭</button>';

    // 复习概览
    global.GK.api('/review/stats').then(function (s) {
      var el = document.getElementById('reviewStats');
      if (!el) return;
      var nextTxt = s.next_review_at ? String(s.next_review_at).replace('T', ' ').slice(0, 16) : '—';
      el.innerHTML =
        '<div style="padding:10px;background:#fff5f5;border-radius:8px">' +
          '<div style="display:flex;gap:18px;flex-wrap:wrap">' +
            '<div><div style="font-size:12px;color:#888">待巩固</div><div style="font-size:22px;font-weight:700;color:#e5484d">' + s.due + '</div></div>' +
            '<div><div style="font-size:12px;color:#888">复习队列</div><div style="font-size:22px;font-weight:700">' + s.in_queue + '</div></div>' +
            '<div><div style="font-size:12px;color:#888">已掌握</div><div style="font-size:22px;font-weight:700;color:#30a46c">' + (s.mastered || 0) + '</div></div>' +
            '<div><div style="font-size:12px;color:#888">下次复习</div><div style="font-size:15px;font-weight:600">' + nextTxt + '</div></div>' +
          '</div>' +
        '</div>';
    }).catch(function (e) {
      var el = document.getElementById('reviewStats');
      if (el) el.innerHTML = '<p style="color:#e5484d">概览加载失败: ' + escapeHtml(e.message) + '</p>';
    });

    // 到期待巩固队列
    global.GK.api('/review/due?teacher_id=' + encodeURIComponent(teacherId)).then(function (d) {
      var el = document.getElementById('reviewList');
      if (!el) return;
      var due = d.due || [];
      if (!due.length) {
        el.innerHTML = '<div style="padding:10px;background:#f0faf4;border-radius:8px;color:#30a46c;font-size:13px">✓ 今日无到期错题，继续保持！</div>';
        return;
      }
      var html = '<div style="font-size:12px;color:#888;margin:10px 0 6px">待巩固 ' + due.length + ' 题（按最急优先）</div>';
      due.forEach(function (q) {
        var stageTag = q.review_stage != null
          ? '<span style="display:inline-block;padding:1px 6px;border-radius:8px;background:#ffeef0;color:#e5484d;font-size:11px">第 ' + q.review_stage + ' 轮</span>' : '';
        var catBadge = q.category ? '<span style="display:inline-block;padding:1px 6px;border-radius:8px;background:#eef4ff;color:#4a6cf7;font-size:11px">' + escapeHtml(q.category) + '</span>' : '';
        html += '<div style="padding:8px 10px;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:6px">' +
          '<div style="font-size:13px;white-space:pre-wrap;margin-bottom:4px">' + renderQText(q.question) + '</div>' +
          '<div style="margin-bottom:4px">' + stageTag + ' ' + catBadge + '</div>' +
          '<button onclick="C4.openPractice(' + q.id + ')" style="padding:4px 10px;border:1px solid #4a6cf7;color:#4a6cf7;border-radius:4px;background:#fff;cursor:pointer;font-size:12px">🔁 重做此题</button>' +
          '</div>';
      });
      el.innerHTML = html;
    }).catch(function (e) {
      var el = document.getElementById('reviewList');
      if (el) el.innerHTML = '<p style="color:#e5484d">加载失败: ' + escapeHtml(e.message) + '</p>';
    });

    // 知识点归组
    global.GK.api('/mistakes/groups?teacher_id=' + encodeURIComponent(teacherId)).then(function (d) {
      var el = document.getElementById('reviewGroups');
      if (!el) return;
      var groups = d.groups || [];
      if (!groups.length) {
        el.innerHTML = '';
        return;
      }
      var html = '<div style="font-size:12px;color:#888;margin:12px 0 6px">📚 错题知识点分布</div>';
      var top = groups.slice(0, 6);
      var max = top[0].mistake_count || 1;
      top.forEach(function (g) {
        var pct = Math.round((g.mistake_count / max) * 100);
        html += '<div style="margin-bottom:6px">' +
          '<div style="display:flex;justify-content:space-between;font-size:12px"><span>' + escapeHtml(g.knowledge_point) + '</span><span style="color:#666">' + g.mistake_count + ' 题</span></div>' +
          '<div style="height:6px;background:#eef0f4;border-radius:3px;overflow:hidden"><div style="height:100%;width:' + pct + '%;background:#f0a020"></div></div>' +
          '</div>';
      });
      el.innerHTML = html;
    }).catch(function () { /* 归组失败静默 */ });
  }

  function openPractice(questionId) {
    var teacherId = GK.store.teacherId || 'T001';

    var container = document.getElementById('c4Panel');
    if (!container) {
      container = document.createElement('div');
      container.id = 'c4Panel';
      container.style.cssText = 'position:fixed;right:0;top:60px;bottom:0;width:400px;background:#fff;box-shadow:-2px 0 12px rgba(0,0,0,.1);z-index:100;overflow-y:auto;padding:20px;';
      document.body.appendChild(container);
    }

    container.innerHTML = '<h3>✏️ 真题练习</h3>' +
      '<div style="margin:8px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:center">' +
        '<select id="practiceCat" style="padding:5px 8px;border:1px solid #d0d4de;border-radius:6px;font-size:13px;max-width:160px;background:#fff"><option value="">📚 全部分类</option></select>' +
        '<button id="wrongModeBtn" style="padding:5px 12px;border:1px solid #d0d4de;border-radius:6px;background:#fff;cursor:pointer;font-size:12px">❌ 只刷错题</button>' +
        '<button onclick="C4.openReview()" style="padding:5px 12px;border:1px solid #e5484d;color:#e5484d;border-radius:6px;background:#fff;cursor:pointer;font-size:12px">📅 今日巩固</button>' +
        '<button onclick="C4.openReport()" style="padding:5px 12px;border:1px solid #7c3aed;color:#7c3aed;border-radius:6px;background:#fff;cursor:pointer;font-size:12px">📊 学情报告</button>' +
      '</div>' +
      '<div id="practiceArea" style="margin-top:8px">' +
      '<p style="color:#999">加载中...</p>' +
      '</div>';

    // 分类下拉（#36：题库分类清单 + 切换即抽题）
    global.GK.api('/practice/categories?teacher_id=' + encodeURIComponent(teacherId)).then(function (d) {
      var sel = document.getElementById('practiceCat');
      if (!sel) return;
      (d.categories || []).forEach(function (c) {
        var op = document.createElement('option');
        op.value = c.category;
        op.textContent = c.category + '（' + c.count + '）';
        sel.appendChild(op);
      });
      sel.value = practiceState.category;
      sel.onchange = function () {
        practiceState.category = sel.value;
        loadQuestion();
      };
    }).catch(function () { /* 分类加载失败不阻塞抽题 */ });

    // 只刷错题开关
    var wbtn = document.getElementById('wrongModeBtn');
    function syncWrongBtn() {
      var on = practiceState.mode === 'wrong';
      wbtn.textContent = on ? '✅ 错题模式中（退出）' : '❌ 只刷错题';
      wbtn.style.borderColor = on ? '#e5484d' : '#d0d4de';
      wbtn.style.color = on ? '#e5484d' : '#333';
    }
    syncWrongBtn();
    wbtn.onclick = function () {
      practiceState.mode = practiceState.mode === 'wrong' ? '' : 'wrong';
      syncWrongBtn();
      loadQuestion();
    };

    function loadQuestion(qid) {
      var url = '/practice/next?teacher_id=' + encodeURIComponent(teacherId);
      if (practiceState.category) url += '&category=' + encodeURIComponent(practiceState.category);
      if (practiceState.mode) url += '&mode=' + practiceState.mode;
      if (qid) url += '&question_id=' + qid;
      global.GK.api(url).then(function (d) {
        renderQuestion(d);
      }).catch(function (e) {
        document.getElementById('practiceArea').innerHTML = '<p style="color:#e5484d">加载失败: ' + escapeHtml(e.message) + '</p>';
      });
    }

    function renderQuestion(d) {
      var area = document.getElementById('practiceArea');
      // 题型标签 + #36 分类徽标
      var typeLabel = { choice: '选择题', judge: '判断题', essay: '简答题' }[d.type] || '练习题';
      var catBadge = d.category
        ? '<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:#eef4ff;color:#4a6cf7;font-size:11px;margin-left:6px;vertical-align:1px">' + escapeHtml(d.category) + '</span>' : '';
      if (d.mode_fallback === 'no_wrong') {
        showToast('暂无未掌握错题，已切换普通抽题', '');
      }

      // 选择题渲染选项（#35 R2：选项可能含图片标记（图形推理），走 renderQText 渲染为图）
      var optionsHtml = '';
      if (d.type === 'choice' && d.options && d.options.length) {
        optionsHtml = '<div style="margin-top:8px">';
        d.options.forEach(function (opt) {
          optionsHtml += '<label style="display:block;padding:6px 10px;margin-bottom:6px;border:1px solid #e5e7eb;border-radius:6px;cursor:pointer;font-size:14px">' +
            '<input type="radio" name="practiceOpt" value="' + escapeHtml(String(opt).charAt(0)) + '" style="margin-right:6px;vertical-align:top">' +
            '<span style="display:inline-block;max-width:88%">' + renderQText(String(opt)) + '</span></label>';
        });
        optionsHtml += '</div>';
      }

      var answerInputHtml = '';
      if (d.type === 'choice') {
        // 选择题：用选项单选，不显示文本框
        answerInputHtml = '';
      } else if (d.type === 'judge') {
        answerInputHtml = '<div style="margin-top:8px">' +
          '<label style="margin-right:16px"><input type="radio" name="practiceOpt" value="对" style="margin-right:4px">对</label>' +
          '<label><input type="radio" name="practiceOpt" value="错" style="margin-right:4px">错</label></div>';
      } else {
        answerInputHtml = '<textarea id="practiceAnswer" style="width:100%;height:80px;border:1px solid #d0d4de;border-radius:6px;padding:8px;font-size:14px;resize:none;margin-top:8px"></textarea>';
      }

      var noAnsTip = (d.has_answer === false)
        ? '<div style="font-size:12px;color:#b06a00;background:#fff8e6;border:1px solid #ffe1a8;border-radius:6px;padding:4px 8px;margin-bottom:8px">本题暂无答案与解析，仅供练习，不计入正确率</div>'
        : '';

      area.innerHTML = '<div style="padding:12px;background:#f0f4ff;border-radius:8px;margin-bottom:12px">' +
        '<div style="font-size:12px;color:#888;margin-bottom:6px">【' + typeLabel + '】' + catBadge + '</div>' +
        noAnsTip +
        '<div style="font-weight:600;margin-bottom:8px;white-space:pre-wrap">' + renderQText(d.question) + '</div>' +
        optionsHtml +
        answerInputHtml +
        (d.hint ? '<div style="font-size:12px;color:#999;margin-top:6px">' + escapeHtml(d.hint) + '</div>' : '') +
        '<div style="margin-top:8px;display:flex;gap:8px">' +
        '<button id="submitPractice" style="flex:1;padding:8px;border:none;border-radius:6px;background:#4a6cf7;color:#fff;cursor:pointer">提交</button>' +
        '<button onclick="C4.openPractice()" style="padding:8px 16px;border:1px solid #d0d4de;border-radius:6px;background:#fff;cursor:pointer">下一题</button>' +
        '</div>' +
        '<div id="practiceResult" style="margin-top:8px"></div>' +
        '</div>' +
        '<button onclick="document.getElementById(\'c4Panel\').remove()" style="margin-top:12px;padding:8px 16px;border:none;border-radius:6px;background:#f0f2f7;cursor:pointer">关闭</button>';

      document.getElementById('submitPractice').onclick = function () {
        var answer = '';
        if (d.type === 'choice' || d.type === 'judge') {
          var checked = area.querySelector('input[name="practiceOpt"]:checked');
          if (!checked) { alert('请先选择答案'); return; }
          answer = checked.value;
        } else {
          answer = document.getElementById('practiceAnswer').value.trim();
          if (!answer) { alert('请输入答案'); return; }
        }
        var btn = document.getElementById('submitPractice');
        btn.disabled = true;
        btn.textContent = '提交中...';

        var body = { question: d.question, answer: answer, teacher_id: teacherId };
        if (d.id != null) body.question_id = d.id;

        global.GK.api('/practice/submit', { method: 'POST', body: body }).then(function (log) {
          var result = document.getElementById('practiceResult');
          var wrong = false;
          if (log.score !== null && log.score !== undefined && log.score >= 0) {
            var ok = log.score >= 0.7;
            wrong = !ok;
            result.innerHTML = '<div style="padding:8px;border-radius:6px;background:' + (ok ? '#e6f9f0' : '#fff8f0') + '">' +
              '<strong>' + (ok ? '✓ 回答正确' : '✗ 回答错误') + '</strong>' +
              (log.feedback ? '<br><span style="font-size:13px;color:#666">' + escapeHtml(log.feedback) + '</span>' : '') +
              '</div>';
          } else if (d.has_answer === false) {
            // 无答案题（仅题干+选项的练习卷）：不判分、不记错题本、不触发 AI 批改
            wrong = false;
            result.innerHTML = '<div style="padding:8px;border-radius:6px;background:#fff8e6">' +
              '<strong>本题暂无答案</strong>' +
              '<br><span style="font-size:13px;color:#666">答案与解析尚未采集到，本题仅作练习，不计入正确率。</span>' +
              '</div>';
            btn.disabled = false;
            btn.textContent = '提交';
            return;
          } else {
            // #36 简答题：提交后自动 AI 批改（submit 快速返回，批改独立渲染/可重试）
            wrong = false;
            result.innerHTML = '<div style="padding:8px;border-radius:6px;background:#f5f0ff">' +
              '<strong>🤖 AI 批改中…（约需 10~30 秒）</strong></div>' +
              '<div id="gradeBox"></div>';
            regrade(log.id);
            return;
          }
          if (wrong && global.C4) {
            var mb = document.createElement('button');
            mb.textContent = '❌ 记入错题本';
            mb.style.cssText = 'margin-top:8px;padding:5px 12px;border:1px solid #e5484d;color:#e5484d;border-radius:6px;background:#fff;cursor:pointer;font-size:12px';
            mb.onclick = function () {
              var note = (log.feedback || d.hint || '');
              global.C4.recordMistake(d.question, answer, note, [], global.GK.store.sessionId || '', d.id);
            };
            result.appendChild(mb);
          }
          btn.disabled = false;
          btn.textContent = '提交';
        }).catch(function (e) {
          document.getElementById('practiceResult').innerHTML = '<p style="color:#e5484d">提交失败: ' + escapeHtml(e.message) + '</p>';
          btn.disabled = false;
          btn.textContent = '提交';
        });
      };
    }

    loadQuestion(questionId);
  }

  // -----------------------------------------------------------------
  // 5. 学情报告（#36：总览/分类正确率/薄弱点专项/近7天趋势/主观题概况）
  // -----------------------------------------------------------------
  function openReport() {
    if (!requireLogin()) return;
    var teacherId = GK.store.teacherId || 'T001';

    var container = document.getElementById('c4Panel');
    if (!container) {
      container = document.createElement('div');
      container.id = 'c4Panel';
      container.style.cssText = 'position:fixed;right:0;top:60px;bottom:0;width:400px;background:#fff;box-shadow:-2px 0 12px rgba(0,0,0,.1);z-index:100;overflow-y:auto;padding:20px;';
      document.body.appendChild(container);
    }

    container.innerHTML = '<h3>📊 学情报告</h3>' +
      '<div id="reportArea"><p style="color:#999">加载中...</p></div>' +
      '<button onclick="C4.openPractice()" style="margin-top:12px;padding:8px 16px;border:1px solid #d0d4de;border-radius:6px;background:#fff;cursor:pointer">✏️ 去练习</button> ' +
      '<button onclick="document.getElementById(\'c4Panel\').remove()" style="margin-top:12px;padding:8px 16px;border:none;border-radius:6px;background:#f0f2f7;cursor:pointer">关闭</button>';

    global.GK.api('/practice/report?teacher_id=' + encodeURIComponent(teacherId)).then(function (r) {
      var area = document.getElementById('reportArea');
      var acc = (r.accuracy == null) ? '—' : Math.round(r.accuracy * 100) + '%';

      var cats = (r.by_category || []).map(function (c) {
        var pct = Math.round((c.accuracy || 0) * 100);
        var color = pct >= 80 ? '#30a46c' : (pct >= 60 ? '#f0a020' : '#e5484d');
        return '<div style="margin-bottom:8px">' +
          '<div style="display:flex;justify-content:space-between;font-size:13px"><span>' + escapeHtml(c.category) + '（' + c.total + ' 题）</span>' +
          '<span style="color:' + color + ';font-weight:600">' + pct + '%（对 ' + c.correct + '）</span></div>' +
          '<div style="height:8px;background:#eef0f4;border-radius:4px;overflow:hidden"><div style="height:100%;width:' + pct + '%;background:' + color + '"></div></div>' +
          '</div>';
      }).join('');

      var weakHtml = (r.weak && r.weak.length)
        ? '<div style="font-size:13px;color:#e5484d;margin:6px 0">⚠ 薄弱分类：' + r.weak.map(escapeHtml).join('、') + '</div>' +
          r.weak.map(function (w) {
            return '<button onclick="C4.practiceCategory(\'' + escapeHtml(w) + '\')" style="padding:5px 12px;margin:4px 6px 0 0;border:1px solid #e5484d;color:#e5484d;border-radius:6px;background:#fff;cursor:pointer;font-size:12px">🎯 ' + escapeHtml(w) + ' 专项练习</button>';
          }).join('')
        : '<div style="font-size:13px;color:#30a46c;margin:6px 0">✓ 暂无明显薄弱点，继续保持！</div>';

      var days = (r.recent_days || []).map(function (d) {
        var pct = d.n ? Math.round((d.ok / d.n) * 100) : 0;
        var color = pct >= 60 ? '#30a46c' : '#f0a020';
        return '<span style="display:inline-block;margin:2px 6px 0 0;padding:3px 8px;border-radius:8px;background:#f0f4ff;font-size:12px">' +
          escapeHtml(String(d.d).slice(5)) + ' · ' + d.ok + '/' + d.n + ' <span style="color:' + color + '">' + pct + '%</span></span>';
      }).join('');

      var essayHtml = r.essay && r.essay.pending
        ? '<div style="font-size:12px;color:#7c3aed;margin-top:8px">✍ 主观题待批 ' + r.essay.pending + ' 道（提交练习后自动 AI 批改）</div>'
        : (r.essay && r.essay.avg_score != null
            ? '<div style="font-size:12px;color:#7c3aed;margin-top:8px">✍ 主观题 AI 批改平均分：' + Math.round(r.essay.avg_score * 100) + '/100</div>'
            : '');

      area.innerHTML =
        '<div style="padding:12px;background:#f8faff;border-radius:8px;margin-bottom:10px">' +
          '<div style="display:flex;gap:18px;flex-wrap:wrap;margin-bottom:8px">' +
            '<div><div style="font-size:12px;color:#888">已批改题数</div><div style="font-size:22px;font-weight:700;color:#4a6cf7">' + r.graded + '</div></div>' +
            '<div><div style="font-size:12px;color:#888">客观正确率</div><div style="font-size:22px;font-weight:700;color:#30a46c">' + acc + '</div></div>' +
            '<div><div style="font-size:12px;color:#888">答对题数</div><div style="font-size:22px;font-weight:700">' + r.correct + '</div></div>' +
          '</div>' + essayHtml +
        '</div>' +
        (cats ? '<div style="font-size:12px;color:#888;margin:10px 0 6px">分类正确率</div>' + cats : '<p style="color:#999">还没有练习记录，先去刷几道题吧</p>') +
        weakHtml +
        (days ? '<div style="font-size:12px;color:#888;margin:12px 0 4px">近 7 天</div><div>' + days + '</div>' : '');
    }).catch(function (e) {
      document.getElementById('reportArea').innerHTML = '<p style="color:#e5484d">报告加载失败: ' + escapeHtml(e.message) + '</p>';
    });
  }

  // -----------------------------------------------------------------
  // 6. 挂载对外 API
  // -----------------------------------------------------------------
  global.C4 = {
    openFavorites: openFavorites,
    openMistakes: openMistakes,
    openPractice: openPractice,
    openReport: openReport,
    openReview: openReview,
    bookmarkCurrent: bookmarkCurrent,
    recordMistake: recordMistake,
    deleteFavorite: deleteFavorite,
    deleteMistake: deleteMistake,
    exportFavorites: function () { exportCsv('/favorites/export', '我的收藏.csv'); },
    exportMistakes: function () { exportCsv('/mistakes/export', '错题本.csv'); },
    showPracticeStats: showPracticeStats,
    regrade: regrade,
    practiceCategory: practiceCategory
  };
  global.GK.c4Ready = true;

})(typeof window !== 'undefined' ? window : globalThis);

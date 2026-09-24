/* =====================================================================
 * C6 · 智能组卷模块（G1，网站一前端）
 * ---------------------------------------------------------------------
 * 职责：按分类/题型/难度/知识点自助组卷 → 在线作答 → 客观题自动评分
 * 复用 A4(GK.api) 传输层与 #c4Panel 右侧抽屉；登录用户专用。
 * 对外能力：
 *   C6.openSmartExam() —— 打开智能组卷面板
 * ===================================================================== */
(function (global) {
  'use strict';

  if (!global.GK) global.GK = {};

  var state = { view: 'list', paperId: 0, paper: null, answers: {} };

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  var QIMG_URL_RE = /^\/api\/qimg\/[A-Za-z0-9_-]+\/[a-f0-9]{12}\.(png|jpe?g|gif|webp)$/;
  var QIMG_MARK_RE = /!\[([^\]]*)\]\(([^)\s]+)\)/g;
  function renderQText(text) {
    return escapeHtml(text).replace(QIMG_MARK_RE, function (m, alt, url) {
      if (!QIMG_URL_RE.test(url)) return '';
      return '<img src="' + url + '" alt="' + alt + '" loading="lazy" ' +
        'style="max-width:100%;max-height:220px;margin:4px 0;display:block;cursor:zoom-in" ' +
        'onclick="window.open(\'' + url + '\', \'_blank\')" title="点击查看大图">';
    });
  }

  function currentUserId() {
    return (global.GK.store && global.GK.store.userId) || 'anonymous';
  }

  function requireLogin() {
    if (currentUserId() === 'anonymous') {
      alert('请先登录后使用智能组卷');
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

  function getPanel() {
    var container = document.getElementById('c4Panel');
    if (!container) {
      container = document.createElement('div');
      container.id = 'c4Panel';
      container.style.cssText = 'position:fixed;right:0;top:60px;bottom:0;width:440px;background:#fff;box-shadow:-2px 0 12px rgba(0,0,0,.1);z-index:100;overflow-y:auto;padding:20px;';
      document.body.appendChild(container);
    }
    return container;
  }

  function closeBtnHtml() {
    return '<div style="margin-top:14px;text-align:center">' +
      '<button onclick="document.getElementById(\'c4Panel\').remove()" style="padding:8px 20px;border:none;border-radius:6px;background:#f0f2f7;cursor:pointer">关闭</button></div>';
  }

  var CATEGORIES = ['言语理解', '判断推理', '数量关系', '资料分析', '常识判断', '申论', '面试', '综合'];
  var QTYPES = [['', '不限题型'], ['choice', '选择题'], ['judge', '判断题'], ['essay', '简答题']];

  // -----------------------------------------------------------------
  // 列表视图
  // -----------------------------------------------------------------
  function openSmartExam() {
    if (!requireLogin()) return;
    state.view = 'list';
    state.paper = null;
    getPanel();
    renderList();
  }

  function renderList() {
    var container = getPanel();
    container.innerHTML =
      '<h3 style="margin:0 0 4px">🧩 智能组卷</h3>' +
      '<div style="font-size:12px;color:#888;margin-bottom:10px">按分类/题型/难度/知识点自助组卷，提交后客观题自动评分</div>' +
      '<button id="seNewBtn" style="width:100%;padding:9px;border:1px dashed #7c3aed;color:#7c3aed;border-radius:8px;background:#faf8ff;cursor:pointer;font-size:14px">＋ 生成新试卷</button>' +
      '<div id="seNewBox" style="display:none;margin-top:10px;border:1px solid #e5e7eb;border-radius:8px;padding:10px"></div>' +
      '<div id="seListArea" style="margin-top:12px"><p style="color:#999">加载中...</p></div>' +
      closeBtnHtml();

    document.getElementById('seNewBtn').onclick = renderNewBox;
    loadList();
  }

  function renderNewBox() {
    var box = document.getElementById('seNewBox');
    var show = box.style.display === 'none';
    box.style.display = show ? 'block' : 'none';
    if (!show) return;

    var teacherId = (global.GK.store && global.GK.store.teacherId) || 'T001';
    var catOpts = CATEGORIES.map(function (c) {
      return '<option value="' + c + '">' + c + '</option>';
    }).join('');
    var qtypeOpts = QTYPES.map(function (t) {
      return '<option value="' + t[0] + '">' + t[1] + '</option>';
    }).join('');

    box.innerHTML =
      '<div style="font-size:13px;color:#555;margin-bottom:4px">组卷条件（老师随当前选中 Tab）</div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px">' +
        '<select id="seCat" style="flex:1;min-width:120px;padding:6px;border:1px solid #d0d4de;border-radius:6px;background:#fff;font-size:13px"><option value="">📚 全部分类</option>' + catOpts + '</select>' +
        '<select id="seType" style="flex:1;min-width:110px;padding:6px;border:1px solid #d0d4de;border-radius:6px;background:#fff;font-size:13px">' + qtypeOpts + '</select>' +
      '</div>' +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px">' +
        '<select id="seDiff" style="flex:1;min-width:100px;padding:6px;border:1px solid #d0d4de;border-radius:6px;background:#fff;font-size:13px">' +
          '<option value="">不限难度</option><option value="1">难度 1</option><option value="2">难度 2</option><option value="3">难度 3</option><option value="4">难度 4</option><option value="5">难度 5</option>' +
        '</select>' +
        '<select id="seCount" style="flex:1;min-width:90px;padding:6px;border:1px solid #d0d4de;border-radius:6px;background:#fff;font-size:13px">' +
          '<option value="5">5 题</option><option value="10" selected>10 题</option><option value="15">15 题</option><option value="20">20 题</option><option value="30">30 题</option>' +
        '</select>' +
      '</div>' +
      '<input id="seKp" type="text" placeholder="知识点（可选，如：图形推理）" style="width:100%;box-sizing:border-box;padding:6px;border:1px solid #d0d4de;border-radius:6px;font-size:13px;margin-bottom:8px">' +
      '<button id="seGenBtn" style="width:100%;padding:8px;border:none;border-radius:6px;background:#7c3aed;color:#fff;cursor:pointer;font-size:14px">🎯 生成试卷</button>';

    document.getElementById('seGenBtn').onclick = function () {
      var btn = this;
      var body = {
        teacher_id: teacherId,
        category: document.getElementById('seCat').value,
        qtype: document.getElementById('seType').value,
        difficulty: document.getElementById('seDiff').value ? parseInt(document.getElementById('seDiff').value, 10) : null,
        knowledge_point: document.getElementById('seKp').value.trim(),
        count: parseInt(document.getElementById('seCount').value, 10)
      };
      btn.disabled = true;
      btn.textContent = '组卷中...';
      global.GK.api('/smartexam/generate', { method: 'POST', body: body, timeout: 30000 })
        .then(function (p) {
          showToast('已生成 ' + p.total + ' 题试卷', 'success');
          btn.disabled = false;
          btn.textContent = '🎯 生成试卷';
          openPaper(p.id, p);
        })
        .catch(function (e) {
          btn.disabled = false;
          btn.textContent = '🎯 生成试卷';
          showToast('组卷失败: ' + (e && e.message ? e.message : e), 'error');
        });
    };
  }

  function loadList() {
    global.GK.api('/smartexam/papers').then(function (d) {
      var papers = d.papers || [];
      var area = document.getElementById('seListArea');
      if (!area) return;
      if (!papers.length) {
        area.innerHTML = '<div style="padding:26px 10px;text-align:center;color:#aaa;font-size:13px">还没有组卷记录<br>点击上方「生成新试卷」开始</div>';
        return;
      }
      var html = papers.map(function (p) {
        var statusHtml = p.status === 'graded'
          ? '<span style="display:inline-block;padding:2px 10px;border-radius:10px;color:#fff;font-size:11px;background:#30a46c">已评分</span>'
          : '<span style="display:inline-block;padding:2px 10px;border-radius:10px;color:#fff;font-size:11px;background:#f59e0b">待作答</span>';
        var scoreHtml = p.score != null
          ? ' · 得分 ' + Math.round(p.score * 100) + '%' : '';
        var cfg = p.config || {};
        var opts = [];
        if (cfg.category) opts.push(cfg.category);
        if (cfg.qtype) opts.push(QTYPES.filter(function (t) { return t[0] === cfg.qtype; }).map(function (t) { return t[1]; })[0] || cfg.qtype);
        return '<div class="se-paper-row" data-pid="' + p.id + '" style="border:1px solid #e5e7eb;border-radius:8px;padding:10px;margin-bottom:8px;cursor:pointer;background:#fff">' +
          '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px">' +
            '<div style="font-weight:600;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escapeHtml(p.title || '未命名') + '</div>' +
            statusHtml +
          '</div>' +
          '<div style="font-size:12px;color:#888;margin-top:4px">' + p.total + ' 题' + scoreHtml +
            (opts.length ? ' · ' + opts.join(' / ') : '') + '</div>' +
          '<div style="font-size:12px;color:#999;margin-top:2px">' + escapeHtml((p.updated_at || '').slice(5, 16).replace('T', ' ')) + '</div>' +
        '</div>';
      }).join('');
      area.innerHTML = html;
      area.querySelectorAll('.se-paper-row').forEach(function (row) {
        row.onclick = function () { openPaper(parseInt(row.getAttribute('data-pid'), 10)); };
      });
    }).catch(function (e) {
      var area = document.getElementById('seListArea');
      if (area) area.innerHTML = '<p style="color:#e5484d">加载失败: ' + escapeHtml(e.message) + '</p>';
    });
  }

  // -----------------------------------------------------------------
  // 作答 / 结果视图
  // -----------------------------------------------------------------
  function openPaper(paperId, paper) {
    state.paperId = paperId;
    state.paper = paper || null;
    state.answers = {};
    state.view = 'paper';
    getPanel();
    if (paper && paper.status !== 'graded') {
      renderAnswer();
    } else {
      fetchPaper();
    }
  }

  function fetchPaper() {
    global.GK.api('/smartexam/papers/' + state.paperId).then(function (p) {
      state.paper = p;
      if (p.status === 'graded') {
        renderResult();
      } else {
        renderAnswer();
      }
    }).catch(function (e) {
      getPanel().innerHTML =
        '<h3>🧩 智能组卷</h3><p style="color:#e5484d">加载失败: ' + escapeHtml(e.message) + '</p>' +
        '<button onclick="C6.openSmartExam()" style="padding:8px 16px;border:1px solid #d0d4de;border-radius:6px;background:#fff;cursor:pointer">返回列表</button>' +
        closeBtnHtml();
    });
  }

  function renderAnswer() {
    var p = state.paper;
    var container = getPanel();
    var qs = p.questions || [];
    var typeLabel = { choice: '选择', judge: '判断', essay: '简答' };

    var body = qs.map(function (q, i) {
      var qid = String(q.id);
      var inputHtml = '';
      if (q.qtype === 'choice' && q.options && q.options.length) {
        inputHtml = '<div style="margin-top:6px">' + q.options.map(function (o) {
          var letter = String(o).trim().charAt(0);
          return '<label style="display:block;padding:5px 10px;margin-bottom:5px;border:1px solid #e5e7eb;border-radius:6px;cursor:pointer;font-size:13px">' +
            '<input type="radio" name="se_opt_' + qid + '" value="' + escapeHtml(letter) + '" style="margin-right:6px">' +
            '<span style="display:inline-block;max-width:86%">' + renderQText(String(o)) + '</span></label>';
        }).join('') + '</div>';
      } else if (q.qtype === 'judge') {
        inputHtml = '<div style="margin-top:6px">' +
          '<label style="margin-right:16px"><input type="radio" name="se_opt_' + qid + '" value="对" style="margin-right:4px">对</label>' +
          '<label><input type="radio" name="se_opt_' + qid + '" value="错" style="margin-right:4px">错</label></div>';
      } else {
        inputHtml = '<textarea data-essay="' + qid + '" placeholder="请输入作答…" style="width:100%;box-sizing:border-box;height:70px;border:1px solid #d0d4de;border-radius:6px;padding:8px;font-size:13px;resize:vertical;margin-top:6px"></textarea>';
      }
      return '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px;margin-bottom:10px;background:#fff">' +
        '<div style="font-size:12px;color:#888;margin-bottom:4px">第 ' + (i + 1) + ' 题 · ' + (typeLabel[q.qtype] || q.qtype) +
          (q.category ? ' · <span style="color:#4a6cf7">' + escapeHtml(q.category) + '</span>' : '') + '</div>' +
        '<div style="font-weight:600;font-size:14px;white-space:pre-wrap;line-height:1.6">' + renderQText(q.question) + '</div>' +
        inputHtml +
      '</div>';
    }).join('');

    container.innerHTML =
      '<h3 style="margin:0 0 4px">🧩 ' + escapeHtml(p.title || '智能组卷') + '</h3>' +
      '<div style="font-size:12px;color:#888;margin-bottom:8px">共 ' + qs.length + ' 题 · 提交后客观题自动评分</div>' +
      body +
      '<div style="display:flex;gap:8px;margin-top:4px">' +
        '<button id="seSubmitBtn" style="flex:1;padding:9px;border:none;border-radius:6px;background:#7c3aed;color:#fff;cursor:pointer;font-size:14px">提交试卷</button>' +
        '<button onclick="C6.openSmartExam()" style="padding:9px 14px;border:1px solid #d0d4de;border-radius:6px;background:#fff;cursor:pointer">返回</button>' +
      '</div>' +
      closeBtnHtml();

    document.getElementById('seSubmitBtn').onclick = function () {
      var answers = {};
      var missing = 0;
      qs.forEach(function (q) {
        var qid = String(q.id);
        if (q.qtype === 'choice' || q.qtype === 'judge') {
          var checked = container.querySelector('input[name="se_opt_' + qid + '"]:checked');
          if (checked) answers[qid] = checked.value;
          else missing++;
        } else {
          var ta = container.querySelector('textarea[data-essay="' + qid + '"]');
          answers[qid] = ta ? ta.value.trim() : '';
        }
      });
      var btn = this;
      btn.disabled = true;
      btn.textContent = '评分中...';
      global.GK.api('/smartexam/papers/' + state.paperId + '/submit', {
        method: 'POST', body: { answers: answers }, timeout: 30000
      }).then(function (r) {
        showToast('评分完成', 'success');
        fetchPaper();
      }).catch(function (e) {
        btn.disabled = false;
        btn.textContent = '提交试卷';
        showToast('提交失败: ' + (e && e.message ? e.message : e), 'error');
      });
    };
  }

  function renderResult() {
    var p = state.paper;
    var container = getPanel();
    var qs = p.questions || [];
    var answers = p.answers || {};
    var typeLabel = { choice: '选择', judge: '判断', essay: '简答' };

    var header =
      '<h3 style="margin:0 0 4px">🧩 ' + escapeHtml(p.title || '智能组卷') + '</h3>' +
      '<div style="margin:6px 0 10px;padding:10px;background:#f8faff;border-radius:8px">' +
        '<div style="display:flex;gap:16px;flex-wrap:wrap">' +
          '<div><div style="font-size:12px;color:#888">总分</div><div style="font-size:22px;font-weight:700;color:#7c3aed">' + (p.score != null ? Math.round(p.score * 100) + '%' : '—') + '</div></div>' +
          '<div><div style="font-size:12px;color:#888">答对</div><div style="font-size:22px;font-weight:700;color:#30a46c">' + p.correct + '</div></div>' +
          '<div><div style="font-size:12px;color:#888">客观题</div><div style="font-size:22px;font-weight:700">' + (qs.length - (p.essay_pending || 0)) + '</div></div>' +
        '</div>' +
      '</div>';

    var body = qs.map(function (q, i) {
      var qid = String(q.id);
      var ua = answers[qid] || '';
      var correct = q.answer;
      var resultHtml = '';
      if (q.qtype === 'choice' || q.qtype === 'judge') {
        var ok = String(ua).trim().toUpperCase() === String(correct).trim().toUpperCase();
        if (q.qtype === 'judge') {
          var norm = { '对': '对', '错': '错', '正确': '对', '错误': '错', 'T': '对', 'F': '错' };
          ok = (norm[String(ua).trim().toUpperCase()] || String(ua).trim()) === String(correct).trim();
        }
        resultHtml = '<div style="margin-top:8px;padding:8px;border-radius:6px;font-size:13px;background:' + (ok ? '#e6f9f0' : '#fff8f0') + '">' +
          '<div><strong>' + (ok ? '✓ 正确' : '✗ 错误') + '</strong> 我的答案：' + escapeHtml(ua || '未作答') + '</div>' +
          (!ok ? '<div style="color:#30a46c">正确答案：' + escapeHtml(correct) + '</div>' : '') +
          (q.analysis ? '<div style="margin-top:4px;color:#666;white-space:pre-wrap">' + renderQText(q.analysis) + '</div>' : '') +
        '</div>';
      } else {
        resultHtml = '<div style="margin-top:8px;padding:8px;border-radius:6px;font-size:13px;background:#f5f0ff">' +
          '<div style="color:#7c3aed">✍ 主观题 · 待批改</div>' +
          (ua ? '<div style="margin-top:4px;color:#555;white-space:pre-wrap">作答：' + escapeHtml(ua) + '</div>' : '') +
          (q.answer ? '<div style="margin-top:4px;color:#30a46c">参考要点：' + escapeHtml(q.answer) + '</div>' : '') +
        '</div>';
      }
      return '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:10px;margin-bottom:10px;background:#fff">' +
        '<div style="font-size:12px;color:#888;margin-bottom:4px">第 ' + (i + 1) + ' 题 · ' + (typeLabel[q.qtype] || q.qtype) +
          (q.category ? ' · <span style="color:#4a6cf7">' + escapeHtml(q.category) + '</span>' : '') + '</div>' +
        '<div style="font-weight:600;font-size:14px;white-space:pre-wrap;line-height:1.6">' + renderQText(q.question) + '</div>' +
        resultHtml +
      '</div>';
    }).join('');

    container.innerHTML = header + body +
      '<div style="display:flex;gap:8px;margin-top:4px">' +
        '<button onclick="C6.openSmartExam()" style="flex:1;padding:8px;border:1px solid #d0d4de;border-radius:6px;background:#fff;cursor:pointer">返回列表</button>' +
        '<button id="seDelBtn" style="padding:8px 14px;border:1px solid #e5484d;color:#e5484d;border-radius:6px;background:#fff;cursor:pointer">删除</button>' +
      '</div>' +
      closeBtnHtml();

    document.getElementById('seDelBtn').onclick = function () {
      if (!confirm('确定删除该组卷？')) return;
      global.GK.api('/smartexam/papers/' + p.id, { method: 'DELETE' }).then(function () {
        showToast('已删除', 'success');
        C6.openSmartExam();
      }).catch(function (e) {
        showToast('删除失败: ' + (e && e.message ? e.message : e), 'error');
      });
    };
  }

  global.C6 = { openSmartExam: openSmartExam };
})(window);
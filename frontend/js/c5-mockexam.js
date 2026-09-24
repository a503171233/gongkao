/* =====================================================================
 * C5 · 模考解析模块（F1，网站一前端）
 * ---------------------------------------------------------------------
 * 职责：整卷上传/粘贴 → 后台识别拆题 → 按题型归属老师作答解析 → 报告
 * 复用 A4(GK.api) 传输层与 #c4Panel 右侧抽屉；登录用户专用。
 * 对外能力：
 *   C5.openMockExam() —— 打开模考解析面板
 * ===================================================================== */
(function (global) {
  'use strict';

  if (!global.GK) global.GK = {};

  var state = { view: 'list', paperId: 0, timer: null, questionsLoaded: false };

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
      alert('请先登录后使用模考解析');
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
    setTimeout(function () { div.remove(); }, 3200);
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

  function stopPoll() {
    if (state.timer) {
      clearTimeout(state.timer);
      state.timer = null;
    }
  }

  function isTerminal(status) {
    return status === 'ready' || status === 'failed';
  }

  function statusChip(p) {
    var map = { pending: '排队中', extracting: '识别题目中', answering: '作答解析中', ready: '已完成', failed: '已失败' };
    var colors = { pending: '#8b8fa3', extracting: '#f59e0b', answering: '#4a6cf7', ready: '#30a46c', failed: '#e5484d' };
    var t = map[p.status] || p.status;
    return '<span style="display:inline-block;padding:2px 10px;border-radius:10px;color:#fff;font-size:11px;background:' + (colors[p.status] || '#999') + '">' + t + '</span>';
  }

  function progressHtml(p) {
    if (!p.total_questions) return '';
    var pct = Math.round((p.done_questions + p.failed_questions) / p.total_questions * 100);
    pct = Math.max(0, Math.min(100, pct));
    return '<div style="height:8px;border-radius:4px;background:#eef0f6;overflow:hidden;margin:6px 0 4px">' +
      '<div style="height:100%;width:' + pct + '%;background:#4a6cf7;transition:width .4s"></div></div>' +
      '<div style="font-size:12px;color:#666">' + p.done_questions + ' 题已作答 · ' + p.failed_questions + ' 题失败 · 共 ' + p.total_questions + ' 题（' + pct + '%）</div>';
  }

  // -----------------------------------------------------------------
  // 列表视图
  // -----------------------------------------------------------------
  function openMockExam() {
    if (!requireLogin()) return;
    stopPoll();
    state.view = 'list';
    state.questionsLoaded = false;
    getPanel();
    renderList();
  }

  function renderList() {
    var container = getPanel();
    container.innerHTML =
      '<h3 style="margin:0 0 4px">📝 模考解析</h3>' +
      '<div style="font-size:12px;color:#888;margin-bottom:10px">上传/粘贴整套行测或申论试卷，由对应题型老师逐题作答并解析</div>' +
      '<button id="mockNewBtn" style="width:100%;padding:9px;border:1px dashed #4a6cf7;color:#4a6cf7;border-radius:8px;background:#f7f9ff;cursor:pointer;font-size:14px">＋ 新建模考解析</button>' +
      '<div id="mockNewBox" style="display:none;margin-top:10px;border:1px solid #e5e7eb;border-radius:8px;padding:10px"></div>' +
      '<div id="mockListArea" style="margin-top:12px"><p style="color:#999">加载中...</p></div>' +
      closeBtnHtml();

    document.getElementById('mockNewBtn').onclick = toggleNewBox;
    loadList();
  }

  function toggleNewBox() {
    var box = document.getElementById('mockNewBox');
    var show = box.style.display === 'none';
    box.style.display = show ? 'block' : 'none';
    if (!show) return;
    box.innerHTML =
      '<div style="display:flex;gap:6px;margin-bottom:8px">' +
        '<button id="mockTabFile" style="flex:1;padding:6px;border:1px solid #d0d4de;border-radius:6px;background:#fff;cursor:pointer;font-size:13px">📄 上传文件</button>' +
        '<button id="mockTabText" style="flex:1;padding:6px;border:1px solid #d0d4de;border-radius:6px;background:#fff;cursor:pointer;font-size:13px">📋 粘贴整卷</button>' +
      '</div>' +
      '<div id="mockFormBody" style="margin-top:8px"></div>' +
      '<div style="margin-top:8px;display:flex;align-items:center;gap:8px">' +
        '<select id="mockExamType" style="padding:5px 6px;border:1px solid #d0d4de;border-radius:6px;background:#fff;font-size:13px">' +
          '<option value="">自动判断</option><option value="行测">行测</option><option value="申论">申论</option>' +
        '</select>' +
        '<button id="mockStartBtn" style="padding:7px 16px;border:none;border-radius:6px;background:#4a6cf7;color:#fff;cursor:pointer;font-size:14px">开始解析</button>' +
      '</div>' +
      '<div style="margin-top:6px;font-size:12px;color:#888">整卷识别与逐题作答需数分钟，请耐心等待，可随时关闭面板稍后回来查看。</div>';

    var fileMode = true;
    var selFile = null;
    document.getElementById('mockTabFile').onclick = function () { fileMode = true; paintTabs(); };
    document.getElementById('mockTabText').onclick = function () { fileMode = false; paintTabs(); };

    function paintTabs() {
      var bf = document.getElementById('mockTabFile');
      var bt = document.getElementById('mockTabText');
      bf.style.background = fileMode ? '#4a6cf7' : '#fff';
      bf.style.color = fileMode ? '#fff' : '#333';
      bt.style.background = !fileMode ? '#4a6cf7' : '#fff';
      bt.style.color = !fileMode ? '#fff' : '#333';
      var body = document.getElementById('mockFormBody');
      if (fileMode) {
        body.innerHTML = '<input type="file" id="mockFile" accept=".md,.txt,.docx,.pptx,.pdf,.text" style="font-size:13px;width:100%">' +
          '<div style="font-size:12px;color:#999;margin-top:4px">支持 md/txt/docx/pptx/pdf · 单文件 ≤50MB · 图片随文档识别</div>';
        document.getElementById('mockFile').onchange = function () { selFile = this.files[0] || null; };
      } else {
        body.innerHTML = '<textarea id="mockText" placeholder="将整卷试题文本粘贴到这里（含题号，客观题带选项，作文/简答保留全文）…" style="width:100%;height:150px;border:1px solid #d0d4de;border-radius:6px;padding:8px;font-size:13px;resize:vertical;box-sizing:border-box"></textarea>';
      }
    }
    paintTabs();

    document.getElementById('mockStartBtn').onclick = function () {
      var examType = document.getElementById('mockExamType').value;
      if (fileMode) {
        if (!selFile) { alert('请先选择试卷文件'); return; }
        var fd = new FormData();
        fd.append('file', selFile);
        fd.append('exam_type', examType);
        var btn = this;
        btn.disabled = true;
        btn.textContent = '上传解析中…';
        global.GK.api('/mockexam/papers', { method: 'POST', body: fd, timeout: 180000 }).then(function (p) {
          showToast('试卷已上传，开始解析', 'success');
          btn.disabled = false;
          openPaper(p.id);
        }).catch(function (e) {
          btn.disabled = false;
          btn.textContent = '开始解析';
          showToast('上传失败: ' + (e && e.message ? e.message : e), 'error');
        });
      } else {
        var text = document.getElementById('mockText').value;
        if (!text || text.trim().length < 20) { alert('请粘贴完整的试卷内容（至少 20 字）'); return; }
        var btn2 = this;
        btn2.disabled = true;
        btn2.textContent = '识别解析中…';
        global.GK.api('/mockexam/papers/from-text', {
          method: 'POST',
          body: { title: '', exam_type: examType, text: text },
          timeout: 120000
        }).then(function (p) {
          showToast('已创建解析任务', 'success');
          btn2.disabled = false;
          openPaper(p.id);
        }).catch(function (e) {
          btn2.disabled = false;
          btn2.textContent = '开始解析';
          showToast('创建失败: ' + (e && e.message ? e.message : e), 'error');
        });
      }
    };
  }

  function loadList() {
    global.GK.api('/mockexam/papers').then(function (d) {
      var papers = d.papers || [];
      var area = document.getElementById('mockListArea');
      if (!area) return;
      if (!papers.length) {
        area.innerHTML = '<div style="padding:26px 10px;text-align:center;color:#aaa;font-size:13px">还没有模考解析记录<br>点击上方「新建模考解析」开始</div>';
        return;
      }
      var hasBusy = false;
      var html = papers.map(function (p) {
        if (!isTerminal(p.status)) hasBusy = true;
        var meta = p.exam_type ? escapeHtml(p.exam_type) + ' · ' : '';
        meta += escapeHtml(p.title || '未命名');
        return '<div class="mock-paper-row" data-pid="' + p.id + '" style="border:1px solid #e5e7eb;border-radius:8px;padding:10px;margin-bottom:8px;cursor:pointer;background:#fff">' +
          '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px">' +
            '<div style="font-weight:600;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + meta + '</div>' +
            statusChip(p) +
          '</div>' +
          (isTerminal(p.status) ? '' : progressHtml(p)) +
          (p.message && p.status === 'failed' ? '<div style="font-size:12px;color:#e5484d;margin-top:4px">' + escapeHtml(p.message) + '</div>' : '') +
          '<div style="font-size:12px;color:#999;margin-top:4px">' + p.done_questions + ' 已作答 / ' + p.total_questions + ' 题 · ' + escapeHtml((p.updated_at || '').slice(5, 16).replace('T', ' ')) + '</div>' +
        '</div>';
      }).join('');
      area.innerHTML = html;
      area.querySelectorAll('.mock-paper-row').forEach(function (row) {
        row.onclick = function () { openPaper(parseInt(row.getAttribute('data-pid'), 10)); };
      });
      stopPoll();
      if (hasBusy) {
        state.timer = setTimeout(function () { loadList(); }, 3000);
      }
    }).catch(function (e) {
      var area = document.getElementById('mockListArea');
      if (area) area.innerHTML = '<p style="color:#e5484d">加载失败: ' + escapeHtml(e.message) + '</p>';
    });
  }

  // -----------------------------------------------------------------
  // 详情视图
  // -----------------------------------------------------------------
  function openPaper(paperId) {
    stopPoll();
    state.paperId = paperId;
    state.view = 'detail';
    state.questionsLoaded = false;
    getPanel();
    fetchDetail();
  }

  function fetchDetail() {
    global.GK.api('/mockexam/papers/' + state.paperId).then(function (p) {
      renderDetail(p);
      if (isTerminal(p.status)) {
        if (!state.questionsLoaded) {
          state.questionsLoaded = true;
          loadQuestions(p.id);
        }
        return;
      }
      state.timer = setTimeout(fetchDetail, 2000);
    }).catch(function (e) {
      getPanel().innerHTML =
        '<h3>📝 模考解析</h3>' +
        '<p style="color:#e5484d">加载失败: ' + escapeHtml(e.message) + '</p>' +
        '<button onclick="C5.openMockExam()" style="padding:8px 16px;border:1px solid #d0d4de;border-radius:6px;background:#fff;cursor:pointer">返回列表</button>' +
        closeBtnHtml();
    });
  }

  function renderDetail(p) {
    var container = getPanel();
    var header =
      '<h3 style="margin:0 0 4px">📝 ' + escapeHtml(p.exam_type || '') + ' ' + escapeHtml(p.title || '模考解析') + '</h3>' +
      '<div style="margin:4px 0 8px">' + statusChip(p) + '</div>' +
      (p.message && p.status === 'failed' ? '<div style="font-size:12px;color:#e5484d;margin-bottom:6px">' + escapeHtml(p.message) + '</div>' : '') +
      '<div id="mockProgress">' + progressHtml(p) + '</div>';

    var summaryHtml = '';
    var s = p.summary || {};
    if (p.status === 'ready' && s.total) {
      summaryHtml = '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:8px;margin:6px 0">' +
        '<div style="font-size:12px;color:#888;margin-bottom:4px">按老师分布</div>' +
        Object.keys(s.teachers || {}).map(function (tname) {
          var t = s.teachers[tname];
          return '<div style="display:flex;justify-content:space-between;font-size:13px;padding:2px 0;border-bottom:1px dashed #eef0f6">' +
            '<span>👩‍🏫 ' + escapeHtml(tname) + '</span>' +
            '<span style="color:#666">' + t.total + ' 题 · 解析 ' + t.ok + (t.failed ? ' · 失败 ' + t.failed : '') + '</span></div>';
        }).join('') +
        (Object.keys(s.categories || {}).length
          ? '<div style="font-size:12px;color:#888;margin:6px 0 4px">按题型</div>' +
            Object.keys(s.categories).map(function (c) {
              return '<span style="display:inline-block;margin:2px 4px 0 0;padding:2px 8px;border-radius:10px;background:#eef4ff;color:#4a6cf7;font-size:11px">' +
                escapeHtml(c) + ' · ' + s.categories[c] + '</span>';
            }).join('')
          : '') +
      '</div>';
    }

    var actions =
      '<div style="display:flex;gap:6px;margin:4px 0 10px">' +
        '<button onclick="C5.openMockExam()" style="padding:5px 12px;border:1px solid #d0d4de;border-radius:6px;background:#fff;cursor:pointer;font-size:12px">‹ 返回列表</button>' +
        (isTerminal(p.status) && !state.questionsLoaded
          ? '<button id="mockRetryBtn" style="padding:5px 12px;border:1px solid #f59e0b;color:#b45309;border-radius:6px;background:#fff;cursor:pointer;font-size:12px">🔄 重新解析</button>' : '') +
        '<button id="mockDelBtn" style="padding:5px 12px;border:1px solid #e5484d;color:#e5484d;border-radius:6px;background:#fff;cursor:pointer;font-size:12px">🗑 删除</button>' +
      '</div>';

    container.innerHTML = header + actions + '<div id="mockSummary">' + summaryHtml + '</div>' +
      '<div id="mockQuestionArea" style="margin-top:6px"></div>' + closeBtnHtml();

    var retryBtn = document.getElementById('mockRetryBtn');
    if (retryBtn) retryBtn.onclick = doRetry;
    document.getElementById('mockDelBtn').onclick = function () {
      if (!confirm('确定删除该试卷及全部解析结果？')) return;
      global.GK.api('/mockexam/papers/' + p.id, { method: 'DELETE' }).then(function () {
        showToast('已删除', 'success');
        C5.openMockExam();
      }).catch(function (e) {
        showToast('删除失败: ' + (e && e.message ? e.message : e), 'error');
      });
    };
  }

  function doRetry() {
    global.GK.api('/mockexam/papers/' + state.paperId + '/retry', { method: 'POST' }).then(function (p) {
      showToast('已重新解析', 'success');
      state.questionsLoaded = false;
      fetchDetail();
    }).catch(function (e) {
      showToast('重试失败: ' + (e && e.message ? e.message : e), 'error');
    });
  }

  function loadQuestions(paperId) {
    global.GK.api('/mockexam/papers/' + paperId + '/questions').then(function (d) {
      var qs = d.questions || [];
      var area = document.getElementById('mockQuestionArea');
      if (!area) return;
      if (!qs.length) {
        area.innerHTML = '<div style="padding:18px;text-align:center;color:#999;font-size:13px">暂无题目</div>';
        return;
      }
      var typeLabel = { choice: '选择', judge: '判断', essay: '简答' };
      var html = qs.map(function (q) {
        var optsHtml = '';
        if (q.options && q.options.length) {
          optsHtml = '<div style="margin:4px 0">' + q.options.map(function (o) {
            return '<div style="padding:2px 0;font-size:13px">' + renderQText(String(o)) + '</div>';
          }).join('') + '</div>';
        }
        var answerHtml = q.status === 'ok'
          ? '<div style="margin-top:8px;background:#f7faf8;border:1px solid #e3efe9;border-radius:6px;padding:8px">' +
              '<div style="color:#1a7f4b;font-weight:600;margin-bottom:4px">✔ 参考答案：' + escapeHtml(q.answer || '') + '</div>' +
              (q.analysis ? '<div style="font-size:13px;white-space:pre-wrap;line-height:1.6">' + renderQText(q.analysis) + '</div>' : '') +
            '</div>'
          : q.status === 'failed'
            ? '<div style="margin-top:8px;background:#fdf0f0;border:1px solid #f3d3d3;border-radius:6px;padding:8px;color:#c0392b;font-size:13px">❌ 本题作答解析失败：' + escapeHtml(q.error || '') + '</div>'
            : '';
        var teacherBadge = q.teacher_name
          ? '<span style="display:inline-block;padding:1px 8px;border-radius:9px;background:#f3f0ff;color:#7c3aed;font-size:11px;margin-right:4px">👩‍🏫 ' + escapeHtml(q.teacher_name) + '</span>' : '';
        var catBadge = q.category
          ? '<span style="display:inline-block;padding:1px 8px;border-radius:9px;background:#eef4ff;color:#4a6cf7;font-size:11px;margin-right:4px">' + escapeHtml(q.category) + '</span>' : '';
        return '<div class="mock-qcard" data-expand="0" style="border:1px solid #e5e7eb;border-radius:8px;padding:10px;margin-bottom:8px;background:#fff">' +
          '<div style="font-size:12px;color:#666;margin-bottom:4px">第 ' + q.idx + ' 题 · ' + (typeLabel[q.qtype] || q.qtype) + catBadge + teacherBadge +
          '<span style="float:right;color:#4a6cf7;font-size:12px" class="mock-toggle">查看作答 ▾</span></div>' +
          '<div style="font-weight:600;font-size:14px;white-space:pre-wrap;line-height:1.6">' + renderQText(q.question) + '</div>' +
          optsHtml +
          '<div class="mock-answer" style="display:none">' + answerHtml + '</div>' +
        '</div>';
      }).join('');
      area.innerHTML = '<div style="font-size:12px;color:#888;margin:2px 0 8px">共 ' + qs.length + ' 题 · 点击卡片展开/收起作答解析</div>' + html;
      area.querySelectorAll('.mock-qcard').forEach(function (card) {
        card.onclick = function () {
          var openNow = card.getAttribute('data-expand') !== '1';
          card.setAttribute('data-expand', openNow ? '1' : '0');
          var ans = card.querySelector('.mock-answer');
          var tog = card.querySelector('.mock-toggle');
          if (ans) ans.style.display = openNow ? 'block' : 'none';
          if (tog) tog.textContent = openNow ? '收起作答 ▴' : '查看作答 ▾';
        };
      });
    }).catch(function (e) {
      var area = document.getElementById('mockQuestionArea');
      if (area) area.innerHTML = '<p style="color:#e5484d">题目加载失败: ' + escapeHtml(e.message) + '</p>';
    });
  }

  global.C5 = { openMockExam: openMockExam };
})(window);

/* =====================================================================
 * C11 · 申论 AI 批改模块（批次18）
 * C11.openEssay() 打开面板；依赖 GK.api / GK.printExport。
 * ===================================================================== */
(function (global) {
  'use strict';
  if (!global.GK) global.GK = {};

  var TYPES = {
    summary: { name: '归纳概括', full: 20, tip: '要点全面 · 条理清晰' },
    countermeasure: { name: '提出对策', full: 20, tip: '针对问题 · 可落地' },
    applied: { name: '应用文写作', full: 40, tip: '格式规范 · 场景契合' },
    composition: { name: '文章写作', full: 40, tip: '立意 · 结构 · 论证 · 语言' }
  };
  var _curType = 'summary';

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function btn(t, fn, extra) {
    return '<button onclick="' + fn + '" style="padding:6px 14px;border:1px solid var(--wood);color:var(--wood);border-radius:999px;background:var(--card);cursor:pointer;font-size:12.5px;font-weight:600;' + (extra || '') + '">' + t + '</button>';
  }
  function ensurePanel() {
    var p = document.getElementById('c11Panel');
    if (!p) {
      p = document.createElement('div');
      p.id = 'c11Panel';
      p.style.cssText = 'position:fixed;right:0;top:0;bottom:0;width:520px;max-width:100vw;background:var(--card);box-shadow:-2px 0 14px rgba(93,72,41,.16);z-index:200;display:flex;flex-direction:column;font-family:inherit;';
      document.body.appendChild(p);
    }
    return p;
  }
  function shell(title, inner) {
    var p = ensurePanel();
    p.innerHTML =
      '<div style="display:flex;align-items:center;gap:8px;padding:14px 16px;border-bottom:1px solid var(--line)">' +
        '<span style="font-size:15px;font-weight:700;color:var(--ink)">✍️ ' + title + '</span>' +
        '<span style="flex:1"></span>' +
        '<button onclick="C11.close()" style="padding:5px 10px;border:none;border-radius:8px;background:var(--paper-2);color:var(--ink-2);cursor:pointer;font-size:12px">✕</button>' +
      '</div>' +
      '<div id="c11Body" style="flex:1;overflow-y:auto;padding:16px;color:var(--ink);font-size:13.5px;line-height:1.7">' + inner + '</div>';
    return p;
  }
  function toast(msg, type) {
    var d = document.createElement('div');
    d.style.cssText = 'position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:10px;color:#fffaf0;font-size:13.5px;z-index:9999;box-shadow:0 4px 14px rgba(93,72,41,.3);font-weight:600';
    d.style.background = type === 'error' ? 'var(--bad)' : (type === 'success' ? 'var(--good)' : 'var(--wood)');
    d.textContent = msg;
    document.body.appendChild(d);
    setTimeout(function () { d.remove(); }, 3000);
  }

  function openEssay() {
    var uid = (global.GK.store && global.GK.store.userId) || 'anonymous';
    if (uid === 'anonymous') { global.GK.promptLogin('申论批改'); return; }
    shell('申论 AI 批改', '<p style="color:var(--ink-3);text-align:center;padding:30px 0">加载中…</p>');
    global.GK.api('/me/essay/history').then(function (r) {
      renderEntry(r.items || []);
    }).catch(function () { renderEntry([]); });
  }
  function close() {
    var p = document.getElementById('c11Panel');
    if (p) p.remove();
  }

  function renderEntry(history) {
    var cards = Object.keys(TYPES).map(function (k) {
      var t = TYPES[k];
      var on = k === _curType;
      return '<div onclick="C11.pick(\'' + k + '\')" style="cursor:pointer;border:1.5px solid ' + (on ? 'var(--wood)' : 'var(--line-2)') + ';background:' + (on ? 'var(--gold-soft)' : 'var(--card-2)') + ';border-radius:12px;padding:10px 12px;flex:1;min-width:45%">' +
        '<div style="font-weight:700;font-size:13px;color:var(--ink)">' + t.name + ' <span style="color:var(--gold-deep);font-size:12px">' + t.full + '分</span></div>' +
        '<div style="font-size:11px;color:var(--ink-3);margin-top:2px">' + t.tip + '</div></div>';
    }).join('');
    var hist = '';
    if (history.length) {
      hist = '<div style="font-weight:700;font-size:13px;color:var(--ink);margin:18px 0 8px">📚 批改历史</div>' +
        history.map(function (h) {
          var pct = Math.round(h.total / h.full_score * 100);
          return '<div onclick="C11.view(' + h.id + ')" style="cursor:pointer;display:flex;align-items:center;gap:10px;padding:9px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:6px;background:var(--card-2)">' +
            '<span style="font-weight:800;color:' + (pct >= 70 ? 'var(--good)' : pct >= 50 ? 'var(--gold-deep)' : 'var(--bad)') + ';font-size:16px;min-width:52px">' + h.total + '/' + h.full_score + '</span>' +
            '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12.5px">' + esc(h.title || TYPES[h.essay_type].name) + '</span>' +
            '<span style="font-size:11px;color:var(--ink-3)">' + esc((h.created_at || '').slice(5, 16)) + '</span></div>';
        }).join('');
    }
    shell('申论 AI 批改',
      '<div style="display:flex;flex-wrap:wrap;gap:8px">' + cards + '</div>' +
      '<div style="margin-top:14px">' +
        '<input id="c11Title" placeholder="题目（如：围绕基层治理写议论文）" style="width:100%;box-sizing:border-box;padding:9px 12px;border:1px solid var(--line-2);border-radius:10px;background:#fffaf0;font-size:13px;color:var(--ink)">' +
        '<textarea id="c11Prompt" placeholder="作答要求 / 材料要点（选填）" rows="2" style="width:100%;box-sizing:border-box;padding:9px 12px;border:1px solid var(--line-2);border-radius:10px;background:#fffaf0;font-size:13px;margin-top:8px;resize:vertical;color:var(--ink)"></textarea>' +
        '<textarea id="c11Essay" placeholder="在此粘贴你的申论作答（至少 50 字）…" rows="9" style="width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid var(--line-2);border-radius:10px;background:#fffaf0;font-size:13.5px;margin-top:8px;resize:vertical;line-height:1.7;color:var(--ink)"></textarea>' +
        '<div style="text-align:center;margin-top:12px">' + btn('🖊️ 提交批改', 'C11.grade()', 'padding:10px 30px;font-size:14.5px;background:var(--wood);color:#fffaf0;border:none') + '</div>' +
      '</div>' + hist);
  }
  function pick(t) { _curType = t; openEssay(); }

  function grade() {
    var body = document.getElementById('c11Body') || ensurePanel().querySelector('div:nth-child(2)');
    var payload = {
      essay_type: _curType,
      title: (document.getElementById('c11Title') || {}).value || '',
      prompt: (document.getElementById('c11Prompt') || {}).value || '',
      essay: (document.getElementById('c11Essay') || {}).value || ''
    };
    if (payload.essay.trim().length < 50) { toast('作答至少 50 字', 'error'); return; }
    shell('批改中…', '<div style="text-align:center;padding:60px 0"><div style="font-size:34px">✍️</div><p style="color:var(--ink-2)">阅卷组长正在逐段批改，约 20-40 秒…</p></div>');
    global.GK.api('/me/essay/grade', { method: 'POST', body: payload })
      .then(function (r) { renderResult(r); toast('批改完成', 'success'); })
      .catch(function (e) {
        shell('批改失败', '<p style="color:var(--bad);text-align:center;padding:30px 0">' + esc(e && e.message || e) + '</p>' +
          '<div style="text-align:center">' + btn('← 返回', 'C11.openEssay()') + '</div>');
      });
  }

  function renderResult(r) {
    var pct = Math.round(r.total / r.full_score * 100);
    var col = pct >= 70 ? 'var(--good)' : pct >= 50 ? 'var(--gold-deep)' : 'var(--bad)';
    var dims = (r.dims || []).map(function (d) {
      var w = Math.min(100, Math.round(d.score / d.full * 100));
      return '<div style="margin-bottom:8px"><div style="display:flex;font-size:12px"><span style="flex:1;font-weight:600">' + esc(d.name) + '</span><span style="color:var(--ink-2)">' + d.score + '/' + d.full + '</span></div>' +
        '<div style="height:7px;background:var(--paper-2);border-radius:4px;margin-top:3px;overflow:hidden"><div style="height:100%;width:' + w + '%;background:' + col + ';border-radius:4px"></div></div>' +
        '<div style="font-size:11.5px;color:var(--ink-3);margin-top:2px">' + esc(d.comment) + '</div></div>';
    }).join('');
    var notes = (r.notes || []).map(function (n) {
      return '<div style="border-left:3px solid var(--gold);background:var(--card-2);border-radius:0 10px 10px 0;padding:8px 12px;margin-bottom:7px">' +
        '<div style="font-size:12px;color:var(--wood-deep);font-weight:600">「' + esc(n.quote) + '」</div>' +
        '<div style="font-size:12.5px;color:var(--ink);margin-top:3px">' + esc(n.note) + '</div></div>';
    }).join('');
    var advice = (r.advice || []).map(function (a, i) {
      return '<li style="margin-bottom:5px;font-size:12.5px">' + esc(a) + '</li>';
    }).join('');
    shell('批改结果',
      '<div style="text-align:center;background:linear-gradient(135deg,var(--wood),var(--gold));border-radius:14px;padding:16px;color:#fffaf0">' +
        '<div style="font-size:34px;font-weight:800">' + r.total + '<span style="font-size:15px;opacity:.85"> / ' + r.full_score + '</span></div>' +
        '<div style="font-size:12px;opacity:.9;margin-top:2px">' + esc(r.type_name) + ' · ' + r.word_count + ' 字</div>' +
      '</div>' +
      '<div style="font-size:13px;color:var(--ink-2);margin:12px 0;font-weight:600">总评：' + esc(r.summary) + '</div>' +
      '<div style="font-weight:700;font-size:13px;margin:12px 0 8px">📊 维度评分</div>' + dims +
      '<div style="font-weight:700;font-size:13px;margin:14px 0 8px">📝 逐段批注</div>' + notes +
      '<div style="font-weight:700;font-size:13px;margin:14px 0 8px">💡 改进建议</div><ol style="padding-left:18px;margin:0">' + advice + '</ol>' +
      '<div style="text-align:center;margin-top:16px">' + btn('✕ 关闭', 'C11.close()') + ' ' + btn('🖨 导出 PDF', 'C11.printResult()') + ' ' + btn('← 再练一篇', 'C11.openEssay()') + '</div>');
  }

  function printResult() {
    var body = document.getElementById('c11Body') || ensurePanel().querySelector('div:nth-child(2)');
    if (body && body.innerHTML.replace(/<[^>]*>/g, '').trim()) {
      global.GK.printExport('申论批改报告', body.innerHTML);
    } else {
      toast('暂无可导出的批改结果', 'error');
    }
  }

  global.C11 = { openEssay: openEssay, close: close, pick: pick, grade: grade, printResult: printResult };
})(typeof window !== 'undefined' ? window : globalThis);

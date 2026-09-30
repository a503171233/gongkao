/* =====================================================================
 * C12 · 面试练习模块（批次18）
 * C12.openInterview() 打开面板；结构化面试五模块 AI 出题/点评。
 * ===================================================================== */
(function (global) {
  'use strict';
  if (!global.GK) global.GK = {};

  var MODULES = {
    comprehensive: '综合分析', organize: '组织计划', emergency: '应急应变',
    relations: '人际关系', expression: '言语表达'
  };
  var _cur = null;   // 当前题 {id, module_name, question}

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function btn(t, fn, extra) {
    return '<button onclick="' + fn + '" style="padding:6px 14px;border:1px solid var(--wood);color:var(--wood);border-radius:999px;background:var(--card);cursor:pointer;font-size:12.5px;font-weight:600;' + (extra || '') + '">' + t + '</button>';
  }
  function ensurePanel() {
    var p = document.getElementById('c12Panel');
    if (!p) {
      p = document.createElement('div');
      p.id = 'c12Panel';
      p.style.cssText = 'position:fixed;right:0;top:0;bottom:0;width:520px;max-width:100vw;background:var(--card);box-shadow:-2px 0 14px rgba(93,72,41,.16);z-index:200;display:flex;flex-direction:column;font-family:inherit;';
      document.body.appendChild(p);
    }
    return p;
  }
  function shell(title, inner) {
    var p = ensurePanel();
    p.innerHTML =
      '<div style="display:flex;align-items:center;gap:8px;padding:14px 16px;border-bottom:1px solid var(--line)">' +
        '<span style="font-size:15px;font-weight:700;color:var(--ink)">🎤 ' + title + '</span>' +
        '<span style="flex:1"></span>' +
        '<button onclick="C12.close()" style="padding:5px 10px;border:none;border-radius:8px;background:var(--paper-2);color:var(--ink-2);cursor:pointer;font-size:12px">✕</button>' +
      '</div>' +
      '<div id="c12Body" style="flex:1;overflow-y:auto;padding:16px;color:var(--ink);font-size:13.5px;line-height:1.7">' + inner + '</div>';
  }
  function toast(msg, type) {
    var d = document.createElement('div');
    d.style.cssText = 'position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:10px;color:#fffaf0;font-size:13.5px;z-index:9999;box-shadow:0 4px 14px rgba(93,72,41,.3);font-weight:600';
    d.style.background = type === 'error' ? 'var(--bad)' : 'var(--good)';
    d.textContent = msg;
    document.body.appendChild(d);
    setTimeout(function () { d.remove(); }, 3000);
  }

  function openInterview() {
    var uid = (global.GK.store && global.GK.store.userId) || 'anonymous';
    if (uid === 'anonymous') { global.GK.promptLogin('面试练习'); return; }
    shell('面试练习', '<p style="color:var(--ink-3);text-align:center;padding:30px 0">加载中…</p>');
    global.GK.api('/me/interview/history').then(function (r) {
      renderEntry(r.items || []);
    }).catch(function () { renderEntry([]); });
  }
  function close() {
    var p = document.getElementById('c12Panel');
    if (p) p.remove();
  }

  function renderEntry(history) {
    var mods = Object.keys(MODULES).map(function (k) {
      return '<div onclick="C12.gen(\'' + k + '\')" style="cursor:pointer;border:1.5px solid var(--line-2);background:var(--card-2);border-radius:12px;padding:12px;text-align:center;flex:1;min-width:44%">' +
        '<div style="font-weight:700;font-size:13.5px">' + MODULES[k] + '</div></div>';
    }).join('');
    var hist = '';
    if (history.length) {
      hist = '<div style="font-weight:700;font-size:13px;margin:18px 0 8px">📚 练习记录</div>' +
        history.map(function (h) {
          var score = h.score != null ? h.score : null;
          return '<div style="display:flex;align-items:center;gap:10px;padding:9px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:6px;background:var(--card-2)">' +
            '<span style="font-weight:800;min-width:44px;color:' + (score == null ? 'var(--ink-3)' : score >= 70 ? 'var(--good)' : score >= 55 ? 'var(--gold-deep)' : 'var(--bad)') + '">' + (score == null ? '待答' : score) + '</span>' +
            '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12.5px">' + esc(h.question) + '</span></div>';
        }).join('');
    }
    shell('面试练习',
      '<div style="color:var(--ink-2);font-size:12.5px;margin-bottom:10px">结构化面试五大模块，AI 出题 · 作答 · 考官视角点评</div>' +
      '<div style="display:flex;flex-wrap:wrap;gap:8px">' + mods + '</div>' + hist);
  }

  function gen(module) {
    shell('出题中…', '<div style="text-align:center;padding:60px 0"><div style="font-size:34px">🎤</div><p style="color:var(--ink-2)">考官正在出题…</p></div>');
    global.GK.api('/me/interview/question', { method: 'POST', body: { module: module } })
      .then(function (r) { _cur = r; renderQuestion(); })
      .catch(function (e) {
        shell('出题失败', '<p style="color:var(--bad);text-align:center;padding:30px 0">' + esc(e && e.message || e) + '</p>' +
          '<div style="text-align:center">' + btn('← 返回', 'C12.openInterview()') + '</div>');
      });
  }

  function renderQuestion() {
    shell(MODULES[_cur.module] + ' · 第 ' + _cur.id + ' 题',
      '<div style="background:linear-gradient(135deg,var(--wood),var(--gold));border-radius:14px;padding:16px;color:#fffaf0;font-size:14px;line-height:1.8">' + esc(_cur.question) + '</div>' +
      '<textarea id="c12Ans" placeholder="按面试答题思路作答（先亮观点 → 分层展开 → 落地收尾）。至少 10 字…" rows="9" style="width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid var(--line-2);border-radius:10px;background:#fffaf0;font-size:13.5px;margin-top:12px;resize:vertical;line-height:1.7;color:var(--ink)"></textarea>' +
      '<div style="text-align:center;margin-top:10px">' + btn('🎙️ 提交作答', 'C12.answer()', 'padding:10px 30px;font-size:14.5px;background:var(--wood);color:#fffaf0;border:none') + '</div>' +
      '<div style="text-align:center;margin-top:10px">' + btn('← 换一题', 'C12.openInterview()') + '</div>');
  }

  function answer() {
    var ans = (document.getElementById('c12Ans') || {}).value || '';
    if (ans.trim().length < 10) { toast('作答至少 10 字', 'error'); return; }
    shell('点评中…', '<div style="text-align:center;padding:60px 0"><div style="font-size:34px">🧑‍⚖️</div><p style="color:var(--ink-2)">考官点评中，约 20-40 秒…</p></div>');
    global.GK.api('/me/interview/answer', { method: 'POST', body: { qid: _cur.id, answer: ans } })
      .then(renderReview)
      .catch(function (e) {
        shell('点评失败', '<p style="color:var(--bad);text-align:center;padding:30px 0">' + esc(e && e.message || e) + '</p>' +
          '<div style="text-align:center">' + btn('← 返回', 'C12.openInterview()') + '</div>');
      });
  }

  function renderReview(r) {
    var col = r.score >= 70 ? 'var(--good)' : r.score >= 55 ? 'var(--gold-deep)' : 'var(--bad)';
    var rev = (r.review || []).map(function (x) {
      return '<div style="margin-bottom:7px"><span style="font-weight:700;color:var(--wood-deep);font-size:12.5px">【' + esc(x.dim) + '】</span>' +
        '<span style="font-size:12.5px;color:var(--ink)">' + esc(x.comment) + '</span></div>';
    }).join('');
    var adv = (r.advice || []).map(function (a) { return '<li style="margin-bottom:5px;font-size:12.5px">' + esc(a) + '</li>'; }).join('');
    shell('考官点评',
      '<div style="text-align:center;background:linear-gradient(135deg,var(--wood),var(--gold));border-radius:14px;padding:14px;color:#fffaf0">' +
        '<div style="font-size:32px;font-weight:800">' + r.score + '<span style="font-size:14px;opacity:.85"> / 100</span></div>' +
        '<div style="font-size:12px;opacity:.9;margin-top:2px">' + esc(r.summary) + '</div></div>' +
      '<div style="font-weight:700;font-size:13px;margin:14px 0 8px">📊 考官点评</div>' + rev +
      '<div style="font-weight:700;font-size:13px;margin:14px 0 8px">💡 改进建议</div><ol style="padding-left:18px;margin:0">' + adv + '</ol>' +
      '<div style="font-weight:700;font-size:13px;margin:14px 0 8px">📋 参考答题框架</div>' +
      '<div style="background:var(--card-2);border:1px solid var(--line);border-radius:10px;padding:10px 14px;font-size:12.5px;color:var(--ink-2)">' + esc(r.outline) + '</div>' +
      '<div style="text-align:center;margin-top:16px">' + btn('🎯 再来一题', 'C12.openInterview()') + '</div>');
    toast('点评完成：' + r.score + ' 分', 'success');
  }

  global.C12 = { openInterview: openInterview, close: close, gen: gen, answer: answer };
})(typeof window !== 'undefined' ? window : globalThis);

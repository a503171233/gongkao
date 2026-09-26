/* =====================================================================
 * C10 · 粉笔提升计划模块（批次17）
 * ---------------------------------------------------------------------
 * 职责：学员绑定粉笔账号（验证码/扫码/Cookie 兜底）→ 一键采集错题 →
 *       AI 综合分析 → 专属提升计划任务书（可导出 PDF/DOCX）。
 * 复用 A4 (GK.api/GK.docxExport/GK.printExport) 传输与导出层。
 *
 * 对外能力：
 *   C10.openFenbi() —— 打开提升计划面板
 *   C10.close()     —— 关闭面板（并停止轮询）
 * ===================================================================== */
(function (global) {
  'use strict';

  if (!global.GK) global.GK = {};

  var _pollTimer = null;   // 分析进度轮询
  var _loginTimer = null;  // 登录会话轮询
  var _curSid = null;      // 当前登录会话
  var _curMode = null;     // sms / qrcode / cookie

  // ---------------- 工具 ----------------
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function showToast(msg, type) {
    var div = document.createElement('div');
    div.style.cssText = 'position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;color:#fff;font-size:14px;z-index:9999;box-shadow:0 2px 12px rgba(0,0,0,.15);';
    div.style.background = type === 'error' ? '#e5484d' : (type === 'success' ? '#30a46c' : '#4a6cf7');
    div.textContent = msg;
    document.body.appendChild(div);
    setTimeout(function () { div.remove(); }, 3000);
  }

  function requireLogin() {
    var uid = (global.GK.store && global.GK.store.userId) || 'anonymous';
    if (uid === 'anonymous') {
      alert('请先登录后使用提升计划');
      return false;
    }
    return true;
  }

  function ensurePanel() {
    var p = document.getElementById('c10Panel');
    if (!p) {
      p = document.createElement('div');
      p.id = 'c10Panel';
      p.style.cssText = 'position:fixed;right:0;top:0;bottom:0;width:480px;max-width:94vw;' +
        'background:#fff;box-shadow:-2px 0 14px rgba(0,0,0,.12);z-index:200;' +
        'display:flex;flex-direction:column;font-family:inherit;';
      document.body.appendChild(p);
    }
    return p;
  }

  function stopTimers() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
    if (_loginTimer) { clearInterval(_loginTimer); _loginTimer = null; }
  }

  function close() {
    stopTimers();
    var p = document.getElementById('c10Panel');
    if (p) p.remove();
  }

  function btn(text, onclick, style) {
    return '<button onclick="' + onclick + '" style="padding:5px 12px;border:1px solid #4a6cf7;color:#4a6cf7;' +
      'border-radius:6px;background:#fff;cursor:pointer;font-size:12px;' + (style || '') + '">' + text + '</button>';
  }

  function card(icon, title) {
    return '<div style="font-size:13px;font-weight:700;color:#333;margin:16px 0 8px;display:flex;align-items:center;gap:6px">' +
      '<span>' + icon + '</span><span>' + title + '</span></div>';
  }

  // ---------------- 状态页 ----------------
  function openFenbi() {
    if (!requireLogin()) return;
    var panel = ensurePanel();
    panel.innerHTML =
      '<div style="display:flex;align-items:center;gap:8px;padding:14px 16px;border-bottom:1px solid #eceef3">' +
        '<span style="font-size:16px;font-weight:700">🎯 粉笔提升计划</span>' +
        '<span style="flex:1"></span>' +
        '<button id="c10ExportPdf" onclick="C10.printPlan()" style="padding:5px 10px;border:1px solid #4a6cf7;color:#4a6cf7;border-radius:6px;background:#fff;cursor:pointer;font-size:12px;display:none">🖨 导出 PDF</button>' +
        '<button id="c10ExportDocx" onclick="C10.docxPlan()" style="padding:5px 10px;border:1px solid #4a6cf7;color:#4a6cf7;border-radius:6px;background:#fff;cursor:pointer;font-size:12px;display:none">📄 导出 DOCX</button>' +
        '<button onclick="C10.close()" style="padding:5px 10px;border:none;border-radius:6px;background:#f0f2f7;cursor:pointer;font-size:12px">✕</button>' +
      '</div>' +
      '<div id="c10Body" style="flex:1;overflow-y:auto;padding:14px 16px;color:#333;font-size:13.5px;line-height:1.6">' +
        '<p style="color:#999;text-align:center">加载中…</p>' +
      '</div>';
    refresh();
  }

  function refresh() {
    global.GK.api('/me/fenbi/binding').then(function (d) {
      var body = document.getElementById('c10Body');
      if (!body) return;
      var b = d.binding, latest = d.latest;
      if (latest && latest.status === 'running') {
        renderProgress(latest);
        startPolling();
      } else if (latest && latest.status === 'done' && latest.report && latest.report.summary) {
        renderPlan(latest, b);
      } else if (b && b.status === 'bound') {
        renderBound(b, latest);
      } else if (b && b.status === 'expired') {
        renderExpired(b);
      } else {
        renderEntry();
      }
    }).catch(function (e) {
      var body = document.getElementById('c10Body');
      if (body) body.innerHTML = '<p style="color:#e5484d;text-align:center;padding:40px 0">加载失败: ' + escapeHtml(e && e.message ? e.message : e) + '</p>';
    });
  }

  // ---------------- 入口：三种绑定方式 ----------------
  function renderEntry() {
    var body = document.getElementById('c10Body');
    body.innerHTML =
      '<div style="background:linear-gradient(135deg,#4a6cf7,#6d8dff);color:#fff;border-radius:10px;padding:14px 16px">' +
        '<div style="font-size:14.5px;font-weight:700">绑定粉笔账号，生成专属提升计划</div>' +
        '<div style="font-size:12px;opacity:.9;margin-top:4px">自动同步你的粉笔错题，AI 诊断薄弱点并定制 4 周提分任务书</div>' +
      '</div>' +
      '<div style="display:flex;flex-direction:column;gap:10px;margin-top:14px">' +
        '<div onclick="C10.startCookie()" style="border:1px solid #e3e6ef;border-radius:10px;padding:12px 14px;cursor:pointer">' +
          '<div style="font-weight:700;font-size:13.5px">📋 方式一：粘贴 Cookie（推荐，最简单）</div>' +
          '<div style="color:#888;font-size:12px;margin-top:3px">电脑浏览器登录粉笔网页版 → 复制 Cookie 粘贴即可，1 分钟完成</div>' +
        '</div>' +
        '<div onclick="C10.startSms()" style="border:1px solid #e3e6ef;border-radius:10px;padding:12px 14px;cursor:pointer">' +
          '<div style="font-weight:700;font-size:13.5px">📱 方式二：手机验证码登录</div>' +
          '<div style="color:#888;font-size:12px;margin-top:3px">输入粉笔手机号，接收短信验证码完成登录</div>' +
        '</div>' +
        '<div onclick="C10.startQrcode()" style="border:1px solid #e3e6ef;border-radius:10px;padding:12px 14px;cursor:pointer">' +
          '<div style="font-weight:700;font-size:13.5px">🔷 方式三：粉笔 APP 扫码登录</div>' +
          '<div style="color:#888;font-size:12px;margin-top:3px">打开粉笔 APP 扫一扫，账号密码都不经过本平台</div>' +
        '</div>' +
      '</div>' +
      '<div style="color:#aaa;font-size:11.5px;margin-top:14px;line-height:1.7">· 仅采集错题数据用于分析，题目原文只保存在你的账号下<br>· 随时可解除绑定；账号密码不会也无法被本平台获取</div>';
  }

  // ---------------- Cookie 绑定 ----------------
  function startCookie() {
    _curMode = 'cookie';
    var body = document.getElementById('c10Body');
    body.innerHTML =
      '<div style="font-weight:700;font-size:14px;margin-bottom:8px">📋 Cookie 粘贴绑定</div>' +
      '<div style="background:#f6f7fb;border-radius:8px;padding:10px 12px;font-size:12px;color:#555;line-height:1.8">' +
        '1. 电脑 Chrome 打开 <b>www.fenbi.com</b> 并登录<br>' +
        '2. 按 <b>F12</b> 打开开发者工具 → Console（控制台）<br>' +
        '3. 输入 <code style="background:#eceef5;padding:1px 5px;border-radius:4px">document.cookie</code> 回车<br>' +
        '4. 复制输出的整段文字粘贴到下方' +
      '</div>' +
      '<textarea id="c10Cookie" placeholder="粘贴 document.cookie 的输出内容…" style="width:100%;box-sizing:border-box;height:110px;margin-top:10px;border:1px solid #d9dce6;border-radius:8px;padding:10px;font-size:12px;font-family:monospace;resize:vertical"></textarea>' +
      '<div style="display:flex;gap:8px;margin-top:10px">' +
        '<button id="c10CookieBtn" onclick="C10.submitCookie()" style="flex:1;padding:10px;border:none;border-radius:8px;background:#4a6cf7;color:#fff;font-size:13.5px;cursor:pointer">校验并绑定</button>' +
        btn('← 返回', 'C10.refresh()', 'border-color:#d9dce6;color:#666') +
      '</div>';
  }

  function submitCookie() {
    var ta = document.getElementById('c10Cookie');
    var val = (ta && ta.value || '').trim();
    if (!val) { showToast('请先粘贴 Cookie', 'error'); return; }
    var b = document.getElementById('c10CookieBtn');
    b.disabled = true; b.textContent = '校验中…';
    global.GK.api('/me/fenbi/cookie', { method: 'POST', body: { cookie: val } })
      .then(function (r) {
        showToast('绑定成功！发现 ' + (r.total_mistakes || 0) + ' 道错题', 'success');
        refresh();
      })
      .catch(function (e) {
        b.disabled = false; b.textContent = '校验并绑定';
        showToast((e && e.message) || '绑定失败', 'error');
      });
  }

  // ---------------- 已绑定状态页 ----------------
  function renderBound(b, latest) {
    var body = document.getElementById('c10Body');
    var lastCheck = b.last_check_at ? String(b.last_check_at).slice(0, 16) : '';
    body.innerHTML =
      '<div style="background:#f2fbf5;border:1px solid #cdebd6;border-radius:10px;padding:12px 14px;display:flex;align-items:center;gap:10px">' +
        '<span style="font-size:22px">✅</span>' +
        '<div style="flex:1">' +
          '<div style="font-weight:700;font-size:13.5px">粉笔账号已绑定' + (b.phone ? '（' + escapeHtml(b.phone) + '）' : '') + '</div>' +
          '<div style="color:#888;font-size:12px;margin-top:2px">绑定时间 ' + escapeHtml(String(b.bound_at || '').slice(0, 16)) +
          (lastCheck ? ' · 最近校验 ' + escapeHtml(lastCheck) : '') + '</div>' +
        '</div>' +
        '<button onclick="C10.unbind()" style="padding:4px 10px;border:1px solid #e5484d;color:#e5484d;border-radius:6px;background:#fff;cursor:pointer;font-size:11.5px">解绑</button>' +
      '</div>' +
      (latest && latest.status === 'failed'
        ? '<div style="background:#fff5f5;border:1px solid #ffc9c9;border-radius:8px;padding:10px 12px;margin-top:10px;font-size:12.5px;color:#c92a2a">上次生成失败：' + escapeHtml(latest.error || '未知错误') + '</div>'
        : '') +
      '<div style="text-align:center;margin-top:18px">' +
        '<button onclick="C10.analyze()" style="padding:12px 34px;border:none;border-radius:10px;background:#4a6cf7;color:#fff;font-size:15px;font-weight:700;cursor:pointer;box-shadow:0 4px 14px rgba(74,108,247,.35)">🚀 生成我的提升计划</button>' +
        '<div style="color:#999;font-size:11.5px;margin-top:8px">采集错题 → AI 诊断 → 定制 4 周任务书，约需 1~3 分钟</div>' +
      '</div>';
    if (latest && latest.status === 'done' && latest.report) {
      body.innerHTML += '<div id="c10PlanLegacy">' + renderPlanInner(latest) + '</div>';
      showExport(true);
    } else {
      showExport(false);
    }
  }

  function renderExpired(b) {
    var body = document.getElementById('c10Body');
    body.innerHTML =
      '<div style="background:#fff8e6;border:1px solid #ffe08a;border-radius:10px;padding:14px">' +
        '<div style="font-weight:700;font-size:13.5px">⚠️ 粉笔登录态已失效</div>' +
        '<div style="color:#888;font-size:12.5px;margin-top:4px">请重新绑定以继续采集错题（历史任务书仍可查看）</div>' +
      '</div>' +
      '<div style="display:flex;gap:8px;margin-top:12px">' +
        '<button onclick="C10.startCookie()" style="flex:1;padding:10px;border:none;border-radius:8px;background:#4a6cf7;color:#fff;font-size:13px;cursor:pointer">重新绑定（Cookie）</button>' +
        '<button onclick="C10.startSms()" style="flex:1;padding:10px;border:1px solid #d9dce6;border-radius:8px;background:#fff;color:#333;font-size:13px;cursor:pointer">验证码登录</button>' +
      '</div>';
  }

  function unbind() {
    if (!confirm('确定解除粉笔账号绑定？已生成的任务书将保留。')) return;
    global.GK.api('/me/fenbi/unbind', { method: 'POST' })
      .then(function () { showToast('已解除绑定', 'success'); refresh(); })
      .catch(function (e) { showToast((e && e.message) || '操作失败', 'error'); });
  }

  // ---------------- 分析进度 ----------------
  function analyze() {
    global.GK.api('/me/fenbi/analyze', { method: 'POST' })
      .then(function () {
        showToast('已开始，请稍候…', 'success');
        renderProgress({ stage: '准备中', progress: 5 });
        startPolling();
      })
      .catch(function (e) {
        showToast((e && e.message) || '触发失败', 'error');
      });
  }

  function renderProgress(latest) {
    showExport(false);
    var body = document.getElementById('c10Body');
    var p = Math.max(3, Math.min(100, latest.progress || 0));
    body.innerHTML =
      '<div style="text-align:center;padding:40px 10px">' +
        '<div style="font-size:40px">🤖</div>' +
        '<div style="font-weight:700;font-size:15px;margin-top:10px">AI 正在分析你的错题…</div>' +
        '<div style="color:#888;font-size:12.5px;margin-top:6px" id="c10Stage">' + escapeHtml(latest.stage || '准备中') + '</div>' +
        '<div style="height:8px;background:#eef0f5;border-radius:5px;overflow:hidden;margin:18px 30px 0">' +
          '<div id="c10Bar" style="height:100%;width:' + p + '%;background:linear-gradient(90deg,#4a6cf7,#6d8dff);border-radius:5px;transition:width .6s"></div>' +
        '</div>' +
        '<div id="c10Pct" style="color:#4a6cf7;font-size:13px;font-weight:700;margin-top:8px">' + p + '%</div>' +
        '<div style="color:#bbb;font-size:11.5px;margin-top:14px">错题越多耗时越长，可离开页面稍后回来查看</div>' +
      '</div>';
  }

  function startPolling() {
    stopTimers();
    _pollTimer = setInterval(function () {
      global.GK.api('/me/fenbi/analysis/latest').then(function (d) {
        var l = d.latest;
        if (!l) return;
        if (l.status === 'running') {
          var bar = document.getElementById('c10Bar');
          var pct = document.getElementById('c10Pct');
          var st = document.getElementById('c10Stage');
          if (bar) bar.style.width = Math.max(3, l.progress || 0) + '%';
          if (pct) pct.textContent = (l.progress || 0) + '%';
          if (st) st.textContent = l.stage || '';
        } else {
          stopTimers();
          refresh();
          if (l.status === 'done') showToast('提升计划已生成！', 'success');
          else if (l.status === 'failed') showToast('生成失败：' + (l.error || ''), 'error');
        }
      }).catch(function () { /* 轮询容错 */ });
    }, 3000);
  }

  // ---------------- 任务书渲染 ----------------
  function levelColor(lv) {
    return lv >= 4 ? '#e5484d' : (lv >= 3 ? '#f59f00' : '#4a6cf7');
  }

  function renderPlanInner(latest) {
    var rep = latest.report || {};
    var dist = latest.distribution || [];
    var h = '';
    h += '<div style="border-top:1px dashed #e3e6ef;margin:18px 0 4px"></div>';
    h += '<div style="text-align:center;margin-top:10px">' +
      '<div style="font-size:16px;font-weight:800;color:#333">学员专属提升计划任务书</div>' +
      '<div style="color:#999;font-size:11.5px;margin-top:3px">生成于 ' + escapeHtml(String(latest.done_at || '').slice(0, 16)) +
      ' · 共采集错题 ' + (latest.total_mistakes || 0) + ' 道' + '</div></div>';

    // 总体诊断
    h += card('📋', '总体诊断');
    h += '<div style="background:#f6f7fb;border-radius:8px;padding:11px 13px;font-size:13px;line-height:1.8">' + escapeHtml(rep.summary || '') + '</div>';

    // 错题分布
    if (dist.length) {
      h += card('📊', '错题模块分布');
      var maxT = Math.max.apply(null, dist.map(function (x) { return x.total || 0; }).concat([1]));
      h += dist.map(function (m) {
        return '<div style="margin:7px 0">' +
          '<div style="display:flex;font-size:12px;justify-content:space-between"><span>' + escapeHtml(m.module) + '</span><span style="color:#888">' + m.total + ' 题</span></div>' +
          '<div style="height:7px;background:#eef0f5;border-radius:4px;overflow:hidden;margin-top:3px"><div style="height:100%;width:' + Math.round(100 * (m.total || 0) / maxT) + '%;background:#4a6cf7;border-radius:4px"></div></div>' +
          '</div>';
      }).join('');
    }

    // 薄弱点
    var wps = rep.weak_points || [];
    if (wps.length) {
      h += card('🎯', '薄弱点诊断');
      h += wps.map(function (w) {
        var lv = Math.max(1, Math.min(5, w.level || 3));
        return '<div style="margin:9px 0">' +
          '<div style="display:flex;align-items:center;gap:8px;font-size:12.5px">' +
            '<span style="font-weight:700">' + escapeHtml(w.name) + '</span>' +
            '<span style="color:#999;font-size:11px">' + escapeHtml(w.module || '') + '</span>' +
            '<span style="flex:1"></span>' +
            '<span style="color:' + levelColor(lv) + ';font-size:11px;font-weight:700">' + '★'.repeat(lv) + '</span>' +
          '</div>' +
          '<div style="height:6px;background:#eef0f5;border-radius:3px;overflow:hidden;margin-top:4px"><div style="height:100%;width:' + (lv * 20) + '%;background:' + levelColor(lv) + ';border-radius:3px"></div></div>' +
          '<div style="color:#777;font-size:11.5px;margin-top:3px">' + escapeHtml(w.reason || '') + '</div>' +
          '</div>';
      }).join('');
    }

    // 学习建议
    var adv = rep.advice || [];
    if (adv.length) {
      h += card('💡', '学习建议');
      h += '<ol style="margin:0;padding-left:20px;font-size:12.5px;line-height:1.9">' +
        adv.map(function (a) { return '<li>' + escapeHtml(a) + '</li>'; }).join('') + '</ol>';
    }

    // 4 周计划
    var weeks = rep.weeks || [];
    if (weeks.length) {
      h += card('📅', '四周提升计划');
      h += weeks.map(function (w, i) {
        var tasks = w.tasks || [];
        return '<div style="border:1px solid #e3e6ef;border-radius:10px;padding:11px 13px;margin:10px 0">' +
          '<div style="display:flex;align-items:center;gap:8px">' +
            '<span style="background:#4a6cf7;color:#fff;font-size:11px;padding:2px 8px;border-radius:10px">第 ' + (i + 1) + ' 周</span>' +
            '<span style="font-weight:700;font-size:13px">' + escapeHtml(w.theme || '') + '</span>' +
          '</div>' +
          '<div style="color:#888;font-size:11.5px;margin-top:4px">🎯 ' + escapeHtml(w.goal || '') + '</div>' +
          '<table style="width:100%;border-collapse:collapse;margin-top:8px;font-size:12px">' +
            '<thead><tr style="color:#999;text-align:left">' +
              '<th style="padding:4px 6px;border-bottom:1px solid #eceef3;width:74px">时间</th>' +
              '<th style="padding:4px 6px;border-bottom:1px solid #eceef3">任务</th>' +
              '<th style="padding:4px 6px;border-bottom:1px solid #eceef3;width:56px;text-align:right">时长</th>' +
            '</tr></thead><tbody>' +
            tasks.map(function (t) {
              return '<tr>' +
                '<td style="padding:6px;border-bottom:1px solid #f2f3f8;color:#4a6cf7;white-space:nowrap">' + escapeHtml(t.slot || '') + '</td>' +
                '<td style="padding:6px;border-bottom:1px solid #f2f3f8"><b>' + escapeHtml(t.title || '') + '</b>' +
                  (t.detail ? '<div style="color:#888;font-size:11px;margin-top:2px">' + escapeHtml(t.detail) + '</div>' : '') + '</td>' +
                '<td style="padding:6px;border-bottom:1px solid #f2f3f8;text-align:right;color:#888;white-space:nowrap">' + (t.minutes ? t.minutes + '分钟' : '—') + '</td>' +
              '</tr>';
            }).join('') +
          '</tbody></table></div>';
      }).join('');
    }

    h += '<div style="color:#bbb;font-size:11px;text-align:center;margin:16px 0 6px">— 本任务书由 AI 基于粉笔错题数据生成，供备考参考 —</div>';
    return h;
  }

  function renderPlan(latest, b) {
    var body = document.getElementById('c10Body');
    body.innerHTML =
      '<div style="display:flex;align-items:center;gap:8px;background:#f2fbf5;border:1px solid #cdebd6;border-radius:8px;padding:9px 12px;margin-bottom:4px;font-size:12.5px">' +
        '<span>✅ 已绑定' + (b && b.phone ? '（' + escapeHtml(b.phone) + '）' : '') + ' · 错题 ' + (latest.wrong_count || latest.total_mistakes || 0) + ' 道</span>' +
        '<span style="flex:1"></span>' +
        btn('🔄 重新生成', 'C10.regen()', 'border-color:#d9dce6;color:#666') +
      '</div>' +
      renderPlanInner(latest);
    showExport(true);
  }

  function regen() {
    analyze();
  }

  function showExport(on) {
    ['c10ExportPdf', 'c10ExportDocx'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.style.display = on ? '' : 'none';
    });
  }

  function printPlan() {
    var body = document.getElementById('c10Body');
    if (body && body.innerHTML.indexOf('加载中') < 0) {
      global.GK.printExport('提升计划任务书', body.innerHTML);
    }
  }

  function docxPlan() {
    var body = document.getElementById('c10Body');
    if (body) global.GK.docxExport('提升计划任务书', body.innerHTML);
  }

  // ---------------- 登录会话（验证码/扫码，后端会话端点） ----------------
  function renderLoginShell(title, inner) {
    var body = document.getElementById('c10Body');
    body.innerHTML =
      '<div style="font-weight:700;font-size:14px;margin-bottom:8px">' + title + '</div>' +
      '<div id="c10LoginArea">' + inner + '</div>' +
      '<div style="margin-top:10px">' + btn('← 返回', 'C10.refresh()', 'border-color:#d9dce6;color:#666') + '</div>';
  }

  function startSms() {
    _curMode = 'sms';
    var phone = prompt('请输入粉笔绑定的手机号：');
    if (!phone || !phone.trim()) return;
    renderLoginShell('📱 验证码登录', '<p style="color:#999;text-align:center;padding:20px 0">正在打开登录会话…</p>');
    global.GK.api('/me/fenbi/login/sms', { method: 'POST', body: { phone: phone.trim() } })
      .then(function (r) {
        _curSid = r.sid;
        pollLogin();
      })
      .catch(function (e) {
        renderLoginShell('📱 验证码登录',
          '<p style="color:#e5484d;font-size:12.5px">会话发起失败：' + escapeHtml(e && e.message ? e.message : e) + '</p>');
      });
  }

  function startQrcode() {
    _curMode = 'qrcode';
    renderLoginShell('🔷 扫码登录', '<p style="color:#999;text-align:center;padding:20px 0">正在获取二维码…</p>');
    global.GK.api('/me/fenbi/login/qrcode', { method: 'POST' })
      .then(function (r) {
        _curSid = r.sid;
        pollLogin();
      })
      .catch(function (e) {
        renderLoginShell('🔷 扫码登录',
          '<p style="color:#e5484d;font-size:12.5px">会话发起失败：' + escapeHtml(e && e.message ? e.message : e) + '</p>');
      });
  }

  function pollLogin() {
    stopTimers();
    _loginTimer = setInterval(function () {
      global.GK.api('/me/fenbi/login/status?sid=' + encodeURIComponent(_curSid))
        .then(function (r) { renderLoginState(r); })
        .catch(function () { /* 容错继续轮询 */ });
    }, 2000);
  }

  function renderLoginState(r) {
    var area = document.getElementById('c10LoginArea');
    if (!area) return;
    var state = r.state || 'init';
    if (state === 'success') {
      stopTimers();
      showToast('粉笔账号绑定成功！', 'success');
      refresh();
      return;
    }
    if (state === 'failed' || state === 'cancelled' || state === 'timeout') {
      stopTimers();
      area.innerHTML = '<p style="color:#e5484d;font-size:12.5px">登录失败：' + escapeHtml(r.error || state) + '</p>' +
        '<div style="margin-top:8px">' + btn('重试', 'C10.startSms()', 'border-color:#d9dce6;color:#666') + '</div>';
      return;
    }
    if (state === 'ready_qrcode' && r.shot) {
      area.innerHTML =
        '<div style="text-align:center;padding:8px 0">' +
          '<img src="data:image/png;base64,' + r.shot + '" style="width:220px;height:220px;border:1px solid #e3e6ef;border-radius:8px">' +
          '<div style="color:#888;font-size:12.5px;margin-top:8px">打开粉笔 APP → 扫一扫，确认登录</div>' +
          '<div style="color:#bbb;font-size:11px;margin-top:4px">二维码 5 分钟内有效，过期请重新发起</div>' +
        '</div>';
      return;
    }
    if (state === 'need_drag' && r.shot) {
      area.innerHTML =
        '<div style="text-align:center">' +
          '<img src="data:image/png;base64,' + r.shot + '" style="width:100%;max-width:320px;border:1px solid #e3e6ef;border-radius:8px">' +
          '<div style="color:#666;font-size:12.5px;margin-top:8px">检测到安全验证：请拖动下方滑条到图中缺口位置</div>' +
          '<input id="c10Drag" type="range" min="40" max="560" value="260" style="width:100%;margin-top:8px">' +
          '<div style="color:#999;font-size:11px" id="c10DragVal">滑动距离：260 px</div>' +
          '<button onclick="C10.submitDrag()" style="margin-top:8px;padding:8px 22px;border:none;border-radius:8px;background:#4a6cf7;color:#fff;font-size:13px;cursor:pointer">确认</button>' +
        '</div>';
      var rg = document.getElementById('c10Drag');
      rg.oninput = function () {
        var v = document.getElementById('c10DragVal');
        if (v) v.textContent = '滑动距离：' + rg.value + ' px';
      };
      return;
    }
    if (state === 'need_code') {
      area.innerHTML =
        '<div style="text-align:center">' +
          '<div style="color:#666;font-size:12.5px">验证码已发送至 <b>' + escapeHtml(r.phone || '') + '</b>，请查收短信</div>' +
          '<input id="c10Code" type="text" inputmode="numeric" maxlength="6" placeholder="6 位验证码" ' +
            'style="width:180px;text-align:center;font-size:20px;letter-spacing:6px;border:1px solid #d9dce6;border-radius:8px;padding:10px;margin-top:12px">' +
          '<div><button onclick="C10.submitCode()" style="margin-top:12px;padding:9px 26px;border:none;border-radius:8px;background:#4a6cf7;color:#fff;font-size:13px;cursor:pointer">提交验证码</button></div>' +
        '</div>';
      return;
    }
    if (state === 'submitting' || state === 'init' || state === 'sending') {
      area.innerHTML = '<p style="color:#999;text-align:center;padding:20px 0">' + escapeHtml(r.hint || '处理中…') + '</p>';
    }
  }

  function submitCode() {
    var el = document.getElementById('c10Code');
    var code = (el && el.value || '').trim();
    if (!code) { showToast('请输入验证码', 'error'); return; }
    global.GK.api('/me/fenbi/login/code', { method: 'POST', body: { sid: _curSid, code: code } })
      .catch(function (e) { showToast((e && e.message) || '提交失败', 'error'); });
  }

  function submitDrag() {
    var el = document.getElementById('c10Drag');
    var dist = el ? parseInt(el.value, 10) : 0;
    global.GK.api('/me/fenbi/login/drag', { method: 'POST', body: { sid: _curSid, dist: dist } })
      .catch(function (e) { showToast((e && e.message) || '提交失败', 'error'); });
  }

  // ---------------- 挂载 ----------------
  global.C10 = {
    openFenbi: openFenbi,
    refresh: refresh,
    close: close,
    startCookie: startCookie,
    submitCookie: submitCookie,
    startSms: startSms,
    startQrcode: startQrcode,
    submitCode: submitCode,
    submitDrag: submitDrag,
    analyze: analyze,
    regen: regen,
    unbind: unbind,
    printPlan: printPlan,
    docxPlan: docxPlan
  };
  global.GK.c10Ready = true;

})(typeof window !== 'undefined' ? window : globalThis);

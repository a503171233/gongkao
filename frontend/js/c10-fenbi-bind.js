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
  var _syncTimer = null;   // 三同步进度轮询（批次27）
  var _curSid = null;      // 当前登录会话
  var _curMode = null;     // sms / qrcode / cookie

  // ---------------- 工具 ----------------
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // 批次28：题目富文本——escapeHtml 后把 ![图N](/api/qimg/…) 标记还原为
  // <img>（URL 白名单 /api/qimg/，其余降级占位文字）；[图N] 占位 → 提示。
  function richBody(raw, maxLen) {
    var s = escapeHtml(maxLen ? String(raw || '').slice(0, maxLen) : raw);
    s = s.replace(/!\[图\d*\]\((\/api\/qimg\/[A-Za-z0-9_\-\/.]+)\)/g,
      '<img src="$1" class="fb-qimg" loading="lazy" alt="题图">');
    s = s.replace(/!\[图\d*\]\([^)]*\)/g,
      '<span style="color:var(--ink-3)">[图片加载失败]</span>');
    s = s.replace(/\[图\d*\]/g,
      '<span style="color:var(--ink-3)">🖼</span>');
    return s;
  }

  // 批次28：资料（材料）区块——资料分析题的图表/文字材料
  function materialBlock(material) {
    if (!material || !String(material).trim()) return '';
    return '<div style="background:var(--paper-2);border-radius:9px;padding:9px 11px;margin-bottom:8px">' +
      '<div style="font-size:11px;font-weight:700;color:var(--wood);margin-bottom:3px">📋 资料</div>' +
      '<div style="font-size:12px;line-height:1.6;color:var(--ink-2)">' + richBody(material) + '</div></div>';
  }

  function showToast(msg, type) {
    var div = document.createElement('div');
    div.style.cssText = 'position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:8px;color:#fff;font-size:14px;z-index:9999;box-shadow:0 2px 12px rgba(0,0,0,.15);';
    div.style.background = type === 'error' ? 'var(--bad)' : (type === 'success' ? 'var(--good)' : 'var(--wood)');
    div.textContent = msg;
    document.body.appendChild(div);
    setTimeout(function () { div.remove(); }, 3000);
  }

  function requireLogin() {
    var uid = (global.GK.store && global.GK.store.userId) || 'anonymous';
    if (uid === 'anonymous') {
      global.GK.promptLogin('提升计划');
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
        'background:var(--card);box-shadow:-2px 0 14px rgba(93,72,41,.16);z-index:200;' +
        'display:flex;flex-direction:column;font-family:inherit;';
      document.body.appendChild(p);
    }
    return p;
  }

  function stopTimers() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
    if (_loginTimer) { clearInterval(_loginTimer); _loginTimer = null; }
    if (_syncTimer) { clearTimeout(_syncTimer); _syncTimer = null; }
  }

  function close() {
    stopTimers();
    var p = document.getElementById('c10Panel');
    if (p) p.remove();
  }

  function btn(text, onclick, style) {
    return '<button onclick="' + onclick + '" style="padding:5px 12px;border:1px solid var(--wood);color:var(--wood);' +
      'border-radius:6px;background:#fff;cursor:pointer;font-size:12px;' + (style || '') + '">' + text + '</button>';
  }

  function card(icon, title) {
    return '<div style="font-size:13px;font-weight:700;color:var(--ink);margin:16px 0 8px;display:flex;align-items:center;gap:6px">' +
      '<span>' + icon + '</span><span>' + title + '</span></div>';
  }

  // ---------------- 状态页 ----------------
  function openFenbi() {
    if (!requireLogin()) return;
    var panel = ensurePanel();
    panel.innerHTML =
      '<div style="display:flex;align-items:center;gap:8px;padding:14px 16px;border-bottom:1px solid var(--line)">' +
        '<span style="font-size:16px;font-weight:700">🎯 粉笔提升计划</span>' +
        '<span style="flex:1"></span>' +
        '<button id="c10ExportPdf" onclick="C10.printPlan()" style="padding:5px 10px;border:1px solid var(--wood);color:var(--wood);border-radius:6px;background:#fff;cursor:pointer;font-size:12px;display:none">🖨 导出 PDF</button>' +
        '<button id="c10ExportDocx" onclick="C10.docxPlan()" style="padding:5px 10px;border:1px solid var(--wood);color:var(--wood);border-radius:6px;background:#fff;cursor:pointer;font-size:12px;display:none">📄 导出 DOCX</button>' +
        '<button onclick="C10.close()" style="padding:5px 10px;border:none;border-radius:6px;background:var(--paper-2);cursor:pointer;font-size:12px">✕</button>' +
      '</div>' +
      '<div id="c10Body" style="flex:1;overflow-y:auto;padding:14px 16px;color:var(--ink);font-size:13.5px;line-height:1.6">' +
        '<p style="color:var(--ink-3);text-align:center">加载中…</p>' +
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
      if (body) body.innerHTML = '<p style="color:var(--bad);text-align:center;padding:40px 0">加载失败: ' + escapeHtml(e && e.message ? e.message : e) + '</p>';
    });
  }

  // ---------------- 入口：三种绑定方式 ----------------
  function renderEntry() {
    var body = document.getElementById('c10Body');
    body.innerHTML =
      '<div style="background:linear-gradient(135deg,var(--wood),var(--gold));color:#fff;border-radius:10px;padding:14px 16px">' +
        '<div style="font-size:14.5px;font-weight:700">绑定粉笔账号，生成专属提升计划</div>' +
        '<div style="font-size:12px;opacity:.9;margin-top:4px">自动同步你的粉笔错题，AI 诊断薄弱点并定制 4 周提分任务书</div>' +
      '</div>' +
      '<div style="display:flex;flex-direction:column;gap:10px;margin-top:14px">' +
        '<div onclick="C10.startPwd()" style="border:1px solid var(--line-2);border-radius:10px;padding:12px 14px;cursor:pointer">' +
          '<div style="font-weight:700;font-size:13.5px">🔑 方式一：账号密码登录（最直接）</div>' +
          '<div style="color:var(--ink-3);font-size:12px;margin-top:3px">输入粉笔手机号/邮箱和密码，直接登录；密码仅用于本次登录，不保存</div>' +
        '</div>' +
        '<div onclick="C10.startCookie()" style="border:1px solid var(--line-2);border-radius:10px;padding:12px 14px;cursor:pointer">' +
          '<div style="font-weight:700;font-size:13.5px">📋 方式二：粘贴 Cookie（免密码）</div>' +
          '<div style="color:var(--ink-3);font-size:12px;margin-top:3px">电脑浏览器登录粉笔网页版 → 复制 Cookie 粘贴即可，1 分钟完成</div>' +
        '</div>' +
        '<div onclick="C10.startSms()" style="border:1px solid var(--line-2);border-radius:10px;padding:12px 14px;cursor:pointer">' +
          '<div style="font-weight:700;font-size:13.5px">📱 方式三：手机验证码登录</div>' +
          '<div style="color:var(--ink-3);font-size:12px;margin-top:3px">输入粉笔手机号，接收短信验证码完成登录</div>' +
        '</div>' +
        '<div onclick="C10.startQrcode()" style="border:1px solid var(--line-2);border-radius:10px;padding:12px 14px;cursor:pointer">' +
          '<div style="font-weight:700;font-size:13.5px">🔷 方式四：粉笔 APP 扫码登录</div>' +
          '<div style="color:var(--ink-3);font-size:12px;margin-top:3px">打开粉笔 APP 扫一扫，账号密码都不经过本平台</div>' +
        '</div>' +
      '</div>' +
      '<div style="color:var(--ink-3);font-size:11.5px;margin-top:14px;line-height:1.7">· 仅采集错题数据用于分析，题目原文只保存在你的账号下<br>· 随时可解除绑定；账密登录的密码用后即弃，本平台不存储</div>';
  }

  // ---------------- Cookie 绑定 ----------------
  function startCookie() {
    _curMode = 'cookie';
    var body = document.getElementById('c10Body');
    body.innerHTML =
      '<div style="font-weight:700;font-size:14px;margin-bottom:8px">📋 Cookie 粘贴绑定</div>' +
      '<div style="background:var(--paper-2);border-radius:8px;padding:10px 12px;font-size:12px;color:var(--ink-2);line-height:1.8">' +
        '1. 电脑 Chrome 打开 <b>www.fenbi.com</b> 并登录<br>' +
        '2. 按 <b>F12</b> 打开开发者工具 → Console（控制台）<br>' +
        '3. 输入 <code style="background:var(--paper-2);padding:1px 5px;border-radius:4px">document.cookie</code> 回车<br>' +
        '4. 复制输出的整段文字粘贴到下方' +
      '</div>' +
      '<textarea id="c10Cookie" placeholder="粘贴 document.cookie 的输出内容…" style="width:100%;box-sizing:border-box;height:110px;margin-top:10px;border:1px solid var(--line-2);border-radius:8px;padding:10px;font-size:12px;font-family:monospace;resize:vertical"></textarea>' +
      '<div style="display:flex;gap:8px;margin-top:10px">' +
        '<button id="c10CookieBtn" onclick="C10.submitCookie()" style="flex:1;padding:10px;border:none;border-radius:8px;background:var(--wood);color:#fff;font-size:13.5px;cursor:pointer">校验并绑定</button>' +
        btn('← 返回', 'C10.refresh()', 'border-color:var(--line-2);color:var(--ink-2)') +
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
      '<div style="background:#edf2e6;border:1px solid #e3ecda;border-radius:10px;padding:12px 14px;display:flex;align-items:center;gap:10px">' +
        '<span style="font-size:22px">✅</span>' +
        '<div style="flex:1">' +
          '<div style="font-weight:700;font-size:13.5px">粉笔账号已绑定' + (b.phone ? '（' + escapeHtml(b.phone) + '）' : '') + '</div>' +
          '<div style="color:var(--ink-3);font-size:12px;margin-top:2px">绑定时间 ' + escapeHtml(String(b.bound_at || '').slice(0, 16)) +
          (lastCheck ? ' · 最近校验 ' + escapeHtml(lastCheck) : '') + '</div>' +
        '</div>' +
        '<button onclick="C10.unbind()" style="padding:4px 10px;border:1px solid var(--bad);color:var(--bad);border-radius:6px;background:#fff;cursor:pointer;font-size:11.5px">解绑</button>' +
      '</div>' +
      (latest && latest.status === 'failed'
        ? '<div style="background:#fff5f5;border:1px solid #ffc9c9;border-radius:8px;padding:10px 12px;margin-top:10px;font-size:12.5px;color:#c92a2a">上次生成失败：' + escapeHtml(latest.error || '未知错误') + '</div>'
        : '') +
      renderSyncCards(b) +
      '<div style="text-align:center;margin-top:16px">' +
        '<button onclick="C10.analyze()" style="padding:12px 34px;border:none;border-radius:10px;background:var(--wood);color:#fff;font-size:15px;font-weight:700;cursor:pointer;box-shadow:0 4px 14px rgba(139,94,60,.35)">🚀 生成我的提升计划</button>' +
        '<div style="color:var(--ink-3);font-size:11.5px;margin-top:8px">采集错题 → AI 诊断 → 定制 4 周任务书，约需 1~3 分钟</div>' +
      '</div>';
    if (latest && latest.status === 'done' && latest.report) {
      body.innerHTML += '<div id="c10PlanLegacy">' + renderPlanInner(latest) + '</div>';
      showExport(true);
    } else {
      showExport(false);
    }
  }

  // ---------------- 批次27 · 三大数据同步 ----------------
  var SYNC_DEFS = {
    wrong:   { icon: '📕', name: '错题同步', desc: '粉笔错题本 → 本系统错题库' },
    collect: { icon: '⭐', name: '收藏同步', desc: '粉笔收藏题 → 本系统收藏夹' },
    mock:    { icon: '📝', name: '模考同步', desc: '试卷题目 + 错题 + 整卷分析' }
  };

  function syncCount(b, kind) {
    if (!b) return 0;
    if (kind === 'wrong') return b.wrong_count || 0;
    if (kind === 'collect') return b.collect_count || 0;
    return b.mock_count || 0;
  }

  function renderSyncCards(b) {
    var h = '<div style="font-size:13px;font-weight:700;color:var(--ink);margin:16px 0 8px">🔄 数据同步中心</div>';
    h += '<div style="display:flex;flex-direction:column;gap:8px" id="c10SyncCards">';
    Object.keys(SYNC_DEFS).forEach(function (kind) {
      var def = SYNC_DEFS[kind];
      var n = syncCount(b, kind);
      h +=
        '<div style="border:1px solid var(--line-2);border-radius:10px;padding:10px 12px;display:flex;align-items:center;gap:10px">' +
          '<span style="font-size:20px">' + def.icon + '</span>' +
          '<div style="flex:1;cursor:pointer" onclick="C10.syncCenter(\'' + kind + '\')">' +
            '<div style="font-weight:700;font-size:13px">' + def.name +
              '<span style="margin-left:6px;color:var(--wood);font-size:12px">' + n + ' 条</span></div>' +
            '<div style="color:var(--ink-3);font-size:11.5px;margin-top:2px">' + def.desc + '</div>' +
          '</div>' +
          '<div id="c10SyncBtn_' + kind + '">' +
            '<button onclick="C10.startSync(\'' + kind + '\')" style="padding:5px 12px;border:none;border-radius:7px;background:var(--wood);color:#fff;font-size:12px;cursor:pointer">同步</button>' +
          '</div>' +
        '</div>';
    });
    h += '</div>';
    h += '<div id="c10SyncMsg" style="font-size:12px;color:var(--ink-3);margin-top:6px">增量同步：只拉取上次之后的新数据，随时可中断离开</div>';
    return h;
  }

  function setSyncBtn(kind, html) {
    var el = document.getElementById('c10SyncBtn_' + kind);
    if (el) el.innerHTML = html;
  }

  function startSync(kind) {
    var def = SYNC_DEFS[kind];
    setSyncBtn(kind, '<span style="color:var(--ink-3);font-size:12px">同步中…</span>');
    global.GK.api('/me/fenbi/sync', { method: 'POST', body: { kind: kind } })
      .then(function () { pollSync(kind, null); })
      .catch(function (e) {
        showToast((e && e.message) || '触发失败', 'error');
        setSyncBtn(kind, '<button onclick="C10.startSync(\'' + kind + '\')" style="padding:5px 12px;border:none;border-radius:7px;background:var(--wood);color:#fff;font-size:12px;cursor:pointer">重试</button>');
      });
  }

  function pollSync(kind, prev) {
    if (_syncTimer) { clearTimeout(_syncTimer); _syncTimer = null; }
    global.GK.api('/me/fenbi/sync/status?kind=' + kind).then(function (d) {
      var lat = d.latest;
      if (!lat || lat.status === 'running' || lat.status !== prev) {
        var el = document.getElementById('c10SyncBtn_' + kind);
        if (el && lat) {
          el.innerHTML = '<div style="text-align:right"><div style="color:var(--wood);font-size:11px;font-weight:700">' +
            (lat.progress || 0) + '%</div><div style="color:var(--ink-3);font-size:10.5px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
            escapeHtml(lat.stage || '') + '</div></div>';
        }
        _syncTimer = setTimeout(function () { pollSync(kind, lat ? lat.status : null); }, 2000);
        return;
      }
      if (lat.status === 'done') {
        var def = SYNC_DEFS[kind];
        showToast(def.name + '完成：共 ' + (lat.total || 0) + '，新增 ' + (lat.new_count || 0), 'success');
        setSyncBtn(kind, '<button onclick="C10.startSync(\'' + kind + '\')" style="padding:5px 12px;border:none;border-radius:7px;background:var(--wood);color:#fff;font-size:12px;cursor:pointer">再同步</button>');
        refresh();
      } else {
        showToast(def.name + '失败：' + (lat.error || '未知错误'), 'error');
        setSyncBtn(kind, '<button onclick="C10.startSync(\'' + kind + '\')" style="padding:5px 12px;border:1px solid var(--bad);border-radius:7px;background:#fff;color:var(--bad);font-size:12px;cursor:pointer">重试</button>');
      }
    }).catch(function () {
      _syncTimer = setTimeout(function () { pollSync(kind, prev); }, 3000);
    });
  }

  function syncCenter(tab) {
    tab = tab || 'mock';
    var body = document.getElementById('c10Body');
    if (!body) return;
    var tabs = [
      ['wrong', '📕 错题本'], ['collect', '⭐ 收藏'], ['mock', '📝 模考报告']
    ];
    var h = '<div onclick="C10.refresh()" style="color:var(--wood);font-size:12.5px;cursor:pointer;margin-bottom:10px">← 返回</div>';
    h += '<div style="display:flex;gap:6px;margin-bottom:12px">';
    tabs.forEach(function (t) {
      var on = t[0] === tab;
      h += '<div onclick="C10.syncCenter(\'' + t[0] + '\')" style="flex:1;text-align:center;padding:7px 0;border-radius:8px;font-size:12.5px;cursor:pointer;' +
        (on ? 'background:var(--wood);color:#fff;font-weight:700' : 'background:var(--paper-2);color:var(--ink-2)') + '">' + t[1] + '</div>';
    });
    h += '</div><div id="c10SyncList"><p style="color:var(--ink-3);text-align:center">加载中…</p></div>';
    body.innerHTML = h;
    if (tab === 'mock') { loadMockList(); return; }
    var url = tab === 'wrong' ? '/me/fenbi/wrongs?limit=30' : '/me/fenbi/collects?limit=30';
    global.GK.api(url).then(function (d) {
      var el = document.getElementById('c10SyncList');
      if (!el) return;
      if (!d.items || !d.items.length) {
        el.innerHTML = '<p style="color:var(--ink-3);text-align:center;padding:30px 0">还没有数据，请先返回点击「同步」</p>';
        return;
      }
      var hh = '<div style="color:var(--ink-3);font-size:12px;margin-bottom:8px">共 ' + d.total + ' 条</div>';
      hh += '<div style="display:flex;flex-direction:column;gap:8px">';
      d.items.forEach(function (it) {
        hh +=
          '<div style="border:1px solid var(--line-2);border-radius:9px;padding:10px 12px">' +
            '<div style="display:flex;gap:6px;align-items:center;margin-bottom:4px">' +
              (it.module ? '<span style="background:var(--paper-2);color:var(--ink-2);border-radius:5px;padding:1px 7px;font-size:10.5px">' + escapeHtml(it.module) + '</span>' : '') +
              (it.keypoint ? '<span style="color:var(--wood);font-size:10.5px">' + escapeHtml(it.keypoint) + '</span>' : '') +
              '<span style="flex:1"></span><span style="color:var(--ink-3);font-size:10.5px">难度 ' + (it.difficulty || '-') + '</span>' +
            '</div>' +
            '<div style="font-size:12.5px;line-height:1.55;color:var(--ink)">' + escapeHtml(String(it.content || '').slice(0, 90)) + '…</div>' +
            '<div style="color:var(--good);font-size:11.5px;margin-top:5px">答案：' + escapeHtml(it.answer || '-') + '</div>' +
            (it.analysis ? '<details style="margin-top:5px"><summary style="cursor:pointer;color:var(--wood);font-size:11.5px">查看解析</summary>' +
              '<div style="font-size:12px;color:var(--ink-2);line-height:1.6;margin-top:4px">' + escapeHtml(String(it.analysis).slice(0, 300)) + '…</div></details>' : '') +
          '</div>';
      });
      hh += '</div>';
      el.innerHTML = hh;
    }).catch(function (e) {
      var el = document.getElementById('c10SyncList');
      if (el) el.innerHTML = '<p style="color:var(--bad);text-align:center">加载失败：' + escapeHtml(e && e.message ? e.message : e) + '</p>';
    });
  }

  function loadMockList() {
    global.GK.api('/me/fenbi/mock/exams').then(function (d) {
      var el = document.getElementById('c10SyncList');
      if (!el) return;
      var exams = d.exams || [];
      if (!exams.length) {
        el.innerHTML = '<p style="color:var(--ink-3);text-align:center;padding:30px 0">还没有模考数据，请先返回点击「模考同步」</p>';
        return;
      }
      var h = '<div style="color:var(--ink-3);font-size:12px;margin-bottom:8px">共 ' + exams.length + ' 份</div>';
      h += '<div style="display:flex;flex-direction:column;gap:8px">';
      exams.forEach(function (e) {
        var isMock = e.sheet_type === 1;
        var scoreTxt = isMock && e.fullmark
          ? '<span style="color:var(--wood);font-weight:700;font-size:15px">' + e.score + '</span><span style="color:var(--ink-3);font-size:11px">/' + e.fullmark + ' 分</span>'
          : '<span style="color:var(--ink-3);font-size:11.5px">练习</span>';
        h +=
          '<div onclick="C10.mockDetail(' + e.id + ')" style="border:1px solid var(--line-2);border-radius:9px;padding:10px 12px;cursor:pointer">' +
            '<div style="display:flex;align-items:center;gap:8px">' +
              '<span style="font-size:12.5px;font-weight:700;color:var(--ink);flex:1">' + escapeHtml(e.name) + '</span>' + scoreTxt +
            '</div>' +
            '<div style="color:var(--ink-3);font-size:11.5px;margin-top:4px">' +
              '共 ' + e.question_count + ' 题 · ' +
              '<span style="color:var(--good)">对 ' + e.correct_count + '</span> · ' +
              '<span style="color:var(--bad)">错 ' + e.wrong_count + '</span> · 未答 ' + e.unanswered +
              (e.submit_time ? ' · ' + escapeHtml(String(e.submit_time).slice(0, 16)) : '') +
            '</div>' +
          '</div>';
      });
      h += '</div>';
      el.innerHTML = h;
    }).catch(function (e) {
      var el = document.getElementById('c10SyncList');
      if (el) el.innerHTML = '<p style="color:var(--bad);text-align:center">加载失败：' + escapeHtml(e && e.message ? e.message : e) + '</p>';
    });
  }

  function mockDetail(eid) {
    global.GK.api('/me/fenbi/mock/exams/' + eid).then(function (d) {
      var body = document.getElementById('c10Body');
      if (!body) return;
      var rep = d.report || {};
      var basic = {};
      var courses = [];
      (rep.subReports || []).forEach(function (s) {
        if (s && s.score != null && s.fullmark != null && !basic.name) basic = s;
        if (s && s.courseReports) courses = s.courseReports;
      });
      var answers = d.answers || [];
      var wrongs = answers.filter(function (a) { return a.status === -1; });
      var h = '<div onclick="C10.syncCenter(\'mock\')" style="color:var(--wood);font-size:12.5px;cursor:pointer;margin-bottom:10px">← 返回列表</div>';
      h += '<div style="background:linear-gradient(135deg,var(--wood),var(--gold));color:#fff;border-radius:10px;padding:14px 16px">' +
        '<div style="font-size:14.5px;font-weight:700">' + escapeHtml(d.name || '') + '</div>';
      if (d.fullmark) {
        h += '<div style="margin-top:6px;font-size:13px">得分 <span style="font-size:22px;font-weight:800">' + d.score + '</span> / ' + d.fullmark +
          '<span style="opacity:.85;font-size:12px;margin-left:10px">难度 ' + (d.difficulty || '-') + '</span></div>';
      }
      if (d.submit_time) h += '<div style="opacity:.85;font-size:11.5px;margin-top:4px">提交于 ' + escapeHtml(String(d.submit_time).slice(0, 16)) + '</div>';
      h += '</div>';
      // 总览
      h += '<div style="display:flex;gap:8px;margin-top:10px">' +
        '<div style="flex:1;text-align:center;background:#f0f7ec;border-radius:8px;padding:8px 0"><div style="color:var(--good);font-weight:800;font-size:16px">' + d.correct_count + '</div><div style="color:var(--ink-3);font-size:10.5px">答对</div></div>' +
        '<div style="flex:1;text-align:center;background:#fdf0f0;border-radius:8px;padding:8px 0"><div style="color:var(--bad);font-weight:800;font-size:16px">' + d.wrong_count + '</div><div style="color:var(--ink-3);font-size:10.5px">答错</div></div>' +
        '<div style="flex:1;text-align:center;background:var(--paper-2);border-radius:8px;padding:8px 0"><div style="color:var(--ink-2);font-weight:800;font-size:16px">' + d.unanswered + '</div><div style="color:var(--ink-3);font-size:10.5px">未答</div></div>' +
        '</div>';
      // 分模块统计（整卷分析）
      if (courses.length) {
        h += '<div style="font-size:13px;font-weight:700;margin:14px 0 8px">📊 整卷分析</div>';
        courses.forEach(function (c) {
          var cs = c.courseStat || {};
          var rate = cs.questionCount ? Math.round((cs.correctCount || 0) * 100 / cs.questionCount) : 0;
          h += '<div style="margin-bottom:8px">' +
            '<div style="display:flex;font-size:12px;align-items:center;gap:8px">' +
              '<span style="width:70px;color:var(--ink-2)">' + escapeHtml(c.courseName || '') + '</span>' +
              '<div style="flex:1;height:8px;background:var(--line);border-radius:4px;overflow:hidden">' +
                '<div style="height:100%;width:' + rate + '%;background:' + (rate >= 60 ? 'var(--good)' : 'var(--bad)') + ';border-radius:4px"></div></div>' +
              '<span style="color:var(--ink-3);font-size:11px;width:88px;text-align:right">对 ' + (cs.correctCount || 0) + '/' + (cs.questionCount || 0) + ' · ' + rate + '%</span>' +
            '</div></div>';
        });
      }
      // 错题列表
      h += '<div style="font-size:13px;font-weight:700;margin:14px 0 8px">❌ 错题（' + wrongs.length + '）</div>';
      if (!wrongs.length) {
        h += '<p style="color:var(--ink-3);font-size:12px">本次没有错题（或未作答）</p>';
      }
      wrongs.forEach(function (a, i) {
        h +=
          '<div style="border:1px solid #ffc9c9;background:#fffafa;border-radius:9px;padding:10px 12px;margin-bottom:8px">' +
            '<div style="font-size:10.5px;color:var(--bad);font-weight:700;margin-bottom:3px">错题 ' + (i + 1) + '（ID ' + escapeHtml(a.question_id) + '）</div>' +
            '<div style="font-size:12.5px;line-height:1.55">' + escapeHtml(String(a.content || '').slice(0, 120)) + '…</div>' +
            (a.user_answer && a.user_answer !== 'null' ? '<div style="font-size:11.5px;color:var(--bad);margin-top:4px">我的答案：' + escapeHtml(fmtAnswer(a.user_answer)) + '</div>' : '') +
            '<div style="font-size:11.5px;color:var(--good);margin-top:2px">正确答案：' + escapeHtml(fmtAnswer(a.correct_answer)) + '</div>' +
            (a.analysis ? '<details style="margin-top:5px"><summary style="cursor:pointer;color:var(--wood);font-size:11.5px">查看解析</summary>' +
              '<div style="font-size:12px;color:var(--ink-2);line-height:1.6;margin-top:4px">' + escapeHtml(String(a.analysis).slice(0, 300)) + '…</div></details>' : '') +
          '</div>';
      });
      // 全部题目（折叠）
      h += '<details style="margin-top:10px"><summary style="cursor:pointer;color:var(--wood);font-size:12.5px;font-weight:700">📄 全部 ' + answers.length + ' 题作答明细</summary><div style="margin-top:8px">';
      answers.forEach(function (a) {
        var mark = a.status === 1 ? '<span style="color:var(--good)">✔</span>'
          : (a.status === -1 ? '<span style="color:var(--bad)">✘</span>' : '<span style="color:var(--ink-3)">—</span>');
        h += '<div style="display:flex;gap:6px;font-size:11.5px;color:var(--ink-2);padding:3px 0;border-bottom:1px dashed var(--line)">' +
          '<span style="width:16px">' + mark + '</span>' +
          '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escapeHtml(String(a.content || '').slice(0, 50)) + '</span></div>';
      });
      h += '</div></details>';
      body.innerHTML = h;
      body.scrollTop = 0;
    }).catch(function (e) {
      showToast((e && e.message) || '加载失败', 'error');
    });
  }

  function fmtAnswer(jsonStr) {
    try {
      var o = JSON.parse(jsonStr);
      if (o == null) return '-';
      if (typeof o.choice === 'string') {
        return o.choice.split('').map(function (c) {
          return String.fromCharCode(65 + parseInt(c, 10));
        }).join(' ');
      }
      if (Array.isArray(o.choice)) {
        return o.choice.map(function (c) {
          return String.fromCharCode(65 + parseInt(c, 10));
        }).join(' ');
      }
      return JSON.stringify(o);
    } catch (e) { return String(jsonStr); }
  }

  function renderExpired(b) {
    var body = document.getElementById('c10Body');
    body.innerHTML =
      '<div style="background:#fff8e6;border:1px solid #ffe08a;border-radius:10px;padding:14px">' +
        '<div style="font-weight:700;font-size:13.5px">⚠️ 粉笔登录态已失效</div>' +
        '<div style="color:var(--ink-3);font-size:12.5px;margin-top:4px">请重新绑定以继续采集错题（历史任务书仍可查看）</div>' +
      '</div>' +
      '<div style="display:flex;gap:8px;margin-top:12px">' +
        '<button onclick="C10.startCookie()" style="flex:1;padding:10px;border:none;border-radius:8px;background:var(--wood);color:#fff;font-size:13px;cursor:pointer">重新绑定（Cookie）</button>' +
        '<button onclick="C10.startSms()" style="flex:1;padding:10px;border:1px solid var(--line-2);border-radius:8px;background:#fff;color:var(--ink);font-size:13px;cursor:pointer">验证码登录</button>' +
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
        '<div style="color:var(--ink-3);font-size:12.5px;margin-top:6px" id="c10Stage">' + escapeHtml(latest.stage || '准备中') + '</div>' +
        '<div style="height:8px;background:var(--line);border-radius:5px;overflow:hidden;margin:18px 30px 0">' +
          '<div id="c10Bar" style="height:100%;width:' + p + '%;background:linear-gradient(90deg,var(--wood),var(--gold));border-radius:5px;transition:width .6s"></div>' +
        '</div>' +
        '<div id="c10Pct" style="color:var(--wood);font-size:13px;font-weight:700;margin-top:8px">' + p + '%</div>' +
        '<div style="color:#bbb;font-size:11.5px;margin-top:14px">错题越多耗时越长，可离开页面稍后回来查看</div>' +
      '</div>';
  }

  function startPolling() {
    stopTimers();
    // 27-N P1-5：超时兜底——running 超 9 分钟（后端 8 分钟僵死线 + 轮询间隔余量）
    // 停止轮询并给出明确提示，不再无限停在 70%
    var startedAt = Date.now();
    var timedOut = false;
    _pollTimer = setInterval(function () {
      if (!timedOut && Date.now() - startedAt > 9 * 60 * 1000) {
        timedOut = true;
        stopTimers();
        var body = document.getElementById('c10Body');
        if (body) body.innerHTML =
          '<div style="text-align:center;padding:36px 12px">' +
            '<div style="font-size:36px">⏳</div>' +
            '<div style="font-weight:700;font-size:15px;margin-top:10px">生成时间超出预期</div>' +
            '<div style="color:var(--ink-3);font-size:12.5px;margin-top:6px">任务可能被服务端中断，稍等片刻后可重新生成</div>' +
            '<button onclick="C10.analyze()" style="margin-top:14px;padding:10px 28px;border:none;border-radius:10px;background:var(--wood);color:#fff;font-size:14px;font-weight:700;cursor:pointer">重新生成</button>' +
          '</div>';
        return;
      }
      if (timedOut) return;
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
    return lv >= 4 ? 'var(--bad)' : (lv >= 3 ? '#f59f00' : 'var(--wood)');
  }

  function renderPlanInner(latest) {
    var rep = latest.report || {};
    var dist = latest.distribution || [];
    var h = '';
    // 批次27-E：AI 降级横幅——done 但带 error 说明是本地统计兜底版
    if (latest.error) {
      h += '<div style="background:rgba(245,159,0,.12);border:1px solid rgba(245,159,0,.4);border-radius:10px;padding:9px 13px;margin:14px 0 0;font-size:12px;color:#8a6d1f;line-height:1.7">' +
        '⚠️ <b>基础统计版任务书</b>：AI 服务暂时不可用，当前展示本地统计的诊断结果。服务恢复后可点击下方「重新生成」获取 AI 完整版。' +
        '<div style="color:var(--ink-3);font-size:11px;margin-top:3px">' + escapeHtml(latest.error) + '</div></div>';
    }
    h += '<div style="border-top:1px dashed var(--line-2);margin:18px 0 4px"></div>';
    h += '<div style="text-align:center;margin-top:10px">' +
      '<div style="font-size:16px;font-weight:800;color:var(--ink)">学员专属提升计划任务书</div>' +
      '<div style="color:var(--ink-3);font-size:11.5px;margin-top:3px">生成于 ' + escapeHtml(String(latest.done_at || '').slice(0, 16)) +
      ' · 共采集错题 ' + (latest.total_mistakes || 0) + ' 道' + '</div></div>';

    // 总体诊断
    h += card('📋', '总体诊断');
    h += '<div style="background:var(--paper-2);border-radius:8px;padding:11px 13px;font-size:13px;line-height:1.8">' + escapeHtml(rep.summary || '') + '</div>';

    // 错题分布
    if (dist.length) {
      h += card('📊', '错题模块分布');
      var maxT = Math.max.apply(null, dist.map(function (x) { return x.total || 0; }).concat([1]));
      h += dist.map(function (m) {
        return '<div style="margin:7px 0">' +
          '<div style="display:flex;font-size:12px;justify-content:space-between"><span>' + escapeHtml(m.module) + '</span><span style="color:var(--ink-3)">' + m.total + ' 题</span></div>' +
          '<div style="height:7px;background:var(--line);border-radius:4px;overflow:hidden;margin-top:3px"><div style="height:100%;width:' + Math.round(100 * (m.total || 0) / maxT) + '%;background:var(--wood);border-radius:4px"></div></div>' +
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
            '<span style="color:var(--ink-3);font-size:11px">' + escapeHtml(w.module || '') + '</span>' +
            '<span style="flex:1"></span>' +
            '<span style="color:' + levelColor(lv) + ';font-size:11px;font-weight:700">' + '★'.repeat(lv) + '</span>' +
          '</div>' +
          '<div style="height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:4px"><div style="height:100%;width:' + (lv * 20) + '%;background:' + levelColor(lv) + ';border-radius:3px"></div></div>' +
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

    // 批次27-F：模考时间分配建议（time_plan 为可选键，旧报告/无模考数据不渲染）
    var tpn = rep.time_plan || [];
    if (tpn.length) {
      h += card('⏱', '模考时间分配');
      h += tpn.map(function (t) {
        return '<div style="margin:9px 0;padding:9px 11px;background:var(--paper-2);border-radius:8px;border-left:3px solid var(--gold)">' +
          '<div style="font-size:12.5px;font-weight:700">' + escapeHtml(t.module || '') + '</div>' +
          '<div style="font-size:11.5px;color:#a05a1f;margin-top:2px">⚠ ' + escapeHtml(t.issue || '') + '</div>' +
          '<div style="font-size:12px;line-height:1.7;margin-top:3px">' + escapeHtml(t.suggestion || '') + '</div>' +
          '</div>';
      }).join('');
    }

    // 4 周计划
    var weeks = rep.weeks || [];
    if (weeks.length) {
      h += card('📅', '四周提升计划');
      h += weeks.map(function (w, i) {
        var tasks = w.tasks || [];
        return '<div style="border:1px solid var(--line-2);border-radius:10px;padding:11px 13px;margin:10px 0">' +
          '<div style="display:flex;align-items:center;gap:8px">' +
            '<span style="background:var(--wood);color:#fff;font-size:11px;padding:2px 8px;border-radius:10px">第 ' + (i + 1) + ' 周</span>' +
            '<span style="font-weight:700;font-size:13px">' + escapeHtml(w.theme || '') + '</span>' +
          '</div>' +
          '<div style="color:var(--ink-3);font-size:11.5px;margin-top:4px">🎯 ' + escapeHtml(w.goal || '') + '</div>' +
          '<table style="width:100%;border-collapse:collapse;margin-top:8px;font-size:12px">' +
            '<thead><tr style="color:var(--ink-3);text-align:left">' +
              '<th style="padding:4px 6px;border-bottom:1px solid var(--line);width:74px">时间</th>' +
              '<th style="padding:4px 6px;border-bottom:1px solid var(--line)">任务</th>' +
              '<th style="padding:4px 6px;border-bottom:1px solid var(--line);width:56px;text-align:right">时长</th>' +
            '</tr></thead><tbody>' +
            tasks.map(function (t) {
              return '<tr>' +
                '<td style="padding:6px;border-bottom:1px solid var(--paper-2);color:var(--wood);white-space:nowrap">' + escapeHtml(t.slot || '') + '</td>' +
                '<td style="padding:6px;border-bottom:1px solid var(--paper-2)"><b>' + escapeHtml(t.title || '') + '</b>' +
                  (t.detail ? '<div style="color:var(--ink-3);font-size:11px;margin-top:2px">' + escapeHtml(t.detail) + '</div>' : '') + '</td>' +
                '<td style="padding:6px;border-bottom:1px solid var(--paper-2);text-align:right;color:var(--ink-3);white-space:nowrap">' + (t.minutes ? t.minutes + '分钟' : '—') + '</td>' +
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
      '<div style="display:flex;align-items:center;gap:8px;background:#edf2e6;border:1px solid #e3ecda;border-radius:8px;padding:9px 12px;margin-bottom:4px;font-size:12.5px">' +
        '<span>✅ 已绑定' + (b && b.phone ? '（' + escapeHtml(b.phone) + '）' : '') + ' · 错题 ' + (latest.wrong_count || latest.total_mistakes || 0) + ' 道</span>' +
        '<span style="flex:1"></span>' +
        btn('🔄 重新生成', 'C10.regen()', 'border-color:var(--line-2);color:var(--ink-2)') +
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
      '<div style="margin-top:10px">' + btn('← 返回', 'C10.refresh()', 'border-color:var(--line-2);color:var(--ink-2)') + '</div>';
  }

  function startSms() {
    _curMode = 'sms';
    var body = document.getElementById('c10Body');
    body.innerHTML =
      '<div style="font-weight:700;font-size:14px;margin-bottom:8px">📱 手机验证码登录</div>' +
      '<div id="c10LoginArea">' +
        '<div style="display:flex;flex-direction:column;gap:8px">' +
          '<input id="c10SmsPhone" type="tel" maxlength="11" inputmode="numeric" placeholder="请输入粉笔绑定的手机号" ' +
            'style="padding:11px;border:1px solid var(--line-2);border-radius:8px;font-size:15px;letter-spacing:1px">' +
          '<button onclick="C10.submitSmsStart()" style="padding:10px;border:none;border-radius:8px;background:var(--wood);color:#fff;font-size:13.5px;cursor:pointer">获取验证码</button>' +
          '<div style="color:var(--ink-3);font-size:11px;line-height:1.6">短信验证码仅用于本次登录；若触发安全验证，系统会先尝试 AI 自动识别滑块。</div>' +
        '</div>' +
      '</div>' +
      '<div style="margin-top:10px">' + btn('← 返回', 'C10.refresh()', 'border-color:var(--line-2);color:var(--ink-2)') + '</div>';
  }

  function submitSmsStart() {
    var el = document.getElementById('c10SmsPhone');
    var phone = (el && el.value || '').trim();
    if (!/^1\d{10}$/.test(phone)) { showToast('请输入 11 位手机号', 'error'); return; }
    renderLoginShell('📱 验证码登录', '<p style="color:var(--ink-3);text-align:center;padding:20px 0">正在打开登录会话…</p>');
    global.GK.api('/me/fenbi/login/sms', { method: 'POST', body: { phone: phone } })
      .then(function (r) {
        _curSid = r.sid;
        pollLogin();
      })
      .catch(function (e) {
        renderLoginShell('📱 验证码登录',
          '<p style="color:var(--bad);font-size:12.5px">会话发起失败：' + escapeHtml(e && e.message ? e.message : e) + '</p>');
      });
  }

  function startQrcode() {
    _curMode = 'qrcode';
    renderLoginShell('🔷 扫码登录', '<p style="color:var(--ink-3);text-align:center;padding:20px 0">正在获取二维码…</p>');
    global.GK.api('/me/fenbi/login/qrcode', { method: 'POST' })
      .then(function (r) {
        _curSid = r.sid;
        pollLogin();
      })
      .catch(function (e) {
        renderLoginShell('🔷 扫码登录',
          '<p style="color:var(--bad);font-size:12.5px">会话发起失败：' + escapeHtml(e && e.message ? e.message : e) + '</p>');
      });
  }

  function startPwd() {
    _curMode = 'password';
    var body = document.getElementById('c10Body');
    body.innerHTML =
      '<div style="font-weight:700;font-size:14px;margin-bottom:8px">🔑 账号密码登录</div>' +
      '<div id="c10LoginArea">' +
        '<div style="display:flex;flex-direction:column;gap:8px">' +
          '<input id="c10PwdAccount" type="text" placeholder="粉笔手机号或邮箱" ' +
            'style="padding:10px;border:1px solid var(--line-2);border-radius:8px;font-size:14px">' +
          '<input id="c10PwdPass" type="password" placeholder="粉笔账号密码" ' +
            'style="padding:10px;border:1px solid var(--line-2);border-radius:8px;font-size:14px">' +
          '<button onclick="C10.submitPwdStart()" style="padding:10px;border:none;border-radius:8px;background:var(--wood);color:#fff;font-size:13.5px;cursor:pointer">登录并绑定</button>' +
          '<div style="color:var(--ink-3);font-size:11px;line-height:1.6">密码仅用于本次登录粉笔，用后即弃、不保存；若触发安全验证会弹出滑块请你协助拖动。</div>' +
        '</div>' +
      '</div>' +
      '<div style="margin-top:10px">' + btn('← 返回', 'C10.refresh()', 'border-color:var(--line-2);color:var(--ink-2)') + '</div>';
  }

  function submitPwdStart() {
    var acc = (document.getElementById('c10PwdAccount') || {}).value || '';
    var pwd = (document.getElementById('c10PwdPass') || {}).value || '';
    if (!acc.trim() || !pwd) { showToast('请输入账号和密码', 'error'); return; }
    renderLoginShell('🔑 账号密码登录', '<p style="color:var(--ink-3);text-align:center;padding:20px 0">正在打开登录会话…</p>');
    global.GK.api('/me/fenbi/login/password', { method: 'POST', body: { account: acc.trim(), password: pwd } })
      .then(function (r) {
        _curSid = r.sid;
        pwd.value = ''; acc.value = '';
        pollLogin();
      })
      .catch(function (e) {
        renderLoginShell('🔑 账号密码登录',
          '<p style="color:var(--bad);font-size:12.5px">会话发起失败：' + escapeHtml(e && e.message ? e.message : e) + '</p>');
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
      var retry = _curMode === 'password' ? 'C10.startPwd()'
        : _curMode === 'qrcode' ? 'C10.startQrcode()' : 'C10.startSms()';
      area.innerHTML = '<p style="color:var(--bad);font-size:12.5px">登录失败：' + escapeHtml(r.error || state) + '</p>' +
        '<div style="margin-top:8px">' + btn('重试', retry, 'border-color:var(--line-2);color:var(--ink-2)') + '</div>';
      return;
    }
    if (state === 'ready_qrcode' && r.shot) {
      area.innerHTML =
        '<div style="text-align:center;padding:8px 0">' +
          '<img src="data:image/png;base64,' + r.shot + '" style="width:220px;height:220px;border:1px solid var(--line-2);border-radius:8px">' +
          '<div style="color:var(--ink-3);font-size:12.5px;margin-top:8px">打开粉笔 APP → 扫一扫，确认登录</div>' +
          '<div style="color:#bbb;font-size:11px;margin-top:4px">二维码 5 分钟内有效，过期请重新发起</div>' +
        '</div>';
      return;
    }
    if (state === 'need_drag' && r.shot) {
      area.innerHTML =
        '<div style="text-align:center">' +
          '<img src="data:image/png;base64,' + r.shot + '" style="width:100%;max-width:320px;border:1px solid var(--line-2);border-radius:8px">' +
          '<div style="color:var(--ink-2);font-size:12.5px;margin-top:8px">AI 自动识别未成功，请手动拖动下方滑条到图中缺口位置</div>' +
          '<input id="c10Drag" type="range" min="40" max="560" value="260" style="width:100%;margin-top:8px">' +
          '<div style="color:var(--ink-3);font-size:11px" id="c10DragVal">滑动距离：260 px</div>' +
          '<button onclick="C10.submitDrag()" style="margin-top:8px;padding:8px 22px;border:none;border-radius:8px;background:var(--wood);color:#fff;font-size:13px;cursor:pointer">确认</button>' +
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
          '<div style="color:var(--ink-2);font-size:12.5px">验证码已发送至 <b>' + escapeHtml(r.phone || '') + '</b>，请查收短信</div>' +
          '<input id="c10Code" type="text" inputmode="numeric" maxlength="6" placeholder="6 位验证码" ' +
            'style="width:180px;text-align:center;font-size:20px;letter-spacing:6px;border:1px solid var(--line-2);border-radius:8px;padding:10px;margin-top:12px">' +
          '<div><button onclick="C10.submitCode()" style="margin-top:12px;padding:9px 26px;border:none;border-radius:8px;background:var(--wood);color:#fff;font-size:13px;cursor:pointer">提交验证码</button></div>' +
        '</div>';
      return;
    }
    if (state === 'submitting' || state === 'init' || state === 'sending') {
      area.innerHTML = '<p style="color:var(--ink-3);text-align:center;padding:20px 0">' + escapeHtml(r.hint || '处理中…') + '</p>';
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
    submitSmsStart: submitSmsStart,
    startQrcode: startQrcode,
    startPwd: startPwd,
    submitPwdStart: submitPwdStart,
    submitCode: submitCode,
    submitDrag: submitDrag,
    analyze: analyze,
    regen: regen,
    unbind: unbind,
    printPlan: printPlan,
    docxPlan: docxPlan,
    startSync: startSync,
    syncCenter: syncCenter,
    mockDetail: mockDetail
  };
  global.GK.c10Ready = true;

})(typeof window !== 'undefined' ? window : globalThis);

/* =====================================================================
 * A6 · 附件上传模块（批次28 · 学习页上传文件/图片提问）
 * ---------------------------------------------------------------------
 * 职责：输入栏 📎 按钮 → 选文件 → 逐个 POST /api/ask/upload（FormData，
 *       a4 的 GK.api 原生透传）→ 预览条 chip（上传中/就绪/失败）→
 *       发送时把就绪附件 id 交给 A3 组装 /ask.attachments。
 *
 * 边界：
 *   - 每次提问 ≤3 个就绪附件（后端 AskReq 同步校验）
 *   - 身份切换（auth:changed）清空待发附件——附件归属绑定上传时身份，
 *     上传后登录再发送会被后端归属校验跳过
 *   - 仅 chat.html 挂载 #attachBtn/#attachBar，其他页面静默退出
 * ===================================================================== */
(function (global) {
  'use strict';

  if (!global.GK) global.GK = {};

  var MAX_FILES = 3;
  var list = [];   // [{id, kind, name, status: uploading|ready|error, err}]
  var barEl = null, inputEl = null;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function readyItems() {
    return list.filter(function (a) { return a.status === 'ready'; });
  }

  function render() {
    if (!barEl) return;
    barEl.innerHTML = '';
    barEl.style.display = list.length ? 'flex' : 'none';
    list.forEach(function (a, i) {
      var chip = document.createElement('span');
      chip.className = 'attach-chip' +
        (a.status === 'error' ? ' err' : '') +
        (a.status === 'uploading' ? ' up' : '');
      chip.title = a.err || a.name;
      var label = (a.kind === 'image' ? '🖼 ' : '📄 ') + esc(a.name.length > 16 ? a.name.slice(0, 15) + '…' : a.name);
      var tail = a.status === 'uploading' ? ' <i>上传中…</i>'
        : a.status === 'error' ? ' <i>失败</i>' : '';
      chip.innerHTML = label + tail + ' <b role="button" aria-label="移除附件" data-i="' + i + '">✕</b>';
      var b = chip.querySelector('b');
      b.onclick = function () { list.splice(i, 1); render(); };
      barEl.appendChild(chip);
    });
  }

  function notifyErr(msg) {
    if (global.GK && global.GK.toast) global.GK.toast(msg, 'error');
    else global.alert(msg);
  }

  function upload(file) {
    if (list.length >= MAX_FILES) {
      notifyErr('每次提问最多带 ' + MAX_FILES + ' 个附件');
      return;
    }
    var item = {
      id: '',
      kind: /^image\//.test(file.type || '') ? 'image' : 'file',
      name: file.name || '附件',
      status: 'uploading',
      err: '',
    };
    list.push(item);
    render();
    var fd = new FormData();
    fd.append('file', file, file.name);
    global.GK.api('/ask/upload', { method: 'POST', body: fd, timeout: 120000 })
      .then(function (r) {
        item.id = (r && r.attachment_id) || '';
        item.kind = (r && r.kind) || item.kind;
        item.status = 'ready';
        render();
      })
      .catch(function (e) {
        item.status = 'error';
        item.err = (e && e.message) || '上传失败';
        render();
        notifyErr('「' + item.name + '」上传失败：' + item.err);
        // 失败 chip 4 秒后自动移除，避免堆积
        setTimeout(function () {
          var i = list.indexOf(item);
          if (i > -1 && item.status === 'error') { list.splice(i, 1); render(); }
        }, 4000);
      });
  }

  function bind() {
    barEl = document.getElementById('attachBar');
    inputEl = document.getElementById('attachInput');
    var btn = document.getElementById('attachBtn');
    if (!barEl || !inputEl || !btn) return;   // 非对话页安全退出
    btn.onclick = function () { inputEl.click(); };
    inputEl.onchange = function () {
      Array.prototype.slice.call(inputEl.files || []).forEach(upload);
      inputEl.value = '';
    };
    if (global.GK && global.GK.bus) {
      global.GK.bus.addEventListener('auth:changed', function () {
        list = [];
        render();
      });
    }
  }

  global.GK.attach = {
    ids: function () { return readyItems().map(function (a) { return a.id; }); },
    meta: function () { return readyItems().map(function (a) { return { kind: a.kind, name: a.name }; }); },
    count: function () { return readyItems().length; },
    clear: function () { list = []; render(); },
  };

  if (document.readyState !== 'loading') bind();
  else document.addEventListener('DOMContentLoaded', bind);
})(typeof window !== 'undefined' ? window : globalThis);

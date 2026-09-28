/* =====================================================================
 * A4 · 请求转发模块（网站一 前端传输层）
 * ---------------------------------------------------------------------
 * 职责：封装所有对网站二（RAG 内核，FastAPI :9000，经 nginx /api 反代）
 *       的 HTTP / SSE 请求。前端唯一允许直接 fetch 的模块。
 *
 * 强制约束（违反就重做）：
 *   - 纯原生 JS，无第三方依赖，无打包构建。
 *   - SSE 事件名必须与 02-数据契约 §4 完全一致：
 *       start / delta / refs / done / guard_block / guard_warn / error
 *   - A1/A2/A3/A5 不得各自 fetch；一律调用 GK.api / GK.sse。
 *   - /ask 无 GET 语义，必须 POST + fetch + ReadableStream，
 *     严禁用 EventSource。
 *
 * 对外能力：
 *   GK.api(path, {method, body})  -> Promise<json>
 *   GK.sse(path, body, handlers) -> AbortController
 *
 * 共享状态：
 *   GK.store.token     (Bearer token, 字符串)
 *   GK.store.teacherId (当前老师 id)
 *   GK.store.sessionId (当前会话 id)
 *   GK.env.apiBase     (调试用, 覆盖默认 '/api')
 *
 * 错误约定：非 2xx 时把响应体里的 detail 透出到 thrown Error.message；
 *          401/403/429/5xx 一律按此处理。
 * ===================================================================== */
(function (global) {
  'use strict';

  // -----------------------------------------------------------------
  // 1. 命名空间兜底（避免 A1/A2 抢在 A4 之前初始化时挂载失败）
  // -----------------------------------------------------------------
  if (!global.GK) global.GK = {};
  if (!global.GK.store) {
    global.GK.store = { token: '', teacherId: '', sessionId: '' };
  }
  if (!global.GK.env) {
    global.GK.env = { apiBase: '' };
  }

  // -----------------------------------------------------------------
  // 2. 内部工具
  // -----------------------------------------------------------------
  function defaultApiBase() {
    // 统一拼 /api（对外路径）。本地调试用 GK.env.apiBase 覆盖。
    return (global.GK.env && global.GK.env.apiBase) || '/api';
  }

  function joinUrl(base, path) {
    if (/^https?:\/\//i.test(path)) return path;        // 绝对 URL 直通
    var b = (base || '').replace(/\/+$/, '');
    var p = (path || '').replace(/^\/+/, '');
    return b + '/' + p;
  }

  function safeJson(text) {
    if (!text) return null;
    try { return JSON.parse(text); } catch (_) { return null; }
  }

  // -----------------------------------------------------------------
  // 3. GK.api —— 非流式 JSON 请求
  //    超时 60s；非 2xx 解析响应 detail 抛出。
  // -----------------------------------------------------------------
  function api(path, opts) {
    opts = opts || {};
    var method = (opts.method || 'GET').toUpperCase();
    var body   = opts.body;
    var url    = joinUrl(defaultApiBase(), path);

    var headers = Object.assign({}, opts.headers || {});
    // FormData 原生透传（#19 AI 采集文件上传）：浏览器自动设 multipart boundary，
    // 手动设 Content-Type 反而破坏 boundary，必须跳过
    var isFormData = typeof FormData !== 'undefined' && body instanceof FormData;
    if (body !== undefined && !isFormData && headers['Content-Type'] === undefined) {
      headers['Content-Type'] = 'application/json';
    }
    if (global.GK.store && global.GK.store.token) {
      headers['Authorization'] = 'Bearer ' + global.GK.store.token;
      // 公网发布代理可能剥离 Authorization 头，冗余携带 X-Auth-Token（nginx 侧转换回传）
      headers['X-Auth-Token'] = global.GK.store.token;
    }

    var init = { method: method, headers: headers };
    if (body !== undefined && method !== 'GET' && method !== 'HEAD') {
      init.body = (typeof body === 'string' || isFormData) ? body : JSON.stringify(body);
    }

    var controller = new AbortController();
    init.signal = controller.signal;

    // 超时兜底（AbortController 取消 fetch 即可，不影响业务重试）
    // 默认 60s；大文件上传等长操作可经 opts.timeout(ms) 覆盖
    var timeoutMs = opts.timeout || 60000;
    var timer = setTimeout(function () { controller.abort('timeout'); }, timeoutMs);

    return fetch(url, init).then(function (resp) {
      clearTimeout(timer);
      // 204 / 空 body 容忍
      var ct = resp.headers.get('content-type') || '';
      var isJson = ct.indexOf('application/json') !== -1;
      var rawText = '';
      return resp.text().then(function (text) {
        rawText = text;
        if (resp.ok) {
          if (!text) return null;
          if (isJson) {
            var j = safeJson(text);
            return j === null ? text : j;   // 解析失败时透回原文
          }
          return text;
        }
        // 非 2xx：解析 detail 透出
        var payload = isJson ? safeJson(text) : null;
        var detail = (payload && (payload.detail || payload.message))
                  || text
                  || ('HTTP ' + resp.status);
        var err = new Error(String(detail));
        err.status = resp.status;
        err.payload = payload;
        throw err;
      });
    }, function (err) {
      clearTimeout(timer);
      // 断网 / 超时 / abort：统一兜底为 Error
      if (err && err.name === 'AbortError') {
        var e = new Error('请求已取消');
        e.status = 0;
        throw e;
      }
      var e2 = new Error('网络异常: ' + (err && err.message ? err.message : err));
      e2.status = 0;
      throw e2;
    });
  }

  // -----------------------------------------------------------------
  // 4. GK.sse —— POST 流式（fetch + ReadableStream 手动解析）
  //    协议：text/event-stream，按 event: / data: 分段，空行分隔。
  //    handlers: { start, delta, refs, done, guard_block, guard_warn, error,
  //                on_reconnect({attempt, delay, reason}) 断线重连通知 }
  //    断线自动重连：流中断（未收到终态事件）或网络异常时指数退避重试
  //    （1s/2s/4s，最多 3 次）；已收到 start 后重发携带 resume=已收文本（断点续答）。
  //    返回 AbortController，上层可 abort 旧流。
  // -----------------------------------------------------------------
  function sse(path, body, handlers) {
    handlers = handlers || {};
    var url = joinUrl(defaultApiBase(), path);

    var controller = new AbortController();
    var seenText = '';    // 已完整接收的答案文本（delta.text 顺序累积）
    var started = false;  // 已收到 start（后端已受理、user 消息已入库）
    var finished = false; // 已收到终态事件（done/guard_block/error）
    var attempt = 0;      // 已重连次数
    var MAX_RETRY = 3;
    var BACKOFFS = [1000, 2000, 4000];

    function safeCall(name, data) {
      var fn = handlers[name];
      if (typeof fn === 'function') {
        try { fn(data); } catch (_) { /* 业务回调异常不影响重连流程 */ }
      }
    }

    // 终态跟踪：重连判定依赖 started/finished/seenText 三态
    function eventSink(name, data) {
      if (name === 'start') {
        started = true;
      } else if (name === 'delta') {
        if (data && typeof data.text === 'string') seenText += data.text;
      } else if (name === 'done' || name === 'guard_block' || name === 'error') {
        finished = true; // guard_block/error 均为终态，不重连
      }
      safeCall(name, data);
    }

    function onBroken(reason) {
      if (finished || controller.signal.aborted) return;
      if (attempt >= MAX_RETRY) {
        finished = true;
        safeCall('error', { message: '网络中断且重连失败: ' + reason, reconnect_failed: true });
        return;
      }
      var delay = BACKOFFS[attempt] || 4000;
      attempt += 1;
      safeCall('on_reconnect', { attempt: attempt, delay: delay, reason: reason });
      setTimeout(function () {
        if (finished || controller.signal.aborted) return;
        doFetch(started && seenText ? Object.assign({}, body, { resume: seenText }) : body);
      }, delay);
    }

    function doFetch(requestBody) {
      var headers = { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' };
      if (global.GK.store && global.GK.store.token) {
        headers['Authorization'] = 'Bearer ' + global.GK.store.token;
        // 公网发布代理可能剥离 Authorization 头，冗余携带 X-Auth-Token（nginx 侧转换回传）
        headers['X-Auth-Token'] = global.GK.store.token;
      }
      fetch(url, {
        method: 'POST',
        headers: headers,
        body: JSON.stringify(requestBody == null ? {} : requestBody),
        signal: controller.signal,
        cache: 'no-store',
      }).then(function (resp) {
        if (!resp.ok) {
          // 批次27-M4：5xx（如间歇性 503/502 网关抖动）在未收到 start 前
          // 可以安全重试——走断线退避通道（1s/2s/4s）；4xx 业务错
          // （额度/敏感词/鉴权）重试无意义，保持终态错误。
          if (resp.status >= 500 && !started && attempt < MAX_RETRY) {
            onBroken('HTTP ' + resp.status);
            return null;
          }
          // 非 2xx：业务错（额度/敏感词等）重连无意义，按终态错误处理
          finished = true;
          resp.text().then(function (text) {
            var ct = resp.headers.get('content-type') || '';
            var isJson = ct.indexOf('application/json') !== -1;
            var payload = isJson ? safeJson(text) : null;
            var detail = (payload && (payload.detail || payload.message))
                      || text
                      || ('HTTP ' + resp.status);
            safeCall('error', { message: String(detail), status: resp.status });
          });
          return null;
        }
        if (!resp.body) {
          finished = true;
          safeCall('error', { message: 'empty body' });
          return null;
        }
        return parseSSE(resp, eventSink, controller);
      }).then(function (ended) {
        // 流正常结束但未收到终态事件（done/guard_block/error）→ 视为断流，走重连
        if (ended === undefined && !finished && !controller.signal.aborted) {
          onBroken('stream ended unexpectedly');
        }
      }).catch(function (err) {
        // 27-N P2-16：A3 在新会话/删除会话/切老师时 abort 旧流（防污染新会话），
        // 本分支是活代码——用户主动取消静默返回，绝不能走 onBroken 重连
        if (err && err.name === 'AbortError') return;
        onBroken('网络异常: ' + (err && err.message ? err.message : err));
      });
    }

    doFetch(body);
    return controller;
  }

  // SSE 解析：text/event-stream 按 \n\n 分事件；data: 多行拼接。
  // 协议细节：见 02-数据契约 §4。事件统一经 sink 分发（供 sse() 跟踪三态）。
  function parseSSE(resp, sink, controller) {
    var reader = resp.body.getReader();
    var decoder = new TextDecoder('utf-8');
    var buf = '';
    var aborted = false;
    if (controller && controller.signal) {
      controller.signal.addEventListener('abort', function () { aborted = true; });
    }
    function dispatch(name, data) {
      if (!aborted) {
        try { sink(name, data); } catch (_) { /* 业务回调异常不影响解析继续 */ }
      }
    }

    function pump() {
      if (aborted) return;
      return reader.read().then(function (r) {
        if (r.done) return;
        buf += decoder.decode(r.value, { stream: true });
        // 按空行切事件（CRLF / LF 都兼容）
        var idx;
        while ((idx = buf.search(/\r?\n\r?\n/)) !== -1) {
          var raw = buf.slice(0, idx);
          buf = buf.slice(idx + (buf.charAt(idx) === '\r' ? 4 : 2));
          var eventName = 'message';
          var dataLines = [];
          raw.split(/\r?\n/).forEach(function (line) {
            if (line.indexOf('event:') === 0) eventName = line.slice(6).trim();
            else if (line.indexOf('data:') === 0) dataLines.push(line.slice(5).trim());
          });
          if (dataLines.length === 0) continue;
          var dataText = dataLines.join('\n');
          var parsed = safeJson(dataText);
          // data 解析失败时，把原文包一层传出去，handlers 自决
          var payload = (parsed === null) ? { _raw: dataText } : parsed;
          // 协议事件名映射（与 02-数据契约 §4 一致）
          switch (eventName) {
            case 'start':       dispatch('start',       payload); break;
            case 'delta':       dispatch('delta',       payload); break;
            case 'refs':        dispatch('refs',        payload); break;
            case 'done':        dispatch('done',        payload); break;
            case 'guard_block': dispatch('guard_block', payload); break;
            case 'guard_warn':  dispatch('guard_warn',  payload); break;
            case 'error':       dispatch('error',       payload); break;
            default:            /* 未知事件忽略 */ break;
          }
        }
        return pump();
      });
    }
    return pump();
  }

  // -----------------------------------------------------------------
  // 5. 挂载到 GK
  // -----------------------------------------------------------------
  global.GK.api = api;
  global.GK.sse = sse;

  // -----------------------------------------------------------------
  // 6. 打印/导出 PDF（零依赖：新窗口 + 浏览器打印 → 另存为 PDF）
  //    后端 fpdf2 方案因中文字体体积暂缓，前端方案覆盖错题本/收藏/学习报告。
  // -----------------------------------------------------------------
  function printExport(title, html) {
    var t = String(title || '导出文档');
    var body = String(html || '');
    var w = window.open('', '_blank', 'width=860,height=960');
    if (!w) {
      if (global.GK.toast) global.GK.toast('弹窗被浏览器拦截，请允许弹窗后重试', 'error');
      else alert('弹窗被浏览器拦截，请允许弹窗后重试');
      return;
    }
    // 样式内联（不依赖外部 CSS，保证打印窗口离线可用）；标题转义防注入
    var esc = t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    w.document.open();
    w.document.write(
      '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">' +
      '<title>' + esc + '</title>' +
      '<style>' +
      '@page { margin: 18mm 14mm; }' +
      'body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;' +
      '  color: #1a1a1a; font-size: 13px; line-height: 1.7; margin: 0; }' +
      'h1 { font-size: 20px; text-align: center; margin: 0 0 4px; }' +
      '.meta { color: #888; font-size: 11px; text-align: center; margin-bottom: 18px; }' +
      '.item { border: 1px solid #e2e5ec; border-radius: 6px; padding: 10px 12px;' +
      '  margin-bottom: 10px; break-inside: avoid; page-break-inside: avoid; }' +
      '@media print { .no-print { display: none !important; } }' +
      '</style></head><body>' +
      '<h1>' + esc + '</h1>' +
      '<div class="meta">书山公考 · 导出于 ' + new Date().toLocaleString('zh-CN') + '</div>' +
      body +
      '<script>window.onload = function () { setTimeout(function () { window.print(); }, 120); };<' + '/script>' +
      '</body></html>');
    w.document.close();
    w.focus();
  }
  global.GK.printExport = printExport;

  // -----------------------------------------------------------------
  // 7. 导出 DOCX（#21）：与 printExport 同源 HTML → POST /me/export/docx
  //    后端 python-docx 生成真实 OOXML；blob 触发浏览器下载。
  // -----------------------------------------------------------------
  // 27-N P1-4：导出 HTML 净化——网关 WAF 会拦截请求体含 onclick=/<script 的
  // 内容（403），而各面板导出的是整段 innerHTML（必然带内联 onclick）。
  // 导出文档不需要任何可执行内容：剥内联事件属性、script/iframe 块与 javascript: 伪协议。
  function sanitizeForExport(html) {
    return String(html || '')
      .replace(/<script[\s\S]*?<\/script>/gi, '')
      .replace(/<(iframe|object|embed)\b[^>]*>[\s\S]*?<\/\1>/gi, '')
      .replace(/\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '')
      .replace(/\s(href|src)\s*=\s*("|')\s*javascript:[^"']*\2/gi, ' $1="#"');
  }

  function docxExport(title, html) {
    var t = String(title || '导出文档');
    var body = sanitizeForExport(html);
    if (!body.trim()) {
      if (global.GK.toast) global.GK.toast('暂无可导出的内容', 'error');
      return;
    }
    var headers = {'Content-Type': 'application/json'};
    if (global.GK.store && global.GK.store.token) {
      headers['Authorization'] = 'Bearer ' + global.GK.store.token;
      headers['X-Auth-Token'] = global.GK.store.token;
    }
    fetch('/api/me/export/docx', {
      method: 'POST', headers: headers,
      body: JSON.stringify({title: t, html: body})
    }).then(function (r) {
      if (!r.ok) return r.json().catch(function () { return {}; })
        .then(function (j) { throw new Error(j.detail || ('HTTP ' + r.status)); });
      return r.blob();
    }).then(function (blob) {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = t.replace(/[\\/:*?"<>|]/g, '_') + '.docx';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 3000);
      if (global.GK.toast) global.GK.toast('DOCX 已生成，开始下载', 'success');
    }).catch(function (e) {
      if (global.GK.toast) global.GK.toast('导出失败: ' + e.message, 'error');
      else alert('导出失败: ' + e.message);
    });
  }
  global.GK.docxExport = docxExport;

  // 标记模块已加载（供 A1/A2/A3/A5 / 排障用）
  global.GK.a4Ready = true;
})(typeof window !== 'undefined' ? window : globalThis);

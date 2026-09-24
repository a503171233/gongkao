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
  //    handlers: { start, delta, refs, done, guard_block, guard_warn, error }
  //    返回 AbortController，上层可 abort 旧流。
  // -----------------------------------------------------------------
  function sse(path, body, handlers) {
    handlers = handlers || {};
    var url = joinUrl(defaultApiBase(), path);

    var headers = { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' };
    if (global.GK.store && global.GK.store.token) {
      headers['Authorization'] = 'Bearer ' + global.GK.store.token;
    }

    var controller = new AbortController();

    fetch(url, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(body == null ? {} : body),
      signal: controller.signal,
      cache: 'no-store',
    }).then(function (resp) {
      if (!resp.ok) {
        // 非 2xx：尝试解析 detail，按 api 同样的方式抛错
        resp.text().then(function (text) {
          var ct = resp.headers.get('content-type') || '';
          var isJson = ct.indexOf('application/json') !== -1;
          var payload = isJson ? safeJson(text) : null;
          var detail = (payload && (payload.detail || payload.message))
                    || text
                    || ('HTTP ' + resp.status);
          // 业务方一般通过 error 回调感知（避免 Promise reject 在上游被吞）
          if (typeof handlers.error === 'function') {
            handlers.error({ message: String(detail), status: resp.status });
          } else {
            throw new Error(String(detail));
          }
        });
        return null;
      }
      if (!resp.body) {
        if (typeof handlers.error === 'function') handlers.error({ message: 'empty body' });
        return null;
      }
      return parseSSE(resp, handlers, controller);
    }).catch(function (err) {
      if (err && err.name === 'AbortError') return;     // 用户主动 abort，静默
      if (typeof handlers.error === 'function') {
        handlers.error({ message: '网络异常: ' + (err && err.message ? err.message : err) });
      }
    });

    return controller;
  }

  // SSE 解析：text/event-stream 按 \n\n 分事件；data: 多行拼接。
  // 协议细节：见 02-数据契约 §4
  function parseSSE(resp, handlers, controller) {
    var reader = resp.body.getReader();
    var decoder = new TextDecoder('utf-8');
    var buf = '';
    var aborted = false;
    if (controller && controller.signal) {
      controller.signal.addEventListener('abort', function () { aborted = true; });
    }
    function safeCall(name, data) {
      var fn = handlers[name];
      if (typeof fn === 'function' && !aborted) {
        try { fn(data); } catch (_) { /* 业务回调异常不影响解析继续 */ }
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
            case 'start':       safeCall('start',       payload); break;
            case 'delta':       safeCall('delta',       payload); break;
            case 'refs':        safeCall('refs',        payload); break;
            case 'done':        safeCall('done',        payload); break;
            case 'guard_block': safeCall('guard_block', payload); break;
            case 'guard_warn':  safeCall('guard_warn',  payload); break;
            case 'error':       safeCall('error',       payload); break;
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

  // 标记模块已加载（供 A1/A2/A3/A5 / 排障用）
  global.GK.a4Ready = true;
})(typeof window !== 'undefined' ? window : globalThis);

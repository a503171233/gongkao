// 公考学习小程序 · API 封装
// 契约与 Web 端 a4-request.js 一致：Authorization: Bearer <token>；错误统一吐 Error(message)
const app = getApp();

function base() { return app.globalData.baseUrl; }

/** 通用请求（JSON）。resolve(res.data)。 */
function request(method, path, data) {
  return new Promise((resolve, reject) => {
    wx.request({
      method,
      url: base() + path,
      data: data || {},
      header: app.globalData.token
        ? { Authorization: 'Bearer ' + app.globalData.token }
        : {},
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          const msg = (res.data && (res.data.detail || res.data.message)) ||
            ('请求失败 ' + res.statusCode);
          reject(new Error(msg));
        }
      },
      fail(err) { reject(new Error('网络异常：' + (err.errMsg || ''))); }
    });
  });
}

/** 登录（登录接口自动注册新用户，契约与 Web 端一致）。 */
function login(username, password) {
  return request('POST', '/api/login', { username, password });
}

/** 学习页问答（SSE 流式）。
 *  后端 /api/ask 返回 text/event-stream；wx.request 用 chunked 分块接收。
 *  onDelta(text) 逐块回调；返回的 abort() 可中断。 */
function askSSE(payload, onDelta, onDone, onError) {
  let buf = '';
  const task = wx.request({
    url: base() + '/api/ask',
    method: 'POST',
    enableChunked: true,
    header: {
      'Content-Type': 'application/json',
      Authorization: 'Bearer ' + app.globalData.token
    },
    data: payload,
    success() { if (onDone) onDone(); },
    fail(err) { if (onError) onError(new Error('连接中断：' + (err.errMsg || ''))); }
  });
  task.onChunkReceived(function (res) {
    // SSE 帧解析：event: xxx\ndata: {...}\n\n（与 Web 端 a4 同一解析逻辑）
    buf += arrayBufferToString(res.data);
    let idx;
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const ev = { event: 'message', data: '' };
      frame.split('\n').forEach(line => {
        if (line.indexOf('event:') === 0) ev.event = line.slice(6).trim();
        else if (line.indexOf('data:') === 0) ev.data += line.slice(5).trim();
      });
      if (!ev.data) continue;
      let d = {};
      try { d = JSON.parse(ev.data); } catch (e) { d = { text: ev.data }; }
      if (ev.event === 'delta' && onDelta) onDelta(d.text || '');
      else if (ev.event === 'error' && onError) onError(new Error(d.message || '服务异常'));
    }
  });
  return { abort() { task.abort(); } };
}

/** ArrayBuffer(UTF-8) → 字符串（小程序无 TextDecoder 时的兼容实现）。 */
function arrayBufferToString(buffer) {
  const bytes = new Uint8Array(buffer);
  let out = '';
  let i = 0;
  while (i < bytes.length) {
    const b = bytes[i];
    if (b < 0x80) { out += String.fromCharCode(b); i++; }
    else if (b < 0xE0) {
      out += String.fromCharCode(((b & 0x1F) << 6) | (bytes[i + 1] & 0x3F)); i += 2;
    } else if (b < 0xF0) {
      out += String.fromCharCode(((b & 0x0F) << 12) | ((bytes[i + 1] & 0x3F) << 6) | (bytes[i + 2] & 0x3F));
      i += 3;
    } else {
      const cp = ((b & 0x07) << 18) | ((bytes[i + 1] & 0x3F) << 12) |
        ((bytes[i + 2] & 0x3F) << 6) | (bytes[i + 3] & 0x3F);
      const off = cp - 0x10000;
      out += String.fromCharCode(0xD800 + (off >> 10), 0xDC00 + (off & 0x3FF));
      i += 4;
    }
  }
  return out;
}

module.exports = { request, login, askSSE };

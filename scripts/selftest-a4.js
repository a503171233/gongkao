// A4 静态契约自检（无需后端，验证模块自身实现）
// 运行：node scripts/selftest-a4.js
// 退出码 0 = 全通过；非 0 = 有失败

const path = require('path');
const fs = require('fs');

// 用 vm 模拟 window 全局，加载模块
const vm = require('vm');
const code = fs.readFileSync(
  path.join(__dirname, '..', 'frontend', 'js', 'a4-request.js'),
  'utf-8'
);
// Node 26+ 主线程有 fetch/AbortController/ReadableStream；vm sandbox 是隔离的
// 需把这些 Web API 显式挂到 sandbox 内
// Node fetch 不接受相对 URL，给它一个伪 base
const ctx = vm.createContext({
  console,
  fetch: (url, init) => {
    const u = (typeof url === 'string' && !/^https?:\/\//i.test(url))
      ? 'http://test.local' + (url.startsWith('/') ? url : '/' + url) : url;
    return global.fetch(u, init);
  },
  AbortController: global.AbortController,
  AbortSignal: global.AbortSignal,
  ReadableStream: global.ReadableStream,
  TextDecoder: global.TextDecoder,
  TextEncoder: global.TextEncoder,
  setTimeout, clearTimeout, setImmediate,
});
ctx.window = ctx;  // 让 IIFE 里的 window 找到 ctx
vm.runInContext(code, ctx);

const GK = ctx.window.GK;
const results = [];
function check(name, cond, detail) {
  results.push({ name, pass: !!cond, detail: detail || '' });
}

// ========== 契约自检 ==========
check('GK 命名空间挂载到 window', !!GK, 'typeof window.GK');
check('GK.a4Ready 标记', GK && GK.a4Ready === true);
check('GK.api 是函数', typeof GK.api === 'function');
check('GK.sse 是函数', typeof GK.sse === 'function');
check('GK.store 兜底初始化', GK.store && GK.store.token === '' && GK.store.teacherId === '' && GK.store.sessionId === '');
check('GK.env 兜底初始化', GK.env && GK.env.apiBase === '');

// 模拟已加载状态：注入 token
GK.store.token = 'TEST-TOKEN-12345';

// 1) GK.api('/teachers') 路径前缀：mock fetch 检查
let captured = null;
const realFetch = global.fetch;
global.fetch = (url, init) => {
  captured = { url: url, init: init };
  // 模拟 200 OK + JSON
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: (k) => k.toLowerCase() === 'content-type' ? 'application/json' : '' },
    text: () => Promise.resolve(JSON.stringify({ teachers: [{ teacher_id: 'T001' }] })),
  });
};
GK.api('/teachers').then(d => {
  check('GK.api("/teachers") 带 /api 前缀',
    captured.url.includes('/api/teachers') || captured.url.endsWith('/api/teachers'),
    'url=' + captured.url);
  check('GK.api("") 含 Bearer', /Bearer TEST-TOKEN-12345/.test(captured.init.headers.Authorization), 'hdr=' + JSON.stringify(captured.init.headers));
  check('GK.api 返回 JSON', d && d.teachers && d.teachers[0].teacher_id === 'T001');

  // 2) 429 detail 透出
  global.fetch = () => Promise.resolve({
    ok: false, status: 429,
    headers: { get: (k) => k.toLowerCase() === 'content-type' ? 'application/json' : '' },
    text: () => Promise.resolve(JSON.stringify({ detail: '今日额度已用完' })),
  });
  return GK.api('/ask');
}).then(() => {
  results.push({ name: '429 应该 reject', pass: false, detail: '异常：未 reject' });
}).catch(err => {
  check('429 抛错且 detail 透出', /今日额度已用完/.test(err.message), 'err=' + err.message);
  check('429 错误带 status=429', err.status === 429);
}).then(() => {
  // 3) 401/403/5xx 一致
  global.fetch = () => Promise.resolve({
    ok: false, status: 403,
    headers: { get: (k) => k.toLowerCase() === 'content-type' ? 'application/json' : '' },
    text: () => Promise.resolve(JSON.stringify({ detail: '无权限' })),
  });
  return GK.api('/upload');
}).then(() => {
  results.push({ name: '403 应该 reject', pass: false, detail: '异常：未 reject' });
}).catch(err => {
  check('403 detail 透出', /无权限/.test(err.message) && err.status === 403);
}).then(() => {
  // 4) SSE 事件顺序 + 取消
  // 构造一段真实的 SSE 文本流
  const sseText =
    'event: start\ndata: {"request_id":"r1","teacher_id":"T001","session_id":"s1"}\n\n' +
    'event: delta\ndata: {"text":"你好"}\n\n' +
    'event: delta\ndata: {"text":"，我是"}\n\n' +
    'event: refs\ndata: {"references":[{"doc_name":"a.md","score":0.9}],"session_id":"s1"}\n\n' +
    'event: delta\ndata: {"text":"助手"}\n\n' +
    'event: done\ndata: {"session_id":"s1"}\n\n';
  const stream = new ReadableStream({
    start(c) {
      const enc = new TextEncoder();
      c.enqueue(enc.encode(sseText));
      c.close();
    },
  });
  global.fetch = () => Promise.resolve({
    ok: true, status: 200,
    headers: { get: (k) => k.toLowerCase() === 'content-type' ? 'text/event-stream' : '' },
    body: stream,
  });
  const events = [];
  const ctl = GK.sse('/ask', { query: 'hi', stream: true }, {
    start: d => events.push(['start', d]),
    delta: d => events.push(['delta', d.text]),
    refs:  d => events.push(['refs', d.references.length]),
    done:  d => events.push(['done', d.session_id]),
  });
  return new Promise(res => setTimeout(res, 200)).then(() => {
    check('SSE start 事件触发', events[0] && events[0][0] === 'start' && events[0][1].request_id === 'r1');
    check('SSE delta 增量', events.filter(e => e[0] === 'delta').length === 3);
    check('SSE refs 触发', events.some(e => e[0] === 'refs' && e[1] === 1));
    check('SSE done 触发', events.some(e => e[0] === 'done' && e[1] === 's1'));
    check('SSE 顺序 start→delta→refs→done',
      events.map(e => e[0]).join(',').includes('start') &&
      events.indexOf(events.find(e => e[0]==='start')) < events.indexOf(events.find(e => e[0]==='refs')) &&
      events.indexOf(events.find(e => e[0]==='refs')) < events.indexOf(events.find(e => e[0]==='done'))
    );

    // 5) abort 验证
    const sseText2 = 'event: delta\ndata: {"text":"A"}\n\nevent: delta\ndata: {"text":"B"}\n\n';
    const stream2 = new ReadableStream({
      start(c) {
        const enc = new TextEncoder();
        c.enqueue(enc.encode(sseText2));
        c.close();
      },
    });
    global.fetch = () => Promise.resolve({
      ok: true, status: 200,
      headers: { get: (k) => k.toLowerCase() === 'content-type' ? 'text/event-stream' : '' },
      body: stream2,
    });
    let afterAbort = 0;
    const ctl2 = GK.sse('/ask', {}, {
      delta: d => { if (ctl2.signal.aborted) afterAbort++; },
    });
    ctl2.abort();
    return new Promise(res => setTimeout(res, 200));
  });
}).then(() => {
  // 6) 断网兜底
  global.fetch = () => Promise.reject(new Error('Failed to fetch'));
  return GK.api('/teachers');
}).then(() => {
  results.push({ name: '断网应该 reject', pass: false });
}).catch(err => {
  check('断网抛错', /网络异常/.test(err.message) || /Failed to fetch/.test(err.message), 'err=' + err.message);
}).then(() => {
  // 7) 取消时 fetch 已 Abort
  global.fetch = (url, init) => new Promise((_, rej) => {
    init.signal.addEventListener('abort', () => rej(Object.assign(new Error('aborted'), { name: 'AbortError' })));
  });
  const ctl3 = GK.api('/x');
  setTimeout(() => ctl3.controller && ctl3.controller.abort(), 10);
  return ctl3.then(() => null, err => err);
}).then(err => {
  if (err) check('Aborted 请求抛错', /取消|aborted/i.test(err.message), 'err=' + err.message);
  else results.push({ name: 'Aborted 请求抛错', pass: false, detail: '未 reject' });
}).then(() => {
  global.fetch = realFetch;

  // 汇总
  const pass = results.filter(r => r.pass).length;
  const total = results.length;
  console.log('\n========== A4 静态契约自检 ==========');
  results.forEach(r => {
    console.log((r.pass ? '  ✅' : '  ❌') + ' ' + r.name + (r.detail ? '  [' + r.detail + ']' : ''));
  });
  console.log('----------\n  ' + pass + ' / ' + total + ' 通过');
  console.log('====================================\n');
  process.exit(pass === total ? 0 : 1);
});

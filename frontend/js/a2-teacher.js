/* =====================================================================
 * A2 · 老师选择模块（网站一 前端）
 * ---------------------------------------------------------------------
 * 职责：老师列表拉取与渲染、切换老师、展示当前老师信息、广播切换事件。
 *
 * 强制约束（违反就重做）：
 *   - teacher_id 必须严格用 /teachers 注册表值（T001/T002），前端不得编造。
 *   - 走 GK.api()（A4）拉数据，本模块不得直接 fetch。
 *   - 切换老师只广播 teacher:changed 事件，不创建会话、不清空渲染区
 *     （A3 监听新建会话、A5 监听清空渲染区）。
 *   - 纯原生 JS，无第三方依赖，无构建步骤。
 *
 * 共享状态：
 *   GK.store.teacherId  —— 当前老师 id（A3 读取该状态发起 ask）
 *   GK.bus              —— 事件总线（A1/A3/A5 复用同一实例）
 *
 * 对外能力（与 index.html 调用点一致）：
 *   GK.initTeacher()     —— 页面启动初始化：拉列表渲染 + 默认老师 info
 *   GK.switchTeacher(tid)—— 切换老师：更新状态 → 拉 info → 广播 teacher:changed
 *   GK.loadInfo(tid)     —— 拉 /info 展示当前老师（上传入库后复用）
 *   GK.renderTeachers()  —— 仅重渲染 tab 高亮（会话恢复回写 teacherId 后调用）
 *
 * 事件协议（广播）：
 *   teacher:changed  { teacherId }   —— 切换成功
 * ===================================================================== */
(function (global) {
  'use strict';

  // -----------------------------------------------------------------
  // 1. 命名空间兜底（与 A4 一致，避免依赖加载顺序）
  // -----------------------------------------------------------------
  if (!global.GK) global.GK = {};
  if (!global.GK.store) {
    global.GK.store = { token: '', teacherId: '', sessionId: '' };
  }
  // 事件总线：A1/A2/A3/A5 共用同一实例（A2 负责兜底创建）
  if (!global.GK.bus) {
    global.GK.bus = (typeof EventTarget !== 'undefined')
      ? new EventTarget()
      : null; // 极旧环境降级（现代浏览器恒走 EventTarget）
  }

  var DEFAULT_TEACHER = 'T001';

  // -----------------------------------------------------------------
  // 2. 内部工具
  // -----------------------------------------------------------------
  function getTabsEl() { return document.getElementById('teacherTabs'); }
  function getInfoEl() { return document.getElementById('info'); }

  function broadcast(type, detail) {
    if (!global.GK.bus) return;
    try {
      global.GK.bus.dispatchEvent(new CustomEvent(type, { detail: detail || {} }));
    } catch (e) { /* 事件失败不影响主流程 */ }
  }

  function setInfo(text, isErr) {
    var infoEl = getInfoEl();
    if (!infoEl) return;
    infoEl.textContent = text;
    infoEl.style.color = isErr ? '#ffd9d9' : '';
  }

  // -----------------------------------------------------------------
  // 3. 初始化：拉 /teachers 渲染；默认老师 = URL ?teacher_id= > store > T001
  //    拉取失败：保留默认老师、展示错误提示而非白屏
  // -----------------------------------------------------------------
  // 分享落地：主站 /?teacher_id=T001 直达指定老师
  function urlTeacherParam() {
    try {
      var p = new URLSearchParams(window.location.search);
      var v = p.get('teacher_id');
      return v ? v.trim() : '';
    } catch (e) { return ''; }
  }

  async function loadTeachers() {
    var tabsEl = getTabsEl();
    if (!tabsEl) return;

    // 默认老师：URL ?teacher_id= 优先 → GK.store.teacherId → T001（并写回 store）
    var urlTid = urlTeacherParam();
    if (urlTid) {
      global.GK.store.teacherId = urlTid;
    } else if (!global.GK.store.teacherId) {
      global.GK.store.teacherId = DEFAULT_TEACHER;
    }
    var defaultId = global.GK.store.teacherId;

    try {
      var data = await global.GK.api('/teachers');
      var teachers = (data && data.teachers) || [];

      if (teachers.length === 0) {
        setInfo('暂无可用老师，请稍后再试', true);
        return;
      }

      // URL 指定的老师若不存在（已下线等）→ 回退第一个启用老师
      var exists = teachers.some(function (t) { return t.teacher_id === defaultId; });
      if (!exists) defaultId = teachers[0].teacher_id;
      global.GK.store.teacherId = defaultId;

      tabsEl.innerHTML = '';
      teachers.forEach(function (t) {
        var btn = document.createElement('button');
        btn.className = 'teacher-tab' + (t.teacher_id === defaultId ? ' active' : '');
        btn.textContent = t.teacher_name + '（' + t.teacher_subject + '）';
        btn.title = t.teacher_id; // 高亮判断用注册表 id，不用文本匹配
        btn.onclick = function () { switchTeacher(t.teacher_id); };
        tabsEl.appendChild(btn);
      });

      // 加载当前老师信息（失败时 setInfo 兜底，不白屏）
      return loadInfo(defaultId);
    } catch (e) {
      console.warn('[A2] 老师列表加载失败:', e && e.message ? e.message : e);
      setInfo('老师列表加载失败，已保留默认老师', true);
      return null;
    }
  }

  // -----------------------------------------------------------------
  // 4. 切换老师：更新 GK.store.teacherId → 拉 info → 广播 teacher:changed
  // -----------------------------------------------------------------
  async function switchTeacher(tid) {
    if (!tid || tid === global.GK.store.teacherId) return;
    global.GK.store.teacherId = tid;

    // 更新 tabs 激活态（按 title=teacher_id 精确高亮）
    var tabs = getTabsEl();
    if (tabs) {
      var btns = tabs.querySelectorAll('.teacher-tab');
      btns.forEach(function (btn) {
        btn.classList.toggle('active', btn.title === tid);
      });
    }

    // 拉取新老师信息
    await loadInfo(tid);

    // 广播切换事件（A3 监听新建会话、A5 监听清空渲染区）
    broadcast('teacher:changed', { teacherId: tid });
  }

  // -----------------------------------------------------------------
  // 5. 仅重渲染 tab 高亮（会话恢复回写 teacherId 后调用，不重新拉列表）
  // -----------------------------------------------------------------
  function renderTeachers() {
    var tabs = getTabsEl();
    if (!tabs) return;
    var cur = global.GK.store.teacherId;
    var btns = tabs.querySelectorAll('.teacher-tab');
    btns.forEach(function (btn) {
      btn.classList.toggle('active', btn.title === cur);
    });
  }

  // -----------------------------------------------------------------
  // 6. 信息展示：GET /info?teacher_id= → chunk_count 等；失败兜底
  // -----------------------------------------------------------------
  async function loadInfo(tid) {
    var infoEl = getInfoEl();
    if (!infoEl) return null;
    var teacherId = tid || global.GK.store.teacherId || DEFAULT_TEACHER;
    try {
      var d = await global.GK.api('/info?teacher_id=' + encodeURIComponent(teacherId));
      if (!d || d.teacher_id === undefined) {
        setInfo('老师信息加载失败', true);
        return null;
      }
      setInfo(d.teacher_name + '（' + d.teacher_subject + '）· 已收录 ' +
        d.chunk_count + ' 块知识 · 模型 ' + d.llm_model, false);
      return d;
    } catch (e) {
      console.warn('[A2] 老师信息加载失败:', e && e.message ? e.message : e);
      setInfo('老师信息加载失败，已保留当前老师', true);
      return null;
    }
  }

  // -----------------------------------------------------------------
  // 7. 对外接口（与 index.html 调用点一致）
  // -----------------------------------------------------------------
  var _inited = false;

  // 启动初始化入口：首次真正拉列表，重复调用仅重渲染高亮（幂等）
  function initTeacher() {
    if (_inited) {
      renderTeachers();
      return Promise.resolve();
    }
    _inited = true;
    return loadTeachers();
  }

  global.GK.initTeacher = initTeacher;
  global.GK.switchTeacher = switchTeacher;
  global.GK.loadInfo = loadInfo;
  global.GK.renderTeachers = renderTeachers;

  // 标记模块已加载
  global.GK.a2Ready = true;

  // 自举：不依赖 index.html 调用时机（defer 脚本在 DOMContentLoaded 前已执行）
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { initTeacher(); });
  } else {
    initTeacher();
  }
})(typeof window !== 'undefined' ? window : globalThis);

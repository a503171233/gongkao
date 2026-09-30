// ============================================================
// chat-tabs.js · 移动端 tab 页面控制器（批次27-J 自 chat.html 内联拆出，逻辑零改动）
// 内容：TAB 状态机(26-H) / BANK 题库页加载器 / EXAM 测考页 / PGPAGE 面板推入
//      页面化(27-I) / ME 我的页加载器。仅 ≤720px 启用；依赖：GK、C4（调用时惰性取）。
// ============================================================
// 批次26/27-G/27-H · Tab 导航：移动端 App 化——四 tab = 四个平级页面视图
// （学习=对话 / 题库=专项练习页 / 测考=模考中心 / 我的=学员中心浓缩页；
//   做题/模考流程仍开全屏面板，等价 App 推入做题页。仅 ≤720px 启用。）
(function () {
  "use strict";
  var sheet = document.getElementById("moreSheet");
  var mask = document.getElementById("msMask");
  var tabs = document.querySelectorAll("#tabbar button");
  var VIEWS = ["chat", "bank", "exam", "me"];

  function setOn(key) {
    tabs.forEach(function (b) { b.classList.toggle("on", b.dataset.tab === key); });
  }
  function setView(key) {
    VIEWS.forEach(function (v) { document.body.classList.remove("v-" + v); });
    if (key !== "chat") document.body.classList.add("v-" + key);
    setOn(key);
  }
  window.TAB = {
    go: function (key) {
      TAB.closeMore();
      // 批次27-H：切任何 tab 都视为离开做题/查看流程——统一关闭功能面板
      document.querySelectorAll(".c-panel, #c4Panel").forEach(function (p) {
        var btn = p.querySelector(".close, [data-close], .panel-close");
        if (btn) btn.click();
        else p.style.display = "none";
      });
      // 批次27-M5：面板全关后清掉面板态历史，下一次手势返回正常后退页面
      if (window.history.state && window.history.state.gkPanel) window.history.back();
      if (key === "me" && window.ME && ME.load) ME.load();
      if (key === "bank" && window.BANK && BANK.load) BANK.load();
      if (key === "exam" && window.EXAM && EXAM.load) EXAM.load();
      setView(key);
      if (key === "chat") {
        var chatEl = document.getElementById("chat");
        if (chatEl) chatEl.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    },
    openMore: function () {         // 「我的」页内的全部功能入口
      setOn("me");
      sheet.classList.add("open");
      mask.classList.add("show");
    },
    run: function (fn) {          // 页内功能项：收起菜单后执行
      TAB.closeMore();
      // 批次27-I 修复：不再强制切回学习视图——面板是全屏推入页，
      // 保持来源视图不动，关闭面板后自然回到打开它的页面（题库/测考/我的）
      setTimeout(fn, 120);
    },
    closeMore: function () {
      sheet.classList.remove("open");
      mask.classList.remove("show");
      if (document.querySelector("#tabbar button.on[data-tab='more']")) setOn("chat");
    }
  };
  // 仅移动端启用页面视图（桌面 tab 隐藏，v-* 类摘除恢复纯对话布局）
  function mount() {
    if (window.matchMedia("(max-width: 720px)").matches) {
      document.body.classList.add("has-tabbar");
    } else {
      document.body.classList.remove("has-tabbar", "v-chat", "v-bank", "v-exam", "v-me");
    }
  }
  mount();
  window.addEventListener("resize", mount);
})();

// 批次27-H · 题库页数据（/practice/stats 数据条 + /practice/categories 模块卡）
window.BANK = (function () {
  "use strict";
  var loaded = false;
  function set(id, v) { var el = document.getElementById(id); if (el) el.textContent = v; }
  function iconOf(name) {
    var n = String(name || "");
    if (n.indexOf("言语") > -1) return "📖";
    if (n.indexOf("判断") > -1 || n.indexOf("推理") > -1) return "🧭";
    if (n.indexOf("数量") > -1 || n.indexOf("数学") > -1) return "🔢";
    if (n.indexOf("资料") > -1 || n.indexOf("分析") > -1) return "📊";
    if (n.indexOf("常识") > -1) return "🧠";
    if (n.indexOf("政治") > -1 || n.indexOf("理论") > -1) return "🏛️";
    if (n.indexOf("申论") > -1) return "✍️";
    if (n.indexOf("面试") > -1) return "🎤";
    return "📚";
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function loadStats() {
    GK.api("/practice/stats").then(function (r) {
      set("bkSt0", r && r.total != null ? r.total : "0");
      set("bkSt1", r && r.accuracy != null ? Math.round(r.accuracy * 100) + "%" : "—");
      set("bkSt2", r && r.graded != null ? r.graded : "0");
    }).catch(function () { set("bkSt0", "0"); });
  }
  function loadCats() {
    var tid = (window.GK && GK.store && GK.store.teacherId) || "T001";
    GK.api("/practice/categories?teacher_id=" + encodeURIComponent(tid)).then(function (d) {
      var cats = (d && d.categories) || [];
      var grid = document.getElementById("bankGrid");
      if (!grid) return;
      if (!cats.length) {
        grid.innerHTML = '<div style="grid-column:1/-1;padding:26px 14px;text-align:center;color:var(--ink-3);font-size:13px;background:var(--card);border:1px dashed var(--line-2);border-radius:16px">题库编目中，先到「学习」页提问吧</div>';
        return;
      }
      grid.innerHTML = cats.map(function (c) {
        return '<button type="button" class="bcat" data-cat="' + esc(c.category) + '">' +
          '<i class="bic">' + iconOf(c.category) + '</i>' +
          '<span class="bnm">' + esc(c.category) + '</span>' +
          '<span class="bds">' + (c.count != null ? c.count + " 题可练" : "点击开始") + '</span>' +
          '<i class="bgo">›</i></button>';
      }).join("");
      grid.onclick = function (e) {
        var t = e.target.closest(".bcat");
        if (!t || !window.C4 || typeof C4.practiceCategory !== "function") return;
        TAB.run(function () { C4.practiceCategory(t.getAttribute("data-cat")); });
      };
    }).catch(function () {
      var grid = document.getElementById("bankGrid");
      if (grid) grid.innerHTML = '<div style="grid-column:1/-1;padding:22px;text-align:center;color:var(--ink-3);font-size:12.5px">分类加载失败，下拉重试</div>';
    });
  }
  function load() {
    if (!window.GK || !GK.api) { setTimeout(load, 200); return; }
    if (loaded) return;
    loaded = true;
    loadStats();
    loadCats();
  }
  return { load: load };
})();

// 批次27-H · 测考页数据（粉笔绑定态 → 模考卡副行；暂无则静态）
window.EXAM = (function () {
  "use strict";
  var loaded = false;
  function load() {
    if (!window.GK || !GK.api || loaded) return;
    loaded = true;
    // 预留：后续接入本站模考历史/最高分；当前展示静态说明即可
  }
  return { load: load };
})();

// 批次27-I · 功能面板「推入页面化」补丁（仅移动端生效，CSS 断点同款 720px）
// 统一给功能面板（#c4Panel/.c-panel/id 以 Panel 结尾）加 iOS 式推入页壳：
// 右侧滑入动画 + 顶部毛玻璃返回栏（标题取面板 h3 / 预置映射），点返回关闭面板。
// 中央 hook：面板多为 JS 动态创建（c5/c6 复用 c4Panel、c7~c12 各自创建），
// 用 MutationObserver 监听新增节点与 style 显隐切换，零侵入各功能模块。
window.PGPAGE = (function () {
  "use strict";
  var TITLES = {
    c4Panel: "练习", c5Panel: "模拟考试", c6Panel: "智能组卷",
    c7Panel: "消息中心", c8Panel: "激励中心", c9Panel: "学习报告",
    c10Panel: "粉笔提升计划", c11Panel: "申论批改", c12Panel: "面试练习"
  };
  function isMobile() {
    return window.matchMedia("(max-width: 720px)").matches;
  }
  function pageTitle(p) {
    var preset = TITLES[p.id];
    var h3 = p.querySelector("h3");
    var t = (h3 && h3.textContent || "").replace(/[✏️📝🧩📊📈🎯✍️🎤🔔🏆❌📌📅📖📚🧭🚪💎🔐]/g, "").trim();
    if (!t && preset) t = preset;
    return t || "详情";
  }
  function closePanel(p) {
    var btn = p.querySelector(".close, [data-close], .panel-close");
    if (btn) { btn.click(); return; }
    if (p.parentNode) p.parentNode.removeChild(p);
    else p.style.display = "none";
  }
  // 批次27-M5：面板接入 history——打开推入一条 {gkPanel} 记录，浏览器/手势
  // 返回 = 关闭当前面板（此前会直接离站）；面板内「‹」统一走 history.back()
  // 单一路径，确认逻辑只在 popstate 一处执行。
  function confirmClose(p) {
    if (p.id === "c4Panel") {
      var sp = document.getElementById("submitPractice");
      var res = document.getElementById("practiceResult");
      if (sp && sp.getBoundingClientRect().height > 0 && (!res || !res.textContent.trim())) {
        return window.confirm("本题还未提交，返回后作答不保存。确定返回吗？");
      }
    } else if (p.id === "c6Panel") {
      var cd = document.getElementById("seCountdown");
      if (cd && cd.getBoundingClientRect().height > 0) {
        return window.confirm("考试正在进行中，返回不会暂停计时。确定返回吗？");
      }
    }
    return true;
  }
  function pushPanelState(pid) {
    try {
      if (!(window.history.state && window.history.state.gkPanel)) {
        window.history.pushState({ gkPanel: pid }, "");
      }
    } catch (e) { /* 隐私模式等 pushState 失败：退化为无 history 集成 */ }
  }
  function visiblePanel() {
    var els = document.querySelectorAll("#c4Panel, [id$=\"Panel\"], .c-panel");
    for (var i = 0; i < els.length; i++) {
      if (isVisible(els[i])) return els[i];
    }
    return null;
  }
  window.addEventListener("popstate", function (ev) {
    if (!isMobile()) return;
    if (ev.state && ev.state.gkPanel) return;   // 前进进入面板态：由页面逻辑处理
    var p = visiblePanel();
    if (!p) return;
    if (!confirmClose(p)) {                     // 用户取消返回：顶回面板态
      pushPanelState(p.id);
      return;
    }
    closePanel(p);
  });
  function insertNav(p, animate) {
    var nav = document.createElement("div");
    nav.className = "pg-nav";
    nav.innerHTML = '<button class="pg-back" aria-label="返回">‹</button>' +
      '<span class="pg-title"></span><i class="pg-dot"></i>';
    nav.querySelector(".pg-title").textContent = pageTitle(p);
    nav.querySelector(".pg-back").onclick = function () {
      // 批次27-M5：统一走 history.back() → popstate 关面板（确认逻辑在彼处）；
      // state 缺失（直达/历史异常）时原地关闭，避免误退离站
      if (window.history.state && window.history.state.gkPanel) window.history.back();
      else if (confirmClose(p)) closePanel(p);
    };
    p.insertBefore(nav, p.firstChild);
    // 推入动画：先落位屏外（armed），双 rAF 后滑入（in）
    if (animate && !p.classList.contains("pg-in")) {
      p.classList.add("pg-armed");
      requestAnimationFrame(function () { requestAnimationFrame(function () {
        p.classList.remove("pg-armed");
        p.classList.add("pg-in");
      }); });
    }
  }
  function pageify(p) {
    if (!isMobile()) return;
    var nav = p.querySelector(".pg-nav");
    if (nav) {
      // 已有 nav：仅同步标题（c5/c6 复用 c4Panel 重写 innerHTML 时 h3 会变）
      var t = pageTitle(p);
      var el = nav.querySelector(".pg-title");
      if (t && el.textContent !== t) el.textContent = t;
      return;
    }
    // 无 nav：首次 或 innerHTML 重写把 nav 删了（c11/c12 模式）——重插
    insertNav(p, true);
    pushPanelState(p.id);   // 批次27-M5：面板可见即推入历史记录（幂等）
  }
  function isVisible(p) {
    if (!p || !p.parentNode) return false;
    var r = p.getBoundingClientRect();
    return p.style.display !== "none" && r.width > 50 && r.height > 50;
  }
  function maybe(el) {
    if (el && (el.id === "c4Panel" || /Panel$/.test(el.id || "") ||
        (el.classList && el.classList.contains("c-panel")))) {
      if (isVisible(el)) pageify(el);
    }
  }
  var mo = new MutationObserver(function (muts) {
    if (!isMobile()) return;
    muts.forEach(function (m) {
      // 新增节点：动态创建的面板
      if (m.type === "childList" && m.addedNodes && m.addedNodes.length) {
        m.addedNodes.forEach(function (n) {
          if (n.nodeType !== 1) return;
          maybe(n);
          if (n.querySelectorAll) {
            n.querySelectorAll('[id$="Panel"], .c-panel').forEach(maybe);
          }
        });
      }
      // 面板 innerHTML 重写（c5/c6 复用 c4Panel 的场景）：target 即面板
      if (m.type === "childList") maybe(m.target);
      // style 显隐切换：display 由 none → 可见
      if (m.type === "attributes") maybe(m.target);
    });
  });
  function boot() {
    mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["style"] });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
  return { pageify: pageify };
})();

// 批次27-G · 「我的」视图数据加载（数据源与 student.html 同族：
// /me 头像身份 + /practice/stats + /me/incentive + /me/fenbi/binding 四卡）
window.ME = (function () {
  "use strict";
  var loaded = false;
  function set(id, v) { var el = document.getElementById(id); if (el) el.textContent = v; }
  function renderAnon() {
    loaded = true;
    set("meName", "未登录");
    set("meAvatar", "学");
    set("meSub", "登录后同步你的学习进度");
    var rb = document.getElementById("meRole"); if (rb) rb.style.display = "none";
    document.getElementById("meLoginBtn").style.display = "";
    document.getElementById("meStats").style.display = "none";
  }
  function loadStats() {
    GK.api("/practice/stats").then(function (r) {
      set("meSt0", r && r.total != null ? r.total : "—");
      set("meSt1", r && r.accuracy != null ? Math.round(r.accuracy * 100) + "%" : "—");
    }).catch(function () { set("meSt0", "—"); set("meSt1", "—"); });
    GK.api("/me/incentive").then(function (r) {
      var pts = r && (r.points != null ? r.points : (r.summary && r.summary.points));
      set("meSt2", pts != null ? pts : "—");
    }).catch(function () { set("meSt2", "—"); });
    GK.api("/me/fenbi/binding").then(function (r) {
      set("meSt3", (r && r.bound && r.wrong_count != null) ? r.wrong_count
        : (r && r.bound ? "—" : "未绑定"));
    }).catch(function () { set("meSt3", "—"); });
  }
  function load() {
    if (!window.GK || !GK.api) { setTimeout(load, 200); return; }
    if (loaded) return;   // 首次打开拉取，之后由各功能面板自行刷新数据
    GK.api("/me").then(function (r) {
      loaded = true;
      if (!r || r.anonymous) { renderAnon(); return; }
      var name = r.username || r.user_id || "学员";
      set("meName", name);
      set("meAvatar", name.slice(0, 1).toUpperCase());
      set("meSub", (r.is_member && r.member_expire_at)
        ? "会员至 " + String(r.member_expire_at).slice(0, 10)
        : (r.quota_left === -1 ? "额度不限 · AI 问答随便问"
        : (r.quota_left != null ? "今日剩余 " + r.quota_left + " 次 AI 问答" : "")));
      var map = { admin: ["管理员", "admin"], member: ["会员", "member"], free: ["免费学员", "free"] };
      var m = map[r.role] || map.free;
      var rb = document.getElementById("meRole");
      if (rb) { rb.textContent = m[0]; rb.className = "role-badge " + m[1]; rb.style.display = ""; }
      // 账号组会员行：free=开通会员 / member=会员权益 / admin=隐藏
      var vipRow = document.getElementById("meVipRow");
      var vipTxt = document.getElementById("meVipTxt");
      if (vipRow && vipTxt) {
        if (r.role === "member") {
          var days = r.days_remaining != null ? " · 剩余 " + r.days_remaining + " 天" : "";
          vipTxt.innerHTML = "会员权益" + days + '<span class="rdesc">会员有效期与续费管理</span>';
        } else if (r.role === "admin") {
          vipRow.style.display = "none";
        }
      }
      document.getElementById("meLoginBtn").style.display = "none";
      document.getElementById("meStats").style.display = "";
      loadStats();
    }).catch(renderAnon);
  }
  // 批次27-M2：登录/登出即时刷新「我的」页——此前游客态 renderAnon 置
  // loaded=true 且不订阅 auth:changed，游客在本页登录后仍显示「未登录」，
  // 只有整页刷新才恢复（P1-7）。现在 auth:changed 重置 loaded 并重拉。
  // 注意：本文件 IIFE 无 global 参数（勿用裸 global 标识符），统一走 window。
  if (window.GK && GK.bus) {
    GK.bus.addEventListener("auth:changed", function () {
      loaded = false;
      ME.load();
    });
  }
  return { load: load };
})();

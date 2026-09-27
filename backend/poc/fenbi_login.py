#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""粉笔登录会话管理器（批次17）：学员端验证码/扫码自助绑定。

架构：每会话一个后台线程跑 playwright（sync API，独立线程无事件循环冲突），
HTTP 端点通过 Session 事件邮箱（threading.Event）与 worker 交互：
  start_sms → worker 打开登录弹窗/发码（触发滑块时截图置 need_drag 等人拖）
  → need_code 等学员输码 → 登录 → cookie 含 userid+sess 即成功
  → on_bound 回调落库（fenbi_bind.upsert_binding）→ success。
扫码流程：弹窗切二维码 → 截图 ready_qrcode → 轮询 cookie 出现登录态 → 成功。

生命周期：内存 dict 管理 + 全局并发≤2 + 每用户互斥 + TTL 10min Reaper；
状态同步留痕 fenbi_bind 库 fenbi_login_sessions 表（排查用，会话结束清截图）。

拖动轨迹/选择器/UA 脱敏均沿用 scripts/fenbi_login.py（批次15 实测通过）：
- 发码按钮 eval 语义匹配「获取验证码」；SENT=倒计时/重新获取/已发送
- 滑块 iframe 特征 url 含 verification；拖动起点 (542,477)@1280x720
- human_drag：正弦加速 + y 抖动 + overshoot 回拉
- 登录态判定：cookies 含 userid 与 sess（粉笔无 token 字段，勿误判）
"""
from __future__ import annotations

import base64
import json
import math
import random
import re
import threading
import time

from .config import Config
from . import fenbi_bind

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_LOGIN_ARGS = ["--no-sandbox", "--disable-dev-shm-usage",
               "--disable-blink-features=AutomationControlled"]
_TTL_SECS = 600          # 会话最长存活（无交互即回收）
_CODE_WAIT = 300         # 等验证码/滑距的超时
_QRCODE_WAIT = 150       # 等扫码确认的超时
_MAX_CONCURRENT = 2


def _now() -> float:
    return time.time()


def _shot_b64(page) -> str:
    """截图 → base64（PNG）。失败返回空串。"""
    try:
        return base64.b64encode(page.screenshot()).decode()
    except Exception:  # noqa: BLE001
        return ""


class _Session:
    """单个登录会话（状态机 + 事件邮箱）。"""

    def __init__(self, sid: str, user_id: str, mode: str, phone: str):
        self.sid = sid
        self.user_id = user_id
        self.mode = mode            # sms / qrcode / password
        self.phone = phone
        self.state = "init"
        self.shot = ""
        self.error = ""
        self.hint = ""
        self.created = _now()
        self.updated = _now()
        self.lock = threading.Lock()
        self._code_evt = threading.Event()
        self._code_val = ""
        self._drag_evt = threading.Event()
        self._drag_val = 0.0
        self._cancel_evt = threading.Event()
        self._browser = None
        self._thread = None

    # ---- worker 线程调用 ----
    def set_state(self, state: str, shot: str = "", error: str = "",
                  hint: str = "", phone: str = "") -> None:
        with self.lock:
            # 终态保护（批次26-L）：被顶替/取消/已成败的会话不再被
            # worker 迟到的过程态覆盖（竞态窗口：_create 顶替后 worker
            # 线程尚未执行到首个 cancelled 检查）
            if self.state in ("success", "failed", "cancelled") \
                    and state not in ("success", "failed", "cancelled"):
                return
            self.state = state
            if shot:
                self.shot = shot
            self.error = error
            self.hint = hint
            if phone:
                self.phone = phone
            self.updated = _now()

    def cancelled(self) -> bool:
        return self._cancel_evt.is_set()

    def wait_cancel(self, secs: float) -> None:
        self._cancel_evt.wait(secs)

    # ---- HTTP 线程调用 ----
    def submit_code(self, code: str) -> None:
        self._code_val = code
        self._code_evt.set()

    def submit_drag(self, dist: float) -> None:
        self._drag_val = dist
        self._drag_evt.set()

    def view(self) -> dict:
        with self.lock:
            return {"sid": self.sid, "state": self.state,
                    "shot": self.shot, "error": self.error,
                    "hint": self.hint,
                    "phone": fenbi_bind.mask_phone(self.phone),
                    "mode": self.mode}


class LoginSessionManager:
    """会话注册表：并发限制 / 互斥 / 超时清理 / 绑定回调。"""

    def __init__(self, store: fenbi_bind.FenbiBindStore,
                 on_bound=None, max_concurrent: int = _MAX_CONCURRENT):
        self._store = store
        self._on_bound = on_bound      # fn(user_id, cookie, phone)
        self._sem = threading.Semaphore(max_concurrent)
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reaper = threading.Thread(target=self._reaper_loop,
                                        daemon=True, name="fb-login-reaper")
        self._reaper.start()

    # ---------------- 对外 API（HTTP 线程） ----------------

    def start_sms(self, user_id: str, phone: str) -> dict:
        phone = re.sub(r"\D", "", str(phone or ""))
        if len(phone) != 11:
            raise ValueError("手机号格式不正确")
        s = self._create(user_id, "sms", phone)
        s._thread = threading.Thread(target=self._worker_sms, daemon=True,
                                     args=(s,), name=f"fb-sms-{s.sid[:8]}")
        s._thread.start()
        return s.view()

    def start_qrcode(self, user_id: str) -> dict:
        s = self._create(user_id, "qrcode", "")
        s._thread = threading.Thread(target=self._worker_qrcode, daemon=True,
                                     args=(s,), name=f"fb-qr-{s.sid[:8]}")
        s._thread.start()
        return s.view()

    def start_password(self, user_id: str, account: str,
                       password: str) -> dict:
        """账密登录会话（批次26-K）：账号=手机号或邮箱；密码仅驻内存，
        不落库、不进 status 响应。"""
        account = str(account or "").strip()
        password = str(password or "")
        if not account:
            raise ValueError("请输入手机号或邮箱")
        if not password:
            raise ValueError("请输入密码")
        s = self._create(user_id, "password", account)
        s._password = password
        s._thread = threading.Thread(target=self._worker_password, daemon=True,
                                     args=(s,), name=f"fb-pwd-{s.sid[:8]}")
        s._thread.start()
        return s.view()

    def status(self, sid: str, user_id: str) -> dict:
        s = self._get(sid, user_id)
        return s.view()

    def submit_code(self, sid: str, user_id: str, code: str) -> dict:
        s = self._get(sid, user_id)
        if s.mode != "sms":
            raise ValueError("会话类型不符")
        s.set_state("submitting", hint="正在提交验证码…")
        s.submit_code(code.strip())
        return s.view()

    def submit_drag(self, sid: str, user_id: str, dist: float) -> dict:
        s = self._get(sid, user_id)
        if not (30 <= dist <= 800):
            raise ValueError("滑动距离超出合理范围")
        s.set_state("submitting", hint="正在模拟拖动…")
        s.submit_drag(dist)
        return s.view()

    def cancel(self, sid: str, user_id: str) -> dict:
        s = self._get(sid, user_id)
        s._cancel_evt.set()
        s.set_state("cancelled")
        self._finish(s)
        return {"ok": True}

    # ---------------- 内部 ----------------

    def _create(self, user_id: str, mode: str, phone: str) -> _Session:
        with self._lock:
            # 同用户顶替（批次26-K）：旧活动会话自动取消，不再互斥拦截
            # （此前「已有登录任务」导致验证码失败后无法直接改用扫码）
            for sid0, s0 in list(self._sessions.items()):
                if s0.user_id == user_id and not s0.cancelled() \
                        and s0.state not in ("success", "failed", "cancelled"):
                    s0._cancel_evt.set()
                    s0.set_state("cancelled", hint="已被新的登录请求取代")
                    self._finish_nolock(s0)
                    self._sessions.pop(sid0, None)
            active = [s for s in self._sessions.values()
                      if not s.cancelled()
                      and s.state not in ("success", "failed", "cancelled")]
            if len(active) >= _MAX_CONCURRENT:
                raise ValueError("登录通道繁忙，请 1 分钟后重试")
            sid = fenbi_bind.new_session_id()
            s = _Session(sid, user_id, mode, phone)
            self._sessions[sid] = s
        self._persist(s)
        return s

    def _get(self, sid: str, user_id: str) -> _Session:
        with self._lock:
            s = self._sessions.get(sid)
        if not s or s.user_id != user_id:
            raise ValueError("会话不存在或已过期")
        return s

    def _finish(self, s: _Session) -> None:
        """杀浏览器 + 清截图 + 留痕终态。"""
        try:
            if s._browser:
                s._browser.close()
        except Exception:  # noqa: BLE001
            pass
        s.shot = ""
        with self._lock:
            self._record_final(s)

    def _finish_nolock(self, s: _Session) -> None:
        """_finish 的无锁变体（调用方已持 self._lock，如 _create 顶替）。"""
        try:
            if s._browser:
                s._browser.close()
        except Exception:  # noqa: BLE001
            pass
        s.shot = ""
        self._record_final(s)

    def _record_final(self, s: _Session) -> None:
        """终态留痕（成功/失败/取消保留 60s 供前端取最终态，随后 reaper 清）。"""
        try:
            self._store.update_session(s.sid, state=s.state, error=s.error)
            self._store.clear_session_shot(s.sid)
        except Exception:  # noqa: BLE001
            pass

    def _persist(self, s: _Session) -> None:
        try:
            self._store.save_session(s.sid, s.user_id, s.mode, s.state,
                                     s.phone, s.shot)
        except Exception:  # noqa: BLE001
            pass

    def _reaper_loop(self) -> None:
        while not self._stop.wait(30):
            self._reap_once()

    def _reap_once(self, now: float | None = None) -> None:
        """扫描超时/终态会话并清理（测试可直接调用）。"""
        now = now if now is not None else _now()
        for sid, s in list(self._sessions.items()):
            stale = s.state in ("success", "failed", "cancelled")
            ttl = 60 if stale else _TTL_SECS
            if now - s.updated > ttl:
                if not stale:
                    s._cancel_evt.set()
                    s.set_state("failed", error="会话超时，请重新发起")
                self._finish(s)
                with self._lock:
                    self._sessions.pop(sid, None)

    # ---------------- worker：验证码流程 ----------------

    def _auto_drag_attempts(self, page, s: _Session, tries: int = 3) -> bool:
        """AI 自动识别缺口并拖动（≤tries 次），返回滑块是否通过。
        每次失败后滑块可能刷新缺口，重新截图检测。全部失败回落人工。"""
        for i in range(tries):
            if s.cancelled():
                return False
            time.sleep(1.2)
            shot = _shot_b64(page)
            dist = detect_slider_dist(base64.b64decode(shot)) if shot else None
            if dist is None or not (60 <= dist <= 240):
                s.set_state("submitting",
                            hint=f"滑块自动识别中（第 {i + 1}/{tries} 次）…")
                continue
            s.set_state("submitting",
                        hint=f"AI 已定位缺口，正在自动拖动（第 {i + 1}/{tries} 次）…")
            human_drag(page.mouse, 542, 477, float(dist))
            time.sleep(2.5)
            if _slider_gone(page):
                return True
        return False

    def _worker_sms(self, s: _Session) -> None:
        from playwright.sync_api import sync_playwright
        try:
            self._sem.acquire()
            s.set_state("sending", hint="正在打开粉笔登录页…")
            self._persist(s)
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=_LOGIN_ARGS)
                s._browser = browser
                ctx = browser.new_context(
                    viewport={"width": 1280, "height": 720}, locale="zh-CN",
                    user_agent=_UA)
                ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',"
                    "{get:()=>undefined})")
                page = ctx.new_page()
                page.set_default_timeout(15000)

                page.goto("https://www.fenbi.com",
                          wait_until="domcontentloaded")
                page.wait_for_selector('button:has-text("登录")',
                                       timeout=20000)
                page.click('button:has-text("登录")')
                page.wait_for_selector('input[placeholder="手机号"]',
                                       timeout=15000)
                page.fill('input[placeholder="手机号"]', s.phone)
                page.eval_on_selector(
                    ".fenbi-login-modal-agreement-checkbox",
                    "el => el.click()")

                # ---- 发码（≤3 轮；滑块时截图等人拖） ----
                sent = False
                for rnd in range(1, 4):
                    if s.cancelled():
                        return
                    if _sent_ok(page):
                        sent = True
                        break
                    _click_send_code(page)
                    s.set_state("sending",
                                hint=f"第 {rnd} 次尝试发送验证码…")
                    for _ in range(16):
                        if s.cancelled():
                            return
                        time.sleep(0.5)
                        if _sent_ok(page):
                            sent = True
                            break
                        if _slider_present(page):
                            break
                        perr = _page_error(page)
                        if "锁定" in perr or "频繁" in perr:
                            s.set_state(
                                "failed",
                                error="触发粉笔风控锁定（操作过于频繁），"
                                      "请约 30 分钟后重试，或改用扫码/"
                                      "账号密码/Cookie 登录")
                            self._finish(s)
                            return
                    if sent:
                        break
                    if not _wait_slider(page, 10):
                        continue          # 无滑块无倒计时 → 重试
                    time.sleep(2.0)
                    if self._auto_drag_attempts(page, s):
                        if _sent_ok(page):
                            sent = True
                            break
                        continue          # 滑块过了但未发码 → 外层重试
                    # 自动识别失败 → 人工兜底（截图 + 滑条）
                    s.set_state("need_drag",
                                shot=_shot_b64(page),
                                hint="自动识别未成功，请手动完成滑块验证")
                    self._persist(s)
                    if not s._drag_evt.wait(_CODE_WAIT):
                        s.set_state("failed", error="滑块验证超时，请重新发起")
                        return
                    if s.cancelled():
                        return
                    dist = s._drag_val
                    s.set_state("submitting", hint="正在拖动滑块…")
                    human_drag(page.mouse, 542, 477, float(dist))
                    time.sleep(2.5)
                    if _slider_gone(page) and _sent_ok(page):
                        sent = True
                        break
                if not sent:
                    s.set_state("failed", error="验证码发送失败（多次尝试），"
                                                "请稍后重试或改用扫码/Cookie")
                    self._finish(s)
                    return

                # ---- 等验证码（≤3 次输入） ----
                for attempt in range(1, 4):
                    s.set_state("need_code",
                                hint="验证码已发送，请查收短信")
                    self._persist(s)
                    if not s._code_evt.wait(_CODE_WAIT):
                        s.set_state("failed", error="验证码输入超时，请重新发起")
                        return
                    if s.cancelled():
                        return
                    code = s._code_val.strip()
                    s._code_evt.clear()
                    s.set_state("submitting", hint="正在登录…")
                    page.fill('input[placeholder="验证码"]', code)
                    time.sleep(random.uniform(0.3, 0.6))
                    _click_login(page)
                    time.sleep(4)
                    cookie = _cookie_str(ctx)
                    if _logged_in(ctx):
                        self._bound(s, cookie)
                        return
                    err = _page_error(page)
                    s.set_state("need_code" if attempt < 3 else "failed",
                                error=f"登录未通过（{err or '验证码错误或已过期'}）"
                                      if attempt < 3 else
                                      f"登录失败：{err or '验证码错误'}",
                                hint="" if attempt < 3 else "")
                    self._persist(s)
                self._finish(s)
        except Exception as e:  # noqa: BLE001
            if not s.cancelled():   # 被顶替/取消的会话不覆盖终态
                s.set_state("failed", error=f"登录会话异常：{str(e)[:120]}")
            self._finish(s)
        finally:
            self._sem.release()

    # ---------------- worker：扫码流程 ----------------

    def _worker_qrcode(self, s: _Session) -> None:
        from playwright.sync_api import sync_playwright
        try:
            self._sem.acquire()
            s.set_state("sending", hint="正在打开粉笔登录页…")
            self._persist(s)
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=_LOGIN_ARGS)
                s._browser = browser
                ctx = browser.new_context(
                    viewport={"width": 1280, "height": 720}, locale="zh-CN",
                    user_agent=_UA)
                ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',"
                    "{get:()=>undefined})")
                page = ctx.new_page()
                page.set_default_timeout(15000)

                page.goto("https://www.fenbi.com",
                          wait_until="domcontentloaded")
                page.wait_for_selector('button:has-text("登录")',
                                       timeout=20000)
                page.click('button:has-text("登录")')
                page.wait_for_selector('input[placeholder="手机号"]',
                                       timeout=15000)
                # 切扫码视图：2026-09-27 实测——弹窗右上角 54px 切换图标为
                # .fenbi-login-modal-qr-code-wrap（注意 qr-code 带连字符），
                # 点击后弹窗内出现 180x180 canvas 二维码
                switched = False
                for sel in ('.fenbi-login-modal-qr-code-wrap',
                            '.fenbi-login-modal-wrapper [class*="qr-code"]',
                            'text=扫码登录', 'text=扫一扫', 'text=APP 扫码',
                            '[class*="qr-code"], [class*="qrcode"]'):
                    try:
                        page.locator(sel).first.click(timeout=2500)
                        switched = True
                        break
                    except Exception:  # noqa: BLE001
                        continue
                if not switched:
                    s.set_state("failed", error="未找到扫码入口（粉笔页型可能"
                                                "已调整），请改用验证码/Cookie")
                    self._finish(s)
                    return
                time.sleep(2.0)
                qimg = page.locator(
                    '.fenbi-login-modal-wrapper canvas,'
                    ' .fenbi-login-modal-wrapper img[src*="qr"],'
                    ' img[src*="qrcode"], img[class*="qr"], canvas').first
                try:
                    buf = qimg.screenshot(timeout=8000)
                    shot = base64.b64encode(buf).decode()
                except Exception:  # noqa: BLE001
                    shot = _shot_b64(page)
                s.set_state("ready_qrcode", shot=shot,
                            hint="请用粉笔 APP 扫码")
                self._persist(s)
                # 轮询登录态
                t0 = _now()
                while _now() - t0 < _QRCODE_WAIT:
                    if s.cancelled():
                        return
                    time.sleep(2)
                    if _logged_in(ctx):
                        self._bound(s, _cookie_str(ctx))
                        return
                s.set_state("failed", error="扫码超时（二维码已过期），请重新发起")
                self._finish(s)
        except Exception as e:  # noqa: BLE001
            if not s.cancelled():
                s.set_state("failed", error=f"扫码会话异常：{str(e)[:120]}")
            self._finish(s)
        finally:
            self._sem.release()

    def _bound(self, s: _Session, cookie: str) -> None:
        """登录成功：落库 → success。"""
        s.set_state("success", hint="绑定成功")
        if self._on_bound:
            try:
                self._on_bound(s.user_id, cookie, s.phone)
            except Exception as e:  # noqa: BLE001
                s.set_state("failed", error=f"绑定落库失败：{str(e)[:100]}")
        self._finish(s)

    # ---------------- worker：账密流程（批次26-K） ----------------

    def _worker_password(self, s: _Session) -> None:
        """账号密码登录：2026-09-27 实测弹窗存在「账号密码登录」入口，
        表单 = input[placeholder="手机号或邮箱"] + input[type=password]。"""
        from playwright.sync_api import sync_playwright
        try:
            self._sem.acquire()
            s.set_state("sending", hint="正在打开粉笔登录页…")
            self._persist(s)
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=_LOGIN_ARGS)
                s._browser = browser
                ctx = browser.new_context(
                    viewport={"width": 1280, "height": 720}, locale="zh-CN",
                    user_agent=_UA)
                ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',"
                    "{get:()=>undefined})")
                page = ctx.new_page()
                page.set_default_timeout(15000)

                page.goto("https://www.fenbi.com",
                          wait_until="domcontentloaded")
                page.wait_for_selector('button:has-text("登录")',
                                       timeout=20000)
                page.click('button:has-text("登录")')
                page.wait_for_selector('input[placeholder="手机号"]',
                                       timeout=15000)
                # 切「账号密码登录」视图
                page.locator("text=账号密码登录").first.click(timeout=8000)
                page.wait_for_selector(
                    'input[placeholder="手机号或邮箱"]', timeout=10000)
                time.sleep(0.8)
                s.set_state("submitting", hint="正在提交账号密码…")
                page.fill('input[placeholder="手机号或邮箱"]', s.phone)
                time.sleep(random.uniform(0.2, 0.4))
                page.fill('input[type="password"]', s._password)
                time.sleep(random.uniform(0.2, 0.4))
                try:
                    page.eval_on_selector(
                        ".fenbi-login-modal-agreement-checkbox",
                        "el => el.click()")
                except Exception:  # noqa: BLE001
                    try:  # 兜底：协议区域首元素
                        page.locator(
                            '.fenbi-login-modal-wrapper [class*="agree"]'
                        ).first.click(timeout=3000)
                    except Exception:  # noqa: BLE001
                        pass
                _click_pwd_login(page)

                # ---- 提交后分支：滑块 / 错误 / 成功（≤2 轮提交） ----
                for rnd in range(1, 3):
                    if s.cancelled():
                        return
                    # 等结果（滑块出现 / 报错 / 登录态），最多 12s
                    err = ""
                    got_slider = False
                    for _ in range(24):
                        if s.cancelled():
                            return
                        time.sleep(0.5)
                        if _logged_in(ctx):
                            self._bound(s, _cookie_str(ctx))
                            return
                        if _slider_present(page):
                            got_slider = True
                            break
                        err = _page_error(page)
                        if err:
                            break
                    if got_slider:
                        time.sleep(2.0)
                        if self._auto_drag_attempts(page, s):
                            if _logged_in(ctx):
                                self._bound(s, _cookie_str(ctx))
                                return
                            # 滑块过了但未自动登录 → 重新提交一次
                            s.set_state("submitting",
                                        hint="验证通过，正在重新提交…")
                            _click_pwd_login(page)
                            continue
                        s.set_state("need_drag", shot=_shot_b64(page),
                                    hint="自动识别未成功，请手动完成滑块验证")
                        self._persist(s)
                        if not s._drag_evt.wait(_CODE_WAIT):
                            s.set_state("failed",
                                        error="滑块验证超时，请重新发起")
                            return
                        if s.cancelled():
                            return
                        dist = s._drag_val
                        s._drag_evt.clear()
                        s.set_state("submitting", hint="正在拖动滑块…")
                        human_drag(page.mouse, 542, 477, float(dist))
                        time.sleep(3.0)
                        if _logged_in(ctx):
                            self._bound(s, _cookie_str(ctx))
                            return
                        if _slider_gone(page):
                            # 滑块过了但没自动登录 → 再提交一次
                            s.set_state("submitting",
                                        hint="验证通过，正在重新提交…")
                            _click_pwd_login(page)
                            continue
                        continue      # 滑块未通过 → 等待新一轮拖动
                    if err and ("密码" in err or "错误" in err
                                or "不存在" in err or "失败" in err):
                        s.set_state("failed",
                                    error=f"账号或密码可能不正确（{err[:80]}）"
                                          "，请检查后重试")
                        self._finish(s)
                        return
                    if err:
                        break
                    break
                if "锁定" in err or "频繁" in err:
                    s.set_state("failed",
                                error="触发粉笔风控锁定（操作过于频繁），"
                                      "请约 30 分钟后重试，或改用扫码/"
                                      "Cookie 登录")
                else:
                    s.set_state("failed", error="登录未完成（"
                                                f"{err or '页面无响应'}），"
                                                "请稍后重试或改用验证码/扫码")
                self._finish(s)
        except Exception as e:  # noqa: BLE001
            if not s.cancelled():
                s.set_state("failed", error=f"账密登录会话异常：{str(e)[:120]}")
            self._finish(s)
        finally:
            self._sem.release()


# ---------------- 页面交互工具（沿用批次15验证过的选择器） ----------------

def _sent_ok(page) -> bool:
    """短信已发出：倒计时/重新获取/已发送。"""
    try:
        return bool(re.search(r"验证码\(\d+秒\)|重新获取|已发送",
                              page.inner_text("body")))
    except Exception:  # noqa: BLE001
        return False


def _slider_gone(page) -> bool:
    return not _slider_present(page)


def _slider_present(page) -> bool:
    """滑块验证是否在场上：旧版为 verification iframe；新版（26-L 实测）
    为登录弹窗内的页内 DOM（「拖动下方滑块完成拼图」）。双通道兼容。"""
    try:
        for f in page.frames:
            if "verification" in (f.url or ""):
                return True
    except Exception:  # noqa: BLE001
        pass
    try:
        return page.locator("text=拖动下方滑块完成拼图").count() > 0
    except Exception:  # noqa: BLE001
        return False


def _wait_slider(page, timeout_s: float) -> bool:
    """等待滑块出现，返回是否出现（26-L 起不再返回 frame）。"""
    t0 = _now()
    while _now() - t0 < timeout_s:
        if _slider_present(page):
            return True
        time.sleep(0.5)
    return False


def _click_send_code(page) -> None:
    page.evaluate("""() => {
        const els=[...document.querySelectorAll('*')].filter(e=>
            e.textContent.trim()==='获取验证码');
        (els[els.length-1]||els[0]||{click:()=>{}}).click();
    }""")


def _click_login(page) -> None:
    page.evaluate("""() => {
        const cand=[...document.querySelectorAll('*')].filter(e=>
            e.textContent.trim()==='登录' && e.tagName!=='BUTTON'
            && e.closest('[class*="modal"]'));
        (cand[cand.length-1]||cand[0]||{}).click?.();
    }""")


def _click_pwd_login(page) -> None:
    """账密视图提交：优先弹窗内 button，兜底语义匹配非标题「登录」。"""
    try:
        page.locator(
            '.fenbi-login-modal-wrapper button:has-text("登录")'
        ).last.click(timeout=3000)
        return
    except Exception:  # noqa: BLE001
        pass
    page.evaluate("""() => {
        const cand=[...document.querySelectorAll('*')].filter(e=>
            e.textContent.trim()==='登录' && e.tagName!=='BUTTON'
            && e.closest('[class*="modal"]')
            && !e.querySelector('input'));
        (cand[cand.length-1]||cand[0]||{}).click?.();
    }""")


def _logged_in(ctx) -> bool:
    names = {c["name"] for c in ctx.cookies()}
    return "userid" in names and "sess" in names


def _cookie_str(ctx) -> str:
    pairs = []
    for c in ctx.cookies():
        if "fenbi" in (c.get("domain") or ""):
            pairs.append(f"{c['name']}={c['value']}")
    return "; ".join(pairs)


def _page_error(page) -> str:
    try:
        return page.evaluate("""() => {
            const els=[...document.querySelectorAll(
                '[class*="error"],[class*="tip"],[class*="toast"]')];
            return els.map(e=>e.textContent.trim()).filter(t=>t)
                      .join(' | ').slice(0,120);
        }""")
    except Exception:  # noqa: BLE001
        return ""


def human_drag(mouse, x0: float, y0: float, dist: float) -> None:
    """真人轨迹：正弦加减速 + y 抖动 + overshoot 回拉（批次15 实测可过）。"""
    mouse.move(x0, y0)
    time.sleep(random.uniform(0.12, 0.2))
    mouse.down()
    time.sleep(random.uniform(0.08, 0.15))
    n = 30
    weights = [math.sin(i / n * math.pi) ** 0.85 for i in range(1, n + 1)]
    total = sum(weights)
    overshoot = random.uniform(3, 6)
    x = x0
    for w in weights:
        x += (dist + overshoot) * w / total
        mouse.move(x, y0 + random.uniform(-1.5, 1.5))
        time.sleep(random.uniform(0.008, 0.022))
    time.sleep(random.uniform(0.05, 0.12))
    mouse.move(x0 + dist + overshoot - 2, y0 + random.uniform(-1, 1))
    time.sleep(random.uniform(0.06, 0.12))
    mouse.move(x0 + dist, y0)
    time.sleep(random.uniform(0.1, 0.2))
    mouse.up()


# ---------------- 滑块缺口自动识别（批次26-L v8：暗团积分最强团） ----------------
# 原理：缺口=半透明黑遮罩实心块 → 变暗 ≥18 灰度级且成块（宽 18-60，
#       缺口实测宽 29-48；>60 为长纹理带）。起点块 4 种样式特征各异无法
#       稳定检测 → 用实测均值 522（样本 518/524/518/528，最大偏差 6px）。
#       八边形亮描边样式（无暗缺口）检测不到 → 回落人工（可接受）。
# 四样本联测：real2 177(期望173) / real3 182(期望184)
#            / recon 208(期望200) / sample None(亮描边样式)
_BG_WIN = 91                    # 背景估计窗（大于最大块宽 60）
_BAND_Y0, _BAND_Y1 = 300, 460   # 拼图行带
_SEARCH_X0, _SEARCH_X1 = 578, 776   # 缺口搜索域（避开起点块/边框/图右缘外）
_SEG_W_MIN, _SEG_W_MAX = 18, 60     # 暗团宽度约束
_DARK_TH = 18.0                     # 变暗绝对阈值（灰度级）
_START_X = 522                      # 起点块左缘常数（实测均值）


def detect_slider_dist(png_bytes: bytes):
    """从整页截图检测滑块缺口，返回拖动距离 px；置信度不足返回 None。"""
    try:
        import io

        import numpy as np
        from PIL import Image
        im = Image.open(io.BytesIO(png_bytes)).convert("L")
        a = np.asarray(im, dtype=np.float32)
        if a.shape[:2] != (720, 1280):
            return None
        # 积分图水平 box 均值做背景估计
        c = np.cumsum(a, axis=1, dtype=np.float64)
        c = np.concatenate([np.zeros((a.shape[0], 1)), c], axis=1)
        h = _BG_WIN // 2
        W = a.shape[1]
        i0 = np.clip(np.arange(W) - h, 0, W)
        i1 = np.clip(np.arange(W) + h + 1, 0, W)
        bg = (c[:, i1] - c[:, i0]) / (i1 - i0)
        dark = np.clip(bg - a, 0, None)          # 正=比周围暗
        prof = np.convolve(
            dark[_BAND_Y0:_BAND_Y1, _SEARCH_X0:_SEARCH_X1].mean(axis=0),
            np.ones(3) / 3, mode="same")
        segs = []
        i, n = 0, len(prof)
        while i < n:
            if prof[i] < _DARK_TH:
                i += 1
                continue
            j = i
            while j < n and prof[j] >= _DARK_TH:
                j += 1
            if _SEG_W_MIN <= j - i <= _SEG_W_MAX:
                segs.append((_SEARCH_X0 + i, float(prof[i:j].sum())))
            i = j
        if not segs:
            return None
        segs.sort(key=lambda t: -t[1])   # 暗度×宽度积分最强团 = 缺口
        dist = round(segs[0][0] - _START_X)
        if 56 <= dist <= 254:
            return dist
        return None
    except Exception:  # noqa: BLE001
        return None

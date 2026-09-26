#!/usr/bin/env python3.11
"""粉笔登录人机协作脚本 v2（直填模式 + 滑块协作 + 短信等待）"""
import json, math, os, random, re, time
from playwright.sync_api import sync_playwright

PHONE = "13024552551"
P = lambda f: f"/tmp/fb-{f}"
LOG = open(P("log.txt"), "a", buffering=1)

def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True); LOG.write(msg + "\n")

def set_phase(s):
    with open(P("phase.txt"), "w") as f: f.write(s)

def wait_file(path, timeout_s, poll=2.0):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if os.path.exists(path):
            try:
                c = open(path).read().strip()
                if c: return c
            except OSError: pass
        time.sleep(poll)
    return None

def clean(*paths):
    for p in paths:
        try: os.remove(p)
        except OSError: pass

def human_drag(mouse, x0, y0, dist):
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

def slider_gone(page):
    return all("verification" not in (f.url or "") for f in page.frames)

def SENT(page):
    try:
        return re.search(r"验证码\(\d+秒\)|重新获取|已发送", page.inner_text("body"))
    except Exception:
        return None

def try_login(page, ctx, tag):
    time.sleep(3)
    page.screenshot(path=P("final.png"))
    cookies = ctx.cookies()
    with open(P("cookies.json"), "w") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=1)
    ctx.storage_state(path=P("state.json"))
    names = [c["name"] for c in cookies]
    log(f"[{tag}] cookie 数 {len(cookies)}，名称: {names[:20]}")
    ok = any("token" in n.lower() for n in names)
    set_phase("done" if ok else "done:no-token")
    log(f"[{tag}] 登录流程结束")

def main():
    clean(P("delta.txt"), P("code.txt"), P("phase.txt"), P("log.txt"))
    pre_code = None
    # 注意：验证码由外部在脚本运行中写入；启动时若已存在视为直填
    if os.path.exists(P("precode.txt")) and open(P("precode.txt")).read().strip():
        pre_code = open(P("precode.txt")).read().strip()
        log(f"直填模式 验证码={pre_code}")
    set_phase("init")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ])
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 720}, locale="zh-CN",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        page.set_default_timeout(15000)

        page.goto("https://www.fenbi.com", wait_until="domcontentloaded")
        page.wait_for_selector('button:has-text("登录")', timeout=20000)
        page.click('button:has-text("登录")')
        page.wait_for_selector('input[placeholder="手机号"]', timeout=15000)
        page.fill('input[placeholder="手机号"]', PHONE)
        page.eval_on_selector(".fenbi-login-modal-agreement-checkbox", "el => el.click()")
        log("手机号已填 协议已勾")

        if pre_code:
            page.fill('input[placeholder="验证码"]', pre_code)
            time.sleep(random.uniform(0.3, 0.6))
            page.evaluate("""() => {
                const cand=[...document.querySelectorAll('*')].filter(e=>
                    e.textContent.trim()==='登录' && e.tagName!=='BUTTON'
                    && e.closest('[class*="modal"]'));
                (cand[cand.length-1]||cand[0]||{}).click?.();
            }""")
            time.sleep(3)
            page.screenshot(path=P("final.png"))
            err = page.evaluate("""() => {
                const els=[...document.querySelectorAll('[class*="error"],[class*="tip"],[class*="toast"]')];
                return els.map(e=>e.textContent.trim()).filter(t=>t).join(' | ').slice(0,200);
            }""")
            log(f"直填后页面提示: {err!r}")
            # 判定：弹窗还在且出现错误提示 → 失败转完整流程；否则查 cookie
            cookies = ctx.cookies()
            if any("token" in c["name"].lower() for c in cookies):
                try_login(page, ctx, "直填"); return
            log("直填未成功（可能过期/错误），转完整流程")

        # ---- 发送验证码（最多 4 轮）----
        ok = False
        for rnd in range(1, 5):
            set_phase(f"captcha:{rnd}")
            clean(P("delta.txt"))
            if SENT(page):
                log("已在倒计时中 —— 短信已发送"); ok = True; break
            page.evaluate("""() => {
                const els=[...document.querySelectorAll('*')].filter(e=>e.textContent.trim()==='获取验证码');
                (els[els.length-1]||els[0]||{click:()=>{}}).click();
            }""")
            log(f"第{rnd}轮 已点获取验证码，观察 8s")
            sent = False
            for _ in range(16):
                time.sleep(0.5)
                if not slider_gone(page): break
                if SENT(page): sent = True; break
            if sent:
                log("短信直接发送成功（无滑块）"); ok = True; break
            frame = None
            for _ in range(20):
                for f in page.frames:
                    if "verification" in (f.url or ""): frame = f; break
                if frame: break
                time.sleep(0.5)
            if frame is None:
                log("无滑块无倒计时 —— 重试"); continue
            time.sleep(2.5)
            page.screenshot(path=P(f"cap-{rnd}.png"))
            log(f"验证码截图，等待 delta（/tmp/fb-delta.txt）")
            delta = wait_file(P("delta.txt"), 600)
            if delta is None:
                set_phase("error:delta-timeout"); log("等 delta 超时"); return
            log(f"收到 delta={delta} 开始拖动")
            human_drag(page.mouse, 542, 477, float(delta))
            time.sleep(2.5)
            page.screenshot(path=P(f"after-{rnd}.png"))
            if slider_gone(page) and SENT(page):
                log("滑块通过，短信已发出"); ok = True; break
            log("滑块未通过 —— 重试")
        if not ok:
            page.screenshot(path=P("final.png"))
            set_phase("error:captcha-failed"); log("4 轮未发出短信"); return

        # ---- 等短信验证码 ----
        set_phase("code")
        log("等待短信验证码（/tmp/fb-code.txt）")
        code = wait_file(P("code.txt"), 900)
        if code is None:
            set_phase("error:code-timeout"); log("等验证码超时"); return
        log(f"收到验证码 {code}")
        page.fill('input[placeholder="验证码"]', code)
        time.sleep(random.uniform(0.3, 0.6))
        page.evaluate("""() => {
            const cand=[...document.querySelectorAll('*')].filter(e=>
                e.textContent.trim()==='登录' && e.tagName!=='BUTTON'
                && e.closest('[class*="modal"]'));
            (cand[cand.length-1]||cand[0]||{}).click?.();
        }""")
        try_login(page, ctx, "完整流程")

main()

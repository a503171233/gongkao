# 侦察滑块验证码 DOM 结构 v2（滑块为页面内 DOM，非 iframe）
# 产出：弹窗 HTML / img+canvas+bg 元素坐标 / 手柄结构 / 整页+弹窗截图
import json
import time

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
ARGS = ["--no-sandbox", "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled"]
import os
PHONE = os.environ.get("FB_VERIFY_PHONE", "13812345678")

JS_DUMP = """() => {
  const out = {imgs: [], canvases: [], bgs: [], sliderEls: []};
  const root = [...document.querySelectorAll('div')].filter(
    d => d.textContent.includes('拖动下方滑块完成拼图')).pop();
  if (!root) return {err: 'no-root'};
  out.rootCls = (root.className||'').toString().slice(0,120);
  root.querySelectorAll('img').forEach(el => {
    const r = el.getBoundingClientRect();
    out.imgs.push({src: (el.src||'').slice(0,300), cls: (el.className||'').toString().slice(0,100),
                   rect: [r.x|0, r.y|0, r.width|0, r.height|0]});
  });
  root.querySelectorAll('canvas').forEach(el => {
    const r = el.getBoundingClientRect();
    out.canvases.push({w: el.width, h: el.height, cls: (el.className||'').toString().slice(0,100),
                       rect: [r.x|0, r.y|0, r.width|0, r.height|0]});
  });
  root.querySelectorAll('*').forEach(el => {
    const bi = getComputedStyle(el).backgroundImage;
    if (bi && bi !== 'none') {
      const r = el.getBoundingClientRect();
      if (r.width > 4) out.bgs.push({bi: bi.slice(0,300), cls: (el.className||'').toString().slice(0,100),
                                      rect: [r.x|0, r.y|0, r.width|0, r.height|0]});
    }
  });
  // 滑块手柄/轨道：找含 "拖动下方滑块" 文案的兄弟区域里的可拖元素
  const handle = [...root.querySelectorAll('*')].filter(
    e => /^(\\d+)$/.test((e.textContent||'').trim()) && e.children.length === 0
         && e.getBoundingClientRect().width > 20).map(e => ({
    tag: e.tagName, cls: (e.className||'').toString().slice(0,100),
    text: e.textContent.trim().slice(0,20),
    rect: (r => [r.x|0, r.y|0, r.width|0, r.height|0])(e.getBoundingClientRect()),
    style: (e.getAttribute('style')||'').slice(0,200)}));
  out.sliderEls = handle;
  // 所有带 style transform 的元素
  out.transforms = [...root.querySelectorAll('*')].filter(
    e => (e.getAttribute('style')||'').includes('translate')).slice(0,10).map(e => ({
    tag: e.tagName, cls: (e.className||'').toString().slice(0,100),
    rect: (r => [r.x|0, r.y|0, r.width|0, r.height|0])(e.getBoundingClientRect()),
    style: (e.getAttribute('style')||'').slice(0,200)}));
  return out;
}"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=ARGS)
    ctx = browser.new_context(viewport={"width": 1280, "height": 720},
                              locale="zh-CN", user_agent=UA)
    ctx.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    page = ctx.new_page()
    page.set_default_timeout(15000)
    page.goto("https://www.fenbi.com", wait_until="domcontentloaded")
    page.wait_for_selector('button:has-text("登录")', timeout=20000)
    page.click('button:has-text("登录")')
    page.wait_for_selector('input[placeholder="手机号"]', timeout=15000)
    page.fill('input[placeholder="手机号"]', PHONE)
    page.eval_on_selector(".fenbi-login-modal-agreement-checkbox",
                          "el => el.click()")
    page.evaluate("""() => {
        const els=[...document.querySelectorAll('*')].filter(e=>
            e.textContent.trim()==='获取验证码');
        (els[els.length-1]||els[0]||{click:()=>{}}).click();
    }""")

    try:
        page.wait_for_selector("text=拖动下方滑块完成拼图", timeout=30000)
        print("RESULT: 滑块弹窗出现")
    except Exception:  # noqa: BLE001
        print("RESULT: 30s 内未出现滑块弹窗")
        page.screenshot(path="/tmp/recon2_fail.png")
        browser.close()
        raise SystemExit
    time.sleep(3.0)  # 等背景图加载

    info = page.evaluate(JS_DUMP)
    print(json.dumps(info, ensure_ascii=False, indent=1)[:6000])

    # 弹窗区域元素级截图
    try:
        modal = page.locator("text=请进行安全验证").locator(
            "xpath=ancestor::div[position()=3]").first
        modal.screenshot(path="/tmp/recon2_modal.png")
        print("弹窗截图: /tmp/recon2_modal.png")
    except Exception as e:  # noqa: BLE001
        print("弹窗截图失败:", e)

    page.screenshot(path="/tmp/recon2_page.png")
    print("整页截图: /tmp/recon2_page.png")
    browser.close()

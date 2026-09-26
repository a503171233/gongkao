#!/usr/bin/env python3.11
"""粉笔 Cookie 续期脚本（免短信）
原理：用上次登录持久化的 storage_state 启动浏览器，若登录态仍有效则
直接重新导出 Cookie 写入采集配置（多数「Cookie 失效」场景实为 web
会话过期而登录态仍在）；仅当登录态真失效时才提示走短信重登流程。

用法：python3.11 scripts/fenbi_renew.py   （在项目根目录）
输出： renewed=true/false；renewed=true 时 Cookie 已更新到采集配置。
"""
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "fenbi-state.json"

def main():
    if not STATE.exists():
        print("renewed=false reason=no-state（先跑 scripts/fenbi_login.py 短信登录一次）")
        return 1
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ])
        ctx = browser.new_context(
            storage_state=str(STATE), viewport={"width": 1280, "height": 720},
            locale="zh-CN",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        page.goto("https://www.fenbi.com", wait_until="domcontentloaded")
        time.sleep(3)
        cks = ctx.cookies()
        names = [c["name"] for c in cks]
        logged = "sess" in names and "userid" in names
        if not logged:
            print("renewed=false reason=login-expired（需短信重登：python3.11 scripts/fenbi_login.py）")
            browser.close()
            return 1
        # 刷新会话后导出并写入配置
        parts = [f"{c['name']}={c['value']}" for c in cks
                 if c["domain"].endswith("fenbi.com")]
        cookie = "; ".join(parts)
        ctx.storage_state(path=str(STATE))  # 顺手持久化刷新后的状态
        browser.close()
    import docker  # noqa: F401  宿主机无 docker 包时退化为 docker exec
    raise SystemExit(_write_cfg(cookie))

def _write_cfg(cookie: str) -> int:
    import subprocess
    script = (
        "import json,sys;sys.path.insert(0,'/app');"
        "from poc.fenbi import save_fenbi_config;"
        f"save_fenbi_config({{'cookie': {json.dumps(cookie)}}});print('written')"
    )
    r = subprocess.run(["docker", "exec", "gongkao-backend", "python3", "-c", script],
                       capture_output=True, text=True, timeout=60)
    if r.returncode == 0 and "written" in r.stdout:
        print(f"renewed=true cookie_len={len(cookie)} tail={cookie[-4:]}")
        return 0
    print("renewed=false reason=backend-write-failed", r.stderr[:200])
    return 1

if __name__ == "__main__":
    sys.exit(main())

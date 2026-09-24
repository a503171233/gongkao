# -*- coding: utf-8 -*-
"""模块化架构结构检查：index.html div 配对/id 唯一/script 引用齐全 + 各 js 关键函数存在。"""
import re, subprocess, sys, os

base = r"D:\xiangmu\gongkao\frontend"
html = open(os.path.join(base, "index.html"), encoding="utf-8").read()
ok = True

if html.count("<div") != html.count("</div>"):
    print(f"FAIL: div 不配对 {html.count('<div')} vs {html.count('</div>')}")
    ok = False
ids = re.findall(r'id="([^"]+)"', html)
dup = [i for i in set(ids) if ids.count(i) > 1]
if dup:
    print(f"FAIL: 重复 id {dup}")
    ok = False

# script 引用齐全（A1-A5 + C4）
scripts = re.findall(r'<script src="([^"]+)"', html)
expect = ["js/a4-request.js", "js/a2-teacher.js", "js/a5-render.js",
          "js/a1-auth.js", "js/a3-session.js", "js/c4-study.js"]
for e in expect:
    if e not in scripts:
        print(f"FAIL: index.html 缺 script {e}")
        ok = False

# 关键 DOM 锚点（模块化后仍留在 index.html 的容器/按钮）
for k in ["authArea", "authModal", "giteeLoginBtn", "newSessionBtn", "sessionList",
          "uploadBtn", "teacherTabs", "c4StudyBtn", "refs-head"]:
    if k not in html:
        print(f"FAIL: 缺少 DOM 锚点 {k}")
        ok = False

# js 模块关键函数（模块化架构替代 parseSSE/guard_block 内联）
checks = {
    "js/a1-auth.js": ["openAuthModal", "loadAuthUser", "openMembershipModal"],
    "js/a2-teacher.js": ["loadTeachers", "initTeacher", "teacher:changed".replace(':', ':') or "teacher"],
    "js/a3-session.js": ["initSession", "newSession", "loadSessions"],
    "js/a4-request.js": ["GK.api", "GK.sse"],
    "js/a5-render.js": ["appendDelta", "showRefs", "block", "renderHistory"],
}
for fname, funcs in checks.items():
    p = os.path.join(base, fname)
    if not os.path.exists(p):
        print(f"FAIL: 缺文件 {fname}")
        ok = False
        continue
    src = open(p, encoding="utf-8").read()
    for f in funcs:
        # 宽松匹配：函数定义或赋值
        if f not in src:
            print(f"FAIL: {fname} 缺 {f}")
            ok = False
    r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"FAIL: {fname} JS 语法错 {r.stderr[:150]}")
        ok = False

print("RESULT:", "ALL PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
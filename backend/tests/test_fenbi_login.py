# -*- coding: utf-8 -*-
"""批次17 粉笔登录会话管理器：并发互斥/事件流/越权/超时清理/绑定回调。

不启动真浏览器（playwright 流程的选择器/轨迹已由批次15 scripts/fenbi_login.py
实弹验证）；管理器骨架与状态机在本测试中覆盖。
运行：cd backend && python tests/test_fenbi_login.py
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import poc.fenbi_bind as fb
import poc.fenbi_login as fl


def _main():
    tmp = Path(tempfile.mkdtemp(prefix="gk_fblogin_"))
    store = fb.FenbiBindStore(db_path=tmp / "fenbi_bind.db")
    bound = []

    def on_bound(user_id, cookie, phone):
        bound.append((user_id, cookie, phone))
        store.upsert_binding(user_id, cookie, phone=phone)

    mgr = fl.LoginSessionManager(store, on_bound=on_bound,
                                 max_concurrent=2)
    fails = []

    def chk(name, cond):
        print(("  ✓ " if cond else "  ✗ ") + name)
        if not cond:
            fails.append(name)

    # ---- 手机号校验 ----
    try:
        mgr.start_sms("u1", "12345")
        chk("非法手机号拒绝", False)
    except ValueError:
        chk("非法手机号拒绝", True)

    # ---- 同用户互斥（造活动会话占位） ----
    s1 = fl._Session("sid-a", "u1", "sms", "13800000001")
    s1.set_state("need_code")
    mgr._sessions["sid-a"] = s1
    try:
        mgr.start_sms("u1", "13800000001")
        chk("同用户互斥", False)
    except ValueError as e:
        chk("同用户互斥", "进行中" in str(e))

    # ---- 全局并发上限 ----
    s2 = fl._Session("sid-b", "u2", "sms", "13800000002")
    s2.set_state("need_code")
    mgr._sessions["sid-b"] = s2
    try:
        mgr.start_sms("u3", "13800000003")
        chk("并发上限(第2个可进)", True)  # max=2，占 2 → 第 3 个拒
        mgr.start_sms("u4", "13800000004")
        chk("并发上限应拒绝", False)
    except ValueError as e:
        chk("并发上限(第3个拒绝)", "繁忙" in str(e))

    # ---- 越权访问 ----
    try:
        mgr.status("sid-a", "u2")
        chk("status 越权拒绝", False)
    except ValueError:
        chk("status 越权拒绝", True)

    # ---- 事件流：submit_code/drag → worker 可取 ----
    s1.submit_code("123456")
    chk("code 事件", s1._code_evt.is_set() and s1._code_val == "123456")
    s1.submit_drag(300.5)
    chk("drag 事件", s1._drag_evt.is_set() and s1._drag_val == 300.5)

    # ---- drag 范围校验 ----
    try:
        mgr.submit_drag("sid-a", "u1", 20)
        chk("drag 范围拒绝", False)
    except ValueError:
        chk("drag 范围拒绝", True)
    try:
        mgr.submit_code("sid-a", "u1", "123456")  # mode=sms 合法
        chk("code 提交合法", True)
    except ValueError:
        chk("code 提交合法", False)

    # ---- 绑定回调：_bound → on_bound → success ----
    s1.set_state("submitting")
    mgr._bound(s1, "sess=X; userid=1; persistent=Y")
    chk("回调收到 cookie", bound and bound[0][0] == "u1"
        and "sess=X" in bound[0][1])
    chk("回调落库绑定", store.get_binding("u1")["status"] == "bound")
    chk("掩码手机", "0001" in store.get_binding("u1")["phone"]
        and "13800000001" not in json_safe(store.get_binding("u1")))
    chk("会话终态 success", s1.state == "success")

    # ---- TTL 清理：过期活动会话置 failed，终态会话回收 ----
    s3 = fl._Session("sid-c", "u5", "sms", "13800000005")
    s3.set_state("need_code")
    s3.updated = time.time() - 9999
    mgr._sessions["sid-c"] = s3
    mgr._reap_once()
    chk("过期会话置 failed", s3.state == "failed"
        and "超时" in s3.error)
    mgr._reap_once(now=time.time() + 999)
    chk("终态会话回收", "sid-c" not in mgr._sessions)

    # ---- view 不泄漏原文手机号 ----
    s4 = fl._Session("sid-d", "u6", "sms", "13812345678")
    s4.set_state("need_code")
    v = s4.view()
    chk("view 手机掩码", v["phone"] == "138****5678")

    # ---- 会话留痕表 ----
    store.save_session("sid-t", "u7", "sms", "need_code", "13800000007")
    r = store.get_session("sid-t")
    chk("留痕表掩码", r and "****" in r["phone"])
    store.clear_session_shot("sid-t")
    chk("清截图", store.get_session("sid-t")["shot"] == "")

    # ---- 批次26-K：账密通道状态机 ----
    for bad_acc, bad_pwd, name in [("", "x", "空账号拒绝"),
                                   ("  ", "x", "纯空格账号拒绝"),
                                   ("a@b.com", "", "空密码拒绝")]:
        try:
            mgr.start_password("u9", bad_acc, bad_pwd)
            chk(name, False)
        except ValueError:
            chk(name, True)

    # 造 password 活动会话 → 同用户再发起被互斥
    s5 = fl._Session("sid-p", "u10", "password", "13800000010")
    s5.set_state("submitting")
    s5._password = "secret!"
    mgr._sessions["sid-p"] = s5
    try:
        mgr.start_password("u10", "13800000010", "pw1")
        chk("password 同用户互斥", False)
    except ValueError:
        chk("password 同用户互斥", True)

    # view 掩码 + 密码绝不外泄
    v5 = s5.view()
    chk("password view 掩码", v5["phone"] == "138****0010"
        and v5["mode"] == "password")
    chk("密码不进 view", "secret" not in str(v5))
    chk("密码不进留痕", "secret" not in str(store.get_session("sid-p") or {}))

    # 邮箱账号放行（账密表单支持手机号或邮箱）
    try:
        v6 = mgr.start_password("u11", "user@example.com", "pw-11")
        chk("邮箱账号放行", v6["mode"] == "password")
        mgr.cancel(v6["sid"], "u11")
    except ValueError:
        chk("邮箱账号放行", False)

    print(f"\n结果：{'全部通过' if not fails else f'失败 {len(fails)} 项: {fails}'}")
    sys.exit(1 if fails else 0)


def json_safe(d):
    return str(d)


if __name__ == "__main__":
    _main()

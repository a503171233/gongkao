# -*- coding: utf-8 -*-
"""黄金问答集回归测试（项目资产，可重复运行）。
对应说明书 §13.1：检索命中率 >= 90% 为 M1/M2 验收门槛。

运行（服务器容器内，/app 为工作目录）：
    docker cp /tmp/golden_test.py gongkao-backend:/app/golden_test.py
    docker exec gongkao-backend python /app/golden_test.py [teacher_id]
"""
import sys
import time

from poc import store
from poc.answer import ask

TEACHER_ID = sys.argv[1] if len(sys.argv) > 1 else "T001"

# 黄金题集（围绕已入库样例讲义设计；新增老师知识库时逐步扩充）
QUESTIONS = [
    "转折关系做题的核心口诀是什么",
    "隐性转折词有哪些",
    "转折词之前的内容在文段中起什么作用",
    "网络阅读和纸质阅读，作者的真实观点是什么",
    "“很多人认为”后面的内容是作者的观点吗",
    "看到“但是”就选带“但是”内容的选项，对吗",
    "转折文段正确选项有什么特征",
    "做言语理解题的第一步应该做什么",
    "什么叫“先抑后扬”的套路",
    "转折之后是重点还是转折之前是重点",
]

THRESHOLD = 0.5  # 与 .env 一致

print(f"== 黄金问答集回归 · {TEACHER_ID} ==")
print(f"{'#':<3}{'题目':<28}{'命中':<5}{'top1分':<8}{'拒答':<5}{'答案字数':<8}{'耗时s':<7}")
print("-" * 75)

results = []
for i, q in enumerate(QUESTIONS, 1):
    t0 = time.time()
    try:
        hits = store.retrieve(q, TEACHER_ID, top_n=6)
        hits = [h for h in hits if h["score"] >= THRESHOLD]
        r = ask(q, teacher_id=TEACHER_ID, stream=False)
        elapsed = round(time.time() - t0, 2)
        top1 = round(hits[0]["score"], 3) if hits else 0.0
        rejected = r.get("rejected", False)
        ans_len = len(r.get("answer", ""))
        results.append({"hits": len(hits), "rejected": rejected})
        print(f"{i:<3}{q[:26]:<28}{len(hits):<5}{top1:<8}{str(rejected):<5}{ans_len:<8}{elapsed:<7}")
    except Exception as e:
        print(f"{i:<3}{q[:26]:<28}ERROR: {e}")
        results.append({"hits": 0, "rejected": True})

print("-" * 75)
n = len(results)
hit_rate = sum(1 for r in results if r["hits"] > 0) / n
reject_rate = sum(1 for r in results if r["rejected"]) / n
print(f"检索命中率: {hit_rate*100:.1f}%  ({sum(1 for r in results if r['hits']>0)}/{n})")
print(f"拒答率:      {reject_rate*100:.1f}%")
ok = hit_rate >= 0.9 and reject_rate <= 0.2
print("\nRESULT:", "PASS" if ok else "REVIEW NEEDED")
raise SystemExit(0 if ok else 1)
# -*- coding: utf-8 -*-
"""题库种子导入执行器：读取 seed_qbank.py 的 SEED，调用 StudyStore.add_question 批量写入。
用法（容器内）: python3 /tmp/import_qbank.py
"""
import os
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from poc.study import StudyStore

# 加载种子数据
import importlib.util
spec = importlib.util.spec_from_file_location("seed_qbank", "/tmp/seed_qbank.py")
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)
SEED = seed.SEED

s = StudyStore()
# 已存在题数（每种老师各算一次，避免重复累加）
existing = []
for t in ("T001", "T002", "T003"):
    existing += s.list_questions(t, None, 1000)
existing_texts = {q["question"] for q in existing}
print(f"当前题库已有 {len(existing)} 题（本批将新增 {len(SEED)} 题）")

added = 0
skipped = 0
for teacher_id, qtype, question, options, answer, analysis, difficulty in SEED:
    if question in existing_texts:
        skipped += 1
        print(f"  = 已存在，跳过: {question[:30]}...")
        continue
    try:
        r = s.add_question(teacher_id, qtype, question, answer,
                           options or None, analysis, difficulty)
        added += 1
        print(f"  + [{teacher_id}/{qtype}] #{r['id']} {question[:30]}...")
    except ValueError as e:
        skipped += 1
        print(f"  ! 跳过: {e} | {question[:40]}")

print(f"\n完成：新增 {added} 题，跳过 {skipped} 题")

# 汇总
for t in ("T001", "T002", "T003"):
    qs = s.list_questions(t, None, 1000)
    from collections import Counter
    c = Counter(q["qtype"] for q in qs)
    print(f"  {t}: 共 {len(qs)} 题（choice={c.get('choice',0)} judge={c.get('judge',0)} essay={c.get('essay',0)}）")

import sys, time, json, sqlite3
sys.path.insert(0, '/app')
from poc.fenbi import list_papers, fetch_paper, to_item, _load_rt, _CFG_KEY
from poc.study import StudyStore

rt = _load_rt().get(_CFG_KEY) or {}
cookie = rt.get("cookie") or ""
interval = float(rt.get("min_interval_secs") or 2)
tid = rt.get("teacher_id") or "T001"
TARGET_NEW = 600
MAX_PAGES = 8
st = StudyStore()

con = sqlite3.connect("/data/study.db")
seen = {r[0] for r in con.execute("select distinct source from question_bank")}
con.close()
print(f"已采卷 {len(seen)}，开始翻页扩采 目标新增={TARGET_NEW}", flush=True)

funnel = {"scanned": 0, "pulled": 0, "added": 0, "dupes": 0, "invalid": 0,
          "errors": [], "new_papers": []}
stop = False
for page in range(MAX_PAGES):
    if stop: break
    cursor = page * 20
    try:
        papers = list_papers(cookie, page_len=20, cursor=cursor, interval=interval)
    except Exception as e:
        print(f"页{page} 拉取失败: {e}", flush=True); continue
    print(f"--- 页{page} cursor={cursor} 卷数={len(papers)}", flush=True)
    if not papers: break
    for p in papers:
        if stop: break
        funnel["scanned"] += 1
        if p["name"] in seen:
            continue  # 已采卷跳过（不拉题目，省时间）
        try:
            raws = fetch_paper(cookie, p["exercise_key"], interval)
        except Exception as e:
            funnel["errors"].append(f"{p['name']}: {e}")
            print(f"  [ERR] {p['name']}: {e}", flush=True)
            continue
        items = [x for x in (to_item(r, p["name"], tid) for r in raws) if x]
        funnel["invalid"] += len(raws) - len(items)
        if not items:
            continue
        r = st.add_questions_batch(tid, items, sensitive_check=True)
        added = int(r.get("added", 0))
        funnel["pulled"] += 1
        funnel["added"] += added
        funnel["dupes"] += int(r.get("skipped", 0)) + int(r.get("dupe_source", 0))
        if added > 0:
            funnel["new_papers"].append(f"{p['name']}(+{added})")
            print(f"  [新卷] {p['name']} 新增{added}/总{len(items)}", flush=True)
        if funnel["added"] >= TARGET_NEW:
            print("已达目标新增，停止", flush=True)
            stop = True
        time.sleep(interval)

funnel["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
print("RESULT:", json.dumps(funnel, ensure_ascii=False), flush=True)

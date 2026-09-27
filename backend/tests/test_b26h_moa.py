#!/usr/bin/env python3
"""批次26-H · MoA 多模型协同解析 测试"""
import os, sys, json, tempfile
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TMP = tempfile.mkdtemp(prefix="gk_q26h_")
os.environ["DATA_DIR"] = str(Path(TMP) / "data")
os.environ["DB_PATH"] = str(Path(TMP) / "vector")
os.environ["EMBED_PROVIDER"] = "selftest"
os.environ["CHAT_DB"] = str(Path(TMP) / "chat.db")

import poc.api as api

PASS, FAIL = 0, 0
def chk(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")

def _draft(mid: str) -> str:
    return json.dumps({
        "ai_answer": "B", "verdict": "agree", "reason": f"{mid} 独立解题与给定答案一致",
        "sections": [{"title": t, "content": f"{mid} 的{t}内容，条理清晰。" * 3}
                     for t in ("考点定位", "解题思路", "方法总结", "易错警示", "举一反三")]},
        ensure_ascii=False)

Q = {"question": "下列关于行政许可的说法正确的是？", "qtype": "choice",
     "answer": "B", "options": ["A. 甲", "B. 乙", "C. 丙", "D. 丁"],
     "analysis": "故选B。"}

print("== 1. 配置默认值与开关 ==")
chk("提案层默认 3 模型（GLM 5.2/GPT 5.6 Luna/千问）",
    api._MOA_PROPOSERS == ["glm-5.2", "gpt-5.6-luna", "qwen3.8-flash"],
    f"{api._MOA_PROPOSERS}")
chk("聚合层默认 deepseek-v4-pro", api._MOA_AGGREGATOR == "deepseek-v4-pro")
chk("MoA 默认开启", api._moa_enabled() is True)
with mock.patch.dict(os.environ, {"MOA_ENABLED": "0"}):
    chk("MOA_ENABLED=0 关闭", api._moa_enabled() is False)

print("== 2. _call_llm_by_id ==")
_FAKE_MODEL = {"model_id": "deepseek-v4-pro", "enabled": 1, "temperature": None,
               "base_url": "https://gw.example/v1", "api_key": "sk-test"}
with mock.patch("poc.api.Config") as mc, \
     mock.patch("poc.models.get_ai_model_store") as ms, \
     mock.patch("poc.llm.call_llm", return_value="ok") as mcall:
    mc.return_value.get_teacher.return_value = mock.MagicMock()
    ms.return_value.get_model.return_value = dict(_FAKE_MODEL)
    out = api._call_llm_by_id("deepseek-v4-pro", [{"role": "user", "content": "hi"}])
    chk("按 id 调用透传 model/base_url/api_key",
        mcall.call_args.kwargs.get("model") == "deepseek-v4-pro"
        and mcall.call_args.kwargs.get("api_key") == "sk-test" and out == "ok")
with mock.patch("poc.models.get_ai_model_store") as ms:
    ms.return_value.get_model.return_value = None
    try:
        api._call_llm_by_id("no-such-model", [])
        chk("不存在模型抛 ValueError", False)
    except ValueError:
        chk("不存在模型抛 ValueError", True)

print("== 3. _moa_propose 并行容错 ==")
with mock.patch.object(api, "_MOA_PROPOSERS", ["m1", "m2", "m3"]), \
     mock.patch.object(api, "_call_llm_by_id",
                       side_effect=lambda mid, msgs, **kw: (
                           _draft(mid) if mid != "m2" else (_ for _ in ()).throw(RuntimeError("boom")))):
    drafts = api._moa_propose([])
chk("单模型失败不拖垮整体（2/3 稿）", len(drafts) == 2 and all(m != "m2" for m, _ in drafts),
    f"{[m for m, _ in drafts]}")
with mock.patch.object(api, "_MOA_PROPOSERS", ["x1"]), \
     mock.patch.object(api, "_call_llm_by_id", side_effect=RuntimeError("all down")):
    chk("全失败返回空列表", api._moa_propose([]) == [])

print("== 4. _moa_analysis_call 全链 ==")
seen_models = []
def fake_call(model_id, messages, max_tokens=2400):
    seen_models.append(model_id)
    if model_id == "AGG":
        return _draft("AGG")
    return _draft(model_id)
with mock.patch.object(api, "_MOA_PROPOSERS", ["m1", "m2", "m3"]), \
     mock.patch.object(api, "_MOA_AGGREGATOR", "AGG"), \
     mock.patch.object(api, "_call_llm_by_id", side_effect=fake_call):
    text, agg = api._moa_analysis_call(Q)
chk("聚合层被调用且模型正确", agg == "AGG" and seen_models.count("AGG") == 1,
    f"seen={seen_models}")
chk("提案层 3 模型全部被调", seen_models.count("m1") == 1
    and seen_models.count("m2") == 1 and seen_models.count("m3") == 1)
data = json.loads(text[text.find("{"): text.rfind("}") + 1])
chk("聚合输出为合法解析 JSON", data.get("ai_answer") == "B"
    and len(data.get("sections") or []) == 5)

print("== 5. 无提案抛异常（端点据此降级） ==")
with mock.patch.object(api, "_MOA_PROPOSERS", ["x1"]), \
     mock.patch.object(api, "_call_llm_by_id", side_effect=RuntimeError("down")):
    try:
        api._moa_analysis_call(Q)
        chk("全失败抛 RuntimeError", False)
    except RuntimeError:
        chk("全失败抛 RuntimeError", True)

print("== 6. 聚合 prompt 结构 ==")
msgs = api._moa_aggregate_prompt(Q, [("m1", "稿1"), ("m2", "稿2")])
u = msgs[1]["content"]
chk("包含题目/给定答案/全部草稿", "题目：" in u and "给定答案：B" in u
    and "【草稿1 · m1】" in u and "【草稿2 · m2】" in u)
chk("包含合成规则与答案复核指令", "取各草稿之长" in u and "ai_answer" in u)

print(f"\n通过 {PASS} · 失败 {FAIL}")
sys.exit(1 if FAIL else 0)

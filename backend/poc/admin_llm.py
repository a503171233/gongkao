# -*- coding: utf-8 -*-
"""C2 后台业务逻辑：LLM 调用封装 + JSON 容错解析 + 题目归一化（从 admin.py 拆分）。"""
import json
import re


_QNUM_LINE_RE = re.compile(r"(?m)^\s*\d{1,3}[.．、)）]\s*\S")


def _split_batches(text: str, batch_size: int, max_batches: int) -> list[str]:
    """按题号边界分批（#35 R2：不拦腰截断题目）。
    切点优先级：batch_size 附近最近的题号行 → 段落边界 → 硬切。
    超 max_batches 部分丢弃（由调用方产生显式告警）。"""
    if len(text) <= batch_size:
        return [text]
    batches: list[str] = []
    rest = text
    while rest and len(batches) < max_batches:
        if len(rest) <= batch_size:
            batches.append(rest)
            break
        lo, hi = batch_size // 2, batch_size
        # 优先：题号行边界（题目起点，切在这里保证批首是完整题目）
        qcut = -1
        for m in _QNUM_LINE_RE.finditer(rest[:hi]):
            if m.start() >= lo:
                qcut = m.start()
        if qcut > 0:
            cut = qcut
        else:
            # 次选：段落边界
            cut = rest.rfind("\n", lo, hi)
            if cut == -1:
                cut = batch_size
        batches.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    return batches


def _count_question_floor(piece: str) -> int:
    """粗略估计文档题目数下限（仅用于漏题回采判定）：
    "答案：X" 出现次数与 "编号行"（如 "1. 题干" / "1、"）取较大者。
    对"同题干不同选项"题组，因每题带独立"答案："行，可正确统计为多道。"""
    ans_cnt = len(re.findall(r"答案\s*[:：]", piece))
    num_cnt = len(re.findall(r"(?m)^\s*\d{1,3}[.．、)）]\s*\S", piece))
    return max(ans_cnt, num_cnt)


def _half_split(piece: str) -> list[str]:
    """按中间最近的段落边界拆半（避免把一道题拦腰切到两个子批）。"""
    mid = len(piece) // 2
    cut = piece.rfind("\n", mid // 2, mid + mid // 2)
    if cut == -1:
        cut = mid
    return [piece[:cut], piece[cut:].lstrip("\n")]


def _effective_llm_conf(teacher_id: str = "") -> tuple:
    """(#35) 取 LLM 调用参数载体：返回 (tcfg, eff)。eff 为模型注册表默认模型配置。"""
    from .config import Config

    cfg = Config()
    tcfg = cfg.get_teacher(teacher_id or cfg.teacher_id)
    eff = None
    try:
        from .models import get_ai_model_store
        eff = get_ai_model_store().effective_default()
    except Exception:  # noqa: BLE001  DB 异常不阻断
        eff = None
    return tcfg, eff


def _llm_call(messages, tcfg, max_tokens: int = 2000, model: str | None = None,
              temperature: float | None = None,
              base_url: str | None = None, api_key: str | None = None) -> str:
    """#35 修复：管理端 LLM 调用统一封装 —— 注册表默认模型优先，通道失败回退全局主模型。

    背景：模型注册表默认模型（eff）在网关侧可能无可用渠道（503 model_not_found），
    且 llm.call_llm 的 FALLBACK_MODELS 复用同一通道无法兜底；全局 .env 主模型
    （LLM_MODEL + API_BASE_URL）是独立兜底路径。两路全败时抛 ValueError
    （带模型名与处置建议），由各端点转 400 可读提示，替代裸 500。"""
    from . import llm
    has_eff_route = any(x is not None for x in (model, temperature, base_url, api_key))
    try:
        return llm.call_llm(messages, tcfg, max_tokens=max_tokens, model=model,
                            temperature=temperature, base_url=base_url, api_key=api_key)
    except ValueError:
        raise
    except Exception as first_err:
        if not has_eff_route:
            raise
        try:
            return llm.call_llm(messages, tcfg, max_tokens=max_tokens)
        except Exception as second_err:
            raise ValueError(
                f"AI 服务暂不可用：模型 {model or tcfg.llm_model} 调用失败"
                f"（{str(first_err)[:90]}），全局模型 {tcfg.llm_model} 兜底亦失败"
                f"（{str(second_err)[:90]}）。请稍后重试，或在「AI 模型管理」更换默认模型。")


# ---------- #24 R1：LLM JSON 多策略容错解析 ----------

def _parse_llm_json_array(raw: str) -> tuple[list, str | None]:
    """LLM 原始输出 → (数组, 错误原因)。成功时错误为 None。
    策略：①剥 ``` 包裹取 [..] 直接解析 → ②修复尾逗号/行注释重试
    → ③截断/损坏抢救（扫描平衡 {} 逐对象解析）。"""
    s = (raw or "").strip()
    if not s:
        return [], "模型返回为空"
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    start = s.find("[")
    if start == -1:
        # 退化：模型只输出了单个对象 {...} → 包成数组
        ob, cb = s.find("{"), s.rfind("}")
        if ob != -1 and cb > ob:
            s = "[" + s[ob:cb + 1] + "]"
            start = 0
        else:
            return [], "输出中未找到 JSON 数组（模型未按要求的格式返回）"
    end = s.rfind("]")
    frag = s[start:end + 1] if end > start else None
    if frag is None:
        # 有 [ 无 ]：输出被截断 → 抢救已完整的对象
        objs = _salvage_json_objects(s[start:])
        if objs:
            return objs, None
        return [], "JSON 被截断（缺少结尾 ]，疑似超出模型单次输出长度）"
    # 策略 1：直接解析
    try:
        arr = json.loads(frag)
        if isinstance(arr, list):
            return arr, None
        return [], "顶层不是数组"
    except ValueError:
        pass
    # 策略 2：修复常见语法问题（尾逗号 / // 行注释 / 全角引号）
    fixed = re.sub(r",\s*([\]}])", r"\1", frag)
    fixed = re.sub(r"//[^\n\"]*\n", "\n", fixed)
    fixed = fixed.replace("“", '"').replace("”", '"')
    try:
        arr = json.loads(fixed)
        if isinstance(arr, list):
            return arr, None
    except ValueError:
        pass
    # 策略 3：逐对象抢救（截断在数组中间 / 个别对象损坏）
    objs = _salvage_json_objects(frag)
    if objs:
        return objs, None
    try:
        json.loads(frag)
    except ValueError as e:
        return [], f"JSON 语法错误: {str(e)[:100]}"
    return [], "无法解析"


def _salvage_json_objects(s: str | list | dict) -> list[dict]:
    """扫描平衡的顶层 {...} 对象逐个解析（容错截断与个别损坏对象）。
    字符串感知：引号内的 {}/, 不影响括号深度。
    仅从顶层 { 开始收集对象缓冲（忽略数组开头的 [ 与前导噪声），
    因此即便整个数组被截断、结尾缺 ]，完整对象也能逐个抢救出来。"""
    if isinstance(s, (list, dict)):  # 已是对象/数组，直接喂给调用方兜底
        return []
    out: list[dict] = []
    depth = 0
    buf: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            buf.append(ch)
        elif ch == "{":
            depth += 1
            buf.append(ch)
        elif ch == "}":
            depth -= 1
            buf.append(ch)
            if depth == 0:
                cand = "".join(buf)
                for attempt in (cand, re.sub(r",\s*([\]}])", r"\1", cand)):
                    try:
                        obj = json.loads(attempt)
                        if isinstance(obj, dict):
                            out.append(obj)
                            break
                    except ValueError:
                        continue
                buf = []
            elif depth < 0:
                break  # 结构异常，停止
        elif depth >= 1:
            buf.append(ch)
    return out


def _clean_img_marks(text: str) -> str:
    """#33 R2 题干图片标记清洗：只保留合法 ![图N](/api/qimg/...) 标记。

    LLM 可能改写/编造标记（外部 URL、伪造 fname），前端渲染白名单只认
    /api/qimg/{tid}/{sha1前12}.{ext}，这里同步清洗防止死链与脏数据入库。
    #35 R2 修复：正则补 alt 捕获组（原单组下 m.group(2) 触发 IndexError，
    图形推理等含图片标记的题干首次真正触发该路径）。
    """
    from .ingest import QIMG_URL_RE

    def _sub(m: re.Match) -> str:
        return m.group(0) if QIMG_URL_RE.match(m.group(2)) else ""

    return re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", _sub, text)


def _extract_img_urls(text: str) -> list[str]:
    """从题干提取合法图片 URL 列表（委托 ingest.extract_img_urls，#33 R2）。"""
    from .ingest import extract_img_urls

    return extract_img_urls(text)


def _normalize_questions(arr: list) -> list[dict]:
    """LLM 数组 → 规范化题目列表（字段校验与缺省兜底）。
    兼容模型常见输出变体：#28 实测 gpt-5.6-luna 常输出
    qtype="单选题"/"判断题"、options 为对象 {"A":...}、difficulty="中等"，
    原实现会丢题型/丢选项/int("中等") 崩溃，这里统一归一化。"""
    if not isinstance(arr, list):
        raise ValueError("AI 返回不是题目数组")
    qtype_map = {
        "choice": "choice", "选择": "choice", "单选题": "choice", "单选": "choice",
        "选择题": "choice", "多选": "choice", "多选题": "choice",
        "judge": "judge", "判断": "judge", "判断题": "judge",
        "essay": "essay", "简答": "essay", "简答题": "essay", "主观题": "essay",
        "论述": "essay", "论述题": "essay", "材料题": "essay", "综合": "essay",
    }
    diff_map = {"很简单": 1, "简单": 1, "易": 1, "较易": 1,
                "较简单": 2, "偏易": 2,
                "中等": 3, "中": 3, "一般": 3,
                "较难": 4, "偏难": 4, "困难": 4, "难": 4,
                "很难": 5, "极难": 5, "最难": 5,
                "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
    questions = []
    for it in arr:
        if not isinstance(it, dict) or not str(it.get("question") or "").strip():
            continue
        qtype = qtype_map.get(str(it.get("qtype", "")).strip().lower(), "essay")
        # options：兼容数组 ["A. x"] 与对象 {"A": "x"}；非选择题强制空
        raw_opts = it.get("options")
        opts: list = []
        if isinstance(raw_opts, dict):
            opts = [f"{k}. {v}" for k, v in raw_opts.items() if str(v).strip()]
        elif isinstance(raw_opts, list):
            opts = [str(o).strip() for o in raw_opts if str(o).strip()]
        if qtype != "choice":
            opts = []
        # difficulty：兼容数字与中文
        dv = str(it.get("difficulty", "")).strip().lower()
        try:
            diff = max(1, min(5, int(dv or 1)))
        except (TypeError, ValueError):
            diff = diff_map.get(dv, 3)
        q = {
            "qtype": qtype,
            "question": _clean_img_marks(str(it.get("question", "")).strip()),
            "options": opts,
            "answer": str(it.get("answer", "")).strip(),
            "analysis": str(it.get("analysis", "")).strip(),
            "difficulty": diff,
            "knowledge_point": str(it.get("knowledge_point", "")).strip(),
        }
        # #35 R1：课程分类归一化（别名映射→枚举校验→非法兜底"综合"）
        from .study import normalize_category
        q["category"] = normalize_category(it.get("category")) or "综合"
        # #60：题号剥离后写入 number（整数；供采集端拼来源"…第N题"，不入库）
        try:
            q["number"] = int(it.get("number"))
        except (TypeError, ValueError):
            q["number"] = None
        # #33 R2：题干合法图片 URL 提取入 images（入库 question_bank.images 列）
        q["images"] = _extract_img_urls(q["question"])
        questions.append(q)
    return questions


def _normalize_extracted(raw: str) -> list[dict]:
    """兼容入口（旧契约）：解析失败抛 ValueError（消息含具体原因）。"""
    arr, err = _parse_llm_json_array(raw)
    if err is not None:
        raise ValueError(f"AI 输出解析失败：{err}")
    return _normalize_questions(arr)

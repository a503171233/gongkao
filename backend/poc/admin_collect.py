# -*- coding: utf-8 -*-
"""C2 后台业务逻辑：题库 AI 采集 + 分类 + 单题解析 + 文档预览（从 admin.py 拆分）。"""
import os
import re

from .config import get
from .admin_llm import (
    _split_batches, _count_question_floor, _half_split,
    _effective_llm_conf, _llm_call, _parse_llm_json_array,
    _normalize_questions,
)


# ---------- 文档预览（#16 后台重构：读取原始课件文本） ----------

def preview_document(teacher_id: str, doc_name: str, max_chars: int = 4000) -> dict:
    """读取某老师原始课件文件内容（用于后台预览）。
    文件不存在 → ValueError；只返回前 max_chars 字符。
    """
    from .config import Config
    from .ingest import extract_text
    from pathlib import Path
    cfg = Config()
    safe_name = (doc_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    path = cfg.upload_dir(teacher_id) / safe_name
    if not path.exists():
        # 也尝试直接按 teacher 数据目录查找（历史文件）
        raise ValueError(f"原始文件不存在: {safe_name}")
    try:
        text = extract_text(path)
    except ValueError as e:
        raise ValueError(str(e))
    return {
        "doc_name": safe_name,
        "teacher_id": teacher_id,
        "chars": len(text),
        "preview": text[:max_chars],
        "truncated": len(text) > max_chars,
    }


# ---------- 题库 AI 采集（#16：LLM 提取题目；#19：文件上传解析） ----------

# AI 采集可上传的格式（与 ingest.extract_text 支持面一致）
AICOLLECT_EXTS = {".md", ".txt", ".text", ".docx", ".pptx", ".pdf"}
AICOLLECT_MAX_BYTES = 50 * 1024 * 1024  # 50MB（与前端 UI 承诺、nginx client_max_body_size 对齐）


# ---------- 运行时技能层接入（《运行时AI技能与MCP能力开发说明书》§九 P0-3） ----------
# 提取 prompt 已模板化：poc/skills/prompts/_baseline-q-extract.md = 迁移前内联
# prompt 逐字节摘录（默认，行为保持）；q-extract.md = §4.1 优化模板（正向门槛+
# 注入防御），经 poc/skills/selftest 离线比对门禁后显式切换：
#   SKILL_EXTRACT_ENABLED  =0 紧急回退旧内联循环（默认 1=技能路径）
#   SKILL_EXTRACT_TEMPLATE =baseline（默认）| optimized（§4.1 新模板）
def _skill_extract_enabled() -> bool:
    return (get("SKILL_EXTRACT_ENABLED", "1") or "1").strip().lower() \
        not in ("0", "off", "false", "no")


def _extract_skill_id() -> str:
    return "q-extract"   # 运行期由 SKILL_EXTRACT_TEMPLATE 选择模板


def _llm_call_skill(material: str, teacher_subject: str, tcfg,
                    max_tokens: int = 2000, model: str | None = None,
                    temperature: float | None = None,
                    base_url: str | None = None, api_key: str | None = None):
    """技能路径提取：prompt 在 poc/skills/prompts/，解析重试在技能层，
    LLM 出口注入 _llm_call（注册表默认模型优先/全局兜底语义不变）。
    返回 SkillResult：ok=题目 list；kind=llm 时 error=底层异常原文（调用方抛转 400）。"""
    from . import skills
    return skills.run(_extract_skill_id(),
                      {"material": material, "teacher_subject": teacher_subject or "公考",
                       "allowed_types": "choice,judge,essay", "source_url": "（未提供）"},
                      tcfg=tcfg, max_tokens=max_tokens, model=model,
                      temperature=temperature, base_url=base_url, api_key=api_key,
                      llm=lambda msgs, t, **kw: _llm_call(msgs, t, **kw))


def _render_extract_system(material: str, teacher_subject: str) -> str:
    """渲染现行提取模板 system（baseline=迁移前内联 prompt 逐字节等价）。
    供 SKILL_EXTRACT_ENABLED=0 回退路径与离线比对使用。"""
    from .skills.base import get_skill, render_prompt
    system, _embedded = render_prompt(get_skill("q-extract"),
                                      {"material": material,
                                       "teacher_subject": teacher_subject or "公考",
                                       "allowed_types": "choice,judge,essay",
                                       "source_url": "（未提供）"})
    return system


def parse_collect_file(filename: str, data: bytes, teacher_id: str = "") -> dict:
    """AI 采集文件上传解析：校验格式/大小 → 临时落盘 → ingest.extract_collect。

    与「文档管理」入库同源支持面（md/txt/docx/pptx/pdf），但走采集专用解析：
    #33 R2 图片按文档流顺序内联为 ![图N](/api/qimg/{tid}/{fname}) 标记，
    图片落盘 data/qimg/{teacher_id}/（内容寻址幂等）。
    返回 {doc_name, chars, text, n_img, images}。
    """
    from .ingest import extract_collect
    import tempfile
    from pathlib import Path

    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    ext = Path(name).suffix.lower()
    if ext not in AICOLLECT_EXTS:
        raise ValueError(f"暂不支持该格式: {ext or '(无扩展名)'}（支持 md/txt/docx/pptx/pdf）")
    if not data:
        raise ValueError("文件内容为空")
    if len(data) > AICOLLECT_MAX_BYTES:
        raise ValueError(f"文件超过 50MB 上限（当前 {len(data) / 1024 / 1024:.1f}MB）")

    # 临时文件保留扩展名（extract_collect 按扩展名分发解析器）
    tmp = tempfile.NamedTemporaryFile(suffix=ext, prefix="aicollect_", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        try:
            r = extract_collect(tmp.name, teacher_id)
        except ValueError as e:
            raise ValueError(str(e).replace("入库失败:", "解析失败:"))
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    text = (r["text"] or "").strip()
    if not text and not r["n_img"]:
        raise ValueError("未能从文件中解析出文本内容")
    return {"doc_name": name, "chars": len(text), "text": text,
            "n_img": r["n_img"], "images": r["images"]}


def _is_shenlun_paper(text: str) -> bool:
    """是否申论套卷文本（含「给定资料N」且带「作答要求/申论/根据…资料」信号）。"""
    t = text or ""
    if not re.search(r"给定资料\s*\d", t):
        return False
    return bool(re.search(r"作答要求|申论|根据.{0,40}资料\s*\d", t))


def _parse_given_materials(text: str) -> dict[int, str]:
    """提取材料块 → {N: 内容}。兼容"给定资料N/给定材料N/资料N/材料N"。

    采集端正文是去标签、压缩空白后的单行长文本（无换行），不能用行首定位；
    用「前 non 参考动词」区分"材料标题"与题干里的"根据给定资料N"之类引用。
    内容截到下一材料块或作答要求。"""
    if not text:
        return {}
    # 紧邻在材料标题前的汉字若为"参考类动词"（根/据/依/由/合/读/托/绕/参…），
    # 视为题干引用（"根据给定资料N/结合材料N"），而非材料标题。
    pat = re.compile(
        r"(?<![据由合读托绕参依根定材])(?:给定资料|给定材料|资料|材料)"
        r"\s*(\d{1,2})\s*[:：]?\s*")
    matches = list(pat.finditer(text))
    if not matches:
        return {}
    mats: dict[int, str] = {}
    anq = re.search(r"作答要求", text)
    anq_start = anq.start() if anq else len(text)
    for i, m in enumerate(matches):
        num = int(m.group(1))
        end = matches[i + 1].start() if i + 1 < len(matches) else anq_start
        raw = text[m.end(): max(end, m.end())]
        content = raw.lstrip(" ：:,，。").strip()
        if content:
            mats[num] = content
    # 只保留"块较长"的材料标题，剔除误命中（如题干里的 "材料1" 引用后无大段内容）
    return {k: v for k, v in mats.items() if len(v) >= 60}


def _referenced_materials(qtext: str) -> list[int]:
    """题干中引用的给定资料编号（升序去重）。兼容"给定资料N/给定材料N/资料N/材料N"简写。"""
    return sorted({int(n)
                   for n in re.findall(r"(?:给定资料|给定材料|资料|材料)\s*(\d{1,2})", qtext or "")})


def _attach_shenlun_materials(text: str, questions: list[dict]) -> list[dict]:
    """#问题1 申论材料回填：为引用「给定资料N」的主观题，把对应资料原文
    确定性回填进 question（保证材料不被丢弃，无论分批/模型是否丢料）。
    仅对 essay 生效；无材料或非申论原样返回。"""
    mats = _parse_given_materials(text)
    if not mats:
        return questions
    for q in questions:
        qt = (q.get("question") or "").strip()
        if not qt or q.get("qtype") != "essay":
            continue
        refs = _referenced_materials(qt)
        if not refs:
            continue
        blocks = []
        for n in refs:
            if n in mats:
                blocks.append(f"【给定资料{n}】\n{mats[n]}")
        if blocks:
            q["question"] = "\n\n".join(blocks) + "\n\n" + qt
    return questions


def extract_questions_ai(text: str, teacher_subject: str = "",
                         model: str | None = None, base_url: str | None = None,
                         api_key: str | None = None) -> dict:
    """调用 LLM 从文档文本中提取题目，返回结构化 JSON。
    返回 {"questions": [{qtype, question, options, answer, analysis, difficulty, knowledge_point}]}
    """
    from . import llm
    from .config import Config

    cfg = Config()
    tcfg = cfg.get_teacher(cfg.teacher_id)  # 用默认老师配置做 LLM 参数载体
    text = (text or "").strip()
    if not text:
        raise ValueError("文档内容为空")


    # #19 长文分批：>BATCH 的文档按题号/段落边界切批逐段提取，题干去重合并
    # （修复 #16 缺陷：原先静默截断 12000 字，长文档后半部分题目全丢）
    # #25 调优：BATCH 缩至 2500（题目密集时单批 JSON 输出会超 2000 token 被截断，
    #          此前 12000 的批常整批"模型返回为空"/截断无法解析）。
    #          抽取走 max_tokens=4096；残余截断由 _salvage_json_objects 抢救，
    #          仍失败的批按 1/2 递归细分重试（深度上限 2），最大程度回收题目。
    # #35 R2：MAX_BATCHES 12→60（单请求容量 3 万→15 万字，整套试卷不再截断丢弃）；
    #          批间并发提取（网络 IO，3 并发），结果按批序回主线程去重合并。
    BATCH = 2500
    MAX_BATCHES = 60
    MAX_EXTRACT_TOKENS = 4096
    # #问题1 申论材料：申论整卷（材料 + 作答要求）一次性下发给 LLM（不外部分批），
    # 让模型在同一上下文里看到全部给定材料与作答要求，从而把引用的材料回填进题目、
    # 并产出基于材料的参考要点。仅对识别为申论且篇幅可承受的文档生效。
    shenlun_single = _is_shenlun_paper(text)
    if shenlun_single and len(text) <= 15000:
        MAX_EXTRACT_TOKENS = 9000   # 材料回填后输出量大，放宽输出上限
        BATCH = max(len(text), 2500)

    all_questions: list[dict] = []
    seen_keys: set[str] = set()

    def absorb(qs: list[dict]) -> None:
        for q in qs:
            # #28 去重键改为"题干+选项前缀"：公考客观题常出现"下列关于…正确的是"
            # 同题干不同选项的题组，按题干去重会把多题误并为 1 题；
            # 选项也相同才算边界重复（分批切点两侧提取到同一题）。
            # #33 R2 再追加"题干末 60 字"：资料分析子题 question = 共享材料 + 子题问题，
            # 前 80 字全是材料，judge/essay 无选项 → 同材料多子题曾被误并为 1 题
            # （表现为"只采集到第一题"）；子题问题位于题干末尾，末 60 字可区分。
            key = (q["question"][:80] + "|" + "".join(q.get("options") or [])[:60]
                   + "|" + q["question"][-60:])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_questions.append(q)

    # #25 问题1：模型管理页默认模型接入（未配置时回退 tcfg/老师配置）
    eff = None
    try:
        from .models import get_ai_model_store
        eff = get_ai_model_store().effective_default()
    except Exception:  # noqa: BLE001  DB 异常不阻断采集
        eff = None

    def extract_piece(piece: str, depth: int = 0) -> list[dict]:
        """单批提取；3 次重试（带原因反馈）后仍失败，则短过半递归（depth<2）。
        #29 R3 数量校验回采：解析成功但明显漏题（模型合法输出却跳过/合并题目）时，
        同样拆半重采补齐，回收"同题干被合并"与"判断题漏识别"的题目。"""
        qs: list[dict] | None = None
        err = ""
        raw = ""
        llm_kw = dict(max_tokens=MAX_EXTRACT_TOKENS,
                      model=model or (eff or {}).get("model_id"),
                      temperature=(eff or {}).get("temperature"),
                      base_url=base_url or (eff or {}).get("base_url"),
                      api_key=api_key or (eff or {}).get("api_key"))
        if _skill_extract_enabled():
            # 技能路径（默认）：prompt 在模板、解析失败带原因重试与归一化在技能层完成，
            # 消息/parser/validator/重试话术与迁移前逐字节等价（LLM 出口仍走 _llm_call）。
            r = _llm_call_skill(piece, teacher_subject, tcfg, **llm_kw)
            if r.kind == "llm":
                raise r.exc                          # 等价迁移前：_llm_call 原始异常原样上抛
            raw, err = r.raw, (r.error or "")
            if r.ok:
                qs = r.data
        else:
            # 紧急回退（SKILL_EXTRACT_ENABLED=0）：旧内联消息 + 旧重试循环，
            # system 由 baseline 模板渲染（与迁移前同一文本），行为不变。
            sys_prompt = _render_extract_system(piece, teacher_subject)
            user_prompt = f"文档内容：\n{piece}"
            for attempt in range(3):
                msgs = [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ]
                if attempt > 0:
                    msgs.append({"role": "user", "content":
                                 f"你上一次的输出无法解析（{err}）。请重新输出：必须是完整、合法、"
                                 "未截断的 JSON 数组，不要包含 JSON 以外的任何文字。"})
                raw = _llm_call(msgs, tcfg, **llm_kw)
                arr, err = _parse_llm_json_array(raw)
                if err is None:
                    qs = _normalize_questions(arr)
                    break
        if qs is not None:
            # #29 R3：漏题回采 —— 文档题目下限明显大于回收数时拆半重采补齐。
            # 原 0.6 过严（302 场景仍只回收 2）；0.85 过激进（正常场景接近完整时
            # 也拆半重采 → 重复题膨胀 18/12）。定 0.75：仅"明显漏题"触发回采，
            # 子批内仍可再回采一层，最大程度回收且不引入可见重复。
            expected = _count_question_floor(piece)
            if (expected >= 3 and len(qs) < expected * 0.75
                    and depth < 3 and len(piece) >= 400):
                extra: list[dict] = []
                for sub in _half_split(piece):
                    sub_floor = _count_question_floor(sub)
                    try:
                        sub_qs = extract_piece(sub, depth + 1)
                        # 子批仍明显漏题时再拆半重采一次取优
                        if (depth < 2 and sub_qs and sub_floor >= 2
                                and len(sub_qs) < sub_floor * 0.75):
                            try:
                                sub2 = extract_piece(sub, depth + 2)
                                if len(sub2) > len(sub_qs):
                                    sub_qs = sub2
                            except ValueError:
                                pass
                        extra.extend(sub_qs)
                    except ValueError:
                        pass
                if extra:
                    # 合并原结果 + 回采结果（重复项由外层 absorb 按题干+选项去重）
                    return qs + extra
            return qs
        # 整批失败 → 若还够长，拆半递归（防"批内题目过多/输出超限"）
        if depth < 2 and len(piece) >= 300:
            out: list[dict] = []
            for sub in _half_split(piece):
                try:
                    out.extend(extract_piece(sub, depth + 1))
                except ValueError:
                    pass
            if out:
                return out
        raise ValueError(
            f"批内题目解析失败（已自动重试 2 次）：{err}。"
            f"模型原始输出片段：{(raw or '')[:120]!r}。"
            "可尝试：拆短文档后重新提取，或改用粘贴文本方式。")

    batches = _split_batches(text, BATCH, MAX_BATCHES)
    warnings: list[str] = []
    # #35 R2：超限丢弃显式告警（不再静默）
    handled_chars = sum(len(b) for b in batches)
    if len(text) > handled_chars:
        warnings.append(
            f"文档共 {len(text)} 字，超过单次提取上限（约 {BATCH * MAX_BATCHES} 字），"
            f"后 {len(text) - handled_chars} 字未参与本次提取 — 请拆分文档分次采集补齐")
    # #35 R2：批间并发（纯网络 IO；3 并发 ≈ 22 次/分钟，低于网关 RPM 限制）
    if len(batches) > 1:
        from concurrent.futures import ThreadPoolExecutor
        results: list[list[dict] | None] = [None] * len(batches)

        def _worker(idx: int, piece: str) -> None:
            try:
                results[idx] = extract_piece(piece)
            except ValueError:
                results[idx] = None

        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = [pool.submit(_worker, i, p) for i, p in enumerate(batches)]
            for f in futs:
                f.result()
        for i, qs in enumerate(results):
            if qs is None:
                # 并发失败 → 主线程串行重试一次（保留原始报错语义）
                try:
                    absorb(extract_piece(batches[i]))
                except ValueError as e:
                    warnings.append(f"第 {i + 1}/{len(batches)} 批解析失败已跳过：{e}")
            else:
                absorb(qs)
    else:
        for bi, piece in enumerate(batches, 1):
            try:
                absorb(extract_piece(piece))
            except ValueError as e:
                raise ValueError(f"第 {bi}/{len(batches)} 批题目解析失败：{e}")

    # #35 R2：全局数量对账（估算下限 vs 实际提取数，缺口>10% 告警而非静默）
    n_expected = _count_question_floor(text)
    n_got = len(all_questions)
    if n_expected >= 3 and n_got < n_expected * 0.9:
        warnings.append(
            f"检测到原文约 {n_expected} 道题，本次实际提取 {n_got} 道，"
            "可能存在漏题 — 建议将文档拆分为更小批次重新采集补齐")
    # #问题1 申论材料回填：无论分批/模型如何，把引用的给定资料原文确定性回填进 question
    all_questions = _attach_shenlun_materials(text, all_questions)
    return {"questions": all_questions, "n_expected": n_expected,
            "warnings": warnings}


def categorize_questions_ai(teacher_id: str, qids: list[int] | None = None,
                            limit: int = 50) -> dict:
    """#35 R1 批量 AI 补充课程分类：一次 LLM 调用判定 ≤50 道题的大类并落库。
    qids 为空时自动取该老师 category 为空的题。返回 {updated, failed, total}。"""
    from . import llm
    from .study import get_study_store, normalize_category

    st = get_study_store()
    limit = max(1, min(50, int(limit or 50)))
    qs = st.list_uncategorized_questions(teacher_id, limit=limit, qids=qids)
    if not qs:
        return {"updated": 0, "failed": 0, "total": 0,
                "message": "没有待分类的题目（全部题目已有分类）"}

    tcfg, eff = _effective_llm_conf(teacher_id)
    sys_prompt = (
        "你是公考教研助手。给定题目列表（id/题型/题干摘录），为每道题判定课程大类。\n"
        "分类只能从 [\"言语理解\",\"判断推理\",\"数量关系\",\"资料分析\",\"常识判断\","
        "\"申论\",\"面试\",\"综合\"] 中选恰好一个：\n"
        "片段阅读/逻辑填空/语句表达→言语理解；图形推理/定义判断/类比推理/逻辑判断→判断推理；"
        "数学运算/数字推理→数量关系；图表资料计算→资料分析；"
        "政治/法律/科技/人文/地理常识→常识判断；大作文/概括/对策→申论；"
        "结构化面试问答→面试；无法判断→综合。\n"
        "输出严格 JSON 数组（不要任何其他文字）："
        '[{"id": 题目id, "category": "分类"}]，每道输入题目都必须恰好输出一项。'
    )
    items = []
    for q in qs:
        snippet = (q.get("question") or "").strip().replace("\n", " ")[:120]
        snippet = snippet.replace('"', "'").replace("\\", "")
        items.append(f'{{"id": {int(q["id"])}, "qtype": "{q.get("qtype") or "essay"}", '
                     f'"question": "{snippet}"}}')
    user_prompt = "题目列表：\n[" + ",\n".join(items) + "]"
    raw = _llm_call(
        [{"role": "system", "content": sys_prompt},
         {"role": "user", "content": user_prompt}],
        tcfg, max_tokens=2048,
        model=(eff or {}).get("model_id"),
        temperature=(eff or {}).get("temperature"),
        base_url=(eff or {}).get("base_url"),
        api_key=(eff or {}).get("api_key"),
    )
    arr, err = _parse_llm_json_array(raw)
    if err is not None:
        raise ValueError(f"AI 分类输出解析失败：{err}")
    id2cat: dict[int, str] = {}
    for it in arr if isinstance(arr, list) else []:
        if isinstance(it, dict) and "id" in it:
            cat = normalize_category(it.get("category"))
            if cat:
                try:
                    id2cat[int(it["id"])] = cat
                except (TypeError, ValueError):
                    pass
    updated = 0
    failed = 0
    for q in qs:
        cat = id2cat.get(int(q["id"]))
        if cat:
            try:
                st.update_question(int(q["id"]), category=cat)
                updated += 1
                continue
            except ValueError:
                pass
        failed += 1
    return {"updated": updated, "failed": failed, "total": len(qs)}


def generate_ai_analysis(q: dict) -> str:
    """#35 R3 单题多角度 AI 解析（审题/思路/选项分析/易错警示/考点归纳）。
    q 为 question_bank 行 dict；返回生成的解析文本（纯文本，含【】段落标签）。"""
    from . import llm

    tcfg, eff = _effective_llm_conf(q.get("teacher_id") or "")
    qtype = q.get("qtype") or "essay"
    type_name = {"choice": "选择题", "judge": "判断题", "essay": "简答题"}.get(qtype, "题目")
    opts = "\n".join(q.get("options") or [])
    mid_rule = ("【选项分析】逐项分析每个选项：正确的为什么对，错误的错在哪；\n"
                if qtype == "choice" else
                "【判定依据】给出判断对/错的依据；\n" if qtype == "judge" else
                "【评分要点】列出参考答案的得分点与作答组织结构；\n")
    sys_prompt = (
        "你是资深公考教研专家，擅长从多角度深入解析题目。请对给定题目输出结构化解析，"
        "使用以下固定段落标签（【】形式），输出纯文本：\n"
        "【审题】拆解题干关键信息、限定条件与设问指向；\n"
        "【思路】给出解题路径（方法/公式/切入点），说明为什么这样入手；\n"
        + mid_rule +
        "【易错警示】指出常见误区、易混淆点与命题陷阱；\n"
        "【考点归纳】总结核心考点，并延伸同类题的备考建议。\n"
        "要求：内容具体、贴合本题，不要空话套话；每个段落 2~5 句。"
    )
    user_prompt = f"题目类型：{type_name}\n题干：{q.get('question') or ''}\n"
    if opts:
        user_prompt += f"选项：\n{opts}\n"
    user_prompt += f"正确答案：{q.get('answer') or ''}\n"
    if q.get("knowledge_point"):
        user_prompt += f"参考知识点：{q['knowledge_point']}\n"
    if "![" in (q.get("question") or ""):
        user_prompt += ("（题干中的 ![图N](...) 为题目配图标记，图片内容不可见；"
                        "请按该题型的通用解题方法进行分析）\n")
    out = _llm_call(
        [{"role": "system", "content": sys_prompt},
         {"role": "user", "content": user_prompt}],
        tcfg, max_tokens=2048,
        model=(eff or {}).get("model_id"),
        temperature=(eff or {}).get("temperature"),
        base_url=(eff or {}).get("base_url"),
        api_key=(eff or {}).get("api_key"),
    )
    text = (out or "").strip()
    if not text:
        raise ValueError("AI 未返回解析内容（模型输出为空）")
    return text

# -*- coding: utf-8 -*-
"""C4 · 学员增值功能 — 收藏/错题本/练习模块（聚合入口）。

原单文件 study.py 拆分为 study_common / study_base / study_userdata /
study_questions / study_practice / study_reports；本模块保留 StudyStore 组装、
模块级单例与全量 re-export，外部 `from poc.study import ...` 用法不变。

职责：
  - 收藏：记录问答快照（含 refs），支持列表/取消
  - 错题本：记录答错问题 + 正确讲解，支持复习
  - 练习：出题/提交评分（无题库时兜底随机知识点）

数据存储：独立 study.db（SQLite 标准库），与 auth.db/chat.db/pay.db 隔离。
"""
from typing import Optional

from .study_common import (
    FAVORITE_LIMIT_FREE, FAVORITE_LIMIT_MEMBER, QUESTION_CATEGORIES,
    QUESTION_SOURCE_TYPES, REVIEW_INTERVALS_DAYS, REVIEW_MASTER_STAGE,
    SHARED_TEACHER_ID, _CATEGORY_ALIAS, _extract_imgs_from_question,
    _imgs_json, _norm_question, _norm_source, _now_iso, _parse_images,
    _teacher_scope_sql, normalize_category, normalize_source_type,
)
from .study_base import StudyStoreBase
from .study_userdata import StudyUserdataMixin
from .study_questions import StudyQuestionsMixin
from .study_practice import StudyPracticeMixin
from .study_reports import StudyReportsMixin


class StudyStore(StudyStoreBase, StudyUserdataMixin, StudyQuestionsMixin,
                 StudyPracticeMixin, StudyReportsMixin):
    """学习数据存储（收藏/错题本/练习）。"""


# 模块级单例
_study_store: Optional[StudyStore] = None


def get_study_store() -> StudyStore:
    global _study_store
    if _study_store is None:
        _study_store = StudyStore()
    return _study_store

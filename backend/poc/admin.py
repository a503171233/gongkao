# -*- coding: utf-8 -*-
"""C2 后台业务逻辑聚合入口（原 admin.py 拆分后的兼容层）。

外部代码通过 `from poc import admin as admin_biz` 访问，本模块保持所有
函数/常量符号可见且行为不变；实现已迁至 admin_*.py 子模块。
"""
from .admin_teachers import (
    COURSE_CATEGORIES, COMPANIES, _validate_enum,
    list_teachers_admin, _validate_teacher_payload, create_teacher,
    update_teacher, disable_teacher, enable_teacher, sort_teachers,
)
from .admin_users import (
    list_users_admin, get_user_admin, set_user_status, user_stats_admin,
    set_user_role, grant_user, admin_stats,
    list_recharge_codes, count_recharge_codes, generate_recharge_codes_admin,
    reset_user_password_admin, create_user_admin, delete_user_admin,
)
from .admin_llm import (
    _QNUM_LINE_RE, _split_batches, _count_question_floor, _half_split,
    _effective_llm_conf, _llm_call,
    _parse_llm_json_array, _salvage_json_objects, _clean_img_marks,
    _extract_img_urls, _normalize_questions, _normalize_extracted,
)
from .admin_collect import (
    AICOLLECT_EXTS, AICOLLECT_MAX_BYTES,
    _skill_extract_enabled, _extract_skill_id, _llm_call_skill,
    _render_extract_system, parse_collect_file, preview_document,
    _is_shenlun_paper, _parse_given_materials, _referenced_materials,
    _attach_shenlun_materials, extract_questions_ai,
    categorize_questions_ai, generate_ai_analysis,
)
from .admin_knowledge import (
    enhance_mindmap, _node_path, _count_nodes, _parse_mindmap_enhance,
    BUILD_TREE_MAX_CHARS, _BUILD_TREE_MAX_NODES, _BUILD_TREE_MAX_DEPTH,
    _llm_build_knowledge_tree, _build_tree_tcfg, _aim_eff,
    _create_nodes_recursive, build_tree_from_document, _count_preview_nodes,
    enhance_tree_from_search,
)
from .admin_backup import (
    list_backups_admin, create_backup_admin, restore_backup_admin,
    backup_disk_usage,
)

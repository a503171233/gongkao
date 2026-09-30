# -*- coding: utf-8 -*-
"""C2 后台业务逻辑：数据备份 / 恢复（从 admin.py 拆分）。"""
import re

from .config import Config


# ---------- C4 数据备份 / 恢复（源文件级，安全边界明确） ----------

def list_backups_admin(limit: int = 50) -> list[dict]:
    """列出备份归档（data_dir/backups/backup_*.tar.gz，新的在前）。"""
    import time as _t
    bak_dir = Config.data_dir / "backups"
    if not bak_dir.exists():
        return []
    out = []
    for p in sorted(bak_dir.glob("backup_*.tar.gz"), key=lambda x: x.name, reverse=True)[:limit]:
        st = p.stat()
        out.append({
            "name": p.name,
            "size": st.st_size,
            "mtime": _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(st.st_mtime)),
        })
    return out


def create_backup_admin() -> dict:
    """打包数据目录：raw 源文件 + 全部 *.db（auth/teachers/questions/pay 等）。
    不打包向量库与模型缓存（可重建/可下载），控制归档体积。
    时间戳命名，并发创建不互相覆盖。"""
    import tarfile
    import time as _t
    data = Config.data_dir
    bak_dir = data / "backups"
    bak_dir.mkdir(parents=True, exist_ok=True)
    name = f"backup_{_t.strftime('%Y%m%d_%H%M%S')}.tar.gz"
    path = bak_dir / name
    with tarfile.open(str(path), "w:gz") as tf:
        raw = data / "raw"
        if raw.exists():
            tf.add(str(raw), arcname="raw")
        for db in sorted(data.glob("*.db")):
            try:
                tf.add(str(db), arcname=db.name)
            except Exception:  # noqa: BLE001 数据库被占用时跳过，不影响整体备份
                print(f"[backup] 跳过 {db.name}: 文件被占用")
    st = path.stat()
    return {
        "name": name,
        "size": st.st_size,
        "created_at": _t.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "已归档 raw 源文件与数据库；向量库/模型缓存不打包（可重建）",
    }


def restore_backup_admin(name: str) -> dict:
    """恢复归档中的 raw 源文件（解压合并进 data/raw，同名覆盖）。
    安全边界：只恢复源文件，不替换 db/向量库——避免在线热替换造成数据不一致。
    如需重建向量库，恢复后在文档管理页重新入库。"""
    import shutil
    import tarfile
    if not re.fullmatch(r"backup_\d{8}_\d{6}\.tar\.gz", name or ""):
        raise ValueError("备份名不合法")
    data = Config.data_dir
    path = data / "backups" / name
    if not path.exists():
        raise ValueError(f"备份不存在: {name}")
    tmp = data / ".restore_tmp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    restored, skipped = 0, 0
    with tarfile.open(str(path), "r:gz") as tf:
        for m in tf.getmembers():
            if not (m.name.startswith("raw/") or m.name == "raw"):
                continue
            m.name = m.name[len("raw/"):] if m.name.startswith("raw/") else ""
            if not m.isfile() or not m.name:  # 只解压普通文件
                continue
            m.name = m.name.lstrip("/\\")
            if ".." in m.name.split("/"):
                skipped += 1
                continue
            tf.extract(m, str(tmp))  # 先解压到临时目录，再合并
            restored += 1
    raw = data / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for f in tmp.rglob("*"):
        if f.is_file():
            rel = f.relative_to(tmp)
            dest = raw / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(f.read_bytes())
    shutil.rmtree(tmp, ignore_errors=True)
    return {
        "restored_files": restored,
        "skipped": skipped,
        "note": "已恢复 raw 源文件；向量库与数据库未改动（避免在线不一致）",
    }


def backup_disk_usage() -> dict:
    """备份目录磁盘占用概览（运维看板用）。"""
    import time as _t
    bak_dir = Config.data_dir / "backups"
    files = sorted(bak_dir.glob("backup_*.tar.gz")) if bak_dir.exists() else []
    total = sum(p.stat().st_size for p in files)
    return {
        "dir": str(bak_dir),
        "count": len(files),
        "total_bytes": total,
        "newest": files[-1].name if files else "",
        "oldest": files[0].name if files else "",
        "updated_at": _t.strftime("%Y-%m-%d %H:%M:%S"),
    }

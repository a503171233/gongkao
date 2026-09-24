# -*- coding: utf-8 -*-
"""统一测试 runner（#8 测试体系整理）。

用法：
    cd backend
    python tests/run_tests.py                 # 运行全部脚本风格单测
    python tests/run_tests.py test_auth.py    # 运行指定测试（可多个）

说明：
  - 脚本风格测试（test_*.py 顶层执行 + sys.exit/assert）逐个 subprocess 运行，
    隔离彼此的环境变量/临时目录/SystemExit，聚合 PASS/FAIL 统计。
  - 真 pytest 风格测试（test_36_practice.py / test_mockexam.py）由 pytest 收集，
    本 runner 不重复运行，提示用 `python -m pytest`。
  - 线上冒烟测试（online_smoke.py / online_kernel_8mods.py）需真实服务器，
    默认不跑，可用 `--online` 显式包含。
"""
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
TESTS = BACKEND / "tests"

PYTEST_STYLE = {"test_36_practice.py", "test_mockexam.py"}
ONLINE_TESTS = {"online_smoke.py", "online_kernel_8mods.py"}


def discover(targets: list[str] | None = None, include_online: bool = False) -> list[Path]:
    """发现脚本风格测试文件（排除 pytest 风格与线上冒烟）。"""
    if targets:
        return [TESTS / t for t in targets if (TESTS / t).exists()]
    out = []
    for f in sorted(TESTS.glob("test_*.py")):
        if f.name in PYTEST_STYLE:
            continue
        out.append(f)
    if include_online:
        for name in sorted(ONLINE_TESTS):
            out.append(TESTS / name)
    return out


def main() -> int:
    args = sys.argv[1:]
    include_online = "--online" in args
    targets = [a for a in args if not a.startswith("--")]
    files = discover(targets or None, include_online)

    if not files:
        print("未发现可运行的测试文件。")
        return 1

    print(f"===== 脚本风格单测 · 共 {len(files)} 个 =====")
    passed, failed, errors = [], [], []
    t0 = time.time()
    for f in files:
        name = f.name
        t = time.time()
        try:
            r = subprocess.run(
                [sys.executable, str(f)],
                cwd=str(BACKEND),
                capture_output=True,
                text=True,
                timeout=300,
            )
            if r.returncode == 0:
                passed.append(name)
                print(f"  ✅ {name:40} {(time.time()-t):5.1f}s")
            else:
                failed.append(name)
                tail = (r.stdout or r.stderr).strip().splitlines()
                print(f"  ❌ {name:40} {(time.time()-t):5.1f}s  {tail[-1] if tail else ''}")
        except subprocess.TimeoutExpired:
            errors.append(name)
            print(f"  ⏱️ {name:40} 超时(>300s)")
        except Exception as e:  # noqa: BLE001
            errors.append(name)
            print(f"  💥 {name:40} 异常: {e}")

    print("\n===== 汇总 =====")
    print(f"  通过 {len(passed)} · 失败 {len(failed)} · 异常 {len(errors)} · 总耗时 {time.time()-t0:.1f}s")
    if failed:
        print("  失败清单:", ", ".join(failed))
    if errors:
        print("  异常清单:", ", ".join(errors))
    if not PYTEST_STYLE.isdisjoint(set()):
        pass
    print("  提示: pytest 风格测试请运行 `python -m pytest`")
    return 0 if not failed and not errors else 1


if __name__ == "__main__":
    sys.exit(main())

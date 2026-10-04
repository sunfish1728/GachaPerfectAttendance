"""建立離線安裝用的完整程式包（runtime/build/stage），供 Inno Setup 打包。

內容：
  python/   獨立的 CPython 3.12（從 .local/python 複製、去掉用不到的部分），套件直接裝在 python/Lib/site-packages
  src/ assets/ adapters/ start.bat README.md
python/Lib/site-packages/gachahub.pth 以相對路徑指向 src，整個資料夾可搬移到任何位置。

PySide6 只裝 Essentials：QFluentWidgets 相依的 PySide6 總套件會帶入 Addons（瀏覽器引擎等，數百 MB），本程式用不到。

用法：.venv/Scripts/python.exe scripts/build_bundle.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "runtime" / "build" / "stage"
UV = shutil.which("uv") or str(ROOT / ".local" / "uv" / "uv.exe")

# 從標準 Python 移除：GUI 工具包、測試、開發標頭、pip（執行時都用不到）
PY_SKIP_DIRS = {"tcl", "include", "libs", "Scripts"}
PY_SKIP_LIB = {"test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data"}
PY_SKIP_FILES = {"_tkinter.pyd", "tcl86t.dll", "tk86t.dll"}


def find_python() -> Path:
    base = ROOT / ".local" / "python"
    found = sorted(p for p in base.glob("cpython-3.12.*-windows-x86_64-none") if p.is_dir() and not p.is_symlink())
    if not found:
        raise SystemExit("找不到 .local/python/cpython-3.12.*；請先載入 scripts/env.ps1 並執行 uv python install 3.12")
    return found[-1]


def copy_python(src: Path, dst: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        d = Path(directory)
        skip = {n for n in names if n == "__pycache__" or n in PY_SKIP_FILES}
        if d == src:
            skip |= {n for n in names if n in PY_SKIP_DIRS}
        if d == src / "Lib":
            skip |= {n for n in names if n in PY_SKIP_LIB or n == "EXTERNALLY-MANAGED"}
        if d == src / "Lib" / "site-packages":
            skip |= {n for n in names if n.startswith("pip")}
        return skip

    shutil.copytree(src, dst, ignore=ignore)


def requirements() -> list[str]:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    return [*deps, "PySide6-Essentials"]


def run(cmd: list[str], **kw) -> None:
    print(">", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def main() -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    py_dir = STAGE / "python"
    copy_python(find_python(), py_dir)
    py = py_dir / "python.exe"

    # 所有快取限制在專案資料夾內
    env = dict(os.environ)
    env.update({
        "UV_CACHE_DIR": str(ROOT / ".local" / "uv-cache"),
        "UV_PYTHON_PREFERENCE": "only-system",
        "UV_NO_CONFIG": "1",
        "PYTHONPYCACHEPREFIX": "",
    })
    overrides = STAGE.parent / "overrides.txt"
    overrides.write_text('pyside6 ; sys_platform == "never"\n', encoding="utf-8")  # 不安裝 PySide6 總套件（含 Addons）
    run([UV, "pip", "install", "--python", str(py), "--break-system-packages", "--compile-bytecode",
         "--override", str(overrides), *requirements()], env=env)

    site = py_dir / "Lib" / "site-packages"
    (site / "gachahub.pth").write_text("../../../src\n", encoding="utf-8")

    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".backup")
    shutil.copytree(ROOT / "src", STAGE / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"))
    shutil.copytree(ROOT / "adapters", STAGE / "adapters", ignore=ignore)
    (STAGE / "assets").mkdir()
    for name in ("icon.ico", "icon.png"):
        shutil.copy2(ROOT / "assets" / name, STAGE / "assets" / name)
    for name in ("start.bat", "README.md"):
        shutil.copy2(ROOT / name, STAGE / name)
    run([str(py), "-m", "compileall", "-q", str(STAGE / "src")])

    # 驗證：只用程式包內的 Python 匯入整個介面
    check = ("import gachahub, PySide6, qfluentwidgets, gachahub.gui.main_window, gachahub.adapters.onedragon,"
             " gachahub.adapters.ok_script, gachahub.hooks.audio; print('ok', gachahub.__version__, PySide6.__version__)")
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "UV_", "VIRTUAL_ENV"))}
    clean_env["QT_QPA_PLATFORM"] = "offscreen"
    run([str(py), "-c", check], env=clean_env, cwd=str(STAGE))
    addons = list(site.glob("PySide6/Qt6WebEngine*"))
    if addons:
        raise SystemExit(f"不應包含 PySide6 Addons：{addons[:3]}")
    total = sum(f.stat().st_size for f in STAGE.rglob("*") if f.is_file())
    print(f"程式包完成：{STAGE}（{total / 1024 / 1024:.0f} MB）")


if __name__ == "__main__":
    sys.exit(main())

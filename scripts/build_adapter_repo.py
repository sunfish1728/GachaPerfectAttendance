"""產生可發布的宣告式適配器倉庫。"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gachahub.core.adapter_repo import RepoError, build_index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "adapter-repo")
    args = parser.parse_args()
    output = args.out_dir.resolve()
    if not output.is_relative_to(ROOT):
        parser.error("輸出必須位於專案資料夾內")
    try:
        print(f"已產生倉庫：{build_index(ROOT / 'adapters', output)}")
        return 0
    except RepoError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

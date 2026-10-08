"""PyInstaller の出力フォルダに設定例・説明書を追加し、zip にまとめる。

使い方: python packaging/finalize_dist.py [dist フォルダ]
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
APP = DIST / "PdfAutoMarker"


def main() -> int:
    if not APP.is_dir():
        print(f"ビルド結果が見つかりません: {APP}", file=sys.stderr)
        return 1
    shutil.copy2(ROOT / "examples" / "rules.yaml", APP / "rules.yaml")
    sample_dir = APP / "sample"
    sample_dir.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "examples" / "sample.pdf", sample_dir / "sample.pdf")
    # メモ帳で文字化けしないよう BOM 付き UTF-8 で書き出す
    text = (ROOT / "packaging" / "README_windows.txt").read_text(encoding="utf-8")
    (APP / "README.txt").write_text(text, encoding="utf-8-sig", newline="\r\n")

    archive = shutil.make_archive(str(DIST / "PdfAutoMarker-win64"), "zip", DIST, APP.name)
    print(f"作成しました: {APP}")
    print(f"作成しました: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

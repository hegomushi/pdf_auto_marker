"""CI 用: ビルドした exe の出力 PDF を検証する。

使い方: python packaging/smoke_check.py 元.pdf 焼き込み版.pdf 注釈版.pdf
"""

import sys

import pymupdf


def main(src: str, flat: str, annot: str) -> int:
    with pymupdf.open(src) as s, pymupdf.open(flat) as f, pymupdf.open(annot) as a:
        n_flat = len(list(f[0].annots()))
        n_annot = len(list(a[0].annots()))
        changed = s[0].get_pixmap(dpi=50).samples != f[0].get_pixmap(dpi=50).samples
    print(f"flattened: annotations={n_flat}, page changed={changed}")
    print(f"annotation mode: annotations={n_annot}")
    assert n_flat == 0, "焼き込み版に注釈が残っています"
    assert changed, "焼き込み版にマーキングが描画されていません"
    assert n_annot == 7, "注釈版の注釈数が想定と異なります"
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:4]))

"""動作確認用のサンプル PDF (罫線付きの表) を作成する。

使い方: python tools/make_sample_pdf.py examples/sample.pdf
必要: reportlab (pip install reportlab)
"""

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import ParagraphStyle

FONT = "HeiseiKakuGo-W5"

DATA = [
    ["No", "品名", "数量", "検査結果", "備考"],
    ["1", "ボルト M6", "120", "合格", ""],
    ["2", "ナット M6", "80", "不合格", "要確認"],
    ["3", "ワッシャー", "200", "合格", "在庫少"],
    ["4", "スプリング", "15", "保留", "要確認 (再検査)"],
    ["5", "ボルト M8", "60", "不合格", "NG 返品"],
    ["6", "ブラケット", "30", "合格", "ok"],
]


def build(path: Path) -> None:
    pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    style = ParagraphStyle("t", fontName=FONT, fontSize=14)
    tstyle = TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("GRID", (0, 0), (-1, -1), 0.7, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ])
    story = [Paragraph("検査結果一覧 (サンプル)", style), Spacer(1, 12)]
    t = Table(DATA, colWidths=[30, 110, 50, 70, 140], rowHeights=22)
    t.setStyle(tstyle)
    story += [t, Spacer(1, 24), Paragraph("表の外の 不合格 / 要確認 はマーキングされません", style)]
    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(path), pagesize=A4).build(story)


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "examples/sample.pdf"))

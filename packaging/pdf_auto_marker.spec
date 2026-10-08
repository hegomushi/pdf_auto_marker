# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 設定: GUI 版とコマンドライン版の 2 つの exe を 1 つのフォルダにまとめる。
#   pyinstaller --noconfirm --clean packaging/pdf_auto_marker.spec
# 出力: dist/PdfAutoMarker/
from pathlib import Path

ROOT = Path(SPECPATH).parent
EXCLUDES = ["pytest", "reportlab", "PIL", "numpy", "pandas", "matplotlib", "IPython"]

gui_a = Analysis(
    [str(ROOT / "packaging" / "entry_gui.py")],
    pathex=[str(ROOT)],
    excludes=EXCLUDES,
)
cli_a = Analysis(
    [str(ROOT / "packaging" / "entry_cli.py")],
    pathex=[str(ROOT)],
    excludes=EXCLUDES + ["tkinter", "_tkinter"],
)

gui_exe = EXE(
    PYZ(gui_a.pure),
    gui_a.scripts,
    [],
    exclude_binaries=True,
    name="PdfAutoMarker",
    console=False,   # GUI 版はコンソール画面を出さない
    upx=False,
)
cli_exe = EXE(
    PYZ(cli_a.pure),
    cli_a.scripts,
    [],
    exclude_binaries=True,
    name="pdf-auto-marker",
    console=True,
    upx=False,
)

coll = COLLECT(
    gui_exe, gui_a.binaries, gui_a.datas,
    cli_exe, cli_a.binaries, cli_a.datas,
    upx=False,
    name="PdfAutoMarker",
)

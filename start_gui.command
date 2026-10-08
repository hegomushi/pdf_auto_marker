#!/bin/sh
# PDF 自動マーキング GUI を起動します (macOS: ダブルクリック / Linux: ./start_gui.command)
cd "$(dirname "$0")"
exec python3 -m pdf_auto_marker.gui "$@"

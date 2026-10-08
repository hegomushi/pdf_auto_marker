"""コマンドライン インターフェース。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .marker import inspect_pdf, mark_pdf


def _collect_inputs(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files.extend(sorted(x for x in p.iterdir() if x.suffix.lower() == ".pdf"))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(f"入力が見つかりません: {p}")
    return files


def _output_for(src: Path, output: str | None, multiple: bool) -> Path:
    if output is None:
        return src.with_name(f"{src.stem}_marked.pdf")
    out = Path(output)
    if multiple or out.is_dir() or output.endswith(("/", "\\")):
        return out / f"{src.stem}_marked.pdf"
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf-auto-marker",
        description="PDF 内の表を検出し、指定した単語・行を自動でマーキングします (ローカル処理のみ)。",
    )
    p.add_argument("inputs", nargs="+", help="入力 PDF ファイル、または PDF を含むフォルダ")
    p.add_argument("-c", "--config", help="ルール設定ファイル (YAML / JSON)")
    p.add_argument("-o", "--output",
                   help="出力ファイル (入力が複数の場合は出力フォルダ)。省略時は <元の名前>_marked.pdf")
    p.add_argument("-p", "--pages", help="対象ページ (例: 1-3,5)。設定ファイルの pages より優先")
    p.add_argument("--inspect", action="store_true",
                   help="マーキングせず、検出した表の行・列とセル内容を表示する (列番号の確認用)")
    p.add_argument("--strategy", choices=["lines", "lines_strict", "text"],
                   help="表の検出方法 (--inspect 時、または設定ファイルの指定を上書き)")
    p.add_argument("--keep-previous", action="store_true",
                   help="以前このツールで付けたマーキングを削除せずに残す")
    p.add_argument("-v", "--verbose", action="store_true", help="マーキング箇所を 1 件ずつ表示する")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        inputs = _collect_inputs(args.inputs)
        if not inputs:
            print("処理対象の PDF がありません", file=sys.stderr)
            return 1

        if args.inspect:
            for src in inputs:
                print(f"=== {src} ===")
                print(inspect_pdf(src, strategy=args.strategy or "lines", pages=args.pages))
            return 0

        if not args.config:
            print("エラー: -c/--config でルール設定ファイルを指定してください", file=sys.stderr)
            return 2
        config = load_config(args.config)
        if args.strategy:
            config.table_strategy = args.strategy

        multiple = len(inputs) > 1
        for src in inputs:
            dst = _output_for(src, args.output, multiple)
            result = mark_pdf(src, dst, config, pages=args.pages,
                              clear_previous=not args.keep_previous)
            print(f"{src} -> {dst}")
            print(f"  処理ページ: {result.pages_processed}, 検出した表: {result.tables_found}, "
                  f"マーキング: {len(result.hits)} 件")
            for rule, n in result.count_by_rule().items():
                print(f"    {rule}: {n} 件")
            if args.verbose:
                for h in result.hits:
                    kind = "単語" if h.kind == "word" else "行"
                    print(f"    [{kind}] p{h.page} 表{h.table} 行{h.row} 列{h.column}: "
                          f"{h.text!r} ({h.rule})")
            if result.tables_found == 0:
                print("  注意: 表が検出されませんでした。罫線のない表は --strategy text を試してください")
        return 0
    except (ConfigError, FileNotFoundError, ValueError) as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2

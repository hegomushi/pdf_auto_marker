"""PDF 内の表を検出し、ルールに従ってマーキング (ハイライト注釈) を付ける。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Union

import pymupdf

from .config import Config, MatchOptions, RowRule, WordRule

ANNOT_TITLE = "pdf_auto_marker"

# find_tables 実行時に出る pymupdf_layout の案内メッセージを抑止する
if hasattr(pymupdf, "no_recommend_layout"):
    pymupdf.no_recommend_layout()


# --------------------------------------------------------------------------
# 文字単位のテキストモデル
# --------------------------------------------------------------------------

@dataclass
class Char:
    c: str
    rect: pymupdf.Rect
    line_id: int


def page_chars(page: pymupdf.Page) -> list[Char]:
    """ページ内の全文字を、位置情報付きで読み順に取得する。"""
    chars: list[Char] = []
    line_id = 0
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            line_id += 1
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    r = pymupdf.Rect(ch["bbox"])
                    if r.is_empty:
                        continue
                    chars.append(Char(ch["c"], r, line_id))
    return chars


def chars_in_rect(chars: Iterable[Char], rect: pymupdf.Rect) -> list[Char]:
    """文字の中心点が rect 内にある文字を返す。"""
    out = []
    for ch in chars:
        cx = (ch.rect.x0 + ch.rect.x1) / 2
        cy = (ch.rect.y0 + ch.rect.y1) / 2
        if rect.x0 <= cx <= rect.x1 and rect.y0 <= cy <= rect.y1:
            out.append(ch)
    return out


def _normalize_char(c: str, opts: MatchOptions) -> str:
    if opts.normalize:
        c = unicodedata.normalize("NFKC", c)
    if not opts.case_sensitive:
        c = c.casefold()
    return c


def normalize_text(s: str, opts: MatchOptions) -> str:
    return "".join(_normalize_char(c, opts) for c in s)


class CellText:
    """セル内の文字列 (正規化済み) と、元の文字への対応表。

    セル内で折り返された行は区切り文字なしで連結する (日本語の折り返しを想定)。
    """

    def __init__(self, chars: list[Char], opts: MatchOptions):
        self.chars = chars
        pieces: list[str] = []
        self.index_map: list[int] = []  # 正規化後の各文字 -> 元の chars のインデックス
        for i, ch in enumerate(chars):
            n = _normalize_char(ch.c, opts)
            pieces.append(n)
            self.index_map.extend([i] * len(n))
        self.text = "".join(pieces)

    @property
    def raw_text(self) -> str:
        return "".join(ch.c for ch in self.chars)

    def find(self, patterns: list[str], opts: MatchOptions) -> list[tuple[int, int]]:
        """マッチした範囲を、元の chars のインデックス範囲 [start, end) で返す。"""
        spans: list[tuple[int, int]] = []
        text = self.text
        if not text:
            return spans
        if opts.mode == "exact":
            stripped = text.strip()
            if any(stripped == normalize_text(p, opts).strip() for p in patterns):
                return [(0, len(self.chars))]
            return spans
        for p in patterns:
            if opts.mode == "regex":
                flags = 0 if opts.case_sensitive else re.IGNORECASE
                pat = p
                if opts.normalize:
                    pat = unicodedata.normalize("NFKC", pat)
                it = (m.span() for m in re.finditer(pat, text, flags) if m.end() > m.start())
            else:
                needle = normalize_text(p, opts)
                it = ((m.start(), m.end()) for m in re.finditer(re.escape(needle), text))
            for s, e in it:
                spans.append((self.index_map[s], self.index_map[e - 1] + 1))
        return spans

    def rects_for(self, start: int, end: int) -> list[pymupdf.Rect]:
        """文字範囲を、行ごとにまとめた矩形のリストに変換する。"""
        rects: list[pymupdf.Rect] = []
        cur_line = None
        for ch in self.chars[start:end]:
            if ch.line_id != cur_line or not rects:
                rects.append(pymupdf.Rect(ch.rect))
                cur_line = ch.line_id
            else:
                rects[-1] |= ch.rect
        return rects


# --------------------------------------------------------------------------
# 表の解析
# --------------------------------------------------------------------------

@dataclass
class TableInfo:
    index: int
    bbox: pymupdf.Rect
    rows: list[list[Optional[pymupdf.Rect]]]   # rows[r][c] = セル矩形 (結合セルは None)
    row_bboxes: list[pymupdf.Rect]
    col_bounds: list[tuple[float, float]]       # 各列の (左端, 右端)

    @property
    def col_count(self) -> int:
        return len(self.col_bounds)

    def resolve_columns(self, spec: Optional[tuple[int, int]]) -> Optional[range]:
        """1始まり/負数可の列範囲指定を 0 始まりの range に変換する。範囲外なら None。"""
        n = self.col_count
        if spec is None:
            return range(n)
        start, end = (s - 1 if s > 0 else n + s for s in spec)
        if start > end:
            start, end = end, start
        start, end = max(start, 0), min(end, n - 1)
        if start > end:
            return None
        return range(start, end + 1)


def find_tables(page: pymupdf.Page, strategy: str = "lines") -> list[TableInfo]:
    """ページ内の表を検出する。"""
    if strategy == "text":
        kwargs = {"vertical_strategy": "text", "horizontal_strategy": "text"}
    else:
        kwargs = {"strategy": strategy}
    tables: list[TableInfo] = []
    for t_idx, tbl in enumerate(page.find_tables(**kwargs).tables):
        rows = [[pymupdf.Rect(c) if c else None for c in row.cells] for row in tbl.rows]
        row_bboxes = [pymupdf.Rect(row.bbox) for row in tbl.rows]
        bbox = pymupdf.Rect(tbl.bbox)
        ncols = max((len(r) for r in rows), default=0)
        col_bounds: list[tuple[float, float]] = []
        for c in range(ncols):
            xs = [r[c] for r in rows if c < len(r) and r[c] is not None]
            if xs:
                col_bounds.append((min(x.x0 for x in xs), max(x.x1 for x in xs)))
            else:
                col_bounds.append((bbox.x0, bbox.x1))
        tables.append(TableInfo(t_idx, bbox, rows, row_bboxes, col_bounds))
    return tables


# --------------------------------------------------------------------------
# マーキング
# --------------------------------------------------------------------------

@dataclass
class Hit:
    rule: str
    kind: str        # "word" / "row"
    page: int        # 1 始まり
    table: int       # 1 始まり
    row: int         # 1 始まり
    column: Optional[int]  # 1 始まり (行マーキングの場合はキーワードが見つかった列)
    text: str


@dataclass
class MarkResult:
    hits: list[Hit] = field(default_factory=list)
    tables_found: int = 0
    pages_processed: int = 0
    already_flattened: bool = False  # 入力が既にこのツールで焼き込み済みだった

    def count_by_rule(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for h in self.hits:
            counts[h.rule] = counts.get(h.rule, 0) + 1
        return counts


def parse_pages(spec: Optional[str], page_count: int) -> list[int]:
    """"1-3,5" 形式のページ指定を 0 始まりのページ番号リストに変換する。"""
    if not spec:
        return list(range(page_count))
    pages: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)?\s*-\s*(\d+)?", part)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else page_count
        elif part.isdigit():
            a = b = int(part)
        else:
            raise ValueError(f"ページ指定を解釈できません: {part!r}")
        pages.update(p - 1 for p in range(a, b + 1) if 1 <= p <= page_count)
    return sorted(pages)


def _add_highlight(page: pymupdf.Page, rects: list[pymupdf.Rect], color, opacity: float, label: str):
    annot = page.add_highlight_annot(rects)
    annot.set_colors(stroke=color)
    annot.set_opacity(opacity)
    annot.set_info(title=ANNOT_TITLE, content=label)
    annot.update()
    return annot


def _add_box(page: pymupdf.Page, rect: pymupdf.Rect, color, opacity: float, label: str):
    """枠線なしの塗りつぶし矩形注釈 (乗算合成で下の文字が透ける)。"""
    annot = page.add_rect_annot(rect)
    annot.set_colors(stroke=color, fill=color)
    annot.set_border(width=0)
    annot.set_opacity(opacity)
    annot.set_blendmode(pymupdf.PDF_BM_Multiply)
    annot.set_info(title=ANNOT_TITLE, content=label)
    annot.update()
    return annot


def clear_marks(page: pymupdf.Page) -> int:
    """このツールが過去に付けた注釈を削除する。"""
    removed = 0
    for annot in list(page.annots() or []):
        if annot.info.get("title") == ANNOT_TITLE:
            page.delete_annot(annot)
            removed += 1
    return removed


def _apply_word_rule(page, pno, table: TableInfo, chars, rule: WordRule, result: MarkResult):
    cols = table.resolve_columns(rule.columns)
    if cols is None:
        return
    for r_idx, row in enumerate(table.rows):
        for c_idx in cols:
            if c_idx >= len(row) or row[c_idx] is None:
                continue
            cell = CellText(chars_in_rect(chars, row[c_idx]), rule.match)
            for s, e in cell.find(rule.words, rule.match):
                rects = cell.rects_for(s, e)
                if not rects:
                    continue
                matched = "".join(ch.c for ch in cell.chars[s:e])
                _add_highlight(page, rects, rule.color, rule.opacity, f"{rule.name}: {matched}")
                result.hits.append(Hit(rule.name, "word", pno + 1, table.index + 1,
                                       r_idx + 1, c_idx + 1, matched))


def _row_mark_rect(table: TableInfo, r_idx: int, rule: RowRule) -> Optional[pymupdf.Rect]:
    row_box = table.row_bboxes[r_idx]
    if rule.x_range is not None:
        x0, x1 = rule.x_range
    elif rule.mark_columns is not None:
        cols = table.resolve_columns(rule.mark_columns)
        if cols is None:
            return None
        x0 = table.col_bounds[cols.start][0]
        x1 = table.col_bounds[cols.stop - 1][1]
    else:
        x0, x1 = table.bbox.x0, table.bbox.x1
    p = rule.padding
    return pymupdf.Rect(x0 - p, row_box.y0 - p, x1 + p, row_box.y1 + p)


def _apply_row_rule(page, pno, table: TableInfo, chars, rule: RowRule, result: MarkResult):
    cols = table.resolve_columns(rule.search_columns)
    if cols is None:
        return
    for r_idx, row in enumerate(table.rows):
        found: Optional[tuple[int, str]] = None
        for c_idx in cols:
            if c_idx >= len(row) or row[c_idx] is None:
                continue
            cell = CellText(chars_in_rect(chars, row[c_idx]), rule.match)
            spans = cell.find(rule.keywords, rule.match)
            if spans:
                s, e = spans[0]
                found = (c_idx, "".join(ch.c for ch in cell.chars[s:e]))
                break
        if found is None:
            continue
        rect = _row_mark_rect(table, r_idx, rule)
        if rect is None or rect.is_empty:
            continue
        _add_box(page, rect, rule.color, rule.opacity, f"{rule.name}: {found[1]}")
        result.hits.append(Hit(rule.name, "row", pno + 1, table.index + 1,
                               r_idx + 1, found[0] + 1, found[1]))


def mark_document(doc: pymupdf.Document, config: Config, pages: Optional[str] = None,
                  clear_previous: bool = True) -> MarkResult:
    """開いている Document にマーキングを付ける (保存は呼び出し側で行う)。

    行マーキングを先に付け、単語マーキングをその上に重ねる。
    """
    result = MarkResult()
    for pno in parse_pages(pages if pages is not None else config.pages, doc.page_count):
        page = doc[pno]
        if clear_previous:
            clear_marks(page)
        # 回転ページでも座標系を揃えるため、一時的に回転を 0 にして処理する
        rotation = page.rotation
        if rotation:
            page.set_rotation(0)
        try:
            tables = find_tables(page, config.table_strategy)
            result.pages_processed += 1
            result.tables_found += len(tables)
            if not tables:
                continue
            chars = page_chars(page)
            for table in tables:
                for rule in config.row_rules:
                    _apply_row_rule(page, pno, table, chars, rule, result)
                for rule in config.word_rules:
                    _apply_word_rule(page, pno, table, chars, rule, result)
        finally:
            if rotation:
                page.set_rotation(rotation)
    return result


# 焼き込み済みの PDF であることを示すカタログのキー (再実行時の重ね塗り警告用)
_FLATTENED_KEY = "PdfAutoMarkerFlattened"


def _annots_refs(doc: pymupdf.Document, page: pymupdf.Page) -> list[int]:
    """ページの /Annots 配列に含まれる注釈の xref を返す。"""
    kind, value = doc.xref_get_key(page.xref, "Annots")
    if kind == "xref":  # 間接参照の配列
        value = doc.xref_object(int(value.split()[0]), compressed=True)
    elif kind != "array":
        return []
    return [int(x) for x in re.findall(r"(\d+)\s+0\s+R", value)]


def _set_annots(doc: pymupdf.Document, page: pymupdf.Page, xrefs: list[int]) -> None:
    if xrefs:
        doc.xref_set_key(page.xref, "Annots", "[" + " ".join(f"{x} 0 R" for x in xrefs) + "]")
    else:
        doc.xref_set_key(page.xref, "Annots", "null")


def flatten_marks(doc: pymupdf.Document) -> None:
    """このツールが付けた注釈だけをページ本文に焼き込む。

    焼き込んだマーキングは通常の図形になるため、クリックやマウスオーバーで
    ポップアップ (コメント) が表示されなくなる。元々 PDF にあった注釈やフォームはそのまま残す。
    """
    others: dict[int, list[int]] = {}
    for page in doc:
        refs = _annots_refs(doc, page)
        if not refs:
            continue
        ours = {a.xref for a in page.annots() if a.info.get("title") == ANNOT_TITLE}
        if not ours:
            continue
        # 一時的に自分の注釈だけを残して焼き込み、その後ほかの注釈を戻す
        others[page.number] = [x for x in refs if x not in ours]
        _set_annots(doc, page, [x for x in refs if x in ours])
    if not others:
        return
    for page in doc:
        if page.number not in others:
            # 対象外のページの注釈は焼き込まないよう一時的に外す
            refs = _annots_refs(doc, page)
            if refs:
                others[page.number] = refs
                _set_annots(doc, page, [])
    doc.bake(annots=True, widgets=False)
    for pno, refs in others.items():
        page = doc[pno]
        _set_annots(doc, page, _annots_refs(doc, page) + refs)
    doc.xref_set_key(doc.pdf_catalog(), _FLATTENED_KEY, "true")


def mark_pdf(input_path: Union[str, Path], output_path: Union[str, Path], config: Config,
             pages: Optional[str] = None, clear_previous: bool = True,
             flatten: bool = True) -> MarkResult:
    """PDF ファイルを読み込み、マーキングして別ファイルに保存する。

    flatten=True (既定) の場合、マーキングをページに焼き込み、ポップアップが出ないようにする。
    flatten=False の場合は PDF の注釈として残す (Acrobat 等で個別に削除できるが、
    クリックするとコメントのポップアップが表示される)。
    """
    input_path, output_path = Path(input_path), Path(output_path)
    if input_path.resolve() == output_path.resolve():
        raise ValueError("入力ファイルと出力ファイルが同じです。別のパスを指定してください")
    with pymupdf.open(input_path) as doc:
        if doc.needs_pass:
            raise ValueError(f"パスワード付き PDF は未対応です: {input_path}")
        already = doc.xref_get_key(doc.pdf_catalog(), _FLATTENED_KEY)[1] == "true"
        result = mark_document(doc, config, pages=pages, clear_previous=clear_previous)
        result.already_flattened = already
        if flatten:
            flatten_marks(doc)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(output_path, garbage=3, deflate=True)
    return result


def inspect_pdf(input_path: Union[str, Path], strategy: str = "lines",
                pages: Optional[str] = None) -> str:
    """検出された表の構造とセル内容を文字列で返す (列番号の確認用)。"""
    lines: list[str] = []
    with pymupdf.open(input_path) as doc:
        for pno in parse_pages(pages, doc.page_count):
            page = doc[pno]
            if page.rotation:
                page.set_rotation(0)  # 座標系を揃える (保存はしない)
            tables = find_tables(page, strategy)
            if not tables:
                lines.append(f"[ページ {pno + 1}] 表は検出されませんでした")
                continue
            chars = page_chars(page)
            opts = MatchOptions(normalize=False, case_sensitive=True)
            for t in tables:
                b = t.bbox
                lines.append(f"[ページ {pno + 1}] 表 {t.index + 1}: {len(t.rows)} 行 x {t.col_count} 列"
                             f"  位置(pt) x={b.x0:.1f}-{b.x1:.1f}, y={b.y0:.1f}-{b.y1:.1f}")
                bounds = ", ".join(f"{i + 1}列:{x0:.1f}-{x1:.1f}" for i, (x0, x1) in enumerate(t.col_bounds))
                lines.append(f"    列の横位置(pt): {bounds}")
                for r_idx, row in enumerate(t.rows):
                    cells = []
                    for cell in row:
                        if cell is None:
                            cells.append("(結合)")
                        else:
                            cells.append(CellText(chars_in_rect(chars, cell), opts).raw_text.strip())
                    lines.append(f"    行{r_idx + 1:>3}: " + " | ".join(cells))
    return "\n".join(lines)

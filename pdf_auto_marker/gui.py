"""デスクトップ GUI (tkinter)。

起動: python -m pdf_auto_marker.gui   または   pdf-auto-marker-gui
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

import yaml

from .cli import _output_for
from .config import ConfigError, load_config, parse_color
from .marker import inspect_pdf, mark_pdf

APP_TITLE = "PDF 自動マーキング"

MODE_LABELS = {"contains": "部分一致", "exact": "完全一致 (セル全体)", "regex": "正規表現"}
MODE_FROM_LABEL = {v: k for k, v in MODE_LABELS.items()}
STRATEGY_LABELS = {
    "lines": "罫線から検出 (標準)",
    "lines_strict": "罫線のみで検出",
    "text": "文字の並びから検出 (罫線なしの表)",
}
STRATEGY_FROM_LABEL = {v: k for k, v in STRATEGY_LABELS.items()}


# --------------------------------------------------------------------------
# 設定 dict と GUI 表示の相互変換 (ウィジェットに依存しない部分)
# --------------------------------------------------------------------------

def color_to_hex(value: Any) -> str:
    r, g, b = parse_color(value)
    return "#{:02X}{:02X}{:02X}".format(round(r * 255), round(g * 255), round(b * 255))


def columns_to_str(value: Any) -> str:
    """列範囲の指定を GUI 表示用の文字列 ("2-4" / "3" / "") に変換する。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        a, b = int(value[0]), int(value[1])
        return str(a) if a == b else f"{a}-{b}"
    s = str(value).strip()
    return "" if s.lower() in ("all", "全列") else s


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def normalize_rule(raw: dict, kind: str, defaults: Optional[dict] = None) -> dict:
    """読み込んだルールを GUI で扱う形式 (色は #RRGGBB、列は文字列) にそろえる。"""
    defaults = defaults or {}
    key = "words" if kind == "word" else "keywords"
    rule: dict[str, Any] = {
        "name": str(raw.get("name", "")),
        key: _as_list(raw.get(key)),
        "color": color_to_hex(raw.get("color", "yellow")),
        "opacity": float(raw.get("opacity", defaults.get("opacity", 0.4))),
        "mode": str(raw.get("mode", defaults.get("mode", "contains"))),
        "case_sensitive": bool(raw.get("case_sensitive", defaults.get("case_sensitive", False))),
        "normalize": bool(raw.get("normalize", defaults.get("normalize", True))),
    }
    if kind == "word":
        rule["columns"] = columns_to_str(raw.get("columns"))
    else:
        rule["search_columns"] = columns_to_str(raw.get("search_columns"))
        rule["mark_columns"] = columns_to_str(raw.get("mark_columns"))
        xr = raw.get("x_range")
        rule["x_range"] = [float(xr[0]), float(xr[1])] if xr else None
        rule["padding"] = float(raw.get("padding", 0.0))
    return rule


def rule_for_config(rule: dict) -> dict:
    """GUI 内部のルールを設定ファイル形式に変換する (空の項目は省略)。"""
    out = {k: v for k, v in rule.items() if v not in ("", None)}
    if not out.get("name"):
        out.pop("name", None)
    if out.get("padding") == 0.0:
        out.pop("padding")
    return out


def build_config_dict(word_rules: list[dict], row_rules: list[dict],
                      strategy: str, pages: str) -> dict:
    data: dict[str, Any] = {}
    if pages.strip():
        data["pages"] = pages.strip()
    data["table"] = {"strategy": strategy}
    data["word_rules"] = [rule_for_config(r) for r in word_rules]
    data["row_rules"] = [rule_for_config(r) for r in row_rules]
    return data


def load_rules_file(path: str | Path) -> tuple[list[dict], list[dict], str, str]:
    """設定ファイルを読み込み、(単語ルール, 行ルール, 検出方法, ページ) を返す。"""
    load_config(path)  # 先に検証しておく
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    defaults = raw.get("defaults", {}) or {}
    words = [normalize_rule(r, "word", defaults) for r in raw.get("word_rules", []) or []]
    rows = [normalize_rule(r, "row", defaults) for r in raw.get("row_rules", []) or []]
    strategy = str((raw.get("table", {}) or {}).get("strategy", "lines"))
    pages = "" if raw.get("pages") is None else str(raw["pages"])
    return words, rows, strategy, pages


def save_rules_file(path: str | Path, config_dict: dict) -> None:
    header = "# pdf_auto_marker ルール設定ファイル (GUI で保存)\n"
    body = yaml.safe_dump(config_dict, allow_unicode=True, sort_keys=False, default_flow_style=False)
    Path(path).write_text(header + body, encoding="utf-8")


def rule_summary(rule: dict, kind: str) -> tuple[str, ...]:
    """一覧表示用の各列の文字列。"""
    texts = ", ".join(rule["words"] if kind == "word" else rule["keywords"])
    mode = MODE_LABELS.get(rule["mode"], rule["mode"])
    if kind == "word":
        return (rule["name"], texts, rule["color"], mode, rule["columns"] or "全列")
    if rule.get("x_range"):
        x0, x1 = rule["x_range"]
        width = f"{x0:g}〜{x1:g} pt"
    elif rule.get("mark_columns"):
        width = f"列 {rule['mark_columns']}"
    else:
        width = "行全体"
    return (rule["name"], texts, rule["color"], mode, rule["search_columns"] or "全列", width)


# --------------------------------------------------------------------------
# ルール編集ダイアログ
# --------------------------------------------------------------------------

class ColorField(ttk.Frame):
    """色の見本 + 16進入力 + 色選択ボタン。"""

    def __init__(self, master, value: str):
        super().__init__(master)
        self.var = tk.StringVar(value=value)
        self.swatch = tk.Label(self, width=4, relief="solid", borderwidth=1)
        self.swatch.pack(side="left", padx=(0, 6))
        ttk.Entry(self, textvariable=self.var, width=10).pack(side="left")
        ttk.Button(self, text="色を選ぶ…", command=self._choose).pack(side="left", padx=6)
        self.var.trace_add("write", lambda *_: self._refresh())
        self._refresh()

    def _refresh(self):
        try:
            self.swatch.configure(background=color_to_hex(self.var.get()))
        except (ConfigError, ValueError):
            self.swatch.configure(background="white")

    def _choose(self):
        try:
            initial = color_to_hex(self.var.get())
        except (ConfigError, ValueError):
            initial = "#FFFF00"
        _, hex_color = colorchooser.askcolor(color=initial, parent=self, title="マーキングの色")
        if hex_color:
            self.var.set(hex_color.upper())

    def get(self) -> str:
        return self.var.get().strip()


class RuleDialog(tk.Toplevel):
    """単語ルール / 行ルールの追加・編集ダイアログ。"""

    def __init__(self, master, kind: str, rule: Optional[dict] = None):
        super().__init__(master)
        self.kind = kind
        self.result: Optional[dict] = None
        rule = rule or normalize_rule({"color": "#FFFF00" if kind == "word" else "#FF7878"}, kind)
        self.title("単語マーキングの設定" if kind == "word" else "行マーキングの設定")
        self.transient(master)
        self.resizable(False, False)

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        row = 0

        def label(text, r, hint=None):
            ttk.Label(body, text=text).grid(row=r, column=0, sticky="nw", pady=4, padx=(0, 8))
            if hint:
                ttk.Label(body, text=hint, foreground="#666").grid(row=r, column=2, sticky="w", padx=6)

        label("名前", row)
        self.name_var = tk.StringVar(value=rule["name"])
        ttk.Entry(body, textvariable=self.name_var, width=34).grid(row=row, column=1, sticky="w")
        row += 1

        key = "words" if kind == "word" else "keywords"
        label("マーキングする単語" if kind == "word" else "行を探すキーワード", row, "1 行に 1 つ")
        self.words_text = tk.Text(body, width=34, height=5)
        self.words_text.insert("1.0", "\n".join(rule[key]))
        self.words_text.grid(row=row, column=1, sticky="w")
        row += 1

        label("色", row)
        self.color = ColorField(body, rule["color"])
        self.color.grid(row=row, column=1, sticky="w")
        row += 1

        label("透明度", row, "0.1 (薄い) 〜 1.0 (濃い)")
        self.opacity_var = tk.DoubleVar(value=rule["opacity"])
        ttk.Spinbox(body, from_=0.1, to=1.0, increment=0.1, textvariable=self.opacity_var,
                    width=6).grid(row=row, column=1, sticky="w")
        row += 1

        label("一致方法", row)
        self.mode_var = tk.StringVar(value=MODE_LABELS.get(rule["mode"], MODE_LABELS["contains"]))
        ttk.Combobox(body, textvariable=self.mode_var, values=list(MODE_LABELS.values()),
                     state="readonly", width=20).grid(row=row, column=1, sticky="w")
        row += 1

        self.case_var = tk.BooleanVar(value=rule["case_sensitive"])
        ttk.Checkbutton(body, text="英字の大文字・小文字を区別する",
                        variable=self.case_var).grid(row=row, column=1, sticky="w")
        row += 1
        self.norm_var = tk.BooleanVar(value=rule["normalize"])
        ttk.Checkbutton(body, text="全角・半角の違いを無視する",
                        variable=self.norm_var).grid(row=row, column=1, sticky="w")
        row += 1

        col_hint = "例: 3 / 2-4 / 2--1 (末尾まで)。空欄 = 全列"
        if kind == "word":
            label("検索する列", row, col_hint)
            self.columns_var = tk.StringVar(value=rule["columns"])
            ttk.Entry(body, textvariable=self.columns_var, width=12).grid(row=row, column=1, sticky="w")
            row += 1
        else:
            label("キーワードを探す列", row, col_hint)
            self.search_var = tk.StringVar(value=rule["search_columns"])
            ttk.Entry(body, textvariable=self.search_var, width=12).grid(row=row, column=1, sticky="w")
            row += 1

            label("マーキングする幅", row)
            width_frame = ttk.Frame(body)
            width_frame.grid(row=row, column=1, columnspan=2, sticky="w")
            if rule.get("x_range"):
                initial = "x"
            elif rule.get("mark_columns"):
                initial = "cols"
            else:
                initial = "all"
            self.width_mode = tk.StringVar(value=initial)
            self.mark_cols_var = tk.StringVar(value=rule["mark_columns"])
            xr = rule.get("x_range") or ["", ""]
            self.x0_var = tk.StringVar(value="" if xr[0] == "" else f"{xr[0]:g}")
            self.x1_var = tk.StringVar(value="" if xr[1] == "" else f"{xr[1]:g}")
            ttk.Radiobutton(width_frame, text="行全体", value="all",
                            variable=self.width_mode).grid(row=0, column=0, sticky="w")
            ttk.Radiobutton(width_frame, text="列の範囲:", value="cols",
                            variable=self.width_mode).grid(row=1, column=0, sticky="w")
            ttk.Entry(width_frame, textvariable=self.mark_cols_var, width=10).grid(row=1, column=1, sticky="w")
            ttk.Label(width_frame, text="例: 2-4", foreground="#666").grid(row=1, column=2, sticky="w", padx=6)
            ttk.Radiobutton(width_frame, text="横位置 (pt):", value="x",
                            variable=self.width_mode).grid(row=2, column=0, sticky="w")
            xf = ttk.Frame(width_frame)
            xf.grid(row=2, column=1, columnspan=2, sticky="w")
            ttk.Entry(xf, textvariable=self.x0_var, width=7).pack(side="left")
            ttk.Label(xf, text="〜").pack(side="left")
            ttk.Entry(xf, textvariable=self.x1_var, width=7).pack(side="left")
            ttk.Label(xf, text="「表の構造を確認」で位置を確認できます",
                      foreground="#666").pack(side="left", padx=6)
            row += 1

            label("余白 (pt)", row, "マーキング範囲を上下左右に広げる量")
            self.padding_var = tk.DoubleVar(value=rule["padding"])
            ttk.Spinbox(body, from_=0, to=20, increment=0.5, textvariable=self.padding_var,
                        width=6).grid(row=row, column=1, sticky="w")
            row += 1

        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="OK", command=self._ok).pack(side="left", padx=4)
        ttk.Button(buttons, text="キャンセル", command=self.destroy).pack(side="left")
        self.bind("<Escape>", lambda _: self.destroy())

        self.grab_set()
        self.words_text.focus_set()

    def collect(self) -> dict:
        """入力内容をルール dict にする (検証はしない)。"""
        key = "words" if self.kind == "word" else "keywords"
        words = [w.strip() for w in self.words_text.get("1.0", "end").splitlines() if w.strip()]
        try:
            opacity = float(self.opacity_var.get())
        except (tk.TclError, ValueError):
            raise ConfigError("透明度は数値で入力してください") from None
        rule: dict[str, Any] = {
            "name": self.name_var.get().strip(),
            key: words,
            "color": self.color.get(),
            "opacity": opacity,
            "mode": MODE_FROM_LABEL.get(self.mode_var.get(), "contains"),
            "case_sensitive": bool(self.case_var.get()),
            "normalize": bool(self.norm_var.get()),
        }
        if self.kind == "word":
            rule["columns"] = self.columns_var.get().strip()
            return rule
        rule["search_columns"] = self.search_var.get().strip()
        rule["mark_columns"] = ""
        rule["x_range"] = None
        mode = self.width_mode.get()
        if mode == "cols":
            if not self.mark_cols_var.get().strip():
                raise ConfigError("マーキングする列の範囲を入力してください (例: 2-4)")
            rule["mark_columns"] = self.mark_cols_var.get().strip()
        elif mode == "x":
            try:
                rule["x_range"] = [float(self.x0_var.get()), float(self.x1_var.get())]
            except ValueError:
                raise ConfigError("横位置は数値で入力してください (例: 100 〜 400)") from None
        try:
            rule["padding"] = float(self.padding_var.get())
        except (tk.TclError, ValueError):
            raise ConfigError("余白は数値で入力してください") from None
        return rule

    def _ok(self):
        try:
            rule = self.collect()
            if not (rule.get("words") or rule.get("keywords")):
                raise ConfigError("単語 (キーワード) を 1 つ以上入力してください")
            if not rule["name"]:
                first = (rule.get("words") or rule.get("keywords") or [""])[0]
                rule["name"] = first
            # 設定ファイルと同じ検証を通す
            key = "word_rules" if self.kind == "word" else "row_rules"
            load_config({key: [rule_for_config(rule)]})
            rule["color"] = color_to_hex(rule["color"])
        except ConfigError as e:
            msg = str(e)
            for prefix in ("word_rules[1].", "row_rules[1].", "word_rules[1]: ", "row_rules[1]: "):
                msg = msg.replace(prefix, "")
            messagebox.showerror("入力エラー", msg, parent=self)
            return
        self.result = rule
        self.destroy()


# --------------------------------------------------------------------------
# ルール一覧 (Treeview + 操作ボタン)
# --------------------------------------------------------------------------

class RuleList(ttk.Frame):
    def __init__(self, master, kind: str, on_change: Callable[[], None] = lambda: None):
        super().__init__(master, padding=6)
        self.kind = kind
        self.rules: list[dict] = []
        self.on_change = on_change
        if kind == "word":
            cols = [("name", "名前", 110), ("words", "単語", 220), ("color", "色", 80),
                    ("mode", "一致方法", 130), ("columns", "検索する列", 80)]
        else:
            cols = [("name", "名前", 110), ("words", "キーワード", 170), ("color", "色", 80),
                    ("mode", "一致方法", 130), ("search", "探す列", 70), ("width", "マーキング幅", 120)]
        self.tree = ttk.Treeview(self, columns=[c[0] for c in cols], show="headings",
                                 height=5, selectmode="browse")
        for cid, text, width in cols:
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda _: self.edit())
        self.tree.bind("<Delete>", lambda _: self.remove())

        btns = ttk.Frame(self)
        btns.grid(row=0, column=2, sticky="n", padx=(6, 0))
        for text, cmd in (("追加…", self.add), ("編集…", self.edit), ("削除", self.remove),
                          ("▲ 上へ", lambda: self.move(-1)), ("▼ 下へ", lambda: self.move(1))):
            ttk.Button(btns, text=text, command=cmd, width=9).pack(fill="x", pady=2)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._tags: dict[str, str] = {}

    def set_rules(self, rules: list[dict]):
        self.rules = list(rules)
        self.refresh()

    def refresh(self, select: Optional[int] = None):
        self.tree.delete(*self.tree.get_children())
        for i, rule in enumerate(self.rules):
            tag = f"c{rule['color']}"
            if tag not in self._tags:
                self.tree.tag_configure(tag, background=_tint(rule["color"]))
                self._tags[tag] = rule["color"]
            self.tree.insert("", "end", iid=str(i), values=rule_summary(rule, self.kind), tags=(tag,))
        if select is not None and 0 <= select < len(self.rules):
            self.tree.selection_set(str(select))
            self.tree.see(str(select))
        self.on_change()

    def _selected(self) -> Optional[int]:
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def add(self):
        dlg = RuleDialog(self.winfo_toplevel(), self.kind)
        self.wait_window(dlg)
        if dlg.result:
            self.rules.append(dlg.result)
            self.refresh(select=len(self.rules) - 1)

    def edit(self):
        i = self._selected()
        if i is None:
            return
        dlg = RuleDialog(self.winfo_toplevel(), self.kind, dict(self.rules[i]))
        self.wait_window(dlg)
        if dlg.result:
            self.rules[i] = dlg.result
            self.refresh(select=i)

    def remove(self):
        i = self._selected()
        if i is None:
            return
        del self.rules[i]
        self.refresh(select=min(i, len(self.rules) - 1))

    def move(self, delta: int):
        i = self._selected()
        if i is None or not 0 <= i + delta < len(self.rules):
            return
        self.rules[i], self.rules[i + delta] = self.rules[i + delta], self.rules[i]
        self.refresh(select=i + delta)


def _tint(hex_color: str, opacity: float = 0.35) -> str:
    """一覧の行背景用に、色を白と混ぜて薄くする。"""
    r, g, b = parse_color(hex_color)
    mix = [round((1 - opacity + c * opacity) * 255) for c in (r, g, b)]
    return "#{:02X}{:02X}{:02X}".format(*mix)


# --------------------------------------------------------------------------
# メインウィンドウ
# --------------------------------------------------------------------------

class App(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=10)
        self.root = root
        self.pack(fill="both", expand=True)
        self.inputs: list[Path] = []
        self.config_path: Optional[Path] = None
        self.msg_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.last_output_dir: Optional[Path] = None

        root.title(APP_TITLE)
        root.minsize(780, 700)
        root.geometry("900x780")
        self._build_files()
        self._build_rules()
        self._build_options()
        self._build_actions()
        self._build_log()
        self.after(100, self._poll_queue)

    # --- 画面構築 ---
    def _build_files(self):
        frame = ttk.LabelFrame(self, text="① 入力 PDF と出力先", padding=8)
        frame.pack(fill="x")
        self.input_list = tk.Listbox(frame, height=5, selectmode="extended")
        self.input_list.grid(row=0, column=0, rowspan=4, sticky="nsew")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.input_list.yview)
        sb.grid(row=0, column=1, rowspan=4, sticky="ns")
        self.input_list.configure(yscrollcommand=sb.set)
        for r, (text, cmd) in enumerate((("PDF を追加…", self.add_files),
                                         ("フォルダを追加…", self.add_folder),
                                         ("選択を削除", self.remove_files),
                                         ("すべてクリア", self.clear_files))):
            ttk.Button(frame, text=text, command=cmd, width=14).grid(row=r, column=2, padx=(6, 0), pady=1)

        out = ttk.Frame(frame)
        out.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(out, text="出力フォルダ:").pack(side="left")
        self.output_var = tk.StringVar()
        ttk.Entry(out, textvariable=self.output_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(out, text="参照…", command=self.choose_output).pack(side="left")
        ttk.Label(frame, text="※ 出力フォルダが空欄の場合は、入力 PDF と同じ場所に「<元の名前>_marked.pdf」で保存します",
                  foreground="#666").grid(row=5, column=0, columnspan=3, sticky="w")
        frame.columnconfigure(0, weight=1)

    def _build_rules(self):
        frame = ttk.LabelFrame(self, text="② マーキングのルール (ダブルクリックで編集)", padding=4)
        frame.pack(fill="both", expand=True, pady=(8, 0))
        self.notebook = ttk.Notebook(frame)
        self.notebook.pack(fill="both", expand=True)
        self.word_list = RuleList(self.notebook, "word", self._update_tab_titles)
        self.row_list = RuleList(self.notebook, "row", self._update_tab_titles)
        self.notebook.add(self.word_list, text="単語マーキング")
        self.notebook.add(self.row_list, text="行マーキング")
        self._update_tab_titles()

        cfg = ttk.Frame(frame)
        cfg.pack(fill="x", pady=(4, 0))
        ttk.Button(cfg, text="ルールを読み込む…", command=self.load_rules).pack(side="left")
        ttk.Button(cfg, text="ルールを保存…", command=self.save_rules).pack(side="left", padx=6)
        self.config_label = ttk.Label(cfg, text="(未保存)", foreground="#666")
        self.config_label.pack(side="left", padx=6)

    def _build_options(self):
        frame = ttk.LabelFrame(self, text="③ オプション", padding=8)
        frame.pack(fill="x", pady=(8, 0))
        ttk.Label(frame, text="表の検出方法:").grid(row=0, column=0, sticky="w")
        self.strategy_var = tk.StringVar(value=STRATEGY_LABELS["lines"])
        ttk.Combobox(frame, textvariable=self.strategy_var, values=list(STRATEGY_LABELS.values()),
                     state="readonly", width=30).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(frame, text="対象ページ:").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.pages_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.pages_var, width=12).grid(row=0, column=3, sticky="w", padx=6)
        ttk.Label(frame, text="例: 1-3,5 (空欄 = 全ページ)", foreground="#666").grid(row=0, column=4, sticky="w")
        self.keep_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="以前このツールで付けたマーキングを残す",
                        variable=self.keep_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.open_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="完了後に出力フォルダを開く",
                        variable=self.open_var).grid(row=1, column=3, columnspan=2, sticky="w", pady=(6, 0))

    def _build_actions(self):
        frame = ttk.Frame(self)
        frame.pack(fill="x", pady=(8, 0))
        self.inspect_btn = ttk.Button(frame, text="表の構造を確認", command=self.inspect)
        self.inspect_btn.pack(side="left")
        self.run_btn = ttk.Button(frame, text="▶ マーキング実行", command=self.run)
        self.run_btn.pack(side="right")
        self.progress = ttk.Progressbar(frame, mode="determinate", length=220)
        self.progress.pack(side="right", padx=8)

    def _build_log(self):
        frame = ttk.LabelFrame(self, text="ログ", padding=4)
        frame.pack(fill="both", expand=True, pady=(8, 0))
        self.log_text = tk.Text(frame, height=8, state="disabled", wrap="word")
        self.log_text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        sb.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=sb.set)

    def _update_tab_titles(self):
        if not hasattr(self, "notebook"):
            return
        self.notebook.tab(0, text=f"単語マーキング ({len(self.word_list.rules)})")
        self.notebook.tab(1, text=f"行マーキング ({len(self.row_list.rules)})")

    # --- 入力ファイル ---
    def _add_inputs(self, paths):
        for p in map(Path, paths):
            if p.suffix.lower() == ".pdf" and p not in self.inputs:
                self.inputs.append(p)
                self.input_list.insert("end", str(p))

    def add_files(self):
        self._add_inputs(filedialog.askopenfilenames(
            parent=self.root, title="PDF を選択", filetypes=[("PDF", "*.pdf"), ("すべて", "*.*")]))

    def add_folder(self):
        d = filedialog.askdirectory(parent=self.root, title="PDF のあるフォルダを選択")
        if d:
            self._add_inputs(sorted(x for x in Path(d).iterdir() if x.suffix.lower() == ".pdf"))

    def remove_files(self):
        for i in reversed(self.input_list.curselection()):
            self.input_list.delete(i)
            del self.inputs[i]

    def clear_files(self):
        self.input_list.delete(0, "end")
        self.inputs.clear()

    def choose_output(self):
        d = filedialog.askdirectory(parent=self.root, title="出力フォルダを選択")
        if d:
            self.output_var.set(d)

    # --- ルールの読み込み / 保存 ---
    def current_config_dict(self) -> dict:
        return build_config_dict(self.word_list.rules, self.row_list.rules,
                                 STRATEGY_FROM_LABEL.get(self.strategy_var.get(), "lines"),
                                 self.pages_var.get())

    def load_rules_from(self, path: str | Path):
        words, rows, strategy, pages = load_rules_file(path)
        self.word_list.set_rules(words)
        self.row_list.set_rules(rows)
        self.strategy_var.set(STRATEGY_LABELS.get(strategy, STRATEGY_LABELS["lines"]))
        self.pages_var.set(pages)
        self.config_path = Path(path)
        self.config_label.configure(text=str(path))
        self.log(f"ルールを読み込みました: {path}")

    def load_rules(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="ルール設定ファイルを開く",
            filetypes=[("ルール設定", "*.yaml *.yml *.json"), ("すべて", "*.*")])
        if not path:
            return
        try:
            self.load_rules_from(path)
        except (ConfigError, OSError, yaml.YAMLError, ValueError) as e:
            messagebox.showerror("読み込みエラー", str(e), parent=self.root)

    def save_rules(self):
        initial = self.config_path.name if self.config_path else "rules.yaml"
        path = filedialog.asksaveasfilename(
            parent=self.root, title="ルールを保存", initialfile=initial, defaultextension=".yaml",
            filetypes=[("YAML", "*.yaml *.yml")])
        if not path:
            return
        try:
            save_rules_file(path, self.current_config_dict())
        except OSError as e:
            messagebox.showerror("保存エラー", str(e), parent=self.root)
            return
        self.config_path = Path(path)
        self.config_label.configure(text=str(path))
        self.log(f"ルールを保存しました: {path}")

    # --- ログ ---
    def log(self, text: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # --- 実行 ---
    def _check_inputs(self) -> bool:
        if not self.inputs:
            messagebox.showwarning(APP_TITLE, "入力 PDF を追加してください", parent=self.root)
            return False
        return True

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        self.run_btn.configure(state=state)
        self.inspect_btn.configure(state=state)
        self.root.configure(cursor="watch" if busy else "")

    def inspect(self):
        if not self._check_inputs():
            return
        strategy = STRATEGY_FROM_LABEL.get(self.strategy_var.get(), "lines")
        pages = self.pages_var.get().strip() or None
        targets = [self.inputs[i] for i in self.input_list.curselection()] or self.inputs[:1]

        def job():
            parts = []
            for src in targets:
                parts.append(f"=== {src.name} ===\n" + inspect_pdf(src, strategy=strategy, pages=pages))
            return "\n\n".join(parts)

        self._start(job, on_done=self._show_inspect)

    def _show_inspect(self, text: str):
        win = tk.Toplevel(self.root)
        win.title("表の構造 (行・列の番号と横位置)")
        win.geometry("820x480")
        t = tk.Text(win, wrap="none")
        ys = ttk.Scrollbar(win, orient="vertical", command=t.yview)
        xs = ttk.Scrollbar(win, orient="horizontal", command=t.xview)
        t.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        t.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        win.rowconfigure(0, weight=1)
        win.columnconfigure(0, weight=1)
        t.insert("1.0", text)
        t.configure(state="disabled")

    def build_jobs(self) -> list[tuple[Path, Path]]:
        output = self.output_var.get().strip()
        out_arg = (output.rstrip("/\\") + os.sep) if output else None
        return [(src, _output_for(src, out_arg, True)) for src in self.inputs]

    def run(self):
        if not self._check_inputs():
            return
        try:
            config = load_config(self.current_config_dict())
        except ConfigError as e:
            messagebox.showerror("ルールのエラー", str(e), parent=self.root)
            return
        pages = self.pages_var.get().strip() or None
        keep = self.keep_var.get()
        jobs = self.build_jobs()
        self.progress.configure(maximum=len(jobs), value=0)
        self.log(f"--- マーキング開始 ({len(jobs)} ファイル) ---")

        def job():
            total, failed = 0, 0
            for n, (src, dst) in enumerate(jobs, 1):
                try:
                    result = mark_pdf(src, dst, config, pages=pages, clear_previous=not keep)
                except Exception as e:  # 1 ファイルの失敗で全体を止めない
                    failed += 1
                    self.msg_queue.put(("log", f"✕ {src.name}: {e}"))
                else:
                    total += len(result.hits)
                    counts = ", ".join(f"{k}: {v}" for k, v in result.count_by_rule().items()) or "該当なし"
                    self.msg_queue.put(("log", f"✓ {src.name} -> {dst}  ({len(result.hits)} 件: {counts})"))
                    if result.tables_found == 0:
                        self.msg_queue.put(("log", "    注意: 表が検出されませんでした。"
                                                   "罫線のない表は「文字の並びから検出」を試してください"))
                self.msg_queue.put(("progress", n))
            self.last_output_dir = jobs[-1][1].parent
            return total, failed, len(jobs)

        self._start(job, on_done=self._run_done)

    def _run_done(self, summary):
        total, failed, count = summary
        self.log(f"--- 完了: {count - failed}/{count} ファイル, マーキング合計 {total} 件 ---")
        if failed:
            messagebox.showwarning(APP_TITLE, f"{failed} 件のファイルで失敗しました。ログを確認してください。",
                                   parent=self.root)
        elif self.open_var.get() and self.last_output_dir:
            open_folder(self.last_output_dir)

    def _start(self, func: Callable[[], Any], on_done: Callable[[Any], None]):
        self._set_busy(True)

        def worker():
            try:
                self.msg_queue.put(("done", (on_done, func())))
            except Exception as e:
                self.msg_queue.put(("error", f"{e}\n\n{traceback.format_exc(limit=3)}"))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "progress":
                    self.progress.configure(value=payload)
                elif kind == "done":
                    self._set_busy(False)
                    callback, value = payload
                    callback(value)
                elif kind == "error":
                    self._set_busy(False)
                    self.log(f"エラー: {payload}")
                    messagebox.showerror("エラー", payload.split("\n\n")[0], parent=self.root)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)


def open_folder(path: Path) -> None:
    """OS のファイルマネージャでフォルダを開く。"""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


def main(argv: Optional[list[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if sys.platform.startswith("win"):
        try:  # Windows の高 DPI 環境で文字がぼやけないようにする (Tk 生成前に設定)
            from ctypes import windll  # type: ignore[attr-defined]
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    app = App(root)
    # 引数: PDF ファイル / フォルダ / ルール設定ファイル (YAML/JSON) を渡せる
    for a in args:
        p = Path(a)
        if p.suffix.lower() in (".yaml", ".yml", ".json"):
            try:
                app.load_rules_from(p)
            except Exception as e:
                app.log(f"ルールを読み込めませんでした: {e}")
        elif p.is_dir():
            app._add_inputs(sorted(x for x in p.iterdir() if x.suffix.lower() == ".pdf"))
        else:
            app._add_inputs([p])
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

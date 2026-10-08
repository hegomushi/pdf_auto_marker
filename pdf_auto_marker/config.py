"""設定ファイル (YAML / JSON) の読み込みと検証。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import yaml

RGB = tuple[float, float, float]

# よく使う色名 (英語 / 日本語)。値は 0-255 の RGB。
NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "yellow": (255, 255, 0), "黄": (255, 255, 0), "黄色": (255, 255, 0),
    "red": (255, 80, 80), "赤": (255, 80, 80),
    "green": (100, 220, 100), "緑": (100, 220, 100),
    "blue": (100, 160, 255), "青": (100, 160, 255),
    "orange": (255, 165, 0), "オレンジ": (255, 165, 0), "橙": (255, 165, 0),
    "pink": (255, 150, 200), "ピンク": (255, 150, 200),
    "cyan": (0, 230, 255), "水色": (0, 230, 255),
    "purple": (190, 130, 255), "紫": (190, 130, 255),
    "gray": (180, 180, 180), "grey": (180, 180, 180), "灰色": (180, 180, 180),
}

MATCH_MODES = ("contains", "exact", "regex")
TABLE_STRATEGIES = ("lines", "lines_strict", "text")


class ConfigError(ValueError):
    """設定内容が不正な場合に送出される。"""


def parse_color(value: Any) -> RGB:
    """色指定を PyMuPDF 用の 0.0-1.0 の RGB タプルに変換する。

    受け付ける形式:
      - "#FFFF00" / "FFFF00" / "#FF0" (16進)
      - "yellow" / "黄色" などの色名
      - [255, 255, 0] (0-255) または [1.0, 1.0, 0.0] (0.0-1.0)
    """
    if isinstance(value, str):
        s = value.strip()
        if s.lower() in NAMED_COLORS or s in NAMED_COLORS:
            rgb = NAMED_COLORS.get(s.lower(), NAMED_COLORS.get(s))
            return tuple(c / 255 for c in rgb)  # type: ignore[return-value]
        hex_part = s.lstrip("#")
        if re.fullmatch(r"[0-9a-fA-F]{3}", hex_part):
            hex_part = "".join(ch * 2 for ch in hex_part)
        if re.fullmatch(r"[0-9a-fA-F]{6}", hex_part):
            return tuple(int(hex_part[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
        raise ConfigError(f"色の指定を解釈できません: {value!r}")
    if isinstance(value, (list, tuple)) and len(value) == 3:
        nums = [float(v) for v in value]
        if any(n < 0 for n in nums):
            raise ConfigError(f"色の値に負数は使えません: {value!r}")
        if all(n <= 1.0 for n in nums) and any(isinstance(v, float) for v in value):
            return tuple(nums)  # type: ignore[return-value]
        if any(n > 255 for n in nums):
            raise ConfigError(f"色の値は 0-255 で指定してください: {value!r}")
        return tuple(n / 255 for n in nums)  # type: ignore[return-value]
    raise ConfigError(f"色の指定を解釈できません: {value!r}")


def _as_str_list(value: Any, key: str) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        raise ConfigError(f"'{key}' には文字列または文字列のリストを指定してください")
    items = [v for v in items if v != ""]
    if not items:
        raise ConfigError(f"'{key}' が空です")
    return items


def _parse_column_spec(value: Any, key: str) -> Optional[tuple[int, int]]:
    """列範囲の指定 (1始まり、両端含む、負数は末尾から) を (start, end) に変換する。

    - null / "all"       -> None (全列)
    - 3                  -> (3, 3)
    - [2, 4] / "2-4"     -> (2, 4)
    - [2, -1]            -> 2列目から最終列まで
    """
    if value is None or (isinstance(value, str) and value.strip().lower() in ("all", "全列", "")):
        return None
    if isinstance(value, int):
        start = end = value
    elif isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*-\s*-?\d+\s*", value):
        m = re.fullmatch(r"\s*(-?\d+)\s*-\s*(-?\d+)\s*", value)
        start, end = int(m.group(1)), int(m.group(2))  # type: ignore[union-attr]
    elif isinstance(value, str) and re.fullmatch(r"\s*-?\d+\s*", value):
        start = end = int(value)
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        start, end = int(value[0]), int(value[1])
    else:
        raise ConfigError(f"'{key}' の指定を解釈できません: {value!r} (例: [2, 4] / \"2-4\" / 3 / all)")
    if start == 0 or end == 0:
        raise ConfigError(f"'{key}' の列番号は 1 始まりです (0 は使えません)")
    return (start, end)


def _parse_opacity(value: Any, key: str) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"'{key}' は数値で指定してください: {value!r}") from None
    if not 0.0 < f <= 1.0:
        raise ConfigError(f"'{key}' は 0 より大きく 1 以下で指定してください: {value!r}")
    return f


def _parse_mode(value: Any, key: str) -> str:
    mode = str(value).lower()
    if mode not in MATCH_MODES:
        raise ConfigError(f"'{key}' は {', '.join(MATCH_MODES)} のいずれかです: {value!r}")
    return mode


@dataclass
class MatchOptions:
    mode: str = "contains"        # contains / exact / regex
    case_sensitive: bool = False
    normalize: bool = True        # 全角/半角の揺れを吸収 (Unicode NFKC)


@dataclass
class WordRule:
    """機能1: 表内の指定単語をマーキングするルール。"""
    words: list[str]
    color: RGB
    opacity: float
    match: MatchOptions
    columns: Optional[tuple[int, int]] = None   # 検索対象の列範囲 (None = 全列)
    name: str = ""


@dataclass
class RowRule:
    """機能2: 指定文字を含む行を、指定範囲の幅でマーキングするルール。"""
    keywords: list[str]
    color: RGB
    opacity: float
    match: MatchOptions
    search_columns: Optional[tuple[int, int]] = None  # キーワードを探す列範囲 (None = 全列)
    mark_columns: Optional[tuple[int, int]] = None    # マーキングする列範囲 (None = 行全体)
    x_range: Optional[tuple[float, float]] = None     # マーキングする横範囲を pt で直接指定
    padding: float = 0.0                               # マーキング範囲の上下左右の余白 (pt)
    name: str = ""


@dataclass
class Config:
    word_rules: list[WordRule] = field(default_factory=list)
    row_rules: list[RowRule] = field(default_factory=list)
    table_strategy: str = "lines"
    pages: Optional[str] = None


def _match_options(raw: dict, defaults: MatchOptions, where: str) -> MatchOptions:
    return MatchOptions(
        mode=_parse_mode(raw.get("mode", defaults.mode), f"{where}.mode"),
        case_sensitive=bool(raw.get("case_sensitive", defaults.case_sensitive)),
        normalize=bool(raw.get("normalize", defaults.normalize)),
    )


def _check_regex(patterns: Sequence[str], match: MatchOptions, where: str) -> None:
    if match.mode != "regex":
        return
    for p in patterns:
        try:
            re.compile(p)
        except re.error as e:
            raise ConfigError(f"{where}: 正規表現が不正です {p!r}: {e}") from None


def load_config(source: Any) -> Config:
    """ファイルパス、または読み込み済みの dict から Config を生成する。"""
    if isinstance(source, (str, Path)):
        path = Path(source)
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    else:
        raw = source
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("設定ファイルの最上位はマッピング (key: value) である必要があります")

    defaults_raw = raw.get("defaults", {}) or {}
    default_opacity = _parse_opacity(defaults_raw.get("opacity", 0.4), "defaults.opacity")
    default_match = _match_options(defaults_raw, MatchOptions(), "defaults")

    strategy = str((raw.get("table", {}) or {}).get("strategy", "lines"))
    if strategy not in TABLE_STRATEGIES:
        raise ConfigError(f"table.strategy は {', '.join(TABLE_STRATEGIES)} のいずれかです: {strategy!r}")

    pages = raw.get("pages")
    cfg = Config(table_strategy=strategy, pages=None if pages is None else str(pages))

    for i, r in enumerate(raw.get("word_rules", []) or [], 1):
        where = f"word_rules[{i}]"
        if not isinstance(r, dict):
            raise ConfigError(f"{where} はマッピングで指定してください")
        if "words" not in r:
            raise ConfigError(f"{where}: 'words' は必須です")
        if "color" not in r:
            raise ConfigError(f"{where}: 'color' は必須です")
        match = _match_options(r, default_match, where)
        words = _as_str_list(r["words"], f"{where}.words")
        _check_regex(words, match, where)
        cfg.word_rules.append(WordRule(
            words=words,
            color=parse_color(r["color"]),
            opacity=_parse_opacity(r.get("opacity", default_opacity), f"{where}.opacity"),
            match=match,
            columns=_parse_column_spec(r.get("columns"), f"{where}.columns"),
            name=str(r.get("name", where)),
        ))

    for i, r in enumerate(raw.get("row_rules", []) or [], 1):
        where = f"row_rules[{i}]"
        if not isinstance(r, dict):
            raise ConfigError(f"{where} はマッピングで指定してください")
        if "keywords" not in r:
            raise ConfigError(f"{where}: 'keywords' は必須です")
        if "color" not in r:
            raise ConfigError(f"{where}: 'color' は必須です")
        match = _match_options(r, default_match, where)
        keywords = _as_str_list(r["keywords"], f"{where}.keywords")
        _check_regex(keywords, match, where)
        x_range = r.get("x_range")
        if x_range is not None:
            if not (isinstance(x_range, (list, tuple)) and len(x_range) == 2):
                raise ConfigError(f"{where}.x_range は [左端, 右端] (pt) で指定してください")
            x0, x1 = float(x_range[0]), float(x_range[1])
            if x0 >= x1:
                raise ConfigError(f"{where}.x_range は 左端 < 右端 で指定してください")
            x_range = (x0, x1)
        if x_range is not None and r.get("mark_columns") is not None:
            raise ConfigError(f"{where}: 'mark_columns' と 'x_range' は同時に指定できません")
        cfg.row_rules.append(RowRule(
            keywords=keywords,
            color=parse_color(r["color"]),
            opacity=_parse_opacity(r.get("opacity", default_opacity), f"{where}.opacity"),
            match=match,
            search_columns=_parse_column_spec(r.get("search_columns"), f"{where}.search_columns"),
            mark_columns=_parse_column_spec(r.get("mark_columns"), f"{where}.mark_columns"),
            x_range=x_range,
            padding=float(r.get("padding", 0.0)),
            name=str(r.get("name", where)),
        ))

    if not cfg.word_rules and not cfg.row_rules:
        raise ConfigError("word_rules / row_rules のどちらかに 1 件以上のルールを指定してください")
    return cfg

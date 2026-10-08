import sys
from pathlib import Path

import pymupdf
import pytest

from pdf_auto_marker import ConfigError, load_config, mark_pdf, parse_color
from pdf_auto_marker.cli import main
from pdf_auto_marker.marker import ANNOT_TITLE, parse_pages

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
pytest.importorskip("reportlab")
import make_sample_pdf  # noqa: E402


@pytest.fixture(scope="session")
def sample_pdf(tmp_path_factory):
    path = tmp_path_factory.mktemp("pdf") / "sample.pdf"
    make_sample_pdf.build(path)
    return path


def _run(sample_pdf, tmp_path, cfg_dict):
    out = tmp_path / "out.pdf"
    result = mark_pdf(sample_pdf, out, load_config(cfg_dict))
    return result, out


def _annots(path):
    with pymupdf.open(path) as doc:
        return [(a.type[1], pymupdf.Rect(a.rect), a.info["content"]) for a in doc[0].annots()]


def test_word_rule_marks_only_inside_table(sample_pdf, tmp_path):
    result, out = _run(sample_pdf, tmp_path, {
        "word_rules": [{"words": ["要確認"], "color": "yellow"}],
    })
    # 表外の「要確認」は対象外 -> 表内の 2 件のみ
    assert [(h.row, h.column) for h in result.hits] == [(3, 5), (5, 5)]
    annots = _annots(out)
    assert len(annots) == 2 and all(t == "Highlight" for t, _, _ in annots)


def test_word_rule_column_filter_and_case(sample_pdf, tmp_path):
    result, _ = _run(sample_pdf, tmp_path, {
        "word_rules": [{"words": ["OK", "m6"], "color": "#0f0", "columns": "2-5"}],
    })
    texts = sorted(h.text for h in result.hits)
    assert texts == ["M6", "M6", "ok"]  # 大文字小文字を区別しない

    result, _ = _run(sample_pdf, tmp_path, {
        "word_rules": [{"words": ["OK"], "color": "#0f0", "case_sensitive": True}],
    })
    assert result.hits == []


def test_word_rule_regex_and_normalize(sample_pdf, tmp_path):
    result, _ = _run(sample_pdf, tmp_path, {
        "word_rules": [{"words": [r"M\d"], "color": "red", "mode": "regex"}],
    })
    assert sorted(h.text for h in result.hits) == ["M6", "M6", "M8"]

    # 全角で指定しても半角の "NG" にマッチする
    result, _ = _run(sample_pdf, tmp_path, {
        "word_rules": [{"words": ["ＮＧ"], "color": "red"}],
    })
    assert [h.text for h in result.hits] == ["NG"]


def test_row_rule_mark_columns_width(sample_pdf, tmp_path):
    result, out = _run(sample_pdf, tmp_path, {
        "row_rules": [{"keywords": "不合格", "color": [255, 0, 0],
                       "search_columns": 4, "mark_columns": [2, 4]}],
    })
    assert [h.row for h in result.hits] == [3, 6]
    annots = _annots(out)
    assert len(annots) == 2
    for kind, rect, _ in annots:
        assert kind == "Square"
        # 2列目の左端 (≈127.6) から 4列目の右端 (≈357.6) まで
        assert rect.x0 == pytest.approx(127.6, abs=1.5)
        assert rect.x1 == pytest.approx(357.6, abs=1.5)


def test_row_rule_full_width_negative_columns_and_x_range(sample_pdf, tmp_path):
    result, out = _run(sample_pdf, tmp_path, {
        "row_rules": [
            {"keywords": "保留", "color": "blue", "mode": "exact"},
            {"keywords": "在庫少", "color": "green", "mark_columns": [-2, -1]},
            {"keywords": "ブラケット", "color": "gray", "x_range": [100, 200]},
        ],
    })
    assert [(h.rule, h.row) for h in result.hits] == [
        ("row_rules[1]", 5), ("row_rules[2]", 4), ("row_rules[3]", 7)]
    rects = [r for _, r, _ in _annots(out)]
    assert rects[0].x0 == pytest.approx(97.6, abs=1.5) and rects[0].x1 == pytest.approx(497.6, abs=1.5)
    assert rects[1].x0 == pytest.approx(287.6, abs=1.5) and rects[1].x1 == pytest.approx(497.6, abs=1.5)
    assert rects[2].x0 == pytest.approx(100, abs=1.5) and rects[2].x1 == pytest.approx(200, abs=1.5)


def test_exact_mode_does_not_match_partial(sample_pdf, tmp_path):
    result, _ = _run(sample_pdf, tmp_path, {
        "row_rules": [{"keywords": "合格", "color": "green", "mode": "exact", "search_columns": 4}],
    })
    assert [h.row for h in result.hits] == [2, 4, 7]


def test_rerun_replaces_previous_marks(sample_pdf, tmp_path):
    cfg = {"word_rules": [{"words": ["要確認"], "color": "yellow"}]}
    _, out1 = _run(sample_pdf, tmp_path, cfg)
    out2 = tmp_path / "out2.pdf"
    mark_pdf(out1, out2, load_config(cfg))
    annots = _annots(out2)
    assert len(annots) == 2
    with pymupdf.open(out2) as doc:
        assert all(a.info["title"] == ANNOT_TITLE for a in doc[0].annots())


def test_rotated_page(sample_pdf, tmp_path):
    rotated = tmp_path / "rot.pdf"
    with pymupdf.open(sample_pdf) as doc:
        doc[0].set_rotation(90)
        doc.save(rotated)
    out = tmp_path / "rot_out.pdf"
    result = mark_pdf(rotated, out, load_config(
        {"word_rules": [{"words": ["要確認"], "color": "yellow"}]}))
    assert len(result.hits) == 2
    with pymupdf.open(out) as doc:
        assert doc[0].rotation == 90


def test_example_config_and_cli(sample_pdf, tmp_path, capsys):
    out = tmp_path / "cli.pdf"
    assert main([str(sample_pdf), "-c", str(ROOT / "examples" / "rules.yaml"), "-o", str(out)]) == 0
    assert out.exists()
    assert "マーキング: 7 件" in capsys.readouterr().out
    assert main([str(sample_pdf), "--inspect"]) == 0
    assert "7 行 x 5 列" in capsys.readouterr().out


@pytest.mark.parametrize("value, expected", [
    ("#FFFF00", (1.0, 1.0, 0.0)),
    ("ff0", (1.0, 1.0, 0.0)),
    ("黄色", (1.0, 1.0, 0.0)),
    ([255, 0, 0], (1.0, 0.0, 0.0)),
    ([0.5, 0.0, 1.0], (0.5, 0.0, 1.0)),
])
def test_parse_color(value, expected):
    assert parse_color(value) == pytest.approx(expected)


@pytest.mark.parametrize("cfg", [
    {},
    {"word_rules": [{"words": ["a"]}]},
    {"word_rules": [{"words": ["a"], "color": "nocolor"}]},
    {"word_rules": [{"words": ["("], "color": "red", "mode": "regex"}]},
    {"row_rules": [{"keywords": ["a"], "color": "red", "mark_columns": [0, 2]}]},
    {"row_rules": [{"keywords": ["a"], "color": "red", "mark_columns": [1, 2], "x_range": [0, 9]}]},
    {"row_rules": [{"keywords": ["a"], "color": "red", "opacity": 2}]},
])
def test_invalid_config(cfg):
    with pytest.raises(ConfigError):
        load_config(cfg)


def test_parse_pages():
    assert parse_pages(None, 3) == [0, 1, 2]
    assert parse_pages("1,3", 5) == [0, 2]
    assert parse_pages("2-", 4) == [1, 2, 3]
    assert parse_pages("4-9", 5) == [3, 4]

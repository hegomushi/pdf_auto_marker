import pytest

pytest.importorskip("tkinter")
from pdf_auto_marker import gui, load_config  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def test_columns_to_str():
    assert gui.columns_to_str(None) == ""
    assert gui.columns_to_str([2, 4]) == "2-4"
    assert gui.columns_to_str([3, 3]) == "3"
    assert gui.columns_to_str("all") == ""
    assert gui.columns_to_str(5) == "5"


def test_color_to_hex():
    assert gui.color_to_hex("yellow") == "#FFFF00"
    assert gui.color_to_hex([255, 120, 120]) == "#FF7878"


def test_rules_roundtrip(tmp_path):
    words, rows, strategy, pages = gui.load_rules_file(ROOT / "examples" / "rules.yaml")
    assert [r["name"] for r in words] == ["要確認", "NG"]
    assert rows[0]["mark_columns"] == "2-4" and rows[0]["search_columns"] == "4"
    assert rows[1]["mode"] == "exact"

    data = gui.build_config_dict(words, rows, strategy, pages)
    cfg = load_config(data)  # GUI から作った設定がそのまま有効であること
    assert cfg.row_rules[0].mark_columns == (2, 4)

    path = tmp_path / "saved.yaml"
    gui.save_rules_file(path, data)
    assert gui.load_rules_file(path) == (words, rows, strategy, pages)


def test_rule_summary_row_width():
    base = gui.normalize_rule({"keywords": "a", "color": "red"}, "row")
    assert gui.rule_summary(base, "row")[-1] == "行全体"
    base["mark_columns"] = "2-4"
    assert gui.rule_summary(base, "row")[-1] == "列 2-4"
    base["x_range"] = [100.0, 250.5]
    assert gui.rule_summary(base, "row")[-1] == "100〜250.5 pt"

import panel


def test_list_chars_lists_json(tmp_path):
    (tmp_path / "archer1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "mage.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    out = panel.list_chars(str(tmp_path))
    names = [p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for p in out]
    assert names == ["archer1.json", "mage.json"]     # sorted, .json only


def test_list_chars_missing_dir_returns_empty(tmp_path):
    assert panel.list_chars(str(tmp_path / "nope")) == []

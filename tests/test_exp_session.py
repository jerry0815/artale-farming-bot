"""EXP session log helpers: append + read-back (JSON Lines)."""
import recovery


def test_append_and_read_roundtrip(tmp_path):
    p = str(tmp_path / "exp.jsonl")
    recovery.append_exp_session({"label": "human", "exp_gained": 100}, path=p)
    recovery.append_exp_session({"label": "bot", "exp_gained": 200}, path=p)
    rows = recovery.read_exp_sessions(path=p)
    assert len(rows) == 2
    assert rows[0]["label"] == "bot"        # newest first
    assert rows[1]["exp_gained"] == 100


def test_read_missing_file_returns_empty(tmp_path):
    assert recovery.read_exp_sessions(path=str(tmp_path / "nope.jsonl")) == []


def test_read_limit_returns_newest(tmp_path):
    p = str(tmp_path / "exp.jsonl")
    for i in range(5):
        recovery.append_exp_session({"label": str(i)}, path=p)
    rows = recovery.read_exp_sessions(path=p, limit=2)
    assert [r["label"] for r in rows] == ["4", "3"]     # last two, newest first


def test_append_never_raises_on_bad_path():
    # A directory path (not writable as a file) must not raise -- best-effort logging.
    recovery.append_exp_session({"label": "x"}, path=".")   # '.' is a dir; should be swallowed

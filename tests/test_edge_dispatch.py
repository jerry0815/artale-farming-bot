"""Data-driven execute_edge dispatch (no game; primitives are faked)."""
import recovery


def test_execute_edge_dispatches_rope_with_recorded_params(monkeypatch):
    calls = {}

    def fake_hop(grab_x, land_y_max, dismount=None, land_node=None, cap=12.0):
        calls.update(grab_x=grab_x, land_y_max=land_y_max, dismount=dismount, land_node=land_node)
        return True

    monkeypatch.setattr(recovery, "climb_rope_hop", fake_hop)
    edge = {"src": "PORTAL_BOT", "dst": "LOWER_R", "kind": "rope",
            "grab_x": 132, "land_y": 151, "dismount": "right"}
    assert recovery.execute_edge(edge) is True
    assert calls == {"grab_x": 132, "land_y_max": 151, "dismount": "right", "land_node": "LOWER_R"}


def test_execute_edge_dispatches_walk(monkeypatch):
    seen = {}
    monkeypatch.setattr(recovery, "walk_to_x", lambda x, **k: seen.setdefault("x", x) or True)
    edge = {"src": "TOP", "dst": "FAR", "kind": "walk", "target_x": 112}
    assert recovery.execute_edge(edge) is True
    assert seen["x"] == 112


def test_execute_edge_falls_through_to_composite_for_hardcoded_edges(monkeypatch):
    hit = {"n": 0}
    monkeypatch.setitem(recovery.EDGE_ACTIONS, ("REST", "TOP_FARM"),
                        lambda: hit.__setitem__("n", hit["n"] + 1) or True)
    edge = {"src": "REST", "dst": "TOP_FARM"}   # no 'kind' -> composite path
    assert recovery.execute_edge(edge) is True
    assert hit["n"] == 1


def test_execute_edge_unknown_falls_back_to_replay(monkeypatch):
    edge = {"src": "NOWHERE", "dst": "VOID", "kind": "walk"}   # walk but no target_x, no composite
    assert recovery.execute_edge(edge) is False

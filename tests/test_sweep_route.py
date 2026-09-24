"""Route mode for the sweep loop: a fixed list of segments (dir, until_x on the minimap,
transition at the end, attack or just travel). Heading is fixed per segment -- no ping-pong."""
import sweep


def test_seg_heading():
    assert sweep.seg_heading({"dir": "right"}) == 1
    assert sweep.seg_heading({"dir": "left"}) == -1


def test_seg_done_right_when_minimap_x_reaches_until():
    seg = {"dir": "right", "until_x": 275}
    assert not sweep.seg_done(seg, 250)
    assert sweep.seg_done(seg, 275) and sweep.seg_done(seg, 290)


def test_seg_done_left_when_minimap_x_reaches_until():
    seg = {"dir": "left", "until_x": 214}
    assert not sweep.seg_done(seg, 240)
    assert sweep.seg_done(seg, 214) and sweep.seg_done(seg, 200)


def test_route_action_never_flips_mid_segment():
    # empty ground inside a segment -> keep walking the segment (ADVANCE would ping-pong)
    assert sweep.route_action(sweep.ADVANCE) == sweep.WALK
    for a in (sweep.FIRE, sweep.WALK, sweep.GATHER):
        assert sweep.route_action(a) == a


class _KB:
    def __init__(self):
        self.events = []
    def safe_press(self, k): self.events.append(("press", k))
    def safe_release(self, k): self.events.append(("release", k))
    def safe_release_all(self): self.events.append(("release_all", None))


KEYS = {"attack": "x", "dash": "z", "right": "R", "left": "L", "up": "U", "jump": "J"}


def _reader(seq):
    it = iter(seq)
    last = [seq[-1]]
    def read():
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]
    return read


def test_transition_none_is_a_noop_success():
    kb = _KB()
    assert sweep.route_transition(None, 1, 1, KEYS, kb, read_xy=_reader([(0, 0)])) is True
    assert not [e for e in kb.events if e[0] == "press"]


def test_transition_up_confirms_on_level_change():
    kb = _KB()
    # baseline on the bottom floor, then the portal lands her on the upper level (y 106 -> 55)
    ok = sweep.route_transition("up", -1, -1, KEYS, kb,
                                read_xy=_reader([(214, 106), (214, 106), (140, 55)]))
    assert ok is True and ("press", "U") in kb.events


def test_transition_up_retries_once_then_reports_failure():
    kb = _KB()
    ok = sweep.route_transition("up", -1, -1, KEYS, kb, read_xy=_reader([(214, 106)]),
                                up_wait=0.3, poll=0.1)
    presses = [k for e, k in kb.events if e == "press"]
    assert ok is False
    assert presses.count("U") == 2            # first try + one retry
    assert "R" in presses or "L" in presses   # nudged before the retry


def test_transition_jump_double_jumps_holding_next_direction():
    kb = _KB()
    ok = sweep.route_transition("jump", 1, 1, KEYS, kb, read_xy=_reader([(195, 55)]))
    presses = [k for e, k in kb.events if e == "press"]
    assert ok is True
    assert presses.count("J") == 2
    assert presses.index("R") < presses.index("J")   # moving toward the next segment first

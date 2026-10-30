"""Live check: can the sweep loop get her off middle_road2's right-end ledge?

The ledge sits just right of the middle floor's right end (minimap ~x 292, y 108 -- 15 below the
middle floor's 93). On 2026-10-21 the loop got stuck there: segments 2 and 5 handed her back and
forth ("fell off its floor ... resync to segment N") for minutes without pressing a key.

How to run:
  1. In game, walk her to the RIGHT end of the middle floor and step off onto the small ledge.
  2. Stop any panel farm run, then:   python ledge_recovery_test.py
     (it checks she is on the ledge, then runs the real farm loop from segment 2 for 30 s;
      'n' pauses it, as in a normal run)
  3. Read the verdict at the end.

PASS = she ends up back on a route floor and the loop resynced only a few times.
FAIL = she is still on the ledge, or resync lines kept piling up (the old ping-pong).
"""
import io, re, sys
import core
import farm_general
import watermap
from pynput.keyboard import Listener

MAP = "maps/middle_road2.json"
RUN_SECS = 30
LEDGE_X, LEDGE_Y = (270, 310), (100, 116)     # minimap box around the ledge read (292, 108)


class _Tee(io.TextIOBase):
    """Echo the loop's prints and keep them for the verdict."""
    def __init__(self, real):
        self.real, self.buf = real, []

    def write(self, s):
        self.real.write(s); self.buf.append(s)
        return len(s)

    def flush(self):
        self.real.flush()


def main():
    cfg = watermap.load_map(MAP)
    core.apply_map_minimap(cfg)
    if cfg.get("map_box"):
        core.set_map_box(*cfg["map_box"])
    x, y = core.get_character_full()
    print(f"[ledge-test] she reads at minimap ({x}, {y})")
    if not (LEDGE_X[0] <= x <= LEDGE_X[1] and LEDGE_Y[0] <= y <= LEDGE_Y[1]):
        print(f"[ledge-test] not on the ledge (want x {LEDGE_X}, y {LEDGE_Y}) -- walk her there first")
        return 2

    cfg = {**cfg, "start_segment": 2}             # middle floor, heading left: the stuck case
    lis = Listener(on_press=core.kb.on_press); lis.start()   # 'n' pauses as usual
    tee = _Tee(sys.stdout); sys.stdout = tee
    try:
        farm_general.farming_loop_general(cfg, max_seconds=RUN_SECS)
    finally:
        sys.stdout = tee.real
        core.kb.safe_release_all(); lis.stop()

    log = "".join(tee.buf)
    resyncs = len(re.findall(r"-> resync to segment", log))
    rescues = len(re.findall(r"no route segment on this level|same segment", log))
    fx, fy = core.get_character_full()
    floors = sorted({s["y"] for s in cfg["route"] if s.get("y") is not None})
    on_floor = fy >= 0 and any(abs(fy - f) <= int(cfg.get("fall_tol", 12)) for f in floors)
    off_ledge = not (LEDGE_X[0] <= fx <= LEDGE_X[1] and LEDGE_Y[0] <= fy <= LEDGE_Y[1])
    print(f"\n[ledge-test] end position ({fx}, {fy}); floors {floors}")
    print(f"[ledge-test] rescue moves: {rescues}   segment resyncs: {resyncs}")
    ok = on_floor and off_ledge and resyncs <= 5
    print(f"[ledge-test] {'PASS' if ok else 'FAIL'} -- "
          + ("back on a route floor" if ok else
             "still on the ledge" if not off_ledge else
             f"too many resyncs ({resyncs}) -- ping-pong?" if resyncs > 5 else
             "not on any route floor"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

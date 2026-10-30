"""Ladder-grab tester: repeat the route's rope/ladder climb and log which take-off spot works.

    python ladder_test.py                                  # stand jump at x=99, 5 trials
    python ladder_test.py --x 100 --trials 10              # stand jump at another spot
    python ladder_test.py --mode run --takeoff 93          # run right from ~9 px out, jump at 93
    python ladder_test.py --mode run --dir left --takeoff 105

Start her on the ladder's floor (middle_road2: middle floor, y~96), left of the ladder for
--dir right. Each trial: line up on the minimap, jump (stand: straight up from --x; run: walk in
--dir and jump when the dot reaches --takeoff, keeping the arrow held through the air), hold UP,
then watch the minimap y: HIT = standing on --target-y, MISS = landed back on --base-y. After a
HIT she drops back down (down+jump); before every try she walks --walkoff px away from the spot
and lines up again from there. Ctrl+C stops. Results print per trial
and append to logs/ladder_test.jsonl. PRESSES KEYS -- the game window gets focus.
"""
import argparse
import json
import os
import time

import core
import sweep


def fast_reader():
    """Fast single minimap reads (~17 ms) tracked near the last good read."""
    last = [None]

    def read():
        x, y = core.get_character_color(near=last[0])
        if x >= 0:
            last[0] = (x, y)
        return x, y
    return read


def wait_level(read, base_y, target_y, timeout=5.0, tol=6):
    """Watch minimap y after the jump: 'hit' (steady on target_y), 'miss' (steady back on base_y
    after leaving it), 'stuck' (timeout elsewhere, e.g. hanging mid-ladder)."""
    t0, prev, left, y = time.time(), None, False, None
    while time.time() - t0 < timeout:
        _, y = read()
        if y >= 0:
            left = left or abs(y - base_y) >= 3
            steady = prev is not None and abs(y - prev) <= 1
            if steady and abs(y - target_y) <= tol:
                return "hit", y
            if steady and left and abs(y - base_y) <= 2:
                return "miss", y
            prev = y
        time.sleep(0.05)
    return ("no-jump" if not left else "stuck"), y        # no-jump: never left the floor (hit?)


def walk_off(args, keys, read, heading):
    """Walk AWAY from the jump spot (against the approach heading) so every try lines up from
    the same distance, arriving on the move -- like the route does -- not from where the last
    try left her."""
    if args.mode == "stand":
        goal = args.x - heading * args.walkoff
    else:                                              # the run-up start already backs off
        goal = args.takeoff - heading * max(args.walkoff, args.runup)
    back = keys["left"] if heading > 0 else keys["right"]
    core.kb.safe_press(back)
    t0 = time.time()
    while time.time() - t0 < 3.0:
        x = read()[0]
        if x >= 0 and (goal - x) * heading >= 0:            # at/behind the walk-off point
            break
        time.sleep(0.02)
    core.kb.safe_release(back)
    time.sleep(0.2)


def trial(args, keys, read):
    kb = core.kb
    heading = 1 if args.dir == "right" else -1
    face = keys["right"] if heading > 0 else keys["left"]
    walk_off(args, keys, read, heading)
    if args.mode == "stand":
        sweep.approach_x(args.x, heading, keys, kb, read, sleep=time.sleep, tol=args.tol,
                         brake=10 ** 6)
        x_jump = read()[0]
        kb.safe_press(keys["jump"]); time.sleep(0.05); kb.safe_release(keys["jump"])
        kb.safe_press(keys["up"])
    else:
        start = args.takeoff - heading * args.runup           # back off, then run in
        sweep.approach_x(start, heading, keys, kb, read, sleep=time.sleep, tol=1, brake=10 ** 6)
        kb.safe_press(face)
        t0 = time.time()
        while time.time() - t0 < 3.0:
            x = read()[0]
            if x >= 0 and (x - args.takeoff) * heading >= 0:
                break
            time.sleep(0.01)
        x_jump = read()[0]
        kb.safe_press(keys["jump"]); time.sleep(0.05); kb.safe_release(keys["jump"])
        kb.safe_press(keys["up"])
        time.sleep(args.air_hold)                              # keep drifting toward the rope
        kb.safe_release(face)
    res, y = wait_level(read, args.base_y, args.target_y)
    kb.safe_release(keys["up"]); kb.safe_release_all()
    return res, x_jump, y


def reset(args, keys, read):
    """After a HIT she stands on the floor above: drop back down to the base floor."""
    kb = core.kb
    for _ in range(3):
        _, y = read()
        if y >= 0 and abs(y - args.base_y) <= 3:
            return True
        if y >= 0 and abs(y - args.target_y) <= 6:              # on top: drop through
            kb.safe_press(keys["down"]); time.sleep(0.1)
            kb.safe_press(keys["jump"]); time.sleep(0.05); kb.safe_release(keys["jump"])
            time.sleep(0.1); kb.safe_release(keys["down"])
        else:                                                   # hanging on the ladder: climb down
            kb.safe_press(keys["down"]); time.sleep(2.5); kb.safe_release(keys["down"])
        time.sleep(1.2)
    _, y = read()
    return y >= 0 and abs(y - args.base_y) <= 3


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--map", default="maps/middle_road2.json")
    ap.add_argument("--mode", choices=("stand", "run"), default="stand")
    ap.add_argument("--dir", choices=("right", "left"), default="right",
                    help="approach side (stand) / run direction (run)")
    ap.add_argument("--x", type=int, default=99, help="stand: minimap x to jump from")
    ap.add_argument("--tol", type=int, default=0, help="stand: line-up tolerance (minimap px)")
    ap.add_argument("--takeoff", type=int, default=93, help="run: minimap x where she jumps")
    ap.add_argument("--runup", type=int, default=8, help="run: px of run-up before the take-off")
    ap.add_argument("--air-hold", type=float, default=0.3, help="run: s the arrow stays held after the jump")
    ap.add_argument("--walkoff", type=int, default=12,
                    help="px she walks away from the spot before each try (then lines up again)")
    ap.add_argument("--base-y", type=int, default=96, help="floor she jumps from (minimap y)")
    ap.add_argument("--target-y", type=int, default=51, help="floor the ladder leads to (minimap y)")
    ap.add_argument("--trials", type=int, default=5)
    args = ap.parse_args()

    cfg = core.watermap.load_map(args.map)
    core.apply_map_minimap(cfg)
    if cfg.get("map_box"):
        core.set_map_box(*cfg["map_box"])
    keys = {"right": core.Key.right, "left": core.Key.left, "up": core.Key.up,
            "down": core.Key.down, "jump": core.JUMP}
    if not core.focus():
        print("[ladder] could not focus the game window"); return
    time.sleep(0.3)
    read = fast_reader()
    os.makedirs("logs", exist_ok=True)
    label = (f"stand x={args.x} tol={args.tol} from {args.dir}" if args.mode == "stand" else
             f"run {args.dir} takeoff={args.takeoff} runup={args.runup} air={args.air_hold}")
    print(f"[ladder] {label}  ({args.trials} trials, base y={args.base_y} -> target y={args.target_y})")
    tally = {"hit": 0, "miss": 0, "no-jump": 0, "stuck": 0}
    try:
        for i in range(1, args.trials + 1):
            if not reset(args, keys, read):
                print(f"[ladder] trial {i}: not on the base floor (y={read()[1]}) -- stopping"); break
            res, xj, y = trial(args, keys, read)
            tally[res] += 1
            print(f"[ladder] trial {i}: {res.upper():5}  jumped at x={xj}  ended y={y}")
            with open("logs/ladder_test.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "map": args.map,
                                     "mode": args.mode, "dir": args.dir, "x": args.x, "tol": args.tol,
                                     "takeoff": args.takeoff, "runup": args.runup,
                                     "air_hold": args.air_hold, "jump_x": xj, "result": res,
                                     "end_y": y}) + "\n")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("[ladder] stopped")
    finally:
        core.kb.safe_release_all()
    n = sum(tally.values())
    print(f"[ladder] {label}: {tally['hit']}/{n} hit, {tally['miss']} miss, "
          f"{tally['no-jump']} no-jump, {tally['stuck']} stuck")


if __name__ == "__main__":
    main()

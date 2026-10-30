"""Safety subsystem: lie-check scans, alarms, background safety monitor, free-market
auto-escape, and enemy-dot detection. Extracted verbatim from core.py (behavior unchanged).

All module-level STATE stays in core.py (alarms, alerts, tick timers, FM state, thread
handles, ENEMY_THRESHOLD, minimap bounds); these functions read/write it as core.<name>, so
monkeypatch.setattr(core, ...) still binds it. core.py re-exports every function below, so
callers/tests keep using core.<name>.
"""
import core

def dump_frame(reason="manual", frame=None, tag="", folder=None):
    """Save a FULL capture() frame to datasets/lie_check/captures/ for offline recall tuning.
    F10 dumps the current frame on demand -- use it to grab a lie-check screen the detector
    MISSED (the one case auto-dump can't see, since it only fires on a hit). The fast/full
    ticks also call this when they fire, so real live-res positives accumulate for threshold
    tuning. Reuses the passed `frame` when given (no extra grab). Never raises -- this rides
    the safety path and must not crash the loop. `folder` overrides the captures dir."""
    try:
        f = frame if frame is not None else core.capture()
        if f is None or not hasattr(f, "shape"):
            print("[dump] no frame (window gone?)")
            return None
        folder = folder or core._LIE_CAP_DIR
        core.os.makedirs(folder, exist_ok=True)
        ts = core.time.strftime("%Y%m%d_%H%M%S", core.time.localtime()) + f"_{int((core.time.time() % 1) * 1000):03d}"
        safe_tag = "".join(c for c in tag if c.isalnum() or c in "-_.")
        name = f"{ts}_{reason}" + (f"_{safe_tag}" if safe_tag else "") + ".png"
        path = core.os.path.join(folder, name)
        core.cv2.imwrite(path, f)
        print(f"[dump] saved {path}  ({f.shape[1]}x{f.shape[0]})")
        return path
    except Exception as e:
        print(f"[dump] failed: {e}")
        return None


def _hit_tag(hits):
    """Compact filename tag from detection hits: names+scores, e.g. 'curse_lock0.85'."""
    return "_".join(f"{n.replace('.png', '')}{s:.2f}" for n, s in hits)


def lie_check_fast_tick(interval=0.8):
    """Fast SAFETY scan (~40ms) on ONE capture at `interval` cadence, run in the hot loops:
    BOTH the transparent-shape lie-check (captcha -> alarm) AND the another-player check
    (red dot on the minimap -> alarm + auto-pause), plus the whisper check (Discord note +
    a few saved frames). They share this single tick so they run at the same time and
    frequency -- neither can be missed while the other is checked."""
    now = core.time.time()
    if now - core._last_fast_tick[0] < interval:
        return
    core._last_fast_tick[0] = now
    f = core.capture()
    if f is None or not hasattr(f, "shape"):
        return
    if core._lie_enabled:                                       # transparent-shape captcha -> alarm
        hits = core.detect_lie_check(f, templates_folder=core._LIE_DIR,
                                template_filter=core._FAST_TEMPLATES, work_width=520)
        if core._fast_alert.update(bool(hits)) and hits:
            print(f"[lie-check] ⚠️ 透明圖形驗證 {[(n, round(s, 2)) for n, s in hits]} -- ALARM ('n' 暫停)")
            core.notify.send("lie_check", f"⚠️ 透明圖形驗證 (captcha) detected {[n for n, _ in hits]} — needs a human ('n' 暫停)")
            core.dump_frame("fast_auto", f, tag=core._hit_tag(hits))
    dots = core._enemy_dots(f)                                  # another player -> alarm + pause
    if core.dbg_on():
        core.dbg(f"[enemy] scan -> {len(dots)} red dot(s)" + (f" at {dots}" if dots else ""))
    if dots:                                                    # panel mode (never an alarm):
        stop = core._ENEMY_MODE[0] == "stop"                    # the LOOP stops / Free Markets
        msg = ("⚠️ Another player on the map — bot stopped, Free Market if they stay"
               if stop else "ℹ️ Another player on the map — still farming")
        if core.notify.send("another_player", msg):             # notify: <= 1 post / 120 s
            print(f"[enemy] another player on the map -> {'stop' if stop else 'notify only'}")
    if core._WHISPER_MODE[0] != "off" and core._whisper_watch.update(f):   # whisper -> Discord
        print("[whisper] 💬 blue chat line -> someone whispered you")
        core.notify.send("whisper", "💬 有人密你 — a whisper (blue text) just showed in the chat bar")
        core._whisper_dumps_left[0] = core._WHISPER_DUMP_FRAMES
    if core._whisper_dumps_left[0] > 0:                         # trigger frame + the next ticks'
        n = core._WHISPER_DUMP_FRAMES - core._whisper_dumps_left[0] + 1
        core._whisper_dumps_left[0] -= 1
        core.dump_frame("whisper", f, tag=f"{n}of{core._WHISPER_DUMP_FRAMES}",
                        folder=core._WHISPER_CAP_DIR)


def lie_check_full_tick(interval=1.5, template_every=15.0):
    """Full check for the slower screens (curse / monster). The YOLO check (~16 ms) runs every
    tick; the picture templates cost ~1.5 s of CPU per call (measured live 2026-09-26), which kept
    a core busy non-stop next to the farm loop, so they only run every `template_every` s as a
    backup (the monster popup that templates missed on middle_road2 scored 0.92 on YOLO)."""
    if not core._lie_enabled:
        return
    now = core.time.time()
    if now - core._last_full_tick[0] < interval:
        return
    core._last_full_tick[0] = now
    f = core.capture()
    if f is None or not hasattr(f, "shape"):
        return
    # higher work_width keeps the curse banner/lock detail (fine 2-line text + icon)
    # so they clear threshold on smaller live windows; per-template thresholds apply.
    scores = {}
    hits = []
    if now - core._last_full_tpl[0] >= template_every:
        core._last_full_tpl[0] = now
        hits = core.detect_lie_check(f, templates_folder=core._LIE_DIR,
                                     template_filter=core._FULL_TEMPLATES, work_width=1000,
                                     scores=scores)
    yolo = core.detect_lie_check_yolo(f, conf=0.80)           # [] when the model file is absent
    #   conf 0.80: real popups score 0.978-0.991; worst false detection on normal frames was
    #   0.701 (a minimap-crop debug image) -- 0.80 clears positives with headroom for degraded
    #   live frames while staying above that floor. Recall-first, measured not guessed.
    hits = hits + [(f"yolo:{cls}", cf) for cls, cf, _ in yolo]
    if core._full_alert.update(bool(hits)) and hits:
        print(f"[lie-check] ⚠️ 需真人處理畫面 {[(n, round(s, 2)) for n, s in hits]} -- ALARM ('n' 暫停)")
        core.notify.send("lie_check", f"⚠️ 需真人處理畫面 (curse/monster) {[n for n, _ in hits]} — needs a human ('n' 暫停)")
        core.dump_frame("full_auto", f, tag=core._hit_tag(hits))
    elif not hits:                                        # near-miss capture (below threshold)
        near = [(n, s) for n, s in scores.items() if s >= core._NEAR_MISS_FLOOR]
        if near and now - core._last_near_miss_dump[0] >= core._NEAR_MISS_INTERVAL:
            core._last_near_miss_dump[0] = now
            near.sort(key=lambda x: -x[1])
            print(f"[lie-check] near-miss (below threshold) {[(n, round(s, 2)) for n, s in near]} -- dumping frame")
            core.dump_frame("near_miss", f, tag=core._hit_tag(near))


def lie_check_tick():
    """Loop-top check: run both pipelines (fast covers transparent between states too)."""
    core.lie_check_fast_tick()
    core.lie_check_full_tick()


def _safety_monitor_loop():
    while not core._safety_stop.is_set() and not core.STOP.is_set():
        if not core.kb.pause:
            try:
                core.lie_check_full_tick()                  # self-throttled to 1.5s internally
                core.STATUS["lie"] = core.is_lie_check_active()
            except Exception as e:
                print(f"[safety] monitor error: {e}")
        try:
            core._maybe_escalate_to_free_market()           # OUTSIDE the pause gate: the enemy alarm fires
        except Exception as e:                          # a pause, and the escape must run DURING it
            print(f"[free-market] monitor error: {e}")
        core._safety_stop.wait(0.3)                          # poll ~3x/s; full_tick paces the real work


def start_safety_monitor():
    """Start the background FULL lie-check monitor (idempotent). Call at farm start."""
    if not core._lie_enabled:
        return
    t = core._safety_thread[0]
    if t is not None and t.is_alive():
        return
    core._safety_stop.clear()
    t = core.threading.Thread(target=core._safety_monitor_loop, name="safety-monitor", daemon=True)
    core._safety_thread[0] = t
    t.start()
    print("[safety] background curse/monster monitor started (off the action loop)")


def stop_safety_monitor():
    """Stop the background monitor (idempotent). Call at every farm-loop exit."""
    core._safety_stop.set()
    t = core._safety_thread[0]
    if t is not None and t.is_alive() and t is not core.threading.current_thread():
        t.join(timeout=1.0)
    core._safety_thread[0] = None


def lie_check_silence():
    """Stop both alarms (e.g. when the bot pauses -- a human is present)."""
    core._fast_alert.update(False)
    core._full_alert.update(False)


def is_lie_check_active():
    """True while either lie-check alarm is sounding (a human is needed)."""
    return bool(core._fast_alert.alarm.active or core._full_alert.alarm.active)


def enemy_alarm_on():
    """Start the another-player alarm (keeps beeping until 'm' / silence)."""
    core._enemy_alarm.start()


def enemy_alarm_silence():
    core._enemy_alarm.stop()


def silence_all_alarms():
    """'m': acknowledge -- stop every alarm (lie-check + another-player). Quiet when nothing
    is sounding, so 'm' stays usable for other things (e.g. record-route node marks)."""
    active = core.is_lie_check_active() or core._enemy_alarm.active
    core.lie_check_silence()
    core.enemy_alarm_silence()
    if active:
        print("[alarm] silenced ('m')")


def _click_screen(x, y):
    """Left-click at a PHYSICAL screen pixel. The process is DPI-aware at runtime, so these match
    _window_box()/mss coords and SetCursorPos lands where the capture frame says."""
    u = core.ctypes.windll.user32
    u.SetCursorPos(int(x), int(y)); core.time.sleep(0.05)
    u.mouse_event(0x0002, 0, 0, 0, 0)          # LEFTDOWN
    core.time.sleep(0.03)
    u.mouse_event(0x0004, 0, 0, 0, 0)          # LEFTUP


def click_free_market():
    """Focus the game and click 自由市場 to leave the map. Screen point = window box + the button's
    frame offset (recomputed live). One click -- it opens/teleports directly."""
    fx, fy = core._FM_XY[0]
    pt = core.game_to_screen(fx, fy)             # canonical frame point -> real screen pixel
    if pt is None:
        print("[free-market] game window not found -> cannot click"); return
    sx, sy = pt
    print(f"[free-market] enemy alarm un-acked {core._FM_AFTER[0]:g}s -> click 自由市場 @ screen ({sx},{sy})")
    try:
        core.notify.send("another_player",
                    f"⚠️ No response in {core._FM_AFTER[0]:g}s — auto-clicking Free Market to leave the map")
    except Exception:
        pass
    if not core.focus():
        print("[free-market] could not focus game -> skip click"); return
    core._click_screen(sx, sy)
    core.enemy_alarm_silence()                            # escaped the map -> the action is done, so stop
    print("[free-market] clicked -> alarm silenced")  # the alarm (no human needed to ack it now)


def _maybe_escalate_to_free_market():
    """If the enemy alarm has sounded UN-acknowledged for _FM_AFTER seconds, click Free Market once.
    Tracks the alarm's continuous-active time; silencing it ('m') drops _enemy_alarm.active and
    resets here -- so pressing 'm' within the window cancels the escape. Runs from the safety
    monitor thread (which keeps ticking while the bot is paused, i.e. exactly the enemy state)."""
    if not core._FM_ESCAPE[0] or not core._enemy_alarm.active:
        core._enemy_alarm_since[0] = None
        core._fm_clicked[0] = False
        return
    now = core.time.time()
    if core._enemy_alarm_since[0] is None:
        core._enemy_alarm_since[0] = now
    elif not core._fm_clicked[0] and now - core._enemy_alarm_since[0] >= core._FM_AFTER[0]:
        core._fm_clicked[0] = True                        # click ONCE; won't repeat until silenced
        try:
            core.click_free_market()
        except Exception as e:
            print(f"[free-market] click failed: {e}")


def set_enemy_threshold(t):
    core.ENEMY_THRESHOLD = float(t)


def _dump_enemy_sample(frame, tag=""):
    """Save a FULL frame to datasets/enemy_dots/samples/ for offline template-vs-color review.
    Best-effort -- never raises (rides the safety path). Off unless core._ENEMY_SAMPLE_DUMPS: the
    shadow comparison fires constantly on red-heavy maps (18 GB of frames, ~1.7 GB/day)."""
    if not core._ENEMY_SAMPLE_DUMPS:
        return None
    try:
        core.os.makedirs(core._ENEMY_SAMPLE_DIR, exist_ok=True)
        ts = core.time.strftime("%Y%m%d_%H%M%S", core.time.localtime()) + f"_{int((core.time.time() % 1) * 1000):03d}"
        safe = "".join(ch for ch in tag if ch.isalnum() or ch in "-_.")
        path = core.os.path.join(core._ENEMY_SAMPLE_DIR, f"{ts}_enemy_{safe}.png")
        core.cv2.imwrite(path, frame)
        return path
    except Exception:
        return None


def _append_enemy_shadow_log(row):
    """Append one shadow-comparison row (ts, template count, color count, agree) as JSON.
    Best-effort -- never raises."""
    try:
        import json
        core.os.makedirs(core.os.path.dirname(core._ENEMY_SHADOW_LOG) or ".", exist_ok=True)
        with open(core._ENEMY_SHADOW_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _enemy_shadow(frame, mm, template_dots):
    """Double-check the color detector against the authoritative template result on this frame.
    Logs every activity and dumps a sample (throttled) whenever either detector fires. Compares
    at the PRESENCE level (len>0) -- the boolean the safety check actually uses. Never raises."""
    try:
        color_dots = core.detect_red_dots_color(mm)
    except Exception:
        return
    t, c = len(template_dots), len(color_dots)
    if t == 0 and c == 0:
        return                                     # all-clear frame -> nothing to learn
    agree = (t > 0) == (c > 0)
    now = core.time.time()
    core._append_enemy_shadow_log({"ts": core.time.strftime("%Y-%m-%d %H:%M:%S", core.time.localtime(now)),
                              "template": t, "color": c, "agree": agree})
    key = "agree" if agree else "disagree"
    interval = core._ENEMY_AGREE_DUMP_INTERVAL if agree else core._ENEMY_DISAGREE_DUMP_INTERVAL
    if now - core._enemy_shadow_state[key] >= interval:
        core._enemy_shadow_state[key] = now
        core._dump_enemy_sample(frame, f"t{t}_c{c}_{'agree' if agree else 'DISAGREE'}")


def _enemy_dots(frame):
    """Red dots (other players) in the minimap band of a captured BGR frame. TEMPLATE match is
    authoritative (drives the another-player alarm); detect_red_dots_color runs in shadow to
    validate the switch (see _enemy_shadow). Presence-only -> non-empty means escape."""
    if frame is None or core._ENEMY_MODE[0] == "off":       # turned off in the panel
        return []
    mm = core._minimap(frame)              # FULL configured minimap band, at the reference scale
    try:
        dots = core.detect_red_dots(mm, templates_folder="assets/minimap_other_character/",
                               threshold=core.ENEMY_THRESHOLD)
    except Exception:
        return []
    try:
        core._enemy_shadow(frame, mm, dots)            # shadow double-check: never affects the return
    except Exception:
        pass
    return dots


def get_enemy():
    """Red dots (other players) on the minimap band. Non-empty -> escape. Standalone capture
    (used by guard/panel); the hot-loop scan shares lie_check_fast_tick's frame."""
    return core._enemy_dots(core.capture())

"""Capture the player's NAME TAG for the KenYu-style name-tag anchor.

The name tag (your character's name under the sprite) is the robust player anchor: it's
matched by WHITE-MASK (only the text, so the semi-transparent box background doesn't
matter) with local-cache tracking. It IS character-specific, so recapture per character.

Usage (stand still so the tag is unobscured, ideally between attacks):
    python capture_nametag.py deep_sea_2           # the NAME tag
    python capture_nametag.py deep_sea_2 title     # the 稱號/title banner (secondary anchor)
Drag a TIGHT box around JUST the text, ENTER to save, C/ESC to cancel.
Saves assets/player/<map>_<kind>.png. Then set in maps/<map>.json:
    "anchor": "nametag", "nametag_template": "assets/player/<map>_nametag.png"
    (secondary)          "title_template":   "assets/player/<map>_title.png"
"""
import os
import sys
import cv2


def main():
    if len(sys.argv) < 2:
        print("usage: python capture_nametag.py <map>   (e.g. deep_sea_2)")
        return
    mp = sys.argv[1]
    kind = sys.argv[2] if len(sys.argv) > 2 else "nametag"    # 'nametag' or 'title'
    import recovery
    if not recovery.focus():
        print("could not focus the game window"); return
    import time
    time.sleep(0.4)
    f = recovery.capture()
    if f is None:
        print("no frame -- game window visible?"); return
    print(f"Drag a TIGHT box around your {kind} text only, then ENTER to save (C/ESC cancel).")
    x, y, w, h = cv2.selectROI(f"select {kind}", f, showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()
    if w == 0 or h == 0:
        print("no selection -> cancelled"); return
    os.makedirs(os.path.join("assets", "player"), exist_ok=True)
    out = os.path.join("assets", "player", f"{mp}_{kind}.png")
    cv2.imwrite(out, f[y:y + h, x:x + w])
    key = "title_template" if kind == "title" else "nametag_template"
    print(f"saved {out}  ({w}x{h})")
    print(f'set in maps/{mp}.json:  "{key}": "{out.replace(os.sep, "/")}"')


if __name__ == "__main__":
    main()

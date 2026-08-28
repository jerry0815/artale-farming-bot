"""Capture the player's NAME TAG for the KenYu-style name-tag anchor.

The name tag (your character's name under the sprite) is the robust player anchor: it's
matched by WHITE-MASK (only the text, so the semi-transparent box background doesn't
matter) with local-cache tracking. It IS character-specific, so recapture per character.

Usage (stand still so the name tag is unobscured, ideally between attacks):
    python capture_nametag.py deep_sea_2
Drag a TIGHT box around JUST your name text, ENTER to save, C/ESC to cancel.
Saves assets/player/<map>_nametag.png. Then set in maps/<map>.json:
    "anchor": "nametag", "nametag_template": "assets/player/<map>_nametag.png"
"""
import os
import sys
import cv2


def main():
    if len(sys.argv) < 2:
        print("usage: python capture_nametag.py <map>   (e.g. deep_sea_2)")
        return
    mp = sys.argv[1]
    import recovery
    if not recovery.focus():
        print("could not focus the game window"); return
    import time
    time.sleep(0.4)
    f = recovery.capture()
    if f is None:
        print("no frame -- game window visible?"); return
    print("Drag a TIGHT box around your NAME text only, then ENTER to save (C/ESC cancel).")
    x, y, w, h = cv2.selectROI("select name tag", f, showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()
    if w == 0 or h == 0:
        print("no selection -> cancelled"); return
    os.makedirs(os.path.join("assets", "player"), exist_ok=True)
    out = os.path.join("assets", "player", f"{mp}_nametag.png")
    cv2.imwrite(out, f[y:y + h, x:x + w])
    print(f"saved {out}  ({w}x{h})")
    print(f'set in maps/{mp}.json:  "anchor": "nametag", "nametag_template": "{out.replace(os.sep, "/")}"')
    print("tune nametag_feet_offset (feet vs name-tag top) and nametag_accept (match cutoff) if needed")


if __name__ == "__main__":
    main()

"""Multi-class box labeler for water mobs (fishhouse=0, goby=1), for the hybrid
synthetic+real training set.

Workflow:
  1. Grab real frames to label (from a recording), PRE-LABELLED by the current model so you
     only fix its mistakes:
        python label_mobs.py --grab "C:/.../clip.mp4" 40
     (or --grab-dir <folder-of-pngs> to import existing screenshots)
  2. Label / correct in the browser:
        python label_mobs.py                 # http://localhost:8001
     drag = new box (in the ACTIVE class) | click a box then 1/2 = set its class,
     Del = remove | ArrowRight/Left = next/prev (auto-saves) | 1/2 = active class.
  3. Retrain with real frames mixed in:  python train_mobs.py 80 640 --real

Real frames land in datasets/mobs_real/{images,labels}; train_mobs.py --real adds them.
"""
import os
import glob
import json
import http.server
import socketserver
import urllib.parse

import cv2

CLASSES = ["fishhouse", "goby", "player"]
COLORS = ["#ff3b3b", "#3bd23b", "#4aa3ff"]           # fishhouse red, goby green, player blue
ROOT = os.path.join("datasets", "mobs_real")
IMG_DIR = os.path.join(ROOT, "images")
LBL_DIR = os.path.join(ROOT, "labels")
PORT = 8001


def frames():
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(IMG_DIR, "*.png")))


def read_boxes(stem):
    p = os.path.join(LBL_DIR, stem + ".txt")
    out = []
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            t = line.split()
            if len(t) == 5:
                out.append([int(float(t[0])), float(t[1]), float(t[2]), float(t[3]), float(t[4])])
    return out


def write_boxes(stem, boxes):
    os.makedirs(LBL_DIR, exist_ok=True)
    with open(os.path.join(LBL_DIR, stem + ".txt"), "w", encoding="utf-8") as f:
        for ci, cx, cy, w, h in boxes:
            f.write(f"{int(ci)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n")


def _prelabel(img_bgr, model, conf=0.4, imgsz=640):
    """Model predictions -> YOLO boxes [[cls,cx,cy,w,h]] normalized (initial labels)."""
    H, W = img_bgr.shape[:2]
    r = model.predict(img_bgr, imgsz=imgsz, conf=conf, verbose=False)[0]
    out = []
    for b in r.boxes:
        x0, y0, x1, y1 = b.xyxy[0].tolist()
        out.append([int(b.cls[0]), ((x0 + x1) / 2) / W, ((y0 + y1) / 2) / H,
                    (x1 - x0) / W, (y1 - y0) / H])
    return out


def grab_from_video(video, count, model=None):
    os.makedirs(IMG_DIR, exist_ok=True)
    v = cv2.VideoCapture(video)
    n = int(v.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, n // max(1, count))
    saved = 0
    for f in range(0, n, step):
        if saved >= count:
            break
        v.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, fr = v.read()
        if not ok:
            continue
        stem = f"real_{f:06d}"
        if os.path.exists(os.path.join(LBL_DIR, stem + ".txt")):
            continue                                  # already labeled -> never clobber hand work
        cv2.imwrite(os.path.join(IMG_DIR, stem + ".png"), fr)
        if model is not None:
            write_boxes(stem, _prelabel(fr, model))
        saved += 1
    v.release()
    return saved


def grab_from_dir(src, model=None):
    os.makedirs(IMG_DIR, exist_ok=True)
    saved = 0
    for p in sorted(glob.glob(os.path.join(src, "*.png")) + glob.glob(os.path.join(src, "*.jpg"))):
        fr = cv2.imread(p)
        if fr is None:
            continue
        stem = "real_" + os.path.splitext(os.path.basename(p))[0]
        if os.path.exists(os.path.join(LBL_DIR, stem + ".txt")):
            continue                                  # already labeled -> never clobber hand work
        cv2.imwrite(os.path.join(IMG_DIR, stem + ".png"), fr)
        if model is not None:
            write_boxes(stem, _prelabel(fr, model))
        saved += 1
    return saved


PAGE = """<!doctype html><html><head><meta charset=utf-8><title>mob labeler</title>
<style>
 body{margin:0;font-family:system-ui;background:#111;color:#eee}
 #bar{position:fixed;top:0;left:0;right:0;height:44px;background:#1c1c22;display:flex;
      align-items:center;gap:12px;padding:0 14px;z-index:10}
 #bar b{color:#6cf} button{background:#2a2a33;color:#eee;border:1px solid #444;
      border-radius:6px;padding:6px 10px;cursor:pointer} button:hover{background:#3a3a46}
 #wrap{position:absolute;top:52px;left:8px} canvas{cursor:crosshair;display:block}
 #hint{color:#999;font-size:12px} .fh{color:#ff6b6b} .gb{color:#3bd23b}
 #active{font-weight:700}
</style></head><body>
<div id=bar>
 <button onclick="nav(-1)">&larr;</button><button onclick="nav(1)">&rarr;</button>
 <button onclick="save()">Save (S)</button>
 <span id=pos></span> <b id=cnt></b>
 <span>active: <span id=active></span></span>
 <span id=hint>drag = new box &middot; click box then 1/2 = set class &middot; Del = remove &middot;
   1=fishhouse 2=goby 3=player &middot; arrows = prev/next</span>
</div>
<div id=wrap><canvas id=cv></canvas></div>
<script>
const CLASSES=["fishhouse","goby","player"], COLORS=["#ff3b3b","#3bd23b","#4aa3ff"];
let files=[], idx=0, boxes=[], sel=-1, img=new Image(), drag=null, active=0;
const cv=document.getElementById('cv'), ctx=cv.getContext('2d'); const MAXW=1500;
function setActive(a){ active=a; const e=document.getElementById('active');
  e.textContent=CLASSES[a]; e.style.color=COLORS[a]; }
async function boot(){ files=await (await fetch('/api/frames')).json(); idx=0; setActive(0); load(); }
async function load(){
  boxes=await (await fetch('/api/label?name='+files[idx])).json(); sel=-1;
  img=new Image();
  img.onload=()=>{ const s=Math.min(1,MAXW/img.naturalWidth);
    cv.width=img.naturalWidth*s; cv.height=img.naturalHeight*s; draw(); };
  img.src='/img/'+files[idx];
  document.getElementById('pos').textContent=(idx+1)+' / '+files.length+'  '+files[idx];
}
function px(b){ return [(b[1]-b[3]/2)*cv.width,(b[2]-b[4]/2)*cv.height,b[3]*cv.width,b[4]*cv.height]; }
function draw(){
  ctx.drawImage(img,0,0,cv.width,cv.height);
  boxes.forEach((b,i)=>{ const [x,y,w,h]=px(b);
    ctx.lineWidth=i===sel?3:2; ctx.strokeStyle=i===sel?'#ffd23f':COLORS[b[0]];
    ctx.strokeRect(x,y,w,h);
    ctx.fillStyle=COLORS[b[0]]; ctx.font='13px system-ui';
    ctx.fillText(CLASSES[b[0]], x+2, y-3); });
  document.getElementById('cnt').textContent=boxes.length+' boxes';
}
function hit(mx,my){ for(let i=boxes.length-1;i>=0;i--){ const [x,y,w,h]=px(boxes[i]);
    if(mx>=x&&mx<=x+w&&my>=y&&my<=y+h) return i; } return -1; }
cv.onmousedown=e=>{ const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
  const h=hit(mx,my); if(h>=0){ sel=h; draw(); } else { drag={x0:mx,y0:my,x1:mx,y1:my}; sel=-1; } };
cv.onmousemove=e=>{ if(!drag)return; const r=cv.getBoundingClientRect();
  drag.x1=e.clientX-r.left; drag.y1=e.clientY-r.top; draw();
  ctx.strokeStyle=COLORS[active]; ctx.lineWidth=2;
  ctx.strokeRect(drag.x0,drag.y0,drag.x1-drag.x0,drag.y1-drag.y0); };
cv.onmouseup=e=>{ if(!drag)return; const x0=Math.min(drag.x0,drag.x1),y0=Math.min(drag.y0,drag.y1),
  w=Math.abs(drag.x1-drag.x0),h=Math.abs(drag.y1-drag.y0); drag=null;
  if(w>6&&h>6) boxes.push([active,(x0+w/2)/cv.width,(y0+h/2)/cv.height,w/cv.width,h/cv.height]); draw(); };
document.onkeydown=e=>{
  if(e.key==='Delete'||e.key==='Backspace'){ if(sel>=0){ boxes.splice(sel,1); sel=-1; draw(); e.preventDefault(); } }
  else if(e.key==='1'){ if(sel>=0){ boxes[sel][0]=0; draw(); } else setActive(0); }
  else if(e.key==='2'){ if(sel>=0){ boxes[sel][0]=1; draw(); } else setActive(1); }
  else if(e.key==='3'){ if(sel>=0){ boxes[sel][0]=2; draw(); } else setActive(2); }
  else if(e.key==='ArrowRight') nav(1);
  else if(e.key==='ArrowLeft') nav(-1);
  else if(e.key==='s'||e.key==='S') save(); };
async function save(){ await fetch('/api/label?name='+files[idx],{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(boxes)}); }
async function nav(d){ await save(); idx=(idx+d+files.length)%files.length; load(); }
boot();
</script></body></html>"""


def make_handler():
    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, code, ctype, body):
            self.send_response(code); self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/":
                self._send(200, "text/html; charset=utf-8", PAGE.encode())
            elif u.path == "/api/frames":
                self._send(200, "application/json", json.dumps(frames()).encode())
            elif u.path == "/api/label":
                stem = os.path.splitext(os.path.basename(q.get("name", [""])[0]))[0]
                self._send(200, "application/json", json.dumps(read_boxes(stem)).encode())
            elif u.path.startswith("/img/"):
                name = os.path.basename(urllib.parse.unquote(u.path[5:]))
                fp = os.path.join(IMG_DIR, name)
                if os.path.exists(fp):
                    self._send(200, "image/png", open(fp, "rb").read())
                else:
                    self._send(404, "text/plain", b"no")
            else:
                self._send(404, "text/plain", b"no")

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/api/label":
                n = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(n) or b"[]")
                stem = os.path.splitext(os.path.basename(q.get("name", [""])[0]))[0]
                write_boxes(stem, data)
                self._send(200, "application/json", b'{"ok":true}')
            else:
                self._send(404, "text/plain", b"no")
    return H


if __name__ == "__main__":
    import sys
    if "--grab" in sys.argv or "--grab-dir" in sys.argv:
        model = None
        if "--no-prelabel" not in sys.argv:
            try:
                from ultralytics import YOLO
                model = YOLO("models/mob_yolo.pt")
                print("[label] pre-labeling with models/mob_yolo.pt (fix its mistakes)")
            except Exception as e:
                print(f"[label] no model prelabel ({e}); labeling from scratch")
        if "--grab" in sys.argv:
            i = sys.argv.index("--grab")
            video = sys.argv[i + 1]
            count = int(sys.argv[i + 2]) if len(sys.argv) > i + 2 and sys.argv[i + 2].isdigit() else 40
            got = grab_from_video(video, count, model)
        else:
            src = sys.argv[sys.argv.index("--grab-dir") + 1]
            got = grab_from_dir(src, model)
        print(f"[label] grabbed {got} frames -> {IMG_DIR}. Now run: python label_mobs.py")
    else:
        os.makedirs(IMG_DIR, exist_ok=True)
        with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), make_handler()) as srv:
            print(f"labeling {len(frames())} frames -> http://localhost:{PORT}  (Ctrl+C to stop)")
            srv.serve_forever()

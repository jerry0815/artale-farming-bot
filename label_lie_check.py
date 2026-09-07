"""Browser box-labeler for the lie-check MONSTER samples (single class: monster_check = id 0).

Serves datasets/lie_check/samples/monster/*.png in a browser with a box pre-drawn by the
CURRENT YOLO model (models/lie_check_yolo.pt) so you only fix/add -- draw the one it misses
(e.g. the faint bright-map popup), nudge the others. Writes YOLO labels to
datasets/lie_check/samples/monster/labels/, which make_lie_check_data.py will consume.

Run:   python label_lie_check.py            # then open http://localhost:8002
Keys:  drag = new box | click a box then Del/Backspace = remove | ArrowRight/Left =
       next/prev (auto-saves) | S = save now | X = drop frame (bad sample -> _dropped/).
"""
import json, os, glob, shutil, http.server, socketserver, urllib.parse
import cv2

IMG_DIR = os.path.join("datasets", "lie_check", "samples", "monster")
LBL_DIR = os.path.join(IMG_DIR, "labels")
POOL_DIR = os.path.join(IMG_DIR, "_dropped")
PORT = 8002
os.makedirs(LBL_DIR, exist_ok=True)
os.makedirs(POOL_DIR, exist_ok=True)


def frames():
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(IMG_DIR, "*.png")))


def read_boxes(stem):
    p = os.path.join(LBL_DIR, stem + ".txt")
    out = []
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            t = line.split()
            if len(t) == 5:
                out.append([float(t[1]), float(t[2]), float(t[3]), float(t[4])])
    return out


def write_boxes(stem, boxes):
    with open(os.path.join(LBL_DIR, stem + ".txt"), "w", encoding="utf-8") as f:
        for cx, cy, w, h in boxes:
            f.write(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n")   # class 0 = monster_check


def drop_frame(stem):
    img = os.path.join(IMG_DIR, stem + ".png")
    if os.path.exists(img):
        shutil.move(img, os.path.join(POOL_DIR, stem + ".png"))
    lbl = os.path.join(LBL_DIR, stem + ".txt")
    if os.path.exists(lbl):
        os.remove(lbl)


def prelabel():
    """For every frame WITHOUT a label yet, seed it with the current model's monster_check box
    (low conf so weak detections still give a starting box). A frame the model misses gets an
    empty label -> you draw it. Never overwrites a label you've already saved."""
    try:
        import lie_check_yolo as ly
        ly.reset_cache()
    except Exception as e:
        print(f"[label] model unavailable ({e}); starting with empty labels")
        return
    seeded = drawn = 0
    for name in frames():
        stem = name[:-4]
        if os.path.exists(os.path.join(LBL_DIR, stem + ".txt")):
            continue
        im = cv2.imread(os.path.join(IMG_DIR, name))
        h, w = im.shape[:2]
        dets = [d for d in ly.detect_lie_check_yolo(im, conf=0.15) if d[0] == "monster_check"]
        boxes = []
        for _c, _cf, (x0, y0, x1, y1) in dets:
            boxes.append([(x0 + x1) / 2 / w, (y0 + y1) / 2 / h, (x1 - x0) / w, (y1 - y0) / h])
        write_boxes(stem, boxes)
        seeded += 1
        drawn += bool(boxes)
    print(f"[label] pre-labeled {seeded} frames ({drawn} with a model box, {seeded - drawn} empty -> draw them)")


PAGE = """<!doctype html><html><head><meta charset=utf-8><title>monster lie-check labeler</title>
<style>
 body{margin:0;font-family:system-ui;background:#111;color:#eee}
 #bar{position:fixed;top:0;left:0;right:0;height:44px;background:#1c1c22;display:flex;
      align-items:center;gap:14px;padding:0 14px;z-index:10}
 #bar b{color:#6cf} button{background:#2a2a33;color:#eee;border:1px solid #444;
      border-radius:6px;padding:6px 10px;cursor:pointer} button:hover{background:#3a3a46}
 #wrap{position:absolute;top:52px;left:8px} canvas{cursor:crosshair;display:block}
 #hint{color:#999;font-size:13px}
</style></head><body>
<div id=bar>
 <button onclick="nav(-1)">&larr; Prev</button>
 <button onclick="nav(1)">Next &rarr;</button>
 <button onclick="save()">Save (S)</button>
 <span id=pos></span> <b id=cnt></b>
 <button onclick="dropFrame()">Drop frame (X)</button>
 <span id=hint>monster_check &middot; drag = new box &middot; click box then Del = remove &middot; X = drop bad frame &middot; arrows = prev/next (auto-save)</span>
</div>
<div id=wrap><canvas id=cv></canvas></div>
<script>
let files=[], idx=0, boxes=[], sel=-1, img=new Image(), drag=null;
const cv=document.getElementById('cv'), ctx=cv.getContext('2d');
const MAXW=1400;
async function boot(){ files=await (await fetch('/api/frames')).json(); idx=0; load(); }
async function load(){
  boxes=await (await fetch('/api/label?name='+files[idx])).json(); sel=-1;
  img=new Image();
  img.onload=()=>{ const s=Math.min(1,MAXW/img.naturalWidth);
    cv.width=img.naturalWidth*s; cv.height=img.naturalHeight*s; draw(); };
  img.src='/img/'+files[idx];
  document.getElementById('pos').textContent=(idx+1)+' / '+files.length+'  '+files[idx];
}
function draw(){
  ctx.drawImage(img,0,0,cv.width,cv.height);
  boxes.forEach((b,i)=>{ const [x,y,w,h]=px(b);
    ctx.lineWidth=i===sel?3:2; ctx.strokeStyle=i===sel?'#ffd23f':'#ff3b3b';
    ctx.strokeRect(x,y,w,h); });
  document.getElementById('cnt').textContent=boxes.length+' boxes';
}
function px(b){ return [(b[0]-b[2]/2)*cv.width,(b[1]-b[3]/2)*cv.height,b[2]*cv.width,b[3]*cv.height]; }
function hit(mx,my){ for(let i=boxes.length-1;i>=0;i--){ const [x,y,w,h]=px(boxes[i]);
    if(mx>=x&&mx<=x+w&&my>=y&&my<=y+h) return i; } return -1; }
cv.onmousedown=e=>{ const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
  const h=hit(mx,my); if(h>=0){ sel=h; draw(); } else { drag={x0:mx,y0:my,x1:mx,y1:my}; sel=-1; } };
cv.onmousemove=e=>{ if(!drag)return; const r=cv.getBoundingClientRect();
  drag.x1=e.clientX-r.left; drag.y1=e.clientY-r.top; draw();
  ctx.strokeStyle='#3bf'; ctx.lineWidth=2;
  ctx.strokeRect(drag.x0,drag.y0,drag.x1-drag.x0,drag.y1-drag.y0); };
cv.onmouseup=e=>{ if(!drag)return; const x0=Math.min(drag.x0,drag.x1),y0=Math.min(drag.y0,drag.y1),
  w=Math.abs(drag.x1-drag.x0),h=Math.abs(drag.y1-drag.y0); drag=null;
  if(w>6&&h>6) boxes.push([(x0+w/2)/cv.width,(y0+h/2)/cv.height,w/cv.width,h/cv.height]); draw(); };
document.onkeydown=e=>{ if(e.key==='Delete'||e.key==='Backspace'){ if(sel>=0){ boxes.splice(sel,1); sel=-1; draw(); e.preventDefault(); } }
  else if(e.key==='ArrowRight') nav(1);
  else if(e.key==='ArrowLeft') nav(-1);
  else if(e.key==='x'||e.key==='X') dropFrame();
  else if(e.key==='s'||e.key==='S') save(); };
async function dropFrame(){
  const name=files[idx];
  await fetch('/api/drop?name='+name,{method:'POST'});
  files.splice(idx,1);
  if(files.length===0){ document.getElementById('pos').textContent='(no frames left)'; return; }
  if(idx>=files.length) idx=0;
  load();
}
async function save(){ await fetch('/api/label?name='+files[idx],{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(boxes)}); }
async function nav(d){ await save(); idx=(idx+d+files.length)%files.length; load(); }
boot();
</script></body></html>"""


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
        elif u.path == "/api/drop":
            stem = os.path.splitext(os.path.basename(q.get("name", [""])[0]))[0]
            drop_frame(stem)
            self._send(200, "application/json", b'{"ok":true}')
        else:
            self._send(404, "text/plain", b"no")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True     # restart without "address already in use"
    daemon_threads = True          # don't let a lingering keep-alive connection block others


def main():
    prelabel()
    with _Server(("127.0.0.1", PORT), H) as httpd:
        print(f"[label] monster lie-check labeler at http://localhost:{PORT}  (Ctrl+C to quit)")
        print(f"[label] {len(frames())} frames in {IMG_DIR} -> labels in {LBL_DIR}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()

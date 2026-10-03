"""hellolite - send a file (up to 1GB) from one device to another with a link or QR code.

Run:
    pip install fastapi uvicorn segno
    python hellolite.py
Then open the printed address on any device on the same network.
"""
import asyncio, os, re, secrets, shutil, socket, tempfile, time, io, html, mimetypes
from pathlib import Path
from urllib.parse import quote, unquote

import segno, uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

MAX_BYTES = 1024 * 1024 * 1024      # 1 GB
CHUNK = 1024 * 1024                 # 1 MB
TTL = 15 * 60                       # unclaimed files expire after 15 minutes
PORT = int(os.environ.get("PORT", 8000))
STORE = Path(tempfile.mkdtemp(prefix="hellolite_"))
FILES: dict[str, dict] = {}         # id -> {path, name, size, created, active}
DONE: dict[str, float] = {}         # id -> time it finished downloading (for status messages)

def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1)); return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()

def drop(fid: str, delivered: bool = False):
    meta = FILES.pop(fid, None)
    if meta:
        try:
            Path(meta["path"]).unlink(missing_ok=True)
        except OSError:
            pass
        if delivered:
            DONE[fid] = time.time()

async def sweeper():
    while True:
        await asyncio.sleep(30)
        now = time.time()
        for fid in [k for k, v in FILES.items() if now - v["created"] > TTL and not v["active"]]:
            drop(fid)
        for fid in [k for k, t in DONE.items() if now - t > 3600]:
            DONE.pop(fid, None)

app = FastAPI(docs_url=None, redoc_url=None)

@app.on_event("startup")
async def _start():
    asyncio.create_task(sweeper())

@app.on_event("shutdown")
async def _stop():
    shutil.rmtree(STORE, ignore_errors=True)

@app.middleware("http")
async def headers(request: Request, call_next):
    r = await call_next(request)
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "no-referrer"
    return r

# ---------- API ----------
@app.post("/upload")
async def upload(request: Request):
    # The browser sends the name URL-encoded so non-English file names survive.
    raw = unquote(request.headers.get("x-filename", "file"))
    name = re.sub(r"[\x00-\x1f\x7f]", "", os.path.basename(raw.replace("\\", "/"))).strip() or "file"
    declared = int(request.headers.get("content-length") or 0)
    if declared > MAX_BYTES:
        return JSONResponse({"error": "File is larger than 1 GB."}, 413)
    if shutil.disk_usage(STORE).free < declared + 200 * CHUNK:
        return JSONResponse({"error": "Server is out of disk space."}, 507)
    fid = secrets.token_urlsafe(6)
    path = STORE / fid
    total = 0
    try:
        with open(path, "wb") as f:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError
                f.write(chunk)
    except ValueError:
        path.unlink(missing_ok=True)
        return JSONResponse({"error": "File is larger than 1 GB."}, 413)
    except Exception:
        path.unlink(missing_ok=True)
        return JSONResponse({"error": "Upload was interrupted."}, 400)
    FILES[fid] = {"path": str(path), "name": name, "size": total, "created": time.time(), "active": 0}
    scheme = "https" if os.environ.get("SSL_CERT") else "http"
    base = f"{scheme}://{lan_ip()}:{PORT}" if request.url.hostname in ("localhost", "127.0.0.1") else str(request.base_url).rstrip("/")
    return {"id": fid, "url": f"{base}/d/{fid}"}

@app.get("/qr")
async def qr(u: str):
    buf = io.BytesIO()
    segno.make(u, error="m").save(buf, kind="svg", scale=6, border=1, dark="#10233f", light=None)
    return Response(buf.getvalue(), media_type="image/svg+xml")

@app.get("/s/{fid}")
async def status(fid: str):
    meta = FILES.get(fid)
    if meta:
        return {"state": "downloading" if meta["active"] else "ready"}
    return {"state": "done" if fid in DONE else "expired"}

def parse_range(header: str | None, size: int):
    """Return (start, end), "bad" for an unsatisfiable range, or None for no range."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not m or not (m[1] or m[2]):
        return None
    if m[1]:
        a = int(m[1]); b = int(m[2]) if m[2] else size - 1
    else:
        a = max(size - int(m[2]), 0); b = size - 1
    b = min(b, size - 1)
    return "bad" if a > b else (a, b)

@app.api_route("/f/{fid}", methods=["GET", "HEAD"])
async def fetch(fid: str, request: Request):
    meta = FILES.get(fid)
    if not meta:
        return JSONResponse({"error": "This link has expired."}, 404)
    size, name = meta["size"], meta["name"]

    rng = parse_range(request.headers.get("range"), size) if size else None
    if rng == "bad":
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    start, end = rng if rng else (0, size - 1)

    fallback = re.sub(r"[^A-Za-z0-9._ -]", "_", name) or "file"
    hdrs = {
        "Content-Length": str(max(end - start + 1, 0)),
        "Content-Disposition": f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}",
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    if rng:
        hdrs["Content-Range"] = f"bytes {start}-{end}/{size}"
    code = 206 if rng else 200
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"

    if request.method == "HEAD":
        return Response(status_code=code, media_type=ctype, headers=hdrs)

    def stream():
        left = end - start + 1
        meta["active"] += 1
        try:
            with open(meta["path"], "rb") as f:
                f.seek(start)
                while left > 0:
                    chunk = f.read(min(CHUNK, left))
                    if not chunk:
                        return
                    left -= len(chunk)
                    yield chunk
        except FileNotFoundError:
            return
        finally:
            meta["active"] -= 1
        # Only delete once the final byte has actually been sent. If the
        # connection drops part-way, the file stays so the download can resume.
        if left <= 0 and end >= size - 1:
            drop(fid, delivered=True)

    return StreamingResponse(stream(), status_code=code, media_type=ctype, headers=hdrs)

# ---------- Pages ----------
STYLE = """<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<link rel=preconnect href=https://fonts.googleapis.com><link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap" rel=stylesheet>
<script src=https://cdn.tailwindcss.com></script>
<style>body{font-family:'Space Grotesk',system-ui,sans-serif;background:#eef4fb;color:#10233f}
.drop.on{background:#d9ecff;border-color:#1f6feb}</style>"""

def page(title, body):
    return HTMLResponse(f"<!doctype html><html lang=en><title>{title}</title>{STYLE}<body class='min-h-screen'>{body}</body></html>")

# Receiver-facing pages use inline CSS only, so they load instantly and work
# even when the network has no internet access (no CDN, no web fonts).
LITE_CSS = """*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#eef4fb;color:#10233f;
font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{width:calc(100% - 32px);max-width:420px;margin:16px;padding:32px 24px;background:#fff;border-radius:20px;
text-align:center;box-shadow:0 8px 30px rgba(16,35,63,.1)}
.icon{font-size:48px;line-height:1}
.small{font-size:13px;opacity:.65;margin:0}
h1{font-size:24px;line-height:1.25;margin:8px 0;overflow-wrap:anywhere}
.btn{display:block;margin-top:24px;padding:18px;border-radius:14px;background:#1f6feb;color:#fff;
font-size:18px;font-weight:600;text-decoration:none}
.btn:active{background:#1857b8}
.msg{margin:16px 0 0;font-size:14px;min-height:21px}
a.link{color:#1f6feb}"""

def lite_page(title, body):
    return HTMLResponse(
        f"<!doctype html><html lang=en><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'><meta name=robots content=noindex>"
        f"<title>{title}</title><style>{LITE_CSS}</style><body><main>{body}</main></body></html>",
        headers={"Cache-Control": "no-store"})

HOME = """
<main class="max-w-xl mx-auto px-5 py-10">
  <h1 class="text-4xl font-bold tracking-tight">hellolite</h1>
  <p class="mt-2 text-lg">Send a file to another device. Up to 1 GB, deleted after one download.</p>

  <section id=send class="mt-8">
    <label id=zone class="drop block border-2 border-dashed border-[#10233f]/40 rounded-2xl p-10 text-center cursor-pointer transition">
      <input id=pick type=file class=hidden>
      <span class="block text-xl font-medium">Drop a file here</span>
      <span class="block mt-1 text-sm opacity-70">or tap to choose one</span>
    </label>
    <p id=err role=alert class="mt-3 text-sm font-medium text-red-700"></p>
  </section>

  <section id=prog class="hidden mt-8">
    <div class="flex justify-between text-sm font-medium"><span id=fname class=truncate></span><span id=pct>0%</span></div>
    <div class="h-3 mt-2 rounded-full bg-white overflow-hidden"><div id=bar class="h-full w-0 bg-[#1f6feb] transition-all"></div></div>
    <div class="flex justify-between text-sm mt-2 opacity-75"><span id=spd></span><span id=eta></span></div>
    <button id=cancel class="mt-4 text-sm underline">Cancel</button>
  </section>

  <section id=done class="hidden mt-8 bg-white rounded-2xl p-6 text-center">
    <p class="font-bold text-xl">Ready to share</p>
    <p class="text-sm mt-1">Scan the code or send the link. It works for one download, up to 15 minutes.</p>
    <img id=qr class="mx-auto my-4 w-48 h-48" alt="QR code for the download link">
    <div class="flex gap-2">
      <input id=link readonly class="flex-1 min-w-0 rounded-lg border border-[#10233f]/30 px-3 py-2 text-sm">
      <button id=copy class="rounded-lg bg-[#10233f] text-white px-4 py-2 text-sm font-medium">Copy link</button>
    </div>
    <p id=state role=status class="mt-4 text-sm font-medium"></p>
    <button onclick=location.reload() class="mt-4 text-sm underline">Send another file</button>
  </section>

  <p class="mt-10 text-xs opacity-60">Open on another device: <b>http://__IP__:__PORT__</b></p>
</main>
<script>
const MAX=1024*1024*1024,$=i=>document.getElementById(i);
const fmt=b=>b>1e6?(b/1e6).toFixed(1)+' MB':(b/1e3).toFixed(0)+' KB';
const t=s=>s>=60?Math.floor(s/60)+'m '+Math.round(s%60)+'s':Math.round(s)+'s';
let xhr,wake;
['dragover','dragleave','drop'].forEach(e=>$('zone').addEventListener(e,ev=>{ev.preventDefault();
  $('zone').classList.toggle('on',e==='dragover'); if(e==='drop')send(ev.dataTransfer.files[0]);}));
$('pick').onchange=e=>send(e.target.files[0]);
$('cancel').onclick=()=>{xhr&&xhr.abort();location.reload()};
$('copy').onclick=async()=>{const i=$('link');
  try{await navigator.clipboard.writeText(i.value)}catch(e){i.select();document.execCommand('copy')}
  $('copy').textContent='Copied'};
function watch(id){
  const msgs={ready:'Waiting for the other device\u2026',downloading:'Downloading\u2026',
    done:'\u2713 Downloaded. The file has been deleted.',expired:'This link has expired.'};
  const iv=setInterval(async()=>{try{
    const s=(await (await fetch('/s/'+id)).json()).state;$('state').textContent=msgs[s]||'';
    if(s==='done'||s==='expired')clearInterval(iv)}catch(e){}},1500);
}
async function send(f){
  if(!f)return; $('err').textContent='';
  if(f.size>MAX){$('err').textContent=`${f.name} is ${fmt(f.size)}. hellolite sends files up to 1 GB.`;return}
  $('send').classList.add('hidden');$('prog').classList.remove('hidden');$('fname').textContent=f.name;
  try{wake=await navigator.wakeLock?.request('screen')}catch(e){}
  const start=Date.now();xhr=new XMLHttpRequest();xhr.open('POST','/upload');
  xhr.setRequestHeader('X-Filename',encodeURIComponent(f.name));
  xhr.upload.onprogress=e=>{const p=e.loaded/e.total*100,s=e.loaded/((Date.now()-start)/1000||1);
    $('bar').style.width=p+'%';$('pct').textContent=Math.floor(p)+'%';document.title=`[${Math.floor(p)}%] hellolite`;
    $('spd').textContent=fmt(s)+'/s';$('eta').textContent=t((e.total-e.loaded)/s)+' left'};
  xhr.onload=()=>{wake&&wake.release();document.title='hellolite';let r={};try{r=JSON.parse(xhr.responseText)}catch(e){}
    if(xhr.status!==200){location.reload();return}
    $('prog').classList.add('hidden');$('done').classList.remove('hidden');
    $('link').value=r.url;$('qr').src='/qr?u='+encodeURIComponent(r.url);watch(r.id)};
  xhr.onerror=()=>{$('err').textContent='Upload failed. Check your connection and try again.';$('prog').classList.add('hidden');$('send').classList.remove('hidden')};
  xhr.send(f);
}
</script>"""

DOWNLOAD = """<div class=icon>&#128196;</div>
<p class=small style="margin-top:12px">Someone sent you a file</p>
<h1>__NAME__</h1>
<p>__SIZE__</p>
<a id=go class=btn href="/f/__FID__">Download</a>
<p id=msg class=msg role=status></p>
<p class=small>Deleted from the server once it finishes.</p>
<script>
const a=document.getElementById('go'),msg=document.getElementById('msg');
a.addEventListener('click',()=>{
  a.textContent='Downloading\\u2026';
  msg.innerHTML='Check your Downloads folder. Didn\\u2019t start? <a class=link href="/f/__FID__">Tap here</a>.';
  const iv=setInterval(async()=>{try{
    const s=(await (await fetch('/s/__FID__')).json()).state;
    if(s==='done'){clearInterval(iv);a.style.display='none';msg.textContent='\\u2713 Done. The file is in your Downloads folder.'}
  }catch(e){}},1000);
});
</script>"""

@app.get("/", response_class=HTMLResponse)
async def home():
    return page("hellolite", HOME.replace("__IP__", lan_ip()).replace("__PORT__", str(PORT)))

@app.get("/d/{fid}", response_class=HTMLResponse)
async def download_page(fid: str):
    meta = FILES.get(fid)
    if not meta:
        if fid in DONE:
            return lite_page("Already downloaded", """<div class=icon>&#9989;</div>
              <h1>Already downloaded</h1>
              <p>This file was downloaded and then deleted. Ask the sender for a new link if you need it again.</p>
              <a class=link href="/">Send a file with hellolite</a>""")
        return lite_page("Link expired", """<div class=icon>&#9203;</div>
          <h1>This link has expired</h1>
          <p>Files are deleted after one download or 15 minutes. Ask the sender for a new link.</p>
          <a class=link href="/">Send a file with hellolite</a>""")
    size = f"{meta['size']/1e6:.1f} MB" if meta["size"] > 1e6 else f"{meta['size']/1e3:.0f} KB"
    body = (DOWNLOAD.replace("__NAME__", html.escape(meta["name"]))
                    .replace("__SIZE__", size).replace("__FID__", fid))
    return lite_page(f"Download {html.escape(meta['name'])}", body)

if __name__ == "__main__":
    # For HTTPS (removes the browser's "may harm your device" download warning):
    #   mkcert -install && mkcert <your-lan-ip> localhost
    #   SSL_CERT=<ip>+1.pem SSL_KEY=<ip>+1-key.pem python hellolite.py
    cert, key = os.environ.get("SSL_CERT"), os.environ.get("SSL_KEY")
    scheme = "https" if cert and key else "http"
    print(f"\n  hellolite is running\n  On this device: {scheme}://localhost:{PORT}\n  On your network: {scheme}://{lan_ip()}:{PORT}\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning",
                ssl_certfile=cert if scheme == "https" else None,
                ssl_keyfile=key if scheme == "https" else None)

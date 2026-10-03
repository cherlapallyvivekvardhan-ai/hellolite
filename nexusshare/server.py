"""NexusShare - futuristic file sharing. FastAPI + WebSockets, streamed uploads/downloads."""
import asyncio, os, re, socket, time, uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

BASE = Path(__file__).parent
UPLOAD_DIR = BASE / "uploads"
MAX_BYTES = int(float(os.getenv("MAX_GB", "5")) * 1024**3)   # per-file limit
PUBLIC_TTL = int(os.getenv("PUBLIC_TTL", "60"))               # Quantum Drop lifetime (s)
ROOM_TTL = int(os.getenv("ROOM_TTL", "3600"))                 # Nexus Room file lifetime (s)
PORT = int(os.getenv("PORT", "8000"))
PUBLIC = "PUBLIC"

files: dict[str, dict] = {}            # id -> metadata (in memory)
sockets: dict[str, set] = {}           # room -> websockets


def valid_room(r: str) -> bool:
    return r == PUBLIC or re.fullmatch(r"\d{6}", r) is not None


def safe_name(n: str) -> str:
    n = re.sub(r"[\x00-\x1f\\/]", "", Path(n).name).strip()
    return n[:150] or "file"


def view(f: dict) -> dict:
    return {"id": f["id"], "name": f["name"], "size": f["size"],
            "remaining": max(0, round(f["expires"] - time.time(), 1)),
            "url": f"/download/{f['id']}"}


async def broadcast(room: str, msg: dict):
    for ws in list(sockets.get(room, ())):
        try:
            await ws.send_json(msg)
        except Exception:
            sockets[room].discard(ws)


async def remove(f: dict):
    files.pop(f["id"], None)
    (UPLOAD_DIR / f["id"]).unlink(missing_ok=True)
    await broadcast(f["room"], {"type": "remove", "id": f["id"]})


async def sweeper():
    while True:
        await asyncio.sleep(1)
        now = time.time()
        for f in [f for f in files.values() if f["expires"] <= now]:
            await remove(f)


@asynccontextmanager
async def lifespan(_):
    UPLOAD_DIR.mkdir(exist_ok=True)
    for p in UPLOAD_DIR.iterdir():          # wipe leftovers from previous runs
        p.unlink(missing_ok=True)
    task = asyncio.create_task(sweeper())
    yield
    task.cancel()


app = FastAPI(title="NexusShare", lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/info")
async def info():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1)); ip = s.getsockname()[0]; s.close()
    except Exception:
        ip = "127.0.0.1"
    return {"lan_url": f"http://{ip}:{PORT}", "public_ttl": PUBLIC_TTL,
            "room_ttl": ROOM_TTL, "max_bytes": MAX_BYTES}


@app.post("/api/upload/{room}")
async def upload(room: str, request: Request):
    """Raw-body streaming upload: constant RAM no matter the file size."""
    if not valid_room(room):
        raise HTTPException(400, "Invalid room code")
    if int(request.headers.get("content-length") or 0) > MAX_BYTES:
        raise HTTPException(413, "File too large")
    name = safe_name(unquote(request.headers.get("x-filename", "file")))
    fid, path, size = uuid.uuid4().hex, None, 0
    path = UPLOAD_DIR / fid
    try:
        with open(path, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "File too large")
                await asyncio.to_thread(out.write, chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    ttl = PUBLIC_TTL if room == PUBLIC else ROOM_TTL
    f = {"id": fid, "name": name, "size": size, "room": room, "expires": time.time() + ttl}
    files[fid] = f
    await broadcast(room, {"type": "add", "file": view(f)})
    return {"ok": True, "id": fid}


@app.get("/download/{fid}")
async def download(fid: str):
    f = files.get(fid)
    if not f or f["expires"] <= time.time():
        raise HTTPException(404, "File expired or not found")
    return FileResponse(UPLOAD_DIR / fid, filename=f["name"], media_type="application/octet-stream")


@app.websocket("/ws/{room}")
async def ws_room(ws: WebSocket, room: str):
    if not valid_room(room):
        await ws.close(code=1008); return
    await ws.accept()
    sockets.setdefault(room, set()).add(ws)
    await ws.send_json({"type": "snapshot",
                        "files": [view(f) for f in files.values() if f["room"] == room]})
    await broadcast(room, {"type": "peers", "count": len(sockets[room])})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        sockets[room].discard(ws)
        await broadcast(room, {"type": "peers", "count": len(sockets[room])})


if __name__ == "__main__":
    print(f"\n  NexusShare online -> http://localhost:{PORT}  (LAN: see dashboard)\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT)

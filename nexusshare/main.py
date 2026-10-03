"""NexusShare - Streamlit edition (runs on Streamlit Community Cloud).
Streamlit cannot do raw WebSockets/streamed uploads, so this version polls instead.
For true 1 GB+ streaming use server.py (FastAPI) on Render/Railway/Fly/your PC."""
import html, random, re, shutil, time, uuid
from pathlib import Path

import streamlit as st

BASE = Path(__file__).parent
UP = BASE / "uploads"
UP.mkdir(exist_ok=True)
PUBLIC_TTL, ROOM_TTL = 60, 3600

st.set_page_config(page_title="NexusShare", page_icon="⚡", layout="centered")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@600;800&family=Rajdhani:wght@500;600&display=swap');
.stApp{background:radial-gradient(circle at 15% 15%,rgba(0,243,255,.14),transparent 40%),radial-gradient(circle at 85% 10%,rgba(255,43,214,.14),transparent 40%),#05070f;font-family:Rajdhani,sans-serif}
.nx-title{font:800 clamp(2rem,8vw,3.2rem) Orbitron,sans-serif;text-align:center;letter-spacing:4px;background:linear-gradient(90deg,#00f3ff,#ff2bd6);-webkit-background-clip:text;background-clip:text;color:transparent;filter:drop-shadow(0 0 14px rgba(0,243,255,.5))}
.nx-sub{text-align:center;color:#7f8fb0;letter-spacing:3px;text-transform:uppercase;font-size:.8rem;margin-bottom:18px}
.nx-card{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.1);border-left:4px solid #00f3ff;border-radius:12px;padding:12px 16px}
.nx-card.pub{border-left-color:#ff2bd6}
.nx-card small{color:#7f8fb0}
.stButton>button,.stDownloadButton>button{border:1px solid #00f3ff;background:transparent;color:#00f3ff;border-radius:10px;font-family:Orbitron,sans-serif}
.stButton>button:hover,.stDownloadButton>button:hover{background:#00f3ff;color:#000;box-shadow:0 0 16px #00f3ff}
.stTabs [data-baseweb=tab]{font-family:Orbitron,sans-serif}
</style>
<div class="nx-title">NEXUSSHARE</div>
<div class="nx-sub">Zero-lag · Zero-trace · Any device</div>
""", unsafe_allow_html=True)


@st.cache_resource
def files() -> dict:
    """Shared across every visitor of the app (id -> metadata)."""
    for p in UP.iterdir():
        p.unlink(missing_ok=True)
    return {}


def sweep():
    now = time.time()
    for f in [f for f in files().values() if f["expires"] <= now]:
        files().pop(f["id"], None)
        (UP / f["id"]).unlink(missing_ok=True)


def fmt(b: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024 or u == "GB":
            return f"{b:.0f} {u}" if u == "B" else f"{b:.1f} {u}"
        b /= 1024


def save(room: str, uf):
    fid = uuid.uuid4().hex
    with open(UP / fid, "wb") as out:
        shutil.copyfileobj(uf, out, 1024 * 1024)
    ttl = PUBLIC_TTL if room == "PUBLIC" else ROOM_TTL
    files()[fid] = {"id": fid, "room": room, "name": Path(uf.name).name or "file",
                    "size": uf.size, "created": time.time(), "expires": time.time() + ttl}


def uploader(room: str):
    n = st.session_state.setdefault(f"n_{room}", 0)
    got = st.file_uploader("Drop files to beam", accept_multiple_files=True,
                           key=f"up_{room}_{n}", label_visibility="collapsed")
    if got:
        with st.spinner("Beaming…"):
            for uf in got:
                save(room, uf)
        st.session_state[f"n_{room}"] += 1
        st.rerun()


@st.fragment(run_every=2)
def feed(room: str):
    sweep()
    items = sorted((f for f in files().values() if f["room"] == room), key=lambda f: -f["created"])
    st.markdown("##### LIVE FEED")
    if not items:
        st.caption("No files yet.")
    for f in items:
        left = int(f["expires"] - time.time())
        left_txt = f"{left}s" if room == "PUBLIC" else f"{left // 60} min"
        c1, c2 = st.columns([4, 1.4], vertical_alignment="center")
        c1.markdown(f'<div class="nx-card {"pub" if room == "PUBLIC" else ""}"><b>{html.escape(f["name"])}</b>'
                    f'<br><small>{fmt(f["size"])} · expires in {left_txt}</small></div>', unsafe_allow_html=True)
        ready = st.session_state.get("ready")
        if ready == f["id"] and (UP / f["id"]).exists():
            c2.download_button("⇩ Save", data=(UP / f["id"]).read_bytes(), file_name=f["name"],
                               key=f"dl_{f['id']}")
        else:
            if c2.button("Get", key=f"get_{f['id']}"):
                st.session_state["ready"] = f["id"]
                st.rerun(scope="fragment")


room_tab, pub_tab = st.tabs(["⬡ Nexus Room", "⚡ Quantum Drop (60s)"])

with room_tab:
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    code = c1.text_input("Room code", key="code", max_chars=6, placeholder="6-digit code")
    c2.button("Generate", on_click=lambda: st.session_state.update(code=str(random.randint(100000, 999999))))
    if re.fullmatch(r"\d{6}", code or ""):
        st.success(f"Room {code} linked — share this code; anyone with it can join.")
        uploader(code)
        feed(code)
    else:
        st.info("Enter or generate a 6-digit code to open a room.")

with pub_tab:
    st.warning("Public zone — everyone sees these files. They self-destruct 60 s after upload.")
    uploader("PUBLIC")
    feed("PUBLIC")

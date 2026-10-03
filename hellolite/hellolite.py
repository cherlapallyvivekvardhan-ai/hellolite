"""hellolite (Streamlit edition) - send a file to another device with a link or QR code.

Runs on Streamlit Community Cloud. Files live on the server for 15 minutes or until
the first download, whichever comes first.
"""
import shutil, tempfile, time, secrets, io, html
from pathlib import Path

import segno
import streamlit as st

MAX_MB = 300            # keep <= .streamlit/config.toml maxUploadSize; free hosting has limited RAM
MAX_BYTES = MAX_MB * 1024 * 1024
TTL = 15 * 60

st.set_page_config(page_title="hellolite", page_icon="📤", layout="centered")


@st.cache_resource
def store():
    """One store shared by every visitor/session of this app process."""
    return {"dir": Path(tempfile.mkdtemp(prefix="hellolite_")), "files": {}, "done": set()}


S = store()


def drop(fid: str, delivered: bool = False):
    meta = S["files"].pop(fid, None)
    if meta:
        Path(meta["path"]).unlink(missing_ok=True)
        if delivered:
            S["done"].add(fid)


def sweep():
    now = time.time()
    for fid in [k for k, v in S["files"].items() if now - v["created"] > TTL]:
        drop(fid)


def fmt(n: int) -> str:
    return f"{n / 1e6:.1f} MB" if n > 1e6 else f"{n / 1e3:.0f} KB"


def base_url() -> str:
    try:
        h = st.context.headers
        host = h.get("Host", "localhost:8501")
        local = host.startswith(("localhost", "127.", "192.168.", "10."))
        proto = h.get("X-Forwarded-Proto") or ("http" if local else "https")
        return f"{proto}://{host}"
    except Exception:
        return ""


sweep()
fid_param = st.query_params.get("d")

# ---------------------------------------------------------------- receiver
if fid_param:
    meta = S["files"].get(fid_param)
    if not meta:
        done = fid_param in S["done"]
        st.markdown("## " + ("✅ Already downloaded" if done else "⏳ This link has expired"))
        st.write("The file was downloaded and then deleted. Ask the sender for a new link."
                 if done else "Files are deleted after one download or 15 minutes. Ask the sender for a new link.")
        st.link_button("Send a file with hellolite", "/")
    else:
        st.caption("Someone sent you a file")
        st.markdown(f"## 📄 {html.escape(meta['name'])}")
        st.write(fmt(meta["size"]))
        st.download_button(
            "Download",
            data=Path(meta["path"]).read_bytes(),
            file_name=meta["name"],
            mime="application/octet-stream",
            type="primary",
            use_container_width=True,
            on_click=drop, args=(fid_param, True),   # delete from the server once it is handed over
        )
        st.caption("Deleted from the server once you download it.")
    st.stop()

# ------------------------------------------------------------------ sender
st.title("hellolite")
st.write(f"Send a file to another device. Up to {MAX_MB} MB, deleted after one download.")

up = st.file_uploader("Drop a file here or tap to choose one", label_visibility="collapsed")

if up is None:
    old = st.session_state.pop("fid", None)
    st.session_state.pop("up_key", None)
    if old:
        drop(old)
    st.stop()

if up.size > MAX_BYTES:
    st.error(f"{up.name} is {fmt(up.size)}. This version sends files up to {MAX_MB} MB.")
    st.stop()

if st.session_state.get("up_key") != up.file_id:      # save once, not on every rerun
    old = st.session_state.pop("fid", None)
    if old:
        drop(old)
    fid = secrets.token_urlsafe(6)
    path = S["dir"] / fid
    with open(path, "wb") as f:
        shutil.copyfileobj(up, f)
    S["files"][fid] = {"path": str(path), "name": Path(up.name).name or "file",
                       "size": up.size, "created": time.time()}
    st.session_state["up_key"], st.session_state["fid"] = up.file_id, fid

fid = st.session_state["fid"]
link = f"{base_url()}/?d={fid}"


@st.fragment(run_every=3)
def share():
    meta = S["files"].get(fid)
    if meta is None:
        if fid in S["done"]:
            st.success("✓ Downloaded. The file has been deleted.")
        else:
            st.warning("This link has expired.")
        return
    left = max(0, int(TTL - (time.time() - meta["created"])) // 60)
    st.subheader("Ready to share")
    st.caption(f"Scan the code or send the link. Works for one download, about {left} more minutes.")
    buf = io.BytesIO()
    segno.make(link, error="m").save(buf, kind="png", scale=6, border=2, dark="#10233f")
    st.image(buf.getvalue(), width=220)
    st.code(link, language=None)           # tap the copy icon at the top right
    st.caption("Waiting for the other device…")
    if st.button("Delete now"):
        drop(fid)
        st.rerun()


share()

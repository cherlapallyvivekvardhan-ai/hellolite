# NexusShare — futuristic file sharing

Two editions, same features (6-digit **Nexus Rooms** + 60 s public **Quantum Drop**):

| | `main.py` (Streamlit) | `server.py` (FastAPI) |
|---|---|---|
| Runs on | Streamlit Community Cloud, local | Render / Railway / Fly / Docker / your PC |
| Live sync | polling every 2 s | WebSockets |
| Big files | limited by app RAM (~1 GB on free Cloud → keep under ~200 MB) | streamed, 1 GB+ with flat RAM |

## Why the Streamlit Cloud error happened
Streamlit Cloud runs `streamlit run <main module>` and health-checks it. The old `main.py` was a FastAPI
server, so Streamlit answered 404 and the deploy failed. FastAPI/WebSocket apps cannot run on Streamlit Cloud.
`main.py` is now a real Streamlit app, and the FastAPI one is `server.py`.

## Streamlit Cloud
Main file path: `nexusshare/main.py`. For uploads > 200 MB copy `.streamlit/config.toml` to the **repo root**
(Streamlit Cloud only reads config from the repo root). Requirements: `nexusshare/requirements.txt`.

## FastAPI edition (real 1 GB+ streaming)
    pip install -r requirements-server.txt
    python server.py            # http://localhost:8000
Deploy: Render (`render.yaml`), Railway/Heroku-style (`Procfile`), or `docker build -t nexusshare .`
Worldwide from your PC: `ngrok http 8000`.

Env vars (server.py): PORT, MAX_GB, PUBLIC_TTL, ROOM_TTL.
Metadata is in memory; uploads/ is wiped on restart. Room codes act like passwords — share only with people you trust.

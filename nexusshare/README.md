# NexusShare — futuristic file sharing

- **Nexus Rooms**: enter/generate a 6-digit code; every device in that room sees files live (WebSockets). Files live 1 hour (`ROOM_TTL`).
- **Quantum Drop**: public zone; files self-destruct 60 s after upload (`PUBLIC_TTL`).
- Streaming uploads/downloads: 1 GB+ files with flat RAM usage. Limit via `MAX_GB` (default 5).

## Run
    pip install -r requirements.txt
    python main.py          # or ./run.sh  /  run.bat
Open http://localhost:8000. Other devices on the same Wi-Fi: use the LAN link shown in the dashboard.

## Share worldwide (no port forwarding)
    ngrok http 8000        # or: cloudflared tunnel --url http://localhost:8000

## Config (env vars)
PORT, MAX_GB, PUBLIC_TTL, ROOM_TTL

## Notes
Metadata is in memory; uploads/ is wiped on restart. Room codes are not passwords — anyone with the code can join, so use a tunnel only with people you trust.

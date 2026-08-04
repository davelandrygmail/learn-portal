# Learn Portal

A lightweight web portal for browsing your `/teach` learning workspaces on a
headless Hermes Agent server. Scans your workspace directories, lists every
topic, and serves wrapped lesson views in the browser — no database, no
external services, just the filesystem.

```
┌─────────────────────────────────────────────────────┐
│  📚 Learn Portal                                    │
│                                                     │
│  ┌──────────────────┐  ┌──────────────────────────┐ │
│  │  🖨️ Resin 3D     │  │  🏗️ SAFe Learning       │ │
│  │  Printing        │  │  1 lesson · 1 reference  │ │
│  │  1 lesson        │  │  Latest: RTE Role &      │ │
│  │  Latest: Intro   │  │  Mindset                 │ │
│  │  to Resin 3D...  │  │                          │ │
│  └──────────────────┘  └──────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

## How It Works

- **Discover** — on every request the app scans `/mnt/data/Workspace/Learning/*/MISSION.md`
  to find teaching workspaces (directories created by the `/teach` skill).
- **Index** — the home page shows every workspace as a card with lesson count
  and the latest lesson title.
- **Browse** — click into a workspace to see its full lesson list and any
  reference documents.
- **Learn** — lesson pages are the original authored HTML with a thin sticky
  navbar added for breadcrumb navigation and prev/next arrows.

### Lesson Wrapping

Each lesson keeps its original content, styles, and interactive scripts
intact. The portal makes three surgical injections:

1. A `<base>` tag so relative asset URLs (`../assets/style.css`) resolve
   correctly.
2. Portal navbar styles (scoped with `.lp-` prefixes to avoid conflicts).
3. A sticky navigation bar as the first child of `<body>`.

## Prerequisites

- Linux server with `systemd --user` available
- Python 3.11+
- Python packages: `fastapi`, `uvicorn[standard]`, `jinja2`
- (Optional but recommended) [`uv`](https://docs.astral.sh/uv/) — fast
  Python package installer

## Quick Start

```bash
# 1. Clone the repo on your Hermes server
git clone https://github.com/davelandrygmail/learn-portal.git
cd learn-portal

# 2. Run the install script (installs deps + sets up systemd service)
chmod +x install.sh
./install.sh

# 3. Open in your browser
#    Local:    http://localhost:7777
#    Network:  http://<server-ip>:7777
```

## Manual Start (without systemd)

```bash
# Install deps
uv pip install -r requirements.txt
# or: pip install -r requirements.txt

# Run the server (uses the project venv created by install.sh / pip)
.venv/bin/uvicorn app:app --host 0.0.0.0 --port 7777
```

## Routes

| Path | What it shows |
|---|---|
| `/` | Home — all workspaces as cards |
| `/{topic}/` | Topic detail — lesson list + references |
| `/{topic}/lessons/{n}` | Wrapped lesson with navigation |
| `/{topic}/reference/{f}` | Raw reference doc (no wrapping) |
| `/health` | JSON health check |

## File Structure

```
learn-portal/
├── app.py              # FastAPI application
├── templates/
│   ├── index.html      # Home page
│   └── topic.html      # Topic detail page
├── requirements.txt    # Python dependencies
├── install.sh          # One-shot setup script
└── README.md           # This file
```

## Managing the Service

```bash
# Status
systemctl --user status learn-portal.service

# Stop / start / restart
systemctl --user stop learn-portal.service
systemctl --user start learn-portal.service
systemctl --user restart learn-portal.service

# Live logs
journalctl --user -u learn-portal.service -f

# Disable on boot
systemctl --user disable learn-portal.service
```

## Adding a New Workspace

Just create a new subdirectory in `/mnt/data/Workspace/Learning/` with a
`MISSION.md` and `lessons/` directory using the `/teach` skill. The portal
picks it up automatically on the next page load.

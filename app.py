"""
learn-portal — Web viewer for /teach workspaces.

Scans /mnt/data/Workspace/Learning for teaching workspaces (directories with
MISSION.md), lists them on a clean home page, and serves wrapped lesson views
with navigation chrome (breadcrumbs + prev/next arrows).

Static workspace files (CSS, images) are served under /ws/ so relative asset
URLs in wrapped lessons resolve correctly.
"""

import asyncio
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

import jinja2
import markdown as md_lib
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi import WebSocket, WebSocketDisconnect

# ── Paths ──────────────────────────────────────────────────────────────────

WORKSPACE_ROOT = Path("/mnt/data/Workspace")
# All /teach workspaces live under the shared Learning directory.
LEARNING_DIR = WORKSPACE_ROOT / "Learning"
# Project dir derived from this file's location so the app keeps working
# regardless of where the repo lives on disk.
PORTAL_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Learn Portal")

# Create Jinja2 environment directly (avoids a Starlette wrapper issue where
# ``Jinja2Templates`` can produce 'unhashable type: dict' on template cache
# lookups during request handling).
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(PORTAL_DIR / "templates")),
    autoescape=jinja2.select_autoescape(),
)


def _render(name: str, **context) -> str:
    """Render a Jinja2 template with the given context.

    FastAPI endpoints call this instead of ``templates.TemplateResponse``
    to dodge the Starlette wrapper issue noted above.
    """
    template = _jinja_env.get_template(name)
    return template.render(**context)

# Mount workspace files so rewritten asset URLs resolve.
# e.g. a lesson referencing ../assets/style.css resolves to
# /ws/Learning/{topic}/assets/style.css
app.mount("/ws", StaticFiles(directory=str(WORKSPACE_ROOT)), name="workspace")

# Portal's own static assets (e.g. the in-lesson teach chat pane).
_STATIC_DIR = PORTAL_DIR / "static"
_STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ── Workspace Discovery ────────────────────────────────────────────────────

def _friendly_name(dir_name: str) -> str:
    """Convert a directory name like ``3d-printing-resin`` or ``SAFe-Learning``
    into a display-friendly form preserving intentional casing.

    - All-lowercase words get capitalized (first letter upper).
    - Mixed-case words (e.g. ``SAFe``) are left as-is.
    """
    parts = []
    for word in dir_name.replace("-", " ").split():
        if not word:
            continue
        # Has intentional uppercase mixed with lowercase → preserve
        if any(c.isupper() for c in word) and any(c.islower() for c in word):
            parts.append(word)
        else:
            parts.append(word.capitalize())
    return " ".join(parts)


def _parse_mission_title(content: str) -> str:
    """Extract a display title from MISSION.md.

    Tries in order:
      1. YAML frontmatter ``title:`` field
      2. ``# Mission: <text>`` heading
    """
    m = re.search(r"^title:\s*\"?(.+?)\"?\s*$", content, re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(r"^#\s+Mission:\s*(.+)$", content, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return ""


def _parse_mission_preview(content: str) -> str:
    """First meaningful sentence from MISSION.md (≤200 chars).

    Skips YAML frontmatter, headings, and blockquote labels like
    ``**Purpose** –`` to find actual descriptive content.
    """
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip YAML frontmatter and headings
        if line.startswith("---") or line.startswith("#"):
            continue
        # Strip blockquote markers
        clean = re.sub(r"^>\s*", "", line).strip()
        if not clean:
            continue
        # Skip bold label lines (e.g. **Purpose** –, **Goals** –)
        if re.match(r"\*\*.+\*\*\s*[–\-]?\s*$", clean):
            continue
        # This is real content
        return clean[:200] + ("…" if len(clean) > 200 else "")
    return ""


def _discover_workspaces() -> list[dict]:
    """Scan LEARNING_DIR for subdirectories containing a MISSION.md."""
    workspaces: list[dict] = []

    for d in sorted(LEARNING_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue

        mission_file = d / "MISSION.md"
        if not mission_file.exists():
            continue

        # Read mission metadata
        try:
            mission_raw = mission_file.read_text(encoding="utf-8")
        except Exception:
            continue

        title = _parse_mission_title(mission_raw) or _friendly_name(d.name)
        preview = _parse_mission_preview(mission_raw)

        # ── Lessons ────────────────────────────────────────────────────────
        lessons_dir = d / "lessons"
        lessons: list[dict] = []
        if lessons_dir.exists():
            for f in sorted(lessons_dir.glob("*.html")):
                order_match = re.match(r"(\d+)", f.stem)
                order = int(order_match.group(1)) if order_match else 0

                lesson_title = f.stem
                try:
                    html_content = f.read_text(encoding="utf-8")
                    t_match = re.search(
                        r"<title>(.*?)</title>", html_content, re.DOTALL
                    )
                    if t_match:
                        lesson_title = t_match.group(1).strip()
                except Exception:
                    pass

                lessons.append({
                    "file": f,
                    "title": lesson_title,
                    "order": order,
                    "filename": f.name,
                })

        # ── Reference docs ─────────────────────────────────────────────────
        ref_dir = d / "reference"
        references: list[dict] = []
        if ref_dir.exists():
            for f in sorted(ref_dir.glob("*.html")):
                ref_title = f.stem.replace("-", " ").title()
                references.append({
                    "file": f,
                    "title": ref_title,
                    "filename": f.name,
                })

        workspaces.append({
            "name": d.name,
            "title": title,
            "path": d,
            "mission": preview or title,
            "lessons": lessons,
            "lesson_count": len(lessons),
            "latest_lesson": lessons[-1] if lessons else None,
            "references": references,
            "ref_count": len(references),
        })

    return workspaces


# ── Inline Portal Styles ───────────────────────────────────────────────────

_PORTAL_STYLES = """
<style>
.lp-navbar {
    background: #1a1a2e; color: #eee; padding: 10px 24px;
    display: flex; justify-content: space-between; align-items: center;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    font-size: 14px; position: sticky; top: 0; z-index: 1000;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
}
.lp-navbar a { color: #7bb3ff; text-decoration: none; }
.lp-navbar a:hover { text-decoration: underline; }
.lp-breadcrumbs { display: flex; align-items: center; gap: 8px; }
.lp-sep { color: #666; }
.lp-current { color: #ccc; }
.lp-nav-arrows { display: flex; align-items: center; gap: 12px; }
.lp-position { color: #888; font-size: 13px; white-space: nowrap; }
.lp-disabled { color: #444; }
.lp-prev, .lp-next { padding: 4px 12px; border-radius: 4px; }
.lp-prev:hover, .lp-next:hover { background: rgba(255,255,255,0.1); }
body { margin-top: 0 !important; }

/* ── In-lesson /teach chat pane ─────────────────────────── */
#lp-teach-btn {
    background: #2a8a5a; color: #fff; border: none;
    padding: 4px 14px; border-radius: 6px; cursor: pointer;
    font-size: 13px; font-weight: 600; margin-left: 16px;
    transition: background .2s;
}
#lp-teach-btn:hover { background: #35a06c; }
#lp-teach-btn.lp-chat-busy { background: #b8860b; }
.lp-chat-open #lp-teach-btn { background: #6a2a8a; }

#lp-chat {
    display: none;
    position: fixed; left: 0; right: 0; bottom: 0;
    height: 45vh; z-index: 2000;
    background: #0f0f1a; color: #e0e0e0;
    border-top: 2px solid #2a2a4a;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    flex-direction: column;
    box-shadow: 0 -6px 24px rgba(0,0,0,0.5);
}
.lp-chat-open #lp-chat { display: flex; }
#lp-chat-head {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 16px; background: #1a1a2e; border-bottom: 1px solid #2a2a4a;
    font-size: 13px; color: #888; flex: 0 0 auto;
}
#lp-chat-close {
    background: none; border: none; color: #888; cursor: pointer;
    font-size: 15px; padding: 0 6px;
}
#lp-chat-close:hover { color: #fff; }
#lp-chat-log {
    flex: 1 1 auto; overflow-y: auto; padding: 16px;
    display: flex; flex-direction: column; gap: 10px;
}
.lp-chat-user, .lp-chat-assistant { display: flex; }
.lp-chat-user { justify-content: flex-end; }
.lp-chat-assistant { justify-content: flex-start; }
.lp-chat-bubble {
    max-width: 78%; padding: 10px 14px; border-radius: 12px;
    line-height: 1.55; font-size: 14px; word-wrap: break-word;
}
.lp-chat-user .lp-chat-bubble {
    background: #1f3a63; color: #dbe8ff;
    border-bottom-right-radius: 3px;
}
.lp-chat-assistant .lp-chat-bubble {
    background: #1a1a2e; color: #e0e0e0;
    border-bottom-left-radius: 3px;
}
.lp-chat-bubble pre {
    background: #0a0a14; padding: 10px; border-radius: 6px;
    overflow-x: auto; font-size: 12.5px;
}
.lp-chat-bubble code { background: #0a0a14; padding: 1px 5px; border-radius: 4px; }
.lp-chat-bubble strong { color: #fff; }
#lp-chat-input {
    display: flex; gap: 8px; padding: 10px 12px;
    background: #1a1a2e; border-top: 1px solid #2a2a4a; flex: 0 0 auto;
}
#lp-chat-msg {
    flex: 1; resize: none; height: 44px;
    background: #0f0f1a; color: #e0e0e0; border: 1px solid #2a2a4a;
    border-radius: 8px; padding: 10px 12px; font-size: 14px;
    font-family: inherit;
}
#lp-chat-send {
    background: #2a8a5a; color: #fff; border: none;
    padding: 0 20px; border-radius: 8px; cursor: pointer; font-weight: 600;
}
#lp-chat-send:disabled, #lp-chat-msg:disabled {
    opacity: .5; cursor: not-allowed;
}
#lp-chat-status { font-style: italic; }
.lp-chat-loading { color: #7bb3ff; }
</style>
"""


# ── Lesson Wrapping ────────────────────────────────────────────────────────

def _wrap_lesson_html(
    lesson_file: Path,
    topic_name: str,
    topic_title: str,
    lesson_order: int,
    lesson_count: int,
    prev_url: Optional[str],
    next_url: Optional[str],
) -> str:
    """Read a lesson HTML file and inject the portal navigation chrome.

    Three surgical modifications are made to the original HTML:
      1. A <base> tag is inserted after <head> so relative asset URLs
         (e.g. ``../assets/style.css``) resolve under the /ws/ mount.
      2. Portal navbar styles are injected before </head>.
      3. The navbar element is inserted as the first child of <body>.

    The lesson's own content, styles, and scripts are left completely
    untouched.
    """
    html = lesson_file.read_text(encoding="utf-8")

    # ── Breadcrumbs ────────────────────────────────────────────────────────
    breadcrumbs = (
        f'<a href="/">← All Topics</a>'
        f'<span class="lp-sep">›</span>'
        f'<a href="/{topic_name}/">{topic_title}</a>'
        f'<span class="lp-sep">›</span>'
        f'<span class="lp-current">Lesson {lesson_order}</span>'
    )

    # ── Prev / Next links ──────────────────────────────────────────────────
    def _link(url: str | None, label: str) -> str:
        if url:
            return f'<a href="{url}">{label}</a>'
        return f'<span class="lp-disabled">{label}</span>'

    navbar = (
        f'<nav class="lp-navbar">'
        f'  <div class="lp-breadcrumbs">{breadcrumbs}</div>'
        f'  <div class="lp-nav-arrows">'
        f'    {_link(prev_url, "← Previous")}'
        f'    <span class="lp-position">Lesson {lesson_order} of {lesson_count}</span>'
        f'    {_link(next_url, "Next →")}'
        f'    <button id="lp-teach-btn" type="button" aria-pressed="false">'
        f'💬 Ask</button>'
        f'  </div>'
        f'</nav>'
    )

    # ── Injection 1: rewrite relative asset & reference paths ──────────────
    # Instead of a <base> tag (which hijacks ALL relative URLs), surgically
    # rewrite only the paths we know the lesson uses:
    #   ../assets/...  →  /ws/Learning/{topic}/assets/...   (CSS, images)
    #   ../reference/... → /{topic}/reference/...  (portal route w/ rendering)
    #   ../MISSION.md  →  /ws/Learning/{topic}/MISSION.md   (raw file)
    #   ../RESOURCES.md → /ws/Learning/{topic}/RESOURCES.md
    ws_prefix = f'/ws/Learning/{topic_name}'
    html = html.replace(
        '../assets/', f'{ws_prefix}/assets/'
    )
    html = html.replace(
        '../reference/', f'/{topic_name}/reference/'
    )
    html = html.replace(
        '../MISSION.md', f'{ws_prefix}/MISSION.md'
    )
    html = html.replace(
        '../RESOURCES.md', f'{ws_prefix}/RESOURCES.md'
    )
    # Also rewrite ../lessons/ links (cross-references between lessons).
    # Strip the filename suffix — only the numeric order is needed.
    html = re.sub(
        r'\.\./lessons/(\d+)[^"\' )]*',
        rf'/{topic_name}/lessons/\g<1>',
        html,
    )
    # Also rewrite same-directory lesson links like 0002-foo.html → /topic/lessons/2
    html = re.sub(
        r'(href=["\']?)(\d+)-[^"\' )]*\.html',
        rf'\g<1>/{topic_name}/lessons/\g<2>',
        html,
    )

    # ── Injection 2: portal styles ─────────────────────────────────────────
    html = html.replace("</head>", f"{_PORTAL_STYLES}\n</head>")

    # ── Injection 3: navbar inside <body> ──────────────────────────────────
    body_match = re.search(r"<body[^>]*>", html)
    if body_match:
        pos = body_match.end()
        html = html[:pos] + "\n" + navbar + html[pos:]

    # ── Injection 4: /teach chat pane before </body> ───────────────────────
    teach_pane = (
        '<div id="lp-chat" data-topic="%s" aria-hidden="true">'
        '  <div id="lp-chat-head">'
        '    <span>💬 Teach — %s</span>'
        '    <span id="lp-chat-status"></span>'
        '    <button id="lp-chat-close" type="button" aria-label="Close">✕</button>'
        '  </div>'
        '  <div id="lp-chat-log"></div>'
        '  <div id="lp-chat-input">'
        '    <textarea id="lp-chat-msg" placeholder="Ask about this lesson…" '
        'disabled rows="1"></textarea>'
        '    <button id="lp-chat-send" type="button" disabled>Send</button>'
        '  </div>'
        '</div>'
        '<script src="/static/teach-chat.js" defer></script>'
    ) % (topic_name, topic_title)
    html = html.replace("</body>", teach_pane + "\n</body>")

    return html


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page — grid of all teaching workspaces."""
    workspaces = _discover_workspaces()
    return HTMLResponse(
        _render("index.html", workspaces=workspaces)
    )


@app.get("/{topic_name}/", response_class=HTMLResponse)
async def topic_detail(request: Request, topic_name: str):
    """Topic detail — lesson list + reference docs for one workspace."""
    workspaces = _discover_workspaces()
    topic = next((ws for ws in workspaces if ws["name"] == topic_name), None)
    if not topic:
        return HTMLResponse(
            "<h1>Topic not found</h1><p>No workspace named "
            f"<code>{topic_name}</code>.</p>",
            status_code=404,
        )
    return HTMLResponse(
        _render("topic.html", topic=topic)
    )


@app.get("/{topic_name}/lessons/{lesson_order}", response_class=HTMLResponse)
async def lesson_view(request: Request, topic_name: str, lesson_order: int):
    """Wrapped lesson — lesson content with navigation chrome."""
    workspaces = _discover_workspaces()
    topic = next((ws for ws in workspaces if ws["name"] == topic_name), None)
    if not topic:
        return HTMLResponse(
            "<h1>Topic not found</h1>", status_code=404
        )

    lesson = next(
        (l for l in topic["lessons"] if l["order"] == lesson_order), None
    )
    if not lesson:
        return HTMLResponse(
            f"<h1>Lesson not found</h1>"
            f"<p>Lesson {lesson_order} does not exist in "
            f"<code>{topic_name}</code>.</p>",
            status_code=404,
        )

    lesson_count = len(topic["lessons"])
    prev_url = f"/{topic_name}/lessons/{lesson_order - 1}" if lesson_order > 1 else None
    next_url = (
        f"/{topic_name}/lessons/{lesson_order + 1}"
        if lesson_order < lesson_count
        else None
    )

    html = _wrap_lesson_html(
        lesson["file"],
        topic_name,
        topic["title"],
        lesson_order,
        lesson_count,
        prev_url,
        next_url,
    )
    return HTMLResponse(content=html)


_MD_REF_STYLES = """
<style>
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f0f1a; color: #e0e0e0;
    max-width: 800px; margin: 0 auto; padding: 24px;
    line-height: 1.7;
}
a { color: #7bb3ff; }
h1, h2, h3 { color: #fff; }
h1 { font-size: 1.8rem; border-bottom: 1px solid #2a2a4a; padding-bottom: 8px; }
h2 { font-size: 1.3rem; margin-top: 28px; }
h3 { font-size: 1.1rem; margin-top: 20px; }
code { background: #1a1a2e; padding: 2px 6px; border-radius: 4px; }
pre { background: #1a1a2e; padding: 16px; border-radius: 8px; overflow-x: auto; }
hr { border: none; border-top: 1px solid #2a2a4a; margin: 24px 0; }
blockquote { border-left: 3px solid #7bb3ff; margin: 16px 0; padding: 8px 16px; background: #1a1a2e; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; }
th, td { border: 1px solid #2a2a4a; padding: 8px 12px; text-align: left; }
th { background: #1a1a2e; color: #fff; }
.lp-back { margin-bottom: 20px; }
.lp-back a { color: #7bb3ff; text-decoration: none; font-size: .92rem; }
.lp-back a:hover { text-decoration: underline; }
</style>
"""


def _is_markdown(content: str) -> bool:
    """Heuristic: file is Markdown (not HTML) if it has no HTML structure tags."""
    return not bool(re.search(r"<(!DOCTYPE|html|head|body|div\s)", content, re.IGNORECASE))


def _render_markdown_ref(file_path: Path, topic_name: str, title: str) -> str:
    """Convert a Markdown reference file to a wrapped HTML page."""
    raw = file_path.read_text(encoding="utf-8")
    body_html = md_lib.markdown(raw, extensions=["fenced_code", "tables"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8">
<title>{title} — {topic_name}</title>
{_MD_REF_STYLES}
</head>
<body>
<div class="lp-back"><a href="/{topic_name}/">← Back to {topic_name}</a></div>
{body_html}
</body>
</html>"""


@app.get("/{topic_name}/reference/{filename}")
async def reference_view(topic_name: str, filename: str):
    """Serve a reference document.

    Detects Markdown files (created by /teach with .html extension but
    containing Markdown) and renders them as styled HTML.  Real HTML files
    are served as-is.
    """
    ref_path = LEARNING_DIR / topic_name / "reference" / filename
    if not ref_path.is_file():
        return HTMLResponse(
            "<h1>Reference not found</h1>", status_code=404
        )
    content = ref_path.read_text(encoding="utf-8")
    if _is_markdown(content):
        title = filename.replace(".html", "").replace("-", " ").title()
        return HTMLResponse(_render_markdown_ref(ref_path, topic_name, title))
    return FileResponse(str(ref_path))


@app.get("/health")
async def health():
    """Health-check endpoint (also returns discovered workspace count)."""
    workspaces = _discover_workspaces()
    return {
        "status": "ok",
        "workspace_count": len(workspaces),
        "workspaces": [ws["title"] for ws in workspaces],
    }


# ── In-lesson /teach chat pane ───────────────────────────────────────────────
#
# A WebSocket endpoint that proxies a browser chat pane to a topic-scoped,
# stateful Hermes session. Each user message runs:
#
#     hermes chat [--resume <session-id>] --skills teach -q "<message>"
#
# with the working directory set to the topic's workspace
# (Learning/<topic>/). State is kept by Hermes' own session DB: the first
# message in a topic starts a fresh session (no --resume, so it creates one),
# and later messages resume it by the real session ID discovered via
# `hermes sessions list --workspace <topic>`. Because the session's recorded
# working directory is the topic dir, this survives portal restarts.
#
# The first message in a topic seeds the session with a /teach continuation
# directive so the model is grounded in that topic's MISSION.md.
#
# Important: `--resume <name>` ONLY resumes an already-existing session — it
# errors with "Session not found" on a fresh topic. We therefore never pass a
# made-up id; we always resolve a real one (or none, for the first turn).

# Path to the hermes CLI (resolved once at import).
_HERMES_BIN = (
    __import__("shutil").which("hermes")
    or "/home/hermes-agent/.local/bin/hermes"
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _is_valid_topic(topic_name: str) -> bool:
    """Topic must be a single path-safe segment mapping to an existing dir."""
    if not topic_name or topic_name in {".", ".."}:
        return False
    if re.search(r"[/\\]", topic_name):
        return False
    return (LEARNING_DIR / topic_name).is_dir()


def _teach_bootstrap(topic_name: str) -> str:
    """The directive prepended to the first message in a fresh session."""
    return (
        "You are in a /teach learning session for the topic at "
        f"/mnt/data/Workspace/Learning/{topic_name}/. "
        "Follow the teach skill: consult MISSION.md and the learning-records "
        "to understand what this learner already knows, ground your teaching "
        "in that workspace (do not invent files elsewhere), and work within "
        "that directory. Continue their learning in their zone of proximal "
        "development. IMPORTANT: this is a non-interactive terminal channel "
        "(stdin is closed) — do NOT pause to ask the learner a question or "
        "call the clarify tool; instead make reasonable assumptions from the "
        "learning-records, teach one focused thing at the right level, and "
        "invite a correction in your reply. Here is the learner's next message:"
    )


def _existing_session_id(topic_name: str) -> Optional[str]:
    """Return the Hermes session ID for a topic, or None if none exists yet.

    Deterministic discovery via the `hermes sessions list` filter on the
    topic's workspace directory — no dependence on chat stdout formatting.
    The newest matching session is used (rows are newest-first).
    """
    topic_dir = LEARNING_DIR / topic_name
    cmd = [
        _HERMES_BIN, "sessions", "list",
        "--workspace", str(topic_dir),
        "--limit", "5",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env=_hermes_env(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    head = (proc.stdout or "") + (proc.stderr or "")
    for line in head.splitlines():
        # Session rows look like:
        #   Title   Workspace        Last Active   ID
        #   —       Learn-portal     8m ago        20260805_132001_9dbba0
        match = re.search(r"(\d{8}_\d{6}_[a-f0-9]{6})\b", line)
        if match:
            return match.group(1)
    return None


def _hermes_env():
    """Environment for spawned hermes processes.

    The learn-portal systemd service runs with a minimal environment (no
    HERMES_HOME, no 9ROUTER_API_KEY, no HERMES_REAL_HOME). Those variables are
    present in Dave's interactive shells and are what let a `hermes chat`
    subprocess authenticate as the `@custom:9router:Deepseek` combo. Without
    them, the spawned hermes falls back to an upstream `openai` route, which
    9Router rejects ("No active credentials for provider: openai").

    We therefore hard-pin the Hermes home and pass the 9Router key through
    explicitly rather than relying on whatever the systemd unit happens to
    provide. HOME is forced so ~/.hermes/state.db resolves correctly.
    """
    env = dict(os.environ)
    env.setdefault("HOME", str(Path.home()))
    # Hermes resolves its auth.json / config.yaml and custom providers from
    # HERMES_HOME. Pin it so the subprocess authenticates as Dave, not as an
    # anonymous process that 9Router treats as its `openai` provider.
    env.setdefault("HERMES_HOME", "/home/hermes-agent/.hermes")
    env.setdefault("HERMES_REAL_HOME", "/home/hermes-agent")
    return env


def _build_teach_cmd(
    topic_name: str, message: str, *, seed: bool, session_id: Optional[str]
) -> list[str]:
    """Assemble the hermes chat command for one turn."""
    cmd = [_HERMES_BIN, "chat"]
    if session_id:
        # Resume an existing session by its real ID.
        cmd += ["--resume", session_id]
    cmd += ["--skills", "teach"]
    if session_id:
        # We already set cwd below; don't let hermes cd into the recorded
        # workspace (which is the same dir anyway, but be explicit/safe).
        cmd += ["--no-restore-cwd"]
    prompt = message
    if seed:
        prompt = f"{_teach_bootstrap(topic_name)}\n\n{message}"
    cmd += ["-q", prompt]
    return cmd


async def _drain(proc: asyncio.subprocess.Process, out: list[str]) -> None:
    """Read a subprocess's combined stdout+stderr until EOF.

    Separated out of ``_run_teach`` so the whole turn can be bounded by an
    overall timeout via ``asyncio.wait_for`` — a non-interactive ``-q`` /teach
    turn can otherwise block forever (e.g. the agent waiting on a clarify or
    tool prompt with stdin=/dev/null).
    """
    assert proc.stdout is not None
    while True:
        chunk = await proc.stdout.read(256)
        if not chunk:
            break
        out.append(chunk.decode("utf-8", "replace"))


_HERMES_TOP = "╭─ ⚕ Hermes"
_HERMES_BOT = "╰─"


def _clean_hermes_output(raw: str) -> str:
    """Keep only the assistant's teaching prose from a `hermes chat` capture.

    A ``hermes chat -q`` run prints chrome around the actual teaching content:
    a ``Query: ...`` prefix, ``Initializing agent...``, ``↻ Resumed session``,
    box-drawing separators, per-tool status/``┊``/git-diff spam, and a trailing
    ``Resume this session with:`` footer. In the learning pane the learner
    should see only the Hermes assistant's prose. So we walk the captured text
    line by line, keep lines *inside* ``╭─ ⚕ Hermes ─╮`` bordered blocks (with
    the border lines themselves dropped), and discard everything else.
    """
    in_block = False
    out: list[str] = []
    pending: list[str] = []
    for line in (raw or "").splitlines():
        s = line.strip()
        if s.startswith(_HERMES_TOP):
            in_block = True
            pending = []
            continue
        if "╯" in s and _HERMES_BOT in s:
            in_block = False
            if pending:
                out.append("\n".join(pending).strip())
            pending = []
            continue
        if in_block:
            pending.append(line)
    return "\n\n".join(chunk for chunk in out if chunk).strip()


async def _run_teach(
    topic_name: str, message: str, *, seed: bool, session_id: Optional[str]
) -> tuple[int, str]:
    """Run one hermes chat invocation for the topic.

    Returns (exit_code, combined_stdout_stderr) once the process finishes.
    The gate agent/approval config may cause longer runs; we let the portal's
    default timeouts apply and stream nothing back here — the WebSocket handler
    streams chunk-by-chunk instead.
    """
    workdir = LEARNING_DIR / topic_name
    cmd = _build_teach_cmd(topic_name, message, seed=seed, session_id=session_id)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_hermes_env(),
    )
    out: list[str] = []
    assert proc.stdout is not None
    _RUN_TIMEOUT = float(os.environ.get("LP_TEACH_TIMEOUT", "240"))
    try:
        await asyncio.wait_for(_drain(proc, out), timeout=_RUN_TIMEOUT)
        await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        # The child didn't finish inside the budget. In non-interactive -q mode a
        # /teach turn can hang forever (e.g. the agent blocks on a clarify/tool
        # wait with stdin=/dev/null). Kill it so the pane never sits at
        # "Thinking…" indefinitely — the WS handler turns this into a clear error.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return 124, "".join(out) + "\n[hermes aborted: the turn exceeded the time budget]"
    return proc.returncode or 0, "".join(out)


@app.websocket("/chat/{topic_name}")
async def chat_ws(websocket: WebSocket, topic_name: str):
    """Bidirectionally proxy the /teach session for `topic_name`.

    Browser sends user messages; we relay streamed assistant output back as
    deltas. A fresh topic session is created (seeded with /teach) on the first
    message; later messages resume it.
    """
    if not _is_valid_topic(topic_name):
        await websocket.close(code=4404)  # custom: topic not found
        return

    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            import json as _json

            try:
                msg = _json.loads(data).get("message")
            except Exception:
                msg = data
            if not msg or not msg.strip():
                await websocket.send_text(_json.dumps({"error": "Empty message"}))
                continue

            await websocket.send_text(_json.dumps({"status": "thinking"}))

            # Resolve (or don't) the topic's session right now.
            sid = _existing_session_id(topic_name)
            code, full = await _run_teach(
                topic_name, msg.strip(), seed=not bool(sid), session_id=sid
            )

            if code == 0 and full.strip():
                await websocket.send_text(
                    _json.dumps({"delta": _clean_hermes_output(full), "done": True})
                )
            else:
                await websocket.send_text(
                    _json.dumps({"error": full.strip() or f"hermes exited {code}"})
                )
    except WebSocketDisconnect:
        return
    except Exception as exc:  # noqa: BLE001 — report and close
        try:
            await websocket.send_text(
                _json.dumps({"error": f"Internal error: {exc}"})
            )
        except Exception:
            pass

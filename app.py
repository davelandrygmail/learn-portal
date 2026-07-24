"""
learn-portal — Web viewer for /teach workspaces.

Scans /mnt/data/Workspace for teaching workspaces (directories with MISSION.md),
lists them on a clean home page, and serves wrapped lesson views with
navigation chrome (breadcrumbs + prev/next arrows).

Static workspace files (CSS, images) are served under /ws/ so that the
<base> tag in wrapped lessons resolves relative URLs correctly.
"""

import os
import re
from pathlib import Path
from typing import Optional

import jinja2
import markdown as md_lib
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# ── Paths ──────────────────────────────────────────────────────────────────

WORKSPACE_ROOT = Path("/mnt/data/Workspace")
PORTAL_DIR = WORKSPACE_ROOT / "learn-portal"

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

# Mount workspace files so <base> tags in wrapped lessons resolve.
# e.g. a lesson referencing ../assets/style.css resolves to
# /ws/{topic}/assets/style.css
app.mount("/ws", StaticFiles(directory=str(WORKSPACE_ROOT)), name="workspace")


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
    """Scan WORKSPACE_ROOT for subdirectories containing a MISSION.md."""
    workspaces: list[dict] = []

    for d in sorted(WORKSPACE_ROOT.iterdir()):
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
        f'  </div>'
        f'</nav>'
    )

    # ── Injection 1: <base> tag ────────────────────────────────────────────
    base_tag = f'<base href="/ws/{topic_name}/lessons/">'
    html = html.replace("<head>", f"<head>\n    {base_tag}", 1)

    # ── Injection 2: portal styles ─────────────────────────────────────────
    html = html.replace("</head>", f"{_PORTAL_STYLES}\n</head>")

    # ── Injection 3: navbar inside <body> ──────────────────────────────────
    body_match = re.search(r"<body[^>]*>", html)
    if body_match:
        pos = body_match.end()
        html = html[:pos] + "\n" + navbar + html[pos:]

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
    ref_path = WORKSPACE_ROOT / topic_name / "reference" / filename
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

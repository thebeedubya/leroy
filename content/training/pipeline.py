#!/usr/bin/env python3
"""
Training Content Pipeline: Markdown -> Slides (PNG/PDF) + Audio (MP3) + Video (MP4) + PDFs
Usage: python3 pipeline.py <module_directory_path>
"""
import sys, os, re, json, time, shutil, subprocess, tempfile
import requests as _requests
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", "")
ELEVENLABS_API_KEY  = os.environ.get("ELEVENLABS_API_KEY", "")
VOICES_URL          = "https://api.elevenlabs.io/v1/voices"
TTS_URL             = "https://api.elevenlabs.io/v1/text-to-speech/{vid}"
TTS_MODEL           = "eleven_multilingual_v2"
FALLBACK_VOICE      = os.environ.get("ELEVENLABS_FALLBACK_VOICE", "")
BRAD_WOOD_VOICE_ID  = os.environ.get("ELEVENLABS_VOICE_ID", "")

MARP_FRONT = """\
---
marp: true
theme: uncover
paginate: true
style: |
  section { font-family: 'Helvetica Neue', Arial, sans-serif; background: #fff; color: #1A1A2E; }
  h1 { color: #E91E63; border-bottom: 2px solid #E91E63; padding-bottom: 8px; }
  strong { color: #E91E63; }
---
"""

# ── Utilities ────────────────────────────────────────────────────────────────

def runcmd(cmd, capture=False):
    print(f"  [cmd] {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, capture_output=capture, text=True)
    return r

def slug(path: Path): return path.name.lower().replace(" ", "-")

def need(tool):
    p = shutil.which(tool)
    if not p: sys.exit(f"ERROR: {tool} not found in PATH")
    print(f"  [ok] {tool} -> {p}", flush=True); return p

def audio_dur(path, ffprobe):
    r = runcmd([ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams", str(path)], capture=True)
    try:
        for s in json.loads(r.stdout).get("streams", []):
            if s.get("duration"): return float(s["duration"])
    except Exception: pass
    return 0.0

# ── Step A: Slides ────────────────────────────────────────────────────────────

TEMPLATE_PATH = Path(__file__).parent / "slide-template.html"

def parse_slides(md: str):
    slides = []
    for block in re.split(r'\n(?=## Slide \d+)', md):
        m = re.match(r'## Slide \d+:\s*(.*)', block.strip())
        if not m: continue
        body = block[m.end():].strip()
        body = re.sub(r'\*\*Visual:\*\*.*?(?=\n\n|\Z)', '', body, flags=re.DOTALL)
        body = re.sub(r'\*\*Speaker Note:\*\*.*?(?=\n\n|\Z)', '', body, flags=re.DOTALL)
        # Strip trailing --- slide dividers so they don't become extra marp pages
        body = re.sub(r'\n\s*---\s*$', '', body.strip())
        body = re.sub(r'\n\s*---\s*\n', '\n\n', body)
        slides.append({"title": m.group(1).strip(), "body": body.strip()})
    return slides


def classify_slide(title, body, slide_index, total_slides) -> str:
    """Return the layout type for a slide.
    Priority: title > quote > stat-grid > checklist > two-column > section-divider > bullet
    """
    # 1. title: first slide
    if slide_index == 0:
        return "title"

    # 2. quote: last slide OR body starts with >
    if slide_index == total_slides - 1 or body.lstrip().startswith('>'):
        return "quote"

    lines = [l.strip() for l in body.split('\n') if l.strip()]

    # 3. stat-grid: 3+ lines matching "number word(s)" pattern e.g. "9 Training Modules"
    stat_pat = re.compile(r'^\d+[\+%]?\s+\w')
    stat_lines = [l for l in lines if stat_pat.match(l.lstrip('- ').lstrip('* '))]
    if len(stat_lines) >= 3:
        return "stat-grid"

    # 4. checklist: 3+ checkbox lines or "check" in title
    check_lines = [l for l in lines if l.startswith('- [ ]') or l.startswith('- [x]')
                   or l.startswith('* [ ]') or l.startswith('* [x]')]
    if len(check_lines) >= 3 or 'check' in title.lower():
        return "checklist"

    # 5. two-column: two list groups separated by blank line
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body) if p.strip()]
    if len(paragraphs) >= 2:
        list_groups = [p for p in paragraphs
                       if any(ln.strip().startswith('- ') or ln.strip().startswith('* ')
                              for ln in p.split('\n'))]
        if len(list_groups) >= 2:
            return "two-column"

    # 6. section-divider: "section" in title or body under 30 words
    if 'section' in title.lower() or len(body.split()) < 30:
        return "section-divider"

    # 7. bullet: default
    return "bullet"


def _extract_layout_block(template_html: str, layout: str) -> str:
    """Extract a layout's HTML block from the template file."""
    start_tag = f"<!-- LAYOUT_START:{layout} -->"
    end_tag = f"<!-- LAYOUT_END:{layout} -->"
    start = template_html.find(start_tag)
    end = template_html.find(end_tag)
    if start == -1 or end == -1:
        raise ValueError(f"Layout '{layout}' not found in template")
    return template_html[start + len(start_tag):end].strip()


def _md_bullets_to_html(body: str) -> str:
    """Convert markdown bullet lines from body text to <li> elements."""
    items = []
    for line in body.split('\n'):
        line = line.strip()
        if line.startswith('- ') or line.startswith('* '):
            text = line[2:].strip()
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong class="highlight">\1</strong>', text)
            items.append(f'<li>{text}</li>')
    if not items:
        # Fallback: treat non-empty non-heading lines as bullets
        for line in body.split('\n'):
            line = line.strip()
            if line and not line.startswith('#'):
                text = re.sub(r'\*\*(.+?)\*\*', r'<strong class="highlight">\1</strong>', line)
                items.append(f'<li>{text}</li>')
    return '\n'.join(items)


def render_slide_html(slide_data, layout, slide_index, total_slides, persona, module_num) -> str:
    """Render a slide using slide-template.html. Write to temp file, return path.

    Args:
        slide_data:   dict with 'title' and 'body' keys
        layout:       layout type string from classify_slide()
        slide_index:  0-based slide index
        total_slides: total number of slides
        persona:      badge label string (e.g. 'AROYA')
        module_num:   module number for TAG token
    Returns:
        str: path to the written temp HTML file
    """
    template_html = TEMPLATE_PATH.read_text()

    # Extract <head> + opening <body> from template (includes styles + shared SVG)
    body_open = template_html.find('<body>') + len('<body>')
    head_section = template_html[:body_open]

    # Extract shared SVG symbol block
    svg_start = template_html.find('<svg xmlns="http://www.w3.org/2000/svg" style="display:none">')
    svg_end = template_html.find('</svg>', svg_start) + len('</svg>') if svg_start != -1 else -1
    shared_svg = template_html[svg_start:svg_end] if svg_start != -1 else ""

    slide_block = _extract_layout_block(template_html, layout)

    body_text = slide_data.get("body", "") if isinstance(slide_data, dict) else ""
    title_text = slide_data.get("title", "") if isinstance(slide_data, dict) else str(slide_data)

    progress_pct = int(((slide_index + 1) / max(total_slides, 1)) * 100)
    progress_width = f"{progress_pct}%"
    pagenum = f"{slide_index + 1} / {total_slides}"
    badge = str(persona) if persona else "AROYA"
    tag = f"Module {int(module_num):02d}" if module_num is not None else f"Module {slide_index:02d}"

    def bold_to_span(text):
        return re.sub(r'\*\*(.+?)\*\*', r'<span class="highlight">\1</span>', text)

    def bullets_to_li(text):
        items = []
        for line in text.split('\n'):
            line = line.strip()
            if line.startswith('- ') or line.startswith('* '):
                items.append(f'<li>{bold_to_span(line[2:].strip())}</li>')
        if not items:
            for line in text.split('\n'):
                line = line.strip()
                if line and not line.startswith('#'):
                    items.append(f'<li>{bold_to_span(line)}</li>')
        return '\n'.join(items)

    if layout == "title":
        h1_m = re.search(r'^# (.+)$', body_text, re.MULTILINE)
        display_title = h1_m.group(1) if h1_m else title_text
        h2_m = re.search(r'^## (?!Slide)(.+)$', body_text, re.MULTILINE)
        display_subtitle = h2_m.group(1) if h2_m else ""
        plain_lines = [l.strip() for l in body_text.split('\n')
                       if l.strip() and not l.strip().startswith('#')]
        display_body = plain_lines[0] if plain_lines else ""
        subtitle_line = (' · '.join(plain_lines[1:]) if len(plain_lines) > 1
                         else (plain_lines[0] if plain_lines else ""))
        tokens = {
            "{{TAG}}": tag,
            "{{TITLE}}": bold_to_span(display_title),
            "{{BODY}}": display_body,
            "{{SUBTITLE}}": subtitle_line or display_subtitle,
            "{{BADGE}}": badge,
            "{{PAGENUM}}": pagenum,
            "{{PROGRESS_WIDTH}}": progress_width,
        }
    elif layout == "bullet":
        h2_m = re.search(r'^## (?!Slide)(.+)$', body_text, re.MULTILINE)
        display_title = h2_m.group(1) if h2_m else title_text
        tokens = {
            "{{TAG}}": tag,
            "{{TITLE}}": bold_to_span(display_title),
            "{{BODY}}": bullets_to_li(body_text),
            "{{BADGE}}": badge,
            "{{PAGENUM}}": pagenum,
            "{{PROGRESS_WIDTH}}": progress_width,
        }
    elif layout == "quote":
        clean_quote = body_text.lstrip('> ').strip()
        tokens = {
            "{{QUOTE}}": clean_quote,
            "{{ATTRIBUTION}}": slide_data.get("attribution", "") if isinstance(slide_data, dict) else "",
            "{{BADGE}}": badge,
            "{{PAGENUM}}": pagenum,
            "{{PROGRESS_WIDTH}}": progress_width,
        }
    elif layout == "stat-grid":
        stat_pat = re.compile(r'^(\d+[\+%]?\+?)\s+(.+)$')
        stats_html = ""
        for line in [l.strip() for l in body_text.split('\n') if l.strip()]:
            clean = line.lstrip('- ').lstrip('* ')
            m = stat_pat.match(clean)
            if m:
                stats_html += (f'<div class="stat-box">'
                               f'<div class="stat-num">{m.group(1)}</div>'
                               f'<div class="stat-label">{m.group(2)}</div>'
                               f'</div>')
        tokens = {
            "{{TAG}}": tag,
            "{{TITLE}}": bold_to_span(title_text),
            "{{BODY}}": "",
            "{{STATS}}": stats_html,
            "{{BADGE}}": badge,
            "{{PAGENUM}}": pagenum,
            "{{PROGRESS_WIDTH}}": progress_width,
        }
    elif layout == "checklist":
        checks_html = ""
        for line in [l.strip() for l in body_text.split('\n') if l.strip()]:
            if line.startswith('- ') or line.startswith('* '):
                text = re.sub(r'^[-*]\s*\[.\]\s*', '', line)
                text = re.sub(r'^[-*]\s*', '', text)
                checks_html += (f'<div class="check-row">'
                                f'<div class="check-box"></div>'
                                f'<span class="check-text">{bold_to_span(text)}</span>'
                                f'</div>')
        tokens = {
            "{{TAG}}": tag,
            "{{TITLE}}": bold_to_span(title_text),
            "{{CHECKS}}": checks_html,
            "{{BADGE}}": badge,
            "{{PAGENUM}}": pagenum,
            "{{PROGRESS_WIDTH}}": progress_width,
        }
    elif layout == "two-column":
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', body_text) if p.strip()]
        col1_header = col2_header = ""
        col1_body = col2_body = ""
        if paragraphs:
            lines1 = paragraphs[0].split('\n')
            col1_header = lines1[0].strip().lstrip('#').strip() if lines1 else ""
            col1_body = bullets_to_li('\n'.join(lines1[1:]))
        if len(paragraphs) > 1:
            lines2 = paragraphs[1].split('\n')
            col2_header = lines2[0].strip().lstrip('#').strip() if lines2 else ""
            col2_body = bullets_to_li('\n'.join(lines2[1:]))
        tokens = {
            "{{TAG}}": tag,
            "{{TITLE}}": bold_to_span(title_text),
            "{{COL1_HEADER}}": col1_header,
            "{{COL1_BODY}}": col1_body,
            "{{COL2_HEADER}}": col2_header,
            "{{COL2_BODY}}": col2_body,
            "{{BADGE}}": badge,
            "{{PAGENUM}}": pagenum,
            "{{PROGRESS_WIDTH}}": progress_width,
        }
    else:  # section-divider and any other layout
        h2_m = re.search(r'^## (?!Slide)(.+)$', body_text, re.MULTILINE)
        display_title = h2_m.group(1) if h2_m else title_text
        plain_lines = [l.strip() for l in body_text.split('\n')
                       if l.strip() and not l.strip().startswith('#')]
        tokens = {
            "{{TAG}}": tag,
            "{{TITLE}}": bold_to_span(display_title),
            "{{BODY}}": plain_lines[0] if plain_lines else "",
            "{{BADGE}}": badge,
            "{{PAGENUM}}": pagenum,
            "{{PROGRESS_WIDTH}}": progress_width,
        }

    for token, value in tokens.items():
        slide_block = slide_block.replace(token, value)

    # Clean up any remaining unreplaced tokens
    slide_block = re.sub(r'\{\{[A-Z_]+\}\}', '', slide_block)

    html_content = f"{head_section}\n{shared_svg}\n{slide_block}\n</body>\n</html>"

    # Write to temp file, return path
    tmp = tempfile.NamedTemporaryFile(suffix='.html', mode='w', delete=False, encoding='utf-8')
    tmp.write(html_content)
    tmp.close()
    return tmp.name


def html_to_png(html_path, png_path) -> bool:
    """Render an HTML file to a 960x540 PNG using Playwright.

    Args:
        html_path: path to HTML file
        png_path:  destination PNG path
    Returns:
        bool: True on success, False on failure
    """
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 960, "height": 540})
            page.emulate_media(color_scheme="dark")
            page.goto(f"file://{os.path.abspath(str(html_path))}", wait_until="networkidle")
            # Screenshot the .slide element (not full page)
            slide_el = page.query_selector('.slide')
            if slide_el:
                slide_el.screenshot(path=str(png_path))
            else:
                page.screenshot(path=str(png_path), full_page=False)
            browser.close()
        return True
    except Exception as e:
        print(f"  ERROR html_to_png: {e}", flush=True)
        return False


def step_a(module_dir: Path, produced: Path):
    print("\n[STEP A] Slides -> PNG (Playwright template engine)", flush=True)
    src = module_dir / "slides.md"
    if not src.exists(): print("  WARN: slides.md missing"); return []

    slides = parse_slides(src.read_text())
    print(f"  Parsed {len(slides)} slides", flush=True)

    slides_dir = produced / "slides"
    slides_dir.mkdir(exist_ok=True)

    total = len(slides)
    pngs = []
    tmp_html_files = []
    for i, s in enumerate(slides):
        out_png = slides_dir / f"slide.{i+1:03d}.png"
        layout = classify_slide(s['title'], s['body'], i, total)
        print(f"  Slide {i+1}/{total} [{layout}]: {s['title']}", flush=True)
        html_path = render_slide_html(s, layout, i, total, "AROYA", 1)
        tmp_html_files.append(html_path)
        ok = html_to_png(html_path, out_png)
        if ok:
            pngs.append(out_png)
        else:
            print(f"  WARN: PNG not produced for slide {i+1}", flush=True)

    # Clean up temp HTML files
    for f in tmp_html_files:
        try: os.unlink(f)
        except Exception: pass

    # Optional PDF export via marp if available
    marp = shutil.which("marp")
    if marp:
        parts = [MARP_FRONT]
        for i, s in enumerate(slides):
            if i: parts.append("---\n")
            parts.append(f"# {s['title']}\n\n{s['body']}\n")
        marp_md = "\n".join(parts)
        marp_file = produced / "_slides_marp.md"
        marp_file.write_text(marp_md)
        print("  Exporting PDF via marp...", flush=True)
        runcmd([marp, str(marp_file), "--pdf",
                "--output", str(produced / "slides.pdf"), "--allow-local-files"])

    print(f"  PNGs: {len(pngs)}", flush=True)
    return pngs

# ── Step B: Narration ─────────────────────────────────────────────────────────

def get_voice():
    try:
        r = _requests.get(VOICES_URL, headers={"xi-api-key": ELEVENLABS_API_KEY}, timeout=15)
        r.raise_for_status()
        voices = r.json().get("voices", [])
        print(f"  Found {len(voices)} voices", flush=True)
        cloned_voices = []
        for v in voices:
            cat, name, vid = v.get("category",""), v.get("name",""), v.get("voice_id","")
            print(f"    {name} | {cat} | {vid}", flush=True)
            if cat in ("cloned", "professional"):
                cloned_voices.append((name, vid))
        # Prefer Brad Wood's cloned voice by ID, then by name, then first found
        for name, vid in cloned_voices:
            if vid == BRAD_WOOD_VOICE_ID:
                print(f"  Using Brad Wood voice: {name} ({vid})", flush=True)
                return vid
        for name, vid in cloned_voices:
            if "brad" in name.lower():
                print(f"  Using: {name} ({vid})", flush=True)
                return vid
        if cloned_voices:
            name, vid = cloned_voices[0]
            print(f"  CLONED VOICE FOUND: {name} -> {vid}", flush=True)
            return vid
    except Exception as e:
        print(f"  Voice fetch error: {e}", flush=True)
    print(f"  NO CLONED VOICE - USING DEFAULT: {FALLBACK_VOICE} (Daniel)", flush=True)
    return FALLBACK_VOICE

def parse_narration(md: str):
    blocks = re.split(r'\n(?=### \[SLIDE \d+)', md)
    result = []
    for block in blocks:
        m = re.match(r'### \[SLIDE (\d+)[^\n]*', block.strip())
        if not m: continue
        text = block[m.end():].strip()
        text = re.sub(r'\[[\d:]+[\s—\-][\d:]+\]', '', text)   # strip timestamp
        text = re.sub(r'\n---\s*$', '', text).strip()
        result.append({"num": int(m.group(1)), "text": text})
    return result

def clean_tts(text: str):
    text = re.sub(r'\[PAUSE\]', '...', text)
    text = re.sub(r'\*+([^*]+)\*+', r'\1', text)
    text = re.sub(r'`([^`]*)`', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'  +', ' ', text)
    return text.strip()

def tts(voice_id: str, text: str, out: Path) -> bool:
    url = TTS_URL.format(vid=voice_id) + "?output_format=mp3_44100_128"
    payload = {"text": clean_tts(text), "model_id": TTS_MODEL,
               "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json",
               "Accept": "audio/mpeg"}
    try:
        r = _requests.post(url, json=payload, headers=headers, timeout=90)
        r.raise_for_status()
        out.write_bytes(r.content)
        print(f"    {out.name}: {out.stat().st_size//1024} KB", flush=True)
        return True
    except Exception as e:
        print(f"    ERROR {out.name}: {e}", flush=True)
        return False

def step_b(module_dir: Path, produced: Path):
    print("\n[STEP B] Narration -> MP3 (ElevenLabs)", flush=True)
    src = module_dir / "narration.md"
    if not src.exists(): print("  WARN: narration.md missing"); return []

    voice_id = get_voice()
    narrations = parse_narration(src.read_text())
    print(f"  Parsed {len(narrations)} narration blocks", flush=True)

    audio_dir = produced / "audio"
    audio_dir.mkdir(exist_ok=True)
    mp3s = []
    for n in narrations:
        out = audio_dir / f"slide-{n['num']:02d}.mp3"
        print(f"  TTS slide {n['num']:02d}...", flush=True)
        if tts(voice_id, n["text"], out):
            mp3s.append(out)
        time.sleep(0.5)

    print(f"  MP3s: {len(mp3s)}", flush=True)
    return mp3s

# ── Step C: Video ─────────────────────────────────────────────────────────────

def step_c(produced: Path, module_slug: str, ffmpeg: str, ffprobe: str):
    print("\n[STEP C] PNG + MP3 -> MP4 (ffmpeg)", flush=True)
    slides_dir, audio_dir = produced / "slides", produced / "audio"
    pngs = sorted(slides_dir.glob("*.png")) if slides_dir.exists() else []
    mp3s = sorted(audio_dir.glob("slide-*.mp3")) if audio_dir.exists() else []
    if not pngs: print("  WARN: no PNGs"); return
    if not mp3s: print("  WARN: no MP3s"); return

    segs_dir = produced / "segments"
    segs_dir.mkdir(exist_ok=True)
    segs = []
    for mp3 in mp3s:
        m = re.search(r'slide-(\d+)', mp3.name)
        if not m: continue
        idx = int(m.group(1)) - 1
        png = pngs[idx] if idx < len(pngs) else pngs[-1]
        seg = segs_dir / f"seg-{idx+1:02d}.mp4"
        print(f"  Segment {idx+1:02d}: {png.name} + {mp3.name}", flush=True)
        r = runcmd([ffmpeg, "-y", "-loop", "1", "-i", str(png), "-i", str(mp3),
                    "-c:v", "libx264", "-tune", "stillimage", "-c:a", "aac",
                    "-pix_fmt", "yuv420p", "-shortest", str(seg)])
        if r.returncode == 0: segs.append(seg)

    if not segs: print("  ERROR: no segments produced"); return

    concat = produced / "_concat.txt"
    concat.write_text("\n".join(f"file '{s.resolve()}'" for s in segs))
    final = produced / f"{module_slug}.mp4"
    print(f"  Concat {len(segs)} segments -> {final.name}", flush=True)
    runcmd([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(final)])
    if final.exists():
        size_mb = final.stat().st_size / (1024*1024)
        dur = audio_dur(final, ffprobe)
        print(f"  Final MP4: {size_mb:.1f} MB, {dur:.0f}s", flush=True)

# ── Step D: PDFs ───────────────────────────────────────────────────────────────

def step_d(module_dir: Path, produced: Path, marp: str):
    print("\n[STEP D] assessment.md + facilitator-guide.md -> PDF", flush=True)
    for fname in ("assessment.md", "facilitator-guide.md"):
        src = module_dir / fname
        if not src.exists(): print(f"  WARN: {fname} missing"); continue
        out_pdf = produced / (src.stem + ".pdf")
        content = src.read_text()
        wrapped = f"---\nmarp: true\ntheme: uncover\npaginate: true\n---\n\n{content}"
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
            f.write(wrapped); tmp = f.name
        try:
            runcmd([marp, tmp, "--pdf", "--output", str(out_pdf), "--allow-local-files"])
        finally:
            os.unlink(tmp)
        if out_pdf.exists():
            print(f"  {out_pdf.name}: {out_pdf.stat().st_size//1024} KB", flush=True)
        else:
            # try pandoc fallback
            pandoc = shutil.which("pandoc")
            if pandoc:
                runcmd([pandoc, str(src), "-o", str(out_pdf)])
            else:
                print(f"  WARN: {out_pdf.name} not produced, pandoc not available")

# ── Validate ─────────────────────────────────────────────────────────────────

def validate(produced: Path, module_slug: str, ffprobe: str):
    print("\n[VALIDATE]", flush=True)
    pngs = sorted((produced/"slides").glob("*.png")) if (produced/"slides").exists() else []
    mp3s = sorted((produced/"audio").glob("*.mp3")) if (produced/"audio").exists() else []
    total_dur = sum(audio_dur(m, ffprobe) for m in mp3s)
    final = produced / f"{module_slug}.mp4"
    print(f"  PNGs: {len(pngs)} (expect 12)", flush=True)
    print(f"  MP3s: {len(mp3s)} (expect 12)", flush=True)
    print(f"  Total audio: {int(total_dur//60)}m {int(total_dur%60)}s", flush=True)
    if final.exists():
        print(f"  MP4: {final.stat().st_size/(1024*1024):.1f} MB, {audio_dur(final,ffprobe):.0f}s", flush=True)
    else:
        print(f"  MP4: NOT FOUND", flush=True)
    for p in ("slides.pdf", "assessment.pdf", "facilitator-guide.pdf"):
        fp = produced / p
        if fp.exists(): print(f"  {p}: {fp.stat().st_size//1024} KB", flush=True)
        else: print(f"  {p}: missing", flush=True)
    errors = []
    if len(pngs) == 0: errors.append("No PNGs")
    if len(mp3s) == 0: errors.append("No MP3s")
    if not final.exists(): errors.append("No MP4")
    if errors: print(f"\n  PARTIAL - issues: {', '.join(errors)}", flush=True)
    else: print("\n  ALL OUTPUTS OK", flush=True)
    return len(errors) == 0

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) != 2:
        sys.exit(f"Usage: python3 {sys.argv[0]} <module_directory_path>")
    module_dir = Path(sys.argv[1]).expanduser().resolve()
    if not module_dir.is_dir():
        sys.exit(f"ERROR: not a directory: {module_dir}")

    print(f"\n{'='*60}\nTRAINING PIPELINE\nModule: {module_dir.name}\n{'='*60}\n", flush=True)
    print("[TOOLS] Checking...", flush=True)
    ffmpeg  = need("ffmpeg")
    ffprobe = need("ffprobe")
    marp    = shutil.which("marp")
    if marp:
        print(f"  [ok] marp -> {marp}", flush=True)
    else:
        print("  [warn] marp not found - PDF slide export will be skipped", flush=True)
    # Check playwright
    try:
        import playwright  # noqa: F401
        print("  [ok] playwright available", flush=True)
    except ImportError:
        print("  [warn] playwright not installed - installing now...", flush=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "playwright"], check=True)
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        print("  [ok] playwright installed", flush=True)

    produced = module_dir / "produced"
    produced.mkdir(exist_ok=True)
    module_slug = slug(module_dir)
    print(f"\nOutput -> {produced}", flush=True)

    step_a(module_dir, produced)
    step_b(module_dir, produced)
    step_c(produced, module_slug, ffmpeg, ffprobe)
    if marp:
        step_d(module_dir, produced, marp)
    else:
        print("\n[STEP D] Skipped (marp not available)", flush=True)
    ok = validate(produced, module_slug, ffprobe)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()

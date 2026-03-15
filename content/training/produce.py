#!/usr/bin/env python3
"""
AROYA Training Pipeline — produce.py
Converts training markdown files into production-ready outputs:
  - slides/slide-01.png through slide-NN.png
  - slides.pdf
  - audio/slide-01.mp3 through slide-NN.mp3
  - {module-slug}.mp4
  - assessment.pdf
  - facilitator-guide.pdf

Usage:
  python3 produce.py <module_dir>

Example:
  python3 produce.py content/training/output/internal/module-00-train-the-trainer/
"""

import os
import sys
import re
import subprocess
import textwrap
import tempfile
import shutil
from pathlib import Path

# ── Brand colors ─────────────────────────────────────────────────────────────
CHARCOAL = (26, 26, 46)       # #1A1A2E
MAGENTA  = (233, 30, 99)      # #E91E63
WHITE    = (255, 255, 255)
LIGHT_BG = (248, 248, 252)    # near-white

SLIDE_W, SLIDE_H = 1920, 1080

FONT_BOLD   = "/System/Library/Fonts/HelveticaNeue.ttc"
FONT_REG    = "/System/Library/Fonts/HelveticaNeue.ttc"
FONT_MONO   = "/System/Library/Fonts/SFNSMono.ttf"

# ── Slide parsing ─────────────────────────────────────────────────────────────

def parse_slides(slides_md: str) -> list[dict]:
    """
    Parse slides.md into a list of slide dicts:
      { number, title, bullets, visual, speaker_note, dark_bg }
    """
    slides = []
    # Split on horizontal rules that separate slides
    # Slide blocks start with "## Slide N:"
    blocks = re.split(r'\n---\n', slides_md)

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        # Find slide header
        m = re.match(r'## Slide (\d+):', block, re.IGNORECASE)
        if not m:
            continue
        num = int(m.group(1))

        # Extract h1 title (first # line)
        title_m = re.search(r'^# (.+)$', block, re.MULTILINE)
        title = title_m.group(1).strip() if title_m else f"Slide {num}"

        # Extract h2 subtitle (## line, not "## Slide")
        subtitle_m = re.search(r'^## (?!Slide)(.+)$', block, re.MULTILINE)
        subtitle = subtitle_m.group(1).strip() if subtitle_m else ""

        # Extract bullets (- lines, not in Visual/Speaker sections)
        # Find content between h1 and **Visual:**
        visual_idx = block.find("**Visual:**")
        speaker_idx = block.find("**Speaker Note:**")

        content_end = visual_idx if visual_idx > 0 else len(block)
        content_block = block[:content_end]

        bullets = re.findall(r'^- (.+)$', content_block, re.MULTILINE)

        # Extract visual direction
        visual = ""
        if visual_idx >= 0:
            visual_end = speaker_idx if speaker_idx > visual_idx else len(block)
            visual_raw = block[visual_idx + len("**Visual:**"):visual_end].strip()
            visual = visual_raw.strip()

        # Extract speaker note
        speaker_note = ""
        if speaker_idx >= 0:
            speaker_note = block[speaker_idx + len("**Speaker Note:**"):].strip()

        # Determine background from visual directions
        dark_bg = "charcoal" in visual.lower() and "background" in visual.lower()

        slides.append({
            "number": num,
            "title": title,
            "subtitle": subtitle,
            "bullets": bullets,
            "visual": visual,
            "speaker_note": speaker_note,
            "dark_bg": dark_bg,
        })

    return slides


def parse_narration(narration_md: str) -> dict[int, str]:
    """
    Parse narration.md into {slide_number: narration_text} dict.
    """
    result = {}
    # Split on slide headers: ### [SLIDE N — ...]
    blocks = re.split(r'\n---\n', narration_md)

    for block in blocks:
        block = block.strip()
        m = re.search(r'\[SLIDE\s+(\d+)\s+[—-]', block)
        if not m:
            continue
        num = int(m.group(1))
        # Get text after the header line
        lines = block.split('\n')
        text_lines = []
        past_header = False
        for line in lines:
            if re.search(r'\[SLIDE\s+\d+\s+[—-]', line):
                past_header = True
                continue
            if past_header:
                text_lines.append(line)

        raw = '\n'.join(text_lines).strip()
        # Clean: remove [PAUSE], *emphasis*, markdown etc.
        clean = raw
        clean = re.sub(r'\[PAUSE\]', '', clean)
        clean = re.sub(r'\*([^*]+)\*', r'\1', clean)  # *italic* → plain
        clean = re.sub(r'`([^`]+)`', r'\1', clean)   # `code` → plain
        clean = re.sub(r'\n{3,}', '\n\n', clean)
        clean = clean.strip()

        result[num] = clean

    return result


# ── Slide rendering ───────────────────────────────────────────────────────────

def render_slide_png(slide: dict, out_path: Path):
    """Render a single slide as a 1920x1080 PNG."""
    from PIL import Image, ImageDraw, ImageFont

    dark = slide["dark_bg"]
    bg_color   = CHARCOAL if dark else LIGHT_BG
    fg_color   = WHITE    if dark else CHARCOAL
    accent     = MAGENTA

    img  = Image.new("RGB", (SLIDE_W, SLIDE_H), bg_color)
    draw = ImageDraw.Draw(img)

    # ── Fonts ──────────────────────────────────────────────────────────────
    def font(size, bold=False, idx=0):
        try:
            return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size, index=idx)
        except Exception:
            return ImageFont.load_default()

    f_title    = font(72, bold=True)
    f_subtitle = font(44)
    f_bullet   = font(38)
    f_small    = font(28)
    f_label    = font(22)

    # ── Magenta accent bar (left) ──────────────────────────────────────────
    draw.rectangle([0, 0, 10, SLIDE_H], fill=MAGENTA)

    # ── Slide number badge ─────────────────────────────────────────────────
    badge_text = f"  {slide['number']:02d}  "
    draw.rectangle([SLIDE_W - 120, SLIDE_H - 60, SLIDE_W, SLIDE_H], fill=MAGENTA)
    draw.text((SLIDE_W - 100, SLIDE_H - 48), f"{slide['number']:02d}",
              font=f_small, fill=WHITE)

    # ── Title ──────────────────────────────────────────────────────────────
    margin_left = 80
    y = 100

    title = slide["title"]
    # Wrap title if too long
    wrapped_title = textwrap.fill(title, width=45)
    for line in wrapped_title.split('\n'):
        draw.text((margin_left, y), line, font=f_title, fill=fg_color)
        bbox = draw.textbbox((0, 0), line, font=f_title)
        y += (bbox[3] - bbox[1]) + 8

    # ── Subtitle ───────────────────────────────────────────────────────────
    if slide.get("subtitle"):
        draw.text((margin_left, y), slide["subtitle"], font=f_subtitle, fill=accent)
        bbox = draw.textbbox((0, 0), slide["subtitle"], font=f_subtitle)
        y += (bbox[3] - bbox[1]) + 20

    # ── Magenta rule under title ───────────────────────────────────────────
    rule_y = y + 10
    draw.rectangle([margin_left, rule_y, SLIDE_W - margin_left, rule_y + 4], fill=MAGENTA)
    y = rule_y + 30

    # ── Bullets ────────────────────────────────────────────────────────────
    bullet_color  = WHITE if dark else CHARCOAL
    bullet_marker = "▸"

    for bullet in slide["bullets"][:7]:  # max 7 bullets shown
        wrapped = textwrap.fill(bullet, width=70)
        lines = wrapped.split('\n')
        first = True
        for line in lines:
            prefix = f"{bullet_marker}  " if first else "     "
            draw.text((margin_left + 20, y), prefix + line,
                      font=f_bullet, fill=bullet_color)
            bbox = draw.textbbox((0, 0), prefix + line, font=f_bullet)
            y += (bbox[3] - bbox[1]) + 10
            first = False
        y += 10  # extra gap between bullets

    # ── Footer bar ─────────────────────────────────────────────────────────
    footer_y = SLIDE_H - 60
    draw.rectangle([0, footer_y, SLIDE_W, SLIDE_H],
                   fill=CHARCOAL if not dark else (10, 10, 20))
    draw.text((margin_left, footer_y + 18), "AROYA Enablement Platform",
              font=f_label, fill=accent)

    img.save(str(out_path), "PNG")
    print(f"  ✓ {out_path.name}")


def render_slides(slides: list[dict], slides_dir: Path) -> list[Path]:
    """Render all slides to PNG and return paths in order."""
    slides_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for slide in slides:
        n = slide["number"]
        out = slides_dir / f"slide-{n:02d}.png"
        render_slide_png(slide, out)
        paths.append(out)
    return paths


def slides_to_pdf(png_paths: list[Path], pdf_path: Path):
    """Combine PNG slides into a PDF."""
    from PIL import Image
    images = [Image.open(str(p)).convert("RGB") for p in png_paths]
    if not images:
        raise ValueError("No slide images to combine")
    images[0].save(
        str(pdf_path),
        save_all=True,
        append_images=images[1:],
        resolution=150
    )
    print(f"  ✓ {pdf_path.name}")


# ── Audio generation ──────────────────────────────────────────────────────────

def generate_audio(narration_map: dict[int, str], audio_dir: Path) -> dict[int, Path]:
    """Generate MP3 per slide using gTTS. Returns {slide_num: mp3_path}."""
    from gtts import gTTS
    audio_dir.mkdir(parents=True, exist_ok=True)
    mp3_paths = {}

    for slide_num in sorted(narration_map.keys()):
        text = narration_map[slide_num]
        if not text.strip():
            print(f"  ⚠ Slide {slide_num}: empty narration, skipping")
            continue
        mp3_path = audio_dir / f"slide-{slide_num:02d}.mp3"
        print(f"  → Generating audio for slide {slide_num}...", end="", flush=True)
        tts = gTTS(text=text, lang='en', slow=False)
        tts.save(str(mp3_path))
        print(f" ✓ {mp3_path.name}")
        mp3_paths[slide_num] = mp3_path

    return mp3_paths


# ── Video stitching ───────────────────────────────────────────────────────────

FFMPEG = "/opt/homebrew/bin/ffmpeg"

def slide_to_clip(png_path: Path, mp3_path: Path, clip_path: Path):
    """Combine a PNG slide + MP3 narration into an MP4 clip."""
    cmd = [
        FFMPEG, "-y",
        "-loop", "1", "-i", str(png_path),
        "-i", str(mp3_path),
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-vf", "scale=1920:1080",
        str(clip_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg clip failed for {png_path.name}:\n{result.stderr[-500:]}")


def concat_clips(clip_paths: list[Path], out_mp4: Path):
    """Concatenate MP4 clips using ffmpeg concat demuxer."""
    # Write concat list file
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
        for cp in clip_paths:
            f.write(f"file '{cp.resolve()}'\n")
        list_file = f.name

    try:
        cmd = [
            FFMPEG, "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            str(out_mp4)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed:\n{result.stderr[-500:]}")
    finally:
        os.unlink(list_file)


def stitch_video(
    slides: list[dict],
    png_paths: list[Path],
    mp3_paths: dict[int, Path],
    produced_dir: Path,
    module_slug: str
):
    """Build per-slide clips then concatenate to final MP4."""
    clips_dir = produced_dir / "_clips"
    clips_dir.mkdir(exist_ok=True)

    clip_paths = []
    for slide in slides:
        n = slide["number"]
        png = produced_dir / "slides" / f"slide-{n:02d}.png"
        if n not in mp3_paths:
            print(f"  ⚠ Slide {n}: no audio, skipping from video")
            continue
        mp3 = mp3_paths[n]
        clip = clips_dir / f"clip-{n:02d}.mp4"
        print(f"  → clip {n:02d}...", end="", flush=True)
        slide_to_clip(png, mp3, clip)
        clip_paths.append(clip)
        print(f" ✓")

    mp4_out = produced_dir / f"{module_slug}.mp4"
    print(f"  → Concatenating {len(clip_paths)} clips...")
    concat_clips(clip_paths, mp4_out)
    print(f"  ✓ {mp4_out.name}")

    # Clean up clips dir
    shutil.rmtree(clips_dir, ignore_errors=True)
    return mp4_out


# ── PDF from markdown ─────────────────────────────────────────────────────────

def markdown_to_pdf(md_path: Path, pdf_path: Path, title: str = ""):
    """
    Convert a training markdown file to a styled PDF using reportlab.
    Handles: # H1, ## H2, ### H3, - bullets, **bold**, regular paragraphs.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
        ListFlowable, ListItem
    )
    from reportlab.lib.enums import TA_LEFT, TA_CENTER

    MAGENTA_RL = colors.HexColor("#E91E63")
    CHARCOAL_RL = colors.HexColor("#1A1A2E")
    GRAY = colors.HexColor("#555555")

    # Styles
    styles = getSampleStyleSheet()

    style_h1 = ParagraphStyle(
        "H1", parent=styles["Normal"],
        fontSize=22, leading=28, textColor=CHARCOAL_RL,
        fontName="Helvetica-Bold", spaceBefore=20, spaceAfter=6,
    )
    style_h2 = ParagraphStyle(
        "H2", parent=styles["Normal"],
        fontSize=16, leading=22, textColor=MAGENTA_RL,
        fontName="Helvetica-Bold", spaceBefore=16, spaceAfter=4,
    )
    style_h3 = ParagraphStyle(
        "H3", parent=styles["Normal"],
        fontSize=13, leading=18, textColor=CHARCOAL_RL,
        fontName="Helvetica-Bold", spaceBefore=12, spaceAfter=4,
    )
    style_body = ParagraphStyle(
        "Body", parent=styles["Normal"],
        fontSize=10, leading=15, textColor=GRAY,
        fontName="Helvetica", spaceBefore=4, spaceAfter=4,
    )
    style_bullet = ParagraphStyle(
        "Bullet", parent=style_body,
        leftIndent=20, bulletIndent=0, spaceBefore=2,
    )
    style_meta = ParagraphStyle(
        "Meta", parent=style_body,
        fontSize=9, textColor=colors.HexColor("#888888"),
        fontName="Helvetica-Oblique",
    )

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=1*inch, rightMargin=1*inch,
        topMargin=0.75*inch, bottomMargin=0.75*inch,
        title=title,
    )

    content_md = md_path.read_text(encoding="utf-8")
    story = []

    # Helper: escape special reportlab chars
    def esc(s: str) -> str:
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return s

    # Helper: inline formatting
    def inline(s: str) -> str:
        s = esc(s)
        # **bold**
        s = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)
        # *italic*
        s = re.sub(r'\*(.+?)\*', r'<i>\1</i>', s)
        # `code`
        s = re.sub(r'`(.+?)`', r'<font name="Courier">\1</font>', s)
        return s

    lines = content_md.split('\n')
    i = 0
    pending_bullets = []

    def flush_bullets():
        nonlocal pending_bullets
        if pending_bullets:
            items = [ListItem(Paragraph(inline(b), style_bullet), leftIndent=20)
                     for b in pending_bullets]
            story.append(ListFlowable(items, bulletType='bullet', start='•'))
            story.append(Spacer(1, 4))
            pending_bullets = []

    while i < len(lines):
        line = lines[i]

        # Blank line
        if not line.strip():
            flush_bullets()
            i += 1
            continue

        # HR
        if re.match(r'^---+$', line.strip()):
            flush_bullets()
            story.append(HRFlowable(width="100%", thickness=0.5,
                                     color=colors.HexColor("#DDDDDD")))
            story.append(Spacer(1, 6))
            i += 1
            continue

        # H1
        if line.startswith('# ') and not line.startswith('## '):
            flush_bullets()
            text = line[2:].strip()
            story.append(Paragraph(inline(text), style_h1))
            story.append(HRFlowable(width="100%", thickness=2, color=MAGENTA_RL))
            story.append(Spacer(1, 8))
            i += 1
            continue

        # H2
        if line.startswith('## '):
            flush_bullets()
            text = line[3:].strip()
            story.append(Paragraph(inline(text), style_h2))
            i += 1
            continue

        # H3
        if line.startswith('### '):
            flush_bullets()
            text = line[4:].strip()
            story.append(Paragraph(inline(text), style_h3))
            i += 1
            continue

        # Bullet
        if line.startswith('- ') or line.startswith('* '):
            pending_bullets.append(line[2:].strip())
            i += 1
            continue

        # Numbered list
        if re.match(r'^\d+\.\s', line):
            pending_bullets.append(re.sub(r'^\d+\.\s', '', line).strip())
            i += 1
            continue

        # Blockquote / indented
        if line.startswith('>'):
            flush_bullets()
            text = line.lstrip('> ').strip()
            q_style = ParagraphStyle("Quote", parent=style_body,
                                     leftIndent=30, textColor=CHARCOAL_RL,
                                     fontName="Helvetica-Oblique")
            story.append(Paragraph(inline(text), q_style))
            i += 1
            continue

        # Regular paragraph
        flush_bullets()
        story.append(Paragraph(inline(line.strip()), style_body))
        i += 1

    flush_bullets()

    doc.build(story)
    print(f"  ✓ {pdf_path.name}")


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_pipeline(module_dir: Path):
    module_dir = module_dir.resolve()

    # Validate input
    required = ["slides.md", "narration.md", "assessment.md",
                 "facilitator-guide.md", "metadata.json"]
    for f in required:
        if not (module_dir / f).exists():
            print(f"ERROR: missing {f} in {module_dir}")
            sys.exit(1)

    module_slug = module_dir.name
    produced_dir = module_dir / "produced"
    produced_dir.mkdir(exist_ok=True)

    print(f"\n{'='*60}")
    print(f"AROYA Training Pipeline")
    print(f"Module: {module_slug}")
    print(f"Output: {produced_dir}")
    print(f"{'='*60}\n")

    # ── Step 1: Parse slides ───────────────────────────────────────────────
    print("▶ Step 1/5: Parsing slides...")
    slides_md = (module_dir / "slides.md").read_text()
    slides = parse_slides(slides_md)
    print(f"  Found {len(slides)} slides")

    # ── Step 2: Render slides to PNG + PDF ─────────────────────────────────
    print("\n▶ Step 2/5: Rendering slides (PNG + PDF)...")
    slides_dir = produced_dir / "slides"
    png_paths  = render_slides(slides, slides_dir)
    slides_pdf = produced_dir / "slides.pdf"
    slides_to_pdf(png_paths, slides_pdf)

    # ── Step 3: Generate audio ─────────────────────────────────────────────
    print("\n▶ Step 3/5: Generating audio (MP3)...")
    narration_md  = (module_dir / "narration.md").read_text()
    narration_map = parse_narration(narration_md)
    print(f"  Found narration for {len(narration_map)} slides")
    audio_dir  = produced_dir / "audio"
    mp3_paths  = generate_audio(narration_map, audio_dir)

    # ── Step 4: Stitch video ───────────────────────────────────────────────
    print("\n▶ Step 4/5: Stitching video (MP4)...")
    stitch_video(slides, png_paths, mp3_paths, produced_dir, module_slug)

    # ── Step 5: Generate PDFs ──────────────────────────────────────────────
    print("\n▶ Step 5/5: Generating PDFs (assessment + facilitator guide)...")
    markdown_to_pdf(
        module_dir / "assessment.md",
        produced_dir / "assessment.pdf",
        title=f"{module_slug} — Assessment"
    )
    markdown_to_pdf(
        module_dir / "facilitator-guide.md",
        produced_dir / "facilitator-guide.pdf",
        title=f"{module_slug} — Facilitator Guide"
    )

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("✅ Pipeline complete! Outputs:")
    for item in sorted(produced_dir.rglob("*")):
        if item.is_file() and not item.name.startswith('.'):
            rel = item.relative_to(produced_dir)
            size_kb = item.stat().st_size // 1024
            print(f"  {rel}  ({size_kb} KB)")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    module_dir = Path(sys.argv[1])
    if not module_dir.exists():
        print(f"ERROR: directory not found: {module_dir}")
        sys.exit(1)

    run_pipeline(module_dir)

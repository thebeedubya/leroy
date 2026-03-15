"""Parse content agent markdown briefs into structured data.

Format expected:
  # Daily Media Brief: YYYY-MM-DD
  ## Yesterday's Summary
  ...summary text...

  ## Content Angle N: {title}
  **Post-Worthiness Score**: X/10
  **Target Angle**: ...
  **Source Sessions**: ...
  **Aianna Confidence**: high/medium/low
  **Status:** draft|approved|rejected|posted
  **Posted URLs:**

  ### Blog Post (dbradwood.com)
  ---
  yaml front matter
  ---
  blog body markdown

  ### LinkedIn
  post text

  ### X Thread
  1/ tweet...
  2/ tweet...

  ### Instagram
  CAPTION:
  caption text

  CAROUSEL:
  Slide 1: ...

  ## Aianna Query Log
  | table |
"""

import re
import logging
from datetime import date as date_type
from typing import Optional

logger = logging.getLogger("content-parser")


def parse_brief(text: str, source_date: str) -> dict:
    """Parse a full daily media brief into structured dict.

    Returns:
    {
        "date": "YYYY-MM-DD",
        "summary": "...",
        "angles": [
            {
                "index": 0,
                "title": "...",
                "score": 6,
                "target_angle": "...",
                "source_sessions": "...",
                "confidence": "high",
                "status": "draft",
                "platforms": {
                    "blog": {"content": "...", "front_matter": "..."},
                    "linkedin": {"content": "..."},
                    "x": {"content": "..."},
                    "instagram": {"content": "...", "carousel_slides": ["..."]}
                }
            }
        ],
        "aianna_query_log": "..."
    }
    """
    lines = text.split("\n")
    result = {
        "date": source_date,
        "summary": "",
        "angles": [],
        "aianna_query_log": "",
    }

    # Split into top-level sections by ## headers
    sections = _split_h2_sections(lines)

    for section_title, section_lines in sections:
        title_lower = section_title.lower()

        if "yesterday" in title_lower or "summary" in title_lower:
            result["summary"] = "\n".join(section_lines).strip()

        elif "content angle" in title_lower:
            angle = _parse_angle_section(section_title, section_lines, len(result["angles"]))
            if angle:
                result["angles"].append(angle)

        elif "aianna query" in title_lower or "query log" in title_lower:
            result["aianna_query_log"] = "\n".join(section_lines).strip()

    return result


def _is_toplevel_h2(line: str) -> bool:
    """Return True if this line is a known top-level ## section header.

    We only split on the known structural markers to avoid splitting on
    ## headers embedded inside blog post bodies.
    """
    if not line.startswith("## "):
        return False
    title = line[3:].strip().lower()
    return (
        title.startswith("content angle")
        or "yesterday" in title
        or "summary" in title
        or "aianna query" in title
        or "query log" in title
    )


def _split_h2_sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Split lines into (section_title, section_lines) tuples.

    Only splits on known top-level ## markers to avoid breaking on ##
    headers embedded in blog post bodies.
    """
    sections = []
    current_title = None
    current_lines = []

    for line in lines:
        if _is_toplevel_h2(line):
            if current_title is not None:
                sections.append((current_title, current_lines))
            current_title = line[3:].strip()
            current_lines = []
        elif current_title is not None:
            current_lines.append(line)

    if current_title is not None:
        sections.append((current_title, current_lines))

    return sections


def _parse_angle_section(section_title: str, lines: list[str], index: int) -> Optional[dict]:
    """Parse a '## Content Angle N: Title' section."""
    # Extract title from section heading
    # Pattern: "Content Angle N: Title" or "Content Angle N - Title"
    title = section_title
    m = re.match(r"Content Angle\s+\d+[:\-]\s*(.+)", section_title, re.IGNORECASE)
    if m:
        title = m.group(1).strip()

    angle = {
        "index": index,
        "title": title,
        "score": None,
        "target_angle": None,
        "source_sessions": None,
        "confidence": None,
        "status": "draft",
        "platforms": {},
    }

    # Parse metadata lines until first H3
    meta_end = 0
    for i, line in enumerate(lines):
        if line.startswith("### "):
            meta_end = i
            break
        else:
            meta_end = len(lines)
        _parse_meta_line(line, angle)

    # Parse H3 sub-sections (platforms)
    h3_sections = _split_h3_sections(lines[meta_end:])
    for h3_title, h3_lines in h3_sections:
        h3_lower = h3_title.lower()
        content = "\n".join(h3_lines).strip()

        if "blog" in h3_lower:
            front_matter, body = _extract_yaml_front_matter(content)
            angle["platforms"]["blog"] = {
                "content": body,
                "front_matter": front_matter,
            }
        elif "linkedin" in h3_lower:
            angle["platforms"]["linkedin"] = {"content": content}
        elif " x " in h3_lower or h3_lower.startswith("x ") or h3_lower == "x thread":
            angle["platforms"]["x"] = {"content": content}
        elif "instagram" in h3_lower:
            caption, carousel = _extract_instagram(content)
            angle["platforms"]["instagram"] = {
                "content": caption,
                "carousel_slides": carousel,
            }

    return angle


def _parse_meta_line(line: str, angle: dict):
    """Extract structured metadata from bold key-value lines."""
    # **Key**: Value  or  **Key:** Value
    m = re.match(r"\*\*([^*]+)\*\*[:\s]+(.+)", line)
    if not m:
        return
    key = m.group(1).strip().lower().rstrip(":")
    value = m.group(2).strip()

    if "score" in key:
        # "6/10" or "6"
        score_m = re.search(r"(\d+)", value)
        if score_m:
            angle["score"] = int(score_m.group(1))
    elif "target angle" in key:
        angle["target_angle"] = value
    elif "source session" in key:
        angle["source_sessions"] = value
    elif "aianna confidence" in key or "confidence" in key:
        angle["confidence"] = value.lower()
    elif "status" in key:
        angle["status"] = value.lower().strip("*").strip()


def _split_h3_sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Split lines into (h3_title, lines) tuples."""
    sections = []
    current_title = None
    current_lines = []

    for line in lines:
        if line.startswith("### "):
            if current_title is not None:
                sections.append((current_title, current_lines))
            current_title = line[4:].strip()
            current_lines = []
        elif current_title is not None:
            current_lines.append(line)

    if current_title is not None:
        sections.append((current_title, current_lines))

    return sections


def _extract_yaml_front_matter(content: str) -> tuple[str, str]:
    """Extract YAML front matter from content between --- delimiters."""
    if not content.startswith("---"):
        return "", content

    # Find closing ---
    rest = content[3:]
    end = rest.find("\n---")
    if end == -1:
        return "", content

    front_matter = rest[:end].strip()
    body = rest[end + 4:].strip()
    return front_matter, body


def _extract_instagram(content: str) -> tuple[str, list[str]]:
    """Split Instagram section into caption text and carousel slides list."""
    caption = ""
    carousel_slides = []

    # Split on CAROUSEL: marker
    parts = re.split(r"\n\s*CAROUSEL\s*:\s*\n", content, maxsplit=1, flags=re.IGNORECASE)

    if len(parts) == 2:
        cap_part, carousel_part = parts
        # Remove CAPTION: prefix if present
        cap_part = re.sub(r"^CAPTION\s*:\s*\n?", "", cap_part, flags=re.IGNORECASE).strip()
        caption = cap_part

        # Each non-empty line in carousel is a slide
        for line in carousel_part.split("\n"):
            line = line.strip()
            if line:
                carousel_slides.append(line)
    else:
        # No carousel, just caption
        cap = re.sub(r"^CAPTION\s*:\s*\n?", "", content, flags=re.IGNORECASE).strip()
        caption = cap

    return caption, carousel_slides

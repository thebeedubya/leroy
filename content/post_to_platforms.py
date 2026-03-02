#!/usr/bin/env python3
"""
Cowork Post - Social media publishing via Chrome AppleScript automation.

Usage:
    python3 post_to_platforms.py                    # posts today's draft
    python3 post_to_platforms.py 2026-02-28         # posts specific date
    python3 post_to_platforms.py /path/to/file.md   # posts specific file

Requirements:
    - macOS (uses osascript + System Events)
    - Google Chrome with active authenticated sessions for each platform
    - Terminal/Claude Code must have Accessibility access:
        System Settings > Privacy & Security > Accessibility
    - No pip packages required

Platforms:
    - LinkedIn  (via LinkedIn feed post composer)
    - X/Twitter (via x.com/compose/tweet, supports threads)
    - Instagram (via web, requires image path; caption-only not supported)
    - Blog      (dbradwood.com — method TBD, stubbed)
"""

import sys
import os
import re
import json
import time
import subprocess
import logging
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

DRAFTS_DIR = Path.home() / "Projects/leroy/content/drafts"
LOGS_DIR   = Path.home() / "Projects/leroy/content/logs"
POST_DELAY = 30   # seconds between platform posts to avoid rate limits

# ── Logging setup ─────────────────────────────────────────────────────────────

LOGS_DIR.mkdir(parents=True, exist_ok=True)
session_ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file    = LOGS_DIR / f"post_{session_ts}.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("cowork_post")

session_log: list[dict] = []   # accumulates per-platform results for this run


# ── AppleScript / Chrome helpers ──────────────────────────────────────────────

def _applescript(script: str, timeout: int = 30) -> tuple[str, str]:
    """Run AppleScript. Returns (stdout, stderr)."""
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return "", "timeout"


def chrome_navigate(url: str, wait: float = 4.0) -> None:
    """Open a URL in the front Chrome window and wait for it to load."""
    script = f'''tell application "Google Chrome"
    activate
    if (count windows) = 0 then make new window
    set URL of active tab of front window to "{url}"
end tell'''
    _applescript(script)
    time.sleep(wait)


def chrome_js(js: str) -> str:
    """Execute JS in the active Chrome tab; returns string result."""
    # AppleScript requires double-quotes escaped and newlines collapsed.
    safe = js.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    script = (
        'tell application "Google Chrome" to '
        f'execute active tab of front window javascript "{safe}"'
    )
    out, _ = _applescript(script)
    return out


def chrome_get_url() -> str:
    """Return the current active Chrome tab URL."""
    out, _ = _applescript(
        'tell application "Google Chrome" to get URL of active tab of front window'
    )
    return out


def wait_for(selector: str, timeout: float = 15.0) -> bool:
    """Poll until a CSS selector is present in the DOM, or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = chrome_js(f'document.querySelector("{selector}") !== null')
        if result == "true":
            return True
        time.sleep(0.75)
    return False


def chrome_click(selector: str) -> str:
    """Click a DOM element by CSS selector. Returns 'ok' or 'not_found'."""
    js = (
        f'var el = document.querySelector("{selector}");'
        ' if (el) { el.click(); "ok"; } else { "not_found"; }'
    )
    return chrome_js(js)


def chrome_click_by_text(tag: str, text: str) -> None:
    """Click the first element matching tag whose text content contains text."""
    chrome_js(
        f'Array.from(document.querySelectorAll("{tag}")).find('
        f'el => el.textContent.includes("{text}"))?.click()'
    )


def paste_into(selector: str, text: str) -> bool:
    """
    Reliable text input for React/SPA editors:
    1. Copy text to macOS clipboard via pbcopy.
    2. Focus the target element.
    3. Select-all then paste via System Events keystrokes.
    Returns True on success.

    NOTE: Requires Accessibility permission for Terminal / Claude Code:
          System Settings > Privacy & Security > Accessibility
    """
    # Step 1: load clipboard
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=5)
    except Exception as e:
        log.error(f"pbcopy failed: {e}")
        return False

    # Step 2: focus the element in Chrome
    focused = chrome_js(
        f'var el = document.querySelector("{selector}");'
        ' if (el) { el.focus(); "ok"; } else { "not_found"; }'
    )
    if focused != "ok":
        return False

    time.sleep(0.3)

    # Step 3: select all + paste
    _applescript('''tell application "Google Chrome" to activate
tell application "System Events"
    keystroke "a" using command down
    delay 0.2
    keystroke "v" using command down
end tell''')

    time.sleep(0.5)
    return True


def set_file_in_system_dialog(file_path: str) -> None:
    """
    After a file picker sheet opens in Chrome, type the path and press Return.
    Works with macOS NSOpenPanel sheets.
    """
    # Use Cmd+Shift+G to open "Go to folder" inside the dialog
    _applescript(f'''tell application "System Events"
    tell process "Google Chrome"
        keystroke "g" using {{command down, shift down}}
        delay 1
        keystroke "{file_path}"
        delay 0.5
        key code 36
        delay 0.5
        key code 36
    end tell
end tell''')


# ── Draft file parser ─────────────────────────────────────────────────────────

def parse_draft_file(path: Path) -> list[dict]:
    """
    Parse YYYY-MM-DD.md. Returns list of dicts for each approved draft.

    Handles two heading formats from the daily-media command:
      - "## Angle N: Title"          (spec format)
      - "## Content Angle N: Title"  (daily-media command format)

    Status field is required for posting (must be "approved").
    If no Status field is present in a block, the block is treated as "draft"
    and skipped.
    """
    raw = path.read_text(encoding="utf-8")

    # Match both "## Angle N:" and "## Content Angle N:" headings
    blocks = re.split(r'\n(?=## (?:Content )?Angle \d+:)', raw)

    drafts = []
    for block in blocks:
        title_m = re.match(r'## (?:Content )?Angle \d+: (.+)', block)
        if not title_m:
            continue

        title    = title_m.group(1).strip()
        status_m = re.search(r'\*\*Status:\*\*\s*(draft|approved|posted)', block)
        status   = status_m.group(1).strip() if status_m else "draft"

        if status != "approved":
            continue

        blog      = _extract_section(block, r"Blog Post \(dbradwood\.com\)")
        linkedin  = _extract_section(block, "LinkedIn")
        x_block   = _extract_section(block, "X Thread")
        tweets    = _parse_tweets(x_block)
        ig_block  = _extract_section(block, "Instagram")
        instagram = _parse_instagram(ig_block)

        src_m  = re.search(r'\*\*Source Sessions:\*\*\s*(.+)', block)
        source = src_m.group(1).strip() if src_m else ""

        drafts.append({
            "title":     title,
            "source":    source,
            "blog":      blog,
            "linkedin":  linkedin,
            "tweets":    tweets,
            "instagram": instagram,
        })

    return drafts


def _extract_section(block: str, heading: str) -> str:
    """
    Extract content under a ### heading within an angle block.

    Section boundaries:
      - Another ### heading
      - A section-separator "---" on its own line preceded by a blank line
        AND followed by a blank line then another ### heading.
        This avoids stopping on YAML frontmatter "---" inside blog content.
    """
    pattern = rf'### {heading}\n(.*?)(?=\n###|\n\n---\n\n###|\Z)'
    m = re.search(pattern, block, re.DOTALL)
    if not m:
        return ""
    raw = m.group(1)
    # Strip trailing standalone "---" section separators (not frontmatter)
    raw = re.sub(r'\n\n---\s*$', '', raw.rstrip())
    return raw.strip()


def _parse_tweets(x_block: str) -> list[str]:
    """
    Extract tweet texts. Handles two formats:
      - "**Tweet N:** text"            (spec format)
      - "N/ text"                      (daily-media X thread format)
    """
    # Try **Tweet N:** format first
    results = [
        m.group(1).strip()
        for m in re.finditer(
            r'\*\*Tweet \d+:\*\*\s*(.+?)(?=\n\*\*Tweet |\Z)', x_block, re.DOTALL
        )
    ]
    if results:
        return results

    # Fall back to "N/ text" thread format
    results = [
        m.group(1).strip()
        for m in re.finditer(
            r'^\d+/\s*(.+?)(?=\n\d+/|\Z)', x_block, re.DOTALL | re.MULTILINE
        )
    ]
    return results


def _parse_instagram(ig_block: str) -> dict:
    """
    Return {caption, image} from Instagram block.
    Handles two formats:
      - "**Caption:** text"    (spec format)
      - "CAPTION:\ntext"       (daily-media format)
    """
    # Spec format: **Caption:** text
    cap_m = re.search(r'\*\*Caption:\*\*\s*(.+?)(?=\n\*\*|\nCAPTION:|\nCAROUSEL:|\Z)', ig_block, re.DOTALL)
    if not cap_m:
        # daily-media format: CAPTION:\ntext\n\nCARROUSEL:
        cap_m = re.search(r'CAPTION:\n(.+?)(?=\n\nCAROUSEL:|\nCAROUSEL:|\Z)', ig_block, re.DOTALL)

    img_m = re.search(r'\*\*Image:\*\*\s*(.+)', ig_block)

    return {
        "caption": cap_m.group(1).strip() if cap_m else "",
        "image":   img_m.group(1).strip() if img_m else "needed",
    }


# ── Platform posters ──────────────────────────────────────────────────────────

def _result(platform: str) -> dict:
    return {
        "platform":  platform,
        "success":   False,
        "url":       "",
        "error":     "",
        "timestamp": datetime.now().isoformat(),
    }


def post_linkedin(content: str) -> dict:
    """Post to LinkedIn via Chrome automation."""
    r = _result("linkedin")
    log.info("→ Posting to LinkedIn...")

    try:
        chrome_navigate("https://www.linkedin.com/feed/", wait=5)

        if "linkedin.com/login" in chrome_get_url() or "linkedin.com/uas" in chrome_get_url():
            r["error"] = "Not authenticated — log in to LinkedIn in Chrome first."
            return r

        # Click "Start a post" — try multiple selector strategies
        triggers = [
            ".share-box-feed-entry__trigger",
            "[data-control-name='share.sharebox_feed_entry_trigger']",
            ".share-creation-state__placeholder-trigger",
            "button[aria-label='Start a post']",
        ]
        opened = False
        for sel in triggers:
            if wait_for(sel, timeout=6):
                if chrome_click(sel) == "ok":
                    opened = True
                    break

        if not opened:
            # Fallback: text search
            chrome_click_by_text("button", "Start a post")
            opened = True

        time.sleep(2.5)

        # Find the content editor (LinkedIn uses Quill)
        editors = [
            ".ql-editor",
            ".share-creation-state__content .ql-editor",
            "[contenteditable='true']",
        ]
        typed = False
        for sel in editors:
            if wait_for(sel, timeout=10):
                if paste_into(sel, content):
                    typed = True
                    break

        if not typed:
            r["error"] = "Could not locate LinkedIn post editor."
            return r

        time.sleep(1.0)

        # Click Post
        post_btns = [
            ".share-actions__primary-action",
            ".share-box_actions button.artdeco-button--primary",
            "button[aria-label='Post']",
        ]
        posted = False
        for sel in post_btns:
            if wait_for(sel, timeout=8):
                if chrome_click(sel) == "ok":
                    posted = True
                    break

        if not posted:
            chrome_click_by_text("button", "Post")
            posted = True

        time.sleep(4)

        r["success"] = True
        r["url"]     = "https://www.linkedin.com/feed/"
        log.info("  LinkedIn: POSTED")

    except Exception as e:
        r["error"] = str(e)
        log.error(f"  LinkedIn: FAILED — {e}")

    return r


def post_x_thread(tweets: list[str]) -> dict:
    """Post a thread to X/Twitter via Chrome automation."""
    r = _result("twitter")
    log.info(f"→ Posting X thread ({len(tweets)} tweet(s))...")

    if not tweets:
        r["error"] = "No tweets provided."
        return r

    try:
        chrome_navigate("https://x.com/compose/tweet", wait=5)

        url = chrome_get_url()
        if "login" in url or "flow/login" in url:
            r["error"] = "Not authenticated — log in to X in Chrome first."
            return r

        # First tweet textarea
        ta0_selectors = [
            "[data-testid='tweetTextarea_0']",
            ".public-DraftEditor-content",
            "[aria-label='Tweet text']",
            "[aria-label='Post text']",
        ]
        typed = False
        for sel in ta0_selectors:
            if wait_for(sel, timeout=12):
                if paste_into(sel, tweets[0]):
                    typed = True
                    break

        if not typed:
            r["error"] = "Could not find X tweet composer."
            return r

        time.sleep(0.5)

        # Additional tweets in thread
        for i, tweet_text in enumerate(tweets[1:], start=1):
            add_selectors = [
                "[data-testid='addButton']",
                "[aria-label='Add']",
            ]
            for sel in add_selectors:
                if wait_for(sel, timeout=6):
                    chrome_click(sel)
                    break
            time.sleep(0.8)

            ta_sel = f"[data-testid='tweetTextarea_{i}']"
            if wait_for(ta_sel, timeout=8):
                paste_into(ta_sel, tweet_text)
            time.sleep(0.4)

        # Post the thread
        post_selectors = [
            "[data-testid='tweetButtonInline']",
            "[data-testid='tweetButton']",
            "button[aria-label='Post']",
        ]
        posted = False
        for sel in post_selectors:
            if wait_for(sel, timeout=8):
                if chrome_click(sel) == "ok":
                    posted = True
                    break

        if not posted:
            r["error"] = "Could not find X post button."
            return r

        time.sleep(4)

        r["success"] = True
        r["url"]     = chrome_get_url()
        log.info("  X/Twitter: POSTED")

    except Exception as e:
        r["error"] = str(e)
        log.error(f"  X/Twitter: FAILED — {e}")

    return r


def post_instagram(caption: str, image_path: str) -> dict:
    """
    Post to Instagram web via Chrome automation.
    Image upload uses the macOS file picker via System Events.
    """
    r = _result("instagram")
    log.info("→ Posting to Instagram...")

    if not image_path or image_path.lower() == "needed":
        r["error"] = "No image path provided — Instagram requires an image."
        return r

    img = Path(image_path).expanduser().resolve()
    if not img.exists():
        r["error"] = f"Image not found: {img}"
        return r

    try:
        chrome_navigate("https://www.instagram.com/", wait=5)

        url = chrome_get_url()
        if "accounts/login" in url:
            r["error"] = "Not authenticated — log in to Instagram in Chrome first."
            return r

        # Click the "Create" / "New post" button
        create_clicked = False
        create_selectors = [
            "[aria-label='New post']",
            "svg[aria-label='New post']",
        ]
        for sel in create_selectors:
            if wait_for(sel, timeout=10):
                # Use single-quoted JS string to avoid AppleScript escaping issues
                chrome_js(
                    f"var el = document.querySelector('{sel}');"
                    " if (el) {"
                    "   var p = el.closest('a, button, [role=\"button\"]');"
                    "   if (p) p.click(); else el.click();"
                    " }"
                )
                create_clicked = True
                break

        if not create_clicked:
            # Fallback: find New post link by aria-label
            chrome_js(
                "Array.from(document.querySelectorAll('a,button'))"
                ".find(el => el.getAttribute('aria-label') === 'New post')?.click()"
            )

        time.sleep(3)

        # Look for file input (Instagram renders one after clicking Create)
        # Click "Select from computer" if a button appears
        chrome_click_by_text("button", "Select from computer")
        time.sleep(1.5)

        # Use System Events to type the file path into the native file picker
        set_file_in_system_dialog(str(img))
        time.sleep(2)

        # Advance through the multi-step dialog (Crop → Filter → Caption)
        for _ in range(2):
            next_selectors = [
                "button[aria-label='Next']",
                "button._acan._acap._acas",
            ]
            for sel in next_selectors:
                if wait_for(sel, timeout=6):
                    chrome_click(sel)
                    break
            chrome_click_by_text("div[role='button']", "Next")
            time.sleep(2)

        # Find caption textarea
        caption_selectors = [
            "textarea[aria-label='Write a caption...']",
            "[aria-label='Write a caption...']",
            ".uiScrollableArea textarea",
        ]
        cap_typed = False
        for sel in caption_selectors:
            if wait_for(sel, timeout=10):
                if paste_into(sel, caption):
                    cap_typed = True
                    break

        if not cap_typed:
            r["error"] = "Could not find Instagram caption field."
            return r

        time.sleep(1)

        # Click Share
        share_selectors = [
            "button[type='button']",
        ]
        chrome_click_by_text("div[role='button']", "Share")
        time.sleep(4)

        r["success"] = True
        r["url"]     = "https://www.instagram.com/"
        log.info("  Instagram: POSTED")

    except Exception as e:
        r["error"] = str(e)
        log.error(f"  Instagram: FAILED — {e}")

    return r


def post_blog(blog_content: str, title: str) -> dict:
    """
    Publish to dbradwood.com.
    Method TBD pending site rebuild decision (git push / CMS / direct file).
    Currently stubbed.
    """
    r = _result("blog")
    r["error"] = "Blog publishing method TBD — dbradwood.com rebuild in progress. Skipped."
    log.info("  Blog: SKIPPED (deployment method TBD)")
    return r


# ── Draft file updater ────────────────────────────────────────────────────────

def update_draft_file(path: Path, title: str, platform_results: list[dict]) -> None:
    """Update the draft file: set status=posted, fill in Posted URLs."""
    content = path.read_text(encoding="utf-8")

    posted_urls = ", ".join(
        f"{r['platform']}: {r['url']}"
        for r in platform_results
        if r.get("success") and r.get("url")
    ) or "see logs"

    # Locate the angle block and patch it — handle both heading formats
    title_esc = re.escape(title)
    pattern = re.compile(
        rf'(## (?:Content )?Angle \d+: {title_esc}.*?)(?=\n## (?:Content )?Angle |\Z)',
        re.DOTALL,
    )

    def patch(m: re.Match) -> str:
        block = m.group(1)
        block = re.sub(r'\*\*Status:\*\*\s*approved', '**Status:** posted', block)
        block = re.sub(r'\*\*Posted URLs:\*\*\s*.+', f'**Posted URLs:** {posted_urls}', block)
        return block

    updated = pattern.sub(patch, content)
    path.write_text(updated, encoding="utf-8")
    log.info(f"  Draft file updated for: {title}")


# ── Session log writer ────────────────────────────────────────────────────────

def write_session_log(entry: dict) -> None:
    session_log.append(entry)
    log_file.write_text(json.dumps(session_log, indent=2), encoding="utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────

def resolve_draft_path(arg: str | None) -> Path:
    if arg is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
        return DRAFTS_DIR / f"{date_str}.md"
    p = Path(arg).expanduser()
    if p.suffix == ".md":
        return p
    # Treat as YYYY-MM-DD date string
    return DRAFTS_DIR / f"{arg}.md"


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    draft_path = resolve_draft_path(arg)

    if not draft_path.exists():
        print(f"ERROR: Draft file not found: {draft_path}")
        sys.exit(1)

    log.info(f"Reading: {draft_path}")
    approved = parse_draft_file(draft_path)

    if not approved:
        print("No approved drafts found.")
        print("Set **Status:** approved for any draft you want to publish.")
        sys.exit(0)

    print(f"\nFound {len(approved)} approved draft(s):")
    for d in approved:
        print(f"  - {d['title']}")

    total_platforms = 0
    total_success   = 0

    for draft in approved:
        print(f"\n{'='*60}")
        print(f"Publishing: {draft['title']}")
        print(f"{'='*60}")

        platform_results: list[dict] = []

        # 1. LinkedIn
        if draft["linkedin"]:
            lr = post_linkedin(draft["linkedin"])
            platform_results.append(lr)
            write_session_log({"draft": draft["title"], **lr})
            total_platforms += 1
            if lr["success"]:
                total_success += 1
                print(f"  LinkedIn:  OK — {lr['url']}")
            else:
                print(f"  LinkedIn:  FAILED — {lr['error']}")
            time.sleep(POST_DELAY)

        # 2. X/Twitter thread
        if draft["tweets"]:
            xr = post_x_thread(draft["tweets"])
            platform_results.append(xr)
            write_session_log({"draft": draft["title"], **xr})
            total_platforms += 1
            if xr["success"]:
                total_success += 1
                print(f"  X/Twitter: OK — {xr['url']}")
            else:
                print(f"  X/Twitter: FAILED — {xr['error']}")
            time.sleep(POST_DELAY)

        # 3. Instagram
        ig = draft["instagram"]
        if ig["caption"]:
            ir = post_instagram(ig["caption"], ig["image"])
            platform_results.append(ir)
            write_session_log({"draft": draft["title"], **ir})
            total_platforms += 1
            if ir["success"]:
                total_success += 1
                print(f"  Instagram: OK — {ir['url']}")
            else:
                print(f"  Instagram: FAILED — {ir['error']}")
        else:
            print("  Instagram: SKIPPED (no caption provided)")

        # 4. Blog
        if draft["blog"]:
            br = post_blog(draft["blog"], draft["title"])
            platform_results.append(br)
            write_session_log({"draft": draft["title"], **br})
            print(f"  Blog:      {br['error']}")

        # Update draft file
        update_draft_file(draft_path, draft["title"], platform_results)

    print(f"\n{'='*60}")
    print(f"SUMMARY: {total_success}/{total_platforms} platforms succeeded")
    print(f"Log:     {log_file}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

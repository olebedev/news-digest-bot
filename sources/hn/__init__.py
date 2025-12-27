import datetime
import html
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from readability import Document

SLUG = "hn"
SOURCE_NAME = "Hacker News"
HN_API = "https://hacker-news.firebaseio.com/v0"

BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / "state.json"
OUT_DIR = BASE_DIR / "out"  # gitignored

THRESH = 100
MAX_ITEMS_PER_GEN = 10
PAGE_SIZE = 200
MAX_HISTORY_ENTRIES = 200
TOP_STORIES_SIZE = 500
ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("", ATOM_NS)


def entry_key(data):
    return str(
        data.get("id") or data.get("comments") or data.get("link") or data.get("title")
    )


logger = logging.getLogger(__name__)
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def get_json(url, timeout=20):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def hn_item(item_id: int):
    return get_json(f"{HN_API}/item/{item_id}.json")


def strip_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return " ".join(soup.get_text(" ").split())


def fetch_article_text(url: str, limit_chars=30000) -> str:
    r = requests.get(url, timeout=25, headers={"User-Agent": "news-digest-bot/1.0"})
    r.raise_for_status()
    doc = Document(r.text)
    main_html = doc.summary(html_partial=True)
    text = strip_text_from_html(main_html)
    return text[:limit_chars]


def fetch_hn_thread_html(comments_url: str, limit_chars=400_000) -> str:
    r = requests.get(
        comments_url, timeout=25, headers={"User-Agent": "news-digest-bot/1.0"}
    )
    r.raise_for_status()
    return r.text[:limit_chars]


def summarize(system, user):
    resp = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.output_text.strip()


def load_state():
    if not STATE_PATH.exists():
        return {"last_scores": {}, "feed_entries": []}
    with STATE_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    def parse_dt(dt_str):
        if not dt_str:
            return None
        try:
            dt = datetime.datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt
        except Exception:
            return None

    feed_entries = []
    for e in data.get("feed_entries", []):
        entry = dict(e)
        entry["published_at"] = parse_dt(e.get("published_at"))
        feed_entries.append(entry)

    return {
        "last_scores": data.get("last_scores", {}),
        "feed_entries": feed_entries,
    }


def save_state(state):
    def to_serializable(entries):
        out = []
        for e in entries:
            item = dict(e)
            dt = item.get("published_at")
            if isinstance(dt, datetime.datetime):
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                item["published_at"] = dt.isoformat()
            out.append(item)
        return out

    state_to_save = {
        "last_scores": state.get("last_scores", {}),
        "feed_entries": to_serializable(state.get("feed_entries", [])),
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state_to_save, f, ensure_ascii=False, indent=2)


def isoformat(dt: datetime.datetime | None) -> str:
    if dt is None:
        dt = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.isoformat()


def render_summary_html(entry: dict) -> str:
    points = entry.get("score")
    comments_count = entry.get("comments_count")
    main_link = entry.get("link")
    comments_link = entry.get("comments")
    article_summary = entry.get("article_summary") or ""
    comments_summary = entry.get("comments_summary") or ""

    parts = [
        f"<p><strong>Points:</strong> {html.escape(str(points) if points is not None else 'n/a')}</p>",
    ]

    parts.append(
        f"<p><strong>Total comments:</strong> {html.escape(str(comments_count) if comments_count is not None else 'n/a')}</p>"
    )

    if main_link:
        esc = html.escape(main_link)
        parts.append(f'<p><strong>Link:</strong> <a href="{esc}">{esc}</a></p>')
    else:
        parts.append("<p><strong>Link:</strong> (none)</p>")

    parts.append(
        f"<p><strong>Article summary:</strong> {html.escape(article_summary)}</p>"
    )

    bullet_items = [
        line[1:].strip() if line.strip().startswith("-") else None
        for line in comments_summary.splitlines()
    ]
    bullet_items = [b.lstrip() for b in bullet_items if b]

    if bullet_items:
        items_html = "".join(f"<li>{html.escape(item)}</li>" for item in bullet_items)
        parts.append(f"<p><strong>Comments summary:</strong></p><ul>{items_html}</ul>")
    else:
        parts.append(
            f"<p><strong>Comments summary:</strong> {html.escape(comments_summary)}</p>"
        )

    if comments_link:
        esc = html.escape(comments_link)
        parts.append(f'<p><strong>HN thread:</strong> <a href="{esc}">{esc}</a></p>')

    return "".join(parts)


def write_atom_feeds(entries, generated_at: datetime.datetime, base_url: str | None):
    base_url = base_url.rstrip("/") if base_url else ""
    total_entries = len(entries)
    total_pages = max(1, (total_entries + PAGE_SIZE - 1) // PAGE_SIZE)
    generated_paths = []

    def feed_filename(idx: int) -> str:
        return "feed.xml" if idx == 0 else f"feed-{idx}.xml"

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for idx in range(total_pages):
        start = idx * PAGE_SIZE
        end = start + PAGE_SIZE
        page_entries = entries[start:end]

        filename = feed_filename(idx)
        self_href = f"{base_url}/{filename}" if base_url else filename
        current_href = (
            f"{base_url}/{feed_filename(0)}" if base_url else feed_filename(0)
        )

        feed = ET.Element(f"{{{ATOM_NS}}}feed")
        ET.SubElement(
            feed, f"{{{ATOM_NS}}}title"
        ).text = f"News digest bot ({SOURCE_NAME} 100+ points)"
        ET.SubElement(feed, f"{{{ATOM_NS}}}id").text = self_href or "urn:news-digest"
        ET.SubElement(feed, f"{{{ATOM_NS}}}updated").text = isoformat(generated_at)
        ET.SubElement(
            feed,
            f"{{{ATOM_NS}}}link",
            {
                "rel": "self",
                "href": self_href,
                "type": "application/atom+xml",
            },
        )
        ET.SubElement(
            feed,
            f"{{{ATOM_NS}}}link",
            {
                "rel": "current",
                "href": current_href,
                "type": "application/atom+xml",
            },
        )
        ET.SubElement(
            feed,
            f"{{{ATOM_NS}}}link",
            {"rel": "alternate", "href": "https://news.ycombinator.com/"},
        )

        if idx == 0 and total_pages > 1:
            next_href = (
                f"{base_url}/{feed_filename(idx + 1)}"
                if base_url
                else feed_filename(idx + 1)
            )
            ET.SubElement(
                feed, f"{{{ATOM_NS}}}link", {"rel": "next-archive", "href": next_href}
            )
            ET.SubElement(
                feed, f"{{{ATOM_NS}}}link", {"rel": "next", "href": next_href}
            )
        if idx > 0:
            prev_href = (
                f"{base_url}/{feed_filename(idx - 1)}"
                if base_url
                else feed_filename(idx - 1)
            )
            ET.SubElement(
                feed, f"{{{ATOM_NS}}}link", {"rel": "prev-archive", "href": prev_href}
            )
            ET.SubElement(
                feed, f"{{{ATOM_NS}}}link", {"rel": "prev", "href": prev_href}
            )
            if idx < total_pages - 1:
                next_href = (
                    f"{base_url}/{feed_filename(idx + 1)}"
                    if base_url
                    else feed_filename(idx + 1)
                )
                ET.SubElement(
                    feed,
                    f"{{{ATOM_NS}}}link",
                    {"rel": "next-archive", "href": next_href},
                )
                ET.SubElement(
                    feed, f"{{{ATOM_NS}}}link", {"rel": "next", "href": next_href}
                )

        for e in page_entries:
            entry_el = ET.SubElement(feed, f"{{{ATOM_NS}}}entry")
            ET.SubElement(entry_el, f"{{{ATOM_NS}}}title").text = e["title"]
            entry_id = (
                f"urn:news-digest:{e.get('id') or e.get('comments') or e.get('link')}"
            )
            ET.SubElement(entry_el, f"{{{ATOM_NS}}}id").text = entry_id

            published = e.get("published_at") or generated_at
            ET.SubElement(entry_el, f"{{{ATOM_NS}}}updated").text = isoformat(published)
            ET.SubElement(entry_el, f"{{{ATOM_NS}}}published").text = isoformat(
                published
            )

            main_link = e.get("link")
            comments_link = e.get("comments")
            if main_link:
                ET.SubElement(
                    entry_el,
                    f"{{{ATOM_NS}}}link",
                    {"rel": "alternate", "href": main_link},
                )
            if comments_link:
                ET.SubElement(
                    entry_el,
                    f"{{{ATOM_NS}}}link",
                    {"rel": "related", "href": comments_link, "title": "HN comments"},
                )

            summary_el = ET.SubElement(entry_el, f"{{{ATOM_NS}}}summary")
            summary_el.set("type", "html")
            summary_el.text = render_summary_html(e)

        out_path = OUT_DIR / filename
        ET.ElementTree(feed).write(out_path, encoding="utf-8", xml_declaration=True)
        generated_paths.append(out_path)
        logger.info(
            "Wrote Atom feed page %s with %d entries", out_path, len(page_entries)
        )

    # clean up old feed pages that are no longer used
    for existing in OUT_DIR.glob("feed*.xml"):
        if existing not in generated_paths:
            try:
                existing.unlink()
                logger.info("Removed stale feed page %s", existing)
            except OSError:
                logger.warning("Failed to remove stale feed page %s", existing)

    return generated_paths


def run(feed_base_url: str | None = None):
    logger.info(
        "Starting %s digest run (threshold=%s, max_items=%s)",
        SOURCE_NAME,
        THRESH,
        MAX_ITEMS_PER_GEN,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    last_scores = {int(k): v for k, v in state.get("last_scores", {}).items()}
    feed_history = state.get("feed_entries", [])
    seen_keys = {entry_key(e) for e in feed_history}
    logger.info(
        "Loaded state: %d scores tracked, %d historical entries",
        len(last_scores),
        len(feed_history),
    )

    ids = get_json(f"{HN_API}/topstories.json")[:TOP_STORIES_SIZE]
    logger.info("Fetched %d top stories to scan", len(ids))

    crossed = []
    for i, item_id in enumerate(ids):
        item = hn_item(item_id)
        if not item or item.get("type") != "story":
            continue
        score = int(item.get("score") or 0)
        prev = int(last_scores.get(item_id, 0))

        title = item.get("title")
        main_url = item.get("url", "")
        hn_comments = f"https://news.ycombinator.com/item?id={item_id}"
        item_key = entry_key(
            {
                "id": item_id,
                "comments": hn_comments,
                "link": main_url,
                "title": title,
            }
        )
        if prev < THRESH <= score and item_key not in seen_keys:
            crossed.append((score, item))
            logger.info(
                "Story %s crossed %s points (%s)",
                item_id,
                THRESH,
                item.get("title", "(no title)"),
            )
        last_scores[item_id] = score

        if i % 25 == 0:
            time.sleep(0.2)
            logger.info("Scanned %d/%d top stories", i + 1, len(ids))

    crossed.sort(key=lambda x: x[0], reverse=True)
    crossed = crossed[:MAX_ITEMS_PER_GEN]

    now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    new_entries = []

    if not crossed:
        logger.info("No new stories crossed threshold; generating feed with 0 items")

    for score, item in crossed:
        item_id = item["id"]
        title = item.get("title", "(no title)")
        hn_comments = f"https://news.ycombinator.com/item?id={item_id}"
        main_url = item.get("url", "")
        comments_count = item.get("descendants")
        logger.info("Preparing digest entry for %s (%s points)", item_id, score)

        thread_html = None
        article_summary = "(no external link)"
        if main_url:
            logger.info("Fetching and summarizing article: %s", main_url)
            try:
                article_text = fetch_article_text(main_url)
                article_summary = summarize(
                    "You summarize articles for a technical audience. Keep it concise and focused on facts, one short paragraph.",
                    f"Title: {title}\nURL: {main_url}\n\nArticle text:\n{article_text}",
                )
                logger.info("Article summary complete for %s", item_id)
            except Exception as e:
                article_summary = f"(failed to fetch/summarize article: {e})"
                logger.warning("Failed to summarize article for %s: %s", item_id, e)
        else:
            # no external link; try HN text body or thread page
            try:
                body_html = item.get("text")
                if body_html:
                    article_text = strip_text_from_html(body_html)
                else:
                    if thread_html is None:
                        thread_html = fetch_hn_thread_html(hn_comments)
                    article_text = strip_text_from_html(thread_html)
                article_summary = summarize(
                    "You summarize Hacker News self-posts or thread content. Keep it concise and focused on the main subject, one short paragraph.",
                    f"Title: {title}\nHN thread: {hn_comments}\n\nThread text:\n{article_text}",
                )
                logger.info(
                    "Article summary (HN self-post/thread) complete for %s", item_id
                )
            except Exception as e:
                article_summary = f"(failed to summarize HN post/thread: {e})"
                logger.warning(
                    "Failed to summarize HN post/thread for %s: %s", item_id, e
                )

        logger.info("Fetching and summarizing comments: %s", hn_comments)
        try:
            if thread_html is None:
                thread_html = fetch_hn_thread_html(hn_comments)
            comments_html = thread_html
            comments_summary = summarize(
                "You summarize Hacker News comment threads from the raw HTML page. Output two parts:\n1) 'Top upvoted themes:' 3-5 bullets reflecting the most upvoted or most visible comments/threads and their arguments (group similar ideas).\n2) 'Overall discussion:' one concise paragraph capturing main themes and disagreements. Avoid quotes and usernames. Focus on the visible ordering of comments as presented in the HTML.",
                f"HN thread: {hn_comments}\nTitle: {title}\n\nHN thread HTML:\n{comments_html}",
            )
            logger.info("Comments summary complete for %s", item_id)
        except Exception as e:
            comments_summary = f"(failed to fetch/summarize comments: {e})"
            logger.warning("Failed to summarize comments for %s: %s", item_id, e)

        new_entries.append(
            {
                "id": item_id,
                "title": title,
                "score": score,
                "comments": hn_comments,
                "link": main_url,
                "comments_count": comments_count,
                "article_summary": article_summary,
                "comments_summary": comments_summary,
                "published_at": datetime.datetime.utcfromtimestamp(
                    int(item.get("time", now.timestamp()))
                ).replace(tzinfo=datetime.timezone.utc),
            }
        )

        seen_keys.add(
            entry_key(
                {
                    "id": item_id,
                    "comments": hn_comments,
                    "link": main_url,
                    "title": title,
                }
            )
        )

    combined = {}
    for e in feed_history:
        combined[entry_key(e)] = e
    for e in new_entries:
        combined[entry_key(e)] = e

    sorted_entries = sorted(
        combined.values(),
        key=lambda e: (
            e.get("published_at")
            or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
            e.get("score") or 0,
        ),
        reverse=True,
    )[:MAX_HISTORY_ENTRIES]

    save_state(
        {
            "last_scores": {str(k): v for k, v in last_scores.items()},
            "feed_entries": sorted_entries,
        }
    )
    logger.info(
        "Run complete: tracked %d stories, total entries %d",
        len(last_scores),
        len(sorted_entries),
    )

    generated_paths = write_atom_feeds(
        sorted_entries, generated_at=now, base_url=feed_base_url
    )

    return {"slug": SLUG, "files": generated_paths}

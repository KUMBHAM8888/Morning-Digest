import os
import re
import html
import smtplib
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
from google import genai

load_dotenv()

HOURS_FRESH = 24
MAX_ARTICLES_PER_FEED = 8

NITTER_INSTANCES = [
    "nitter.privacydev.net",
    "nitter.poast.org",
    "nitter.woodland.cafe",
    "nitter.1d4.us",
]

TWITTER_ACCOUNTS = ["AndrewYNg", "ylecun", "sama", "elonmusk"]

FEEDS = {
    "tech_ai": [
        ("The Verge",           "https://www.theverge.com/rss/index.xml"),
        ("TechCrunch",          "https://techcrunch.com/feed/"),
        ("Wired",               "https://www.wired.com/feed/rss"),
        ("Hacker News",         "https://news.ycombinator.com/rss"),
        ("Ars Technica",        "https://feeds.arstechnica.com/arstechnica/index"),
        ("MIT Technology Review","https://www.technologyreview.com/feed/"),
        ("VentureBeat AI",      "https://venturebeat.com/category/ai/feed/"),
        ("NVIDIA Newsroom",     "https://nvidianews.nvidia.com/rss"),
        ("Anthropic",           "https://www.anthropic.com/rss.xml"),
        ("OpenAI",              "https://openai.com/news/rss.xml"),
        ("Google DeepMind",     "https://deepmind.google/blog/rss.xml"),
        ("Reddit AI/Tech",      "https://www.reddit.com/r/artificial+MachineLearning+singularity+technology+ChatGPT+LocalLLaMA/top/.rss?t=day"),
    ],
    "markets_world": [
        ("CNBC",            "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("MarketWatch",     "https://feeds.marketwatch.com/marketwatch/topstories/"),
        ("Yahoo Finance",   "https://finance.yahoo.com/news/rssindex"),
        ("Reuters Business","https://feeds.reuters.com/reuters/businessNews"),
        ("BBC Business",    "https://feeds.bbci.co.uk/news/business/rss.xml"),
        ("Guardian Business","https://www.theguardian.com/uk/business/rss"),
        ("Axios",           "https://api.axios.com/feed/"),
        ("Reuters World",   "https://feeds.reuters.com/Reuters/worldNews"),
        ("BBC World",       "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("Foreign Policy",  "https://foreignpolicy.com/feed/"),
        ("Al Jazeera",      "https://www.aljazeera.com/xml/rss/all.xml"),
        ("NPR World",       "https://feeds.npr.org/1004/rss.xml"),
        ("Deutsche Welle",  "https://rss.dw.com/rdf/rss-en-world"),
        ("The Economist",   "https://www.economist.com/latest/rss.xml"),
    ],
    "trending": [
        ("Digiday",          "https://digiday.com/feed/"),
        ("Social Media Today","https://www.socialmediatoday.com/rss/"),
        ("Axios Media",      "https://api.axios.com/feed/media/"),
        ("Reddit Popular",   "https://www.reddit.com/r/popular/top/.rss?t=day"),
    ],
}


def build_nitter_feeds():
    working_instance = None
    for instance in NITTER_INSTANCES:
        try:
            r = requests.head(f"https://{instance}", timeout=5)
            if r.status_code < 500:
                working_instance = instance
                break
        except Exception:
            continue

    if not working_instance:
        print("  Warning: No working Nitter instance found, skipping Twitter feeds")
        return []

    return [(f"@{account}", f"https://{working_instance}/{account}/rss")
            for account in TWITTER_ACCOUNTS]


def is_recent(entry):
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                pub = datetime(*t[:6], tzinfo=timezone.utc)
                cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_FRESH)
                return pub >= cutoff
            except Exception:
                pass
    return True  # include if no date available


def normalize_title(title):
    return re.sub(r"[^a-z0-9]", "", title.lower())


def strip_html(text):
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", html.unescape(text)).strip()


def fetch_articles(extra_feeds=None):
    all_feeds = {cat: list(feeds) for cat, feeds in FEEDS.items()}
    if extra_feeds:
        all_feeds["trending"] = all_feeds["trending"] + extra_feeds

    seen_titles = set()
    articles_by_category = {}
    reddit_headers = {"User-Agent": "MorningDigest/1.0"}

    for category, feed_list in all_feeds.items():
        articles = []
        fresh_count = old_count = dup_count = 0

        for source_name, url in feed_list:
            try:
                kwargs = {}
                if "reddit.com" in url:
                    kwargs["request_headers"] = reddit_headers
                feed = feedparser.parse(url, **kwargs)

                count = 0
                for entry in feed.entries:
                    if count >= MAX_ARTICLES_PER_FEED:
                        break
                    if not is_recent(entry):
                        old_count += 1
                        continue

                    title = getattr(entry, "title", "").strip()
                    if not title:
                        continue

                    norm = normalize_title(title)
                    if norm in seen_titles:
                        dup_count += 1
                        continue
                    seen_titles.add(norm)

                    raw_summary = (getattr(entry, "summary", "")
                                   or getattr(entry, "description", ""))
                    summary = strip_html(raw_summary)
                    summary = summary[:150] if "reddit.com" in url else summary[:280]

                    articles.append(f"[{source_name}] {title}: {summary}")
                    fresh_count += 1
                    count += 1

            except Exception as e:
                print(f"  Warning: Failed to fetch {source_name}: {e}")

        print(f"  {category}: {fresh_count} fresh | {old_count} too old | {dup_count} duplicates")
        articles_by_category[category] = articles

    return articles_by_category


def build_prompt(articles_by_category):
    today = datetime.now().strftime("%A, %B %d, %Y")
    sections = []
    for category, articles in articles_by_category.items():
        if articles:
            sections.append(f"=== {category.upper()} ===\n" + "\n".join(articles))
    raw_content = "\n\n".join(sections)

    return f"""You are writing a morning news briefing in the style of Superhuman AI and The Rundown AI newsletters.

Today is {today}.

RAW ARTICLES:
{raw_content}

Write a morning digest following these rules:
- Only include genuinely newsworthy stories. No padding, no filler.
- Cover AI/tech stories comprehensively as the priority section.
- Each bullet: what happened + why it matters, in 1-2 sentences. Be specific.
- No repetition across sections. Consolidate overlapping stories into one bullet.
- Add "(via Source Name)" at the end of each bullet.
- For Reddit/X items, surface what people are actually discussing, not just the headline.
- Format each headline in **bold**: **Headline here**

Use EXACTLY these section headers (copy them verbatim):

## 🌅 Good Morning — {today} — [one sentence naming the single biggest story]
## 🤖 Tech & AI — ALL significant AI/tech stories, no fixed count
## 🌍 Markets & World — market moves, macro trends, geopolitics
## 🔥 Trending Today — viral topics from Reddit and X/Twitter

Each bullet format:
- **Bold headline.** Body explanation of what happened and why it matters. (via Source Name)
"""


def call_gemini(prompt):
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro"]
    last_error = None

    for model in models:
        try:
            response = client.models.generate_content(model=model, contents=prompt)
            return response.text
        except Exception as e:
            err = str(e).lower()
            if any(x in err for x in ["503", "unavailable", "429", "resource_exhausted"]):
                print(f"  Model {model} unavailable, trying next...")
                last_error = e
                continue
            raise

    raise RuntimeError(f"All Gemini models failed. Last error: {last_error}")


def parse_bullet(line):
    line = line.strip().lstrip("-•* ").strip()

    source = ""
    m = re.search(r"\(via ([^)]+)\)\s*$", line)
    if m:
        source = m.group(1)
        line = line[: m.start()].strip()

    headline = ""
    body = line
    m = re.match(r"\*\*(.+?)\*\*[.:]?\s*(.*)", line, re.DOTALL)
    if m:
        headline = m.group(1).strip().rstrip(".")
        body = m.group(2).strip()

    return headline, body, source


def render_section(label, emoji, bullets, bg, border, header_color):
    if not bullets:
        return ""

    rows = []
    for i, bullet in enumerate(bullets):
        headline, body, source = parse_bullet(bullet)
        divider = (
            ""
            if i == len(bullets) - 1
            else f'<tr><td colspan="2" style="padding:0 16px;"><div style="height:1px;background:#E8E3DD;"></div></td></tr>'
        )
        headline_html = (
            f'<div style="font-family:Georgia,serif;font-size:14px;font-weight:bold;'
            f'color:#2D2D2D;margin-bottom:4px;">{headline}</div>'
            if headline
            else ""
        )
        source_html = (
            f'<div style="font-family:Arial,sans-serif;font-size:11px;color:#A09890;'
            f'font-style:italic;margin-top:4px;">via {source}</div>'
            if source
            else ""
        )
        rows.append(
            f"""<tr>
          <td style="width:20px;padding:12px 6px 12px 16px;vertical-align:top;font-size:10px;color:#C4A882;">▶</td>
          <td style="padding:12px 16px 12px 6px;vertical-align:top;">
            {headline_html}
            <div style="font-family:Arial,sans-serif;font-size:13px;color:#555;line-height:1.5;">{body}</div>
            {source_html}
          </td>
        </tr>{divider}"""
        )

    return (
        f'<div style="margin-bottom:24px;background:{bg};border:1px solid {border};'
        f'border-radius:12px;overflow:hidden;">'
        f'<div style="padding:12px 16px;border-bottom:2px solid {border};">'
        f'<span style="font-family:Arial,sans-serif;font-size:15px;font-weight:bold;color:{header_color};">'
        f"{emoji} {label}</span></div>"
        f'<table style="width:100%;border-collapse:collapse;">{"".join(rows)}</table>'
        f"</div>"
    )


def build_html(digest_text):
    today = datetime.now().strftime("%A, %B %d, %Y")

    sections = {"good_morning": [], "tech_ai": [], "markets_world": [], "trending": []}
    current = None
    intro_text = ""

    for line in digest_text.splitlines():
        s = line.strip()
        if re.search(r"Good Morning", s, re.I):
            current = "good_morning"
            parts = s.split("—")
            if len(parts) >= 3:
                intro_text = parts[-1].strip()
            continue
        if re.search(r"Tech.*AI|AI.*Tech", s, re.I):
            current = "tech_ai"
            continue
        if re.search(r"Markets.*World|World.*Markets", s, re.I):
            current = "markets_world"
            continue
        if re.search(r"Trending", s, re.I):
            current = "trending"
            continue
        if current and re.match(r"^[-•*]", s) and s not in ("---", "***"):
            sections[current].append(s)

    good_morning_html = ""
    if intro_text:
        good_morning_html = (
            '<div style="margin-bottom:24px;background:#E8F5E9;border:1px solid #7CB99A;'
            'border-radius:12px;overflow:hidden;">'
            '<div style="padding:12px 16px;border-bottom:2px solid #7CB99A;">'
            '<span style="font-family:Arial,sans-serif;font-size:15px;font-weight:bold;color:#2E7D52;">'
            "🌅 Good Morning</span></div>"
            '<div style="padding:16px;">'
            f'<p style="font-family:Georgia,serif;font-size:14px;color:#2E7D52;'
            f'font-style:italic;margin:0;line-height:1.6;">{intro_text}</p>'
            "</div></div>"
        )

    tech_html    = render_section("Tech & AI",        "🤖", sections["tech_ai"],      "#FFF8F0", "#F4A261", "#C4622D")
    markets_html = render_section("Markets & World",  "🌍", sections["markets_world"],"#F0F7F4", "#A8D5B5", "#3A7D5A")
    trending_html= render_section("Trending Today",   "🔥", sections["trending"],     "#FFFBF0", "#F4A261", "#C4622D")

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#F7F4EF;font-family:Arial,sans-serif;">
  <div style="max-width:650px;margin:0 auto;padding:20px;">

    <div style="background:#E8F5E9;border-radius:12px;padding:24px;text-align:center;margin-bottom:24px;border:1px solid #7CB99A;">
      <div style="font-family:Arial,sans-serif;font-size:11px;font-variant:small-caps;letter-spacing:2px;color:#3A7D52;margin-bottom:8px;">MORNING DIGEST</div>
      <h1 style="font-family:Georgia,serif;font-size:28px;color:#1A3A2A;margin:0 0 8px 0;font-weight:normal;">Your Daily Brief</h1>
      <div style="font-family:Arial,sans-serif;font-size:13px;color:#5A8A6A;">{today}</div>
    </div>

    {good_morning_html}
    {tech_html}
    {markets_html}
    {trending_html}

    <div style="background:#EDE9E3;border-radius:12px;padding:16px;text-align:center;margin-top:8px;">
      <p style="font-family:Arial,sans-serif;font-size:11px;color:#8A8480;margin:0;line-height:1.6;">
        Generated by your morning digest bot &middot; Powered by Gemini &middot; Only news from the last 24 hours
      </p>
    </div>

  </div>
</body>
</html>"""


def send_email(html_body):
    gmail_address = os.getenv("GMAIL_ADDRESS")
    app_password = os.getenv("GMAIL_APP_PASSWORD")
    recipient = os.getenv("RECIPIENT_EMAIL")
    today = datetime.now().strftime("%B %d, %Y")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Morning Digest — {today}"
    msg["From"] = gmail_address
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_address, app_password)
        server.sendmail(gmail_address, recipient, msg.as_string())

    return recipient


def main():
    print("[1/4] Fetching articles...")
    nitter_feeds = build_nitter_feeds()
    articles_by_category = fetch_articles(extra_feeds=nitter_feeds)

    total = sum(len(a) for a in articles_by_category.values())
    if total == 0:
        print("No articles found. Exiting.")
        return
    print(f"  Total: {total} articles")

    print("[2/4] Calling Gemini...")
    prompt = build_prompt(articles_by_category)
    digest_text = call_gemini(prompt)

    print("[3/4] Building HTML email...")
    html_body = build_html(digest_text)

    print("[4/4] Sending email...")
    recipient = send_email(html_body)

    print(f"Done! Digest sent to {recipient}")


if __name__ == "__main__":
    main()

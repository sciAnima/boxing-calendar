# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")

import re
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from ics import Calendar, Event
from ics.grammar.parse import ContentLine

URL = "https://www.boxingscene.com/schedule"
BASE = "https://www.boxingscene.com"

CT_ZONE = ZoneInfo("America/Chicago")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

# Matches: "Fri, Jun 5, 2026 - 5:30 PM EST"
# Groups:   weekday  month  day  year   hour  min  ampm  tz
DATETIME_RE = re.compile(
    r"\w+,\s+(\w+)\s+(\d{1,2}),\s+(\d{4})\s+-\s+(\d{1,2}):(\d{2})\s+(AM|PM)\s+(\w+)",
    re.I
)

TZ_MAP = {
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "ET":  "America/New_York",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "CT":  "America/Chicago",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "GMT": "Europe/London",
    "BST": "Europe/London",
    "CET": "Europe/Berlin",
    "JST": "Asia/Tokyo",
}

MONTH_MAP = {
    "Jan": "January", "Feb": "February", "Mar": "March",
    "Apr": "April",   "May": "May",      "Jun": "June",
    "Jul": "July",    "Aug": "August",   "Sep": "September",
    "Oct": "October", "Nov": "November", "Dec": "December",
    # Full names pass through fine too
    "January": "January", "February": "February", "March": "March",
    "April": "April", "June": "June", "July": "July",
    "August": "August", "September": "September", "October": "October",
    "November": "November", "December": "December",
}


def fetch_html() -> str:
    headers = {
        "User-Agent": USER_AGENTS[datetime.now().day % len(USER_AGENTS)],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://www.google.com/",
    }
    session = requests.Session()
    session.headers.update(headers)
    resp = session.get(URL, timeout=30)
    print(f"HTTP {resp.status_code}")
    resp.raise_for_status()
    return resp.text


def parse_datetime(text: str) -> datetime | None:
    """Parse 'Fri, Jun 5, 2026 - 5:30 PM EST' into a CT datetime."""
    m = DATETIME_RE.search(text)
    if not m:
        return None

    month_abbr, day, year, hour, minute, ampm, tz_abbr = m.groups()
    month = MONTH_MAP.get(month_abbr.capitalize(), month_abbr)
    tz_name = TZ_MAP.get(tz_abbr.upper(), "America/New_York")  # default ET

    try:
        dt = datetime.strptime(
            f"{month} {day} {year} {hour}:{minute} {ampm.upper()}",
            "%B %d %Y %I:%M %p"
        )
        return dt.replace(tzinfo=ZoneInfo(tz_name)).astimezone(CT_ZONE)
    except ValueError:
        return None


def parse_schedule(html: str) -> list[Event]:
    soup = BeautifulSoup(html, "html.parser")
    events: list[Event] = []
    seen_uids = set()

    # Every event on BoxingScene's schedule page is an <a> tag linking to
    # /events/fight-slug. The link text contains: fight name + date/time + venue + network
    for a in soup.find_all("a", href=re.compile(r"/events/")):
        text = a.get_text(separator=" | ").strip()

        # Must contain a vs. fight pattern
        if not re.search(r"\bvs\.?\b", text, re.I):
            continue

        # Must contain a parseable date
        if not DATETIME_RE.search(text):
            continue

        # Split out the parts by pipe
        parts = [p.strip() for p in text.split("|") if p.strip()]
        if not parts:
            continue

        # Fight name is always first
        fight_name = parts[0].strip()

        # Find the date/time part
        datetime_str = ""
        venue = ""
        network = ""

        for part in parts[1:]:
            if DATETIME_RE.search(part) and not datetime_str:
                datetime_str = part
            elif any(kw in part for kw in ["DAZN","ESPN","Netflix","Prime","HBO","Showtime",
                                            "Paramount","PPV","Sky","TNT","BBC","TrillerTV",
                                            "ProBoxTV","YouTube","TBA"]):
                network = part
            elif part and not venue and len(part) > 5:
                venue = part

        # Parse datetime
        start_ct = parse_datetime(datetime_str)
        if not start_ct:
            continue

        end_ct    = start_ct + timedelta(hours=3)
        start_utc = start_ct.astimezone(timezone.utc)
        end_utc   = end_ct.astimezone(timezone.utc)

        event_url = BASE + a["href"] if a.get("href", "").startswith("/") else a.get("href", "")

        # Deduplicate by fight name + date
        slug     = re.sub(r"[^a-z0-9]+", "-", fight_name.lower()).strip("-")
        uid_date = start_ct.strftime("%Y%m%d")
        uid      = f"{uid_date}-{slug}@boxingscene-calendar"
        if uid in seen_uids:
            continue
        seen_uids.add(uid)

        ev = Event()
        ev.name  = fight_name
        ev.begin = start_utc
        ev.end   = end_utc
        ev.description = "\n".join(filter(None, [
            f"Date/Time: {datetime_str.strip()}",
            f"Venue: {venue}" if venue else "",
            f"Network: {network}" if network else "",
            f"More info: {event_url}" if event_url else "",
            "",
            "Source: BoxingScene.com",
        ]))
        ev.uid = uid

        events.append(ev)
        print(f"  + {uid_date} | {fight_name} | {datetime_str.strip()} | {network}")

    return events


def main():
    print("Fetching BoxingScene schedule...")
    html = fetch_html()
    print(f"[DEBUG] Fetched {len(html)} chars")

    print("Parsing events...")
    events = parse_schedule(html)

    if not events:
        print("\n[DEBUG] No events found — check HTML structure")

    cal = Calendar()
    cal.extra.append(ContentLine(name="CALSCALE", value="GREGORIAN"))
    cal.extra.append(ContentLine(name="COMMENT", value="Event data sourced from BoxingScene.com"))
    for ev in events:
        cal.events.add(ev)

    output = "boxing_schedule.ics"
    with open(output, "w", encoding="utf-8") as f:
        f.writelines(cal)

    print(f"\nDone — {len(events)} events written to {output}")


if __name__ == "__main__":
    main()

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

URL = "https://www.boxingnews24.com/boxing-schedule/"

CT_ZONE = ZoneInfo("America/Chicago")
ET_ZONE = ZoneInfo("America/New_York")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}

# Matches: "March 14: Dublin, Ireland | Local: 6:00 PM | USA ET: 2:00 PM | UK London: 6:00 PM | live on DAZN"
HEADER_RE = re.compile(
    r"^(\w+)\s+(\d{1,2}):\s*(.+?)(?:\|\s*Local[^|]*)?(?:\|\s*USA ET:\s*(\d{1,2}:\d{2}\s*(?:AM|PM)))?.*?(?:\|\s*live on\s*(.+))?$",
    re.I
)

# Simpler ET time extractor
ET_RE   = re.compile(r"USA ET:\s*(\d{1,2}:\d{2}\s*(?:AM|PM))", re.I)
NET_RE  = re.compile(r"live on\s*(.+)", re.I)
FIGHT_RE = re.compile(r"^📌\s*(.+)", re.I)


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


def parse_et_time(time_str: str, date_obj: datetime) -> datetime | None:
    """Convert an ET time string like '2:00 PM' to a CT datetime."""
    time_str = re.sub(r"\s+", "", time_str).upper()  # "2:00PM"
    try:
        dt = datetime.strptime(
            f"{date_obj.month}/{date_obj.day}/{date_obj.year} {time_str}",
            "%m/%d/%Y %I:%M%p"
        )
        return dt.replace(tzinfo=ET_ZONE).astimezone(CT_ZONE)
    except ValueError:
        return None


def strip_emoji(s: str) -> str:
    return re.sub(r"[^\x20-\x7EÀ-ÖØ-öø-ÿ''\-\.,/|:📌 ]", "", s).strip()


def parse_schedule(html: str) -> list[Event]:
    soup = BeautifulSoup(html, "html.parser")

    # The schedule is inside the main post content
    content = soup.find("div", class_=re.compile(r"entry|post|content|article", re.I))
    if not content:
        print("WARNING: Could not isolate content div, using full page")
        content = soup

    # Replace <a> tags with plain text
    for a in content.find_all("a"):
        a.replace_with(a.get_text())

    lines = content.get_text(separator="\n").split("\n")
    current_year = datetime.now(CT_ZONE).year

    events  = []
    seen    = set()

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # --- Detect a card header line ---
        # Format: "March 14: Dublin, Ireland | ... | USA ET: 2:00 PM | ... | live on DAZN"
        month_match = re.match(
            r"^(" + "|".join(MONTHS.keys()) + r")\s+(\d{1,2}):\s*(.+)",
            line, re.I
        )

        if month_match:
            month_name = month_match.group(1).capitalize()
            day        = int(month_match.group(2))
            rest       = month_match.group(3)

            month_num  = MONTHS.get(month_name)
            if not month_num:
                i += 1
                continue

            date_obj = datetime(current_year, month_num, day)

            # Extract location (everything before first |)
            parts    = rest.split("|")
            location = strip_emoji(parts[0]).strip()

            # Extract ET time
            et_time_str = None
            et_m = ET_RE.search(rest)
            if et_m:
                et_time_str = et_m.group(1).strip()

            # Extract network
            network = ""
            net_m = NET_RE.search(rest)
            if net_m:
                network = strip_emoji(net_m.group(1)).strip()

            # --- Collect fight lines that follow ---
            fight_lines = []
            i += 1
            while i < len(lines):
                next_line = lines[i].strip()
                # Next card header = stop
                if re.match(r"^(" + "|".join(MONTHS.keys()) + r")\s+\d{1,2}:", next_line, re.I):
                    break
                # Fight bullet
                if next_line.startswith("📌"):
                    fight_text = next_line[1:].strip()  # remove 📌
                    fight_text = strip_emoji(fight_text).strip()
                    if fight_text:
                        fight_lines.append(fight_text)
                i += 1

            if not fight_lines:
                continue

            # Main event = first fight listed
            main_event = fight_lines[0]
            # Clean up: remove round count suffix for event name
            main_name = re.sub(r",\s*\d+\s*rounds.*$", "", main_event, flags=re.I).strip()

            # Build start time
            start_ct = None
            if et_time_str:
                start_ct = parse_et_time(et_time_str, date_obj)
            if not start_ct:
                start_ct = datetime(
                    date_obj.year, date_obj.month, date_obj.day,
                    21, 0, tzinfo=CT_ZONE
                )

            # 5 hours covers prelims + main event
            end_ct    = start_ct + timedelta(hours=5)
            start_utc = start_ct.astimezone(timezone.utc)
            end_utc   = end_ct.astimezone(timezone.utc)

            # Deduplicate
            slug     = re.sub(r"[^a-z0-9]+", "-", main_name.lower()).strip("-")
            uid_date = date_obj.strftime("%Y%m%d")
            uid      = f"{uid_date}-{slug}@boxingnews24-calendar"
            if uid in seen:
                continue
            seen.add(uid)

            undercard_str = "\n".join(f"  • {f}" for f in fight_lines[1:])

            ev = Event()
            ev.name  = main_name
            ev.begin = start_utc
            ev.end   = end_utc
            ev.description = "\n".join(filter(None, [
                f"Date: {date_obj.strftime('%A, %B %d, %Y')}",
                f"Location: {location}",
                f"Network: {network}" if network else "",
                f"Start: {start_ct.strftime('%I:%M %p CT')}",
                "",
                f"Main Event: {main_event}",
                "",
                "Undercard:" if fight_lines[1:] else "",
                undercard_str,
                "",
                "Source: BoxingNews24.com",
            ]))
            ev.uid = uid

            events.append(ev)
            time_label = start_ct.strftime("%I:%M %p CT")
            print(f"  + {uid_date} | {main_name} | {location} | {time_label} | {network or 'TBC'}")

        else:
            i += 1

    return events


def main():
    print("Fetching BoxingNews24 schedule...")
    html = fetch_html()
    print(f"[DEBUG] Fetched {len(html)} chars")

    print("Parsing events...")
    events = parse_schedule(html)

    if not events:
        print("\n[DEBUG] No events found — check HTML structure")

    cal = Calendar()
    cal.extra.append(ContentLine(name="CALSCALE", value="GREGORIAN"))
    cal.extra.append(ContentLine(name="COMMENT", value="Event data sourced from BoxingNews24.com"))
    for ev in events:
        cal.events.add(ev)

    output = "boxing_schedule.ics"
    with open(output, "w", encoding="utf-8") as f:
        f.writelines(cal)

    print(f"\nDone — {len(events)} events written to {output}")


if __name__ == "__main__":
    main()

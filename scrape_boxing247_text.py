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

URL = "https://www.boxing247.com/fight-schedule"

LOCATION_TIMEZONES = {
    "USA": "America/New_York",
    "United States": "America/New_York",
    "England": "Europe/London",
    "UK": "Europe/London",
    "Scotland": "Europe/London",
    "Wales": "Europe/London",
    "Ireland": "Europe/Dublin",
    "Northern Ireland": "Europe/London",
    "Mexico": "America/Mexico_City",
    "Australia": "Australia/Brisbane",
    "Puerto Rico": "America/Puerto_Rico",
    "Germany": "Europe/Berlin",
    "Denmark": "Europe/Copenhagen",
    "Japan": "Asia/Tokyo",
    "United Arab Emirates": "Asia/Dubai",
    "Saudi Arabia": "Asia/Riyadh",
    "Canada": "America/Toronto",
    "France": "Europe/Paris",
    "Spain": "Europe/Madrid",
    "Italy": "Europe/Rome",
    "Greece": "Europe/Athens",
    "Egypt": "Africa/Cairo",
    "Argentina": "America/Argentina/Buenos_Aires",
    "South Africa": "Africa/Johannesburg",
}

CT_ZONE = ZoneInfo("America/Chicago")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

MONTHS = (
    "January|February|March|April|May|June|"
    "July|August|September|October|November|December"
)

# Matches: "March 14: Dublin, Ireland"  (after emoji/whitespace stripped)
HEADER_DATE_RE = re.compile(
    rf"({MONTHS})\s+(\d{{1,2}})\s*:\s*(.+)"
)


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


def strip_emoji(s: str) -> str:
    """Remove emoji/flags, keep ASCII + accented latin."""
    return re.sub(r"[^\x20-\x7EÀ-ÖØ-öø-ÿ''\-\.,/|:()\s]", "", s).strip()


def infer_tz(location: str) -> ZoneInfo | None:
    if not location:
        return None
    for key, tz in LOCATION_TIMEZONES.items():
        if key.lower() in location.lower():
            return ZoneInfo(tz)
    return None


def extract_time(info: str, date_str: str, location: str) -> datetime | None:
    """Parse start time from broadcast info string, return as CT datetime."""
    for pat, tz_name in [
        (r"(\d{1,2}:\d{2}\s*(?:AM|PM)).*?ET", "America/New_York"),
        (r"(\d{1,2}:\d{2}\s*(?:AM|PM)).*?UK", "Europe/London"),
    ]:
        m = re.search(pat, info, re.I)
        if m:
            try:
                time_str = re.sub(r"\s+", "", m.group(1)).upper()
                dt = datetime.strptime(f"{date_str} {time_str}", "%B %d, %Y %I:%M%p")
                return dt.replace(tzinfo=ZoneInfo(tz_name)).astimezone(CT_ZONE)
            except ValueError:
                continue

    m = re.search(r"(\d{1,2}:\d{2}\s*(?:AM|PM)).*?Local", info, re.I)
    if m:
        tz = infer_tz(location)
        if tz:
            try:
                time_str = re.sub(r"\s+", "", m.group(1)).upper()
                dt = datetime.strptime(f"{date_str} {time_str}", "%B %d, %Y %I:%M%p")
                return dt.replace(tzinfo=tz).astimezone(CT_ZONE)
            except ValueError:
                pass

    return None


def parse_schedule(html: str) -> list[Event]:
    """Parse using HTML structure (<strong> headers + <li> fight lines)
    instead of plain text — immune to line-splitting and whitespace quirks."""

    soup = BeautifulSoup(html, "html.parser")

    # Find the main content div
    content = soup.find("div", class_=re.compile(r"entry|post|content|article", re.I))
    if not content:
        print("WARNING: Could not isolate content div, using full page")
        content = soup

    current_year = datetime.now(CT_ZONE).year
    events: list[Event] = []

    # Every card header is a <strong> tag inside a <p> tag.
    # We walk all <strong> tags, check if they look like a card header,
    # then collect the <li> siblings that follow until the next <strong>.
    for strong in content.find_all("strong"):
        # Get full text of the <strong> tag, stripping emoji/flags
        raw_header = strong.get_text(separator=" ")
        header = strip_emoji(raw_header).strip()

        m = HEADER_DATE_RE.match(header)
        if not m:
            continue

        month    = m.group(1)
        day      = m.group(2)
        rest     = m.group(3).strip()  # "Dublin, Ireland (Live on DAZN | 7:00 PM UK...)"
        date_str = f"{month} {day}, {current_year}"

        # Split location from broadcast info
        if "(" in rest:
            location = rest[:rest.index("(")].strip(" ,")
            info     = strip_emoji(rest[rest.index("(")+1:rest.rfind(")")]).strip()
        else:
            location = rest.strip()
            info     = ""

        location = strip_emoji(location).strip()

        # Find fight <li> items — walk siblings of the <strong>'s parent <p>
        fight_lines = []
        parent = strong.find_parent(["p", "div"])
        if parent:
            sibling = parent.find_next_sibling()
            while sibling:
                tag = sibling.name
                # <ul> contains the fight bullets
                if tag == "ul":
                    for li in sibling.find_all("li"):
                        text = strip_emoji(li.get_text(separator=" ")).strip()
                        if re.search(r"\bversus\b|\bvs\.?\b", text, re.I):
                            fight_lines.append(text)
                # <hr> or next <p> with a <strong> date = end of card
                elif tag == "hr":
                    break
                elif tag == "p" and sibling.find("strong"):
                    sib_text = strip_emoji(sibling.get_text()).strip()
                    if HEADER_DATE_RE.match(sib_text):
                        break
                sibling = sibling.find_next_sibling()

        # --- Start time ---
        start_ct = extract_time(info, date_str, location) if info else None
        if not start_ct:
            base = datetime.strptime(date_str, "%B %d, %Y")
            start_ct = datetime(base.year, base.month, base.day, 21, 0, tzinfo=CT_ZONE)

        end_ct    = start_ct + timedelta(hours=3)
        start_utc = start_ct.astimezone(timezone.utc)
        end_utc   = end_ct.astimezone(timezone.utc)

        # --- Main event = first fight listed ---
        if fight_lines:
            fl = fight_lines[0]
            vm = re.search(
                r"(.+?)\s+(?:versus|vs\.?)\s+(.+?)(?:,\s*\d|$)", fl, re.I
            )
            main_event = (
                f"{vm.group(1).strip()} versus {vm.group(2).strip()}"
                if vm else fl[:120]
            )
        else:
            main_event = f"Boxing - {location}"

        undercard = "\n".join(fight_lines)

        ev = Event()
        ev.name        = main_event
        ev.begin       = start_utc
        ev.end         = end_utc
        ev.description = "\n".join(filter(None, [
            f"Date: {date_str}",
            f"Location: {location}",
            f"Broadcast: {info}" if info else "",
            "",
            undercard,
            "",
            "Source: Boxing247.com",
        ]))

        slug     = re.sub(r"[^a-z0-9]+", "-", f"{location} {main_event}".lower()).strip("-")
        uid_date = datetime.strptime(date_str, "%B %d, %Y").strftime("%Y%m%d")
        ev.uid   = f"{uid_date}-{slug}@boxing247-calendar"

        events.append(ev)
        print(f"  + {date_str} | {location} | {main_event}")

    return events


def main():
    print("Fetching Boxing247 schedule...")
    html = fetch_html()
    print(f"[DEBUG] Fetched {len(html)} chars")

    print("Parsing events...")
    events = parse_schedule(html)

    if not events:
        print("\n[DEBUG] No events found — check HTML structure")

    cal = Calendar()
    cal.extra.append(ContentLine(name="CALSCALE", value="GREGORIAN"))
    cal.extra.append(ContentLine(name="COMMENT", value="Event data sourced from Boxing247.com"))
    for ev in events:
        cal.events.add(ev)

    output = "boxing_schedule.ics"
    with open(output, "w", encoding="utf-8") as f:
        f.writelines(cal)

    print(f"\nDone — {len(events)} events written to {output}")


if __name__ == "__main__":
    main()

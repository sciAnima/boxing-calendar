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

# Matches header lines like:
# 📅 March 8: Las Vegas, Nevada (Live on DAZN | 8:00 PM ET / 1:00 AM UK)
CARD_HEADER_RE = re.compile(
    rf"""
    (?:📅\s*)?                       # optional calendar emoji
    ({MONTHS})\s+(\d{{1,2}})         # group1=month  group2=day
    \s*:\s*
    ([^(\n]+?)                       # group3=location (before paren)
    \s*
    (?:\(([^)]*)\))?                 # group4=optional broadcast info in parens
    \s*$
    """,
    re.VERBOSE | re.MULTILINE,
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
                time_str = re.sub(r"\s+", "", m.group(1)).upper()  # "8:00PM"
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


def strip_emoji(s: str) -> str:
    """Remove emoji and flag characters, keep readable ASCII + accented latin."""
    return re.sub(r"[^\x20-\x7EÀ-ÖØ-öø-ÿ''\-\.,/|: ]", "", s).strip()


def parse_schedule(html: str) -> list[Event]:
    soup = BeautifulSoup(html, "html.parser")

    # Target the main post content to avoid nav/sidebar noise
    content = soup.find("div", class_=re.compile(r"entry|post|content|article", re.I))
    if not content:
        print("WARNING: Could not isolate content div, using full page")
        content = soup

    # Replace <a> tags with their text so hyperlinked words like "DAZN"
    # don't break the broadcast info paren matching
    for a in content.find_all("a"):
        a.replace_with(a.get_text())

    lines = content.get_text(separator="\n").split("\n")
    current_year = datetime.now(CT_ZONE).year
    events: list[Event] = []

    i = 0
    while i < len(lines):
        raw_line = lines[i].strip()
        # Strip emoji before trying to match
        line = strip_emoji(raw_line)
        m = CARD_HEADER_RE.match(line)

        if m:
            month    = m.group(1)
            day      = m.group(2)
            location = strip_emoji(m.group(3)).strip()
            info     = strip_emoji(m.group(4) or "").strip()
            date_str = f"{month} {day}, {current_year}"

            # Collect fight bullet lines that follow this header
            fight_lines = []
            i += 1
            while i < len(lines):
                next_raw = lines[i].strip()
                next_line = strip_emoji(next_raw)
                # Stop at next card header or horizontal rule
                if CARD_HEADER_RE.match(next_line) or re.match(r"^-{3,}$", next_line):
                    break
                if re.search(r"\bversus\b|\bvs\.?\b", next_line, re.I):
                    fight_lines.append(next_line)
                i += 1

            # --- Start time ---
            start_ct = extract_time(info, date_str, location) if info else None
            if not start_ct:
                base = datetime.strptime(date_str, "%B %d, %Y")
                start_ct = datetime(base.year, base.month, base.day, 21, 0, tzinfo=CT_ZONE)

            end_ct    = start_ct + timedelta(hours=3)
            start_utc = start_ct.astimezone(timezone.utc)
            end_utc   = end_ct.astimezone(timezone.utc)

            # --- Main event name: first fight listed ---
            if fight_lines:
                fl = re.sub(r"^\s*[*\-•]\s*", "", fight_lines[0])
                vm = re.search(
                    r"(.+?)\s+(?:versus|vs\.?)\s+(.+?)(?:,\s*\d|$)", fl, re.I
                )
                main_event = (
                    f"{vm.group(1).strip()} versus {vm.group(2).strip()}"
                    if vm else fl[:120]
                )
            else:
                main_event = f"Boxing – {location}"

            undercard = "\n".join(
                re.sub(r"^\s*[*\-•]\s*", "", fl) for fl in fight_lines
            )

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

        else:
            i += 1

    return events


def main():
    print("Fetching Boxing247 schedule...")
    html = fetch_html()

    # DEBUG: save raw response so we can see what the site actually returned
    with open("debug_raw.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[DEBUG] Saved raw HTML ({len(html)} chars) to debug_raw.html")
    print("[DEBUG] First 1000 chars of response:")
    print(html[:1000])

    print("Parsing events...")
    events = parse_schedule(html)

    if not events:
        print("\n[DEBUG] No events found. First 3000 chars of page text:")
        soup = BeautifulSoup(html, "html.parser")
        print(soup.get_text(separator="\n")[:3000])

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

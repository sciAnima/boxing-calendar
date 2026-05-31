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

URL = "https://fightnights.com/upcoming-boxing-schedule"

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
    "Russia": "Europe/Moscow",
}

CT_ZONE = ZoneInfo("America/Chicago")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

# Abbreviated month -> full month name
MONTH_ABBR = {
    "Jan": "January", "Feb": "February", "Mar": "March",
    "Apr": "April",   "May": "May",      "Jun": "June",
    "Jul": "July",    "Aug": "August",   "Sep": "September",
    "Oct": "October", "Nov": "November", "Dec": "December",
}

TIME_RE = re.compile(r"(\d{1,2}(?::\d{2})?)\s*(am|pm)\s*(ET|CT|PT|MT|UK|GMT|CET|Local)?", re.I)
DATE_RE = re.compile(r"(\w{3}),\s+(\w{3})\s+(\d{1,2})\s+(\d{4})")


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


def expand_month(abbr: str) -> str:
    """Convert abbreviated month (Jun) to full name (June)."""
    return MONTH_ABBR.get(abbr.capitalize(), abbr)


def parse_time(time_str: str, date_str: str, location: str) -> datetime | None:
    """Parse a time string like '7pm ET' into a CT datetime."""
    if not time_str or time_str.strip().upper() in ("TBA", "CHECK LOCAL LISTINGS", ""):
        return None

    m = TIME_RE.search(time_str)
    if not m:
        return None

    raw_time = m.group(1)
    ampm     = m.group(2)
    tz_hint  = m.group(3)

    formatted = f"{raw_time}:00{ampm.upper()}" if ":" not in raw_time else f"{raw_time}{ampm.upper()}"

    tz_map = {
        "ET":  "America/New_York",
        "CT":  "America/Chicago",
        "MT":  "America/Denver",
        "PT":  "America/Los_Angeles",
        "UK":  "Europe/London",
        "GMT": "Europe/London",
        "CET": "Europe/Berlin",
    }

    tz_name = tz_map.get((tz_hint or "").upper())
    if not tz_name:
        tz_obj = infer_tz(location)
        if not tz_obj:
            return None
        tz_name = str(tz_obj)

    try:
        dt = datetime.strptime(f"{date_str} {formatted}", "%B %d %Y %I:%M%p")
        return dt.replace(tzinfo=ZoneInfo(tz_name)).astimezone(CT_ZONE)
    except ValueError:
        return None


def parse_schedule(html: str) -> list[Event]:
    soup = BeautifulSoup(html, "html.parser")
    events: list[Event] = []

    for li in soup.find_all("li"):
        h2 = li.find("h2")
        if not h2:
            continue

        fight_name = h2.get_text(separator=" ").strip()
        if not re.search(r"\bvs\.?\b", fight_name, re.I):
            continue

        full_text = li.get_text(separator="|").strip()
        parts = [p.strip() for p in full_text.split("|") if p.strip()]

        # --- Date: expand abbreviated month before parsing ---
        date_str = None
        for part in parts:
            dm = DATE_RE.search(part)
            if dm:
                month_full = expand_month(dm.group(2))  # "Jun" -> "June"
                day        = dm.group(3)
                year       = dm.group(4)
                date_str   = f"{month_full} {day} {year}"
                break

        if not date_str:
            continue

        # --- Venue ---
        venue = ""
        for part in parts:
            if (fight_name in part or
                DATE_RE.search(part) or
                "View Fight" in part or
                TIME_RE.search(part)):
                continue
            if len(part) > len(venue):
                venue = part
        venue = venue.strip()

        # --- Time ---
        time_str = ""
        for part in parts:
            if TIME_RE.search(part):
                time_str = part
                break

        # --- Network ---
        network = ""
        for part in reversed(parts):
            if (part and
                part != venue and
                fight_name not in part and
                not DATE_RE.search(part) and
                not TIME_RE.search(part) and
                "View Fight" not in part and
                len(part) < 60):
                network = part
                break

        # --- Start time ---
        start_ct = parse_time(time_str, date_str, venue)
        if not start_ct:
            base = datetime.strptime(date_str, "%B %d %Y")
            start_ct = datetime(base.year, base.month, base.day, 21, 0, tzinfo=CT_ZONE)

        end_ct    = start_ct + timedelta(hours=3)
        start_utc = start_ct.astimezone(timezone.utc)
        end_utc   = end_ct.astimezone(timezone.utc)

        link      = li.find("a", href=re.compile(r"/event-"))
        event_url = f"https://fightnights.com{link['href']}" if link else ""

        ev = Event()
        ev.name  = fight_name
        ev.begin = start_utc
        ev.end   = end_utc
        ev.description = "\n".join(filter(None, [
            f"Date: {date_str}",
            f"Venue: {venue}",
            f"Time: {time_str}" if time_str else "",
            f"Network: {network}" if network else "",
            f"More info: {event_url}" if event_url else "",
            "",
            "Source: FightNights.com",
        ]))

        slug     = re.sub(r"[^a-z0-9]+", "-", fight_name.lower()).strip("-")
        uid_date = datetime.strptime(date_str, "%B %d %Y").strftime("%Y%m%d")
        ev.uid   = f"{uid_date}-{slug}@fightnights-calendar"

        events.append(ev)
        print(f"  + {date_str} | {fight_name} | {venue} | {time_str}")

    return events


def main():
    print("Fetching FightNights schedule...")
    html = fetch_html()
    print(f"[DEBUG] Fetched {len(html)} chars")

    print("Parsing events...")
    events = parse_schedule(html)

    if not events:
        print("\n[DEBUG] No events found — check HTML structure")

    cal = Calendar()
    cal.extra.append(ContentLine(name="CALSCALE", value="GREGORIAN"))
    cal.extra.append(ContentLine(name="COMMENT", value="Event data sourced from FightNights.com"))
    for ev in events:
        cal.events.add(ev)

    output = "boxing_schedule.ics"
    with open(output, "w", encoding="utf-8") as f:
        f.writelines(cal)

    print(f"\nDone — {len(events)} events written to {output}")


if __name__ == "__main__":
    main()

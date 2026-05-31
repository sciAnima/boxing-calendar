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

SCHEDULE_URL = "https://box.live/upcoming-fights-schedule/"
TV_URL       = "https://box.live/us-boxing-tv-schedule/"

CT_ZONE = ZoneInfo("America/Chicago")
ET_ZONE = ZoneInfo("America/New_York")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

# Matches "Saturday, 10 January 2026" or "Friday, 23 January 2026"
FULL_DATE_RE = re.compile(
    r"\w+,\s+(\d{1,2})\s+(\w+)\s+(\d{4})"
)

# Matches "20:00 EST" or "17:00 PST" — we only want EST
EST_TIME_RE = re.compile(r"(\d{2}):(\d{2})\s+EST")

MONTH_MAP = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}


def fetch(url: str) -> str:
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
    resp = session.get(url, timeout=30)
    print(f"  HTTP {resp.status_code} — {url}")
    resp.raise_for_status()
    return resp.text


def make_slug(fight: str) -> str:
    """Normalize a fight string to a match key: 'Lopez vs Stevenson' -> 'lopez-stevenson'"""
    fight = re.sub(r"\s+vs\.?\s+", "-", fight, flags=re.I)
    fight = re.sub(r"[^a-z0-9\-]", "", fight.lower())
    return fight.strip("-")


def parse_date(date_str: str) -> datetime | None:
    """Parse 'Saturday, 10 January 2026' -> date object."""
    m = FULL_DATE_RE.search(date_str)
    if not m:
        return None
    day, month_name, year = m.group(1), m.group(2), m.group(3)
    month = MONTH_MAP.get(month_name)
    if not month:
        return None
    try:
        return datetime(int(year), month, int(day))
    except ValueError:
        return None


# ── Page 1: Full schedule table ───────────────────────────────────────────────

def parse_schedule_page(html: str) -> list[dict]:
    """
    Parse the schedule table from /upcoming-fights-schedule/
    Returns list of dicts: {slug, fight, date_obj, venue, undercard}
    """
    soup = BeautifulSoup(html, "html.parser")
    events = []

    # The clean data lives in the table at the bottom of the page
    table = soup.find("table")
    if not table:
        print("WARNING: No table found on schedule page")
        return events

    current_date = None
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells or len(cells) < 2:
            continue

        # Skip header row
        if cells[0].find("th") or cells[0].name == "th":
            continue

        cell_texts = [c.get_text(separator=" ").strip() for c in cells]

        # Column 0: Date
        date_text = cell_texts[0]
        if date_text:
            parsed = parse_date(date_text)
            if parsed:
                current_date = parsed

        if not current_date:
            continue

        # Column 1: Fight (e.g. "Walsh vs Ocampo")
        fight = cell_texts[1] if len(cell_texts) > 1 else ""
        if not fight or not re.search(r"\bvs\.?\b", fight, re.I):
            continue

        # Column 2: Venue
        venue = cell_texts[2] if len(cell_texts) > 2 else ""

        # Column 3+: Undercard fights (bullet list)
        undercard_cell = cells[3] if len(cells) > 3 else None
        undercard = []
        if undercard_cell:
            for li in undercard_cell.find_all("li"):
                txt = li.get_text(separator=" ").strip()
                if txt:
                    undercard.append(txt)

        events.append({
            "slug":      make_slug(fight),
            "fight":     fight,
            "date_obj":  current_date,
            "venue":     venue,
            "undercard": undercard,
        })

    print(f"  Schedule page: {len(events)} events parsed")
    return events


# ── Page 2: US TV table ───────────────────────────────────────────────────────

def parse_tv_page(html: str) -> dict[str, dict]:
    """
    Parse the US TV table from /us-boxing-tv-schedule/
    Returns dict keyed by slug: {network, start_est_h, start_est_m, date_obj}
    """
    soup = BeautifulSoup(html, "html.parser")
    tv_data = {}

    table = soup.find("table")
    if not table:
        print("WARNING: No table found on TV page")
        return tv_data

    current_date = None
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells or len(cells) < 3:
            continue

        if cells[0].name == "th":
            continue

        cell_texts = [c.get_text(separator=" ").strip() for c in cells]

        # Column 0: Date
        date_text = cell_texts[0]
        if date_text:
            parsed = parse_date(date_text)
            if parsed:
                current_date = parsed

        if not current_date:
            continue

        # Column 1: Fight slug
        fight = cell_texts[1] if len(cell_texts) > 1 else ""
        if not fight or not re.search(r"\bvs\.?\b", fight, re.I):
            continue

        # Column 2: Network
        network = cell_texts[2] if len(cell_texts) > 2 else ""

        # Column 3: Start time e.g. "20:00 EST / 17:00 PST"
        time_text = cell_texts[3] if len(cell_texts) > 3 else ""
        tm = EST_TIME_RE.search(time_text)
        start_h = int(tm.group(1)) if tm else None
        start_m = int(tm.group(2)) if tm else None

        slug = make_slug(fight)
        tv_data[slug] = {
            "network":    network,
            "start_est_h": start_h,
            "start_est_m": start_m,
            "date_obj":   current_date,
        }

    print(f"  TV page: {len(tv_data)} entries parsed")
    return tv_data


# ── Merge + build calendar ────────────────────────────────────────────────────

def build_events(schedule: list[dict], tv: dict[str, dict]) -> list[Event]:
    events = []
    seen = set()

    for item in schedule:
        slug     = item["slug"]
        fight    = item["fight"]
        date_obj = item["date_obj"]
        venue    = item["venue"]
        undercard = item["undercard"]

        # Look up TV data — try exact slug first, then partial match
        tv_info = tv.get(slug)
        if not tv_info:
            # Partial match: check if any TV slug shares both fighter names
            parts = slug.split("-")
            for tv_slug, tv_val in tv.items():
                tv_parts = tv_slug.split("-")
                if len(parts) >= 2 and len(tv_parts) >= 2:
                    # Match if first names of both fighters match
                    if parts[0] == tv_parts[0] and parts[-1] == tv_parts[-1]:
                        # Also check dates are within 1 day
                        if abs((tv_val["date_obj"] - date_obj).days) <= 1:
                            tv_info = tv_val
                            break

        network = tv_info["network"] if tv_info else ""
        start_h = tv_info["start_est_h"] if tv_info else None
        start_m = tv_info["start_est_m"] if tv_info else None

        # Build start datetime in CT
        if start_h is not None and start_m is not None:
            dt_et = datetime(
                date_obj.year, date_obj.month, date_obj.day,
                start_h, start_m,
                tzinfo=ET_ZONE
            )
            start_ct = dt_et.astimezone(CT_ZONE)
        else:
            # Default: 9pm CT if no time available
            start_ct = datetime(
                date_obj.year, date_obj.month, date_obj.day,
                21, 0, tzinfo=CT_ZONE
            )

        # Duration: 5 hours covers prelims through main event
        end_ct    = start_ct + timedelta(hours=5)
        start_utc = start_ct.astimezone(timezone.utc)
        end_utc   = end_ct.astimezone(timezone.utc)

        # Dedup
        uid_date = date_obj.strftime("%Y%m%d")
        uid = f"{uid_date}-{slug}@boxlive-calendar"
        if uid in seen:
            continue
        seen.add(uid)

        ev = Event()
        ev.name  = fight
        ev.begin = start_utc
        ev.end   = end_utc
        ev.description = "\n".join(filter(None, [
            f"Date: {date_obj.strftime('%A, %d %B %Y')}",
            f"Venue: {venue}" if venue else "",
            f"Network: {network}" if network else "",
            f"Start: {start_ct.strftime('%I:%M %p CT')}" if start_h is not None else "Start: TBC",
            "",
            "Undercard:",
            *[f"  • {u}" for u in undercard],
            "",
            "Source: Box.Live",
        ]))
        ev.uid = uid

        events.append(ev)
        time_str = start_ct.strftime("%I:%M %p CT") if start_h is not None else "TBC"
        print(f"  + {uid_date} | {fight} | {time_str} | {network or 'TBC'}")

    return events


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print("Fetching schedule page...")
    schedule_html = fetch(SCHEDULE_URL)

    print("Fetching US TV page...")
    tv_html = fetch(TV_URL)

    print("Parsing schedule...")
    schedule = parse_schedule_page(schedule_html)

    print("Parsing TV data...")
    tv = parse_tv_page(tv_html)

    print("Merging and building calendar...")
    events = build_events(schedule, tv)

    if not events:
        print("\n[DEBUG] No events built — check parsing output above")

    cal = Calendar()
    cal.extra.append(ContentLine(name="CALSCALE", value="GREGORIAN"))
    cal.extra.append(ContentLine(name="COMMENT", value="Event data sourced from Box.Live"))
    for ev in events:
        cal.events.add(ev)

    output = "boxing_schedule.ics"
    with open(output, "w", encoding="utf-8") as f:
        f.writelines(cal)

    print(f"\nDone — {len(events)} events written to {output}")


if __name__ == "__main__":
    main()

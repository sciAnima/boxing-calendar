import re
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from ics import Calendar, Event
from ics.grammar.parse import ContentLine

URL = "https://www.boxing247.com/fight-schedule"

# Map country keywords to time zones
LOCATION_TIMEZONES = {
    "USA": "America/New_York",
    "United States": "America/New_York",
    "England": "Europe/London",
    "UK": "Europe/London",
    "Scotland": "Europe/London",
    "Wales": "Europe/London",
    "Ireland": "Europe/Dublin",
    "Mexico": "America/Mexico_City",
    "Australia": "Australia/Brisbane",
    "Puerto Rico": "America/Puerto_Rico",
    "Germany": "Europe/Berlin",
    "Denmark": "Europe/Copenhagen",
    "Japan": "Asia/Tokyo",
    "United Arab Emirates": "Asia/Dubai",
    "Saudi Arabia": "Asia/Riyadh",
}

CT_ZONE = ZoneInfo("America/Chicago")


def fetch_page_text() -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0 Safari/537.36"
        )
    }
    resp = requests.get(URL, headers=headers, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    return soup.get_text(separator="\n")


def infer_timezone_from_location(location: str) -> ZoneInfo | None:
    if not location:
        return None
    for key, tz in LOCATION_TIMEZONES.items():
        if key.lower() in location.lower():
            return ZoneInfo(tz)
    return None


def extract_ringwalk_time(info: str | None, date_str: str, location: str) -> datetime | None:
    if not info:
        return None

    # ET ringwalk
    m_et = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM)).{0,40}ET.*ringwalk", info, re.I)
    if m_et:
        t = m_et.group(1)
        dt = datetime.strptime(f"{date_str} {t}", "%B %d, %Y %I:%M %p")
        dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))
        return dt.astimezone(CT_ZONE)

    # UK ringwalk
    m_uk = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM)).{0,40}UK.*ringwalk", info, re.I)
    if m_uk:
        t = m_uk.group(1)
        dt = datetime.strptime(f"{date_str} {t}", "%B %d, %Y %I:%M %p")
        dt = dt.replace(tzinfo=ZoneInfo("Europe/London"))
        return dt.astimezone(CT_ZONE)

    # Local Time ringwalk
    m_local = re.search(
        r"(\d{1,2}:\d{2}\s*(AM|PM)).{0,40}Local Time.*ringwalk", info, re.I
    )
    if m_local:
        t = m_local.group(1)
        tz = infer_timezone_from_location(location)
        if tz:
            dt = datetime.strptime(f"{date_str} {t}", "%B %d, %Y %I:%M %p")
            dt = dt.replace(tzinfo=tz)
            return dt.astimezone(CT_ZONE)

    return None


def parse_header_line(line: str):
    m = re.match(r"([A-Za-z]+ \d{1,2}, \d{4}):\s*(.*)", line)
    if not m:
        return None, None, None

    date_str = m.group(1).strip()
    rest = m.group(2).strip()

    loc = rest
    info = None

    m2 = re.match(r"(.*?)(\((.*)\))\s*$", rest)
    if m2:
        loc = m2.group(1).strip()
        info = m2.group(3).strip()

    return date_str, loc, info


def normalize_line(line: str) -> str:
    return " ".join(line.split())


def build_events_from_text(text: str) -> list[Event]:
    lines = [normalize_line(l) for l in text.splitlines()]
    events: list[Event] = []

    current_date_str = None
    current_location = None
    current_info = None

    for line in lines:
        if not line:
            continue

        date_str, loc, info = parse_header_line(line)
        if date_str:
            current_date_str = date_str
            current_location = loc
            current_info = info
            continue

        if current_date_str and current_location:
            fight_line = line

            if re.search(r"^TV:|^PPV:|^Stream:", fight_line, re.I):
                continue

            try:
                start_dt_ct = extract_ringwalk_time(
                    current_info or "", current_date_str, current_location
                )

                if not start_dt_ct:
                    base_date = datetime.strptime(current_date_str, "%B %d, %Y")
                    start_dt_ct = datetime(
                        year=base_date.year,
                        month=base_date.month,
                        day=base_date.day,
                        hour=21,
                        minute=0,
                        tzinfo=CT_ZONE,
                    )

                end_dt_ct = start_dt_ct + timedelta(hours=3)

                start_utc = start_dt_ct.astimezone(timezone.utc)
                end_utc = end_dt_ct.astimezone(timezone.utc)

                ev = Event()
                ev.name = fight_line
                ev.begin = start_utc
                ev.end = end_utc

                desc_parts = [
                    f"Location: {current_location}",
                    f"Info: {current_info}" if current_info else "",
                    "Source: Boxing247.com",
                ]
                ev.description = "\n".join([d for d in desc_parts if d])

                slug = re.sub(r"[^a-zA-Z0-9]+", "-", fight_line).strip("-").lower()
                uid_date = datetime.strptime(current_date_str, "%B %d, %Y").strftime(
                    "%Y%m%d"
                )
                ev.uid = f"{uid_date}-{slug}@boxing247-calendar"

                events.append(ev)
            except Exception:
                continue

    return events


def main():
    print("Fetching Boxing247 schedule...")
    text = fetch_page_text()
    print("Building events...")
    events = build_events_from_text(text)

    cal = Calendar()

    # Correct ICS metadata
    cal.extra.append(ContentLine(name="CALSCALE", value="GREGORIAN"))
    cal.extra.append(ContentLine(name="COMMENT", value="Event data sourced from Boxing247.com"))

    for ev in events:
        cal.events.add(ev)

    with open("boxing_schedule.ics", "w", encoding="utf-8") as f:
        f.writelines(cal)

    print(f"Wrote {len(events)} events to boxing_schedule.ics")


if __name__ == "__main__":
    main()

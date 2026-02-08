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
    "Canada": "America/Toronto",
}

CT_ZONE = ZoneInfo("America/Chicago")

MONTHS_REGEX = r"(January|February|March|April|May|June|July|August|September|October|November|December)"


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
    # Visible text, but we’ll parse it as a single blob, not line-by-line
    return soup.get_text(separator=" ")


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
    m_et = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM)).{0,40}ET", info, re.I)
    if m_et:
        t = m_et.group(1)
        dt = datetime.strptime(f"{date_str} {t}", "%B %d, %Y %I:%M %p")
        dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))
        return dt.astimezone(CT_ZONE)

    # UK ringwalk
    m_uk = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM)).{0,40}UK", info, re.I)
    if m_uk:
        t = m_uk.group(1)
        dt = datetime.strptime(f"{date_str} {t}", "%B %d, %Y %I:%M %p")
        dt = dt.replace(tzinfo=ZoneInfo("Europe/London"))
        return dt.astimezone(CT_ZONE)

    # Local Time ringwalk
    m_local = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM)).{0,40}Local Time", info, re.I)
    if m_local:
        t = m_local.group(1)
        tz = infer_timezone_from_location(location)
        if tz:
            dt = datetime.strptime(f"{date_str} {t}", "%B %d, %Y %I:%M %p")
            dt = dt.replace(tzinfo=tz)
            return dt.astimezone(CT_ZONE)

    return None


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_into_cards(text: str) -> list[str]:
    """
    Boxing247 now formats the schedule as a long inline blob like:

    February 5: Montreal, Quebec, Canada 🇨🇦 (Live on TBA | at 8:00 PM ET 🇺🇸 / 1:00 AM UK 🇬🇧)
    Albert Ramirez versus Lerrone Richards, ...
    📅 February 6: Guadalajara, Mexico 🇲🇽 (Live on DAZN | at 7:00 PM Local / 8:00 PM ET 🇺🇸 / 1:00 AM UK 🇬🇧)
    ...

    We treat each 'Month Day:' as the start of a card and slice until the next one.
    """
    text = normalize_space(text)

    # Add a marker before each date to make splitting easier
    pattern = rf"(📅\s*)?{MONTHS_REGEX}\s+\d{{1,2}}:"
    matches = list(re.finditer(pattern, text))

    cards: list[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segment = text[start:end].strip()
        if segment:
            cards.append(segment)

    return cards


def parse_card_header_and_fights(card_text: str):
    """
    From a card segment like:

    'February 5: Montreal, Quebec, Canada 🇨🇦 (Live on TBA | at 8:00 PM ET 🇺🇸 / 1:00 AM UK 🇬🇧) Albert Ramirez versus ...'

    we extract:
      - date_str: 'February 5, 2025'
      - location: 'Montreal, Quebec, Canada'
      - info: 'Live on TBA | at 8:00 PM ET 🇺🇸 / 1:00 AM UK 🇬🇧'
      - main_fight: 'Albert Ramirez versus Lerrone Richards, 12 rds, for Ramirez’s WBA interim light heavyweight title'
    """
    card_text = normalize_space(card_text)

    # Date + rest
    m = re.match(rf"(📅\s*)?({MONTHS_REGEX}\s+\d{{1,2}}):\s*(.*)", card_text)
    if not m:
        return None, None, None, None

    date_no_year = m.group(2)  # e.g. 'February 5'
    rest = m.group(3).strip()

    # Infer year as current year
    current_year = datetime.now(CT_ZONE).year
    date_str = f"{date_no_year}, {current_year}"

    # Split header (location + parentheses) from fights
    # We assume first ')' closes the broadcast/time info
    idx_paren = rest.find(")")
    if idx_paren != -1:
        header_part = rest[: idx_paren + 1]
        fights_part = rest[idx_paren + 1 :].strip()
    else:
        header_part = rest
        fights_part = ""

    # Location: between ':' and '('
    loc = header_part
    info = None
    m_loc = re.match(r"(.*?)(\((.*)\))", header_part)
    if m_loc:
        loc = m_loc.group(1).strip()
        info = m_loc.group(3).strip()

    # Strip trailing flags from location
    loc = re.sub(r"[^\w\s,]+$", "", loc).strip()

    # Main fight: first 'versus' phrase
    main_fight = None
    if fights_part:
        m_fight = re.search(r"([A-Z][^,]+?versus[^📅]+?)(?=(?: [A-Z][a-z]+ [A-Z][a-z]+ versus|📅|$))", fights_part)
        if m_fight:
            main_fight = m_fight.group(1).strip()
        else:
            # Fallback: take first chunk up to next '📅' or 200 chars
            main_fight = fights_part.split("📅")[0].strip()
            if len(main_fight) > 200:
                main_fight = main_fight[:200] + "..."

    return date_str, loc, info, main_fight


def build_events_from_text(text: str) -> list[Event]:
    cards = split_into_cards(text)
    events: list[Event] = []

    for card in cards:
        try:
            date_str, location, info, main_fight = parse_card_header_and_fights(card)
            if not date_str or not location:
                continue

            # Infer start time from ringwalk info if possible
            start_dt_ct = extract_ringwalk_time(info or "", date_str, location)

            if not start_dt_ct:
                # Default: 9:00 PM CT on that date
                base_date = datetime.strptime(date_str, "%B %d, %Y")
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
            # Event name: main fight or generic label
            if main_fight:
                ev.name = main_fight
            else:
                ev.name = f"Boxing card – {location}"

            ev.begin = start_utc
            ev.end = end_utc

            desc_parts = [
                f"Date: {date_str}",
                f"Location: {location}",
            ]
            if info:
                desc_parts.append(f"Info: {info}")
            desc_parts.append("Source: Boxing247.com")

            ev.description = "\n".join(desc_parts)

            # UID: date + slug of location + optional main fight
            slug_base = location
            if main_fight:
                slug_base = f"{location} {main_fight}"
            slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug_base).strip("-").lower()
            uid_date = datetime.strptime(date_str, "%B %d, %Y").strftime("%Y%m%d")
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
    cal.extra.append(ContentLine(name="CALSCALE", value="GREGORIAN"))
    cal.extra.append(ContentLine(name="COMMENT", value="Event data sourced from Boxing247.com"))

    for ev in events:
        cal.events.add(ev)

    with open("boxing_schedule.ics", "w", encoding="utf-8") as f:
        f.writelines(cal)

    print(f"Wrote {len(events)} events to boxing_schedule.ics")


if __name__ == "__main__":
    main()

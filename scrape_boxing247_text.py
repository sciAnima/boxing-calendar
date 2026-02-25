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

import cloudscraper


def fetch_page_text() -> str:
    scraper = cloudscraper.create_scraper(
        browser={
            "browser": "chrome",
            "platform": "windows",
            "mobile": False,
        }
    )

    resp = scraper.get(URL, timeout=30)
    print(f"DEBUG STATUS: {resp.status_code}")
    print(f"DEBUG HEADERS: {dict(resp.headers)}")
    resp.raise_for_status()
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text(separator="\n")
    return text


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


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

    m_et = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM)).{0,40}ET", info, re.I)
    if m_et:
        t = m_et.group(1)
        dt = datetime.strptime(f"{date_str} {t}", "%B %d, %Y %I:%M %p")
        dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))
        return dt.astimezone(CT_ZONE)

    m_uk = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM)).{0,40}UK", info, re.I)
    if m_uk:
        t = m_uk.group(1)
        dt = datetime.strptime(f"{date_str} {t}", "%B %d, %Y %I:%M %p")
        dt = dt.replace(tzinfo=ZoneInfo("Europe/London"))
        return dt.astimezone(CT_ZONE)

    m_local = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM)).{0,40}Local", info, re.I)
    if m_local:
        t = m_local.group(1)
        tz = infer_timezone_from_location(location)
        if tz:
            dt = datetime.strptime(f"{date_str} {t}", "%B %d, %Y %I:%M %p")
            dt = dt.replace(tzinfo=tz)
            return dt.astimezone(CT_ZONE)

    return None


def split_into_cards(text: str) -> list[str]:
    # Keep line breaks for easier matching, then normalize later per card
    # We only care that "Month Day:" appears; ignore any leading emoji/junk.
    pattern = rf"{MONTHS_REGEX}\s+\d{{1,2}}:"

    matches = list(re.finditer(pattern, text))
    cards: list[str] = []

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segment = text[start:end].strip()
        if segment:
            cards.append(segment)

    print(f"DEBUG: Found {len(cards)} cards")
    for i, c in enumerate(cards[:10]):
        print(f"DEBUG CARD {i}: {c[:200].replace(chr(10), ' ')}")

    return cards


def parse_card(card_text: str):
    # First, collapse whitespace
    card_text = normalize_space(card_text)

    # Strip any junk before the month name (emoji, mojibake, etc.)
    card_text = re.sub(r"^[^A-Za-z]+", "", card_text).strip()

    if ":" not in card_text:
        return None, None, None, None

    date_part, rest = card_text.split(":", 1)
    date_part = date_part.strip()
    rest = rest.strip()

    if not re.match(rf"^{MONTHS_REGEX}\s+\d{{1,2}}$", date_part):
        return None, None, None, None

    current_year = datetime.now(CT_ZONE).year
    date_str = f"{date_part}, {current_year}"

    parts = rest.split(")", 1)
    header_part = parts[0] + ")" if len(parts) > 1 else parts[0]
    fights_part = parts[1] if len(parts) > 1 else ""

    loc = header_part.split("(", 1)[0].strip()
    loc = re.sub(r"[^A-Za-z0-9 ,.-]", "", loc).strip()

    info = None
    if "(" in header_part and ")" in header_part:
        info = header_part.split("(", 1)[1].rsplit(")", 1)[0].strip()

    fights_part = normalize_space(fights_part)

    if not fights_part.strip():
        m_fallback = re.search(
            r"[A-Za-z].+?versus.+?(?=$)", card_text, re.I | re.S
        )
        if m_fallback:
            fights_part = m_fallback.group(0).strip()

    main_fight = None
    if fights_part:
        m_fight = re.search(
            r"([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-\. ]+?)\s+(?:versus|vs\.?|v\.?)\s+([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-\. ]+)",
            fights_part,
            re.IGNORECASE,
        )
        if m_fight:
            main_fight = f"{m_fight.group(1).strip()} versus {m_fight.group(2).strip()}"
        else:
            main_fight = fights_part
            if len(main_fight) > 200:
                main_fight = main_fight[:200] + "..."

    return date_str, loc, info, main_fight


def build_events_from_text(text: str) -> list[Event]:
    cards = split_into_cards(text)
    events: list[Event] = []

    for idx, card in enumerate(cards):
        try:
            date_str, location, info, main_fight = parse_card(card)
            print(f"DEBUG PARSED CARD {idx}: date={date_str}, loc={location}, main_fight={main_fight}")
            if not date_str or not location:
                continue

            start_dt_ct = extract_ringwalk_time(info or "", date_str, location)

            if not start_dt_ct:
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
            ev.name = main_fight if main_fight else f"Boxing card – {location}"
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

            slug_base = f"{location} {main_fight}" if main_fight else location
            slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug_base).strip("-").lower()
            uid_date = datetime.strptime(date_str, "%B %d, %Y").strftime("%Y%m%d")
            ev.uid = f"{uid_date}-{slug}@boxing247-calendar"

            events.append(ev)
        except Exception as e:
            print(f"DEBUG ERROR on card {idx}: {e}")
            continue

    return events


def main():
    print("Fetching Boxing247 schedule...")
    text = fetch_page_text()
    print("DEBUG: First 5000 characters of scraped text:")
    print(text[:5000])
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

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
import time
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from ics import Calendar, Event

URL = "https://www.boxing247.com/fight-schedule"

# Map country keywords to time zones
LOCATION_TIMEZONES = {
    "USA": "America/New_York",  # ET default for US cards
    "England": "Europe/London",
    "UK": "Europe/London",
    "Mexico": "America/Mexico_City",
    "Australia": "Australia/Brisbane",
    "Puerto Rico": "America/Puerto_Rico",
    "Germany": "Europe/Berlin",
    "Denmark": "Europe/Copenhagen",
    "Japan": "Asia/Tokyo",
    "United Arab Emirates": "Asia/Dubai",
}

CT_ZONE = ZoneInfo("America/Chicago")


def load_page():
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-blink-features=AutomationControlled")

    driver = uc.Chrome(options=options)
    driver.get(URL)
    time.sleep(5)
    return driver


def extract_text(driver):
    body = driver.find_element(By.TAG_NAME, "body")
    return body.text


def infer_timezone_from_location(location):
    for key, tz in LOCATION_TIMEZONES.items():
        if key.lower() in location.lower():
            return ZoneInfo(tz)
    return None


def extract_ringwalk_time(info, date_str, location):
    """
    Detects ringwalk times like:
    - "ringwalks expected at 10:30 PM ET"
    - "main event ringwalk around 4:00 AM UK"
    - "ringwalks at 7:00 PM Local Time"
    Returns a datetime in CT or None.
    """
    if not info:
        return None

    # ET ringwalk
    m_et = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM)).{0,20}ET.*ringwalk", info, re.I)
    if m_et:
        t = m_et.group(1)
        dt = datetime.strptime(f"{date_str} {t}", "%B %d, %Y %I:%M %p")
        dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))
        return dt.astimezone(CT_ZONE)

    # UK ringwalk
    m_uk = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM)).{0,20}UK.*ringwalk", info, re.I)
    if m_uk:
        t = m_uk.group(1)
        dt = datetime.strptime(f"{date_str} {t}", "%B %d, %Y %I:%M %p")
        dt = dt.replace(tzinfo=ZoneInfo("Europe/London"))
        return dt.astimezone(CT_ZONE)

    # Local Time ringwalk
    m_local = re.search(
        r"(\d{1,2}:\d{2}\s*(AM|PM)).{0,20}Local Time.*ringwalk", info, re.I
    )
    if m_local:
        t = m_local.group(1)
        tz = infer_timezone_from_location(location)
        if tz:
            dt = datetime.strptime(f"{date_str} {t}", "%B %d, %Y %I:%M %p")
            dt = dt.replace(tzinfo=tz)
            return dt.astimezone(CT_ZONE)

    return None


def parse_header_line(line):
    """
    Example header line:
    January 16, 2026: Palm Desert, California, USA 🇺🇸 (LIVE on DAZN at 8:00 PM ET ...)
    Returns: (event_date, location, tv_network, start_ct)
    """
    m = re.match(r"([A-Za-z]+ \d{1,2}, \d{4}):\s*(.*)", line)
    if not m:
        return None, None, None, None

    date_str = m.group(1)
    rest = m.group(2)

    # Split into location + (...) info
    loc = rest
    info = None
    m2 = re.match(r"(.*?)(\((.*)\))\s*$", rest)
    if m2:
        loc = m2.group(1).strip()
        info = m2.group(3).strip()

    try:
        event_date = datetime.strptime(date_str, "%B %d, %Y")
    except Exception:
        return None, None, None, None

    tv_network = None
    time_info = info or ""

    # Extract network: "on XX at"
    m_net = re.search(r"on ([A-Za-z0-9 +]+?) at ", time_info)
    if m_net:
        tv_network = m_net.group(1).strip()

    # First, try ringwalk override
    ringwalk_ct = extract_ringwalk_time(time_info, date_str, loc)
    if ringwalk_ct:
        return event_date, loc, tv_network, ringwalk_ct

    # Otherwise, card start time logic

    # ET time
    m_et = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM))\s*ET", time_info)
    # UK time
    m_uk = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM))\s*UK", time_info)
    # Local Time
    m_local = re.search(r"(\d{1,2}:\d{2}\s*(AM|PM))\s*Local Time", time_info)

    if m_et:
        time_str = m_et.group(1)
        dt = datetime.strptime(
            f"{date_str} {time_str}", "%B %d, %Y %I:%M %p"
        ).replace(tzinfo=ZoneInfo("America/New_York"))
        start_ct = dt.astimezone(CT_ZONE)
        return event_date, loc, tv_network, start_ct

    if m_uk:
        time_str = m_uk.group(1)
        dt = datetime.strptime(
            f"{date_str} {time_str}", "%B %d, %Y %I:%M %p"
        ).replace(tzinfo=ZoneInfo("Europe/London"))
        start_ct = dt.astimezone(CT_ZONE)
        return event_date, loc, tv_network, start_ct

    if m_local:
        time_str = m_local.group(1)
        tz = infer_timezone_from_location(loc)
        if tz:
            dt = datetime.strptime(
                f"{date_str} {time_str}", "%B %d, %Y %I:%M %p"
            ).replace(tzinfo=tz)
            start_ct = dt.astimezone(CT_ZONE)
            return event_date, loc, tv_network, start_ct

    # Default: 9 PM CT (with tzinfo)
    start_ct = event_date.replace(hour=21, minute=0, tzinfo=CT_ZONE)
    return event_date, loc, tv_network, start_ct


def parse_fights(text):
    """
    Returns list of:
      (start_datetime_ct, fight_text, location, tv_network, header_info, full_card)
    """
    fights = []

    blocks = re.split(r"📅", text)
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue

        header_line = lines[0]
        event_date, location, tv_network, start_ct = parse_header_line(header_line)

        if not event_date:
            continue

        # Collect all fights in this block
        card_fights = []
        for line in lines[1:]:
            if "versus" in line.lower():
                card_fights.append(line.strip())

        # Add each fight with the full card summary
        for fight in card_fights:
            fights.append(
                (start_ct, fight, location, tv_network, header_line, card_fights)
            )

    return fights


def build_calendar(fights):
    cal = Calendar()

    for start_ct, fight, location, tv_network, header_info, full_card in fights:
        end = start_ct + timedelta(hours=3)

        event = Event()
        event.name = fight
        event.begin = start_ct
        event.end = end

        if location:
            event.location = location

        desc = []

        if tv_network:
            desc.append(f"TV: {tv_network}")

        desc.append(f"Header: {header_info}")

        # Full card summary
        desc.append("Full Card:")
        for f in full_card:
            desc.append(f"- {f}")

        event.description = "\n".join(desc)

        cal.events.add(event)

    return cal


def save_calendar(cal):
    with open("boxing_schedule.ics", "w", encoding="utf-8") as f:
        f.writelines(cal)


def main():
    driver = load_page()
    text = extract_text(driver)
    driver.quit()

    fights = parse_fights(text)

    print(f"Found {len(fights)} fights")
    for start_ct, fight, location, tv_network, header_info, full_card in fights:
        print(
            start_ct,
            "-",
            fight,
            "|",
            location or "No location",
            "|",
            tv_network or "No network",
        )

    cal = build_calendar(fights)
    save_calendar(cal)
    print("Saved boxing_schedule.ics")


if __name__ == "__main__":
    main()
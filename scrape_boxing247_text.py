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
    m = re.match(r"([A-Za-z]+ \d{1,2}, \d{4}):\s*(.*)", line)
    if not m:
        return None, None, None, None

    date_str = m.group(1)
    rest = m.group(2)

    loc = rest
    info = None
    m2 = re.match(r"(.*?)(\((.*)\))\s*$", rest)
    if m2:
        loc = m2.group(1).strip()

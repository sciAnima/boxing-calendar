# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")

import json
import re
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from ics import Calendar, Event
from ics.grammar.parse import ContentLine
from playwright.sync_api import sync_playwright

BN24_URL       = "https://www.boxingnews24.com/boxing-schedule/"
BS_URL         = "https://www.boxingscene.com/schedule"
BS_EXT_URL     = "https://www.boxingscene.com/schedule/extended"
BS_RESULTS_URL = "https://www.boxingscene.com/results"
TBL_URL        = "https://www.teamboxingleague.com/pages/events"
PROBOX_URL     = "https://proboxtv.com/tickets/"
DAZN_URL       = "https://www.dazn.com/en-US/schedule"

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

MONTH_ABBR = {
    "Jan": "January", "Feb": "February", "Mar": "March",
    "Apr": "April",   "May": "May",      "Jun": "June",
    "Jul": "July",    "Aug": "August",   "Sep": "September",
    "Oct": "October", "Nov": "November", "Dec": "December",
}

# BoxingNews24 layout: a dated heading, then labelled sections per event
BN24_DATE_RE       = re.compile(r"^\w+,\s+(\w+)\s+(\d{1,2}),\s+(\d{4})$")
BN24_TIME_RE       = re.compile(r"^(\d{1,2}:\d{2})\s*([ap]m)\s+([A-Z]{2,4})$", re.I)
BN24_SHORT_DATE_RE = re.compile(r"^\w{3},\s+(\w{3})\s+(\d{1,2})\b")
BN24_BOUT_RE       = re.compile(r"\bbout\b", re.I)
BN24_SECTIONS = {
    "Main event":            "main",
    "Broadcaster":           "broadcaster",
    "Estimated event start": "start",
    "Estimated ring walks":  "ring",
    "Undercard":             "undercard",
}

BS_DT_RE = re.compile(
    r"\w+,\s+(\w+)\s+(\d{1,2}),\s+(\d{4})\s+-\s+(\d{1,2}):(\d{2})\s+(AM|PM)(?:\s+(\w+))?",
    re.I
)

# DAZN lists every sport; keep boxing competitions and drop non-fight content
DAZN_BOXING_RE = re.compile(r"boxing", re.I)
DAZN_SKIP_RE   = re.compile(
    r"weigh-?in|press conference|prelims?|face-?off|countdown|workout|embedded", re.I
)

TZ_MAP = {
    "ET": "America/New_York", "EST": "America/New_York", "EDT": "America/New_York",
    "CT": "America/Chicago",  "CST": "America/Chicago",  "CDT": "America/Chicago",
    "PT": "America/Los_Angeles", "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    "GMT": "Europe/London",    "BST": "Europe/London",
}


def fetch(url: str) -> str | None:
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
    try:
        session = requests.Session()
        session.headers.update(headers)
        resp = session.get(url, timeout=30)
        print(f"  HTTP {resp.status_code} - {url}")
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.HTTPError as e:
        print(f"  WARNING: HTTP error fetching {url}: {e}")
        return None
    except requests.exceptions.ConnectionError as e:
        print(f"  WARNING: Connection error fetching {url}: {e}")
        return None
    except requests.exceptions.Timeout:
        print(f"  WARNING: Timeout fetching {url}")
        return None
    except Exception as e:
        print(f"  WARNING: Unexpected error fetching {url}: {e}")
        return None


def fetch_bs_rendered(url: str, max_clicks: int = 30) -> str | None:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                user_agent=USER_AGENTS[datetime.now().day % len(USER_AGENTS)],
                viewport={"width": 1366, "height": 2000},
            )
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_function(
                "document.querySelectorAll(\"a[href*='/events/']\").length > 0",
                timeout=20000,
            )

            def link_count() -> int:
                return page.eval_on_selector_all(
                    "a[href*='/events/']", "els => els.length"
                )

            def strip_consent_overlay() -> None:
                page.evaluate(
                    "document.querySelectorAll('[data-ketch-backdrop], #lanyard_root')"
                    ".forEach(el => el.remove())"
                )

            strip_consent_overlay()
            prev_count = link_count()
            print(f"  BoxingScene: {prev_count} events visible before Load More ({url})")

            for click_num in range(1, max_clicks + 1):
                load_more = page.get_by_role("button", name=re.compile("load more", re.I))
                try:
                    load_more.first.wait_for(state="visible", timeout=4000)
                except Exception:
                    print(f"  BoxingScene: Load More button gone after {click_num - 1} click(s)")
                    break

                strip_consent_overlay()
                load_more.first.scroll_into_view_if_needed(timeout=4000)
                try:
                    load_more.first.evaluate("el => el.click()")
                except Exception as e:
                    print(f"  BoxingScene: click {click_num} failed ({e}); stopping")
                    break

                try:
                    page.wait_for_function(
                        f"document.querySelectorAll(\"a[href*='/events/']\").length > {prev_count}",
                        timeout=8000,
                    )
                except Exception:
                    print(f"  BoxingScene: no new events after click {click_num}; stopping")
                    break

                new_count = link_count()
                print(f"  BoxingScene: click {click_num} -> {new_count} events visible")
                if new_count <= prev_count:
                    break
                prev_count = new_count

            strip_consent_overlay()
            html = page.content()
            browser.close()
            print(f"  HTTP 200 (rendered, {prev_count} events) - {url}")
            return html
    except Exception as e:
        print(f"  WARNING: Error rendering {url} with Playwright: {e}")
        return None


def parse_event_page_results(html: str) -> list[dict]:
    """Parse fight-by-fight results from a BoxingScene event page."""
    soup = BeautifulSoup(html, "html.parser")
    fights = []
    for h3 in soup.find_all("h3"):
        title_div = h3.find(class_="card-title")
        if not title_div:
            continue
        title_text = title_div.get_text(" ", strip=True)
        if not re.search(r"\bdefeats\b", title_text, re.I):
            continue
        parts = re.split(r"\s+Defeats\s+", title_text, maxsplit=1, flags=re.I)
        if len(parts) != 2:
            continue
        winner, loser = parts[0].strip(), parts[1].strip()
        method = ""
        for child in h3.children:
            if getattr(child, "name", None) == "div" and "card-title" not in (child.get("class") or []):
                method = child.get_text(" ", strip=True)
                break
        fights.append({"winner": winner, "loser": loser, "method": method})
    return fights


def parse_event_page_full(html: str, bs_slug: str) -> dict | None:
    """Parse a BoxingScene event page into a complete event dict with results."""
    soup = BeautifulSoup(html, "html.parser")

    # Extract event metadata from schema.org JSON-LD
    name = ""
    date_obj: datetime | None = None
    location = ""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if data.get("@type") == "SportsEvent":
                name = data.get("name", "")
                raw_date = data.get("startDate", "")
                if raw_date:
                    date_obj = datetime.strptime(raw_date[:10], "%Y-%m-%d")
                loc = data.get("location", {})
                venue = loc.get("name", "")
                region = loc.get("address", {}).get("addressRegion", "")
                location = ", ".join(filter(None, [venue, region]))
                break
        except Exception:
            pass

    if not name or not date_obj:
        return None

    # Extract start time from page text via BS_DT_RE
    page_text = soup.get_text(" ", strip=True)
    start_ct: datetime | None = None
    dm = BS_DT_RE.search(page_text)
    if dm:
        h, mn, ampm = int(dm.group(4)), int(dm.group(5)), dm.group(6)
        tz_abbr = dm.group(7) or "ET"
        tz_name = TZ_MAP.get(tz_abbr.upper(), "America/New_York")
        try:
            dt_str = f"{date_obj.strftime('%B')} {date_obj.day} {date_obj.year} {h}:{mn:02d} {ampm.upper()}"
            dt_parsed = datetime.strptime(dt_str, "%B %d %Y %I:%M %p")
            start_ct = dt_parsed.replace(tzinfo=ZoneInfo(tz_name)).astimezone(CT_ZONE)
        except ValueError:
            pass
    if not start_ct:
        start_ct = datetime(date_obj.year, date_obj.month, date_obj.day, 21, 0, tzinfo=CT_ZONE)

    fights = parse_event_page_results(html)

    return {
        "name":     name,
        "date_obj": date_obj,
        "location": location,
        "network":  "",
        "start_ct": start_ct,
        "fights":   [],
        "source":   "BoxingScene.com",
        "bs_slug":  bs_slug,
        "results":  fights,
    }


def fetch_bs_past_events(results_url: str, max_events: int = 15) -> dict[str, dict]:
    """Scrape the BoxingScene /results listing and individual event pages.
    Returns {slug: event_dict_with_results} for past events.
    """
    past: dict[str, dict] = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                user_agent=USER_AGENTS[datetime.now().day % len(USER_AGENTS)],
                viewport={"width": 1366, "height": 2000},
            )

            page.goto(results_url, timeout=45000, wait_until="domcontentloaded")
            try:
                page.wait_for_function(
                    "document.querySelectorAll(\"a[href*='/events/']\").length > 0",
                    timeout=20000,
                )
            except Exception:
                print("  WARNING: BoxingScene results page loaded no event links")
                browser.close()
                return past

            event_urls: list[str] = page.eval_on_selector_all(
                "a[href*='/events/']",
                "els => [...new Set(els.map(e => e.href))]"
            )
            print(f"  BoxingScene /results: {len(event_urls)} recent events found")

            for event_url in event_urls[:max_events]:
                bs_slug = event_url.rstrip("/").split("/")[-1]
                try:
                    page.goto(event_url, timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)
                    html = page.content()
                    ev = parse_event_page_full(html, bs_slug)
                    if ev:
                        slug = make_slug(ev["name"])
                        past[slug] = ev
                        r = ev["results"]
                        if r:
                            print(f"  Past event: {ev['name']} -> {r[0]['winner']} DEF. {r[0]['loser']} ({r[0]['method']})")
                        else:
                            print(f"  Past event: {ev['name']} (no results yet)")
                except Exception as e:
                    print(f"  WARNING: Failed to fetch past event {bs_slug}: {e}")

            browser.close()
    except Exception as e:
        print(f"  WARNING: Error fetching BoxingScene past events: {e}")
    return past


def strip_emoji(s: str) -> str:
    return re.sub(r"[^\x20-\x7EÀ-ÖØ-öø-ÿ''\-\.,/|: ]", "", s).strip()


def make_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def bn24_start_ct(date_obj: datetime, time_line: str, date_line: str = "") -> datetime | None:
    """'7:00 pm ET' (+ optional 'Sat, Oct 17' line) -> start time in Central."""
    tm = BN24_TIME_RE.match(time_line)
    if not tm:
        return None
    day = date_obj
    dm = BN24_SHORT_DATE_RE.match(date_line)
    if dm and MONTH_ABBR.get(dm.group(1).capitalize()):
        month = MONTHS[MONTH_ABBR[dm.group(1).capitalize()]]
        year = date_obj.year + 1 if date_obj.month - month > 6 else date_obj.year
        try:
            day = datetime(year, month, int(dm.group(2)))
        except ValueError:
            day = date_obj
    tz = ZoneInfo(TZ_MAP.get(tm.group(3).upper(), "America/New_York"))
    try:
        dt = datetime.strptime(
            f"{day:%Y-%m-%d} {tm.group(1)} {tm.group(2).upper()}", "%Y-%m-%d %I:%M %p"
        )
    except ValueError:
        return None
    return dt.replace(tzinfo=tz).astimezone(CT_ZONE)


# == Source 1: BoxingNews24 ====================================================

def parse_bn24_event(date_obj: datetime, lines: list[str]) -> dict | None:
    """Parse one event block (the lines after its dated heading)."""
    section = None
    main = location = detail = ""
    network_parts: list[str] = []
    ppv = False
    start_ct = None
    undercard: list[list[str]] = []  # [fight, detail]

    for i, line in enumerate(lines):
        if line in BN24_SECTIONS:
            section = BN24_SECTIONS[line]
        elif section == "main":
            if not main:
                main = line
            elif BN24_BOUT_RE.search(line):
                detail = detail or line
            elif not location:
                location = line
        elif section == "broadcaster":
            if re.match(r"live on\b", line, re.I):
                network = re.sub(r"\s*-\s*click here.*$", "", line[len("live on"):], flags=re.I)
                network_parts.append(network.strip())
            elif line.startswith(","):
                network_parts.append(line.lstrip(", ").strip())  # "+ TNT, truTV" style add-ons
            elif line.upper() == "PPV":
                ppv = True
        elif section == "start":
            if start_ct is None and BN24_TIME_RE.match(line):
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                start_ct = bn24_start_ct(date_obj, line, nxt)
        elif section == "undercard":
            if re.match(r"^\d+ bouts?$", line, re.I):
                continue
            if BN24_BOUT_RE.search(line) and undercard:
                undercard[-1][1] = line
            else:
                undercard.append([line, ""])

    if not main:
        return None

    network = ", ".join(filter(None, network_parts))
    if ppv:
        network = f"{network} (PPV)" if network else "PPV"

    def with_detail(fight: str, info: str) -> str:
        return f"{fight} ({info})" if info else fight

    return {
        "name":        main,
        "date_obj":    date_obj,
        "location":    location,
        "network":     network,
        "start_ct":    start_ct or datetime(date_obj.year, date_obj.month, date_obj.day, 21, 0, tzinfo=CT_ZONE),
        "start_known": start_ct is not None,
        "fights":      [with_detail(main, detail)] + [with_detail(f, d) for f, d in undercard],
        "source":      "BoxingNews24.com",
        "bs_slug":     "",
        "results":     [],
    }


def parse_bn24(html: str) -> dict[str, dict]:
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("div", class_=re.compile(r"entry|post|content|article", re.I)) or soup
    lines = [l.strip() for l in content.get_text(separator="\n").split("\n") if l.strip()]

    # One block per event: a dated heading ("Saturday, October 17, 2026") and what follows it
    blocks: list[tuple[datetime, list[str]]] = []
    current: list[str] | None = None
    for line in lines:
        m = BN24_DATE_RE.match(line)
        if m and m.group(1).capitalize() in MONTHS:
            current = []
            blocks.append((datetime(int(m.group(3)), MONTHS[m.group(1).capitalize()], int(m.group(2))), current))
        elif line.startswith("Share this article"):
            current = None
        elif current is not None:
            current.append(line)

    events = {}
    for date_obj, block_lines in blocks:
        ev = parse_bn24_event(date_obj, block_lines)
        if ev:
            # Drop "vs" so the slug matches BoxingScene's "Fighter A-Fighter B" naming
            events[make_slug(re.sub(r"\s+vs\.?\s+", " ", ev["name"], flags=re.I))] = ev

    print(f"  BoxingNews24: {len(events)} events parsed")
    if not events:
        print("  WARNING: BoxingNews24 page was fetched but no events parsed - its layout may have changed")
    return events


# == Source 2: BoxingScene ======================================================

def parse_bs(html: str, label: str = "BoxingScene") -> dict[str, dict]:
    soup = BeautifulSoup(html, "html.parser")
    events = {}

    for a in soup.find_all("a", href=re.compile(r"/events/")):
        text = a.get_text(separator=" | ").strip()
        if not BS_DT_RE.search(text):
            continue

        parts = [p.strip() for p in text.split("|") if p.strip()]
        if not parts:
            continue

        fight_name = parts[0].strip()
        if not fight_name:
            continue

        datetime_str = ""
        venue = ""
        network = ""
        for part in parts[1:]:
            if BS_DT_RE.search(part) and not datetime_str:
                datetime_str = part
            elif any(kw in part for kw in [
                "DAZN","ESPN","Netflix","Prime","HBO","Showtime",
                "Paramount","PPV","Sky","TNT","BBC","TrillerTV","ProBoxTV"
            ]):
                network = part
            elif part and not venue and len(part) > 5:
                venue = part

        dm = BS_DT_RE.search(datetime_str)
        if not dm:
            continue

        month_abbr = dm.group(1)
        month_full = MONTH_ABBR.get(month_abbr.capitalize(), month_abbr)
        month_num  = MONTHS.get(month_full)
        if not month_num:
            continue

        day  = int(dm.group(2))
        year = int(dm.group(3))
        h, mn, ampm = int(dm.group(4)), int(dm.group(5)), dm.group(6)
        tz_abbr = dm.group(7) or "ET"
        tz_name = TZ_MAP.get(tz_abbr.upper(), "America/New_York")

        try:
            dt_str   = f"{month_full} {day} {year} {h}:{mn:02d} {ampm.upper()}"
            dt_parsed = datetime.strptime(dt_str, "%B %d %Y %I:%M %p")
            start_ct  = dt_parsed.replace(tzinfo=ZoneInfo(tz_name)).astimezone(CT_ZONE)
        except ValueError:
            continue

        date_obj = datetime(year, month_num, day)
        slug = make_slug(fight_name)
        bs_slug = a.get("href", "").rstrip("/").split("/")[-1]

        events[slug] = {
            "name":     fight_name,
            "date_obj": date_obj,
            "location": venue,
            "network":  network,
            "start_ct": start_ct,
            "fights":   [],
            "source":   "BoxingScene.com",
            "bs_slug":  bs_slug,
            "results":  [],
        }

    print(f"  {label}: {len(events)} events parsed")
    return events


# == Source 3: Team Boxing League ==============================================

def parse_tbl(html: str) -> dict[str, dict]:
    soup = BeautifulSoup(html, "html.parser")
    events = {}

    for row in soup.find_all("div", class_=re.compile(r"\bevent-row\b")):
        if not row.find(class_="event-row__date"):
            continue

        date_el = row.find(class_="event-row__date")
        date_str = date_el.get_text(strip=True)
        try:
            date_obj = datetime.strptime(date_str, "%A, %B %d, %Y")
        except ValueError:
            continue

        time_el  = row.find(class_="event-row__time")
        time_raw = time_el.get_text(strip=True) if time_el else "7:00PM"
        time_str = re.sub(r"\s*LOCAL\s*", "", time_raw, flags=re.I).strip()
        try:
            t = datetime.strptime(time_str, "%I:%M%p")
            start_ct = datetime(
                date_obj.year, date_obj.month, date_obj.day,
                t.hour, t.minute, tzinfo=CT_ZONE
            )
        except ValueError:
            start_ct = datetime(
                date_obj.year, date_obj.month, date_obj.day, 19, 0, tzinfo=CT_ZONE
            )

        team_names = [
            el.get_text(strip=True)
            for el in row.find_all(class_="event-row__team-name")
        ]
        if len(team_names) < 2:
            continue
        name = f"{team_names[0]} vs. {team_names[1]}"

        venue_el   = row.find(class_="event-row__venue")
        venue      = venue_el.get_text(strip=True) if venue_el else ""
        addr_parts = [el.get_text(strip=True) for el in row.find_all(class_="event-row__address")]
        location   = ", ".join(filter(None, [venue] + addr_parts))

        slug = make_slug(name) + f"-{date_obj.strftime('%Y%m%d')}"

        events[slug] = {
            "name":     name,
            "date_obj": date_obj,
            "location": location,
            "network":  "YouTube",
            "start_ct": start_ct,
            "fights":   [],
            "source":   "TeamBoxingLeague.com",
            "bs_slug":  "",
            "results":  [],
        }

    print(f"  Team Boxing League: {len(events)} events parsed")
    return events


# == Source 4: ProBox TV =======================================================

def parse_probox(html: str) -> dict[str, dict]:
    soup = BeautifulSoup(html, "html.parser")
    events = {}

    for card in soup.find_all("div", class_=re.compile(r"\bmatch-preview\b")):
        title_el = card.find(class_="match-preview__title")
        if not title_el:
            continue
        name = title_el.get_text(strip=True)
        if not name:
            continue

        time_el = card.find("time")
        if not time_el or not time_el.get("datetime"):
            continue
        try:
            dt_parsed = datetime.strptime(time_el["datetime"], "%Y-%m-%d %H:%M")
            start_ct  = dt_parsed.replace(tzinfo=ET_ZONE).astimezone(CT_ZONE)
            date_obj  = datetime(dt_parsed.year, dt_parsed.month, dt_parsed.day)
        except ValueError:
            continue

        place_el = card.find(class_="match-preview__match-place")
        location = place_el.get_text(strip=True) if place_el else ""

        slug = make_slug(name) + f"-{date_obj.strftime('%Y%m%d')}"

        events[slug] = {
            "name":     name,
            "date_obj": date_obj,
            "location": location,
            "network":  "YouTube",
            "start_ct": start_ct,
            "fights":   [],
            "source":   "ProBoxTV.com",
            "bs_slug":  "",
            "results":  [],
        }

    print(f"  ProBox TV: {len(events)} events parsed")
    return events


# == Source 5: DAZN ============================================================

def dazn_day_fallback(day_text: str) -> datetime | None:
    """Day header text ("October 03 Saturday") -> 9:00 PM CT that day, as UTC.
    Year is inferred (the schedule spans ~10 days, so a past month means next year)."""
    month_pattern = "|".join(MONTHS.keys())
    m = re.search(rf"({month_pattern})\s+(\d{{1,2}})", day_text, re.I)
    if not m:
        return None
    now = datetime.now(CT_ZONE)
    month, day = MONTHS[m.group(1).capitalize()], int(m.group(2))
    year = now.year + 1 if month < now.month - 1 else now.year
    try:
        return datetime(year, month, day, 21, 0, tzinfo=CT_ZONE).astimezone(timezone.utc)
    except ValueError:
        return None


def fetch_dazn(url: str) -> list[dict]:
    """Render the DAZN schedule (client-side app) and read each boxing event
    page's JSON-LD for its exact UTC start time.
    Returns [{"name", "competition", "start_utc"}].
    """
    found: list[dict] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            # No user_agent override: DAZN serves an empty schedule to spoofed UAs
            page = browser.new_page(viewport={"width": 1366, "height": 2000})
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            try:
                page.wait_for_selector("[data-testid='standard-rail__tile__link']", state="attached", timeout=30000)
            except Exception:
                # Dump what the browser actually got (uploaded by the workflow as debug_raw.html)
                print(f"  DAZN: schedule did not render - url={page.url!r} title={page.title()!r}")
                with open("debug_raw.html", "w", encoding="utf-8") as f:
                    f.write(f"<!-- url: {page.url} | title: {page.title()} -->\n{page.content()}")
                browser.close()
                raise
            page.wait_for_timeout(3000)

            tiles = page.eval_on_selector_all(
                "[data-testid='standard-rail__tile__link']",
                """els => els.map(a => {
                    const rail = a.closest('.standard-rail');
                    const day = rail && rail.previousElementSibling;
                    return [a.href, day ? day.innerText : '',
                            ...[...a.querySelectorAll('span')].map(s => s.innerText)];
                })""",
            )
            seen: set[str] = set()
            candidates = []
            for href, day_text, *spans in tiles:
                if len(spans) < 2 or href in seen:
                    continue
                name, competition = spans[0].strip(), spans[1].strip()
                if not DAZN_BOXING_RE.search(competition) or DAZN_SKIP_RE.search(name):
                    continue
                seen.add(href)
                candidates.append((href, name, competition, day_text))
            print(f"  DAZN: {len(tiles)} tiles, {len(candidates)} boxing events")

            for href, name, competition, day_text in candidates:
                start = None
                try:
                    page.goto(href, timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_selector("script[type='application/ld+json']", state="attached", timeout=15000)
                    blobs = page.eval_on_selector_all(
                        "script[type='application/ld+json']", "els => els.map(e => e.textContent)"
                    )
                    for blob in blobs:
                        data = json.loads(blob)
                        if data.get("@type") == "SportsEvent" and data.get("startDate"):
                            start = datetime.fromisoformat(data["startDate"].replace("Z", "+00:00"))
                            break
                except Exception as e:
                    print(f"  WARNING: DAZN event page unavailable for {name}: {e}")

                if start is None:
                    # Detail page not published yet (404) - use the schedule's day header
                    start = dazn_day_fallback(day_text)
                    if start is None:
                        print(f"  WARNING: no date for DAZN event {name}; skipping")
                        continue
                    print(f"  DAZN: {name} has no start time - defaulting to 9:00 PM CT")
                found.append({"name": name, "competition": competition, "start_utc": start})

            browser.close()
    except Exception as e:
        print(f"  WARNING: Error rendering DAZN schedule: {e}")
    return found


def parse_dazn(raw: list[dict], quiet: bool = False) -> dict[str, dict]:
    events = {}
    for item in raw:
        start_ct = item["start_utc"].astimezone(CT_ZONE)
        date_obj = datetime(start_ct.year, start_ct.month, start_ct.day)
        slug = make_slug(item["name"])
        events[slug] = {
            "name":     item["name"],
            "date_obj": date_obj,
            "location": "",
            "network":  "DAZN",
            "start_ct": start_ct,
            "fights":   [],
            "source":   "DAZN.com",
            "bs_slug":  "",
            "results":  [],
        }

    if not quiet:
        print(f"  DAZN: {len(events)} events parsed")
    return events


def load_previous_dazn(path: str) -> dict[str, dict]:
    """Read DAZN-sourced events back out of the last generated calendar."""
    events: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return events

    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold wrapped lines
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        fields = {}
        for line in block.strip().splitlines():
            key, _, value = line.partition(":")
            fields[key.split(";")[0]] = value
        if "Source: DAZN.com" not in fields.get("DESCRIPTION", ""):
            continue
        try:
            start_utc = datetime.strptime(fields["DTSTART"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        name = (fields.get("SUMMARY", "")
                .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))
        if name:
            events[make_slug(name)] = {"name": name, "start_utc": start_utc}
    return events


def carry_forward_dazn(fresh: dict[str, dict], path: str = "boxing_schedule.ics") -> dict[str, dict]:
    """DAZN only lists ~10 days and can fail to load from CI, so keep events the
    last run already found rather than letting them vanish from the calendar.

    - Scrape failed (nothing fresh): keep everything from the last 3 days onward.
    - Scrape worked: keep only events that have already started (they just fell
      off DAZN's window); a missing future event was cancelled or moved.
    """
    now = datetime.now(timezone.utc)
    scrape_ok = bool(fresh)  # decided up front: carrying events forward mutates `fresh`
    kept = 0
    for slug, prev in load_previous_dazn(path).items():
        if slug in fresh:
            continue
        recent = prev["start_utc"] >= now - timedelta(days=3)
        keep = recent and (prev["start_utc"] <= now if scrape_ok else True)
        if keep:
            fresh.update(parse_dazn([prev], quiet=True))
            kept += 1
    if kept:
        print(f"  DAZN: kept {kept} event(s) from the previous calendar")
    return fresh


# == Merge + build calendar =====================================================

SOURCE_RANK = {
    "DAZN.com": 1,
    "TeamBoxingLeague.com": 2, "ProBoxTV.com": 2,
    "BoxingScene.com": 3,
    "BoxingNews24.com": 4,
}


def drop_duplicate_events(merged: dict[str, dict]) -> None:
    """Sources name the same fight differently ("Whittaker vs. Wallace" vs
    "Ben Whittaker vs. Conor Wallace"), so their slugs don't match. Drop an
    "A vs. B" event when a higher-priority source lists both surnames within a
    day, keeping any fuller venue/network the dropped one had."""
    entries = [
        (slug, ev["date_obj"], SOURCE_RANK.get(ev["source"], 0), set(make_slug(ev["name"]).split("-")))
        for slug, ev in merged.items()
    ]
    for slug, ev in list(merged.items()):
        sides = re.split(r"\s+vs\.?\s+", ev["name"], flags=re.I)
        if len(sides) != 2:
            continue
        surnames = {make_slug(side).split("-")[-1] for side in sides if make_slug(side)}
        if len(surnames) != 2:
            continue
        rank = SOURCE_RANK.get(ev["source"], 0)
        for other_slug, date_obj, other_rank, tokens in entries:
            if other_rank > rank and abs((date_obj - ev["date_obj"]).days) <= 1 and surnames <= tokens:
                keep = merged[other_slug]
                if ev["location"] and (not keep["location"] or keep["location"] in ev["location"]):
                    keep["location"] = ev["location"]  # "Utilita Arena Sheffield" over "Sheffield"
                if ev["network"] and not keep["network"]:
                    keep["network"] = ev["network"]
                del merged[slug]
                break


def build_calendar(
    bn24: dict,
    bs: dict,
    tbl: dict,
    probox: dict,
    dazn: dict | None = None,
    past: dict | None = None,
) -> list[Event]:
    merged: dict[str, dict] = {}

    # Past events go in first (lowest priority — any live source overrides)
    if past:
        for slug, ev in past.items():
            merged[slug] = ev

    # Priority (lowest to highest): DAZN < TBL = ProBox < BS < BN24
    # (DAZN has exact start times but no venue, so richer sources override it)
    for slug, ev in (dazn or {}).items():
        merged[slug] = ev

    for slug, ev in tbl.items():
        merged[slug] = ev

    for slug, ev in probox.items():
        merged[slug] = ev

    for slug, ev in bs.items():
        # Preserve results if past dict had them for this slug
        existing_results = merged.get(slug, {}).get("results", [])
        merged[slug] = {**ev, "results": existing_results or ev.get("results", [])}

    # BN24 overrides BS but preserve bs_slug and results, and don't let a bare
    # "Broadcast unconfirmed" entry erase a time/network/venue another source has
    for slug, ev in bn24.items():
        prev = merged.get(slug, {})
        merged[slug] = {
            **ev,
            "bs_slug": prev.get("bs_slug", "") or ev.get("bs_slug", ""),
            "results": prev.get("results", []) or ev.get("results", []),
        }
        if prev and not ev.get("start_known", True):
            merged[slug]["start_ct"] = prev["start_ct"]
        if prev.get("network") and not ev["network"]:
            merged[slug]["network"] = prev["network"]
        if prev.get("location") and (not ev["location"] or ev["location"] in prev["location"]):
            merged[slug]["location"] = prev["location"]  # e.g. "Utilita Arena Sheffield" over "Sheffield"

    drop_duplicate_events(merged)

    events = []
    seen_uids: set[str] = set()

    for slug, ev in sorted(merged.items(), key=lambda x: x[1]["date_obj"]):
        start_ct  = ev["start_ct"]
        end_ct    = start_ct + timedelta(hours=5)
        start_utc = start_ct.astimezone(timezone.utc)
        end_utc   = end_ct.astimezone(timezone.utc)

        uid_date = ev["date_obj"].strftime("%Y%m%d")
        uid      = f"{uid_date}-{slug}@boxing-calendar"
        if uid in seen_uids:
            continue
        seen_uids.add(uid)

        fight_results: list[dict] = ev.get("results") or []

        cal_ev = Event()
        cal_ev.begin = start_utc
        cal_ev.end   = end_utc
        cal_ev.uid   = uid

        if fight_results:
            main_r = fight_results[0]
            cal_ev.name = f"{main_r['winner']} DEF. {main_r['loser']}"
            results_lines = "\n".join(
                f"  {f['winner']} DEF. {f['loser']}" + (f" - {f['method']}" if f["method"] else "")
                for f in fight_results
            )
            cal_ev.description = "\n".join(filter(None, [
                f"Date: {ev['date_obj'].strftime('%A, %B %d, %Y')}",
                f"Location: {ev['location']}" if ev["location"] else "",
                f"Network: {ev['network']}" if ev["network"] else "",
                f"Start: {start_ct.strftime('%I:%M %p CT')}",
                "",
                "Results:",
                results_lines,
                "",
                f"Source: {ev['source']}",
            ]))
            print(f"  + {uid_date} | {cal_ev.name[:45]} | RESULT [{ev['source'][:3]}]")
        else:
            undercard_str = "\n".join(f"  * {f}" for f in ev["fights"][1:])
            cal_ev.name = ev["name"]
            cal_ev.description = "\n".join(filter(None, [
                f"Date: {ev['date_obj'].strftime('%A, %B %d, %Y')}",
                f"Location: {ev['location']}" if ev["location"] else "",
                f"Network: {ev['network']}" if ev["network"] else "",
                f"Start: {start_ct.strftime('%I:%M %p CT')}",
                "",
                f"Main Event: {ev['fights'][0]}" if ev["fights"] else "",
                "",
                "Undercard:" if len(ev["fights"]) > 1 else "",
                undercard_str,
                "",
                f"Source: {ev['source']}",
            ]))
            print(f"  + {uid_date} | {ev['name'][:45]} | {start_ct.strftime('%I:%M %p CT')} | {ev['network'] or 'TBC'} [{ev['source'][:3]}]")

        events.append(cal_ev)

    return events


# == Entry point =================================================================

def main():
    print("Fetching BoxingNews24...")
    bn24_html = fetch(BN24_URL)

    print("Fetching BoxingScene main schedule (rendered, with Load More)...")
    bs_html = fetch_bs_rendered(BS_URL)

    print("Fetching BoxingScene extended schedule (Other networks)...")
    bs_ext_html = fetch_bs_rendered(BS_EXT_URL)

    print("Fetching Team Boxing League...")
    tbl_html = fetch(TBL_URL)

    print("Fetching ProBox TV...")
    probox_html = fetch(PROBOX_URL)

    print("Fetching DAZN schedule...")
    dazn_raw = fetch_dazn(DAZN_URL)

    print("Fetching BoxingScene past event results...")
    past = fetch_bs_past_events(BS_RESULTS_URL)

    bn24   = parse_bn24(bn24_html)                    if bn24_html    else {}
    bs     = parse_bs(bs_html, "BoxingScene main")    if bs_html      else {}
    bs_ext = parse_bs(bs_ext_html, "BoxingScene ext") if bs_ext_html  else {}
    bs.update(bs_ext)
    tbl    = parse_tbl(tbl_html)                      if tbl_html     else {}
    probox = parse_probox(probox_html)                if probox_html  else {}
    dazn   = carry_forward_dazn(parse_dazn(dazn_raw) if dazn_raw else {})

    if not bn24 and not bs and not tbl and not probox and not dazn and not past:
        print("ERROR: All sources failed - no events to write")
        sys.exit(1)

    if not bn24:   print("WARNING: BoxingNews24 failed - skipping")
    if not bs:     print("WARNING: BoxingScene failed - skipping")
    if not tbl:    print("WARNING: Team Boxing League failed - skipping")
    if not probox: print("WARNING: ProBox TV failed - skipping")
    if not dazn:   print("WARNING: DAZN failed - skipping")

    print("Merging and building calendar...")
    events = build_calendar(bn24, bs, tbl, probox, dazn, past)

    if not events:
        print("WARNING: No events parsed from any source")

    cal = Calendar()
    cal.extra.append(ContentLine(name="CALSCALE", value="GREGORIAN"))
    cal.extra.append(ContentLine(name="COMMENT", value="Data from BoxingNews24.com + BoxingScene.com + TeamBoxingLeague.com + ProBoxTV.com + DAZN.com"))
    for ev in events:
        cal.events.add(ev)

    output = "boxing_schedule.ics"
    with open(output, "w", encoding="utf-8") as f:
        f.writelines(cal)

    print(f"\nDone - {len(events)} events written to {output}")


if __name__ == "__main__":
    main()

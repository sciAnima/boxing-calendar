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
from playwright.sync_api import sync_playwright

BN24_URL = "https://www.boxingnews24.com/boxing-schedule/"
BS_URL   = "https://www.boxingscene.com/schedule"

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

ET_RE  = re.compile(r"USA ET:\s*(\d{1,2}:\d{2}\s*(?:AM|PM))", re.I)
NET_RE = re.compile(r"live on\s*(.+)", re.I)

BS_DT_RE = re.compile(
    r"\w+,\s+(\w+)\s+(\d{1,2}),\s+(\d{4})\s+-\s+(\d{1,2}):(\d{2})\s+(AM|PM)\s+(\w+)",
    re.I
)

TZ_MAP = {
    "EST": "America/New_York", "EDT": "America/New_York",
    "CST": "America/Chicago",  "CDT": "America/Chicago",
    "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    "GMT": "Europe/London",    "BST": "Europe/London",
}


def fetch(url: str) -> str | None:
    """Fetch a URL and return HTML. Returns None on any error instead of crashing."""
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
    """
    BoxingScene only server-renders the first page of the schedule; later
    events (including ones further out, e.g. late August) only appear after
    repeatedly clicking "Load more events", which fires a React Server
    Action with no plain-HTTP equivalent. Drive a real headless browser and
    click through until no further events load.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                user_agent=USER_AGENTS[datetime.now().day % len(USER_AGENTS)],
                viewport={"width": 1366, "height": 2000},
            )
            page.goto(url, timeout=45000, wait_until="networkidle")

            def link_count() -> int:
                return page.eval_on_selector_all(
                    "a[href*='/events/']", "els => els.length"
                )

            prev_count = link_count()
            print(f"  BoxingScene: {prev_count} events visible before Load More")

            for click_num in range(1, max_clicks + 1):
                load_more = page.get_by_role("button", name=re.compile("load more", re.I))
                try:
                    load_more.first.wait_for(state="visible", timeout=4000)
                except Exception:
                    print(f"  BoxingScene: Load More button gone after {click_num - 1} click(s)")
                    break

                load_more.first.scroll_into_view_if_needed(timeout=4000)
                try:
                    load_more.first.click(timeout=5000)
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

            html = page.content()
            browser.close()
            print(f"  HTTP 200 (rendered, {prev_count} events) - {url}")
            return html
    except Exception as e:
        print(f"  WARNING: Error rendering {url} with Playwright: {e}")
        return None


def strip_emoji(s: str) -> str:
    return re.sub(r"[^\x20-\x7EÀ-ÖØ-öø-ÿ''\-\.,/|: ]", "", s).strip()


def make_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_et_time(time_str: str, date_obj: datetime) -> datetime | None:
    time_str = re.sub(r"\s+", "", time_str).upper()
    try:
        dt = datetime.strptime(
            f"{date_obj.month}/{date_obj.day}/{date_obj.year} {time_str}",
            "%m/%d/%Y %I:%M%p"
        )
        return dt.replace(tzinfo=ET_ZONE).astimezone(CT_ZONE)
    except ValueError:
        return None


# == Source 1: BoxingNews24 ====================================================

def parse_bn24(html: str) -> dict[str, dict]:
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find("div", class_=re.compile(r"entry|post|content|article", re.I))
    if not content:
        content = soup

    for a in content.find_all("a"):
        a.replace_with(a.get_text())

    lines = content.get_text(separator="\n").split("\n")
    lines = [l.strip() for l in lines if l.strip()]

    current_year = datetime.now(CT_ZONE).year
    month_pattern = "|".join(MONTHS.keys())
    events = {}

    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(rf"^({month_pattern})\s+(\d{{1,2}}):\s*(.+)", line, re.I)
        if m:
            month_name = m.group(1).capitalize()
            day        = int(m.group(2))
            rest       = m.group(3)
            month_num  = MONTHS.get(month_name)
            if not month_num:
                i += 1
                continue

            date_obj = datetime(current_year, month_num, day)
            location = strip_emoji(rest.split("|")[0]).strip()

            et_time_str = None
            et_m = ET_RE.search(rest)
            if et_m:
                et_time_str = et_m.group(1).strip()

            network = ""
            net_m = NET_RE.search(rest)
            if net_m:
                network = strip_emoji(net_m.group(1)).strip()

            fight_lines = []
            i += 1
            while i < len(lines):
                nl = lines[i]
                if re.match(rf"^({month_pattern})\s+\d{{1,2}}:", nl, re.I):
                    break
                if nl.startswith("\U0001F4CC"):
                    ft = strip_emoji(nl[1:]).strip()
                    if ft:
                        fight_lines.append(ft)
                i += 1

            if not fight_lines:
                continue

            main_event = fight_lines[0]
            main_name  = re.sub(r",\s*\d+\s*rounds.*$", "", main_event, flags=re.I).strip()
            slug       = make_slug(main_name)

            start_ct = parse_et_time(et_time_str, date_obj) if et_time_str else None
            if not start_ct:
                start_ct = datetime(date_obj.year, date_obj.month, date_obj.day, 21, 0, tzinfo=CT_ZONE)

            events[slug] = {
                "name":     main_name,
                "date_obj": date_obj,
                "location": location,
                "network":  network,
                "start_ct": start_ct,
                "fights":   fight_lines,
                "source":   "BoxingNews24.com",
            }
        else:
            i += 1

    print(f"  BoxingNews24: {len(events)} events parsed")
    return events


# == Source 2: BoxingScene ======================================================

def parse_bs(html: str) -> dict[str, dict]:
    soup = BeautifulSoup(html, "html.parser")
    events = {}

    for a in soup.find_all("a", href=re.compile(r"/events/")):
        text = a.get_text(separator=" | ").strip()
        if not re.search(r"\bvs\.?\b", text, re.I):
            continue
        if not BS_DT_RE.search(text):
            continue

        parts = [p.strip() for p in text.split("|") if p.strip()]
        if not parts:
            continue

        fight_name = parts[0].strip()

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
        h, mn, ampm, tz_abbr = int(dm.group(4)), int(dm.group(5)), dm.group(6), dm.group(7)
        tz_name = TZ_MAP.get(tz_abbr.upper(), "America/New_York")

        try:
            dt_str   = f"{month_full} {day} {year} {h}:{mn:02d} {ampm.upper()}"
            dt_parsed = datetime.strptime(dt_str, "%B %d %Y %I:%M %p")
            start_ct  = dt_parsed.replace(tzinfo=ZoneInfo(tz_name)).astimezone(CT_ZONE)
        except ValueError:
            continue

        date_obj = datetime(year, month_num, day)
        slug = make_slug(fight_name)

        events[slug] = {
            "name":     fight_name,
            "date_obj": date_obj,
            "location": venue,
            "network":  network,
            "start_ct": start_ct,
            "fights":   [],
            "source":   "BoxingScene.com",
        }

    print(f"  BoxingScene: {len(events)} events parsed")
    return events


# == Merge + build calendar =====================================================

def build_calendar(bn24: dict, bs: dict) -> list[Event]:
    merged = {}

    for slug, ev in bs.items():
        merged[slug] = ev

    for slug, ev in bn24.items():
        merged[slug] = ev

    events = []
    seen_uids = set()

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

        undercard_str = "\n".join(f"  * {f}" for f in ev["fights"][1:])

        cal_ev = Event()
        cal_ev.name  = ev["name"]
        cal_ev.begin = start_utc
        cal_ev.end   = end_utc
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
        cal_ev.uid = uid

        events.append(cal_ev)
        print(f"  + {uid_date} | {ev['name'][:45]} | {start_ct.strftime('%I:%M %p CT')} | {ev['network'] or 'TBC'} [{ev['source'][:3]}]")

    return events


# == Entry point =================================================================

def main():
    print("Fetching BoxingNews24...")
    bn24_html = fetch(BN24_URL)

    print("Fetching BoxingScene (rendered, with Load More)...")
    bs_html = fetch_bs_rendered(BS_URL)

    # Parse whatever we got - gracefully skip failed sources
    bn24 = parse_bn24(bn24_html) if bn24_html else {}
    bs   = parse_bs(bs_html)     if bs_html   else {}

    if not bn24 and not bs:
        print("ERROR: Both sources failed - no events to write")
        sys.exit(1)

    if not bn24:
        print("WARNING: BoxingNews24 failed - using BoxingScene only")
    if not bs:
        print("WARNING: BoxingScene failed - using BoxingNews24 only")

    print("Merging and building calendar...")
    events = build_calendar(bn24, bs)

    if not events:
        print("WARNING: No events parsed from either source")

    cal = Calendar()
    cal.extra.append(ContentLine(name="CALSCALE", value="GREGORIAN"))
    cal.extra.append(ContentLine(name="COMMENT", value="Data from BoxingNews24.com + BoxingScene.com"))
    for ev in events:
        cal.events.add(ev)

    output = "boxing_schedule.ics"
    with open(output, "w", encoding="utf-8") as f:
        f.writelines(cal)

    print(f"\nDone - {len(events)} events written to {output}")


if __name__ == "__main__":
    main()

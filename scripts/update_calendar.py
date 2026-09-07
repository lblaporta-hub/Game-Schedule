#!/usr/bin/env python3

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup


# ============================================================
# PATHS / CONFIG
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

CONFIG_FILE = ROOT / "config" / "competitions.json"

config = json.loads(
    CONFIG_FILE.read_text(encoding="utf-8")
)

CFG = config["calendar"]

OUTPUT_DIR = ROOT / CFG.get(
    "output_dir",
    "calendars"
)

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# SETTINGS
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/140 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
}

TIMEZONE = CFG.get(
    "timezone",
    "Europe/Lisbon"
)

TEAM_CALENDAR_URL = (
    "https://www.fpb.pt/calendario/clube_68/"
)

FPB_AJAX_URL = (
    "https://www.fpb.pt/wp-admin/admin-ajax.php"
)


MONTHS = {
    "JAN": 1,
    "FEV": 2,
    "MAR": 3,
    "ABR": 4,
    "MAI": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SET": 9,
    "OUT": 10,
    "NOV": 11,
    "DEZ": 12,
}


TIME_RE = re.compile(
    r"\b(\d{1,2})H(\d{2})\b",
    re.IGNORECASE
)

DATE_RE = re.compile(
    r"^(\d{1,2})\s+([A-ZÇ]+)\s+(\d{4})$",
    re.IGNORECASE
)


# ============================================================
# TEXT HELPERS
# ============================================================

def clean(text):
    return re.sub(
        r"\s+",
        " ",
        (text or "").replace("\xa0", " ")
    ).strip()


def escape_ics(text):
    return (
        clean(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def is_gdessa(text):
    text = clean(text).casefold()

    aliases = CFG.get(
        "team_aliases",
        ["GDESSA Barreiro", "GDESSA"]
    )

    return any(
        alias.casefold() in text
        for alias in aliases
    )


# ============================================================
# DATE
# ============================================================

def parse_date(text):

    text = clean(text).upper()

    match = DATE_RE.match(text)

    if not match:
        return None

    day = int(match.group(1))
    month_name = match.group(2)
    year = int(match.group(3))

    month = MONTHS.get(month_name)

    if not month:
        return None

    try:
        return datetime(
            year,
            month,
            day
        )
    except ValueError:
        return None


def parse_iso_date(value):

    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d"
        )
    except (ValueError, TypeError):
        return None


# ============================================================
# TIME
# ============================================================

def parse_time(text):

    match = TIME_RE.search(
        text or ""
    )

    if not match:
        return None

    return (
        f"{int(match.group(1)):02d}:"
        f"{match.group(2)}"
    )


# ============================================================
# TEAM NAME NORMALIZATION
# ============================================================

def collapse_duplicate(text):

    text = clean(text)

    words = text.split()

    if len(words) < 2:
        return text

    if len(words) % 2 == 0:

        half = len(words) // 2

        first = " ".join(
            words[:half]
        )

        second = " ".join(
            words[half:]
        )

        if first.casefold() == second.casefold():
            return first

    return text


def normalize_team_name(text):

    text = clean(text)

    text = collapse_duplicate(text)

    return text


# ============================================================
# COMPETITION DETECTION
# ============================================================

def detect_competition(text):

    t = clean(text).casefold()

    rules = {

        "liga-betclic": [
            "liga betclic feminina",
        ],

        "preparacao": [
            "jogos preparação femininos",
            "jogos de preparação femininos",
        ],

        "supertaca": [
            "supertaça feminina",
            "supertaça",
        ],

        "taca-portugal": [
            "taça de portugal feminina",
            "taça de portugal",
        ],

        "2-divisao": [
            "2ª divisão feminina",
            "2.ª divisão feminina",
            "2a divisão feminina",
            "2 divisão feminina",
        ],

        "sub18": [
            "campeonato nacional sub18 femininos",
            "campeonato nacional sub18",
            "sub 18 feminino",
            "sub18 feminino",
        ],
    }

    for competition_id, keywords in rules.items():

        for keyword in keywords:

            if keyword in t:
                return competition_id

    return None


# ============================================================
# COMPETITION METADATA
# ============================================================

def competition_by_id():

    return {
        comp["id"]: comp
        for comp in CFG["competitions"]
    }


# ============================================================
# COMPETITION NUMBER FROM FPB URL
# ============================================================

def get_fpb_competition_id(url):

    try:

        parsed = urlparse(url)

        query = parse_qs(
            parsed.query
        )

        values = query.get(
            "competicao"
        )

        if values:
            return values[0]

    except Exception:
        pass

    return None


# ============================================================
# SEASON END
# ============================================================

def get_season_end_date():

    season = str(
        CFG.get(
            "season",
            "2026/2027"
        )
    )

    match = re.search(
        r"(\d{4})\s*[/\-]\s*(\d{2,4})",
        season
    )

    if match:

        first_year = int(
            match.group(1)
        )

        second_part = match.group(2)

        if len(second_part) == 2:

            second_year = (
                (first_year // 100) * 100
                + int(second_part)
            )

        else:

            second_year = int(
                second_part
            )

        return datetime(
            second_year,
            6,
            30
        )

    # Safe fallback
    return datetime.now() + timedelta(
        days=365
    )


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    HEADERS
)


def get_response(
    url,
    params=None,
    headers=None
):

    print(
        f"FETCH: {url}"
    )

    response = SESSION.get(
        url,
        params=params,
        headers=headers,
        timeout=45
    )

    response.raise_for_status()

    response.encoding = (
        response.apparent_encoding
        or "utf-8"
    )

    return response


def get_soup(url):

    response = get_response(
        url
    )

    return BeautifulSoup(
        response.text,
        "html.parser"
    )


# ============================================================
# EXTRACT FIXTURE FROM FPB ANCHOR
# ============================================================

def parse_fixture(anchor_text):

    text = clean(anchor_text)

    if not is_gdessa(text):
        return None

    has_time = bool(
        TIME_RE.search(text)
    )

    has_status = bool(
        re.search(
            r"\b(a definir|a indicar|adiado)\b",
            text,
            re.IGNORECASE
        )
    )

    if not has_time and not has_status:
        return None

    # --------------------------------------------------------
    # Competition
    # --------------------------------------------------------

    competition_id = detect_competition(
        text
    )

    if not competition_id:
        return None

    # --------------------------------------------------------
    # Remove competition suffix
    # --------------------------------------------------------

    fixture_part = text

    competition_markers = [
        "Sénior Feminino |",
        "Sub 18 Feminino |",
        "Sub 18 F |",
    ]

    for marker in competition_markers:

        if marker in fixture_part:

            fixture_part = fixture_part.split(
                marker,
                1
            )[0]

            break

    fixture_part = clean(
        fixture_part
    )

    # --------------------------------------------------------
    # Find time/status
    # --------------------------------------------------------

    time_match = TIME_RE.search(
        fixture_part
    )

    status_match = re.search(
        r"\b(a definir|a indicar|adiado)\b",
        fixture_part,
        re.IGNORECASE
    )

    if time_match:

        separator = time_match

        home_raw = clean(
            fixture_part[
                :separator.start()
            ]
        )

        remaining = clean(
            fixture_part[
                separator.end():
            ]
        )

        game_time = parse_time(
            separator.group(0)
        )

    elif status_match:

        separator = status_match

        home_raw = clean(
            fixture_part[
                :separator.start()
            ]
        )

        remaining = clean(
            fixture_part[
                separator.end():
            ]
        )

        game_time = None

    else:

        return None

    # --------------------------------------------------------
    # Venue detection
    # --------------------------------------------------------

    venue_markers = [
        "Pavilhão ",
        "Pav. ",
        "Complexo ",
        "Arena ",
        "Nave ",
        "Esc Sec ",
        "Escola Secundária ",
        "Colégio ",
        "Pavilhao ",
    ]

    venue_position = None

    for marker in venue_markers:

        position = remaining.find(
            marker
        )

        if position >= 0:

            if (
                venue_position is None
                or position < venue_position
            ):
                venue_position = position

    if venue_position is not None:

        away_part = clean(
            remaining[
                :venue_position
            ]
        )

        venue = clean(
            remaining[
                venue_position:
            ]
        )

    else:

        away_part = clean(
            remaining
        )

        venue = ""

    home = normalize_team_name(
        home_raw
    )

    away = normalize_team_name(
        away_part
    )

    if not home or not away:
        return None

    # Remove accidental competition/status leftovers.
    away = re.sub(
        r"\s+(Sénior|Sub)\s+.*$",
        "",
        away,
        flags=re.IGNORECASE
    )

    away = normalize_team_name(
        away
    )

    return {
        "competition_id": competition_id,
        "home": home,
        "away": away,
        "time": game_time,
        "venue": venue,
    }


# ============================================================
# EXTRACT CALENDAR HTML
# ============================================================

def extract_calendar_html(
    html,
    source_url,
    forced_competition=None
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    events = []

    current_date = None

    # --------------------------------------------------------
    # The FPB returns calendar blocks containing:
    #
    # <h3>5 OUT 2026</h3>
    #
    # followed by <a> game blocks.
    # --------------------------------------------------------

    for element in soup.find_all(
        ["h3", "a"]
    ):

        # ----------------------------------------------------
        # DATE
        # ----------------------------------------------------

        if element.name == "h3":

            date = parse_date(
                element.get_text(
                    " ",
                    strip=True
                )
            )

            if date:

                current_date = date

            continue

        # ----------------------------------------------------
        # GAME
        # ----------------------------------------------------

        if current_date is None:
            continue

        href = element.get(
            "href"
        )

        if not href:
            continue

        text = element.get_text(
            " ",
            strip=True
        )

        fixture = parse_fixture(
            text
        )

        if not fixture:
            continue

        if forced_competition:

            fixture[
                "competition_id"
            ] = forced_competition

        fixture[
            "date"
        ] = current_date.strftime(
            "%Y-%m-%d"
        )

        fixture[
            "source"
        ] = source_url

        fixture[
            "source_href"
        ] = urljoin(
            source_url,
            href
        )

        events.append(
            fixture
        )

    return deduplicate(
        events
    )


# ============================================================
# EXTRACT INITIAL CALENDAR PAGE
# ============================================================

def extract_calendar_page(
    url,
    forced_competition=None
):

    soup = get_soup(
        url
    )

    return extract_calendar_html(
        str(soup),
        url,
        forced_competition
    )


# ============================================================
# FPB AJAX LOAD MORE
# ============================================================

def fetch_ajax_period(
    competition_number,
    from_date,
    to_date,
    source_url,
    forced_competition=None
):

    params = [
        (
            "action",
            "get_more_days"
        ),
        (
            "competicao[]",
            str(competition_number)
        ),
        (
            "period[time_option]",
            "loadmore"
        ),
        (
            "period[from_date]",
            from_date.strftime(
                "%Y/%m/%d"
            )
        ),
        (
            "period[to_date]",
            to_date.strftime(
                "%Y/%m/%d"
            )
        ),
    ]

    print(
        "AJAX:",
        from_date.strftime("%Y-%m-%d"),
        "->",
        to_date.strftime("%Y-%m-%d"),
        "| competition",
        competition_number
    )

    headers = {
        "Referer": source_url,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
    }

    response = get_response(
        FPB_AJAX_URL,
        params=params,
        headers=headers
    )

    # --------------------------------------------------------
    # FPB returns JSON:
    #
    # {
    #     "result": [
    #         "HTML..."
    #     ]
    # }
    # --------------------------------------------------------

    try:

        payload = response.json()

    except ValueError:

        print(
            "WARNING: AJAX response was not JSON"
        )

        return []

    result = payload.get(
        "result"
    )

    if not result:
        return []

    if isinstance(
        result,
        list
    ):

        html = "\n".join(
            str(item)
            for item in result
        )

    else:

        html = str(
            result
        )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # WordPress AJAX may escape HTML as:
    #
    # <\/h3>
    #
    # Restore it before BeautifulSoup.
    # --------------------------------------------------------

    html = html.replace(
        r"\/",
        "/"
    )

    events = extract_calendar_html(
        html,
        source_url,
        forced_competition
    )

    print(
        f"AJAX RESULT: {len(events)} GDESSA events"
    )

    return events


# ============================================================
# LOAD ALL COMPETITION GAMES
# ============================================================

def extract_full_competition_calendar(
    url,
    forced_competition=None
):

    # --------------------------------------------------------
    # 1. Load initial page
    # --------------------------------------------------------

    initial_events = extract_calendar_page(
        url,
        forced_competition
    )

    print(
        f"INITIAL PAGE: {len(initial_events)} events"
    )

    all_events = list(
        initial_events
    )

    # --------------------------------------------------------
    # 2. Find FPB numeric competition ID
    # --------------------------------------------------------

    competition_number = (
        get_fpb_competition_id(
            url
        )
    )

    if not competition_number:

        print(
            "NO FPB COMPETITION ID:",
            url
        )

        return deduplicate(
            all_events
        )

    # --------------------------------------------------------
    # 3. Find first date not covered by initial HTML
    # --------------------------------------------------------

    dates = [
        parse_iso_date(
            event["date"]
        )
        for event in initial_events
        if event.get("date")
    ]

    dates = [
        date
        for date in dates
        if date is not None
    ]

    if dates:

        cursor = max(dates) + timedelta(
            days=1
        )

    else:

        # Safe fallback for the season.
        cursor = datetime(
            datetime.now().year,
            7,
            1
        )

    season_end = get_season_end_date()

    # --------------------------------------------------------
    # 4. Load future games in 30-day chunks
    #
    # This reproduces the FPB "scroll/load more" behaviour.
    # --------------------------------------------------------

    while cursor <= season_end:

        period_end = min(
            cursor + timedelta(days=29),
            season_end
        )

        try:

            ajax_events = fetch_ajax_period(
                competition_number,
                cursor,
                period_end,
                url,
                forced_competition
            )

            all_events.extend(
                ajax_events
            )

        except Exception as error:

            print(
                "ERROR AJAX PERIOD:",
                cursor.strftime("%Y-%m-%d"),
                "->",
                period_end.strftime("%Y-%m-%d"),
                error
            )

        cursor = (
            period_end
            + timedelta(days=1)
        )

    return deduplicate(
        all_events
    )


# ============================================================
# DEDUPLICATION
# ============================================================

def event_key(event):

    return (
        event["competition_id"],
        event["date"],
        event["home"].casefold(),
        event["away"].casefold(),
    )


def deduplicate(events):

    result = {}

    for event in events:

        result[
            event_key(event)
        ] = event

    return list(
        result.values()
    )


# ============================================================
# UID
# ============================================================

def event_uid(event):

    raw = "|".join([
        event["competition_id"],
        event["date"],
        event["home"],
        event["away"],
    ])

    digest = hashlib.sha1(
        raw.casefold().encode(
            "utf-8"
        )
    ).hexdigest()

    return (
        f"{digest[:20]}@gdessa"
    )


# ============================================================
# ICS GENERATION
# ============================================================

def generate_ics(
    events,
    calendar_name
):

    timestamp = datetime.utcnow().strftime(
        "%Y%m%dT%H%M%SZ"
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{escape_ics(CFG['prodid'])}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_ics(calendar_name)}",
        f"X-WR-TIMEZONE:{TIMEZONE}",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D",
        "X-PUBLISHED-TTL:P1D",
    ]

    for event in sorted(
        events,
        key=lambda e: (
            e["date"],
            e.get("time") or "",
        )
    ):

        gdessa_home = is_gdessa(
            event["home"]
        )

        location_icon = (
            "🏠"
            if gdessa_home
            else "🚌"
        )

        if event.get("time"):

            summary = (
                f"{location_icon} "
                f"{event['home']} vs "
                f"{event['away']} — "
                f"{event['time']}"
            )

        else:

            summary = (
                f"{location_icon} "
                f"{event['home']} vs "
                f"{event['away']}"
            )

        description = (
            f"GDESSA Barreiro\\n"
            f"Competição: "
            f"{event['competition_id']}\\n"
            f"Época: {CFG['season']}\\n"
            f"FPB: {event['source_href']}"
        )

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{event_uid(event)}",
            f"DTSTAMP:{timestamp}",
        ])

        # ----------------------------------------------------
        # TIMED
        # ----------------------------------------------------

        if event.get("time"):

            start = datetime.fromisoformat(
                f"{event['date']}T{event['time']}"
            )

            end = (
                start
                + timedelta(hours=2)
            )

            lines.extend([
                (
                    f"DTSTART;TZID={TIMEZONE}:"
                    f"{start:%Y%m%dT%H%M%S}"
                ),
                (
                    f"DTEND;TZID={TIMEZONE}:"
                    f"{end:%Y%m%dT%H%M%S}"
                ),
            ])

        # ----------------------------------------------------
        # ALL DAY
        # ----------------------------------------------------

        else:

            start = datetime.fromisoformat(
                event["date"]
            )

            end = (
                start
                + timedelta(days=1)
            )

            lines.extend([
                (
                    "DTSTART;VALUE=DATE:"
                    f"{start:%Y%m%d}"
                ),
                (
                    "DTEND;VALUE=DATE:"
                    f"{end:%Y%m%d}"
                ),
            ])

        lines.append(
            f"SUMMARY:{escape_ics(summary)}"
        )

        if event.get("venue"):

            lines.append(
                "LOCATION:"
                + escape_ics(
                    event["venue"]
                )
            )

        lines.append(
            "DESCRIPTION:"
            + escape_ics(
                description
            )
        )

        lines.append(
            "URL:"
            + escape_ics(
                event["source_href"]
            )
        )

        lines.append(
            "END:VEVENT"
        )

    lines.append(
        "END:VCALENDAR"
    )

    return "\r\n".join(
        lines
    ) + "\r\n"


# ============================================================
# MAIN
# ============================================================

def main():

    competitions = (
        CFG["competitions"]
    )

    active = [
        comp
        for comp in competitions
        if comp.get("active")
    ]

    metadata = competition_by_id()

    all_events = []

    # --------------------------------------------------------
    # 1. INDIVIDUAL COMPETITION PAGES
    # --------------------------------------------------------

    for comp in active:

        url = comp.get("url")

        if not url:

            print(
                f"NO URL: {comp['name']}"
            )

            continue

        try:

            events = extract_full_competition_calendar(
                url,
                forced_competition=comp["id"]
            )

            print(
                f"{comp['name']}: "
                f"{len(events)} events"
            )

            all_events.extend(
                events
            )

        except Exception as error:

            print(
                f"ERROR {comp['name']}: "
                f"{error}"
            )

    # --------------------------------------------------------
    # 2. GDESSA TEAM CALENDAR
    #
    # Keep this as an additional source.
    # Individual competition pages are now the primary source.
    # --------------------------------------------------------

    try:

        team_events = extract_calendar_page(
            TEAM_CALENDAR_URL
        )

        print(
            f"GDESSA team calendar: "
            f"{len(team_events)} events"
        )

        for event in team_events:

            competition_id = (
                event["competition_id"]
            )

            comp = metadata.get(
                competition_id
            )

            if not comp:

                print(
                    "UNKNOWN COMPETITION:",
                    competition_id
                )

                continue

            if not comp.get("active"):

                print(
                    "SKIP inactive:",
                    comp["name"]
                )

                continue

            all_events.append(
                event
            )

    except Exception as error:

        print(
            "ERROR TEAM CALENDAR:",
            error
        )

    # --------------------------------------------------------
    # 3. DEDUPLICATE
    # --------------------------------------------------------

    all_events = deduplicate(
        all_events
    )

    # --------------------------------------------------------
    # 4. PRINT EVERY EVENT
    # --------------------------------------------------------

    print("")
    print(
        "================================"
    )
    print(
        f"TOTAL EVENTS: {len(all_events)}"
    )
    print(
        "================================"
    )

    for event in sorted(
        all_events,
        key=lambda e: (
            e["date"],
            e.get("time") or ""
        )
    ):

        print(
            event["date"],
            event.get("time") or "TBD",
            "|",
            event["home"],
            "vs",
            event["away"],
            "|",
            event["competition_id"]
        )

    print(
        "================================"
    )

    # --------------------------------------------------------
    # 5. SAFETY CHECK
    # --------------------------------------------------------

    if not all_events:

        raise RuntimeError(
            "NO EVENTS FOUND. "
            "Existing ICS files were NOT overwritten."
        )

    # --------------------------------------------------------
    # 6. INDIVIDUAL FEEDS
    # --------------------------------------------------------

    for comp in active:

        comp_events = [
            event
            for event in all_events
            if event[
                "competition_id"
            ] == comp["id"]
        ]

        if not comp_events:

            print(
                f"NO EVENTS FOR: "
                f"{comp['id']}"
            )

            continue

        output_file = (
            OUTPUT_DIR
            / f"{comp['id']}.ics"
        )

        output_file.write_text(
            generate_ics(
                comp_events,
                (
                    f"{CFG['name']} — "
                    f"{comp['short_name']}"
                )
            ),
            encoding="utf-8"
        )

        print(
            f"FEED: "
            f"{output_file.name} "
            f"({len(comp_events)} events)"
        )

    # --------------------------------------------------------
    # 7. MASTER FEED
    # --------------------------------------------------------

    master = generate_ics(
        all_events,
        f"{CFG['name']} — All Competitions"
    )

    master_file = (
        OUTPUT_DIR
        / "gdessa.ics"
    )

    master_file.write_text(
        master,
        encoding="utf-8"
    )

    # Root compatibility copy
    root_file = (
        ROOT / "gdessa.ics"
    )

    root_file.write_text(
        master,
        encoding="utf-8"
    )

    print(
        f"MASTER: {len(all_events)} events"
    )

    print(
        "Calendar update completed successfully."
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()

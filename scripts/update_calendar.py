#!/usr/bin/env python3

import hashlib
import html
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

CONFIG_FILE = ROOT / "config" / "competitions.json"

CFG = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))["calendar"]

OUT = ROOT / CFG.get("output_dir", "calendars")
OUT.mkdir(parents=True, exist_ok=True)


# Official GDESSA FPB team calendar.
# This is used as an additional source because it contains
# GDESSA's fixtures across competitions.
TEAM_CALENDAR_URL = "https://www.fpb.pt/calendario/clube_68/"


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; GDESSA-Calendar/3.0; "
        "+https://github.com/lblaporta-hub/Game-Schedule)"
    )
}


# Portuguese FPB month abbreviations
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
    r"\b([01]?\d|2[0-3])H([0-5]\d)\b",
    re.IGNORECASE,
)


DATE_RE = re.compile(
    r"^\s*(\d{1,2})\s+([A-ZÇ]+)\s+(\d{4})\s*$",
    re.IGNORECASE,
)


STATUS_RE = re.compile(
    r"\b(a definir|a indicar|adiado)\b",
    re.IGNORECASE,
)


# ============================================================
# BASIC HELPERS
# ============================================================

def clean(text):
    """
    Normalize whitespace and HTML entities.
    """
    return re.sub(
        r"\s+",
        " ",
        html.unescape(text or "")
    ).strip()


def esc(text):
    """
    Escape text for iCalendar.
    """
    return (
        clean(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fetch(url):
    """
    Download and parse an FPB page.
    """
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    response.encoding = (
        response.apparent_encoding
        or response.encoding
    )

    return BeautifulSoup(
        response.text,
        "html.parser"
    )


# ============================================================
# DATE / TIME
# ============================================================

def parse_date(text):
    """
    Parse FPB date headings such as:

        5 OUT 2026
        1 DEZ 2026
        6 FEV 2027
    """

    match = DATE_RE.match(clean(text))

    if not match:
        return None

    month = match.group(2).upper()

    if month not in MONTHS:
        return None

    return datetime(
        int(match.group(3)),
        MONTHS[month],
        int(match.group(1)),
    )


def parse_time(text):
    """
    Convert FPB time:

        11H30 -> 11:30
        17H00 -> 17:00

    Returns None when the time is not defined.
    """

    match = TIME_RE.search(text or "")

    if not match:
        return None

    return (
        f"{match.group(1).zfill(2)}:"
        f"{match.group(2)}"
    )


# ============================================================
# TEAM IDENTIFICATION
# ============================================================

def is_gdessa(text):
    """
    Check whether a fixture contains GDESSA.
    """

    text = clean(text).casefold()

    return any(
        alias.casefold() in text
        for alias in CFG["team_aliases"]
    )


# ============================================================
# TEAM NAME PARSING
# ============================================================

def remove_duplicate_team_name(text):
    """
    FPB sometimes renders a team twice:

        GDESSA Barreiro GDESSA Barreiro

    Convert it to:

        GDESSA Barreiro
    """

    words = text.split()

    if len(words) < 2:
        return text

    if len(words) % 2 == 0:
        half = len(words) // 2

        first = " ".join(words[:half])
        second = " ".join(words[half:])

        if first.casefold() == second.casefold():
            return first

    return text


def split_teams(text):
    """
    Extract:

        home
        away
        venue

    from the combined FPB fixture text.
    """

    text = clean(text)

    # Remove competition/team category information
    # from the end of the fixture.
    markers = [
        "Sénior Feminino |",
        "Sénior Masculino |",
        "Sub 18 Feminino |",
        "Sub 18 F |",
        "Sub 18 Feminino",
        "Sub 18 F",
        "Jogos Preparação Femininos",
        "Jogos de Preparação Femininos",
    ]

    for marker in markers:
        if marker in text:
            text = text.split(marker, 1)[0].strip()
            break

    # Remove the time or status from the middle.
    time_match = TIME_RE.search(text)

    if time_match:
        left = clean(text[:time_match.start()])
        right = clean(text[time_match.end():])
    else:
        status_match = STATUS_RE.search(text)

        if not status_match:
            return None, None, None

        left = clean(text[:status_match.start()])
        right = clean(text[status_match.end():])

    home = remove_duplicate_team_name(left)

    # Known FPB venue prefixes.
    venue_prefixes = [
        "Pavilhão ",
        "Pav. ",
        "Pav Multiusos",
        "Complexo ",
        "Nave ",
        "Arena ",
        "Colégio ",
        "Escola Secundária ",
        "Esc Sec ",
    ]

    positions = []

    for prefix in venue_prefixes:
        position = right.find(prefix)

        if position > 0:
            positions.append(position)

    if positions:
        venue_position = min(positions)

        away = remove_duplicate_team_name(
            clean(right[:venue_position])
        )

        venue = clean(
            right[venue_position:]
        )

    else:
        away = remove_duplicate_team_name(right)
        venue = ""

    return home, away, venue


# ============================================================
# COMPETITION DETECTION
# ============================================================

def competition_text(text):
    """
    Return the competition portion of an FPB fixture.
    """

    text = clean(text)

    markers = [
        "Sénior Feminino |",
        "Sénior Masculino |",
        "Sub 18 Feminino |",
        "Sub 18 F |",
        "Sub 18 Feminino",
        "Sub 18 F",
    ]

    for marker in markers:
        if marker in text:
            return clean(
                text.split(marker, 1)[1]
            )

    return ""


def normalise_competition_name(text):
    """
    Normalize competition text for matching.
    """

    text = clean(text).casefold()

    replacements = {
        "supertaça feminina": "supertaca",
        "supertaça": "supertaca",
        "liga betclic feminina": "liga-betclic",
        "jogos preparação femininos": "preparacao",
        "jogos de preparação femininos": "preparacao",
        "campeonato nacional sub18 femininos": "sub18",
        "campeonato nacional sub 18 femininos": "sub18",
        "taça de portugal feminina skoiy": "taca-portugal",
        "taça de portugal feminina": "taca-portugal",
        "2ª divisão feminina": "2-divisao",
        "2.ª divisão feminina": "2-divisao",
        "2a divisão feminina": "2-divisao",
    }

    for name, comp_id in replacements.items():
        if name in text:
            return comp_id

    return None


def competition_matches(comp, text):
    """
    Determine whether a fixture belongs to a configured
    competition.
    """

    comp_id = comp["id"]

    detected = normalise_competition_name(
        competition_text(text)
    )

    if detected:
        return detected == comp_id

    # Fallback keyword matching.
    keywords = {
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
        "2-divisao": [
            "2ª divisão feminina",
            "2.ª divisão feminina",
            "2a divisão feminina",
        ],
        "taca-portugal": [
            "taça de portugal feminina",
            "taça de portugal",
        ],
        "sub18": [
            "sub 18 feminino",
            "sub18 feminino",
            "campeonato nacional sub18",
        ],
    }

    for keyword in keywords.get(comp_id, []):
        if keyword.casefold() in text.casefold():
            return True

    return False


# ============================================================
# EVENT EXTRACTION
# ============================================================

def extract_events(url, comp):
    """
    Extract GDESSA fixtures from an FPB calendar page.
    """

    print(f"FETCH: {url}")

    soup = fetch(url)

    events = []

    current_date = None

    # FPB fixture pages use h3 for the date and anchors
    # for the individual games.
    for node in soup.find_all(["h3", "a"]):

        # ----------------------------------------------------
        # DATE
        # ----------------------------------------------------

        if node.name == "h3":

            parsed = parse_date(
                node.get_text(" ", strip=True)
            )

            if parsed:
                current_date = parsed

            continue

        # ----------------------------------------------------
        # FIXTURE
        # ----------------------------------------------------

        if current_date is None:
            continue

        href = node.get("href", "")

        text = clean(
            node.get_text(
                " ",
                strip=True
            )
        )

        if not href or not text:
            continue

        # Only GDESSA games.
        if not is_gdessa(text):
            continue

        # Make sure this is actually a fixture.
        if not TIME_RE.search(text) and not STATUS_RE.search(text):
            continue

        # Filter competition.
        if not competition_matches(comp, text):
            continue

        home, away, venue = split_teams(text)

        if not home or not away:
            print(
                "WARNING: Could not parse fixture:",
                text
            )
            continue

        time = parse_time(text)

        event = {
            "competition_id": comp["id"],
            "competition": comp["name"],
            "short_name": comp["short_name"],
            "emoji": comp.get("emoji", "🏀"),
            "date": current_date.strftime("%Y-%m-%d"),
            "time": time,
            "home": home,
            "away": away,
            "venue": venue,
            "source": url,
            "source_href": urljoin(url, href),
        }

        events.append(event)

    return deduplicate_events(events)


# ============================================================
# TEAM CALENDAR EXTRACTION
# ============================================================

def extract_team_calendar():
    """
    Extract ALL GDESSA fixtures from the official GDESSA
    FPB team calendar.

    This acts as a second source and catches fixtures that
    may not yet be available through an individual competition
    calendar.
    """

    print(
        f"FETCH TEAM CALENDAR: {TEAM_CALENDAR_URL}"
    )

    soup = fetch(TEAM_CALENDAR_URL)

    events = []

    current_date = None

    for node in soup.find_all(["h3", "a"]):

        if node.name == "h3":

            parsed = parse_date(
                node.get_text(" ", strip=True)
            )

            if parsed:
                current_date = parsed

            continue

        if current_date is None:
            continue

        href = node.get("href", "")

        text = clean(
            node.get_text(
                " ",
                strip=True
            )
        )

        if not href or not text:
            continue

        if not is_gdessa(text):
            continue

        if not TIME_RE.search(text) and not STATUS_RE.search(text):
            continue

        home, away, venue = split_teams(text)

        if not home or not away:
            print(
                "WARNING: Could not parse team fixture:",
                text
            )
            continue

        detected_competition = normalise_competition_name(
            competition_text(text)
        )

        if not detected_competition:
            print(
                "WARNING: Unknown competition:",
                text
            )
            continue

        time = parse_time(text)

        events.append({
            "competition_id": detected_competition,
            "date": current_date.strftime("%Y-%m-%d"),
            "time": time,
            "home": home,
            "away": away,
            "venue": venue,
            "source": TEAM_CALENDAR_URL,
            "source_href": urljoin(
                TEAM_CALENDAR_URL,
                href
            ),
        })

    return deduplicate_events(events)


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


def deduplicate_events(events):
    """
    Keep one copy of each fixture.
    """

    unique = {}

    for event in events:
        unique[event_key(event)] = event

    return list(unique.values())


# ============================================================
# MERGING TEAM CALENDAR WITH CONFIG
# ============================================================

def apply_competition_metadata(events, competitions):
    """
    Add configured competition metadata to events discovered
    through the GDESSA team calendar.
    """

    lookup = {
        comp["id"]: comp
        for comp in competitions
    }

    output = []

    for event in events:

        comp = lookup.get(
            event["competition_id"]
        )

        if not comp:
            print(
                "WARNING: Competition not configured:",
                event["competition_id"]
            )
            continue

        # Respect active/inactive configuration.
        if not comp.get("active"):
            print(
                "SKIP inactive competition:",
                comp["name"]
            )
            continue

        event["competition"] = comp["name"]
        event["short_name"] = comp["short_name"]
        event["emoji"] = comp.get(
            "emoji",
            "🏀"
        )

        output.append(event)

    return output


# ============================================================
# ICS
# ============================================================

def uid(event):
    """
    Stable UID for each game.

    The UID does NOT contain the time, so changing the game
    time in FPB updates the same calendar event instead of
    creating a duplicate.
    """

    raw = "|".join([
        event["competition_id"],
        event["date"],
        event["home"],
        event["away"],
    ])

    return (
        hashlib.sha1(
            raw.casefold().encode("utf-8")
        ).hexdigest()[:20]
        + "@gdessa"
    )


def make_ics(events, calendar_name):
    """
    Generate a complete iCalendar feed.
    """

    now = datetime.utcnow().strftime(
        "%Y%m%dT%H%M%SZ"
    )

    output = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{esc(CFG['prodid'])}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(calendar_name)}",
        f"X-WR-TIMEZONE:{CFG['timezone']}",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D",
        "X-PUBLISHED-TTL:P1D",
    ]

    for event in sorted(
        events,
        key=lambda item: (
            item["date"],
            item.get("time") or "",
            item["home"],
            item["away"],
        ),
    ):

        home_is_gdessa = any(
            alias.casefold()
            in event["home"].casefold()
            for alias in CFG["team_aliases"]
        )

        prefix = (
            "🏠 "
            if home_is_gdessa
            else "🚌 "
        )

        # Add the time to the title when available.
        # This makes the time obvious even in calendar
        # clients that display event titles prominently.
        if event.get("time"):
            summary = (
                f"{prefix}"
                f"{event['home']} vs "
                f"{event['away']} — "
                f"{event['time']}"
            )
        else:
            summary = (
                f"{prefix}"
                f"{event['home']} vs "
                f"{event['away']}"
            )

        description = (
            "GDESSA Barreiro\\n"
            f"Competição: {event['competition']}\\n"
            f"Época: {CFG['season']}\\n"
            f"FPB: {event['source_href']}"
        )

        output.extend([
            "BEGIN:VEVENT",
            f"UID:{uid(event)}",
            f"DTSTAMP:{now}",
        ])

        # ----------------------------------------------------
        # TIMED EVENT
        # ----------------------------------------------------

        if event.get("time"):

            start = datetime.fromisoformat(
                f"{event['date']}T{event['time']}"
            )

            end = start + timedelta(
                hours=2
            )

            output.extend([
                (
                    f"DTSTART;TZID={CFG['timezone']}:"
                    f"{start:%Y%m%dT%H%M%S}"
                ),
                (
                    f"DTEND;TZID={CFG['timezone']}:"
                    f"{end:%Y%m%dT%H%M%S}"
                ),
            ])

        # ----------------------------------------------------
        # ALL-DAY EVENT
        # ----------------------------------------------------

        else:

            start_date = datetime.fromisoformat(
                event["date"]
            )

            end_date = (
                start_date
                + timedelta(days=1)
            )

            output.extend([
                (
                    "DTSTART;VALUE=DATE:"
                    f"{start_date:%Y%m%d}"
                ),
                (
                    "DTEND;VALUE=DATE:"
                    f"{end_date:%Y%m%d}"
                ),
            ])

        output.append(
            f"SUMMARY:{esc(summary)}"
        )

        if event.get("venue"):
            output.append(
                f"LOCATION:{esc(event['venue'])}"
            )

        output.extend([
            f"DESCRIPTION:{esc(description)}",
            f"URL:{esc(event['source_href'])}",
            "END:VEVENT",
        ])

    output.append(
        "END:VCALENDAR"
    )

    return "\r\n".join(output) + "\r\n"


# ============================================================
# MAIN
# ============================================================

def main():

    competitions = CFG["competitions"]

    active_competitions = [
        comp
        for comp in competitions
        if comp.get("active")
    ]

    print(
        f"Active competitions: "
        f"{len(active_competitions)}"
    )

    all_events = []

    # --------------------------------------------------------
    # 1. INDIVIDUAL COMPETITION SOURCES
    # --------------------------------------------------------

    for comp in active_competitions:

        url = comp.get("url")

        if not url:
            print(
                f"NO URL: {comp['name']}"
            )
            continue

        try:

            events = extract_events(
                url,
                comp
            )

            print(
                f"{comp['name']}: "
                f"{len(events)} events"
            )

            # Write individual feed when events
            # were successfully found.
            if events:

                filename = (
                    OUT
                    / f"{comp['id']}.ics"
                )

                filename.write_text(
                    make_ics(
                        events,
                        (
                            f"{CFG['name']} — "
                            f"{comp['short_name']}"
                        ),
                    ),
                    encoding="utf-8",
                )

                all_events.extend(
                    events
                )

            else:

                print(
                    f"WARNING: No events found "
                    f"for {comp['name']}"
                )

        except Exception as error:

            print(
                f"ERROR extracting "
                f"{comp['name']}: {error}"
            )

    # --------------------------------------------------------
    # 2. OFFICIAL GDESSA TEAM CALENDAR
    # --------------------------------------------------------

    try:

        team_events = (
            extract_team_calendar()
        )

        team_events = (
            apply_competition_metadata(
                team_events,
                competitions,
            )
        )

        print(
            f"GDESSA team calendar: "
            f"{len(team_events)} events"
        )

        # Add only events not already present.
        existing = {
            event_key(event)
            for event in all_events
        }

        for event in team_events:

            key = event_key(event)

            if key not in existing:

                all_events.append(event)

                existing.add(key)

                print(
                    "ADDED FROM TEAM CALENDAR:",
                    event["date"],
                    event["home"],
                    "vs",
                    event["away"],
                )

    except Exception as error:

        print(
            "ERROR reading GDESSA team calendar:",
            error
        )

    # --------------------------------------------------------
    # 3. FINAL DEDUPLICATION
    # --------------------------------------------------------

    all_events = deduplicate_events(
        all_events
    )

    # --------------------------------------------------------
    # 4. REBUILD INDIVIDUAL FEEDS FROM
    #    THE COMPLETE DATASET
    # --------------------------------------------------------

    for comp in active_competitions:

        comp_events = [
            event
            for event in all_events
            if event["competition_id"]
            == comp["id"]
        ]

        if not comp_events:
            continue

        filename = (
            OUT
            / f"{comp['id']}.ics"
        )

        filename.write_text(
            make_ics(
                comp_events,
                (
                    f"{CFG['name']} — "
                    f"{comp['short_name']}"
                ),
            ),
            encoding="utf-8",
        )

        print(
            f"FEED: {filename.name} "
            f"({len(comp_events)} events)"
        )

    # --------------------------------------------------------
    # 5. MASTER CALENDAR
    # --------------------------------------------------------

    if not all_events:

        raise SystemExit(
            "No fixtures extracted. "
            "Master feed was NOT overwritten."
        )

    master = make_ics(
        all_events,
        f"{CFG['name']} — All Competitions",
    )

    # Main calendar
    (
        OUT / "gdessa.ics"
    ).write_text(
        master,
        encoding="utf-8",
    )

    # Root compatibility feed
    (
        ROOT / "gdessa.ics"
    ).write_text(
        master,
        encoding="utf-8",
    )

    print(
        f"MASTER: {len(all_events)} events"
    )

    print(
        "Calendar update completed successfully."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()

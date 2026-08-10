#!/usr/bin/env python3
"""
GDESSA FPB -> iCalendar updater.

The script reads the configured FPB pages, extracts GDESSA fixtures,
and writes gdessa.ics. It is intentionally conservative:
- only matches involving GDESSA are included;
- missing times remain all-day events;
- "a definir"/"a indicar" are treated as missing values;
- U18 is skipped automatically while FPB reports "SEM CALENDÁRIO";
- stable UIDs are based on competition + date + teams, so changing
  a time/venue updates the existing calendar event instead of creating
  a duplicate.
"""

from __future__ import annotations
import hashlib
import html
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "data/config.json").read_text(encoding="utf-8"))
OUTPUT = ROOT / "gdessa.ics"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GDESSA-Calendar/1.0; +https://github.com/lblaporta-hub/Game-Schedule)"
}

MONTHS = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12
}

TIME_RE = re.compile(r"\b([01]?\d|2[0-3])H([0-5]\d)\b", re.I)
DATE_RE = re.compile(r"^\s*(\d{1,2})\s+([A-ZÇ]+)\s+(\d{4})\s*$", re.I)

def clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()

def ics_escape(s: str) -> str:
    return clean(s).replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")

def fetch(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return BeautifulSoup(r.text, "html.parser")

def parse_date_heading(text: str):
    m = DATE_RE.match(clean(text))
    if not m:
        return None
    day, mon, year = int(m.group(1)), m.group(2).upper(), int(m.group(3))
    if mon not in MONTHS:
        return None
    return datetime(year, MONTHS[mon], day)

def competition_from_text(text: str, default_name: str) -> str:
    t = clean(text)
    if "Jogos Preparação" in t:
        return "Jogos de Preparação"
    if "Liga Betclic Feminina" in t:
        return "Liga Betclic Feminina"
    if "Sub 18 Feminino" in t or "Sub 18 F" in t:
        return "GDESSA U18"
    return default_name

def is_gdessa(text: str) -> bool:
    t = clean(text).casefold()
    return any(a.casefold() in t for a in CONFIG["calendar"]["team_aliases"])

def split_teams(text: str):
    """
    FPB calendar cards repeat each team name (logo/name markup).
    We use the time token as the separator and collapse the repeated
    team representation. This also supports games with no time.
    """
    t = clean(text)

    # Remove competition/category tail.
    tail_markers = [
        "Sénior Feminino |", "Sub 18 Feminino |", "Sub 18 F |",
        "Sub 18 Feminino", "Jogos Preparação Femininos"
    ]
    for marker in tail_markers:
        idx = t.find(marker)
        if idx >= 0:
            t = t[:idx].strip()
            break

    tm = TIME_RE.search(t)
    if tm:
        left = clean(t[:tm.start()])
        right = clean(t[tm.end():])
    else:
        # "a definir"/"a indicar" is normally between the two teams.
        m = re.search(r"\s+(a definir|a indicar|adiado)\s+", t, re.I)
        if not m:
            return None, None, None
        left = clean(t[:m.start()])
        right = clean(t[m.end():])

    # Repeated markup normally makes "TEAM TEAM". Collapse exact halves.
    def collapse_repeat(s):
        words = s.split()
        if len(words) % 2 == 0:
            half = len(words) // 2
            if " ".join(words[:half]).casefold() == " ".join(words[half:]).casefold():
                return " ".join(words[:half])
        return s

    home = collapse_repeat(left)
    away_part = right

    # Venue starts after the away team. We cannot always infer the boundary
    # from plain text, so use known FPB venue wording when available.
    venue_markers = [
        "Pavilhão ", "Pav.", "Pav Multiusos", "Pav.Multiusos",
        "Complexo ", "Nave ", "Esc Sec ", "Escola Secundária ",
        "Arena ", "Colégio ", "Parque ", "Simoldes "
    ]
    venue_pos = None
    for marker in venue_markers:
        p = away_part.find(marker)
        if p > 0 and (venue_pos is None or p < venue_pos):
            venue_pos = p

    if venue_pos is not None:
        away = clean(away_part[:venue_pos])
        venue = clean(away_part[venue_pos:])
    else:
        away = away_part
        venue = ""

    away = collapse_repeat(away)
    return home, away, venue

def extract_calendar_page(url: str, competition_name: str):
    soup = fetch(url)
    events = []
    current_date = None

    # FPB renders fixture cards as links. We walk the document in order so
    # each fixture inherits the nearest preceding date heading.
    for node in soup.find_all(["h3", "a"]):
        if node.name == "h3":
            d = parse_date_heading(node.get_text(" ", strip=True))
            if d:
                current_date = d
            continue

        href = node.get("href", "")
        text = clean(node.get_text(" ", strip=True))
        if not href or not text or current_date is None:
            continue

        if not any(marker in text for marker in [
            "Liga Betclic Feminina", "Jogos Preparação Femininos",
            "Sub 18 Feminino", "Sub 18 F"
        ]):
            continue
        if not is_gdessa(text):
            continue

        home, away, venue = split_teams(text)
        if not home or not away:
            # Preserve the source text for diagnostics; skip rather than
            # creating a potentially wrong event.
            print("WARN: could not split fixture:", text)
            continue

        comp = competition_from_text(text, competition_name)
        time_match = TIME_RE.search(text)
        hhmm = f"{time_match.group(1).zfill(2)}:{time_match.group(2)}" if time_match else None

        # Clean venue suffix such as ", Lisboa" only when it is clearly a
        # location appended by FPB; keep it otherwise.
        if venue:
            venue = clean(venue)

        events.append({
            "date": current_date.strftime("%Y-%m-%d"),
            "time": hhmm,
            "home": home,
            "away": away,
            "venue": venue,
            "competition": comp,
            "source": url,
            "source_href": urljoin(url, href),
        })

    return events

def event_uid(e):
    raw = "|".join([e["competition"], e["date"], e["home"], e["away"]])
    return hashlib.sha1(raw.casefold().encode("utf-8")).hexdigest()[:20] + "@gdessa"

def make_ics(events):
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GDESSA Barreiro//FPB Automatic Calendar//PT",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:GDESSA Barreiro",
        "X-WR-TIMEZONE:Europe/Lisbon",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D",
        "X-PUBLISHED-TTL:P1D",
    ]

    for e in sorted(events, key=lambda x: (x["date"], x.get("time") or "", x["home"], x["away"])):
        uid = event_uid(e)
        summary = ("🏠 " if e["home"].casefold().startswith("gdessa") else "🚌 ") + f"{e['home']} vs {e['away']}"
        desc = (
            f"GDESSA Barreiro\\n"
            f"Competition: {e['competition']}\\n"
            f"Season: 2026/27\\n"
            f"Source: {e['source']}"
        )

        out += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{now}"]

        if e.get("time"):
            dt = datetime.fromisoformat(f"{e['date']}T{e['time']}")
            dt_end = dt + timedelta(hours=2)
            out += [
                f"DTSTART;TZID=Europe/Lisbon:{dt.strftime('%Y%m%dT%H%M%S')}",
                f"DTEND;TZID=Europe/Lisbon:{dt_end.strftime('%Y%m%dT%H%M%S')}",
            ]
        else:
            d = e["date"].replace("-", "")
            d2 = (datetime.fromisoformat(e["date"]) + timedelta(days=1)).strftime("%Y%m%d")
            out += [f"DTSTART;VALUE=DATE:{d}", f"DTEND;VALUE=DATE:{d2}"]

        out += [f"SUMMARY:{ics_escape(summary)}"]
        if e.get("venue"):
            out.append(f"LOCATION:{ics_escape(e['venue'])}")
        out += [
            f"DESCRIPTION:{ics_escape(desc)}",
            f"URL:{ics_escape(e['source_href'])}",
            "END:VEVENT"
        ]

    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"

def main():
    all_events = []
    for source in CONFIG["calendar"]["include_competitions"]:
        try:
            events = extract_calendar_page(source["url"], source["name"])
            print(f"{source['name']}: {len(events)} GDESSA events found")
            all_events.extend(events)
        except Exception as exc:
            # One unavailable source should not erase the calendar.
            print(f"ERROR reading {source['url']}: {exc}")

    # Deduplicate by competition/date/teams.
    unique = {}
    for e in all_events:
        key = (e["competition"], e["date"], e["home"].casefold(), e["away"].casefold())
        unique[key] = e

    if not unique:
        raise SystemExit("No GDESSA fixtures were extracted; refusing to overwrite gdessa.ics.")

    OUTPUT.write_text(make_ics(list(unique.values())), encoding="utf-8")
    print(f"Wrote {len(unique)} events to {OUTPUT}")

if __name__ == "__main__":
    main()

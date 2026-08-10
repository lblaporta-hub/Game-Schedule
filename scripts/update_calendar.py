#!/usr/bin/env python3
import hashlib, html, json, re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT/"config/competitions.json").read_text(encoding="utf-8"))["calendar"]
OUT = ROOT/CFG.get("output_dir","calendars")
OUT.mkdir(parents=True, exist_ok=True)
HEADERS={"User-Agent":"Mozilla/5.0 (compatible; GDESSA-Calendar/2.0; +https://github.com/lblaporta-hub/Game-Schedule)"}
MONTHS={"JAN":1,"FEV":2,"MAR":3,"ABR":4,"MAI":5,"JUN":6,"JUL":7,"AGO":8,"SET":9,"OUT":10,"NOV":11,"DEZ":12}
TIME_RE=re.compile(r"\b([01]?\d|2[0-3])H([0-5]\d)\b",re.I)
DATE_RE=re.compile(r"^\s*(\d{1,2})\s+([A-ZÇ]+)\s+(\d{4})\s*$",re.I)

def clean(s): return re.sub(r"\s+"," ",html.unescape(s or "")).strip()
def esc(s): return clean(s).replace("\\","\\\\").replace(";","\\;").replace(",","\\,").replace("\n","\\n")
def fetch(url):
    r=requests.get(url,headers=HEADERS,timeout=30); r.raise_for_status()
    r.encoding=r.apparent_encoding or r.encoding
    return BeautifulSoup(r.text,"html.parser")
def parse_date(s):
    m=DATE_RE.match(clean(s))
    if not m or m.group(2).upper() not in MONTHS: return None
    return datetime(int(m.group(3)),MONTHS[m.group(2).upper()],int(m.group(1)))
def is_gdessa(s): return any(a.casefold() in clean(s).casefold() for a in CFG["team_aliases"])

def split_teams(text):
    text=clean(text)
    for marker in ["Sénior Feminino |","Sub 18 Feminino |","Sub 18 F |","Sub 18 Feminino","Jogos Preparação Femininos"]:
        if marker in text: text=text.split(marker,1)[0].strip(); break
    tm=TIME_RE.search(text)
    if tm: left,right=clean(text[:tm.start()]),clean(text[tm.end():])
    else:
        m=re.search(r"\s+(a definir|a indicar|adiado)\s+",text,re.I)
        if not m: return None,None,None
        left,right=clean(text[:m.start()]),clean(text[m.end():])
    def repeat(s):
        w=s.split()
        if len(w)%2==0 and " ".join(w[:len(w)//2]).casefold()==" ".join(w[len(w)//2:]).casefold(): return " ".join(w[:len(w)//2])
        return s
    home=repeat(left)
    positions=[right.find(x) for x in ["Pavilhão ","Pav.","Pav Multiusos","Complexo ","Nave ","Arena ","Colégio ","Escola Secundária "]]
    positions=[p for p in positions if p>0]
    if positions:
        p=min(positions); return home,repeat(clean(right[:p])),clean(right[p:])
    return home,repeat(right),""

def extract(url, comp):
    soup=fetch(url); events=[]; current=None
    for node in soup.find_all(["h3","a"]):
        if node.name=="h3":
            d=parse_date(node.get_text(" ",strip=True))
            if d: current=d
            continue
        href=node.get("href",""); text=clean(node.get_text(" ",strip=True))
        if not href or not text or current is None or not is_gdessa(text): continue
        markers=["Liga Betclic Feminina","Jogos Preparação Femininos","Sub 18 Feminino","Sub 18 F","Campeonato Nacional","Taça de Portugal","Supertaça"]
        if comp["source_type"]=="calendar" and not any(x in text for x in markers): continue
        home,away,venue=split_teams(text)
        if not home or not away:
            print("WARN:",text); continue
        tm=TIME_RE.search(text)
        events.append({"competition_id":comp["id"],"competition":comp["name"],"short_name":comp["short_name"],"emoji":comp.get("emoji","🏀"),"date":current.strftime("%Y-%m-%d"),"time":f"{tm.group(1).zfill(2)}:{tm.group(2)}" if tm else None,"home":home,"away":away,"venue":venue,"source":url,"source_href":urljoin(url,href)})
    return dedup(events)

def dedup(events):
    d={}
    for e in events: d[(e["competition_id"],e["date"],e["home"].casefold(),e["away"].casefold())]=e
    return list(d.values())

def uid(e):
    raw="|".join([e["competition_id"],e["date"],e["home"],e["away"]])
    return hashlib.sha1(raw.casefold().encode()).hexdigest()[:20]+"@gdessa"

def make_ics(events,name):
    now=datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out=["BEGIN:VCALENDAR","VERSION:2.0",f"PRODID:{esc(CFG['prodid'])}","CALSCALE:GREGORIAN","METHOD:PUBLISH",f"X-WR-CALNAME:{esc(name)}",f"X-WR-TIMEZONE:{CFG['timezone']}","REFRESH-INTERVAL;VALUE=DURATION:P1D","X-PUBLISHED-TTL:P1D"]
    for e in sorted(events,key=lambda x:(x["date"],x.get("time") or "",x["home"],x["away"])):
        home_g=any(a.casefold() in e["home"].casefold() for a in CFG["team_aliases"])
        summary=("🏠 " if home_g else "🚌 ")+f"{e['home']} vs {e['away']}"
        desc=f"GDESSA Barreiro\\nCompetição: {e['competition']}\\nÉpoca: {CFG['season']}\\nFPB: {e['source_href']}"
        out += ["BEGIN:VEVENT",f"UID:{uid(e)}",f"DTSTAMP:{now}"]
        if e["time"]:
            start=datetime.fromisoformat(e["date"]+"T"+e["time"]); end=start+timedelta(hours=2)
            out += [f"DTSTART;TZID={CFG['timezone']}:{start:%Y%m%dT%H%M%S}",f"DTEND;TZID={CFG['timezone']}:{end:%Y%m%dT%H%M%S}"]
        else:
            d=e["date"].replace("-",""); d2=(datetime.fromisoformat(e["date"])+timedelta(days=1)).strftime("%Y%m%d")
            out += [f"DTSTART;VALUE=DATE:{d}",f"DTEND;VALUE=DATE:{d2}"]
        out.append("SUMMARY:"+esc(summary))
        if e["venue"]: out.append("LOCATION:"+esc(e["venue"]))
        out += ["DESCRIPTION:"+esc(desc),"URL:"+esc(e["source_href"]),"END:VEVENT"]
    out.append("END:VCALENDAR")
    return "\r\n".join(out)+"\r\n"

def main():
    all_events=[]
    for comp in CFG["competitions"]:
        if not comp.get("active") or not comp.get("url"):
            print("SKIP:",comp["name"]); continue
        try:
            events=extract(comp["url"],comp)
            print(comp["name"],len(events))
            if events:
                (OUT/f"{comp['id']}.ics").write_text(make_ics(events,f"{CFG['name']} — {comp['short_name']}"),encoding="utf-8")
                all_events += events
            else:
                print("WARNING: no fixtures; existing feed kept")
        except Exception as exc: print("ERROR:",comp["name"],exc)
    all_events=dedup(all_events)
    if not all_events: raise SystemExit("No fixtures extracted; master feed not overwritten.")
    master=make_ics(all_events,f"{CFG['name']} — All Competitions")
    (OUT/"gdessa.ics").write_text(master,encoding="utf-8")
    (ROOT/"gdessa.ics").write_text(master,encoding="utf-8")
    print("MASTER:",len(all_events),"events")

if __name__=="__main__": main()

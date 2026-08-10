# GDESSA Game Schedule

Automatic multi-feed iCalendar system for GDESSA Barreiro.

## Feeds
- `calendars/gdessa.ics` — all active competitions
- `calendars/liga-betclic.ics`
- `calendars/preparacao.ics`
- `calendars/sub18.ics`

Future competitions are preconfigured and become active when their FPB URL is published.

## Add a competition
Edit `config/competitions.json` and set its `url` and `"active": true`. No workflow/script change is required.

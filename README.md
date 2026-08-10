# GDESSA Game Schedule

Automatic iCalendar feed for GDESSA Barreiro.

## Sources

- Liga Betclic Feminina 2026/27:
  https://www.fpb.pt/calendario/11445/?competicao=11445
- Jogos de Preparação:
  https://www.fpb.pt/calendario/11471/?competicao=11471
- GDESSA "A" U18:
  https://www.fpb.pt/equipa/equipa_63443/

## Calendar subscription

After GitHub Pages is enabled, subscribe to:

https://lblaporta-hub.github.io/Game-Schedule/gdessa.ics

## Automatic updates

GitHub Actions runs the scraper once per day and can also be run manually from:

Actions -> Update GDESSA Calendar -> Run workflow

The script is conservative: it will not replace the calendar if no GDESSA fixtures are successfully extracted.

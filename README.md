# FISI-Prüfungstrainer

[![Python](https://github.com/eazzy1989/fisi-pruefungstrainer/actions/workflows/python-lint.yml/badge.svg)](https://github.com/eazzy1989/fisi-pruefungstrainer/actions/workflows/python-lint.yml)

Zwei Telegram-Bots zur Vorbereitung auf die IHK-Abschlussprüfung **Fachinformatiker/in für Systemintegration** – entstanden während meiner eigenen Umschulung und im Einsatz für meine Lerngruppe.

| Bot | Prüfung | Highlights |
|---|---|---|
| [`ap1/`](ap1/) | AP1 – Einrichten eines IT-gestützten Arbeitsplatzes | Fragen nach Thema, Mehrfachauswahl, Prüfungssimulation mit Note |
| [`ap2/`](ap2/) | AP2 – Systemintegration inkl. WiSo | drei Fragetypen wie in der IHK-WiSo, WiSo-Simulation, offene Aufgaben mit Musterlösung |

## Was die Bots können

- **Lernen in Durchgängen:** jede Frage genau einmal pro Runde, mit Fortschritt („Frage 4/10“), danach alle oder nur die falschen wiederholen
- **Prüfungssimulation** mit 20, 40 oder 60 Fragen, Teilpunkte bei Mehrfachauswahl und Note nach IHK-Notenschlüssel
- **Statistik pro Nutzer:** Trefferquote je Thema und Hinweis auf schwache Themen – Fortschritt übersteht Neustarts
- **Antworten werden bei jeder Anzeige neu gemischt**, damit niemand Positionen auswendig lernt
- **Mehrere Nutzer gleichzeitig**, optional beschränkt auf freigegebene Telegram-IDs
- **Ein-Schritt-Installation** als systemd-Dienst per `install.sh`, Token landet nur in `.env` (Rechte 600)

## Fragenbank

Dieses Repo enthält **nur den Code und eine kleine Auswahl selbst geschriebener Beispielfragen**. Die Fragenbanken, mit denen ich selbst lerne, sind bewusst nicht enthalten. Eigene Fragen lassen sich einfach als Zeilen in `fragen.csv` bzw. `aufgaben.csv` ergänzen – das Format ist in den READMEs der Bots beschrieben.

## Schnellstart

```bash
git clone https://github.com/eazzy1989/fisi-pruefungstrainer.git
cd fisi-pruefungstrainer/ap1      # oder ap2
bash install.sh                   # fragt nach dem Bot-Token und richtet den Dienst ein
```

Zum Testen ohne Server: `pip install -r requirements.txt` und `BOT_TOKEN=<token> python telegram_bot.py`.

## Tech-Stack

Python · python-telegram-bot (asyncio) · pandas · systemd · Bash

## Lizenz

Code: [MIT](LICENSE)

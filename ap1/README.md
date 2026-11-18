# AP1 Trainer Bot

Telegram-Lerntrainer für Fachinformatiker zur Vorbereitung auf die AP1
(Teil 1: Einrichten eines IT-gestützten Arbeitsplatzes).

## Funktionen

- Fragen nach Themen (z. B. Netzwerk, Server & Cloud, Projektmanagement, IT-Sicherheit,
  Kundenberatung & Wirtschaft, Hardware & Rechnen, Programmierung) – die Themen ergeben sich aus der CSV
- **Mehrfachauswahl:** Antworten antippen (✅), dann „Antwort prüfen“
- Zufallsfragen mit zwei Versuchen und Punkten (2 / 1 Punkt), beim Fehlversuch Hinweis
  „x von y richtigen Antworten erwischt“
- Lernen nach Thema
- Prüfungssimulation mit 20, 40 oder 60 Fragen inkl. Mehrfachauswahl, Note nach IHK-Schlüssel,
  Fehlerauswertung mit Lösungstext
- Statistik mit Trefferquote je Thema und Hinweis auf schwache Themen, Zurücksetzen möglich
- Prüfungsinfo AP1
- Befehle: `/start`, `/id`, `/frage`, `/pruefung`, `/stats`
- Antworten werden bei jeder Anzeige neu gemischt (kein Auswendiglernen der Position)
- **Durchgänge:** Beim Lernen (Zufall oder Thema) kommt jede Frage genau einmal, mit Fortschritt
  („Frage 4/10“). Am Ende Auswertung, dann alle nochmal oder nur die falschen wiederholen.
  Der Fortschritt wird pro Nutzer gespeichert und übersteht Neustarts.
- Mehrere Nutzer gleichzeitig: jeder hat eigene Statistik und eigenen Fortschritt

## Dateien

- `telegram_bot.py` – Hauptanwendung
- `fragen.csv` – Fragenbank (Spalten: Question #, Thema, Question, A–F, Correct Answer;
  mehrere Buchstaben = Mehrfachauswahl). **Im Repo sind nur 12 selbst geschriebene Beispielfragen**
  – die eigentliche Fragenbank ist nicht enthalten.
- `install.sh` – Einrichtung als systemd-Service in einem Schritt
- `.env` – enthält `BOT_TOKEN=...` (legt install.sh an, **nie weitergeben**)
- `stats.json` – Statistik der Nutzer (wird automatisch angelegt, nicht weitergeben)

## Installation (Linux-Server, z. B. Ubuntu)

1. In Telegram bei **@BotFather** mit `/newbot` einen eigenen Bot anlegen und den Token kopieren.
2. Ordner auf den Server kopieren und dort ausführen:

```bash
cd fisi-pruefungstrainer/ap1
bash install.sh
```

Das Skript fragt einmal nach dem Token (wird nur in `.env` gespeichert, Rechte 600), legt eine
Python-venv an, installiert die Pakete und richtet den Service `ap1bot` ein – für den Benutzer und
Ordner, in dem es ausgeführt wird.

Ohne Server (z. B. zum Testen am PC):

```bash
pip install -r requirements.txt
BOT_TOKEN=<token> python telegram_bot.py
```

## Verwaltung

```bash
sudo systemctl status ap1bot      # läuft er?
sudo systemctl restart ap1bot     # nach Änderungen an Fragen/Code
journalctl -u ap1bot -f           # Logs live
```

## Update

Neue Version entpacken (überschreibt nur Code und Fragen – `.env`, `stats.json` und `venv` bleiben)
und `sudo systemctl restart ap1bot`.

## Fragen ergänzen

Neue Zeile in `fragen.csv`: Thema, Frage, Antworten A–F, richtige Buchstaben (`B` oder bei
Mehrfachauswahl z. B. `ACE`). Die Antworten werden bei jeder Anzeige neu gemischt – die Reihenfolge in
der CSV spielt keine Rolle. Danach `sudo systemctl restart ap1bot`.

## Zugang für die Klasse (optional)

Standardmäßig kann jeder den Bot nutzen, der ihn in Telegram findet. Um ihn auf bestimmte Personen zu
beschränken:

1. Jede Person schickt dem Bot `/id` und gibt dir die angezeigte Nummer.
2. In der `.env` eine Zeile ergänzen (IDs mit Komma getrennt):
   `ALLOWED_USERS=123456789,987654321`
3. `sudo systemctl restart ap1bot`

Alle anderen bekommen dann „🔒 Dieser Bot ist privat“.

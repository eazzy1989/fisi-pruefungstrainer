# AP2 Trainer Bot (FISI)

Telegram-Lerntrainer für Fachinformatiker Systemintegration zur Vorbereitung auf die AP2.
Gleicher Aufbau wie der AP1-Trainer, läuft als eigener Service.

## Funktionen

- Fragen nach Themen (z. B. Subnetting, VLAN, DNS/Fehlersuche, VPN/Firewall, Kryptografie/PKI,
  RAID, Backup, Cloud/Server, IT-Sicherheit, WiSo) – jeweils mit Erklärung
- Drei Fragetypen wie in der IHK-WiSo:
  - Einfachauswahl
  - Mehrfachauswahl „zwei zutreffende“ – Ziffern antippen ✅, dann „Antwort prüfen“
  - Zuordnung/Reihenfolge – je Zeile a), b) … eine Ziffer wählen
- Antworten werden bei jeder Anzeige neu gemischt (kein Auswendiglernen der Position)
- **Durchgänge:** Beim Lernen (Zufall oder Thema) kommt jede Frage genau einmal, mit Fortschritt
  („Frage 4/10“). Am Ende Auswertung, dann alle nochmal oder nur die falschen wiederholen.
  Der Fortschritt wird pro Nutzer gespeichert und übersteht Neustarts.
- Mehrere Nutzer gleichzeitig: jeder hat eigene Statistik und eigenen Fortschritt
- Zufallsfragen mit zwei Versuchen und Punkten (2 / 1 Punkt), Lernen nach Thema
- Prüfungssimulation mit 20, 40 oder 60 Fragen und **WiSo-Simulation** (30 Fragen) –
  Teilpunkte bei Mehrfach/Zuordnung wie bei der IHK, Note nach IHK-Schlüssel, Fehlerauswertung
- **Offene Aufgaben** mit Musterlösung und Selbstbewertung, sortiert nach Teil 1
  (Konzeption & Administration), Teil 2 (Netzwerke) und WiSo
- Prüfungsinfo: Aufbau, Gewichtung und Bestehensregeln der AP2
- Statistik mit Trefferquote je Thema und Hinweis auf schwache Themen

Befehle: `/start`, `/id`, `/frage`, `/pruefung`, `/aufgabe`, `/stats`

## Dateien

- `telegram_bot.py` – Hauptanwendung
- `fragen.csv` – Fragenbank (**im Repo nur 10 selbst geschriebene Beispielfragen**)
- `aufgaben.csv` – offene Aufgaben mit Lösung (**im Repo nur 6 eigene Übungsaufgaben**)
- `install.sh` – Einrichtung als systemd-Service in einem Schritt
- `.env` – enthält `BOT_TOKEN=...` (legt install.sh an, **nie weitergeben**)
- `stats.json` – Statistik der Nutzer (wird automatisch angelegt, nicht weitergeben)

## Installation (Linux-Server, z. B. Ubuntu)

1. In Telegram bei **@BotFather** mit `/newbot` einen eigenen Bot anlegen und den Token kopieren.
2. Ordner auf den Server kopieren und dort ausführen:

```bash
cd fisi-pruefungstrainer/ap2
bash install.sh
```

Das Skript fragt einmal nach dem Token (wird nur in `.env` gespeichert, Rechte 600), legt eine
Python-venv an, installiert die Pakete und richtet den Service `ap2bot` ein – für den Benutzer und
Ordner, in dem es ausgeführt wird.

Ohne Server (z. B. zum Testen am PC):

```bash
pip install -r requirements.txt
BOT_TOKEN=<token> python telegram_bot.py
```

## Verwaltung

```bash
sudo systemctl status ap2bot      # läuft er?
sudo systemctl restart ap2bot     # nach Änderungen an Fragen/Code
journalctl -u ap2bot -f           # Logs live
```

## Update

Neue Version entpacken (überschreibt nur Code und Fragen – `.env`, `stats.json` und `venv` bleiben)
und `sudo systemctl restart ap2bot`.

## Fragen ergänzen

Neue Zeile in `fragen.csv`:

- `Typ` = `E` (Einfachauswahl), `M` (Mehrfachauswahl) oder `Z` (Zuordnung/Reihenfolge)
- `Question`, Antworten in `A`–`F`, `Erklaerung` (optional)
- `Correct Answer`: bei E ein Buchstabe (`C`), bei M die Buchstaben (`BE`), bei Z je Teilaufgabe die
  Nummer der passenden Antwort (`213` = a→2, b→1, c→3)
- `Items` (nur Z): Teilaufgaben a), b), … mit `|` getrennt

Antworten (und bei Z auch die Teilaufgaben) werden bei jeder Anzeige neu gemischt. Ausnahme: Antworten
wie „Keine der genannten“ – dann bleibt die Reihenfolge. Danach `sudo systemctl restart ap2bot`.

Offene Aufgaben stehen in `aufgaben.csv` (Pruefung, Thema, Punkte, Aufgabe, Loesung, Teil). Einträge,
deren `Pruefung` mit „Übung“ beginnt, gelten als Übungsaufgaben; alle anderen erscheinen im Menü als
eigene Prüfungssätze (Format z. B. `W25 Teil 1`).

## Zugang für die Klasse (optional)

Standardmäßig kann jeder den Bot nutzen, der ihn in Telegram findet. Um ihn auf bestimmte Personen zu
beschränken:

1. Jede Person schickt dem Bot `/id` und gibt dir die angezeigte Nummer.
2. In der `.env` eine Zeile ergänzen (IDs mit Komma getrennt):
   `ALLOWED_USERS=123456789,987654321`
3. `sudo systemctl restart ap2bot`

Alle anderen bekommen dann „🔒 Dieser Bot ist privat“.

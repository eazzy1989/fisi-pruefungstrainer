"""
AP2 Trainer Bot – Fachinformatiker Systemintegration
Aufbau wie der AP1-Trainer: Menü mit Buttons, Zufallsfragen mit zwei Versuchen,
Prüfungssimulation, Statistik. Zusätzlich: Themenwahl, Erklärungen,
offene Prüfungsaufgaben mit Musterlösung und Schwächen-Statistik.
"""

import json
import os
import random
import re

import pandas as pd

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# =====================================
# KONFIGURATION
# =====================================

TOKEN = os.getenv("BOT_TOKEN")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATS_FILE = os.path.join(BASE_DIR, "stats.json")
OPTIONS = ["A", "B", "C", "D", "E", "F"]

# =====================================
# DATEN LADEN
# =====================================

df = pd.read_csv(os.path.join(BASE_DIR, "fragen.csv"))
if "Typ" not in df.columns:
    df["Typ"] = "E"
if "Items" not in df.columns:
    df["Items"] = ""
df["Typ"] = df["Typ"].fillna("E")
df["Correct Answer"] = df["Correct Answer"].astype(str).str.strip().str.upper()
aufgaben = pd.read_csv(os.path.join(BASE_DIR, "aufgaben.csv"))

THEMEN = sorted(df["Thema"].dropna().unique().tolist())
if "Teil" not in aufgaben.columns:
    aufgaben["Teil"] = aufgaben["Pruefung"].map(
        lambda p: "Teil 1" if p.endswith("Teil 1") else "Teil 2" if p.endswith("Teil 2") else "WiSo")
aufgaben["Uebung"] = aufgaben["Pruefung"].str.startswith("Übung")

# Die drei Prüfungsbereiche der AP2
TEILE = [
    ("Teil 1", "🖥 Teil 1 – Konzeption & Administration von IT-Systemen"),
    ("Teil 2", "🌐 Teil 2 – Analyse & Entwicklung von Netzwerken"),
    ("WiSo", "⚖️ WiSo – Wirtschafts- und Sozialkunde"),
]

# Prüfungssätze (ohne Übungen), neueste zuerst, Winter vor Sommer
PRUEFUNGEN = sorted(
    aufgaben.loc[~aufgaben["Uebung"], "Pruefung"].unique().tolist(),
    key=lambda p: (-int(p[1:3]), p[0] != "W", p),
)
WISO_FRAGEN = df[df["Thema"] == "WiSo"]

# =====================================
# SPEICHER (wie AP1: Dictionaries pro User)
# =====================================

if os.path.exists(STATS_FILE):
    with open(STATS_FILE, "r", encoding="utf-8") as f:
        user_stats = json.load(f)
else:
    user_stats = {}

screen_messages = {}   # user_id -> [message_ids] der aktuellen Ansicht
user_questions = {}    # user_id -> Index der aktuellen Lernfrage
user_view = {}         # user_id -> gemischte Ansicht der aktuellen Frage
user_selection = {}    # user_id -> Auswahl (set bei Mehrfach, dict bei Zuordnung)
user_attempts = {}     # user_id -> Versuch 1 oder 2
user_topic = {}        # user_id -> gewähltes Thema oder None
last_question = {}     # user_id -> letzter Fragen-Index

exam_questions = {}
exam_index = {}
exam_correct = {}
exam_wrong = {}
exam_selection = {}

task_filter = {}       # user_id -> ("alle",) | ("teil", t) | ("uebung", t) | ("pruefung", p)
task_back = {}         # user_id -> Callback für den Zurück-Button
task_current = {}      # user_id -> Index der aktuellen offenen Aufgabe


# =====================================
# HILFSFUNKTIONEN
# =====================================

def save_stats():
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(user_stats, f, ensure_ascii=False, indent=4)


def get_stats(user_id):
    key = str(user_id)
    if key not in user_stats:
        user_stats[key] = {"punkte": 0, "fragen": 0}
    s = user_stats[key]
    # Erweiterungen gegenüber AP1 (abwärtskompatibel)
    s.setdefault("themen", {})
    s.setdefault("pruefungen", [])
    s.setdefault("aufgaben", {"gewusst": 0, "nicht_gewusst": 0})
    return s


def record_answer(user_id, thema, correct):
    s = get_stats(user_id)
    t = s["themen"].setdefault(thema, {"richtig": 0, "gesamt": 0})
    t["gesamt"] += 1
    if correct:
        t["richtig"] += 1


async def clear_screen(context, chat_id, user_id):
    """Löscht alle Nachrichten der aktuellen Ansicht (hält den Chat sauber)."""
    for msg_id in screen_messages.get(user_id, []):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    screen_messages[user_id] = []


async def show(context, chat_id, user_id, text, buttons=None, clear=True):
    """Sendet eine Nachricht und merkt sie für das spätere Aufräumen vor."""
    if clear:
        await clear_screen(context, chat_id, user_id)
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
    screen_messages.setdefault(user_id, []).append(msg.message_id)
    return msg


def btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def typ(frage):
    t = str(frage.get("Typ", "E")) if pd.notna(frage.get("Typ", "E")) else "E"
    return t if t in ("E", "M", "Z") else "E"


def optionen(frage):
    return [o for o in OPTIONS if o in frage and pd.notna(frage[o]) and str(frage[o]).strip()]


def items(frage):
    it = frage.get("Items", "")
    return [x for x in str(it).split("|") if x.strip()] if pd.notna(it) else []


ITEM_LABELS = "abcdefg"


def nummer(o):
    """A→1, B→2 … (IHK-WiSo arbeitet mit Ziffern)"""
    return OPTIONS.index(o) + 1


def answer_text(frage, letters):
    """Lösung als lesbarer Text – passend zum Fragetyp."""
    t = typ(frage)
    if t == "Z":
        opts = optionen(frage)
        return "\n".join(f"{ITEM_LABELS[i]}) {it} → {c}) {frage[opts[int(c) - 1]]}"
                         for i, (it, c) in enumerate(zip(items(frage), letters)))
    if t == "M":
        return "\n".join(f"{nummer(b)}) {frage[b]}" for b in letters if b in optionen(frage))
    return "\n".join(f"{b}) {frage[b]}" for b in letters if b in optionen(frage))


def loesung_kurz(frage):
    t = typ(frage)
    ca = str(frage["Correct Answer"])
    if t == "Z":
        return " ".join(f"{ITEM_LABELS[i]}{c}" for i, c in enumerate(ca))
    if t == "M":
        return " und ".join(str(nummer(b)) for b in ca)
    return ca


def question_body(frage):
    t = typ(frage)
    text = f"{frage['Question']}\n\n"
    if t == "E":
        for o in optionen(frage):
            text += f"{o}) {frage[o]}\n"
        return text
    for o in optionen(frage):
        text += f"{nummer(o)}) {frage[o]}\n"
    if t == "M":
        text += "\n☑️ Mehrfachauswahl: Ziffern antippen (✅), dann „Antwort prüfen“."
    else:
        text += "\n"
        for i, it in enumerate(items(frage)):
            text += f"{ITEM_LABELS[i]}) {it}\n"
        text += "\n🔗 Zuordnung: Für jede Zeile a), b) … die passende Ziffer antippen, dann „Antwort prüfen“."
    return text


def answer_buttons(frage, prefix, token, auswahl=None):
    """E: ein Tipp = Antwort · M: Ziffern an-/abhaken · Z: je Teilaufgabe eine Ziffer wählen."""
    t = typ(frage)
    opts = optionen(frage)
    rows, row = [], []
    if t == "E":
        for o in opts:
            row.append(btn(o, f"{prefix}a_{token}_{o}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return rows
    if t == "M":
        sel = auswahl or set()
        for o in opts:
            row.append(btn(f"✅{nummer(o)}" if o in sel else str(nummer(o)), f"{prefix}t_{token}_{o}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    else:
        zu = auswahl or {}
        for i in range(len(items(frage))):
            r = [btn(f"{ITEM_LABELS[i]})", f"{prefix}n_{token}")]
            for k in range(1, len(opts) + 1):
                r.append(btn(f"✅{k}" if zu.get(i) == k else str(k), f"{prefix}z_{token}_{i}_{k}"))
            rows.append(r)
    rows.append([btn("✔️ Antwort prüfen", f"{prefix}c_{token}")])
    return rows


def auswerten(frage, auswahl):
    """Gibt (komplett_richtig, punkte 0..1, treffer, gesamt) zurück – IHK-ähnliche Teilbewertung."""
    t = typ(frage)
    ca = str(frage["Correct Answer"])
    if t == "E":
        ok = auswahl == ca
        return ok, 1.0 if ok else 0.0, int(ok), 1
    if t == "M":
        sel = set(auswahl or set())
        soll = set(ca)
        treffer = len(sel & soll)
        extra = len(sel - soll)
        return sel == soll, max(0, treffer - extra) / len(soll), treffer, len(soll)
    zu = auswahl or {}
    treffer = sum(1 for i, c in enumerate(ca) if zu.get(i) == int(c))
    return treffer == len(ca), treffer / len(ca), treffer, len(ca)


def auswahl_text(frage, auswahl):
    t = typ(frage)
    if t == "E":
        return str(auswahl)
    if t == "M":
        return " und ".join(str(nummer(b)) for b in sorted(auswahl)) or "–"
    return " ".join(f"{ITEM_LABELS[i]}{auswahl.get(i, '–')}" for i in range(len(items(frage))))


_FEST = re.compile(r"\b(alle|keine)\s+(der\s+)?(oben\s+)?(genannten|aufgeführten|obigen)\b|\boben\s+genannt|^\s*position\s+\d", re.I)
_SCHRITT = re.compile(r"^\s*\d+\.\s*Schritt", re.I)


def gemischt(frage):
    """Kopie der Frage mit zufälliger Reihenfolge – die Lösung wird passend umgerechnet.
    Bleibt unverändert, wenn sich Antworten aufeinander beziehen ("Keine der genannten" …)."""
    f = frage.copy()
    opts = optionen(f)
    texte = [f[o] for o in opts]
    if len(opts) < 2 or any(_FEST.search(str(t)) for t in texte):
        return f
    t = typ(f)
    ca = str(f["Correct Answer"])
    if t == "Z":
        its = items(f)
        neu_opts = list(range(len(opts)))
        if not any(_SCHRITT.search(str(x)) for x in texte):   # Reihenfolge: Schritt-Spalte bleibt 1., 2., …
            random.shuffle(neu_opts)
        for neu_pos, alt_pos in enumerate(neu_opts):
            f[opts[neu_pos]] = texte[alt_pos]
        alt_zu_neu = {alt + 1: neu + 1 for neu, alt in enumerate(neu_opts)}
        reihen = list(range(len(its)))
        random.shuffle(reihen)
        f["Items"] = "|".join(its[i] for i in reihen)
        f["Correct Answer"] = "".join(str(alt_zu_neu[int(ca[i])]) for i in reihen)
        return f
    perm = opts[:]
    random.shuffle(perm)
    alt_zu_neu = {}
    for neu, alt in zip(opts, perm):
        f[neu] = frage[alt]
        alt_zu_neu[alt] = neu
    f["Correct Answer"] = "".join(sorted(alt_zu_neu[b] for b in ca if b in alt_zu_neu))
    return f


def leer_auswahl(frage):
    return set() if typ(frage) == "M" else {}


# =====================================
# MENÜS
# =====================================

def main_menu():
    return [
        [btn("📚 Lernen", "menu_learn")],
        [btn("📊 Statistik", "menu_stats")],
        [btn("ℹ️ Prüfungsinfo AP2", "menu_info")],
    ]


def learn_menu():
    return [
        [btn("🎯 Zufallsfragen (alle Themen)", "learn_random")],
        [btn("🗂 Nach Thema lernen", "topic_menu")],
        [btn("📝 Prüfungssimulation", "pruefung_menu")],
        [btn("📄 Prüfungsaufgaben & Übungen", "task_menu")],
        [btn("🔙 Zurück", "menu_home")],
    ]


async def show_main(context, chat_id, user_id):
    await show(context, chat_id, user_id, "🎓 AP2 Trainer – FISI\n\nWähle eine Option:", main_menu())


async def show_learn(context, chat_id, user_id):
    await show(context, chat_id, user_id, "📚 Lernen\n\nWähle eine Option:", learn_menu())


async def show_topics(context, chat_id, user_id):
    laufend = get_stats(user_id).get("durchgaenge", {})
    rows = []
    for i, t in enumerate(THEMEN):
        n = int((df["Thema"] == t).sum())
        d = laufend.get(t)
        info = f" ▶️ {d['total'] - len(d['queue'])}/{d['total']}" if d and 0 < len(d["queue"]) < d["total"] else (
            " ✅" if d and not d["queue"] else "")
        rows.append([btn(f"{t} ({n}){info}", f"topic_{i}")])
    rows.append([btn("🔙 Zurück", "menu_learn")])
    await show(context, chat_id, user_id, "🗂 Thema wählen:\n\nJede Frage kommt pro Durchgang genau einmal. ▶️ = angefangen, ✅ = fertig.", rows)


async def show_exam_menu(context, chat_id, user_id):
    rows = [[btn(f"🎯 {n} Fragen (alle Themen)", f"exam_{n}")] for n in (20, 40, 60)]
    rows.append([btn(f"⚖️ WiSo-Simulation ({min(30, len(WISO_FRAGEN))} Fragen)", "exam_wiso")])
    rows.append([btn("🔙 Zurück", "menu_learn")])
    await show(
        context, chat_id, user_id,
        "📝 Prüfungssimulation\n\nGemischte Fragen, Auswertung am Ende.\n"
        "Die WiSo-Simulation ist wie in der echten Prüfung aufgebaut: 30 Fragen, 60 Minuten.",
        rows,
    )


async def show_task_menu(context, chat_id, user_id):
    rows = []
    for i, (teil, name) in enumerate(TEILE):
        rows.append([btn(f"{name} ({(aufgaben['Teil'] == teil).sum()})", f"taskteil_{i}")])
    rows.append([btn(f"🎲 Alle gemischt ({len(aufgaben)}){task_fortschritt(user_id, 'alle')}", "taskset_alle")])
    rows.append([btn("🔙 Zurück", "menu_learn")])
    await show(
        context, chat_id, user_id,
        "📄 Prüfungsaufgaben & Übungen\n\n"
        "📄 = Aufgabe aus einer eigenen Prüfungssammlung\n✏️ = Übungsaufgabe im IHK-Stil\n\n"
        "Erst selbst lösen (Papier!), dann Lösung anzeigen und ehrlich bewerten. "
        "Jede Aufgabe kommt pro Durchgang genau einmal (▶️ angefangen, ✅ fertig).\n\nWähle den Prüfungsbereich:",
        rows,
    )


async def show_teil_menu(context, chat_id, user_id, i):
    teil, name = TEILE[i]
    pool = aufgaben[aufgaben["Teil"] == teil]
    rows = [[btn(f"🎲 Alle aus diesem Bereich ({len(pool)}){task_fortschritt(user_id, 'teil:' + teil)}", f"taskset_t{i}")]]
    n_ueb = int(pool["Uebung"].sum())
    if n_ueb:
        rows.append([btn(f"✏️ Nur Übungsaufgaben ({n_ueb}){task_fortschritt(user_id, 'uebung:' + teil)}", f"taskset_u{i}")])
    for j, p in enumerate(PRUEFUNGEN):
        anzahl = int((aufgaben["Pruefung"] == p).sum())
        if p.endswith(teil) and anzahl:
            rows.append([btn(f"📄 {p} ({anzahl}){task_fortschritt(user_id, 'pruefung:' + p)}", f"taskset_p{j}")])
    text = f"{name}\n\n"
    if teil == "WiSo":
        text += ("In der echten Prüfung wird WiSo per Multiple Choice geprüft. "
                 "Hier übst du offene Aufgaben – für das echte Format nimm die WiSo-Simulation.")
        rows.append([btn("⚖️ WiSo-Simulation (30 Fragen)", "exam_wiso")])
    else:
        text += "Wähle eine Prüfung oder lass dir zufällig Aufgaben geben:"
    rows.append([btn("🔙 Zurück", "task_menu")])
    task_back[user_id] = f"taskteil_{i}"
    await show(context, chat_id, user_id, text, rows)


PRUEFUNGSINFO = (
    "ℹ️ AP2 Fachinformatiker Systemintegration (AO 2020)\n\n"
    "📝 Schriftlich (bundeseinheitlich):\n"
    "• Konzeption & Administration von IT-Systemen – 90 min, 10 %\n"
    "• Analyse & Entwicklung von Netzwerken – 90 min, 10 %\n"
    "• Wirtschafts- und Sozialkunde – 60 min, 30 Aufgaben, 10 %\n\n"
    "🛠 Projekt (50 %):\n"
    "• Betriebliche Projektarbeit + Doku: max. 40 Stunden (25 %)\n"
    "• Präsentation (max. 15 min) + Fachgespräch: zusammen max. 30 min (25 %)\n"
    "• Projektantrag muss vorher vom Prüfungsausschuss genehmigt werden\n\n"
    "📌 AP1 (Einrichten eines IT-gestützten Arbeitsplatzes) zählt 20 %.\n\n"
    "✅ Bestanden (laut Ausbildungsverordnung), wenn:\n"
    "• Gesamtergebnis Teil 1 + Teil 2 mind. ausreichend (50 Punkte)\n"
    "• Ergebnis von Teil 2 mind. ausreichend\n"
    "• Projekt mind. ausreichend\n"
    "• mind. zwei der drei schriftlichen Bereiche mind. ausreichend\n"
    "• kein Bereich von Teil 2 ungenügend (unter 30 Punkte)\n\n"
    "🗣 Mündliche Ergänzungsprüfung: in einem schriftlichen Bereich möglich, "
    "wenn sie für das Bestehen den Ausschlag geben kann (Gewichtung schriftlich : mündlich = 2 : 1).\n\n"
    "🎯 Notenschlüssel: 1 ab 92 | 2 ab 81 | 3 ab 67 | 4 ab 50 | 5 ab 30 Punkte\n\n"
    "Quelle: IHK-Merkblatt Fachinformatiker (VO vom 28.02.2020). Termine und Details bei deiner IHK prüfen."
)


async def show_info(context, chat_id, user_id):
    await show(context, chat_id, user_id, PRUEFUNGSINFO, [[btn("🔙 Hauptmenü", "menu_home")]])


async def show_stats(context, chat_id, user_id):
    s = get_stats(user_id)
    text = (
        "📊 Statistik\n\n"
        f"⭐ Punkte: {s['punkte']}\n"
        f"📚 Fragen: {s['fragen']}\n"
    )

    themen = s["themen"]
    if themen:
        text += "\n📈 Trefferquote je Thema:\n"
        ranking = sorted(themen.items(), key=lambda kv: kv[1]["richtig"] / max(kv[1]["gesamt"], 1))
        for name, t in ranking:
            quote = round(t["richtig"] / t["gesamt"] * 100) if t["gesamt"] else 0
            icon = "🟢" if quote >= 80 else "🟡" if quote >= 50 else "🔴"
            text += f"{icon} {name}: {quote}% ({t['richtig']}/{t['gesamt']})\n"
        schwach = [n for n, t in ranking if t["gesamt"] >= 3][:3]
        if schwach:
            text += "\n🎯 Üben solltest du: " + ", ".join(schwach) + "\n"

    if s["pruefungen"]:
        letzte = s["pruefungen"][-5:]
        text += "\n📝 Letzte Simulationen: " + ", ".join(f"{p['quote']}%" for p in letzte) + "\n"

    a = s["aufgaben"]
    if a["gewusst"] + a["nicht_gewusst"]:
        text += f"\n📄 Prüfungsaufgaben: {a['gewusst']} gewusst, {a['nicht_gewusst']} nicht gewusst\n"

    rows = [
        [btn("📚 Lernen", "menu_learn")],
        [btn("🗑 Statistik zurücksetzen", "stats_reset")],
        [btn("🔙 Hauptmenü", "menu_home")],
    ]
    await show(context, chat_id, user_id, text, rows)


# =====================================
# LERNMODUS (Zufallsfragen, wie AP1)
# =====================================

# ---------- Durchgänge: jede Frage eines Bereichs genau einmal ----------

def deck_key(user_id):
    return user_topic.get(user_id) or "*"


def deck_name(key):
    return "alle Themen" if key == "*" else key


def deck_pool(key):
    return (df.index if key == "*" else df.index[df["Thema"] == key]).tolist()


def neuer_durchgang(user_id, key, nur=None):
    pool = [int(i) for i in (nur if nur is not None else deck_pool(key)) if i in df.index]
    random.shuffle(pool)
    d = {"queue": pool, "total": len(pool), "gewusst": 0, "falsch": [], "nur_falsche": nur is not None}
    get_stats(user_id).setdefault("durchgaenge", {})[key] = d
    save_stats()
    return d


def get_deck(user_id, key=None):
    key = key or deck_key(user_id)
    d = get_stats(user_id).setdefault("durchgaenge", {}).get(key)
    if d is None:
        return neuer_durchgang(user_id, key)
    gueltig = set(df.index)          # falls die Fragenbank aktualisiert wurde
    d["queue"] = [i for i in d["queue"] if i in gueltig]
    return d


def deck_abschliessen(user_id, idx, gewusst):
    d = get_deck(user_id)
    if idx in d["queue"]:
        d["queue"].remove(idx)
        if gewusst:
            d["gewusst"] += 1
        else:
            d["falsch"].append(int(idx))
    save_stats()


def deck_kopf(user_id):
    d = get_deck(user_id)
    return f"{d['total'] - len(d['queue']) + 1}/{d['total']}"


async def show_durchgang_ende(context, chat_id, user_id):
    key = deck_key(user_id)
    d = get_deck(user_id)
    falsch = len(d["falsch"])
    art = " (Wiederholung der falschen)" if d.get("nur_falsche") else ""
    quote = round(d["gewusst"] / d["total"] * 100) if d["total"] else 0
    text = (f"🎉 Durchgang geschafft!\n\nAlle {d['total']} Fragen{art} – {deck_name(key)} – sind beantwortet.\n\n"
            f"✅ Auf Anhieb richtig: {d['gewusst']} ({quote} %)\n❌ Mit Fehler: {falsch}\n\nWie geht es weiter?")
    rows = [[btn(f"🔁 Alle {len(deck_pool(key))} Fragen nochmal", "deck_alle")]]
    if falsch:
        rows.append([btn(f"❌ Nur die {falsch} falschen wiederholen", "deck_falsch")])
    rows.append([btn("🗂 Anderes Thema", "topic_menu"), btn("🔙 Lernmenü", "menu_learn")])
    await show(context, chat_id, user_id, text, rows)


def get_random_index(user_id):
    thema = user_topic.get(user_id)
    pool = df[df["Thema"] == thema] if thema else df
    indices = pool.index.tolist()
    if len(indices) > 1 and user_id in last_question:
        indices = [i for i in indices if i != last_question[user_id]]
    idx = random.choice(indices)
    last_question[user_id] = idx
    return idx


def learn_buttons(user_id):
    idx = user_questions[user_id]
    rows = answer_buttons(user_view[user_id], "l", idx, user_selection.get(user_id))
    rows.append([btn("📊 Statistik", "menu_stats"), btn("🛑 Beenden", "menu_exit")])
    return rows


async def send_question(context, chat_id, user_id, neu=True):
    if neu or user_id not in user_view:
        if neu:
            d = get_deck(user_id)
            if not d["queue"]:
                await show_durchgang_ende(context, chat_id, user_id)
                return
            user_questions[user_id] = d["queue"][0]
            user_attempts[user_id] = 1
        user_view[user_id] = gemischt(df.loc[user_questions[user_id]])
    frage = user_view[user_id]
    user_selection[user_id] = leer_auswahl(frage)
    await show(context, chat_id, user_id, f"📚 AP2 Frage {deck_kopf(user_id)} – {frage['Thema']}\n\n{question_body(frage)}",
               learn_buttons(user_id))


def after_answer_buttons():
    return [
        [btn("➡️ Weiter", "next")],
        [btn("📊 Statistik", "menu_stats"), btn("🛑 Beenden", "menu_exit")],
    ]


async def handle_answer(context, chat_id, user_id, auswahl):
    idx = user_questions.get(user_id)
    if idx is None or user_id not in user_view:
        await send_question(context, chat_id, user_id)
        return
    frage = user_view[user_id]
    erklaerung = frage.get("Erklaerung")
    erklaerung = f"\n\n💡 {erklaerung}" if pd.notna(erklaerung) else ""
    ok, _, treffer, gesamt = auswerten(frage, auswahl)
    s = get_stats(user_id)

    if ok:
        punkte = 2 if user_attempts[user_id] == 1 else 1
        s["punkte"] += punkte
        s["fragen"] += 1
        record_answer(user_id, frage["Thema"], user_attempts[user_id] == 1)
        deck_abschliessen(user_id, idx, user_attempts[user_id] == 1)
        user_questions.pop(user_id, None)
        await show(context, chat_id, user_id, f"✅ Richtig!\n⭐ +{punkte} Punkte{erklaerung}",
                   after_answer_buttons(), clear=False)
        return

    if user_attempts[user_id] == 1:
        user_attempts[user_id] = 2
        hinweis = "Du hast noch einen zweiten Versuch."
        if typ(frage) == "M":
            hinweis = f"{treffer} von {gesamt} richtigen Ziffern erwischt.\n" + hinweis
        elif typ(frage) == "Z":
            hinweis = f"{treffer} von {gesamt} Zuordnungen stimmen.\n" + hinweis
        await send_question(context, chat_id, user_id, neu=False)
        await show(context, chat_id, user_id, f"❌ Falsch!\n\n{hinweis}", clear=False)
        return

    s["fragen"] += 1
    record_answer(user_id, frage["Thema"], False)
    deck_abschliessen(user_id, idx, False)
    user_questions.pop(user_id, None)
    await show(
        context, chat_id, user_id,
        f"❌ Wieder falsch!\n\nRichtig: {loesung_kurz(frage)}\n{answer_text(frage, str(frage['Correct Answer']))}{erklaerung}",
        after_answer_buttons(), clear=False,
    )


# =====================================
# PRÜFUNGSSIMULATION (wie AP1)
# =====================================

async def start_exam(context, chat_id, user_id, amount, pool=None):
    pool = df if pool is None else pool
    amount = min(amount, len(pool))
    auswahl = pool.sample(n=amount)
    exam_questions[user_id] = pd.DataFrame([gemischt(r) for _, r in auswahl.iterrows()]).reset_index(drop=True)
    exam_index[user_id] = 0
    exam_correct[user_id] = 0.0
    exam_wrong[user_id] = []
    await send_exam_question(context, chat_id, user_id)


def exam_buttons(user_id):
    i = exam_index[user_id]
    rows = answer_buttons(exam_questions[user_id].iloc[i], "e", i, exam_selection.get(user_id))
    rows.append([btn("⏹ Abbrechen", "pruefung_menu")])
    return rows


async def send_exam_question(context, chat_id, user_id):
    index = exam_index[user_id]
    frage = exam_questions[user_id].iloc[index]
    total = len(exam_questions[user_id])
    exam_selection[user_id] = leer_auswahl(frage)
    await show(context, chat_id, user_id, f"🎯 Frage {index + 1} von {total}\n\n{question_body(frage)}",
               exam_buttons(user_id))


async def handle_exam_answer(context, chat_id, user_id, auswahl):
    if user_id not in exam_questions:
        await show_exam_menu(context, chat_id, user_id)
        return

    frage = exam_questions[user_id].iloc[exam_index[user_id]]
    ok, punkte, _, _ = auswerten(frage, auswahl)
    record_answer(user_id, frage["Thema"], ok)
    exam_correct[user_id] += punkte
    if not ok:
        exam_wrong[user_id].append({"frage": frage, "chosen": auswahl_text(frage, auswahl), "punkte": punkte})

    exam_index[user_id] += 1
    if exam_index[user_id] < len(exam_questions[user_id]):
        await send_exam_question(context, chat_id, user_id)
        return

    # ---- Auswertung (Mehrfach/Zuordnung mit Teilpunkten wie bei der IHK) ----
    gesamt = len(exam_questions[user_id])
    erreicht = round(exam_correct[user_id], 2)
    quote = round(erreicht / gesamt * 100)
    voll = gesamt - len(exam_wrong[user_id])
    s = get_stats(user_id)
    s["pruefungen"].append({"fragen": gesamt, "richtig": erreicht, "quote": quote})
    save_stats()

    if quote >= 92: note = "1 (sehr gut)"
    elif quote >= 81: note = "2 (gut)"
    elif quote >= 67: note = "3 (befriedigend)"
    elif quote >= 50: note = "4 (ausreichend)"
    elif quote >= 30: note = "5 (mangelhaft)"
    else: note = "6 (ungenügend)"

    punkte_txt = f"{erreicht:g}".replace(".", ",")
    await show(
        context, chat_id, user_id,
        f"🏁 Prüfung beendet\n\n✅ Komplett richtig: {voll}\n❌ Fehler: {gesamt - voll}\n"
        f"🧮 Punkte (mit Teilpunkten): {punkte_txt} von {gesamt}\n\n"
        f"📈 Ergebnis: {quote}%\n🎓 Note nach IHK-Schlüssel: {note}",
    )

    for i, fehler in enumerate(exam_wrong[user_id], start=1):
        f = fehler["frage"]
        erkl = f"\n\n💡 {f['Erklaerung']}" if pd.notna(f.get("Erklaerung")) else ""
        teil = f" (Teilpunkte: {fehler['punkte']:.0%})" if 0 < fehler["punkte"] < 1 else ""
        await show(
            context, chat_id, user_id,
            f"❌ Fehler {i} – {f['Thema']}{teil}\n\n{f['Question']}\n\n"
            f"Deine Antwort: {fehler['chosen']}\n"
            f"Richtig: {loesung_kurz(f)}\n{answer_text(f, str(f['Correct Answer']))}{erkl}",
            clear=False,
        )

    exam_questions.pop(user_id, None)
    await show(context, chat_id, user_id, "Was möchtest du als Nächstes tun?",
               [[btn("🔁 Neue Simulation", "pruefung_menu")], [btn("🔙 Lernmenü", "menu_learn")]],
               clear=False)


# =====================================
# OFFENE PRÜFUNGSAUFGABEN
# =====================================

def task_header(a):
    if a["Uebung"]:
        return f"✏️ Übung {a['Teil']} – {a['Thema']} ({a['Punkte']} P)"
    return f"📄 IHK {a['Pruefung']} – {a['Thema']} ({a['Punkte']} P)"


# ---------- Durchgänge für offene Aufgaben ----------

def task_key(filt):
    art, *wert = filt or ("alle",)
    return art if art == "alle" else f"{art}:{wert[0]}"


def task_pool(key):
    art, _, wert = key.partition(":")
    if art == "teil":
        pool = aufgaben[aufgaben["Teil"] == wert]
    elif art == "uebung":
        pool = aufgaben[(aufgaben["Teil"] == wert) & aufgaben["Uebung"]]
    elif art == "pruefung":
        pool = aufgaben[aufgaben["Pruefung"] == wert]
    else:
        pool = aufgaben
    return [int(i) for i in pool.index]


def neuer_task_durchgang(user_id, key, nur=None):
    pool = [int(i) for i in (nur if nur is not None else task_pool(key)) if i in aufgaben.index]
    if not key.startswith("pruefung:"):
        random.shuffle(pool)          # einzelne Prüfung: Original-Reihenfolge 1a, 1b, 2a …
    d = {"queue": pool, "total": len(pool), "gewusst": 0, "falsch": [], "nur_falsche": nur is not None}
    get_stats(user_id).setdefault("aufgaben_durchgaenge", {})[key] = d
    save_stats()
    return d


def get_task_deck(user_id, key=None):
    key = key or task_key(task_filter.get(user_id))
    d = get_stats(user_id).setdefault("aufgaben_durchgaenge", {}).get(key)
    if d is None:
        return neuer_task_durchgang(user_id, key)
    gueltig = set(aufgaben.index)
    d["queue"] = [i for i in d["queue"] if i in gueltig]
    return d


def task_fortschritt(user_id, key):
    """Kurzinfo fürs Menü: ' ▶️ 5/38' (angefangen) oder ' ✅' (fertig)."""
    d = get_stats(user_id).get("aufgaben_durchgaenge", {}).get(key)
    if not d:
        return ""
    if not d["queue"]:
        return " ✅"
    erledigt = d["total"] - len(d["queue"])
    return f" ▶️ {erledigt}/{d['total']}" if erledigt else ""


async def show_task_ende(context, chat_id, user_id):
    key = task_key(task_filter.get(user_id))
    d = get_task_deck(user_id)
    falsch = len(d["falsch"])
    art = " (Wiederholung der nicht gewussten)" if d.get("nur_falsche") else ""
    quote = round(d["gewusst"] / d["total"] * 100) if d["total"] else 0
    rows = [[btn(f"🔁 Alle {len(task_pool(key))} Aufgaben nochmal", "tdeck_alle")]]
    if falsch:
        rows.append([btn(f"👎 Nur die {falsch} nicht gewussten", "tdeck_falsch")])
    rows.append([btn("🔙 Zurück", task_back.get(user_id, "task_menu"))])
    await show(context, chat_id, user_id,
               f"🎉 Durchgang geschafft!\n\nAlle {d['total']} Aufgaben{art} sind durch.\n\n"
               f"👍 Gewusst: {d['gewusst']} ({quote} %)\n👎 Nicht gewusst: {falsch}\n\nWie geht es weiter?", rows)


async def send_task(context, chat_id, user_id):
    d = get_task_deck(user_id)
    if not d["queue"]:
        await show_task_ende(context, chat_id, user_id)
        return
    idx = d["queue"][0]
    task_current[user_id] = idx
    a = aufgaben.loc[idx]
    nr = d["total"] - len(d["queue"]) + 1
    await show(
        context, chat_id, user_id,
        f"Aufgabe {nr}/{d['total']} · {task_header(a)}\n\n{a['Aufgabe']}\n\n"
        "✍️ Löse die Aufgabe zuerst selbst.",
        [[btn("💡 Lösung zeigen", "task_solution")],
         [btn("⏭ Später", "task_skip"), btn("🔙 Zurück", task_back.get(user_id, "task_menu"))]],
    )


def task_abschliessen(user_id, idx, gewusst):
    d = get_task_deck(user_id)
    if idx in d["queue"]:
        d["queue"].remove(idx)
        if gewusst:
            d["gewusst"] += 1
        else:
            d["falsch"].append(int(idx))
    save_stats()


def task_spaeter(user_id):
    d = get_task_deck(user_id)
    idx = task_current.get(user_id)
    if idx in d["queue"] and len(d["queue"]) > 1:
        d["queue"].remove(idx)
        d["queue"].append(idx)
        save_stats()


async def send_task_solution(context, chat_id, user_id):
    idx = task_current.get(user_id)
    if idx is None:
        await show_task_menu(context, chat_id, user_id)
        return
    a = aufgaben.loc[idx]
    await show(
        context, chat_id, user_id,
        f"✅ Musterlösung\n\n{a['Loesung']}\n\nWie lief es?",
        [[btn("👍 Gewusst", f"task_rate_yes_{idx}"), btn("👎 Nicht gewusst", f"task_rate_no_{idx}")],
         [btn("⏭ Später nochmal", "task_skip"), btn("🔙 Zurück", task_back.get(user_id, "task_menu"))]],
        clear=False,
    )


# =====================================
# BEFEHLE
# =====================================

# ---------- Zugang (optional): ALLOWED_USERS=123,456 in der .env ----------
ERLAUBT = {x.strip() for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()}


def erlaubt(user_id):
    return not ERLAUBT or str(user_id) in ERLAUBT


async def zugang_ok(update, context):
    uid = update.effective_user.id
    if erlaubt(uid):
        return True
    text = f"🔒 Dieser Bot ist privat.\n\nDeine Telegram-ID: {uid}\nSchick sie dem Betreiber, damit er dich freischaltet."
    if update.callback_query:
        await update.callback_query.answer("🔒 Kein Zugang – schick /id an den Betreiber.", show_alert=True)
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    return False


async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text=f"🆔 Deine Telegram-ID: {update.effective_user.id}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await zugang_ok(update, context):
        return
    user_id = update.effective_user.id
    get_stats(user_id)
    save_stats()
    await show_main(context, update.effective_chat.id, user_id)


async def frage_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await zugang_ok(update, context):
        return
    user_id = update.effective_user.id
    get_stats(user_id)
    user_topic[user_id] = None
    await send_question(context, update.effective_chat.id, user_id)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await zugang_ok(update, context):
        return
    await show_stats(context, update.effective_chat.id, update.effective_user.id)


async def pruefung_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await zugang_ok(update, context):
        return
    await show_exam_menu(context, update.effective_chat.id, update.effective_user.id)


async def aufgabe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await zugang_ok(update, context):
        return
    await show_task_menu(context, update.effective_chat.id, update.effective_user.id)


# =====================================
# BUTTONS
# =====================================

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await zugang_ok(update, context):
        return
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data or ""

    # "Antwort prüfen" ohne vollständige Auswahl → nur Hinweis (Callback darf nur einmal beantwortet werden)
    if data.startswith(("lc_", "ec_")):
        if data[0] == "l":
            frage = user_view.get(user_id) if user_questions.get(user_id) is not None else None
            sel = user_selection.get(user_id)
        else:
            frage = exam_questions[user_id].iloc[exam_index[user_id]] if user_id in exam_questions else None
            sel = exam_selection.get(user_id)
        if frage is not None:
            if typ(frage) == "M" and not sel:
                await query.answer("Bitte erst mindestens eine Ziffer antippen.", show_alert=True)
                return
            if typ(frage) == "Z" and len(sel or {}) < len(items(frage)):
                await query.answer("Bitte erst jeder Zeile a), b) … eine Ziffer zuordnen.", show_alert=True)
                return
    await query.answer()

    # Die angeklickte Nachricht gehört immer zur aktuellen Ansicht
    if query.message.message_id not in screen_messages.setdefault(user_id, []):
        screen_messages[user_id].append(query.message.message_id)

    get_stats(user_id)

    # ---------- Antworten: l|e + a/t/z/c/n + _<token>[_…] ----------
    if len(data) > 3 and data[0] in "le" and data[1] in "atzcn" and data[2] == "_":
        teile = data.split("_")
        try:
            token = int(teile[1])
        except (IndexError, ValueError):
            return
        lernen = data[0] == "l"
        if lernen:
            if user_questions.get(user_id) != token:
                return  # alter Button / Doppelklick
            sel_store = user_selection
        else:
            if user_id not in exam_questions or exam_index.get(user_id) != token:
                return
            sel_store = exam_selection
        art = data[1]
        if art == "n":
            return
        if art == "a":
            auswahl = teile[2]
        elif art == "t":
            sel_store.setdefault(user_id, set()).symmetric_difference_update({teile[2]})
        elif art == "z":
            zu = sel_store.setdefault(user_id, {})
            i, k = int(teile[2]), int(teile[3])
            if zu.get(i) == k:
                zu.pop(i)
            else:
                zu[i] = k
        if art in ("t", "z"):
            try:
                await query.edit_message_reply_markup(
                    InlineKeyboardMarkup(learn_buttons(user_id) if lernen else exam_buttons(user_id)))
            except BadRequest:
                pass
            return
        if art == "c":
            auswahl = sel_store.get(user_id)
        if lernen:
            await handle_answer(context, chat_id, user_id, auswahl)
        else:
            await handle_exam_answer(context, chat_id, user_id, auswahl)
        return

    if data in ("menu_home", "menu_exit"):
        user_questions.pop(user_id, None)
        await show_main(context, chat_id, user_id)

    elif data == "menu_learn":
        await show_learn(context, chat_id, user_id)

    elif data == "menu_stats":
        await show_stats(context, chat_id, user_id)

    elif data == "menu_info":
        await show_info(context, chat_id, user_id)

    elif data == "stats_reset":
        await show(context, chat_id, user_id, "⚠️ Statistik wirklich zurücksetzen?",
                   [[btn("Ja, zurücksetzen", "stats_reset_ok")], [btn("Abbrechen", "menu_stats")]])

    elif data == "stats_reset_ok":
        user_stats.pop(str(user_id), None)
        get_stats(user_id)
        save_stats()
        await show_stats(context, chat_id, user_id)

    # ---------- Lernen ----------
    elif data == "learn_random":
        user_topic[user_id] = None
        await send_question(context, chat_id, user_id)

    elif data == "topic_menu":
        await show_topics(context, chat_id, user_id)

    elif data.startswith("topic_"):
        i = int(data.split("_")[1])
        if 0 <= i < len(THEMEN):
            user_topic[user_id] = THEMEN[i]
        await send_question(context, chat_id, user_id)

    elif data == "next":
        await send_question(context, chat_id, user_id)
    elif data == "deck_alle":
        neuer_durchgang(user_id, deck_key(user_id))
        await send_question(context, chat_id, user_id)
    elif data == "deck_falsch":
        alt = get_deck(user_id)
        neuer_durchgang(user_id, deck_key(user_id), nur=list(alt["falsch"]))
        await send_question(context, chat_id, user_id)


    # ---------- Prüfungssimulation ----------
    elif data == "pruefung_menu":
        exam_questions.pop(user_id, None)
        await show_exam_menu(context, chat_id, user_id)


    elif data in ("exam_20", "exam_40", "exam_60"):
        await start_exam(context, chat_id, user_id, int(data.split("_")[1]))

    elif data == "exam_wiso":
        await start_exam(context, chat_id, user_id, 30, WISO_FRAGEN)

    # ---------- Prüfungsaufgaben ----------
    elif data == "task_menu":
        task_back[user_id] = "task_menu"
        await show_task_menu(context, chat_id, user_id)

    elif data.startswith("taskteil_"):
        i = int(data.split("_")[1])
        if 0 <= i < len(TEILE):
            await show_teil_menu(context, chat_id, user_id, i)
        else:
            await show_task_menu(context, chat_id, user_id)

    elif data.startswith("taskset_"):
        wahl = data.split("_")[1]
        try:
            if wahl == "alle":
                task_filter[user_id] = ("alle",)
                task_back[user_id] = "task_menu"
            elif wahl[0] == "t":
                task_filter[user_id] = ("teil", TEILE[int(wahl[1:])][0])
            elif wahl[0] == "u":
                task_filter[user_id] = ("uebung", TEILE[int(wahl[1:])][0])
            elif wahl[0] == "p":
                task_filter[user_id] = ("pruefung", PRUEFUNGEN[int(wahl[1:])])
        except (ValueError, IndexError):
            task_filter[user_id] = ("alle",)
        await send_task(context, chat_id, user_id)

    elif data in ("task_next", "task_skip"):
        if data == "task_skip":
            task_spaeter(user_id)
        await send_task(context, chat_id, user_id)

    elif data == "tdeck_alle":
        neuer_task_durchgang(user_id, task_key(task_filter.get(user_id)))
        await send_task(context, chat_id, user_id)

    elif data == "tdeck_falsch":
        alt = get_task_deck(user_id)
        neuer_task_durchgang(user_id, task_key(task_filter.get(user_id)), nur=list(alt["falsch"]))
        await send_task(context, chat_id, user_id)

    elif data == "task_solution":
        await send_task_solution(context, chat_id, user_id)

    elif data.startswith(("task_rate_yes", "task_rate_no")):
        gewusst = data.startswith("task_rate_yes")
        teile = data.split("_")
        idx = task_current.get(user_id)
        if len(teile) == 4 and teile[3] != str(idx):
            return   # Klick auf eine ältere Aufgabe (z. B. Doppelklick) → ignorieren
        s = get_stats(user_id)
        if idx is not None and idx in get_task_deck(user_id)["queue"]:
            s["aufgaben"]["gewusst" if gewusst else "nicht_gewusst"] += 1
            record_answer(user_id, aufgaben.loc[idx, "Thema"], gewusst)
            task_abschliessen(user_id, idx, gewusst)
        save_stats()
        await send_task(context, chat_id, user_id)

    else:
        # Buttons aus einer älteren Bot-Version → sauber ins Hauptmenü
        await show_main(context, chat_id, user_id)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"⚠️ Fehler: {context.error!r}")


# =====================================
# APP
# =====================================

def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN fehlt (Umgebungsvariable setzen, siehe README).")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", id_cmd))
    app.add_handler(CommandHandler("frage", frage_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("pruefung", pruefung_cmd))
    app.add_handler(CommandHandler("aufgabe", aufgabe_cmd))
    app.add_handler(CallbackQueryHandler(button_click))
    app.add_error_handler(on_error)

    print(f"🤖 AP2 Trainer läuft... ({len(df)} Fragen, davon {int((df['Typ'] != 'E').sum())} Mehrfach/Zuordnung, {len(aufgaben)} Aufgaben, {len(THEMEN)} Themen)")
    app.run_polling()


if __name__ == "__main__":
    main()

"""
AP1 Trainer Bot – Fachinformatiker (Teil 1: Einrichten eines IT-gestützten Arbeitsplatzes)

Überarbeitete Version (gleiche Technik wie der AP2-Trainer):
- Mehrfachauswahl-Fragen (z. B. Lösung "ACE") funktionieren jetzt: Antworten an-/abhaken, dann prüfen
- Lernen nach Thema, Trefferquote je Thema, Hinweis auf schwache Themen
- Prüfungssimulation mit allen Fragen inkl. Mehrfachauswahl, Note nach IHK-Schlüssel
- sauberer Chat: alte Nachrichten werden aufgeräumt, keine liegen gebliebenen Hinweise
- keine Abstürze mehr bei alten Buttons, Doppelklicks oder nach einem Neustart
- bestehende stats.json bleibt erhalten (Punkte und Fragen werden übernommen)
"""

import json
import logging
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

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.WARNING)
log = logging.getLogger("ap1bot")

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

df = pd.read_csv(os.path.join(BASE_DIR, "fragen.csv"), encoding="utf-8-sig")
df["Correct Answer"] = df["Correct Answer"].astype(str).str.strip().str.upper()
if "Thema" not in df.columns:
    df["Thema"] = "Allgemein"
df["Thema"] = df["Thema"].fillna("Allgemein")


def ist_mehrfach(frage):
    """Mehrfachauswahl, wenn mehrere Lösungsbuchstaben oder der Text es sagt."""
    q = str(frage["Question"]).lower()
    return len(frage["Correct Answer"]) > 1 or q.startswith("mehrfachauswahl") or "wähle alle" in q


df["Mehrfach"] = df.apply(ist_mehrfach, axis=1)
THEMEN = sorted(t for t in df["Thema"].unique() if t != "Allgemein") + (
    ["Allgemein"] if (df["Thema"] == "Allgemein").any() else [])

# =====================================
# SPEICHER
# =====================================

if os.path.exists(STATS_FILE):
    with open(STATS_FILE, "r", encoding="utf-8") as f:
        user_stats = json.load(f)
else:
    user_stats = {}

screen_messages = {}   # user_id -> [message_ids] der aktuellen Ansicht
user_questions = {}    # user_id -> Index der aktuellen Lernfrage
user_view = {}         # user_id -> gemischte Ansicht der aktuellen Frage
user_attempts = {}     # user_id -> Versuch 1 oder 2
user_selection = {}    # user_id -> gewählte Buchstaben (Mehrfachauswahl)
user_topic = {}        # user_id -> Thema oder None
last_question = {}     # user_id -> letzte Frage (keine direkte Wiederholung)

exam_questions = {}    # user_id -> DataFrame
exam_index = {}
exam_correct = {}
exam_wrong = {}
exam_selection = {}


# =====================================
# HILFSFUNKTIONEN
# =====================================

def save_stats():
    tmp = STATS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(user_stats, f, ensure_ascii=False, indent=4)
    os.replace(tmp, STATS_FILE)   # atomar: keine kaputte Datei bei Absturz


def get_stats(user_id):
    key = str(user_id)
    if key not in user_stats:
        user_stats[key] = {"punkte": 0, "fragen": 0}
    s = user_stats[key]
    s.setdefault("punkte", 0)
    s.setdefault("fragen", 0)
    s.setdefault("themen", {})
    s.setdefault("pruefungen", [])
    return s


def record_answer(user_id, thema, correct):
    t = get_stats(user_id)["themen"].setdefault(thema, {"richtig": 0, "gesamt": 0})
    t["gesamt"] += 1
    if correct:
        t["richtig"] += 1


async def clear_screen(context, chat_id, user_id):
    for msg_id in screen_messages.get(user_id, []):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    screen_messages[user_id] = []


async def show(context, chat_id, user_id, text, buttons=None, clear=True):
    if clear:
        await clear_screen(context, chat_id, user_id)
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    msg = await context.bot.send_message(chat_id=chat_id, text=text[:4096], reply_markup=markup)
    screen_messages.setdefault(user_id, []).append(msg.message_id)
    return msg


def btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def optionen(frage):
    return [o for o in OPTIONS if o in frage and pd.notna(frage[o]) and str(frage[o]).strip()]


def question_body(frage):
    text = f"{frage['Question']}\n\n"
    for o in optionen(frage):
        text += f"{o}) {frage[o]}\n"
    if frage["Mehrfach"]:
        text += "\n☑️ Mehrfachauswahl: Antworten antippen, dann „Antwort prüfen“."
    return text


def answer_text(frage, letters):
    return "\n".join(f"{b}) {frage[b]}" for b in letters if b in optionen(frage))


def answer_buttons(frage, prefix, token, selected=None):
    """Einfachauswahl: ein Tipp = Antwort. Mehrfachauswahl: an-/abhaken + prüfen."""
    rows, row = [], []
    for o in optionen(frage):
        if frage["Mehrfach"]:
            label = f"✅ {o}" if selected and o in selected else o
            row.append(btn(label, f"{prefix}t_{token}_{o}"))
        else:
            row.append(btn(o, f"{prefix}a_{token}_{o}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if frage["Mehrfach"]:
        rows.append([btn("✔️ Antwort prüfen", f"{prefix}c_{token}")])
    return rows


_FEST = re.compile(r"\b(alle|keine)\s+(der\s+)?(oben\s+)?(genannten|aufgeführten|obigen)\b|\boben\s+genannt", re.I)


def gemischt(frage):
    """Kopie der Frage mit zufälliger Antwort-Reihenfolge; die Lösung wird passend umgerechnet.
    Bleibt unverändert, wenn sich Antworten aufeinander beziehen ("Keine der genannten" …)."""
    f = frage.copy()
    opts = optionen(f)
    if len(opts) < 2 or any(_FEST.search(str(f[o])) for o in opts):
        return f
    perm = opts[:]
    random.shuffle(perm)
    alt_zu_neu = {}
    for neu, alt in zip(opts, perm):
        f[neu] = frage[alt]
        alt_zu_neu[alt] = neu
    f["Correct Answer"] = "".join(sorted(alt_zu_neu[b] for b in frage["Correct Answer"] if b in alt_zu_neu))
    return f


def note_fuer(quote):
    if quote >= 92: return "1 (sehr gut)"
    if quote >= 81: return "2 (gut)"
    if quote >= 67: return "3 (befriedigend)"
    if quote >= 50: return "4 (ausreichend)"
    if quote >= 30: return "5 (mangelhaft)"
    return "6 (ungenügend)"


# =====================================
# MENÜS
# =====================================

def main_menu():
    return [
        [btn("📚 Lernen", "menu_learn")],
        [btn("📊 Statistik", "menu_stats")],
        [btn("ℹ️ Prüfungsinfo AP1", "menu_info")],
    ]


async def show_main(context, chat_id, user_id):
    await show(context, chat_id, user_id, "🎓 AP1 Trainer\n\nWähle eine Option:", main_menu())


async def show_learn(context, chat_id, user_id):
    await show(context, chat_id, user_id, "📚 Lernen\n\nWähle eine Option:", [
        [btn("🎯 Zufallsfragen (alle Themen)", "learn_random")],
        [btn("🗂 Nach Thema lernen", "topic_menu")],
        [btn("📝 Prüfungssimulation", "pruefung_menu")],
        [btn("🔙 Zurück", "menu_home")],
    ])


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
    rows = [[btn(f"🎯 {n} Fragen", f"exam_{n}")] for n in (20, 40, 60)]
    rows.append([btn("🔙 Zurück", "menu_learn")])
    await show(context, chat_id, user_id,
               "📝 Prüfungssimulation\n\nGemischte Fragen inkl. Mehrfachauswahl. "
               "Mehrfachauswahl zählt nur, wenn genau die richtigen Antworten gewählt sind.\n\n"
               "Wähle die Anzahl der Fragen:", rows)


PRUEFUNGSINFO = (
    "ℹ️ AP1 – Teil 1 der Abschlussprüfung Fachinformatiker (AO 2020)\n\n"
    "📝 Prüfungsbereich: Einrichten eines IT-gestützten Arbeitsplatzes\n"
    "• schriftlich, 90 Minuten\n"
    "• zählt 20 % zum Gesamtergebnis\n"
    "• keine Zwischenprüfung – die AP1 ist schon Teil der Abschlussnote\n"
    "• für alle Fachrichtungen gleich\n\n"
    "📌 Teil 2 (später): Projekt 50 %, zwei Fachbereiche je 10 %, WiSo 10 %\n\n"
    "🎯 Notenschlüssel: 1 ab 92 | 2 ab 81 | 3 ab 67 | 4 ab 50 | 5 ab 30 Punkte\n\n"
    "Typische Themen: Kundenberatung und Bedarfsanalyse, Angebote und Kosten, Projektplanung, "
    "Hardware und Speicherberechnung, Netzwerk (IP, DNS, DHCP, WLAN), IT-Sicherheit und Datenschutz, "
    "Pseudocode, Qualitätssicherung und Übergabe.\n\n"
    "Quelle: IHK-Merkblatt Fachinformatiker (VO vom 28.02.2020)."
)


async def show_info(context, chat_id, user_id):
    await show(context, chat_id, user_id, PRUEFUNGSINFO, [[btn("🔙 Hauptmenü", "menu_home")]])


async def show_stats(context, chat_id, user_id):
    s = get_stats(user_id)
    text = f"📊 Statistik\n\n⭐ Punkte: {s['punkte']}\n📚 Fragen: {s['fragen']}\n"

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
        text += "\n📝 Letzte Simulationen: " + ", ".join(f"{p['quote']}%" for p in s["pruefungen"][-5:]) + "\n"

    await show(context, chat_id, user_id, text, [
        [btn("📚 Lernen", "menu_learn")],
        [btn("🗑 Statistik zurücksetzen", "stats_reset")],
        [btn("🔙 Hauptmenü", "menu_home")],
    ])


# =====================================
# LERNMODUS
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
    frage = user_view[user_id]
    rows = answer_buttons(frage, "l", user_questions[user_id], user_selection.get(user_id))
    rows.append([btn("📊 Statistik", "menu_stats"), btn("🛑 Beenden", "menu_exit")])
    return rows


async def send_question(context, chat_id, user_id):
    d = get_deck(user_id)
    if not d["queue"]:
        await show_durchgang_ende(context, chat_id, user_id)
        return
    idx = d["queue"][0]
    user_questions[user_id] = idx
    user_attempts[user_id] = 1
    user_selection[user_id] = set()
    frage = user_view[user_id] = gemischt(df.loc[idx])
    await show(context, chat_id, user_id, f"📚 AP1 Frage {deck_kopf(user_id)} – {frage['Thema']}\n\n{question_body(frage)}",
               learn_buttons(user_id))


def after_answer_buttons():
    return [[btn("➡️ Weiter", "next")],
            [btn("📊 Statistik", "menu_stats"), btn("🛑 Beenden", "menu_exit")]]


async def evaluate_learn(context, chat_id, user_id, gewaehlt):
    idx = user_questions.get(user_id)
    if idx is None or user_id not in user_view:
        return
    frage = user_view[user_id]
    richtig = frage["Correct Answer"]
    s = get_stats(user_id)

    if set(gewaehlt) == set(richtig):
        punkte = 2 if user_attempts[user_id] == 1 else 1
        s["punkte"] += punkte
        s["fragen"] += 1
        record_answer(user_id, frage["Thema"], user_attempts[user_id] == 1)
        deck_abschliessen(user_id, idx, user_attempts[user_id] == 1)
        user_questions.pop(user_id, None)
        await show(context, chat_id, user_id, f"✅ Richtig!\n⭐ +{punkte} Punkte",
                   after_answer_buttons(), clear=False)
        return

    if user_attempts[user_id] == 1:
        user_attempts[user_id] = 2
        user_selection[user_id] = set()
        hinweis = "Du hast noch einen zweiten Versuch."
        if frage["Mehrfach"]:
            treffer = len(set(gewaehlt) & set(richtig))
            hinweis = (f"Du hast {treffer} von {len(richtig)} richtigen Antworten erwischt"
                       f"{' und falsche dabei' if set(gewaehlt) - set(richtig) else ''}.\n" + hinweis)
        # Frage neu zeichnen (Haken zurückgesetzt), Hinweis darunter
        await show(context, chat_id, user_id, f"📚 AP1 Frage {deck_kopf(user_id)} – {frage['Thema']}\n\n{question_body(frage)}",
                   learn_buttons(user_id))
        await show(context, chat_id, user_id, f"❌ Falsch!\n\n{hinweis}", clear=False)
        return

    s["fragen"] += 1
    record_answer(user_id, frage["Thema"], False)
    deck_abschliessen(user_id, idx, False)
    user_questions.pop(user_id, None)
    await show(context, chat_id, user_id,
               f"❌ Wieder falsch!\n\nRichtige Antwort ({richtig}):\n{answer_text(frage, richtig)}",
               after_answer_buttons(), clear=False)


# =====================================
# PRÜFUNGSSIMULATION
# =====================================

async def start_exam(context, chat_id, user_id, amount):
    amount = min(amount, len(df))
    auswahl = df.sample(n=amount)
    exam_questions[user_id] = pd.DataFrame([gemischt(r) for _, r in auswahl.iterrows()]).reset_index(drop=True)
    exam_index[user_id] = 0
    exam_correct[user_id] = 0
    exam_wrong[user_id] = []
    exam_selection[user_id] = set()
    await send_exam_question(context, chat_id, user_id)


def exam_buttons(user_id):
    i = exam_index[user_id]
    frage = exam_questions[user_id].iloc[i]
    rows = answer_buttons(frage, "e", i, exam_selection.get(user_id))
    rows.append([btn("⏹ Abbrechen", "pruefung_menu")])
    return rows


async def send_exam_question(context, chat_id, user_id):
    i = exam_index[user_id]
    frage = exam_questions[user_id].iloc[i]
    total = len(exam_questions[user_id])
    exam_selection[user_id] = set()
    await show(context, chat_id, user_id, f"🎯 Frage {i + 1} von {total}\n\n{question_body(frage)}",
               exam_buttons(user_id))


async def evaluate_exam(context, chat_id, user_id, gewaehlt):
    frage = exam_questions[user_id].iloc[exam_index[user_id]]
    richtig = frage["Correct Answer"]
    korrekt = set(gewaehlt) == set(richtig)
    record_answer(user_id, frage["Thema"], korrekt)
    if korrekt:
        exam_correct[user_id] += 1
    else:
        exam_wrong[user_id].append({"frage": frage, "chosen": "".join(sorted(gewaehlt)) or "–"})

    exam_index[user_id] += 1
    if exam_index[user_id] < len(exam_questions[user_id]):
        await send_exam_question(context, chat_id, user_id)
        return

    gesamt = len(exam_questions[user_id])
    anzahl = exam_correct[user_id]
    quote = round(anzahl / gesamt * 100)
    s = get_stats(user_id)
    s["pruefungen"].append({"fragen": gesamt, "richtig": anzahl, "quote": quote})
    save_stats()

    await show(context, chat_id, user_id,
               f"🏁 Prüfung beendet\n\n✅ Richtig: {anzahl}\n❌ Falsch: {gesamt - anzahl}\n\n"
               f"📈 Ergebnis: {quote}%\n🎓 Note nach IHK-Schlüssel: {note_fuer(quote)}")

    for n, fehler in enumerate(exam_wrong[user_id], start=1):
        f = fehler["frage"]
        await show(context, chat_id, user_id,
                   f"❌ Fehler {n} – {f['Thema']}\n\n{f['Question']}\n\n"
                   f"Deine Antwort: {fehler['chosen']}\n"
                   f"Richtig ({f['Correct Answer']}):\n{answer_text(f, f['Correct Answer'])}",
                   clear=False)

    exam_questions.pop(user_id, None)
    await show(context, chat_id, user_id, "Was möchtest du als Nächstes tun?",
               [[btn("🔁 Neue Simulation", "pruefung_menu")], [btn("🔙 Lernmenü", "menu_learn")]],
               clear=False)


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
    user_topic[update.effective_user.id] = None
    await send_question(context, update.effective_chat.id, update.effective_user.id)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await zugang_ok(update, context):
        return
    await show_stats(context, update.effective_chat.id, update.effective_user.id)


async def pruefung_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await zugang_ok(update, context):
        return
    await show_exam_menu(context, update.effective_chat.id, update.effective_user.id)


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

    # "Antwort prüfen" ohne Auswahl → nur Hinweis (ein Callback darf nur einmal beantwortet werden)
    if data.startswith(("lc_", "ec_")):
        sel = user_selection.get(user_id) if data[0] == "l" else exam_selection.get(user_id)
        if not sel:
            await query.answer("Bitte erst mindestens eine Antwort antippen.", show_alert=True)
            return
    await query.answer()

    if query.message.message_id not in screen_messages.setdefault(user_id, []):
        screen_messages[user_id].append(query.message.message_id)
    get_stats(user_id)

    # ---------- Antworten Lernmodus: la_/lt_/lc_<frage>[_<Buchstabe>] ----------
    if data[:3] in ("la_", "lt_", "lc_"):
        teile = data.split("_")
        try:
            token = int(teile[1])
        except (IndexError, ValueError):
            return
        if user_questions.get(user_id) != token:
            return  # alter Button oder Doppelklick → ignorieren
        art = data[1]
        if art == "a":
            await evaluate_learn(context, chat_id, user_id, {teile[2]})
        elif art == "t":
            sel = user_selection.setdefault(user_id, set())
            sel.symmetric_difference_update({teile[2]})
            try:
                await query.edit_message_reply_markup(InlineKeyboardMarkup(learn_buttons(user_id)))
            except BadRequest:
                pass
        elif art == "c":
            await evaluate_learn(context, chat_id, user_id, set(user_selection[user_id]))
        return

    # ---------- Antworten Prüfung: ea_/et_/ec_<index>[_<Buchstabe>] ----------
    if data[:3] in ("ea_", "et_", "ec_"):
        teile = data.split("_")
        try:
            token = int(teile[1])
        except (IndexError, ValueError):
            return
        if user_id not in exam_questions or exam_index.get(user_id) != token:
            return
        art = data[1]
        if art == "a":
            await evaluate_exam(context, chat_id, user_id, {teile[2]})
        elif art == "t":
            exam_selection.setdefault(user_id, set()).symmetric_difference_update({teile[2]})
            try:
                await query.edit_message_reply_markup(InlineKeyboardMarkup(exam_buttons(user_id)))
            except BadRequest:
                pass
        elif art == "c":
            await evaluate_exam(context, chat_id, user_id, set(exam_selection[user_id]))
        return

    # ---------- Navigation ----------
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
    elif data == "learn_random":
        user_topic[user_id] = None
        await send_question(context, chat_id, user_id)
    elif data == "topic_menu":
        await show_topics(context, chat_id, user_id)
    elif data.startswith("topic_"):
        try:
            i = int(data.split("_")[1])
            user_topic[user_id] = THEMEN[i]
        except (ValueError, IndexError):
            user_topic[user_id] = None
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
    elif data == "pruefung_menu":
        exam_questions.pop(user_id, None)
        await show_exam_menu(context, chat_id, user_id)
    elif data in ("exam_20", "exam_40", "exam_60"):
        await start_exam(context, chat_id, user_id, int(data.split("_")[1]))
    else:
        # Buttons aus der alten Bot-Version (z. B. answer_A) → sauber neu starten
        await show_main(context, chat_id, user_id)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Fehler bei Update %s", update, exc_info=context.error)


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
    app.add_handler(CallbackQueryHandler(button_click))
    app.add_error_handler(on_error)
    print(f"🤖 AP1 Trainer läuft... ({len(df)} Fragen, davon {int(df['Mehrfach'].sum())} Mehrfachauswahl, "
          f"{len(THEMEN)} Themen)")
    app.run_polling()


if __name__ == "__main__":
    main()

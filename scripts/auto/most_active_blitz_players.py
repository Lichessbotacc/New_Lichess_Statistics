#!/usr/bin/env python3
"""
most_active_blitz_players.py

Findet moeglichst viele AKTIVE Lichess-Blitz-Spieler (nicht nur die
staerksten nach Rating) und baut daraus ein "Wer hat in den letzten
7 Tagen die meisten Blitz-Partien gespielt"-Ranking (Top 100).

Gedacht fuer einen GitHub-Action-Cron-Trigger alle 6 Stunden (siehe
.github/workflows/blitz-activity.yml), nicht als Dauer-Loop.

-------------------------------------------------------------------
WIE DER SPIELER-POOL WAECHST
-------------------------------------------------------------------
Lichess hat keine oeffentliche API, die "alle aktiven Spieler" auflistet.
Stattdessen sammelt dieses Skript den Pool aus mehreren Quellen und
SPEICHERT ihn dauerhaft in KNOWN_PLAYERS_FILE - der Pool waechst also mit
jedem Lauf weiter:

  1. Teilnehmer aus ALLEN erreichbaren Blitz-Arenen/Swiss-Turnieren
     (aktuell sichtbar + komplette Team-Turnierhistorie, auch
     VERGANGENE Turniere).
  2. SNOWBALL-CRAWL ueber die Partien-Gegner ("Lobby"-Ausbreitung), siehe
     Abschnitt "SNOWBALL-CRAWL" weiter unten.

Jeder Spieler bekommt sich eine "Quelle" gemerkt (turnier / lobby) - also
woher er urspruenglich in den Pool gekommen ist. Diese Quelle taucht
ueberall in der Ausgabe (Banner, Top-1000-Tabelle, status/top1000.json/.md)
mit auf.

-------------------------------------------------------------------
WARUM EIN LAUF NICHT MEHR "BEI NULL" ANFAENGT (COOLDOWNS)
-------------------------------------------------------------------
Jeder Spieler hat einen last_checked-Zeitstempel (in leaderboard.json).
Ein bereits bekannter Spieler wird nur dann erneut auf seine Partienzahl
geprueft, wenn CHECK_COOLDOWN_HOURS seit der letzten Abfrage vergangen
sind - ein komplett NEUER Spieler wird dagegen immer sofort abgefragt.
Dasselbe Prinzip gilt fuer:
  - Den Snowball-Crawl (RECRAWL_COOLDOWN_HOURS) - ein Spieler, dessen
    letzte Partien schon einmal nach Gegnern durchsucht wurden, wird
    erst nach Ablauf dieser Frist erneut als Crawl-Seed benutzt.
Turniere sind weiterhin ueber known_tournaments.json dauerhaft vor
Doppel-Verarbeitung geschuetzt.

-------------------------------------------------------------------
ABWECHSELNDE ZEITBUDGET-PHASEN: LOBBY-CRAWL <-> TURNIERE
-------------------------------------------------------------------
WICHTIG (gegenueber frueheren Versionen geaendert): Der Lobby-Crawl
bekommt in JEDER Runde ZUERST sein Zeitbudget, danach erst die Turniere.
Grund: Turnier-Teilnehmerlisten koennen sehr gross sein und im schlimmsten
Fall (Rate-Limit-Backoff) lange blockieren; wuerden Turniere zuerst
laufen, koennte dabei das GESAMTE Laufzeitbudget aufgebraucht werden,
bevor der Crawl je an die Reihe kommt - dann wuerden nie neue reine
"Lobby"-Spieler (die in keinem Team, Turnier oder den Top 100 stehen)
gefunden. Mit Crawl-zuerst ist das ausgeschlossen: der Crawl bekommt
IMMER mindestens eine PHASE_SLICE_SECONDS-Zeitscheibe pro Runde, egal
wie lange Turniere brauchen wuerden.

Sobald eine der beiden Phasen wirklich fertig ist (alle Turniere
verarbeitet bzw. keine Crawl-Seeds mehr verfuegbar), faellt sie aus der
Abwechslung komplett raus - die jeweils andere Phase bekommt dann das
volle Budget, bis auch sie fertig ist oder das Gesamt-Zeitbudget
(MAX_TOTAL_RUNTIME_SECONDS) fuer diesen Lauf erreicht ist. Unfertiger
Rest wird einfach nicht als "erledigt" markiert und laeuft im naechsten
Lauf automatisch weiter.

Zusaetzlich: Ist nach dem Laden des gespeicherten Standes die Crawl-Queue
leer UND es gibt noch keine gecrawlten Spieler (typischerweise beim
allerersten Lauf oder wenn der Crawl aus irgendeinem Grund noch nie
drankam), wird die Queue vor dem eigentlichen Lauf mit einer zufaelligen
Stichprobe aus dem bestehenden Spieler-Pool "angeimpft", damit der Crawl
garantiert sofort etwas zu tun hat statt leerzulaufen.

-------------------------------------------------------------------
LIVE-RANKING WAEHREND DER SUCHE
-------------------------------------------------------------------
Das Skript wartet NICHT, bis der komplette Spieler-Pool gesammelt ist,
bevor es Partien zaehlt. Jede Quelle wird sofort nach dem Einlesen
verarbeitet, das Leaderboard sofort aktualisiert, und bei einem
Top-Ranking-Einstieg erscheint sofort ein auffaelliger Banner (inkl. Quelle).

GEAENDERT (Logging): Zusaetzlich zum Banner fuer Top-1000-Eintritte wird
JETZT fuer JEDEN tatsaechlich geprueften Spieler (nicht nur Top 1000) eine
eigene Log-Zeile ausgegeben (mit Partienzahl, aktuellem Rang und Quelle) -
siehe update_players_live(). Per Cooldown uebersprungene Spieler werden
NICHT einzeln geloggt (sonst waere die Ausgabe bei grossem Pool zu
unuebersichtlich), tauchen aber weiterhin in der Lauf-Zusammenfassung
("Uebersprungen wg. Cooldown") als Zahl auf.

-------------------------------------------------------------------
SNOWBALL-CRAWL (Gegner-basierte Pool-Erweiterung)
-------------------------------------------------------------------
  - CRAWL_QUEUE_FILE: STRIKTE FIFO-Kette neu gefundener, noch nie
    gecrawlter Spieler. Jeder Spieler nimmt seine letzten
    CRAWL_GAMES_PER_SEED Gegner mit sich, die dann selbst wieder als
    Kettenglied hinten angehaengt werden - eine im Prinzip endlose
    Kette, solange neue Gegner auftauchen.
  - KNOWN_CRAWLED_FILE: username -> Zeitpunkt des letzten Crawls.
  - Seed-Auswahl pro Crawl-Runde, in dieser Prioritaet:
      1. STRIKT FIFO aus der Queue (das eigentliche Kettenglied). Beim
         Wiederaufbau der Queue werden bereits gecrawlte Spieler
         HERAUSGEFILTERT statt erneut mitgeschleppt - sonst waechst die
         Queue mit totem Ballast und wirkt "leer", obwohl sie es nicht
         ist (fruehere Version hatte hier einen Bug).
      2. Nur wenn die (bereinigte) Queue WIRKLICH leer ist: nie
         gecrawlte Spieler zufaellig aus dem restlichen Pool, damit die
         Kette nicht komplett abreisst, wenn sie sich totgelaufen hat.
      3. Erst wenn 1+2 nicht reichen: Spieler, deren letzter Crawl
         laenger als RECRAWL_COOLDOWN_HOURS zurueckliegt.
  - Fuer jeden Seed werden die letzten CRAWL_GAMES_PER_SEED Partien
    angesehen und beide Spielernamen extrahiert. Neue Gegner werden
    sofort live verarbeitet UND ans Ende der Crawl-Queue gehaengt.
    Diese neuen Gegner koennen VOELLIG unabhaengig von Turnieren sein -
    genau das ist der Mechanismus, der echte "nur Lobby"-Spieler findet:
    ein Turnier-Spieler spielt in der freien Lobby gegen jemanden, der in
    keiner anderen Quelle je auftaucht, und dieser Gegner wird hier
    aufgenommen (source="lobby").

-------------------------------------------------------------------
KONFIGURATION
-------------------------------------------------------------------
LICHESS_TOKEN als Umgebungsvariable/GitHub Secret setzen. OHNE Token
gilt ein deutlich niedrigeres Rate-Limit - das ist der haeufigste Grund
fuer 429-Fehler. Ein einfacher Personal Access Token (ohne Scopes)
reicht fuer alle hier verwendeten oeffentlichen Endpunkte.

EXTRA_TEAM_IDS: Liste zusaetzlicher Team-Slugs.
SINCE_DAYS: Zeitraum in Tagen, ueber den Partien gezaehlt werden (7).
MAX_GAMES_PER_QUERY: Obergrenze Partien/Spieler (Deckel).
MAX_TEAM_TOURNAMENTS: Obergrenze vergangene Turniere pro Team/Typ.

CHECK_COOLDOWN_HOURS (Standard 18h): Wie lange ein bekannter Spieler
nicht erneut auf seine Partienzahl geprueft wird.
RECRAWL_COOLDOWN_HOURS (Standard 72h): Wartezeit vor erneutem Crawl
eines bereits gecrawlten Spielers.

CRAWL_SEED_COUNT / CRAWL_GAMES_PER_SEED: Snowball-Crawl-Parameter pro
Runde.
PHASE_SLICE_SECONDS (Standard 30s): Zeitscheibe je Crawl-/Turniere-Runde
in der Abwechslung.
MAX_TOTAL_RUNTIME_SECONDS (Standard 240s): Gesamt-Sicherheitsnetz fuer
die Crawl/Turniere-Abwechslung, damit ein Lauf nicht das GitHub-Actions-
Zeitlimit sprengt.

REQUEST_DELAY_SECONDS: zusaetzliche Pause an einzelnen Stellen (Ergaenzung
zum globalen Throttle, siehe unten).
GLOBAL_MIN_INTERVAL_SECONDS (Standard 3.0s): Mindestabstand zwischen
JEDER einzelnen HTTP-Anfrage an Lichess, unabhaengig davon, an welcher
Stelle im Code sie ausgeloest wird (Turniere, Team-Listen, Partien-
Streams, Crawl). Das ist der zentrale Hebel gegen 429-Fehler: frueher
gab es nur an einzelnen Stellen im Code verstreute time.sleep()-Aufrufe,
wodurch z.B. NDJSON-Streams (Turnier-Teilnehmer, Team-Mitglieder,
Partien-Abfragen) OHNE jede Pause dazwischen liefen. Jetzt greift der
Throttle direkt in der zentralen _request()-Funktion, kann also von
keiner Stelle im Code umgangen werden.

Sobald Lichess mit HTTP 429 antwortet, wartet das Skript automatisch
(Exponential-Backoff) und versucht es danach erneut. Nur bei sehr
langem durchgehendem 429 gibt das Skript fuer DIESEN Lauf auf und
speichert vorher alles bisher Ermittelte. RATE_LIMIT_MAX_TOTAL_WAIT_SECONDS
wurde bewusst deutlich gesenkt (von 1800s auf 180s), damit ein einzelner
haengender Request nicht das komplette Laufzeitbudget des ganzen Runs
auffressen kann - das wuerde sonst z.B. verhindern, dass der Crawl in
dieser Runde ueberhaupt noch drankommt.

Ausfuehren (einmaliger Durchlauf):
    python3 most_active_blitz_players.py
"""

import concurrent.futures
import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
TOKEN = os.environ.get("LICHESS_TOKEN", "")

CLOCK_BASED_PERF_TYPES = {"ultraBullet", "bullet", "blitz", "rapid", "classical"}
VARIANT_PERF_TYPES = {
    "chess960", "crazyhouse", "antichess", "atomic",
    "horde", "kingOfTheHill", "racingKings", "threeCheck",
}
ALLOWED_PERF_TYPES = CLOCK_BASED_PERF_TYPES | VARIANT_PERF_TYPES

PERF_TYPE = os.environ.get("PERF_TYPE", "blitz").strip()
if PERF_TYPE not in ALLOWED_PERF_TYPES:
    sys.exit(
        f"Ungueltiger PERF_TYPE '{PERF_TYPE}'. Erlaubt sind: "
        f"{', '.join(sorted(ALLOWED_PERF_TYPES))}"
    )


def classify_clock_seconds(total_estimated_seconds: float) -> str:
    if total_estimated_seconds < 29:
        return "ultraBullet"
    if total_estimated_seconds < 179:
        return "bullet"
    if total_estimated_seconds < 479:
        return "blitz"
    if total_estimated_seconds < 1499:
        return "rapid"
    return "classical"


def swiss_matches_perf_type(row: dict) -> bool:
    variant = row.get("variant", {})
    variant_key = variant.get("key") if isinstance(variant, dict) else None

    if PERF_TYPE in VARIANT_PERF_TYPES:
        return variant_key == PERF_TYPE

    if variant_key != "standard":
        return False
    clock = row.get("clock", {})
    limit = clock.get("limit", 0) if isinstance(clock, dict) else 0
    increment = clock.get("increment", 0) if isinstance(clock, dict) else 0
    total = limit + 40 * increment
    return classify_clock_seconds(total) == PERF_TYPE


EXTRA_TEAM_IDS = [
     "darkonblitz-dob",
     "darkonteams",
     "--elite-chess-players-union--"
]

SINCE_DAYS = 7
MAX_GAMES_PER_QUERY = 10000
TOP_N = 1000

# --- Rate-Limit-Schutz ----------------------------------------------------
# REQUEST_DELAY_SECONDS bleibt als zusaetzliche, lokale Pause an manchen
# Stellen erhalten (schadet nicht), der eigentliche Schutz ist jetzt aber
# GLOBAL_MIN_INTERVAL_SECONDS in _request(), siehe Docstring oben.
REQUEST_DELAY_SECONDS = float(os.environ.get("REQUEST_DELAY_SECONDS", "2.0"))
# GEAENDERT: Default von "0" auf "1.2" - vorher gab es de facto GAR KEINEN
# Mindestabstand zwischen Requests, wodurch besonders der parallele
# Top-1000-Refresh (REFRESH_WORKERS gleichzeitige Threads) Lichess quasi
# im Sturm angefragt hat -> sofortige 429-Kaskade. _throttle() serialisiert
# ALLE Requests (auch aus mehreren Threads) global auf diesen Mindestabstand,
# daher reicht ein einzelner sinnvoller Wert hier, egal wie viele Worker
# parallel laufen.
GLOBAL_MIN_INTERVAL_SECONDS = float(os.environ.get("GLOBAL_MIN_INTERVAL_SECONDS", "1.2"))

# Anzahl paralleler Worker-Threads fuer den (potenziell 1000 Spieler
# umfassenden) Partienzahl-Refresh der Top-1000. Da _throttle() ALLE
# Requests ohnehin global auf GLOBAL_MIN_INTERVAL_SECONDS serialisiert,
# bringt eine hohe Worker-Zahl kaum echten Speed-Vorteil mehr, erhoeht aber
# das Risiko, dass Lichess mehrere gleichzeitig offene Connections als
# Burst wertet. Daher deutlich gesenkt (20 -> 4).
REFRESH_WORKERS = int(os.environ.get("REFRESH_WORKERS", "4"))

MAX_TEAM_TOURNAMENTS = int(os.environ.get("MAX_TEAM_TOURNAMENTS", "200"))

# --- Cooldowns ----------------------------------------------------------
CHECK_COOLDOWN_HOURS = float(os.environ.get("CHECK_COOLDOWN_HOURS", "18"))
RECRAWL_COOLDOWN_HOURS = float(os.environ.get("RECRAWL_COOLDOWN_HOURS", "72"))

CHECK_COOLDOWN_SECONDS = CHECK_COOLDOWN_HOURS * 3600
RECRAWL_COOLDOWN_SECONDS = RECRAWL_COOLDOWN_HOURS * 3600

# --- Snowball-Crawl -------------------------------------------------------
# GEAENDERT: von 8 auf 24 - mehr Kettenglieder pro Runde bedeutet mehr neue
# Spieler pro Zeiteinheit. Macht durch die Parallelisierung (CRAWL_WORKERS,
# siehe unten) auch keine zusaetzlichen Rate-Limit-Probleme, da _throttle()
# weiterhin JEDEN einzelnen Request global auf GLOBAL_MIN_INTERVAL_SECONDS
# taktet - nur das eigentliche Warten auf die (teils langsame) NDJSON-
# Antwort ueberlappt jetzt zwischen mehreren Seeds.
CRAWL_SEED_COUNT = int(os.environ.get("CRAWL_SEED_COUNT", "24"))
CRAWL_GAMES_PER_SEED = int(os.environ.get("CRAWL_GAMES_PER_SEED", "10"))

# Anzahl paralleler Worker-Threads, die pro Crawl-Runde die Kettenglieder
# gleichzeitig abarbeiten (Gegner-Extraktion je Seed). _throttle() serialisiert
# weiterhin den ZEITPUNKT jeder einzelnen Anfrage global (kein Burst!), aber
# die eigentliche Wartezeit auf die Antwort (Netzwerk-Latenz, NDJSON-Stream
# lesen) ueberlappt zwischen den Threads - das macht den Crawl spuerbar
# schneller, ohne Lichess mit gleichzeitig gestarteten Requests zu bombardieren.
CRAWL_WORKERS = int(os.environ.get("CRAWL_WORKERS", "4"))

# Wenn beim Start eines Laufs weder Crawl-Queue noch je gecrawlte Spieler
# vorhanden sind, wird die Queue mit einer Zufallsstichprobe aus dem Pool
# "angeimpft", damit der Crawl garantiert sofort Seeds hat.
CRAWL_BOOTSTRAP_SAMPLE_SIZE = int(os.environ.get("CRAWL_BOOTSTRAP_SAMPLE_SIZE", "30"))

# Bei JEDER Crawl-Runde (nicht mehr nur beim Wieder-Einstieg in die Phase)
# werden DIVERSE_SEED_COUNT zufaellige Spieler aus bewusst unterschiedlichen
# Rating-Baendern (siehe DIVERSE_RATING_BANDS) vorne an die Crawl-Kette
# gehaengt. Grund: reines Gegner-Ketten-Verzweigen bleibt fast immer im
# gleichen Rating-Band haengen (Lichess matcht aehnliche Ratings gegeneinander)
# - ein 1200er fuehrt so praktisch nie zu einem 2600er. Die Injektion sorgt
# dafuer, dass der Crawl staendig zwischen ganz unterschiedlichen Rating-
# Niveaus hin- und herspringt (z.B. mal 1200er, dann 2000er, dann 2600er),
# statt sich in einem einzigen Band festzufahren.
DIVERSE_SEED_COUNT = int(os.environ.get("DIVERSE_SEED_COUNT", "6"))

# Rating-Baender, aus denen inject_diverse_crawl_seeds() zufaellig zieht
# (untere Grenze inklusive, obere Grenze exklusiv - None = kein Limit).
# Bewusst breit gestreut ueber das ganze Spektrum, damit wirklich sehr
# unterschiedliche Rating-Niveaus gemischt werden statt nur Nachbarbaender.
DIVERSE_RATING_BANDS = [
    (0, 1400),      # Klub-/Gelegenheitsspieler
    (1400, 1800),   # solide Vereinsstaerke
    (1800, 2200),   # starke Amateure
    (2200, 2600),   # Experten/Meister
    (2600, None),   # Titeltraeger/sehr stark
]

# --- Rating-Alternierung fuer den Crawl (gegen "haengt in einem Rating-Band
# fest") ---------------------------------------------------------------
# Lichess matcht Gegner mit aehnlichem Rating - eine reine Gegner-Kette
# bleibt darum fast immer in einem engen Rating-Band haengen (z.B. laenger
# nur 1200er in classical). Um da rauszukommen, wechselt der Crawl nach
# jeweils ALTERNATE_RATING_BATCH_SIZE verarbeiteten Kettengliedern die
# Richtung: fuer die naechsten ALTERNATE_RATING_BATCH_SIZE (oder weniger,
# falls die Queue das nicht hergibt) werden aus der Queue NUR Kandidaten
# mit HOEHEREM Rating als der Referenzwert bevorzugt, danach fuer die
# naechste Charge nur welche mit NIEDRIGEREM Rating usw.
# GEAENDERT: von 50 auf 15 - die Richtung wechselt jetzt gut 3x so oft,
# zusammen mit der jetzt jede-Runde-laufenden Diversitaets-Injektion oben
# ergibt das ein viel unruhigeres, breiter gestreutes Rating-Huepfen statt
# langer Straehnen im selben Band.
ALTERNATE_RATING_BATCH_SIZE = int(os.environ.get("ALTERNATE_RATING_BATCH_SIZE", "15"))

# --- Rating-Refresh fuer die Top-1000-Anzeige ----------------------------
RATING_REFRESH_COOLDOWN_HOURS = float(os.environ.get("RATING_REFRESH_COOLDOWN_HOURS", "18"))
RATING_REFRESH_COOLDOWN_SECONDS = RATING_REFRESH_COOLDOWN_HOURS * 3600

# --- Kompletter Top-1000-Refresh (Rating/Bann UND Partienzahl) -----------
# GEAENDERT: laeuft nicht mehr bei JEDEM Lauf, sondern nur noch hoechstens
# 1x pro TOP1000_REFRESH_COOLDOWN_HOURS (Standard 24h). Das ist der groesste
# einzelne Rate-Limit-Treiber, weil er pro Lauf bis zu ~1000 Partienzahl-
# Abfragen ausloest (parallel mit REFRESH_WORKERS Threads). Bei einem
# Cron-Takt von 6h wuerde er sonst 4x taeglich komplett durchlaufen.
TOP1000_REFRESH_COOLDOWN_HOURS = float(os.environ.get("TOP1000_REFRESH_COOLDOWN_HOURS", "24"))
TOP1000_REFRESH_COOLDOWN_SECONDS = TOP1000_REFRESH_COOLDOWN_HOURS * 3600

# Batch-Groesse fuer den Bulk-User-Endpunkt (POST /api/users), liefert pro
# Aufruf Bot-Flag, Bann-Status (tosViolation/disabled) UND Rating in einem
# Rutsch. Lichess erlaubt hier bis zu 300 IDs pro Aufruf.
PLAYER_INFO_BATCH_SIZE = 300

# --- Abwechselnde Zeitscheiben Crawl <-> Turniere -------------------------
# WICHTIG: Reihenfolge pro Runde ist jetzt CRAWL ZUERST, dann Turniere -
# siehe Docstring-Abschnitt weiter oben ("ABWECHSELNDE ZEITBUDGET-PHASEN").
# GEAENDERT: von 30s auf 1800s (30min) - dadurch bekommt jede Phase einen
# richtigen, zusammenhaengenden Block statt in winzigen 30s-Haeppchen
# hin- und herzuspringen. Bei 5h30m Gesamtbudget ergibt das ~5-6
# vollstaendige Lobby<->Turnier-Wechsel pro Lauf.
PHASE_SLICE_SECONDS = float(os.environ.get("PHASE_SLICE_SECONDS", "1800"))
# GEAENDERT: GitHub-Actions-Hosted-Runner kappen einen Job HART bei 6h
# (360min) - egal was in timeout-minutes im Workflow steht. Da der Cron
# ohnehin alle 6h neu triggert, nutzen wir dieses Fenster jetzt (fast)
# komplett aus, statt schon nach 4 Minuten abzubrechen. 19800s = 5h30m,
# laesst 30min Puffer fuer Checkout/Setup/den finalen Push am Ende, damit
# der Job nicht mitten im Push vom Runner gekillt wird.
# WICHTIG: falls dein Workflow ein kuerzeres "cron"-Intervall als 6h hat
# ODER ein kuerzeres timeout-minutes setzt, MAX_TOTAL_RUNTIME_SECONDS
# entsprechend anpassen (z.B. per Repo-Variable/Secret ueberschreiben),
# sonst wird der Job vom Runner abgewuergt statt sauber zu speichern.
MAX_TOTAL_RUNTIME_SECONDS = float(os.environ.get("MAX_TOTAL_RUNTIME_SECONDS", "19800"))

# --- Herkunfts-Label (wo ein Spieler zuerst gefunden wurde) --------------
SOURCE_LABELS = {
    "turnier": "Turnier",
    "lobby": "Lobby-Crawl",
}

# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent  # scripts/auto -> scripts -> Repo-Root

DATA_DIR = REPO_ROOT / "data" / PERF_TYPE
KNOWN_PLAYERS_FILE = DATA_DIR / "known_players.json"
LEADERBOARD_FILE = DATA_DIR / "leaderboard.json"
KNOWN_TOURNAMENTS_FILE = DATA_DIR / "known_tournaments.json"

CRAWL_QUEUE_FILE = DATA_DIR / "crawl_queue.json"
KNOWN_CRAWLED_FILE = DATA_DIR / "known_crawled.json"       # username -> ISO-Zeitstempel
SOURCE_MAP_FILE = DATA_DIR / "player_source.json"          # username -> Quelle (turnier/lobby)
BOT_STATUS_FILE = DATA_DIR / "player_info.json"              # veraltet, wird noch fuer Migration gelesen
PLAYER_INFO_FILE = DATA_DIR / "player_info.json"            # username -> {"bot", "banned", "rating", "checked_at"}
CRAWL_DIRECTION_FILE = DATA_DIR / "crawl_direction.json"     # Zustand der Rating-Alternierung im Crawl
TOP1000_REFRESH_STATE_FILE = DATA_DIR / "top1000_refresh_state.json"  # Zeitpunkt des letzten Top-1000-Refreshs

STATUS_DIR = REPO_ROOT / "status" / PERF_TYPE
TOP10_JSON_FILE = STATUS_DIR / "top1000.json"
TOP10_MD_FILE = STATUS_DIR / "top1000.md"
TOP_N_LIVE = 1000

# ---------------------------------------------------------------------------
# LIVE GIT PUSH
# ---------------------------------------------------------------------------
LIVE_GIT_PUSH = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
# GEAENDERT: Default jetzt 60s (jede Minute) auf Wunsch, damit das Ranking
# im Repo haeufiger aktualisiert wird. WICHTIG - Trade-off: das bedeutet
# ueber eine Laufzeit von bis zu MAX_TOTAL_RUNTIME_SECONDS (5h30m) potenziell
# ~300 Commits PRO Job und Lauf, mal 13 parallele Matrix-Jobs (perf_type)
# mal mehreren Laeufen/Tag - die Git-Historie waechst dadurch weiterhin
# spuerbar, nur langsamer als beim vorherigen 30s-Takt. Der fetch-depth: 1
# im Workflow verhindert zwar, dass DAS beim Checkout ein Problem wird
# (es wird nur der neueste Stand geholt, nicht die ganze Historie), aber
# die serverseitige Repo-Groesse auf GitHub waechst trotzdem konstant.
# Falls die Repo-Groesse zum Problem wird: periodisch (z.B. woechentlich)
# die Historie squashen/bereinigen (git filter-repo o.ae.) oder dieses
# Intervall wieder erhoehen.
GIT_PUSH_MIN_INTERVAL_SECONDS = float(os.environ.get("GIT_PUSH_MIN_INTERVAL_SECONDS", "300"))
_last_git_push_ts = 0.0
GIT_PUSH_MAX_RETRIES = 8
GIT_PUSH_RETRY_BASE_DELAY_SECONDS = 3

# GEAENDERT (Repo-Groesse): Default-Push-Intervall von 60s auf 300s (5min)
# erhoeht. Grund: bei 13 parallelen perf_type-Jobs x einem neuen Commit
# alle 60s ueber Stunden ist die Git-Historie so unbegrenzt gewachsen,
# bis GitHub das Repo wegen Ueberschreitung der Groessen-Quota komplett
# gesperrt hat ("Repository is above its size quota" /
# "pre-receive hook declined"). Der eigentliche Commit/Push-Mechanismus
# bleibt bewusst UNVERAENDERT (normaler Commit + pull --rebase + push,
# KEIN Force-Push): da jeder Job nur seinen eigenen data/<perf_type>-
# Unterordner im Working Tree aktuell haelt, waere ein Force-Push riskant
# - er koennte zwischenzeitliche Aenderungen anderer Jobs an IHREN
# Unterordnern mit einem veralteten lokalen Stand ueberschreiben.
# Die eigentliche Repo-Groesse wird stattdessen durch einen separaten,
# woechentlichen Squash-Job im Workflow unter Kontrolle gehalten (siehe
# .github/workflows/*.yml, Job "squash-history").


def ensure_on_branch() -> None:
    check = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if check.returncode == 0:
        return

    branch = os.environ.get("GITHUB_REF_NAME") or "main"
    print(f"  [GIT] Detached HEAD erkannt - wechsle explizit auf Branch '{branch}'...")
    subprocess.run(
        ["git", "checkout", "-B", branch, f"origin/{branch}"],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )


def git_commit_and_push(message: str) -> bool:
    if not LIVE_GIT_PUSH:
        return False
    try:
        ensure_on_branch()

        candidate_paths = [
            STATUS_DIR, KNOWN_PLAYERS_FILE, KNOWN_TOURNAMENTS_FILE,
            LEADERBOARD_FILE, CRAWL_QUEUE_FILE, KNOWN_CRAWLED_FILE,
            SOURCE_MAP_FILE, PLAYER_INFO_FILE, CRAWL_DIRECTION_FILE,
        ]
        existing_paths = [str(p) for p in candidate_paths if p.exists()]
        if not existing_paths:
            return False

        subprocess.run(
            ["git", "add", "-f", "--ignore-errors", *existing_paths],
            cwd=REPO_ROOT, check=True, capture_output=True, text=True,
        )
        diff_check = subprocess.run(
            ["git", "diff", "--staged", "--quiet"], cwd=REPO_ROOT
        )
        if diff_check.returncode == 0:
            return False

        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=REPO_ROOT, check=True, capture_output=True, text=True,
        )

        last_error = None
        for attempt in range(1, GIT_PUSH_MAX_RETRIES + 1):
            try:
                subprocess.run(
                    ["git", "pull", "--rebase"],
                    cwd=REPO_ROOT, check=True, capture_output=True, text=True,
                )
                subprocess.run(
                    ["git", "push"],
                    cwd=REPO_ROOT, check=True, capture_output=True, text=True,
                )
                print(f"  [GIT] Live-Push durchgefuehrt: {message}")
                return True
            except subprocess.CalledProcessError as exc:
                last_error = exc
                stderr = exc.stderr or ""
                if "quota" in stderr.lower():
                    # Kein transientes Kollisions-Problem, sondern das Repo
                    # ist ueber die Groessen-Quota - Retries helfen hier
                    # nicht. Sofort abbrechen statt 8x sinnlos zu warten.
                    print(f"  [FEHLER] Repo ist ueber der Groessen-Quota - "
                          f"Push wird abgebrochen, bitte Historie bereinigen "
                          f"(siehe README/Runbook): {stderr.strip()}")
                    return False
                if attempt < GIT_PUSH_MAX_RETRIES:
                    delay = GIT_PUSH_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                    delay += random.uniform(0, delay * 0.5)
                    delay = min(delay, 60)
                    print(f"  [GIT] Push kollidiert (Versuch {attempt}/"
                          f"{GIT_PUSH_MAX_RETRIES}), warte {delay:.1f}s und "
                          f"versuche erneut...")
                    time.sleep(delay)

        stderr = last_error.stderr if last_error and hasattr(last_error, "stderr") else str(last_error)
        print(f"  [WARNUNG] Git-Push nach {GIT_PUSH_MAX_RETRIES} Versuchen "
              f"weiterhin fehlgeschlagen: {stderr}")
        return False
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr if hasattr(exc, "stderr") else str(exc)
        print(f"  [WARNUNG] Git-Commit/Push fehlgeschlagen: {stderr}")
        return False


def maybe_live_push(force: bool = False) -> None:
    global _last_git_push_ts
    now = time.time()
    if not force and (now - _last_git_push_ts) < GIT_PUSH_MIN_INTERVAL_SECONDS:
        return
    _last_git_push_ts = now
    git_commit_and_push(
        f"Live-Update Blitz-Leaderboard {datetime.now(timezone.utc).isoformat()} [skip ci]"
    )

BASE_URL = "https://lichess.org"
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
NDJSON_HEADERS = {**HEADERS, "Accept": "application/x-ndjson"}


class RateLimitError(Exception):
    """Wird ausgeloest, wenn Lichess mit HTTP 429 antwortet."""


# ---------------------------------------------------------------------------
# ZEIT-HELFER (fuer die Cooldown-Logik)
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def seconds_since(iso_ts: str) -> float:
    if not iso_ts:
        return float("inf")
    try:
        then = datetime.fromisoformat(iso_ts)
    except ValueError:
        return float("inf")
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds()


def is_due(iso_ts: str, cooldown_seconds: float) -> bool:
    return seconds_since(iso_ts) >= cooldown_seconds


def format_duration(total_seconds: float) -> str:
    total_seconds = int(total_seconds)
    m, s = divmod(total_seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ---------------------------------------------------------------------------
# HTTP HELPERS
# ---------------------------------------------------------------------------
RATE_LIMIT_INITIAL_BACKOFF_SECONDS = 20
RATE_LIMIT_MAX_BACKOFF_SECONDS = 60
# Gesenkt von 1800s auf 180s: ein einzelner haengender Request darf nicht
# mehr das gesamte Laufzeitbudget (Standard 240s) auffressen. Wird dieses
# Limit erreicht, gibt das Skript fuer DIESEN Lauf auf (RateLimitError) und
# speichert vorher alles - der naechste Cron-Lauf versucht es erneut.
RATE_LIMIT_MAX_TOTAL_WAIT_SECONDS = float(os.environ.get("RATE_LIMIT_MAX_TOTAL_WAIT_SECONDS", "180"))

_last_request_ts = 0.0
_throttle_lock = threading.Lock()

# --- Zusaetzlicher, STRENGERER Throttle nur fuer den Games-Export-Endpunkt
# (/api/games/user/...) ------------------------------------------------
# Dieser Endpunkt wird von Lichess deutlich strenger limitiert als leichte
# Endpunkte (Turnierlisten, Team-Roster etc.), weil er serverseitig ganze
# Partienverlaeufe streamt. Sowohl der Snowball-Crawl (get_recent_opponents)
# als auch der Partienzahl-Refresh (count_recent_blitz_games) nutzen GENAU
# diesen Endpunkt - und beide laufen inzwischen parallel in mehreren
# Worker-Threads (CRAWL_WORKERS/REFRESH_WORKERS). Der allgemeine
# GLOBAL_MIN_INTERVAL_SECONDS-Throttle allein reicht dafuer nicht mehr aus.
# Dieser zweite Throttle wirkt ZUSAETZLICH zum globalen (nicht statt ihm)
# und betrifft NUR Requests an diesen einen Endpunkt.
GAMES_EXPORT_MIN_INTERVAL_SECONDS = float(os.environ.get("GAMES_EXPORT_MIN_INTERVAL_SECONDS", "3.0"))
_last_games_export_ts = 0.0
_games_export_throttle_lock = threading.Lock()


def _throttle() -> None:
    """Globaler Mindestabstand zwischen JEDER Anfrage an Lichess - egal von
    wo im Code sie kommt (Turniere, Teams, Partien-Streams, Crawl). Das ist
    der zentrale Fix gegen 429: frueher gab es nur verstreute time.sleep()
    Aufrufe an einzelnen Stellen, wodurch z.B. NDJSON-Streams komplett ohne
    Pause liefen. Threadsicher (Lock), da der Top-1000-Partienzahl-Refresh
    parallel in mehreren Worker-Threads laeuft."""
    global _last_request_ts
    with _throttle_lock:
        now = time.time()
        wait = GLOBAL_MIN_INTERVAL_SECONDS - (now - _last_request_ts)
        if wait > 0:
            time.sleep(wait)
        _last_request_ts = time.time()


def _throttle_games_export() -> None:
    """Zusaetzlicher, strengerer Mindestabstand NUR fuer den schweren
    Games-Export-Endpunkt (siehe Kommentar oben). Wird VOR _throttle()
    aufgerufen, wirkt also on top des allgemeinen Mindestabstands."""
    global _last_games_export_ts
    with _games_export_throttle_lock:
        now = time.time()
        wait = GAMES_EXPORT_MIN_INTERVAL_SECONDS - (now - _last_games_export_ts)
        if wait > 0:
            time.sleep(wait)
        _last_games_export_ts = time.time()


def _request(url: str, headers: dict, timeout: int = 30):
    backoff = RATE_LIMIT_INITIAL_BACKOFF_SECONDS
    total_waited = 0.0
    while True:
        _throttle()
        req = urllib.request.Request(url, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            remaining = resp.headers.get("X-RateLimit-Remaining") if hasattr(resp, "headers") else None
            if remaining is not None:
                try:
                    if int(remaining) <= 1:
                        print("  [RATE LIMIT] Kontingent laut Header fast aufgebraucht - "
                              "warte vorsorglich 15s...")
                        time.sleep(15)
                except ValueError:
                    pass
            return resp
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise

            retry_after_header = exc.headers.get("Retry-After") if exc.headers else None
            try:
                wait_s = float(retry_after_header)
            except (TypeError, ValueError):
                wait_s = backoff
            wait_s = max(wait_s, 1.0)

            if total_waited + wait_s > RATE_LIMIT_MAX_TOTAL_WAIT_SECONDS:
                raise RateLimitError(
                    f"Rate Limit bei {url} - trotz {total_waited:.0f}s Warten "
                    f"weiterhin 429, gebe fuer diesen Lauf auf"
                ) from exc

            print(f"  [RATE LIMIT] 429 bei {url} - warte {wait_s:.0f}s und "
                  f"versuche es dann automatisch erneut (insgesamt schon "
                  f"{total_waited:.0f}s gewartet)...")
            time.sleep(wait_s)
            total_waited += wait_s
            backoff = min(backoff * 2, RATE_LIMIT_MAX_BACKOFF_SECONDS)


def fetch_json(url: str) -> dict:
    with _request(url, HEADERS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_ndjson(url: str, headers: dict = None):
    with _request(url, headers or NDJSON_HEADERS, timeout=60) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            yield json.loads(line)


# ---------------------------------------------------------------------------
# PERSISTENZ
# ---------------------------------------------------------------------------
def load_json_set(path: Path) -> set:
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, list):
                return set(data)
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def save_json_set(path: Path, values: set) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(values), indent=2))


def load_json_list(path: Path) -> list:
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_json_list(path: Path, values: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, indent=2))


def load_json_dict(path: Path) -> dict:
    """Fuer username/team_id -> Zeitstempel-oder-Label Mappings. Migriert
    transparent alte Dateien, die noch eine reine Liste waren (Vorgaenger-
    Version von known_crawled.json): migrierte Eintraege bekommen 'jetzt'
    als Zeitstempel, damit nicht sofort ein Recrawl-Sturm losgeht."""
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                stamp = now_iso()
                return {name: stamp for name in data}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_json_dict(path: Path, values: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, indent=2, sort_keys=True))


def load_leaderboard() -> dict:
    if LEADERBOARD_FILE.exists():
        try:
            data = json.loads(LEADERBOARD_FILE.read_text())
            if isinstance(data, dict):
                data.setdefault("counts", {})
                data.setdefault("last_checked", {})
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"updated_at": None, "counts": {}, "last_checked": {}}


def save_leaderboard(leaderboard: dict, source_map: dict, player_info: dict = None) -> None:
    player_info = player_info or {}
    counts = leaderboard.get("counts", {})
    ranking = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    leaderboard["ranking"] = [
        {
            "rank": i, "username": name, "games": cnt,
            "rating": player_info.get(name, {}).get("rating"),
            "source": SOURCE_LABELS.get(source_map.get(name), "?"),
            "profile": profile_url(name),
        }
        for i, (name, cnt) in enumerate(ranking, start=1)
    ]
    LEADERBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    LEADERBOARD_FILE.write_text(json.dumps(leaderboard, indent=2, sort_keys=True, ensure_ascii=False))


def tag_source(source_map: dict, usernames, label: str) -> None:
    """Merkt sich, WOHER ein Spieler zuerst gefunden wurde. Ein Spieler,
    der schon eine Quelle hat, wird nicht ueberschrieben - es zaehlt der
    allererste Fund."""
    for name in usernames:
        if name not in source_map:
            source_map[name] = label


# ---------------------------------------------------------------------------
# TURNIERE FINDEN (aktuell sichtbar + Team-Historie, NUR PERF_TYPE)
# ---------------------------------------------------------------------------
def get_visible_blitz_tournament_ids() -> list:
    print(f"  -> Suche aktuell sichtbare {PERF_TYPE}-Arenen...")
    try:
        data = fetch_json(f"{BASE_URL}/api/tournament")
    except (RateLimitError, urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"     [WARNUNG] Turnierliste konnte nicht geladen werden: {exc}")
        return []

    ids = []
    for bucket in ("finished", "started", "created"):
        for t in data.get(bucket, []):
            perf = t.get("perf", {})
            perf_key = perf.get("key") if isinstance(perf, dict) else None
            if perf_key == PERF_TYPE and t.get("id"):
                ids.append((t["id"], "arena"))

    print(f"     {len(ids)} {PERF_TYPE}-Arena(n) sichtbar.")
    return ids


def get_team_tournament_ids(team_id: str) -> list:
    found = []

    url = f"{BASE_URL}/api/team/{team_id}/arena?max={MAX_TEAM_TOURNAMENTS}"
    try:
        for row in fetch_ndjson(url):
            perf = row.get("perf", {})
            perf_key = perf.get("key") if isinstance(perf, dict) else None
            if perf_key == PERF_TYPE and row.get("id"):
                found.append((row["id"], "arena"))
    except RateLimitError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"     [WARNUNG] Arena-Historie von Team '{team_id}' nicht ladbar: {exc}")

    url = f"{BASE_URL}/api/team/{team_id}/swiss?max={MAX_TEAM_TOURNAMENTS}"
    try:
        for row in fetch_ndjson(url):
            if swiss_matches_perf_type(row) and row.get("id"):
                found.append((row["id"], "swiss"))
    except RateLimitError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"     [WARNUNG] Swiss-Historie von Team '{team_id}' nicht ladbar: {exc}")

    return found


def get_tournament_participants(tournament_id: str, kind: str) -> set:
    if kind == "swiss":
        url = f"{BASE_URL}/api/swiss/{tournament_id}/results"
    else:
        url = f"{BASE_URL}/api/tournament/{tournament_id}/results?sheet=false"

    users = set()
    try:
        for row in fetch_ndjson(url):
            name = row.get("username")
            if name:
                users.add(name.lower())
    except RateLimitError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"     [WARNUNG] Teilnehmer von Turnier {tournament_id} nicht ladbar: {exc}")
    return users


# ---------------------------------------------------------------------------
# BOT- & BANN-FILTER + RATING-LOOKUP (kombiniert)
# ---------------------------------------------------------------------------
# Lichess markiert Bot-Accounts im Profil mit title == "BOT" und gebannte/
# geschlossene Accounts mit tosViolation == true bzw. disabled == true.
# Team-Roster, Turnier-Teilnehmerlisten und Lobby-Crawl-Gegner koennen
# beides enthalten. Diese Funktion filtert Bots UND gebannte/geschlossene
# Accounts konsequent an JEDER Stelle raus, an der neue Spieler in den
# Pool aufgenommen werden - gebannte Spieler sollen weder in der
# Rangliste stehen noch weiterhin (erneut) eingefuegt werden koennen.
#
# Genutzt wird dafuer der Bulk-Endpunkt POST /api/users (bis zu 300 IDs
# pro Aufruf), der zusaetzlich zum Bot-Flag auch tosViolation/disabled
# UND das aktuelle Perf-Rating (fuer PERF_TYPE) mitliefert - so wird die
# Top-1000-Anzeige mit Rating "kostenlos" bei diesem ohnehin noetigen
# Aufruf mitbefuellt.
#
# Ergebnis wird dauerhaft in PLAYER_INFO_FILE gecacht:
#   username -> {"bot": bool, "banned": bool, "rating": int|None,
#                "checked_at": ISO-Zeitstempel}
def fetch_player_info_bulk(usernames: list) -> dict:
    """Fragt eine Liste Usernamen ueber POST /api/users ab (Batches von
    PLAYER_INFO_BATCH_SIZE) und gibt username -> Rohdaten-Dict zurueck."""
    result = {}
    for i in range(0, len(usernames), PLAYER_INFO_BATCH_SIZE):
        batch = usernames[i:i + PLAYER_INFO_BATCH_SIZE]
        # WICHTIG: Lichess erwartet hier KOMMA-getrennte IDs im Body,
        # NICHT zeilengetrennt - mit Zeilenumbruch hat der Endpunkt die
        # IDs nicht korrekt erkannt, wodurch Rating/Bann-Status nie
        # aktualisiert wurden (Rating blieb "?", Bann wurde nie erkannt).
        body = ",".join(batch).encode("utf-8")
        req = urllib.request.Request(
            f"{BASE_URL}/api/users", data=body, method="POST",
            headers={**HEADERS, "Content-Type": "text/plain"},
        )
        _throttle()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise RateLimitError(f"Rate Limit bei POST /api/users") from exc
            print(f"  [WARNUNG] Bulk-User-Info fehlgeschlagen fuer Batch "
                  f"({len(batch)} Spieler): {exc}")
            continue
        except (urllib.error.URLError, OSError) as exc:
            print(f"  [WARNUNG] Bulk-User-Info fehlgeschlagen fuer Batch "
                  f"({len(batch)} Spieler): {exc}")
            continue

        if isinstance(data, list):
            for entry in data:
                uid = entry.get("id")
                if uid:
                    result[uid] = entry
    return result


def update_player_info_cache(usernames, player_info: dict) -> None:
    """Fragt alle noch unbekannten (oder faelligen) Usernamen per Bulk-
    Endpunkt ab und aktualisiert player_info dauerhaft."""
    usernames = list(dict.fromkeys(usernames))
    to_fetch = sorted(usernames)
    if not to_fetch:
        return

    raw = fetch_player_info_bulk(to_fetch)
    now = now_iso()
    for uid in to_fetch:
        entry = raw.get(uid)
        if entry is None:
            # Kein Eintrag zurueckgegeben (z.B. geloeschter Account) -
            # vorsichtshalber als "kein Bot, nicht gebannt" werten, damit
            # er nicht dauerhaft haengen bleibt, aber ohne Rating.
            player_info[uid] = {
                "bot": False, "banned": False, "rating": None,
                "checked_at": now,
            }
            continue

        is_bot = entry.get("title") == "BOT"
        is_banned = bool(entry.get("tosViolation")) or bool(entry.get("disabled"))
        perfs = entry.get("perfs", {})
        perf = perfs.get(PERF_TYPE, {}) if isinstance(perfs, dict) else {}
        rating = perf.get("rating") if isinstance(perf, dict) else None

        player_info[uid] = {
            "bot": is_bot, "banned": is_banned, "rating": rating,
            "checked_at": now,
        }

    save_json_dict(PLAYER_INFO_FILE, player_info)


def check_and_filter_players(usernames, player_info: dict) -> set:
    """
    Nimmt eine Menge Usernamen, aktualisiert player_info (Cache) fuer alle
    noch unbekannten Namen via Bulk-Endpunkt, und gibt die Teilmenge OHNE
    Bots und OHNE gebannte/geschlossene Accounts zurueck.
    """
    usernames = set(usernames)
    unknown = [u for u in usernames if u not in player_info]
    if unknown:
        update_player_info_cache(unknown, player_info)

    bots_found = {u for u in usernames if player_info.get(u, {}).get("bot")}
    banned_found = {u for u in usernames if player_info.get(u, {}).get("banned")}

    if bots_found:
        print(f"  [BOT-FILTER] {len(bots_found)} Bot(s) ausgefiltert: "
              f"{sorted(bots_found)[:8]}{'...' if len(bots_found) > 8 else ''}")
    if banned_found:
        print(f"  [BANN-FILTER] {len(banned_found)} gebannte/geschlossene "
              f"Account(s) ausgefiltert: {sorted(banned_found)[:8]}"
              f"{'...' if len(banned_found) > 8 else ''}")

    return {
        u for u in usernames
        if not player_info.get(u, {}).get("bot")
        and not player_info.get(u, {}).get("banned")
    }


def purge_banned_players(pool: set, counts: dict, last_checked: dict,
                          source_map: dict, player_info: dict) -> set:
    """Entfernt bereits bekannte, aber (neu) als gebannt/geschlossen
    erkannte Spieler dauerhaft aus Pool, Leaderboard und Quellen-Map,
    damit sie garantiert nicht mehr in der Rangliste auftauchen."""
    banned = {u for u in pool if player_info.get(u, {}).get("banned")}
    if not banned:
        return banned

    for u in banned:
        pool.discard(u)
        counts.pop(u, None)
        last_checked.pop(u, None)
        source_map.pop(u, None)

    print(f"  [BANN-FILTER] {len(banned)} bereits bekannte Spieler wurden "
          f"als gebannt/geschlossen erkannt und dauerhaft entfernt: "
          f"{sorted(banned)[:8]}{'...' if len(banned) > 8 else ''}")
    return banned


# ---------------------------------------------------------------------------
# SNOWBALL-CRAWL: Gegner aus den letzten Partien eines Spielers extrahieren
# ---------------------------------------------------------------------------
def get_recent_opponents(username: str, limit: int) -> set:
    params = urllib.parse.urlencode({
        "max": limit,
        "perfType": PERF_TYPE,
        "moves": "false",
        "tags": "false",
        "opening": "false",
        "clocks": "false",
        "evals": "false",
    })
    url = f"{BASE_URL}/api/games/user/{username}?{params}"

    opponents = set()
    try:
        _throttle_games_export()
        for game in fetch_ndjson(url):
            players = game.get("players", {})
            for color in ("white", "black"):
                side = players.get(color, {})
                user = side.get("user", {}) if isinstance(side, dict) else {}
                opp_name = user.get("name") or user.get("id")
                if opp_name and opp_name.lower() != username.lower():
                    opponents.add(opp_name.lower())
    except RateLimitError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        print(f"     [WARNUNG] Partien-Gegner von '{username}' nicht ladbar: {exc}")
    return opponents


# ---------------------------------------------------------------------------
# >>> FIX (siehe Docstring "SNOWBALL-CRAWL"): pick_crawl_seeds() und
# run_snowball_crawl_round() wurden angepasst, damit die Gegner-Kette
# wirklich endlos weiterlaeuft, statt sich totzulaufen bzw. sich zu
# wiederholen. Vorher wurden bereits gecrawlte Spieler beim Wiederaufbau
# der Queue erneut mitgeschleppt statt entfernt zu werden -> die Queue
# wuchs mit totem Ballast und "wirkte" leer, obwohl neue Kettenglieder
# eigentlich noch da waren; ausserdem sprang der Fallback bei "leerer"
# Queue sofort auf zufaellige Pool-Spieler statt strikt der Kette zu
# folgen, wodurch der Crawl staendig neu ansetzte statt sich zu
# verzweigen.
# ---------------------------------------------------------------------------
def load_crawl_direction_state() -> dict:
    state = load_json_dict(CRAWL_DIRECTION_FILE)
    if not state:
        state = {"direction": "up", "processed_in_batch": 0, "reference_rating": None}
    return state


def save_crawl_direction_state(state: dict) -> None:
    save_json_dict(CRAWL_DIRECTION_FILE, state)


def rating_matches_direction(name: str, direction_state: dict, player_info: dict) -> bool:
    """Prueft, ob der Rating eines Kandidaten zur aktuellen Crawl-Richtung
    passt. Unbekanntes Rating (noch nie gecheckt) gilt als passend, damit
    die Kette dadurch nicht blockiert wird."""
    rating = player_info.get(name, {}).get("rating")
    reference = direction_state.get("reference_rating")
    if rating is None or reference is None:
        return True
    if direction_state.get("direction") == "up":
        return rating > reference
    return rating < reference


def advance_crawl_direction(processed_names, direction_state: dict, player_info: dict) -> None:
    """Zaehlt verarbeitete Kettenglieder mit; nach ALTERNATE_RATING_BATCH_SIZE
    Stueck wird die Richtung (hoeher/niedriger) gewechselt und der
    Referenz-Rating-Wert aktualisiert (letztes bekanntes Rating aus dieser
    Charge)."""
    last_known_rating = None
    for name in processed_names:
        rating = player_info.get(name, {}).get("rating")
        if rating is not None:
            last_known_rating = rating
        direction_state["processed_in_batch"] = direction_state.get("processed_in_batch", 0) + 1

    if last_known_rating is not None:
        direction_state["reference_rating"] = last_known_rating

    if direction_state.get("processed_in_batch", 0) >= ALTERNATE_RATING_BATCH_SIZE:
        direction_state["direction"] = "down" if direction_state.get("direction") == "up" else "up"
        direction_state["processed_in_batch"] = 0
        print(f"  [RATING-ALTERNIERUNG] Charge voll - Crawl-Richtung wechselt "
              f"jetzt auf '{direction_state['direction']}' "
              f"(Referenz-Rating: {direction_state.get('reference_rating')}).")

    save_crawl_direction_state(direction_state)


def pick_crawl_seeds(queue: list, known_crawled: dict, pool: set,
                      player_info: dict = None, direction_state: dict = None) -> tuple:
    """
    Waehlt bis zu CRAWL_SEED_COUNT Spieler als naechste Kettenglieder.

    Prioritaet:
      1. STRIKT FIFO aus der Queue - das ist die eigentliche Kette.
         Bereits gecrawlte Eintraege werden dabei komplett entfernt
         (NICHT zurueckgelegt!), damit die Queue nicht mit totem
         Ballast waechst und ehrlich anzeigt, ob noch Kettenglieder
         offen sind.
      2. Nur falls die (bereinigte) Queue komplett leer ist: zufaellige,
         nie gecrawlte Spieler aus dem restlichen Pool - damit die Kette
         nicht abbricht, wenn sie sich mal "totgelaufen" hat.
      3. Nur falls auch das nicht reicht: Spieler mit abgelaufenem
         Recrawl-Cooldown erneut als Seed nehmen.
    """
    # Queue bereinigen: bereits gecrawlte Eintraege komplett rauswerfen,
    # NICHT wieder anhaengen -> die Queue waechst nicht mehr mit totem
    # Ballast, und "Queue leer" bedeutet wieder wirklich "leer".
    clean_queue = [c for c in queue if c not in known_crawled]

    player_info = player_info or {}
    direction_state = direction_state or {"direction": "up", "reference_rating": None}

    # Innerhalb eines Fensters am Queue-Anfang werden Kandidaten bevorzugt,
    # deren Rating zur aktuellen Richtung passt (siehe ALTERNATE_RATING_
    # BATCH_SIZE weiter oben) - das haelt die Kette grundsaetzlich FIFO,
    # sucht innerhalb des Fensters aber gezielt nach hoeheren/niedrigeren
    # Ratings, damit der Crawl nicht ewig im gleichen Rating-Band haengt.
    window_size = max(CRAWL_SEED_COUNT * 20, 200)
    window = clean_queue[:window_size]
    rest_after_window = clean_queue[window_size:]

    matching = [c for c in window if rating_matches_direction(c, direction_state, player_info)]
    matching_set = set(matching)
    non_matching = [c for c in window if c not in matching_set]

    seeds = matching[:CRAWL_SEED_COUNT]
    if len(seeds) < CRAWL_SEED_COUNT:
        seeds += non_matching[:CRAWL_SEED_COUNT - len(seeds)]

    chosen_set = set(seeds)
    remaining_window = [c for c in window if c not in chosen_set]
    remaining_queue = remaining_window + rest_after_window

    if len(seeds) < CRAWL_SEED_COUNT:
        fresh_candidates = [p for p in pool if p not in known_crawled and p not in seeds]
        random.shuffle(fresh_candidates)
        for candidate in fresh_candidates:
            if len(seeds) >= CRAWL_SEED_COUNT:
                break
            seeds.append(candidate)

    if len(seeds) < CRAWL_SEED_COUNT:
        stale_candidates = [
            p for p in pool
            if p not in seeds and p in known_crawled
            and is_due(known_crawled[p], RECRAWL_COOLDOWN_SECONDS)
        ]
        random.shuffle(stale_candidates)
        for candidate in stale_candidates:
            if len(seeds) >= CRAWL_SEED_COUNT:
                break
            seeds.append(candidate)

    return seeds, remaining_queue


def bootstrap_crawl_queue_if_empty(pool: set) -> None:
    """Wenn weder Crawl-Queue noch je gecrawlte Spieler existieren (z.B.
    allererster Lauf, oder der Crawl kam aus irgendeinem Grund noch nie
    dran), wird die Queue mit einer Zufallsstichprobe aus dem bestehenden
    Pool angeimpft, damit run_snowball_crawl_round garantiert sofort
    Seeds hat und nicht leerlaeuft."""
    queue = load_json_list(CRAWL_QUEUE_FILE)
    known_crawled = load_json_dict(KNOWN_CRAWLED_FILE)
    if queue or known_crawled or not pool:
        return
    sample = list(pool)
    random.shuffle(sample)
    sample = sample[:CRAWL_BOOTSTRAP_SAMPLE_SIZE]
    print(f"  [CRAWL-BOOTSTRAP] Queue und gecrawlte Spieler waren leer - "
          f"impfe Crawl-Queue mit {len(sample)} zufaelligen Spielern aus dem Pool an.")
    save_json_list(CRAWL_QUEUE_FILE, sample)


def inject_diverse_crawl_seeds(source_map: dict, player_info: dict, pool: set,
                                count: int = DIVERSE_SEED_COUNT) -> None:
    """
    Haengt bis zu `count` zufaellige Spieler VORNE (nicht hinten!) an die
    Crawl-Queue, damit sie als naechstes dran sind - jeweils EINER pro
    Rating-Band aus DIVERSE_RATING_BANDS (soweit vorhanden), damit
    tatsaechlich weit auseinanderliegende Rating-Niveaus gemischt werden
    (z.B. mal 1200er, dann 2000er, dann 2600er) statt Nachbarbaender.

    GEAENDERT: zieht jetzt aus dem GESAMTEN bekannten Pool (turnier UND
    lobby), nicht mehr nur aus Turnier-Teilnehmern - vorher gab es bei
    reinen Lobby-Poolstaenden (z.B. nachdem alle Turniere abgearbeitet
    sind) irgendwann keine frischen Turnier-Kandidaten mehr, wodurch die
    Diversitaets-Injektion leerlief. Rating kommt aus player_info (wird
    beim Bot/Bann-Filter ohnehin mitbefuellt); Spieler ganz ohne bekanntes
    Rating werden als "Rest-Topf" benutzt, falls ein Band leer ist.
    """
    known_crawled = load_json_dict(KNOWN_CRAWLED_FILE)
    queue = load_json_list(CRAWL_QUEUE_FILE)
    queue_set = set(queue)

    def eligible(name: str) -> bool:
        return name not in known_crawled and name not in queue_set

    candidates = [u for u in pool if eligible(u)]
    if not candidates:
        return

    by_band = {i: [] for i in range(len(DIVERSE_RATING_BANDS))}
    unrated = []
    for u in candidates:
        rating = player_info.get(u, {}).get("rating")
        if rating is None:
            unrated.append(u)
            continue
        for i, (lo, hi) in enumerate(DIVERSE_RATING_BANDS):
            if rating >= lo and (hi is None or rating < hi):
                by_band[i].append(u)
                break

    for bucket in by_band.values():
        random.shuffle(bucket)
    random.shuffle(unrated)

    # Ein Kandidat pro Band im Rundlauf, damit die Injektion tatsaechlich
    # ueber verschiedene Rating-Niveaus streut statt zufaellig mehrfach
    # aus demselben (ggf. groessten) Band zu ziehen.
    picks = []
    band_order = list(range(len(DIVERSE_RATING_BANDS)))
    random.shuffle(band_order)
    while len(picks) < count and (any(by_band[i] for i in band_order) or unrated):
        progressed = False
        for i in band_order:
            if len(picks) >= count:
                break
            if by_band[i]:
                picks.append(by_band[i].pop())
                progressed = True
        if len(picks) < count and unrated:
            picks.append(unrated.pop())
            progressed = True
        if not progressed:
            break

    if not picks:
        return

    print(f"  [DIVERSITAET] {len(picks)} Spieler aus unterschiedlichen "
          f"Rating-Baendern werden vorne an die Kette gehaengt: {picks}")
    new_queue = picks + [q for q in queue if q not in picks]
    save_json_list(CRAWL_QUEUE_FILE, new_queue)


def run_snowball_crawl_round(pool: set, updated_this_run: set, counts: dict, last_checked: dict,
                              source_map: dict, since_ms: int, leaderboard: dict, stats: dict,
                              player_info: dict) -> tuple:
    """
    Fuehrt EINE Crawl-Runde aus (bis zu CRAWL_SEED_COUNT Kettenglieder).
    Nimmt das/die naechste(n) Kettenglied(er) strikt FIFO aus der Queue,
    schaut sich deren letzte CRAWL_GAMES_PER_SEED Partien an und haengt
    JEDEN neuen, noch nie gesehenen Gegner sofort hinten an die Queue an -
    das ist der eigentliche Verzweigungsschritt der endlosen Kette.

    GEAENDERT (Speed + Rating-Streuung):
      - inject_diverse_crawl_seeds() laeuft jetzt JEDE Runde (nicht mehr
        nur beim Wieder-Einstieg in die Phase), damit staendig Spieler aus
        weit auseinanderliegenden Rating-Baendern eingemischt werden.
      - Die Gegner-Extraktion der Seeds laeuft parallel in CRAWL_WORKERS
        Threads statt strikt nacheinander - _throttle() verhindert weiterhin
        gleichzeitig GESTARTETE Requests, aber die (oft langsame) Wartezeit
        auf NDJSON-Antworten ueberlappt jetzt zwischen den Seeds.

    Gibt (neue_spieler, anzahl_seeds_verarbeitet) zurueck -
    seeds_processed==0 bedeutet "keine Seeds mehr verfuegbar", das Signal
    fuer den Aufrufer, die Crawl-Phase als abgeschlossen zu markieren.
    """
    inject_diverse_crawl_seeds(source_map, player_info, pool)

    queue = load_json_list(CRAWL_QUEUE_FILE)
    known_crawled = load_json_dict(KNOWN_CRAWLED_FILE)
    direction_state = load_crawl_direction_state()

    seeds, remaining_queue = pick_crawl_seeds(queue, known_crawled, pool, player_info, direction_state)
    if not seeds:
        return set(), 0

    never_crawled_seeds = sum(1 for s in seeds if s not in known_crawled)
    recrawl_seeds = len(seeds) - never_crawled_seeds
    print(f"  {len(seeds)} Kettenglied(er) ({never_crawled_seeds} neu, {recrawl_seeds} Recrawl), "
          f"je die letzten {CRAWL_GAMES_PER_SEED} Partien -> Gegner extrahieren "
          f"(parallel, {CRAWL_WORKERS} Worker)...")

    # Netzwerk-Teil (get_recent_opponents) parallelisiert - liefert nur die
    # rohen Gegner-Namen je Seed zurueck. Alles, was gemeinsamen Zustand
    # (pool/queue/known_crawled/source_map) veraendert, passiert danach
    # bewusst sequenziell in fester Seed-Reihenfolge, damit es threadsicher
    # bleibt und die Ausgabe/Bookkeeping deterministisch ist.
    raw_opponents_by_seed = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=CRAWL_WORKERS) as executor:
        future_to_seed = {
            executor.submit(get_recent_opponents, seed, CRAWL_GAMES_PER_SEED): seed
            for seed in seeds
        }
        for future in concurrent.futures.as_completed(future_to_seed):
            seed = future_to_seed[future]
            try:
                raw_opponents_by_seed[seed] = future.result()
            except RateLimitError:
                raise
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
                print(f"     [WARNUNG] Partien-Gegner von '{seed}' nicht ladbar: {exc}")
                raw_opponents_by_seed[seed] = set()

    all_new_opponents = set()
    for seed in seeds:
        raw_opponents = raw_opponents_by_seed.get(seed, set())
        opponents = check_and_filter_players(raw_opponents, player_info)
        new_opponents = opponents - pool

        if new_opponents:
            print(f"    '{seed}': {len(opponents)} Gegner ({len(new_opponents)} neu -> "
                  f"Kette waechst um: {sorted(new_opponents)[:5]}"
                  f"{'...' if len(new_opponents) > 5 else ''}).")
        else:
            print(f"    '{seed}': {len(opponents)} Gegner, alle bereits bekannt "
                  f"(hier oeffnet sich kein neuer Ast).")

        tag_source(source_map, opponents, "lobby")
        all_new_opponents |= new_opponents
        pool |= opponents
        known_crawled[seed] = now_iso()
        stats["seeds_crawled"] += 1

        update_players_live(opponents, updated_this_run, counts, last_checked,
                             source_map, since_ms, leaderboard, stats, player_info)

        # Neue Gegner werden ans ENDE der Queue gehaengt - genau das laesst
        # die Kette immer weiterwachsen, solange neue Gegner auftauchen.
        for name in sorted(new_opponents):
            if name not in remaining_queue and name not in known_crawled:
                remaining_queue.append(name)

        save_json_list(CRAWL_QUEUE_FILE, remaining_queue)
        save_json_dict(KNOWN_CRAWLED_FILE, known_crawled)
        save_json_dict(SOURCE_MAP_FILE, source_map)
        save_json_set(KNOWN_PLAYERS_FILE, pool)

    advance_crawl_direction(seeds, direction_state, player_info)

    return all_new_opponents, len(seeds)
# <<< ENDE FIX
# ---------------------------------------------------------------------------


def process_crawl_slice(pool: set, updated_this_run: set, counts: dict, last_checked: dict,
                         source_map: dict, since_ms: int, leaderboard: dict, stats: dict,
                         deadline: float, player_info: dict) -> tuple:
    """Fuehrt so viele Crawl-Runden aus, wie in die Zeitscheibe passen.
    Gibt (alle_neuen_spieler, gesamt_seeds_verarbeitet) zurueck."""
    total_new = set()
    total_seeds = 0
    while time.time() < deadline:
        new_players, seeds_processed = run_snowball_crawl_round(
            pool, updated_this_run, counts, last_checked, source_map,
            since_ms, leaderboard, stats, player_info,
        )
        total_new |= new_players
        total_seeds += seeds_processed
        if seeds_processed == 0:
            break
    return total_new, total_seeds


def process_tournament_slice(remaining_sources: list, pool: set, known_tournaments: set,
                              updated_this_run: set, counts: dict, last_checked: dict,
                              source_map: dict, since_ms: int, leaderboard: dict, stats: dict,
                              deadline: float, player_info: dict) -> list:
    """Verarbeitet Turniere aus remaining_sources, bis entweder die Liste
    leer ist oder die Zeitscheibe abgelaufen ist. Gibt den Rest zurueck."""
    while remaining_sources and time.time() < deadline:
        t_id, kind = remaining_sources.pop(0)
        participants = get_tournament_participants(t_id, kind)
        participants = check_and_filter_players(participants, player_info)
        new_count = len(participants - pool)
        pool |= participants
        if new_count:
            print(f"  Turnier {t_id} ({kind}): {len(participants)} Teilnehmer "
                  f"({new_count} neu im Pool).")
        tag_source(source_map, participants, "turnier")
        update_players_live(participants, updated_this_run, counts, last_checked,
                             source_map, since_ms, leaderboard, stats, player_info)
        known_tournaments.add(t_id)
        save_json_set(KNOWN_PLAYERS_FILE, pool)
        save_json_set(KNOWN_TOURNAMENTS_FILE, known_tournaments)
        save_json_dict(SOURCE_MAP_FILE, source_map)

    return remaining_sources


# ---------------------------------------------------------------------------
# PARTIEN ZAEHLEN
# ---------------------------------------------------------------------------
def count_recent_blitz_games(username: str, since_ms: int) -> int:
    params = urllib.parse.urlencode({
        "since": since_ms,
        "perfType": PERF_TYPE,
        "max": MAX_GAMES_PER_QUERY,
        "moves": "false",
        "tags": "false",
        "opening": "false",
        "clocks": "false",
        "evals": "false",
    })
    url = f"{BASE_URL}/api/games/user/{username}?{params}"
    count = 0
    _throttle_games_export()
    for _ in fetch_ndjson(url):
        count += 1
    return count


# ---------------------------------------------------------------------------
# AUSGABE-HELFER
# ---------------------------------------------------------------------------
def print_header(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_section(title: str) -> None:
    print()
    print(f"[{title}]")
    print("-" * 70)


def print_stat(label: str, value, width: int = 42) -> None:
    print(f"  {label:<{width}} {value}")


def profile_url(username: str) -> str:
    return f"{BASE_URL}/@/{username}"


def get_current_rank(name: str, counts: dict) -> int:
    ranking = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    for i, (n, _c) in enumerate(ranking, start=1):
        if n == name:
            return i
    return -1


def flashy_new_entry_banner(rank: int, name: str, count: int, source_label: str) -> None:
    print()
    print("  " + "*" * 60)
    if rank <= 3:
        print(f"  *** NEUER TOP-{rank}!! {name.upper()} MIT {count} PARTIEN! ***")
    elif rank <= 10:
        print(f"  *** NEU IN DEN TOP 10: {name} ({count} Partien)! ***")
    else:
        print(f"  * Neu in Top {TOP_N}: {name} ({count} Partien) - Platz {rank}")
    print(f"  -> Quelle: {source_label}   |   {profile_url(name)}")
    print("  " + "*" * 60)


def write_top10_snapshot(counts: dict, source_map: dict, player_info: dict = None) -> None:
    player_info = player_info or {}
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    ranking = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:TOP_N_LIVE]
    now = now_iso()

    snapshot = {
        "updated_at": now,
        "since_days": SINCE_DAYS,
        "top10": [
            {
                "rank": i, "username": name, "games": cnt,
                "rating": player_info.get(name, {}).get("rating"),
                "source": SOURCE_LABELS.get(source_map.get(name), "?"),
                "profile": profile_url(name),
            }
            for i, (name, cnt) in enumerate(ranking, start=1)
        ],
    }
    TOP10_JSON_FILE.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))

    lines = [
        f"# Top {TOP_N_LIVE} aktivste {PERF_TYPE}-Spieler (letzte {SINCE_DAYS} Tage)",
        "",
        f"_Zuletzt aktualisiert: {now}_",
        "",
        "| Platz | Spieler | Rating | Partien | Quelle | Profil |",
        "|---|---|---|---|---|---|",
    ]
    for i, (name, cnt) in enumerate(ranking, start=1):
        src = SOURCE_LABELS.get(source_map.get(name), "?")
        rating = player_info.get(name, {}).get("rating")
        rating_str = str(rating) if rating is not None else "?"
        lines.append(f"| {i} | {name} | {rating_str} | {cnt} | {src} | [{name}]({profile_url(name)}) |")
    TOP10_MD_FILE.write_text("\n".join(lines) + "\n")


def print_top(counts: dict, source_map: dict, player_info: dict = None, n: int = TOP_N) -> list:
    player_info = player_info or {}
    ranking = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:n]
    print_header(f"TOP {n} AKTIVSTE {PERF_TYPE.upper()}-SPIELER (letzte {SINCE_DAYS} Tage)")
    for i, (name, cnt) in enumerate(ranking, start=1):
        src = SOURCE_LABELS.get(source_map.get(name), "?")
        rating = player_info.get(name, {}).get("rating")
        rating_str = str(rating) if rating is not None else "?"
        print(f"  {i:>3}. {name:<20} {rating_str:>5}  {cnt:>5} Partien   "
              f"[{src:<11}]   {profile_url(name)}")
    print("=" * 70)
    return ranking


# ---------------------------------------------------------------------------
# ZENTRALE LIVE-VERARBEITUNG (mit Cooldown - der eigentliche Kern-Fix)
# ---------------------------------------------------------------------------
def update_players_live(usernames: set, already_updated: set, counts: dict, last_checked: dict,
                         source_map: dict, since_ms: int, leaderboard: dict,
                         stats: dict = None, player_info: dict = None) -> None:
    """
    Aktualisiert die Partienzahl fuer eine Menge Spieler - aber nur, wenn
    es sich lohnt:
      - Schon in diesem Lauf behandelt -> ueberspringen.
      - Komplett neuer Spieler -> immer sofort pruefen.
      - Bereits bekannter Spieler -> nur pruefen, wenn CHECK_COOLDOWN_HOURS
        seit der letzten Pruefung vergangen sind.

    GEAENDERT (Logging): Zusaetzlich zum auffaelligen Banner (nur bei
    Top-1000-Eintritt) wird JETZT fuer JEDEN tatsaechlich geprueften
    Spieler eine eigene Log-Zeile ausgegeben - unabhaengig vom Rang. So
    tauchen auch Spieler ausserhalb der Top 1000 im Log auf, nicht nur
    in leaderboard.json/known_players.json. Per Cooldown uebersprungene
    Spieler werden weiterhin NICHT einzeln geloggt (nur als Summe in der
    Lauf-Zusammenfassung), um die Ausgabe bei grossem Pool nicht komplett
    zu fluten.
    """
    if stats is None:
        stats = {"checked": 0, "skipped_cooldown": 0, "new": 0, "failed": 0}

    new_to_process = sorted(usernames - already_updated)
    if not new_to_process:
        return

    for username in new_to_process:
        already_updated.add(username)
        is_new_player = username not in counts

        if not is_new_player and not is_due(last_checked.get(username, ""), CHECK_COOLDOWN_SECONDS):
            stats["skipped_cooldown"] += 1
            continue

        try:
            new_count = count_recent_blitz_games(username, since_ms)
        except RateLimitError:
            raise
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            print(f"    [WARNUNG] '{username}' konnte nicht abgefragt werden: {exc}")
            stats["failed"] += 1
            continue

        old_count = counts.get(username)
        counts[username] = new_count
        last_checked[username] = now_iso()
        stats["checked"] += 1
        if is_new_player:
            stats["new"] += 1

        rank = get_current_rank(username, counts)
        source_label = SOURCE_LABELS.get(source_map.get(username), "?")

        # Immer eine Zeile pro tatsaechlich geprueftem Spieler loggen -
        # nicht nur bei Top-1000-Eintritt. Der auffaellige Banner bleibt
        # zusaetzlich fuer echte Top-1000-Eintritte bestehen.
        tag = "NEU" if is_new_player else "UPDATE"
        rank_str = str(rank) if rank > 0 else "?"
        print(f"    [{tag}] {username:<20} {new_count:>5} Partien  "
              f"(Rang: {rank_str:>5})  [{source_label:<11}]  {profile_url(username)}")

        if new_count > 0 and (old_count is None or new_count != old_count):
            if 0 < rank <= TOP_N:
                flashy_new_entry_banner(rank, username, new_count, source_label)

        leaderboard["counts"] = counts
        leaderboard["last_checked"] = last_checked
        leaderboard["updated_at"] = now_iso()
        save_leaderboard(leaderboard, source_map, player_info)
        write_top10_snapshot(counts, source_map, player_info)
        maybe_live_push()


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main() -> None:
    run_start = time.time()

    if not TOKEN:
        print("Hinweis: Kein LICHESS_TOKEN gesetzt - es wird unauthentifiziert "
              "abgefragt (deutlich niedrigeres Rate-Limit). Ein Personal Access "
              "Token (ohne Scopes) auf lichess.org erstellen und als "
              "LICHESS_TOKEN Secret setzen wird dringend empfohlen.")

    known_players = load_json_set(KNOWN_PLAYERS_FILE)
    known_tournaments = load_json_set(KNOWN_TOURNAMENTS_FILE)
    crawl_queue_len = len(load_json_list(CRAWL_QUEUE_FILE))
    known_crawled_len = len(load_json_dict(KNOWN_CRAWLED_FILE))
    source_map = load_json_dict(SOURCE_MAP_FILE)
    player_info = load_json_dict(PLAYER_INFO_FILE)
    # Migration: alte bot_status.json (username -> bool) in das neue,
    # reichhaltigere Format uebernehmen, falls player_info.json noch leer ist.
    # WICHTIG: checked_at bleibt leer ("nie geprueft") statt "jetzt" - die
    # alte Datei enthielt NUR den Bot-Status, keine echte Bann-Pruefung.
    # Wuerde man hier "jetzt" eintragen, wuerde das Skript faelschlich
    # denken, der Bann-Status sei frisch verifiziert, und wuerde den
    # echten Stand (inkl. bereits gebannter Accounts) bis zum Ablauf von
    # RATING_REFRESH_COOLDOWN_HOURS nicht pruefen.
    if not player_info:
        legacy_bot_status = load_json_dict(BOT_STATUS_FILE)
        if legacy_bot_status:
            for uid, is_bot in legacy_bot_status.items():
                is_bot = (is_bot is True or is_bot == "true")
                player_info[uid] = {
                    "bot": is_bot, "banned": False, "rating": None,
                    "checked_at": "",
                }

    print_header(f"BLITZ-ACTIVITY RUN - {PERF_TYPE} - {now_iso()}")
    print_stat("Bekannte Spieler im Pool", len(known_players))
    print_stat("Bekannte (bereits verarbeitete) Turniere", len(known_tournaments))
    print_stat("Crawl-Queue-Laenge", crawl_queue_len)
    print_stat("Bereits gecrawlte Spieler", known_crawled_len)
    print_stat("Cooldown Spieler-Refresh", f"{CHECK_COOLDOWN_HOURS:.0f}h")
    print_stat("Cooldown Recrawl", f"{RECRAWL_COOLDOWN_HOURS:.0f}h")
    print_stat("Zeitscheibe Crawl/Turniere", f"{PHASE_SLICE_SECONDS:.0f}s")
    print_stat("Gesamt-Zeitbudget Crawl/Turniere", f"{MAX_TOTAL_RUNTIME_SECONDS:.0f}s")
    print_stat("Globaler Mindestabstand pro Request", f"{GLOBAL_MIN_INTERVAL_SECONDS:.1f}s")

    leaderboard = load_leaderboard()
    counts = leaderboard.get("counts", {})
    last_checked = leaderboard.get("last_checked", {})
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=SINCE_DAYS)).timestamp() * 1000)

    pool = set(known_players)
    updated_this_run = set()

    # HINWEIS: Der frueher hier laufende separate Top-1000-Rating/Bann- und
    # Partienzahl-Massen-Refresh wurde komplett ENTFERNT, da er trotz
    # Cooldown/Throttling regelmaessig Rate-Limits ausgeloest hat (bis zu
    # 1000 zusaetzliche Requests an den ohnehin strikt limitierten Games-
    # Export-Endpunkt, obendrauf auf Crawl und Turniere). Das Ranking selbst
    # ist davon nicht betroffen: Rating/Bann-Status und Partienzahl werden
    # fuer jeden Spieler ohnehin schon laufend beim erstmaligen Fund (Crawl/
    # Turnier) sowie ueber den normalen CHECK_COOLDOWN_HOURS-Cooldown in
    # update_players_live() aktuell gehalten.

    save_json_set(KNOWN_PLAYERS_FILE, pool)
    save_json_set(KNOWN_TOURNAMENTS_FILE, known_tournaments)
    save_json_list(CRAWL_QUEUE_FILE, load_json_list(CRAWL_QUEUE_FILE))
    save_json_dict(KNOWN_CRAWLED_FILE, load_json_dict(KNOWN_CRAWLED_FILE))
    save_json_dict(SOURCE_MAP_FILE, source_map)
    maybe_live_push(force=True)

    overall_stats = {"checked": 0, "skipped_cooldown": 0, "new": 0, "failed": 0, "seeds_crawled": 0}

    try:
        # --- Bootstrap: Crawl-Queue animpfen, falls noch nie gecrawlt -----
        bootstrap_crawl_queue_if_empty(pool)

        # --- Lobby-Crawl & Turniere im Wechsel, CRAWL ZUERST --------------
        print_section("Lobby-Crawl & Turniere (abwechselnd, Crawl hat Prioritaet)")

        tournament_sources = list(get_visible_blitz_tournament_ids())
        for team_id in EXTRA_TEAM_IDS:
            print(f"  -> Turnierhistorie von Team '{team_id}'...")
            team_tournaments = get_team_tournament_ids(team_id.lower())
            print(f"     {len(team_tournaments)} {PERF_TYPE}-Turnier(e) gefunden.")
            tournament_sources.extend(team_tournaments)

        remaining_tournaments = [(tid, kind) for tid, kind in tournament_sources
                                  if tid not in known_tournaments]
        print_stat("Turniere insgesamt gesehen", len(tournament_sources))
        print_stat("Davon bereits verarbeitet (uebersprungen)",
                    len(tournament_sources) - len(remaining_tournaments))
        print_stat("Davon neu zu verarbeiten", len(remaining_tournaments))

        tournaments_done = not remaining_tournaments
        crawl_done = False
        loop_start = time.time()
        round_num = 0
        hit_time_cap = False

        while (not tournaments_done or not crawl_done):
            if (time.time() - loop_start) >= MAX_TOTAL_RUNTIME_SECONDS:
                hit_time_cap = True
                break

            round_num += 1

            # CRAWL ZUERST: garantiert, dass Lobby-Spieler in jeder Runde
            # eine Chance bekommen, bevor Turniere (potenziell langsam/
            # rate-limited) das restliche Budget aufbrauchen koennten.
            if not crawl_done:
                print(f"  -- Runde {round_num}: Lobby-Crawl --")
                deadline = time.time() + PHASE_SLICE_SECONDS
                new_from_crawl, seeds_processed = process_crawl_slice(
                    pool, updated_this_run, counts, last_checked, source_map,
                    since_ms, leaderboard, overall_stats, deadline, player_info,
                )
                pool |= new_from_crawl
                if seeds_processed == 0:
                    crawl_done = True
                    print("  Keine Crawl-Seeds mehr verfuegbar - Phase 'Lobby-Crawl' ist fuer diesen Lauf beendet.")

            if (time.time() - loop_start) >= MAX_TOTAL_RUNTIME_SECONDS:
                hit_time_cap = True
                break

            if not tournaments_done:
                print(f"  -- Runde {round_num}: Turniere ({len(remaining_tournaments)} offen) --")
                deadline = time.time() + PHASE_SLICE_SECONDS
                remaining_tournaments = process_tournament_slice(
                    remaining_tournaments, pool, known_tournaments, updated_this_run,
                    counts, last_checked, source_map, since_ms, leaderboard,
                    overall_stats, deadline, player_info,
                )
                if not remaining_tournaments:
                    tournaments_done = True
                    print("  Alle Turniere abgearbeitet - Phase 'Turniere' ist fuer diesen Lauf beendet.")

        if hit_time_cap:
            print(f"  Gesamt-Zeitbudget ({MAX_TOTAL_RUNTIME_SECONDS:.0f}s) erreicht - "
                  f"Rest folgt automatisch im naechsten Lauf.")
        save_json_set(KNOWN_PLAYERS_FILE, pool)

    except RateLimitError as exc:
        print()
        print(f"[RATE LIMIT] {exc}")
        print("Breche Skript sofort ab und speichere den bisherigen Stand. "
              "Naechster Lauf macht hier weiter.")

    save_json_set(KNOWN_PLAYERS_FILE, pool)
    save_json_set(KNOWN_TOURNAMENTS_FILE, known_tournaments)
    save_json_dict(SOURCE_MAP_FILE, source_map)
    save_json_dict(PLAYER_INFO_FILE, player_info)
    leaderboard["counts"] = counts
    leaderboard["last_checked"] = last_checked
    leaderboard["updated_at"] = now_iso()
    save_leaderboard(leaderboard, source_map, player_info)
    write_top10_snapshot(counts, source_map, player_info)
    maybe_live_push(force=True)

    elapsed = time.time() - run_start
    new_players_total = len(pool - known_players)

    print_header("ZUSAMMENFASSUNG DIESES LAUFS")
    print_stat("Laufzeit", format_duration(elapsed))
    print_stat("Spieler-Pool vorher -> nachher", f"{len(known_players)} -> {len(pool)} "
               f"(+{new_players_total})")
    print_stat("Neue Partien-Counts (nie geprueft)", overall_stats["new"])
    print_stat("Aktualisierte Counts (Cooldown abgelaufen)",
               overall_stats["checked"] - overall_stats["new"])
    print_stat("Uebersprungen wg. Cooldown", overall_stats["skipped_cooldown"])
    print_stat("Fehlgeschlagene Abfragen", overall_stats["failed"])
    print_stat("Crawl-Seeds in diesem Lauf", overall_stats["seeds_crawled"])
    print_stat("Spieler insgesamt im Leaderboard", len(counts))

    source_counts = {}
    for name in counts:
        label = SOURCE_LABELS.get(source_map.get(name), "?")
        source_counts[label] = source_counts.get(label, 0) + 1
    print_stat("Verteilung nach Quelle", ", ".join(
        f"{label}: {cnt}" for label, cnt in sorted(source_counts.items(), key=lambda kv: -kv[1])
    ))

    print_top(counts, source_map, player_info)
    print()
    print("Fertig.")


if __name__ == "__main__":
    main()

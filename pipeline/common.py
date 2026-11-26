"""Shared helpers for the Clue Finder puzzle pipeline (runs on GitHub Actions)."""
import datetime as dt, hashlib, hmac, json, os, re, time, unicodedata, urllib.request, urllib.error

import puzzlecrypt as pc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state/puzzles.json")
TG_STATE = os.path.join(ROOT, "state/telegram.json")
FEED = os.path.join(ROOT, "feed/puzzles.json")

EPOCH = dt.date(2026, 11, 1)           # day 1 (must match the app's DailySchedule.EPOCH)
REVIEW_DAYS = 7                        # unreviewed puzzles are approved automatically after this
STOCK_DAYS = 365                       # keep at least a year of puzzles published ahead of today
TEHRAN = dt.timezone(dt.timedelta(hours=3, minutes=30))

CATEGORIES = ["geography", "nature", "animals", "people", "film_tv", "brands", "science",
              "history", "food", "sports", "objects", "culture"]
CAT_FA = {"geography": "جغرافیا", "nature": "طبیعت", "animals": "حیوانات", "people": "شخصیت‌ها",
          "film_tv": "فیلم و سریال", "brands": "برندها", "science": "علم", "history": "تاریخ",
          "food": "خوراکی", "sports": "ورزش", "objects": "اشیا", "culture": "فرهنگ و هنر"}


def today_day() -> int:
    return (dt.datetime.now(TEHRAN).date() - EPOCH).days + 1


def day_date(day: int) -> dt.date:
    return EPOCH + dt.timedelta(days=day - 1)


def jalali(d: dt.date) -> str:
    gy, gm, gd = d.year, d.month, d.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053); days %= 12053
    jy += 4 * (days // 1461); days %= 1461
    if days > 365:
        jy += (days - 1) // 365; days = (days - 1) % 365
    jm = 1 + days // 31 if days < 186 else 7 + (days - 186) // 30
    jd = 1 + (days % 31 if days < 186 else (days - 186) % 30)
    months = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
    return fa_digits(f"{jd} {months[jm - 1]} {jy}")


def fa_digits(s) -> str:
    return str(s).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


# ── keys ────────────────────────────────────────────────────────────────────────
def keys():
    master = pc.unb64(re.sub(r"\s", "", os.environ["PUZZLE_MASTER_KEY"]))
    priv = pc.load_private_key(clean_pem(os.environ["PUZZLE_SIGNING_KEY"]))
    # The keys must be exactly the ones built into the app, or phones would reject everything.
    exp = json.load(open(os.path.join(ROOT, "pipeline/keys_expected.json")))
    if pc.public_key_b64(priv) != exp["signing_public_key"]:
        raise SystemExit("PUZZLE_SIGNING_KEY is not the app's key — copy it again from ClueFinder-keys.txt")
    if hmac.new(master, b"check", hashlib.sha256).hexdigest()[:16] != exp["master_check"]:
        raise SystemExit("PUZZLE_MASTER_KEY is not the app's key — copy it again from ClueFinder-keys.txt")
    return master, priv


def clean_pem(raw: str) -> str:
    """Accepts the key however it was pasted (lost line breaks, extra spaces, with or without header lines)."""
    body = re.sub(r"-----(BEGIN|END)[A-Z ]*-----", "", raw.replace("\\n", "\n"))
    body = re.sub(r"[^A-Za-z0-9+/=]", "", body)
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return "-----BEGIN PRIVATE KEY-----\n" + "\n".join(lines) + "\n-----END PRIVATE KEY-----\n"


# ── state ───────────────────────────────────────────────────────────────────────
def load_state():
    return json.load(open(STATE, encoding="utf-8"))


def save_state(s):
    s["entries"].sort(key=lambda e: (e["day"], e["rev"]))
    json.dump(s, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def load_tg():
    return json.load(open(TG_STATE)) if os.path.exists(TG_STATE) else {"offset": 0}


def current(state):
    """The live entry of every day (highest revision that is not rejected)."""
    out = {}
    for e in state["entries"]:
        if e["status"] == "rejected":
            continue
        if e["day"] not in out or e["rev"] > out[e["day"]]["rev"]:
            out[e["day"]] = e
    return out


def publish(state, priv) -> bool:
    """Writes feed/puzzles.json with every approved online day. Returns True if it changed."""
    entries = [{k: e[k] for k in ("day", "rev", "id", "n", "c")}
               for e in current(state).values() if e["status"] == "approved" and e["day"] > state["bundled_last_day"]]
    key = sorted((e["day"], e["rev"]) for e in entries)
    if os.path.exists(FEED) and state.get("feed_key") == key:
        return False
    json.dump(pc.sign_envelope(priv, entries), open(FEED, "w"), separators=(",", ":"))
    state["feed_key"] = key
    return True


# ── text normalisation (same rules as the app's answer matcher) ────────────────
_MAP = str.maketrans({"ي": "ی", "ى": "ی", "ئ": "ی", "ك": "ک", "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
                      "ؤ": "و", "ة": "ه", "ۀ": "ه"})


def words(s):
    s = re.sub(r"[​-‏‪-‮⁦-⁩]", " ", s)
    s = re.sub(r"[ً-ٰٟـ]", "", s).translate(_MAP)
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"[^\w]+", " ", s).replace("_", " ").strip()
    parts = [p for p in s.split() if p]
    if len(parts) > 1 and parts[0] in {"the", "a", "an"}:
        parts = parts[1:]
    return parts


def norm(s):
    return "".join(words(s))


def answer_tag(master, s) -> str:
    """Keyed hash of a normalised answer, so duplicates can be found without publishing answers."""
    return hmac.new(master, ("ans:" + norm(s)).encode(), hashlib.sha256).hexdigest()[:24]


# ── HTTP ────────────────────────────────────────────────────────────────────────
def http_json(url, payload=None, headers=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ── Telegram ────────────────────────────────────────────────────────────────────
def tg(method, **params):
    if os.environ.get("DRY_RUN"):
        print(f"[telegram] {method}: {json.dumps(params, ensure_ascii=False)[:600]}")
        return {"ok": True, "result": {"message_id": int(time.time() * 1000) % 100000}} if method == "sendMessage" else {"ok": True, "result": []}
    token = os.environ["REVIEW_BOT_TOKEN"].strip()
    try:
        return http_json(f"https://api.telegram.org/bot{token}/{method}", params, timeout=60)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"telegram {method} failed: {e.code} {body[:300]}")
        return {"ok": False, "description": body}


def chat_id():
    return os.environ["REVIEW_CHAT_ID"].strip()

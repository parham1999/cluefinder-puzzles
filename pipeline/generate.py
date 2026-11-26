"""Creates new puzzles with a free AI API (Google Gemini, Groq as backup), checks them, encrypts them and
sends each one to the owner's review bot on Telegram.

Runs daily on GitHub Actions (see .github/workflows/generate.yml).
"""
import json, os, random, re, sys, time, urllib.error

sys.path.insert(0, os.path.dirname(__file__))
import puzzlecrypt as pc
from common import *  # noqa: F401,F403
import review

# Free AI providers (OpenAI-compatible APIs). Each needs its own free API key as a repository secret.
PROVIDERS = [
    {"name": "gemini", "key": "GEMINI_API_KEY",
     "base": "https://generativelanguage.googleapis.com/v1beta/openai",
     "prefer": ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash"], "match": "flash"},
    {"name": "groq", "key": "GROQ_API_KEY",
     "base": "https://api.groq.com/openai/v1",
     "prefer": ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"], "match": "gpt-oss"},
]
_model_cache = {}
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "20"))
BATCH = 5

# Every banned subject — checked by a second AI and by the word list below.
BANNED = """
- ANY religion or religious subject: gods, prophets, imams, saints, holy books, religious places (mosques, churches, temples, shrines), rituals, religious holidays or months, clergy, religious history.
- ANY politics: governments, political systems, parties, politicians, elections, revolutions, protests, sanctions, ideologies, independence movements, disputed borders or disputed place names.
- ANYTHING about Iran's government, the Islamic Republic, the 1979 revolution, its leaders, the Pahlavi dynasty, the IRGC/Basij, hijab laws, or Iran's relations with Israel, the USA or any other country.
- Israel, Zionism, Palestine, and any current or recent conflict.
- Wars, battles, armies, weapons, terrorism, crime, executions, genocide, dictators.
- Any person who is alive today, and any controversial historical person.
- Alcohol, drugs, gambling, smoking, sex, romance, nudity, LGBT topics, gore, death or disease in detail.
- Ethnic, national or racial stereotypes or tensions; national flags or anthems.
""".strip()

BLACKLIST = re.compile(r"(?i)\b(god|gods|allah|prophet|imam|quran|koran|bible|torah|jesus|christ|church|mosque|synagogue|shrine|"
                       r"religio\w*|islam\w*|muslim|christian\w*|jew\w*|hindu\w*|buddhis\w*|pope|saint|ramadan|hajj|mecca|medina|"
                       r"politic\w*|government|president|parliament|election|revolution\w*|regime|ayatollah|khomeini|khamenei|"
                       r"pahlavi|shah|irgc|hijab|israel\w*|zionis\w*|palestin\w*|gaza|sanction\w*|war|wars|warfare|battle|army|"
                       r"military|soldier|weapon\w*|bomb\w*|missile|nuclear|terror\w*|nazi\w*|hitler|wine|beer|vodka|whisk(e)?y|"
                       r"alcohol\w*|cocktail|drug|drugs|cannabis|opium|casino|gambl\w*|sex\w*|kiss\w*|flag|anthem)\b")
BLACKLIST_FA = re.compile(r"(خدا|الله|پیامبر|پیغمبر|امام|قرآن|انجیل|تورات|مسیح|کلیسا|مسجد|کنیسه|حرم|زیارت|دین|دینی|مذهب|اسلام|مسلمان|"
                          r"شیعه|سنی|یهود|مسیحی|بودا|رمضان|محرم|عاشورا|حج|مکه|مدینه|سیاس|دولت|حکومت|رئیس.?جمهور|مجلس|انتخابات|"
                          r"انقلاب|نظام|رهبر|خمینی|خامنه|پهلوی|شاه|سپاه|بسیج|حجاب|اسرائیل|صهیون|فلسطین|غزه|تحریم|جنگ|نبرد|ارتش|"
                          r"سرباز|سلاح|بمب|موشک|هسته‌ای|تروریس|نازی|هیتلر|شراب|مشروب|الکل|ودکا|ویسکی|آبجو|مواد مخدر|تریاک|قمار|"
                          r"سکس|جنسی|بوسه|پرچم|سرود ملی)")


def pick_model(pv, key):
    """Uses the best preferred model that the provider currently lists."""
    if pv["name"] in _model_cache:
        return _model_cache[pv["name"]]
    try:
        ids = [m["id"].removeprefix("models/") for m in http_json(pv["base"] + "/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)["data"]]
    except Exception as e:
        print(f"{pv['name']}: cannot list models ({e}); using default")
        ids = []
    model = next((m for m in pv["prefer"] if m in ids), None) or next((m for m in ids if pv["match"] in m), None) or pv["prefer"][-1]
    print(f"{pv['name']}: using model {model}")
    _model_cache[pv["name"]] = model
    return model


def ask(messages, temperature=0.9):
    """Asks the first free AI provider that has a key and answers with valid JSON."""
    if os.environ.get("MOCK_MODEL"):
        mock = json.load(open(os.environ["MOCK_MODEL"]))
        return mock["censor"] if "(censor)" in messages[0]["content"] else mock["gen"]
    last = "no AI provider key set (GEMINI_API_KEY / GROQ_API_KEY)"
    for pv in PROVIDERS:
        key = os.environ.get(pv["key"], "").strip()
        if not key:
            continue
        model = pick_model(pv, key)
        for attempt in range(3):
            try:
                r = http_json(pv["base"] + "/chat/completions",
                              {"model": model, "messages": messages, "temperature": temperature,
                               "response_format": {"type": "json_object"}},
                              headers={"Authorization": f"Bearer {key}"}, timeout=240)
                content = r["choices"][0]["message"]["content"] or ""
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
                try:
                    return json.loads(content)
                except ValueError:
                    raise ValueError(f"answer is not JSON: {content[:200]!r}")
            except urllib.error.HTTPError as e:
                last = f"{pv['name']}/{model}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}"
                print(last)
                if e.code in (401, 403, 404):
                    break                       # wrong key / model → next provider
                time.sleep(20 if e.code == 429 else 5)
            except Exception as e:
                last = f"{pv['name']}/{model}: {e}"
                print(last)
                time.sleep(3)
    raise RuntimeError(f"all AI providers failed: {last}")


GEN_SYSTEM = f"""You write puzzles for "Clue Finder", a daily guessing game for Iranian players (Persian) and international players (English).
Each puzzle has ONE well-known answer (a thing, animal, place, food, invention, artwork, sport, brand, historical non-political figure who died long ago, etc.)
and FIVE clues ordered from hardest (obscure but true fact) to easiest (almost gives it away). Players see one clue at a time.

STRICT CONTENT RULES — never write a puzzle that touches any of these, not even in a clue or explanation:
{BANNED}
Also avoid anything that could offend conservative Iranian audiences. When in doubt, choose a different answer.

Quality rules:
- Every fact must be true and widely verifiable. No made-up numbers.
- The answer (or an obvious form/translation of it) must NOT appear in any clue, in either language.
- Clues are written in first person ("I …") like the examples, max ~140 characters each.
- Persian must be natural, fluent, standard Persian (use ی and ک, not ي/ك; use half-space (ZWNJ) in «می‌», «ها», «ترین» etc.).
  It is NOT a word-by-word translation: write it as a native Persian writer would.
- "accepted" lists common alternative spellings/names a player might type (same language), always including the answer itself.
- The explanation is 1–2 interesting sentences.
- Answers must be known to a typical Iranian teenager or adult. Mix difficulty: 1 easy, 2 medium, 3 hard.

Return JSON: {{"puzzles":[{{"category":"<one of {', '.join(CATEGORIES)}>","difficulty":1|2|3,
"en":{{"answer":"","accepted":[""],"clues":["","","","",""],"explanation":""}},
"fa":{{"answer":"","accepted":[""],"clues":["","","","",""],"explanation":""}}}}]}}"""

EXAMPLE = {"category": "animals", "difficulty": 1,
           "en": {"answer": "Elephant", "accepted": ["Elephant", "Elephants"],
                  "clues": ["I can also hear through my feet, sensing rumbles in the ground from kilometres away.",
                            "My molars are replaced six times during my life.", "I am the largest living land animal.",
                            "My two long front teeth are made of ivory.", "I have huge ears, and I drink and grab food with my trunk."],
                  "explanation": "Elephants are the largest land animals alive today. Their trunk contains tens of thousands of muscles."},
           "fa": {"answer": "فیل", "accepted": ["فیل"],
                  "clues": ["صدا را از راه پاهایم هم می‌شنوم و لرزش زمین را از کیلومترها دورتر حس می‌کنم.",
                            "دندان‌های آسیای من در طول عمرم شش بار عوض می‌شوند.", "بزرگ‌ترین جانور خشکی‌زی زنده هستم.",
                            "دو دندان بلند جلویی‌ام از عاج است.", "گوش‌های بزرگی دارم و با خرطومم آب می‌نوشم و غذا برمی‌دارم."],
                  "explanation": "فیل‌ها بزرگ‌ترین جانوران خشکی‌زی امروزند. خرطوم فیل ده‌ها هزار ماهیچه دارد."}}

CENSOR_SYSTEM = f"""You are a strict content censor for a puzzle game published in Iran. Flag a puzzle if ANY part of it
(answer, clues, explanation, in English or Persian) touches one of these banned subjects, even indirectly:
{BANNED}
Also flag it if a fact looks false or doubtful, if the answer appears in a clue, or if the Persian text is poor or unnatural.
Return JSON: {{"flagged":[{{"index":<number>,"reason":"<short reason>"}}]}} (empty list if all are fine)."""


def validate(p, known_tags, master):
    """Returns a list of problems (empty = good)."""
    errs = []
    if p.get("category") not in CATEGORIES:
        errs.append("bad category")
    if p.get("difficulty") not in (1, 2, 3):
        p["difficulty"] = 2
    for lang in ("en", "fa"):
        L = p.get(lang) or {}
        clues = L.get("clues") or []
        if len(clues) != 5 or any(not isinstance(c, str) or not c.strip() or len(c) > 200 for c in clues):
            errs.append(f"{lang}: needs 5 clues"); continue
        if not str(L.get("answer", "")).strip() or not str(L.get("explanation", "")).strip():
            errs.append(f"{lang}: empty answer/explanation"); continue
        L["accepted"] = list(dict.fromkeys([L["answer"]] + [a for a in L.get("accepted", []) if isinstance(a, str) and a.strip()]))
        text = " ".join(clues + [L["explanation"], L["answer"]])
        if lang == "fa" and re.search("[يك]", text):
            L.update(json.loads(json.dumps(L).replace("ي", "ی").replace("ك", "ک")))
    if errs:
        return errs
    # answer leak in any language's clues
    cands = {norm(a) for lang in ("en", "fa") for a in p[lang]["accepted"] if len(norm(a)) >= 3}
    for lang in ("en", "fa"):
        for i, clue in enumerate(p[lang]["clues"]):
            cw = words(clue); joined = "".join(cw)
            for a in cands:
                if a in cw or (len(a) >= 5 and a in joined):
                    errs.append(f"{lang}: clue {i + 1} contains the answer")
    whole = json.dumps({k: p[k] for k in ("en", "fa")}, ensure_ascii=False)
    m = BLACKLIST.search(whole) or BLACKLIST_FA.search(whole)
    if m:
        errs.append(f"banned word: {m.group(0)}")
    for a in p["en"]["accepted"] + p["fa"]["accepted"]:
        if answer_tag(master, a) in known_tags:
            errs.append(f"duplicate answer"); break
    return errs


def make_puzzles(count, state, master):
    existing = [pc.decrypt_entry(master, e) for e in current(state).values()]
    known_tags = set(state.get("answer_tags", []))
    avoid = [x["content"]["en"]["answer"] for x in existing][-400:]
    made, tries = [], 0
    while len(made) < count and tries < count // BATCH + 6:
        tries += 1
        n = min(BATCH, count - len(made))
        cats = random.sample(CATEGORIES, k=min(n + 2, len(CATEGORIES)))
        user = (f"Write {n + 2} NEW puzzles (we will keep the best {n}). Use these categories, one each: {', '.join(cats)}.\n"
                f"Do NOT use any of these answers (already used): {', '.join(avoid + [m['en']['answer'] for m in made])}\n\n"
                f"Example of the exact style and format:\n{json.dumps({'puzzles': [EXAMPLE]}, ensure_ascii=False)}")
        try:
            batch = ask([{"role": "system", "content": GEN_SYSTEM}, {"role": "user", "content": user}]).get("puzzles", [])
        except Exception as e:
            print("generation failed:", e); break
        good = []
        for p in batch:
            problems = validate(p, known_tags, master)
            if problems:
                print("dropped:", p.get("en", {}).get("answer"), problems)
            else:
                good.append(p)
        if not good:
            continue
        # second, independent AI pass as a censor
        try:
            verdict = ask([{"role": "system", "content": CENSOR_SYSTEM + "\n(censor)"},
                           {"role": "user", "content": json.dumps([{"index": i, "en": g["en"], "fa": g["fa"]} for i, g in enumerate(good)], ensure_ascii=False)}],
                          temperature=0)
            flagged = {f.get("index"): f.get("reason") for f in verdict.get("flagged", [])}
        except Exception as e:
            print("censor failed, dropping batch:", e); continue
        for i, g in enumerate(good):
            if i in flagged:
                print("censored:", g["en"]["answer"], "-", flagged[i]); continue
            if len(made) < count:
                made.append(g)
                for a in g["en"]["accepted"] + g["fa"]["accepted"]:
                    known_tags.add(answer_tag(master, a))
    state["answer_tags"] = sorted(known_tags)
    return made


def free_days(state, how_many):
    """Days that need a puzzle: holes left by rejections first, then the next days after the last one."""
    taken = set(current(state).keys())
    first = state["bundled_last_day"] + 1
    last = max(taken | {state["bundled_last_day"]})
    holes = [d for d in range(first, last + 1) if d not in taken and d > today_day()]
    out = holes[:how_many]
    d = last
    while len(out) < how_many:
        d += 1; out.append(d)
    return out


def main():
    master, priv = keys()
    state = load_state()
    target_last = today_day() + STOCK_DAYS + REVIEW_DAYS
    taken = set(current(state).keys())
    need = len([d for d in range(state["bundled_last_day"] + 1, target_last + 1) if d not in taken and d > today_day()])
    count = min(need, MAX_PER_RUN)
    print(f"today=day {today_day()}, stocked to day {max(taken)}, missing {need}, making {count}")
    if count > 0:
        puzzles = make_puzzles(count, state, master)
        for day, p in zip(free_days(state, len(puzzles)), puzzles):
            rev = 1 + max([e["rev"] for e in state["entries"] if e["day"] == day] or [-1])
            pid = f"g{day}r{rev}"
            plain = {"id": pid, "category": p["category"], "difficulty": p["difficulty"],
                     "content": {"fa": p["fa"], "en": p["en"]}}
            entry = pc.encrypt_puzzle(master, day, rev, plain)
            entry.update(status="pending", created=int(time.time()))
            msg = review.send_for_review(entry, plain)
            entry["tg"] = msg
            state["entries"].append(entry)
            save_state(state)          # save after every puzzle: a crash never loses work
        print(f"added {len(puzzles)} puzzles")
    review.auto_approve(state)
    publish(state, priv)
    save_state(state)


if __name__ == "__main__":
    missing = [k for k in ("PUZZLE_MASTER_KEY", "PUZZLE_SIGNING_KEY", "REVIEW_BOT_TOKEN", "REVIEW_CHAT_ID") if not os.environ.get(k)]
    if missing and not os.environ.get("DRY_RUN"):
        print("Secrets not set yet:", ", ".join(missing), "— skipping.")
        sys.exit(0)
    main()

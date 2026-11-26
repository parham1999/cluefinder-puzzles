"""Telegram review bot (no server needed): every few minutes GitHub Actions reads the
owner's button presses / messages, approves or rejects puzzles, auto-approves puzzles
nobody reviewed for REVIEW_DAYS days, and republishes the signed feed.
"""
import json, os, re, sys, time

sys.path.insert(0, os.path.dirname(__file__))
import puzzlecrypt as pc
from common import *  # noqa: F401,F403

DIFF_FA = {1: "آسان", 2: "متوسط", 3: "سخت"}


def publish_time(e):
    """Unreviewed puzzles go live after REVIEW_DAYS, or sooner if their day is close."""
    by_review = e["created"] + REVIEW_DAYS * 86400
    by_day = int(dt.datetime.combine(day_date(e["day"]), dt.time(), dt.timezone.utc).timestamp()) - 3 * 86400
    return min(by_review, by_day)


def review_text(entry, plain, status_line=None):
    fa, en = plain["content"]["fa"], plain["content"]["en"]
    d = day_date(entry["day"])
    auto = time.strftime("%Y-%m-%d", time.gmtime(publish_time(entry))) if "created" in entry else ""
    lines = [
        f"🧩 معمای شمارهٔ {fa_digits(entry['day'])} — {jalali(d)} ({d.isoformat()})",
        f"📂 {CAT_FA.get(plain['category'], plain['category'])} · {DIFF_FA.get(plain['difficulty'], '')}",
        "",
        f"✅ جواب: <b>{esc(fa['answer'])}</b>  |  {esc(en['answer'])}",
        "",
    ]
    lines += [f"{fa_digits(i + 1)}. {esc(c)}" for i, c in enumerate(fa["clues"])]
    lines += ["", f"💡 {esc(fa['explanation'])}", "", "🇬🇧 " + " / ".join(esc(c) for c in en["clues"]), ""]
    lines.append(status_line or f"⏳ اگر کاری نکنی، {fa_digits(REVIEW_DAYS)} روز بعد خودکار تأیید می‌شود.")
    return "\n".join(lines)[:4000]


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def buttons(entry):
    return {"inline_keyboard": [[{"text": "✅ تأیید", "callback_data": f"a:{entry['day']}:{entry['rev']}"},
                                 {"text": "❌ رد", "callback_data": f"r:{entry['day']}:{entry['rev']}"}]]}


def send_for_review(entry, plain):
    r = tg("sendMessage", chat_id=chat_id(), text=review_text(entry, plain), parse_mode="HTML", reply_markup=buttons(entry))
    return r.get("result", {}).get("message_id") if r.get("ok") else None


def set_status(state, master, day, rev, status, source):
    for e in state["entries"]:
        if e["day"] == day and e["rev"] == rev:
            if e["status"] == status:
                return e, False
            e["status"], e["decided"], e["by"] = status, int(time.time()), source
            plain = pc.decrypt_entry(master, e)
            line = {"approved": "✅ تأیید شد", "rejected": "❌ رد شد — یک معمای تازه جایش ساخته می‌شود."}[status]
            if source == "auto":
                line = "✅ خودکار تأیید شد (بدون بررسی)"
            if e.get("tg"):
                tg("editMessageText", chat_id=chat_id(), message_id=e["tg"], text=review_text(e, plain, line), parse_mode="HTML")
            return e, True
    return None, False


def handle_updates(state, master):
    tgs = load_tg()
    r = tg("getUpdates", offset=tgs["offset"], timeout=0, allowed_updates=["message", "callback_query"])
    changed = False
    for u in r.get("result", []):
        tgs["offset"] = u["update_id"] + 1
        if "callback_query" in u:
            q = u["callback_query"]
            if str(q["from"]["id"]) != chat_id() and str(q.get("message", {}).get("chat", {}).get("id")) != chat_id():
                continue
            m = re.fullmatch(r"([ar]):(\d+):(\d+)", q.get("data", ""))
            if not m:
                continue
            status = "approved" if m.group(1) == "a" else "rejected"
            e, did = set_status(state, master, int(m.group(2)), int(m.group(3)), status, "owner")
            changed |= did
            tg("answerCallbackQuery", callback_query_id=q["id"], text="ثبت شد" if e else "پیدا نشد")
        elif "message" in u:
            msg = u["message"]
            if str(msg.get("chat", {}).get("id")) != chat_id():
                continue
            text = (msg.get("text") or "").strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
            m = re.fullmatch(r"(رد|تایید|تأیید|reject|approve)\s*(\d+)", text, re.I)
            if m:
                day = int(m.group(2))
                live = current(state).get(day)
                if live:
                    status = "rejected" if m.group(1).lower() in ("رد", "reject") else "approved"
                    _, did = set_status(state, master, day, live["rev"], status, "owner")
                    changed |= did
                    tg("sendMessage", chat_id=chat_id(), text=f"معمای {fa_digits(day)}: {'رد شد ❌' if status == 'rejected' else 'تأیید شد ✅'}")
                else:
                    tg("sendMessage", chat_id=chat_id(), text=f"معمای شمارهٔ {fa_digits(day)} پیدا نشد.")
            elif text in ("/start", "/status", "وضعیت"):
                tg("sendMessage", chat_id=chat_id(), text=status_text(state))
    json.dump(tgs, open(TG_STATE, "w"))
    return changed


def status_text(state):
    cur = current(state)
    online = [e for d, e in cur.items() if d > state["bundled_last_day"]]
    pend = sum(e["status"] == "pending" for e in online)
    appr = sum(e["status"] == "approved" for e in online)
    last = max(cur) if cur else 0
    return (f"📊 وضعیت معماها\n"
            f"امروز: معمای {fa_digits(today_day())}\n"
            f"آماده تا: معمای {fa_digits(last)} ({jalali(day_date(last))})\n"
            f"منتشرشده (آنلاین): {fa_digits(appr)}\n"
            f"در انتظار بررسی: {fa_digits(pend)}\n\n"
            f"برای رد: دکمهٔ ❌ زیر معما، یا بنویس «رد ۱۲۳»")


def auto_approve(state):
    now, changed = time.time(), False
    master = keys()[0]
    for e in list(current(state).values()):
        if e["status"] == "pending" and now >= publish_time(e):
            changed |= set_status(state, master, e["day"], e["rev"], "approved", "auto")[1]
    return changed


def main():
    master, priv = keys()
    state = load_state()
    handle_updates(state, master)
    auto_approve(state)
    publish(state, priv)
    save_state(state)


if __name__ == "__main__":
    missing = [k for k in ("PUZZLE_MASTER_KEY", "PUZZLE_SIGNING_KEY", "REVIEW_BOT_TOKEN", "REVIEW_CHAT_ID") if not os.environ.get(k)]
    if missing and not os.environ.get("DRY_RUN"):
        print("Secrets not set yet:", ", ".join(missing), "— skipping.")
        sys.exit(0)
    main()

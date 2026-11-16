#!/usr/bin/env python3
"""Жаршы — вежливые письма организаторам, когда в карточке не хватает данных.

Порядок:
  1. Сначала enrich.py ищет недостающее на сайте сам. Пишем, только если не нашёл (или сайта нет).
  2. Повод — конкретное событие через 3–45 дней, у которого есть e-mail организатора и нет времени/площадки/программы.
  3. Режим OUTREACH_MODE:
       draft (по умолчанию) — бот только готовит письма. Арман видит их на сайте по адресу …/#outreach
                              и отправляет одним нажатием из своей почты (или копирует текст);
       auto                 — бот сам отправляет с ящика Жаршы (секреты SMTP_*) и читает ответы (IMAP).
  4. Ответ читает Claude: забирает время/адрес/программу → карточка получает «✓ Подтверждено организатором»,
     бот благодарит. Сложное (партнёрство, цены, жалобы) не отвечает — пересылает на OWNER_EMAIL.
Правила: одно письмо + одно напоминание через 5 дней; на один адрес — не чаще раза в 14 дней;
ответ «стоп»/«отписаться» — адрес в стоп-лист навсегда.
Состояние: data/outreach.json (только в приватном репозитории).
"""
from __future__ import annotations

import datetime as dt
import email
import email.utils
import imaplib
import json
import os
import re
import smtplib
import sys
from email.header import decode_header, make_header
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import enrich  # noqa: E402
import run  # noqa: E402

STATE = run.ROOT / "data" / "outreach.json"
MODE = os.environ.get("OUTREACH_MODE") or "draft"
STOP = re.compile(r"\b(стоп|stop|отпис|unsubscribe|не пишите|тоқта)", re.I)
NEED_RU = {"time": "время начала (открытия)", "venue": "точный адрес / площадку", "program_url": "программу мероприятия",
           "registration_url": "ссылку на регистрацию"}
NEED_EN = {"time": "the opening time", "venue": "the exact venue / address", "program_url": "the programme",
           "registration_url": "the registration link"}


def load() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"threads": [], "optout": []}


def letter(e: dict, need: list[str], reminder: bool = False) -> tuple[str, str]:
    abroad = (e.get("country") or "Казахстан") != "Казахстан"
    tag = f"[ZH-{e['id'][-6:]}]"
    if abroad:
        items = "\n".join(f"• {NEED_EN[n]}" for n in need)
        subj = f"{e['title'][:80]} — details for the Zharshy calendar {tag}"
        body = (("Hello again!\n\nJust a gentle reminder of our message below.\n\n" if reminder else "Hello!\n\n") +
                f"We have included «{e['title']}» in Zharshy — a calendar of business events for Kazakhstan — "
                "so that more participants from Kazakhstan learn about it. To help them plan the trip, could you please share:\n"
                f"{items}\n\nA short reply to this email is enough. Thank you!\n\nZharshy team\n"
                "If you prefer not to receive such emails, just reply “stop”.")
    else:
        items = "\n".join(f"• {NEED_RU[n]}" for n in need)
        subj = f"{e['title'][:80]} — уточнение для календаря Жаршы {tag}"
        body = (("Здравствуйте ещё раз!\n\nНапоминаем о нашем письме ниже — будем благодарны за короткий ответ.\n\n" if reminder
                 else "Здравствуйте!\n\n") +
                f"Мы включили «{e['title']}» ({e['start'][8:10]}.{e['start'][5:7]}, {e.get('city','')}) в Жаршы — календарь "
                "деловых мероприятий Казахстана, чтобы о вашем событии узнало больше участников. "
                "Чтобы люди пришли вовремя, уточните, пожалуйста:\n"
                f"{items}\n\nДостаточно ответить на это письмо. Спасибо!\n\nКоманда Жаршы\n"
                "Если не хотите получать такие письма — ответьте «стоп».")
    return subj, body


def smtp_send(to: str, subj: str, body: str, reply_to_msgid: str | None = None) -> str:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"], msg["To"] = subj, to
    msg["From"] = os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"]
    msg["Message-ID"] = email.utils.make_msgid(domain="zharshy")
    if reply_to_msgid:
        msg["In-Reply-To"] = msg["References"] = reply_to_msgid
    with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)
    return msg["Message-ID"]


def make_drafts(db: dict, st: dict, today: dt.date) -> int:
    have = {t["event_id"] for t in st["threads"]}
    recent = {t["to"].lower(): t["created"] for t in st["threads"]}
    n = 0
    for e in db["events"]:
        start = dt.date.fromisoformat(e["start"])
        if not (today + dt.timedelta(days=3) <= start <= today + dt.timedelta(days=45)):
            continue
        to = (e.get("email") or "").strip()
        if not to or "@" not in to or e["id"] in have or to.lower() in st["optout"] or e.get("user_submitted"):
            continue
        if to.lower() in recent and (today - dt.date.fromisoformat(recent[to.lower()][:10])).days < 14:
            continue
        need = [m for m in enrich.missing(e) if m != "registration_url" or not e.get("site")]
        if e.get("site") and not e.get("enrich_at"):
            continue                       # сначала пусть enrich.py поищет на сайте
        if not any(m in need for m in ("time", "venue", "program_url")):
            continue                       # только регистрация — не повод писать
        subj, body = letter(e, need)
        st["threads"].append({"event_id": e["id"], "title": e["title"], "to": to, "need": need, "subject": subj, "body": body,
                              "status": "draft", "created": today.isoformat(), "sent_at": None, "msgid": None, "log": []})
        n += 1
    return n


def send_due(db: dict, st: dict, today: dt.date) -> int:
    n = 0
    evs = {e["id"]: e for e in db["events"]}
    for t in st["threads"]:
        e = evs.get(t["event_id"])
        if not e or t["to"].lower() in st["optout"]:
            continue
        if t["status"] in ("draft", "approved"):
            t["msgid"] = smtp_send(t["to"], t["subject"], t["body"])
            t["status"], t["sent_at"] = "sent", today.isoformat()
            n += 1
        elif t["status"] == "sent" and not t.get("reminded") and t["sent_at"] and \
                (today - dt.date.fromisoformat(t["sent_at"])).days >= 5 and dt.date.fromisoformat(e["start"]) > today + dt.timedelta(days=2):
            need = [m for m in t["need"] if m in enrich.missing(e)]
            if need:
                subj, body = letter(e, need, reminder=True)
                smtp_send(t["to"], "Re: " + t["subject"], body, t.get("msgid"))
                t["reminded"] = today.isoformat()
                n += 1
    return n


def body_text(m) -> str:
    if m.is_multipart():
        for part in m.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        return ""
    return m.get_payload(decode=True).decode(m.get_content_charset() or "utf-8", "replace")


def read_replies(db: dict, st: dict, today: dt.date) -> int:
    host = os.environ.get("IMAP_HOST") or os.environ.get("SMTP_HOST", "smtp.gmail.com").replace("smtp.", "imap.")
    box = imaplib.IMAP4_SSL(host)
    box.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
    box.select("INBOX")
    _, ids = box.search(None, '(UNSEEN SUBJECT "ZH-")')
    evs = {e["id"]: e for e in db["events"]}
    n = 0
    for i in ids[0].split():
        _, data = box.fetch(i, "(RFC822)")
        m = email.message_from_bytes(data[0][1])
        subj = str(make_header(decode_header(m.get("Subject", ""))))
        tag = re.search(r"ZH-([\w-]{6})", subj)
        t = next((x for x in st["threads"] if tag and x["event_id"].endswith(tag.group(1))), None)
        if not t:
            continue
        text = re.split(r"\n>|\nOn .+wrote:|\n.+написал", body_text(m))[0].strip()[:4000]
        t["log"].append({"at": today.isoformat(), "from": "organizer", "text": text[:500]})
        n += 1
        if STOP.search(text):
            st["optout"].append(t["to"].lower())
            t["status"] = "optout"
            continue
        e = evs.get(t["event_id"])
        got = understand(e, text) if e else {}
        if e:
            for k in ("time", "venue", "program_url", "registration_url"):
                v = got.get(k)
                if v and (k != "time" or re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(v))):
                    e[k] = str(v).strip()
            if any(got.get(k) for k in ("time", "venue", "program_url", "registration_url")):
                e["confirmed_org"] = True
        if got.get("needs_human") and os.environ.get("OWNER_EMAIL"):
            smtp_send(os.environ["OWNER_EMAIL"], f"Жаршы: ответ организатора требует вашего внимания — {t['title'][:60]}",
                      f"От: {t['to']}\nСобытие: {t['title']}\nКратко: {got.get('summary','')}\n\n{text}")
            t["status"] = "human"
        else:
            thanks = ("Большое спасибо! Мы обновили карточку мероприятия в Жаршы.\n\n"
                      "Если у вас будут и другие события — их можно добавить самостоятельно через кнопку "
                      "«+ Добавить мероприятие» на сайте Жаршы.\n\nКоманда Жаршы")
            smtp_send(t["to"], "Re: " + t["subject"], thanks, m.get("Message-ID"))
            t["status"] = "done"
    box.logout()
    return n


def understand(e: dict, text: str) -> dict:
    import anthropic
    prompt = (f"Это ответ организатора мероприятия «{e['title']}» ({e['start']}, {e.get('city','')}) на наш вопрос "
              "о недостающих данных для календаря. Верни JSON: {\"time\": \"HH:MM\"|null, \"venue\": str|null, "
              "\"program_url\": str|null, \"registration_url\": str|null, \"needs_human\": bool, \"summary\": str}. "
              "needs_human=true, если в письме вопросы, предложения о сотрудничестве/оплате, жалоба или просьба, "
              "на которую должен ответить человек. Ничего не выдумывай.\n\nПисьмо:\n" + text)
    msg = anthropic.Anthropic().messages.create(model=run.MODEL, max_tokens=300, messages=[{"role": "user", "content": prompt}])
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    mm = re.search(r"\{.*\}", raw, re.S)
    return json.loads(mm.group(0)) if mm else {"needs_human": True, "summary": "не удалось разобрать"}


def main() -> int:
    today = dt.datetime.now(run.TZ).date()
    db = json.loads(run.DB.read_text(encoding="utf-8"))
    st = load()
    made = make_drafts(db, st, today)
    sent = replies = 0
    if MODE == "auto" and os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS"):
        try:
            replies = read_replies(db, st, today)
        except Exception as ex:
            print(f"  ✗ чтение ответов: {ex}")
        try:
            sent = send_due(db, st, today)
        except Exception as ex:
            print(f"  ✗ отправка: {ex}")
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    run.DB.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Письма организаторам ({MODE}): новых черновиков {made}, отправлено {sent}, ответов {replies}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

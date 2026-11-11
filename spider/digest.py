#!/usr/bin/env python3
"""Жаршы — утренние напоминания: «завтра пройдёт…» на почту и в WhatsApp.

Каждое утро (GitHub Actions, 08:00 по Алматы) берёт мероприятия, которые начинаются завтра
и через 7 дней, отбирает их под интересы каждого подписчика и отправляет письмо и/или сообщение
в WhatsApp. Если задан ANTHROPIC_API_KEY, текст пишет ИИ-агент (Claude) — коротко, по-человечески,
на языке подписчика; без ключа уходит аккуратный шаблон.

Подписчики — spider/subscribers.yaml (лежит только в приватном репозитории).
Секреты: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM — почта;
         GREEN_API_URL, GREEN_ID_INSTANCE, GREEN_API_TOKEN — WhatsApp.
Запуск без отправки (проверка): python spider/digest.py --dry
"""
from __future__ import annotations

import datetime as dt
import json
import os
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TZ = dt.timezone(dt.timedelta(hours=5))
SITE = "https://armanimachinena.github.io/zharshy-web/"
MONTHS = {"ru": "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split(),
          "kk": "қаңтар ақпан наурыз сәуір мамыр маусым шілде тамыз қыркүйек қазан қараша желтоқсан".split(),
          "en": "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()}
HEAD = {"ru": ("Завтра", "Через неделю", "Жаршы: мероприятия"), "kk": ("Ертең", "Бір аптадан кейін", "Жаршы: іс-шаралар"),
        "en": ("Tomorrow", "In a week", "Zharshy: events")}


def title(e, lang):
    return ((e.get("i18n") or {}).get(lang) or {}).get("title") or e["title"]


def day(iso, lang):
    d = dt.date.fromisoformat(iso)
    return f"{d.day} {MONTHS[lang][d.month - 1]}"


def matches(e, sub):
    cities, inds, kw = sub.get("cities") or [], sub.get("industries") or [], [k.lower() for k in sub.get("keywords") or []]
    if cities and e.get("city") not in cities:
        return False
    if inds and not set(inds) & set(e.get("industries") or []):
        return False
    if kw:
        hay = " ".join([e["title"], e.get("description", ""), e.get("organizer", ""),
                        " ".join(s.get("name", "") + " " + s.get("company", "") for s in e.get("speakers") or [])]).lower()
        if not any(k in hay for k in kw):
            return False
    return True


def template(groups, lang):
    out = []
    for head, evs in groups:
        if not evs:
            continue
        out.append(f"{head}:")
        for e in evs:
            where = ", ".join(x for x in [e.get("venue"), e.get("city")] if x and x != "уточняется")
            out.append(f"• {title(e, lang)} — {day(e['start'], lang)}{', ' + where if where else ''}" + (f"\n  {e['site']}" if e.get("site") else ""))
        out.append("")
    out.append(SITE)
    return "\n".join(out)


def ai_text(groups, lang, name):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    data = [{"when": h, "events": [{"title": title(e, lang), "start": e["start"], "end": e.get("end"), "city": e.get("city"),
                                    "venue": e.get("venue"), "organizer": e.get("organizer"), "site": e.get("site"),
                                    "speakers": [s.get("name") for s in e.get("speakers") or []]} for e in evs]} for h, evs in groups if evs]
    lang_name = {"ru": "русском", "kk": "казахском", "en": "английском"}[lang]
    msg = anthropic.Anthropic().messages.create(model=os.environ.get("ZHARSHY_MODEL", "claude-haiku-5-5"), max_tokens=900, messages=[{
        "role": "user", "content": f"Ты — ИИ-агент сервиса «Жаршы». Напиши короткое утреннее напоминание на {lang_name} языке"
        f"{' для ' + name if name else ''}: какие деловые мероприятия будут завтра и через неделю. Для каждого — название, дата, место, "
        f"ссылка; одна фраза, зачем идти, только по данным ниже, ничего не выдумывай. Без markdown-таблиц, до 900 символов. "
        f"В конце ссылка {SITE}\n\nДанные: {json.dumps(data, ensure_ascii=False)}"}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip() or None


def send_email(to, subject, body):
    h = os.environ.get("SMTP_HOST")
    if not h:
        print(f"  · почта не настроена — пропускаю {to}")
        return
    m = MIMEText(body, "plain", "utf-8")
    m["Subject"], m["From"], m["To"] = subject, os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "")), to
    with smtplib.SMTP_SSL(h, int(os.environ.get("SMTP_PORT", "465"))) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(m)
    print(f"  ✓ письмо → {to[:3]}***")


def send_whatsapp(phone, body):
    import whatsapp
    if not whatsapp.configured():
        print("  · WhatsApp не настроен — пропускаю")
        return
    import requests
    base = os.environ["GREEN_API_URL"].rstrip("/")
    url = f"{base}/waInstance{os.environ['GREEN_ID_INSTANCE']}/sendMessage/{os.environ['GREEN_API_TOKEN']}"
    chat = "".join(c for c in str(phone) if c.isdigit()) + "@c.us"
    requests.post(url, json={"chatId": chat, "message": body}, timeout=30).raise_for_status()
    print(f"  ✓ WhatsApp → ***{chat[-8:-5]}")


def main(dry=False):
    import yaml
    sys.path.insert(0, str(Path(__file__).parent))
    today = dt.datetime.now(TZ).date()
    tom, week = (today + dt.timedelta(days=1)).isoformat(), (today + dt.timedelta(days=7)).isoformat()
    events = json.loads((ROOT / "data/events.json").read_text(encoding="utf-8"))["events"]
    subs_file = Path(__file__).parent / "subscribers.yaml"
    subs = (yaml.safe_load(subs_file.read_text(encoding="utf-8")) or {}).get("subscribers", []) if subs_file.exists() else []
    print(f"Завтра {tom}: {sum(e['start'] == tom for e in events)}, через неделю {week}: {sum(e['start'] == week for e in events)}; подписчиков: {len(subs)}")
    for sub in subs:
        lang = sub.get("lang", "ru") if sub.get("lang") in HEAD else "ru"
        h1, h7, subj = HEAD[lang]
        groups = [(h1, [e for e in events if e["start"] == tom and matches(e, sub)]),
                  (h7, [e for e in events if e["start"] == week and matches(e, sub)])]
        if not any(evs for _, evs in groups):
            continue
        body = ai_text(groups, lang, sub.get("name")) or template(groups, lang)
        if dry:
            print("---", sub.get("name", ""), "\n" + body)
            continue
        try:
            if sub.get("email"):
                send_email(sub["email"], f"{subj} — {day(tom, lang)}", body)
            if sub.get("whatsapp"):
                send_whatsapp(sub["whatsapp"], body)
        except Exception as ex:
            print(f"  ✗ {sub.get('name', '?')}: {ex}")


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)

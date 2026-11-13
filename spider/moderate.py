#!/usr/bin/env python3
"""Жаршы — приём мероприятий от пользователей с модерацией.

Заявки приходят из Google Формы (ответы → Google Таблица → «Опубликовать в интернете» → CSV).
Ссылка на CSV — секрет SUBMISSIONS_CSV. Каждые 3 часа:
  1) правила: обязательные поля, ссылка на сайт, дата не в прошлом, мат/спам/CAPS, дубли;
  2) проверка сайта: ссылка открывается и на странице есть слова из названия;
  3) ИИ-модератор (Claude): реальность события, тон, исправление опечаток, переводы kk/en, отрасли;
  4) решение: одобрить / на модерацию / отклонить. Админ может решить сам — колонка «Решение»
     в таблице (да / нет). Очередь и причины — data/moderation.json (только в приватном репозитории).
Одобренные события попадают в базу с пометкой «Добавлено пользователем · проверено».
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run  # noqa: E402

ROOT = run.ROOT
DB, QUEUE = ROOT / "data/events.json", ROOT / "data/moderation.json"
BAD = re.compile(r"х[уy][йеёяю]|п[иi]зд|(^|[^а-яё])[её]б[аоуиеыл]|бля|(^|[^а-яё])сук[аи]|муда[кч]|пид[оа]р|гандон|залуп|шлюх|дроч|"
                 r"f+u+c+k|(^|\W)shit|bitch|cunt|asshole|(^|[^а-яәіңғүұқөһ])сік|сігейін|қотақ|шешеңді", re.I)
COLS = {  # ключевые слова заголовков колонок Google Формы → поле
    "title": ["назван", "атау", "title"], "type": ["формат", "тип", "type"], "start": ["начал", "басталу", "start"],
    "end": ["оконч", "аяқталу", "end"], "city": ["город", "қала", "city"], "country": ["стран", "ел", "country"],
    "venue": ["площад", "место", "орны", "venue"], "organizer": ["организатор", "ұйымдастыр", "organizer"],
    "site": ["сайт", "ссылк", "site", "url"], "contacts": ["контакт", "байланыс", "contact"],
    "description": ["описан", "сипаттама", "description"], "submitter": ["ваше имя", "атыңыз", "your name", "submitter"],
    "decision": ["решение", "шешім", "decision"],
}
YES, NO = {"да", "иә", "yes", "одобрить", "approve", "+"}, {"нет", "жоқ", "no", "отклонить", "reject", "-"}


def map_row(header, row):
    out = {}
    for i, h in enumerate(header):
        hl = h.lower()
        for k, keys in COLS.items():
            if k not in out and any(x in hl for x in keys):
                out[k] = (row[i] if i < len(row) else "").strip()
                break
    return out


def to_iso(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def rule_check(r, events, today):
    issues = []
    for k in ("title", "start", "city", "organizer"):
        if not r.get(k):
            issues.append(f"нет поля «{k}»")
    if not r.get("site") and not r.get("contacts"):
        issues.append("нет поля «ссылка или контакты»")
    text = " ".join(r.get(k, "") for k in ("title", "description", "venue", "organizer", "city", "contacts"))
    if BAD.search(text):
        issues.append("нецензурная лексика")
    if len(re.findall(r"https?://", text)) > 3:
        issues.append("много ссылок (спам)")
    letters = re.sub(r"[^A-Za-zА-Яа-яЁёӘәІіҢңҒғҮүҰұҚқӨөҺһ]", "", r.get("title", ""))
    if len(letters) > 12 and letters == letters.upper():
        issues.append("заголовок заглавными")
    if r.get("site") and not re.match(r"^https?://[^\s.]+\.\S{2,}", r["site"]):
        issues.append("некорректная ссылка")
    if r.get("start") and r["start"] < today:
        issues.append("дата в прошлом")
    if r.get("start") and any(run.same({"title": r["title"], "start": r["start"]}, e) for e in events):
        issues.append("дубль события в базе")
    return issues


SOCIAL = re.compile(r"(t\.me|telegram|instagram|facebook|fb\.com|wa\.me|whatsapp|zoom\.us|meet\.google|teams\.microsoft|linkedin|forms\.gle|docs\.google|youtube|tiktok|vk\.com|timepad|eventbrite|lu\.ma)", re.I)


def site_check(r):
    """(открылся, название найдено, пояснение). Нет ссылки или соцсеть, которую нельзя прочитать, — не ошибка."""
    if not r.get("site"):
        return False, False, "без ссылки — проверка по контактам"
    url = r["site"]
    m = re.match(r"https?://t\.me/(?!s/)([\w_]+)", url)
    if m:  # публичный Telegram-канал читаем через t.me/s
        try:
            text = run.telegram_text(m.group(1)).lower()
            words = [w for w in run.norm(r["title"]).split() if len(w) >= 4]
            match = any(w in text for w in words) if words else False
            return True, match, "" if match else "в канале не найдено название события"
        except Exception:
            pass
    try:
        text = run.html_to_text(run.fetch(r["site"])).lower()
    except Exception as e:
        if SOCIAL.search(url):
            return False, False, "ссылка на соцсеть/регистрацию — содержимое закрыто для робота"
        return False, False, f"сайт не открывается: {str(e)[:80]}"
    words = [w for w in run.norm(r["title"]).split() if len(w) >= 4]
    match = any(w in text for w in words) if words else False
    return True, match, "" if match else "на сайте не найдено название события"


def ai_check(r, site_text_ok, match):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    prompt = (f"Ты — модератор сервиса «Жаршы» (деловые мероприятия Казахстана). Сегодня {dt.date.today()}. "
              "Проверь пользовательскую заявку. Отклоняй рекламу товаров, мошенничество, политическую агитацию, оскорбления, "
              "бессмыслицу и частные вечеринки. Деловые, государственные, научные и отраслевые события — допустимы, "
              "в том числе вебинары, митапы и встречи сообществ без официального сайта (со ссылкой на Telegram, Instagram, Zoom "
              "или только с контактами) — их одобряй, если заявка выглядит правдоподобно и не похожа на продажу. "
              f"Сайт открывается: {site_text_ok}; название найдено на сайте: {match}.\n"
              "Верни ТОЛЬКО JSON: {\"verdict\": \"approve|review|reject\", \"reasons\": [str], "
              "\"fixed\": {\"title\": str, \"description\": str (1–2 предложения), \"city\": str, \"venue\": str, \"organizer\": str}, "
              "\"type\": one of forum|exhibition|conference|congress|summit|webinar|networking|competition|official|visit, "
              "\"level\": international|republican|regional|industry, \"industries\": [до 3 из списка " + json.dumps(run.INDUSTRIES, ensure_ascii=False) + "], "
              "\"i18n\": {\"kk\": {\"title\": str, \"description\": str}, \"en\": {\"title\": str, \"description\": str}}}\n\nЗаявка: "
              + json.dumps({k: r.get(k, "") for k in COLS if k not in ("submitter", "decision")}, ensure_ascii=False))
    msg = anthropic.Anthropic().messages.create(model=run.MODEL, max_tokens=1500, messages=[{"role": "user", "content": prompt}])
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else None


def build_event(r, ai):
    fx = (ai or {}).get("fixed") or {}
    contacts = r.get("contacts", "")
    email = (re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", contacts) or [""])[0]
    phone = (re.search(r"\+?\d[\d\s()-]{8,}\d", contacts) or [""])[0]
    ev = {"title": fx.get("title") or r["title"], "type": (ai or {}).get("type") or r.get("type") or "conference",
          "level": (ai or {}).get("level") or "industry", "edition": None,
          "industries": [x for x in (ai or {}).get("industries", []) if x in run.INDUSTRIES][:3],
          "country": r.get("country") or "Казахстан", "city": run.CITIES.get((fx.get("city") or r["city"]).lower(), fx.get("city") or r["city"]),
          "venue": fx.get("venue") or r.get("venue", ""), "start": r["start"], "end": r.get("end") or r["start"],
          "organizer": fx.get("organizer") or r["organizer"], "phone": phone, "email": email, "site": r.get("site", ""),
          "description": fx.get("description") or r.get("description", "")[:600],
          "source_url": r.get("site", ""), "source_name": "Добавлено пользователем", "user_submitted": True,
          "i18n": (ai or {}).get("i18n") or {}}
    if ev["type"] not in run.TYPES | {"official", "visit"}:
        ev["type"] = "conference"
    ev["id"] = run.make_id(ev)
    return ev


def main():
    url = os.environ.get("SUBMISSIONS_CSV")
    if not url:
        print("· SUBMISSIONS_CSV не задан — приём заявок не подключён")
        return 0
    today = dt.datetime.now(run.TZ).date().isoformat()
    db = json.loads(DB.read_text(encoding="utf-8"))
    q = json.loads(QUEUE.read_text(encoding="utf-8")) if QUEUE.exists() else {"items": {}}
    rows = list(csv.reader(io.StringIO(run.fetch(url))))
    if not rows:
        return 0
    header, added = rows[0], 0
    for row in rows[1:]:
        r = map_row(header, row)
        r["start"], r["end"] = to_iso(r.get("start")), to_iso(r.get("end"))
        key = hashlib.sha1("|".join(row[1:]).encode()).hexdigest()[:12]  # без отметки времени
        item = q["items"].get(key, {"status": "new"})
        decision = (r.get("decision") or "").strip().lower()
        if item["status"] in ("approved", "rejected") and not decision:
            continue
        issues = rule_check(r, db["events"], today)
        hard = [i for i in issues if i in ("нецензурная лексика", "много ссылок (спам)", "дата в прошлом") or i.startswith("нет поля")]
        ai, ok, match, why, community = None, False, False, "", False
        if not hard:
            ok, match, why = site_check(r)
            try:
                ai = ai_check(r, ok, match)
            except Exception as e:
                why += f"; ИИ: {e}"
        if decision in NO or hard or (ai or {}).get("verdict") == "reject":
            status = "rejected"
        elif decision in YES or ((ai or {}).get("verdict") == "approve" and ok and match and not issues):
            status = "approved"
        elif ((ai or {}).get("verdict") == "approve" and not issues
              and (ai or {}).get("level") in ("regional", "industry")
              and (ai or {}).get("type") not in ("official", "visit", "summit")):
            status, community = "approved", True   # вебинар/митап без официального сайта — публикуем с пометкой
        else:
            status = "review"
        if "дубль события в базе" in issues and status != "rejected":
            status = "duplicate"
        if status == "review" and ai is None and not hard:
            why = (why + "; " if why else "") + "ИИ-проверка недоступна — нужна ручная проверка"
        reasons = issues + ([why] if why else []) + ((ai or {}).get("reasons") or [])
        if status == "approved":
            ev = build_event(r, ai)
            if community and decision not in YES:
                ev["verified"] = False
            ev["found_at"] = dt.datetime.now(run.TZ).isoformat(timespec="minutes")
            db["events"].append(ev)
            added += 1
        q["items"][key] = {"status": status, "title": r.get("title", ""), "start": r.get("start", ""), "site": r.get("site", ""),
                           "reasons": reasons[:8], "checked_at": dt.datetime.now(run.TZ).isoformat(timespec="minutes")}
        print(f"  {status:9} {r.get('title', '')[:60]} — {'; '.join(reasons[:3])}")
    db["events"].sort(key=lambda e: (e["start"], e["title"]))
    DB.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Модерация: добавлено {added}, на проверке {sum(v['status'] == 'review' for v in q['items'].values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

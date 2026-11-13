#!/usr/bin/env python3
"""Паук Жаршы: обходит источники, вытаскивает мероприятия через Claude, склеивает дубликаты,
пишет data/events.json. GitHub Actions: каждый час Telegram, раз в 3 часа все источники (см. .github/workflows/spider.yml).

Локально:  ANTHROPIC_API_KEY=... python spider/run.py
Без ключа паук только скачивает страницы и ничего не меняет в базе.
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "events.json"
SOURCES = Path(__file__).resolve().parent / "sources.yaml"
TZ = dt.timezone(dt.timedelta(hours=5))  # Asia/Almaty
MODEL = os.environ.get("ZHARSHY_MODEL", "claude-haiku-5-5")
MAX_CHARS = 18000          # сколько текста страницы отдаём модели за раз
UA = "Mozilla/5.0 (compatible; ZharshyBot/0.1; +https://github.com/ArmaniMachinena)"

TYPES = {"forum", "exhibition", "conference", "congress", "summit", "webinar", "networking", "competition", "official", "visit"}
LEVELS = {"international", "republican", "regional", "industry"}
INDUSTRIES = [
    "Энергетика", "Водород", "Нефть и газ", "Горнодобыча и металлургия", "Строительство",
    "Агро и пищепром", "Упаковка", "HoReCa", "Ритейл и FMCG", "Туризм", "Медицина",
    "IT и цифровизация", "Финансы", "Экология и ESG", "Транспорт и логистика",
    "Охрана труда", "Охота и рыбалка", "Дом и интерьер", "Образование", "Госуправление",
    "Машиностроение", "Водное хозяйство", "Космос",
]
CITIES = {  # приведение написаний к одному виду
    "astana": "Астана", "нур-султан": "Астана", "almaty": "Алматы", "алма-ата": "Алматы",
    "shymkent": "Шымкент", "atyrau": "Атырау", "aktau": "Актау", "karaganda": "Караганда",
    "qaraghandy": "Караганда", "aktobe": "Актобе", "pavlodar": "Павлодар", "oskemen": "Усть-Каменогорск",
    "online": "Онлайн", "онлайн": "Онлайн",
}

PROMPT = """Ты — сборщик анонсов деловых мероприятий в Казахстане для сервиса «Жаршы».
Сегодня {today}. Ниже текст страницы «{source}» ({url}).

Найди в нём ВСЕ анонсы будущих (или идущих сейчас) мероприятий, которые проходят в Казахстане,
онлайн для казахстанской аудитории или за рубежом, но важны для казахстанского бизнеса (СНГ, Каспий, отраслевые мировые события): форумы, выставки, конференции, конгрессы, саммиты, вебинары.
Включай и государственную повестку: официальные визиты Президента и Премьер-министра (type "visit"), саммиты,
заседания межгосударственных советов, государственные праздники и общенациональные мероприятия (type "official").
Пропусти закрытые служебные совещания и заседания (Совбез, оперативные совещания, заседания полпредов) — нужны только публичные события,
саммиты и официальные визиты. Пропусти прошедшие события, концерты, спорт, развлечения и новости без конкретной даты.
Если в названии указан год (например «ЕСЭ-2026»), даты должны быть в этом году.

Верни ТОЛЬКО JSON-массив (может быть пустым []). Каждый элемент:
{{"title": str, "type": one of {types}, "level": one of {levels},
  "edition": int (номер проведения, если указан: 21; иначе null),
  "industries": [1-3 значения из {industries}],
  "city": str (по-русски, "Онлайн" для вебинаров), "venue": str,
  "start": "YYYY-MM-DD", "end": "YYYY-MM-DD",
  "country": str (по-русски: "Казахстан", "Узбекистан"…),
  "organizer": str, "phone": str, "email": str, "site": str (URL),
  "program_url": str (ссылка на программу/agenda, если есть, иначе ""),
  "speakers": [{{"name": str, "role": str, "company": str}}] (спикеры/эксперты, если перечислены; иначе []),
  "description": str (1–2 предложения своими словами, по-русски),
  "i18n": {{"kk": {{"title": str, "description": str, "venue": str}},
           "en": {{"title": str, "description": str, "venue": str}}}}
  — переводы на казахский (грамотный литературный язык) и английский; бренды (HOREX, KIOGE) не переводи}}
Не выдумывай: неизвестные поля оставляй пустой строкой. Если год не указан, выбери ближайший будущий.

ТЕКСТ:
{text}"""


# ---------------------------------------------------------------- загрузка
def fetch(url: str) -> str:
    import requests
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ru,kk;q=0.9,en;q=0.8"}, timeout=40)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text


def html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        t.decompose()
    text = soup.get_text("\n")
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def telegram_text(channel: str) -> str:
    """Публичный канал читается без API через веб-превью t.me/s/<канал> (последние ~20 постов)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(fetch(f"https://t.me/s/{channel}"), "html.parser")
    posts = []
    for m in soup.select(".tgme_widget_message"):
        body = m.select_one(".tgme_widget_message_text")
        when = m.select_one("time")
        link = m.get("data-post", "")
        if body:
            posts.append(f"[пост {link} от {when.get('datetime','') if when else ''}]\n{body.get_text(' ', strip=True)}")
    return "\n\n".join(posts)


# ---------------------------------------------------------------- извлечение
def extract(text: str, source: str, url: str, today: str) -> list[dict]:
    import anthropic
    client = anthropic.Anthropic()
    out: list[dict] = []
    for i in range(0, min(len(text), MAX_CHARS * 3), MAX_CHARS):
        chunk = text[i:i + MAX_CHARS]
        msg = client.messages.create(
            model=MODEL, max_tokens=4000,
            messages=[{"role": "user", "content": PROMPT.format(
                today=today, source=source, url=url, text=chunk,
                types=sorted(TYPES), levels=sorted(LEVELS), industries=INDUSTRIES)}],
        )
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        m = re.search(r"\[.*\]", raw, re.S)
        if not m:
            continue
        try:
            items = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        for it in items if isinstance(items, list) else []:
            ev = clean(it, source, url)
            if ev:
                out.append(ev)
    return out


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean(it: dict, source: str, url: str) -> dict | None:
    if not isinstance(it, dict):
        return None
    title = str(it.get("title", "")).strip()
    start = str(it.get("start", "")).strip()
    end = str(it.get("end", "") or start).strip()
    if not title or not DATE_RE.match(start):
        return None
    if not DATE_RE.match(end) or end < start:
        end = start
    ty = re.search(r"\b(20\d{2})\b", title)
    if ty and start[:4] != ty.group(1):          # год в названии важнее угаданного
        start, end = ty.group(1) + start[4:], ty.group(1) + end[4:]
    if end < dt.datetime.now(TZ).date().isoformat():   # прошедшие не добавляем
        return None
    city = str(it.get("city", "")).strip()
    city = CITIES.get(city.lower(), city) or "уточняется"
    inds = [x for x in it.get("industries", []) if x in INDUSTRIES][:3]
    try:
        ed = int(re.match(r"\d+", str(it.get("edition") or "")).group(0))
    except (AttributeError, ValueError):
        ed = None
    i18n = {}
    for lg in ("kk", "en"):
        src = (it.get("i18n") or {}).get(lg) or {}
        if isinstance(src, dict):
            i18n[lg] = {k: str(src.get(k, ""))[:600] for k in ("title", "description", "venue") if src.get(k)}
    ev = {
        "title": title[:200],
        "type": it.get("type") if it.get("type") in TYPES else "conference",
        "level": it.get("level") if it.get("level") in LEVELS else "industry",
        "edition": ed,
        "industries": inds,
        "city": city, "venue": str(it.get("venue", ""))[:200],
        "start": start, "end": end,
        "organizer": str(it.get("organizer", ""))[:200],
        "phone": str(it.get("phone", ""))[:60], "email": str(it.get("email", ""))[:120],
        "site": str(it.get("site", ""))[:300] if str(it.get("site", "")).startswith("http") else "",
        "description": str(it.get("description", ""))[:600],
        "source_url": url, "source_name": source,
        "country": str(it.get("country") or "Казахстан")[:40],
        "program_url": str(it.get("program_url", ""))[:300] if str(it.get("program_url", "")).startswith("http") else "",
        "speakers": [{k: str(sp.get(k, ""))[:120] for k in ("name", "role", "company")}
                     for sp in (it.get("speakers") or [])[:60] if isinstance(sp, dict) and sp.get("name")],
        "i18n": i18n,
    }
    ev["id"] = make_id(ev)
    return ev


# ---------------------------------------------------------------- склейка дубликатов
def norm(title: str) -> str:
    t = title.lower().replace("ё", "е")
    t = re.sub(r"\b(19|20)\d{2}\b", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяәіңғүұқөһ",
              ["a","b","v","g","d","e","e","zh","z","i","y","k","l","m","n","o","p","r","s","t","u","f","h","ts","ch","sh","sch","","y","","e","yu","ya","a","i","n","g","u","u","k","o","h"]))


def make_id(ev: dict) -> str:
    lat = "".join(TR.get(c, c) for c in norm(ev["title"]))
    base = re.sub(r"[^a-z0-9]+", "-", lat)[:40].strip("-")
    h = hashlib.sha1((norm(ev["title"]) + ev["start"]).encode()).hexdigest()[:6]
    return f"{base or 'ev'}-{ev['start'][:4]}-{h}"


def same(a: dict, b: dict) -> bool:
    if abs((dt.date.fromisoformat(a["start"]) - dt.date.fromisoformat(b["start"])).days) > 1:
        return False
    na, nb = norm(a["title"]), norm(b["title"])
    if na in nb or nb in na:
        return True
    wa, wb = na.split(), nb.split()
    # один и тот же бренд в начале (HOREX, KIOGE, AMM…) и тот же день открытия
    if wa and wb and wa[0] == wb[0] and len(wa[0]) >= 3 and a["start"] == b["start"]:
        return True
    sa, sb = set(wa), set(wb)
    if sa and sb and len(sa & sb) / len(sa | sb) >= 0.6:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.82


def ai_twin(ev: dict, cands: list[dict]) -> dict | None:
    """Спорные случаи (тот же день и город, разные названия) решает Claude: одно ли это событие."""
    if not cands or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    lst = "\n".join(f"{i}. {c['title']} | {c.get('organizer','')} | {c.get('venue','')}" for i, c in enumerate(cands))
    q = (f"Новое событие: {ev['title']} | {ev.get('organizer','')} | {ev.get('venue','')} | {ev['start']} {ev['city']}\n"
         f"Уже в базе (тот же день и город):\n{lst}\n"
         "Это то же самое событие, что одно из списка (возможно, другое название или язык)? "
         "Совместные выставки под одной крышей — разные события. Ответь только номером или словом нет.")
    try:
        r = anthropic.Anthropic().messages.create(model=MODEL, max_tokens=10, messages=[{"role": "user", "content": q}])
        a = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip().lower()
        m = re.match(r"\d+", a)
        return cands[int(m.group(0))] if m and int(m.group(0)) < len(cands) else None
    except Exception:
        return None


def merge(existing: list[dict], found: list[dict]) -> tuple[list[dict], int]:
    added = 0
    for ev in found:
        twin = next((e for e in existing if same(e, ev)), None)
        if twin is None:
            cands = [e for e in existing if abs((dt.date.fromisoformat(e["start"]) - dt.date.fromisoformat(ev["start"])).days) <= 1
                     and (e.get("city") == ev.get("city") or "уточняется" in (e.get("city"), ev.get("city")))]
            twin = ai_twin(ev, cands[:12])
        if twin is None:
            ev["found_at"] = dt.datetime.now(TZ).isoformat(timespec="minutes")
            existing.append(ev)
            added += 1
            continue
        for k, v in ev.items():   # дополняем пустые поля, ничего не затираем
            if k in ("id", "source_url", "source_name"):
                continue
            if v and not twin.get(k):
                twin[k] = v
        srcs = twin.setdefault("also_in", [])
        if ev["source_url"] != twin.get("source_url") and ev["source_url"] not in srcs:
            srcs.append(ev["source_url"])
    return existing, added


def prune(events: list[dict], today: str, keep_days: int = 400) -> list[dict]:
    """Архив держим ~13 месяцев: по нему видно, когда ждать следующий выпуск ежегодного события."""
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=keep_days)).isoformat()
    return [e for e in events if (e.get("end") or e["start"]) >= cutoff]


def fingerprint(text: str) -> str:
    """Отпечаток содержания страницы без цифр-счётчиков («5 минут назад», просмотры) и пробелов."""
    t = re.sub(r"\d+", "", text.lower())
    t = re.sub(r"\s+", " ", t)
    return hashlib.sha1(t.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- main
def main() -> int:
    import yaml
    today = dt.datetime.now(TZ).date().isoformat()
    cfg = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))
    db = json.loads(DB.read_text(encoding="utf-8")) if DB.exists() else {"events": []}
    have_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not have_key:
        print("! ANTHROPIC_API_KEY не задан — только проверяю доступность источников")

    jobs = [(s["name"], s["url"], "web") for s in cfg.get("web", [])]
    jobs += [(s["name"], s["url"], "organizers") for s in cfg.get("organizers", [])]
    jobs += [(s["name"], s["url"], "web") for s in cfg.get("more", [])]
    jobs += [(s["name"], s["url"], "state") for s in cfg.get("state", [])]
    jobs += [(s["name"], s["url"], "global") for s in cfg.get("global", [])]
    jobs += [(s["name"], s["url"], "sheets") for s in cfg.get("sheets", [])]
    jobs += [(f"Telegram @{c}", f"https://t.me/s/{c}", "telegram") for c in cfg.get("telegram", [])]

    # Быстрый проход (каждый час): только быстро меняющиеся источники. Полный — раз в 3 часа.
    mode = os.environ.get("ZHARSHY_MODE", "full")
    if mode == "fast":
        jobs = [j for j in jobs if j[2] in ("telegram", "sheets")]
    print(f"Режим: {mode}, источников: {len(jobs)}")

    # Отпечатки страниц: Claude читает только то, что изменилось с прошлого раза (не чаще раза в сутки — принудительно)
    cache_path = ROOT / "data" / "page_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    status_path = ROOT / "data" / "sources_status.json"
    old_status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    prev = {x.get("url"): x for x in old_status.get("sources", [])}
    now = dt.datetime.now(TZ)
    skipped = 0

    found: list[dict] = []
    status: list[dict] = []
    for name, url, kind in jobs:
        st = {"name": name, "url": url, "group": kind, "ok": False, "chars": 0, "events": None, "error": ""}
        status.append(st)
        try:
            text = telegram_text(url.rsplit("/", 1)[-1]) if kind == "telegram" else html_to_text(fetch(url))
            st["ok"], st["chars"] = True, len(text)
        except Exception as e:  # один упавший источник не останавливает остальные
            st["error"] = str(e)[:160]
            print(f"  ✗ {name}: {e}")
            continue
        if not have_key:
            print(f"  ✓ {name}: {len(text)} симв.")
            continue
        digest = fingerprint(text)
        c = cache.get(url) or {}
        fresh = c.get("t") and (now - dt.datetime.fromisoformat(c["t"])) < dt.timedelta(hours=24)
        if c.get("h") == digest and fresh:
            st["events"] = (prev.get(url) or {}).get("events")
            st["unchanged"] = True
            skipped += 1
            print(f"  = {name}: без изменений")
            continue
        try:
            evs = extract(text, name, url, today)
            cache[url] = {"h": digest, "t": now.isoformat(timespec="minutes")}
            st["events"] = len(evs)
            print(f"  ✓ {name}: {len(evs)} событий")
            found += evs
        except Exception as e:
            st["error"] = ("ИИ: " + str(e))[:160]
            print(f"  ✗ {name} (ИИ): {e}")

    # WhatsApp-группы через GREEN-API
    wa = {"name": "WhatsApp (GREEN-API)", "url": "https://green-api.com", "group": "whatsapp", "ok": False, "chars": 0, "events": None, "error": ""}
    status.append(wa)
    try:
        import whatsapp
        if whatsapp.configured():
            wa["ok"] = True
            for name, text in whatsapp.jobs():
                if not have_key:
                    continue
                evs = extract(text, name, "", today)
                for e in evs:
                    e["source_url"] = ""          # ссылки на группу наружу не отдаём
                    e["source_name"] = "WhatsApp"   # название группы тоже не публикуем
                print(f"  ✓ {name}: {len(evs)} событий")
                found += evs
        else:
            wa["error"] = "off"
            print("  · WhatsApp не настроен (нет секретов GREEN_API_*)")
    except Exception as e:
        wa["ok"], wa["error"] = False, str(e)[:160]
        print(f"  ✗ WhatsApp: {e}")

    (ROOT / "data").mkdir(exist_ok=True)
    if mode == "fast":  # источники, которые в быстром проходе не трогали, оставляем как были
        seen = {x["url"] for x in status}
        status = [x for x in old_status.get("sources", []) if x.get("url") not in seen and x.get("group") != "whatsapp"] + status
    full_at = now.isoformat(timespec="minutes") if mode != "fast" else old_status.get("full_at", "")
    status_path.write_text(json.dumps(
        {"checked_at": now.isoformat(timespec="minutes"), "full_at": full_at, "mode": mode, "have_key": have_key, "sources": status},
        ensure_ascii=False, indent=1), encoding="utf-8")
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"Без изменений (Claude не вызывали): {skipped}")

    if not have_key:
        return 0
    events, added = merge(db.get("events", []), found)
    events = prune(events, today)
    events.sort(key=lambda e: (e["start"], e["title"]))
    db = {"updated_at": dt.datetime.now(TZ).isoformat(timespec="minutes"), "events": events}
    DB.parent.mkdir(parents=True, exist_ok=True)
    DB.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Готово: найдено {len(found)}, новых {added}, всего в базе {len(events)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Жаршы — «Пресса о событии»: публикации СМИ о мероприятиях, которые идут сейчас или только что прошли.

Работает БЕЗ ИИ и без токенов:
  • для каждого события в окне [начало − 2 дня … окончание + 3 дня] берём публичную RSS-ленту
    поиска Google News по названию (на русском, а для международных — ещё и на английском);
  • отбираем публикации по ключевым словам названия (бренд, номер проведения, отличительные слова)
    и по дате (не раньше чем за 5 дней до начала);
  • храним только заголовок, издание, дату и ссылку на оригинал — тексты статей не копируем.
Результат — поле "press" у события в data/events.json (до 30 публикаций, новые сверху).
"""
from __future__ import annotations

import datetime as dt
import email.utils
import json
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run  # noqa: E402

DB = run.DB
UA = run.UA
STOP = set("""
и в во на по с со для о об от до из за к ко the of and for in on at to a an
форум форума конференция конференции выставка выставки международный международная международного
республиканский казахстан казахстана казахстанский kazakhstan международ forum conference expo exhibition
international summit саммит конгресс конгресса congress год года жыл
""".split())


def keywords(e: dict) -> tuple[list[str], list[str]]:
    """(обязательные «сильные» слова, обычные отличительные слова) из названия события."""
    title = re.sub(r"\b20\d\d\b", " ", e["title"])
    title = re.sub(r"[«»\"“”()—–:,.!?/]", " ", title)
    words = [w for w in title.split() if w]
    strong, normal = [], []
    for w in words:
        lw = w.lower()
        if lw in STOP or len(lw) < 3:
            continue
        if re.fullmatch(r"[IVXLC]{2,}", w) or re.search(r"\d", w) or (w.isupper() and len(w) >= 3) \
                or re.search(r"[A-Za-z]", w) and w[0].isupper():
            strong.append(lw)
        else:
            normal.append(lw[:6])        # основа слова: «финансистов» → «финанс»
    return strong, normal


TYPES = ["конгрес", "форум", "выстав", "конфер", "саммит", "фестив", "congress", "forum", "expo", "summit", "festival",
         "конференц", "көрме", "съезд", "week", "недел"]


def fold(t: str) -> str:
    return t.lower().replace("ñ", "n").replace("ё", "е")


def query(e: dict) -> str:
    t = re.sub(r"\b20\d\d\b", "", e["title"])
    t = re.split(r"\s[—–-]\s|:|«|\(", t)[0]      # до тире/двоеточия/кавычек — короткое имя события
    t = re.sub(r"[«»\"“”]", "", t).strip()
    words = t.split()
    return f"\"{t}\"" if len(words) <= 2 else " ".join(words[:5])


def queries(e: dict) -> list[str]:
    """Основной запрос + запасной без римских цифр («XIV Конгресс финансистов» → «Конгресс финансистов»)."""
    q1 = query(e)
    t = re.sub(r"\b20\d\d\b|\b[IVXLC]{2,}\b|\d+-?(й|я|е)?\b", " ", e["title"])
    t = re.split(r"\s[—–-]\s|:|«|\(", t)[0]
    w = re.sub(r"[«»\"“”]", "", t).split()
    q2 = " ".join(w[:3])
    return [q1] + ([q2] if len(w) >= 2 and q2.strip('"') != q1.strip('"') else [])


def fetch_rss(q: str, lang: str) -> list[dict]:
    import requests
    hl, gl, ceid = {"ru": ("ru", "KZ", "KZ:ru"), "en": ("en", "US", "US:en")}[lang]
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": hl, "gl": gl, "ceid": ceid})
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return parse_rss(r.text)


def parse_rss(xml: str) -> list[dict]:
    out = []
    root = ET.fromstring(xml)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        try:
            d = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "")
            when = d.astimezone(run.TZ).isoformat(timespec="minutes")
        except Exception:
            when = ""
        out.append({"title": title, "source": source, "url": (it.findtext("link") or "").strip(), "date": when})
    return out


def relevant(item: dict, strong: list[str], normal: list[str], since: str, etypes: list[str] | None = None) -> bool:
    if item["date"] and item["date"][:10] < since:
        return False
    t = fold(item["title"])
    hit_s = sum(1 for w in strong if fold(w) in t)
    hit_n = sum(1 for w in normal if fold(w) in t)
    if strong and hit_s >= 1 and (hit_s + hit_n) >= min(2, len(strong) + len(normal)):
        return True
    if etypes and any(ty in t for ty in etypes) and hit_n >= 1 and (hit_s >= 1 or len(normal) <= 2 or hit_n >= 2):
        return True                    # «Финансисты обсудили … на конгрессе»
    return not strong and hit_n >= min(2, len(normal))


def main() -> int:
    today = dt.datetime.now(run.TZ).date()
    db = json.loads(DB.read_text(encoding="utf-8"))
    n_ev = n_new = 0
    for e in db["events"]:
        if e.get("date_approx"):
            continue
        start = dt.date.fromisoformat(e["start"])
        end = dt.date.fromisoformat(e.get("end") or e["start"])
        if not (start - dt.timedelta(days=2) <= today <= end + dt.timedelta(days=3)):
            continue
        strong, normal = keywords(e)
        if not strong and len(normal) < 2:
            continue                       # слишком общее название — будет мусор
        since = (start - dt.timedelta(days=5)).isoformat()
        etypes = [ty for ty in TYPES if ty in fold(e["title"])]
        langs = ["ru"] + (["en"] if e.get("level") == "international" or re.search(r"[A-Za-z]{4}", e["title"]) else [])
        found = []
        for lang in langs:
            for q in queries(e):
                try:
                    found += [x for x in fetch_rss(q, lang) if relevant(x, strong, normal, since, etypes)]
                except Exception as ex:
                    print(f"  ✗ {e['title'][:50]} ({lang}): {ex}")
                time.sleep(1.5)            # бережно к сервису
        have = {p["url"] for p in e.get("press", [])} | {p["title"].lower() for p in e.get("press", [])}
        fresh = [x for x in found if x["url"] not in have and x["title"].lower() not in have]
        seen = set()
        fresh = [x for x in fresh if not (x["title"].lower() in seen or seen.add(x["title"].lower()))]
        if fresh:
            e["press"] = sorted(fresh + e.get("press", []), key=lambda x: x["date"], reverse=True)[:30]
            n_new += len(fresh)
        n_ev += 1
        print(f"  📰 {e['title'][:60]}: +{len(fresh)} (всего {len(e.get('press', []))})")
    DB.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Пресса: событий в окне {n_ev}, новых публикаций {n_new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

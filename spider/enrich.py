#!/usr/bin/env python3
"""Жаршы — дозаполнение карточек: время начала, площадка, программа, регистрация.

Для предстоящих событий (до 60 дней), где чего-то не хватает и есть сайт, раз в 7 дней открываем сайт
и просим Claude (Haiku) достать ТОЛЬКО недостающие поля. Заполняем лишь пустое — найденное ранее не трогаем.
Экономно: не больше 12 событий за проход, текст сайта обрезается до 12 тыс. символов.
"""
from __future__ import annotations

import datetime as dt
import time as _t
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run  # noqa: E402

MAX_PER_RUN = 8


def missing(e: dict) -> list[str]:
    m = []
    if not e.get("time"):
        m.append("time")
    if not e.get("venue") or e["venue"] == "уточняется":
        m.append("venue")
    if not e.get("program_url"):
        m.append("program_url")
    if not e.get("registration_url"):
        m.append("registration_url")
    return m


def ask(e: dict, text: str, need: list[str]) -> dict:
    import anthropic
    prompt = (f"Мероприятие: «{e['title']}», {e['start']}–{e.get('end') or e['start']}, {e.get('city','')}.\n"
              f"Ниже текст его сайта. Найди ТОЛЬКО то, что прямо указано на странице (не догадывайся):\n"
              "time — время начала/открытия в первый день, формат HH:MM;\n"
              "venue — площадка и адрес;\n"
              "program_url — ссылка на программу/деловую программу;\n"
              "registration_url — ссылка на регистрацию/билеты.\n"
              f"Нужны поля: {', '.join(need)}. Верни JSON с этими ключами, null — если на странице нет.\n\n"
              + text[:12000])
    msg = anthropic.Anthropic().messages.create(model=run.MODEL, max_tokens=300, messages=[{"role": "user", "content": prompt}])
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else {}


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Дозаполнение: нет ключа — пропускаю")
        return 0
    today = dt.datetime.now(run.TZ).date()
    db = json.loads(run.DB.read_text(encoding="utf-8"))
    n = 0
    t0 = _t.time()
    for e in sorted(db["events"], key=lambda x: x["start"]):
        if n >= MAX_PER_RUN or _t.time() - t0 > 180:   # не больше 3 минут за проход
            break
        start = dt.date.fromisoformat(e["start"])
        if not (today <= start <= today + dt.timedelta(days=60)) or not e.get("site"):
            continue
        need = missing(e)
        at = e.get("enrich_at")
        if not need or (at and (today - dt.date.fromisoformat(at[:10])).days < 7):
            continue
        e["enrich_at"] = today.isoformat()
        try:
            text = run.html_to_text(run.fetch(e["site"]))
            got = ask(e, text, need)
        except Exception as ex:
            print(f"  ✗ {e['title'][:50]}: {ex}")
            continue
        n += 1
        filled = []
        t = got.get("time")
        if "time" in need and isinstance(t, str) and re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", t.strip()):
            e["time"] = t.strip().zfill(5)
            filled.append("время")
        v = got.get("venue")
        if "venue" in need and isinstance(v, str) and len(v.strip()) > 3:
            e["venue"] = v.strip()
            filled.append("площадка")
        for k, label in (("program_url", "программа"), ("registration_url", "регистрация")):
            u = got.get(k)
            if k in need and isinstance(u, str) and u.startswith("http"):
                e[k] = u.strip()
                filled.append(label)
        print(f"  🔎 {e['title'][:55]}: {', '.join(filled) or 'ничего нового'}")
    run.DB.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Дозаполнение: проверено {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Жаршы — «Где остановиться»: 10 ближайших гостиниц к площадке события.

Бесплатно и без токенов: координаты площадки — OpenStreetMap Nominatim (1 запрос/сек, по их правилам),
гостиницы вокруг — OpenStreetMap Overpass (радиус 3 км). Цены и рейтинги не берём — их дадут партнёрские
программы Booking/Trip.com, когда будут подключены. Сайт показывает ещё и кнопки поиска на Booking, Trip.com, 2ГИС.

Пишет в событие: geo {lat, lon, precise}, hotels [{name, stars, kind, dist}], hotels_at.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run  # noqa: E402

UA = "ZharshyBot/1.0 (events calendar of Kazakhstan; https://github.com/ArmaniMachinena)"
MAX_PER_RUN = 15
KIND = {"hotel": "hotel", "guest_house": "guest", "hostel": "hostel", "apartment": "apart", "motel": "motel"}


def get(url: str, **params) -> dict | list:
    import requests
    r = requests.get(url, params=params, headers={"User-Agent": UA, "Accept-Language": "ru,en"}, timeout=60)
    r.raise_for_status()
    return r.json()


def geocode(e: dict) -> dict | None:
    venue = (e.get("venue") or "").strip()
    city, country = e.get("city") or "", e.get("country") or "Казахстан"
    tries = ([(f"{venue}, {city}, {country}", True)] if venue and venue != "уточняется" else []) + [(f"{city}, {country}", False)]
    for q, precise in tries:
        time.sleep(1.1)                       # правило Nominatim: не чаще 1 запроса в секунду
        res = get("https://nominatim.openstreetmap.org/search", q=q, format="json", limit=1)
        if res:
            return {"lat": round(float(res[0]["lat"]), 6), "lon": round(float(res[0]["lon"]), 6), "precise": precise}
    return None


def dist(a, b) -> int:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return int(12_742_000 * math.asin(math.sqrt(h)))


def hotels_near(lat: float, lon: float, radius: int = 3000) -> list[dict]:
    q = f"""[out:json][timeout:40];
(nwr["tourism"~"^(hotel|guest_house|hostel|apartment|motel)$"]["name"](around:{radius},{lat},{lon}););
out center tags 80;"""
    import requests
    r = requests.post("https://overpass-api.de/api/interpreter", data={"data": q}, headers={"User-Agent": UA}, timeout=90)
    r.raise_for_status()
    out = []
    for el in r.json().get("elements", []):
        tg = el.get("tags", {})
        la = el.get("lat") or (el.get("center") or {}).get("lat")
        lo = el.get("lon") or (el.get("center") or {}).get("lon")
        if not la or not tg.get("name"):
            continue
        stars = tg.get("stars", "")
        out.append({"name": tg.get("name:ru") or tg["name"], "stars": int(stars[0]) if stars[:1].isdigit() else None,
                    "kind": KIND.get(tg.get("tourism"), "hotel"), "dist": dist((lat, lon), (la, lo))})
    # гостиницы — выше гостевых домов и хостелов, при равном типе — ближе
    rank = {"hotel": 0, "apart": 1, "guest": 2, "motel": 2, "hostel": 3}
    seen, uniq = set(), []
    for h in sorted(out, key=lambda h: (h["dist"] // 700, rank[h["kind"]], h["dist"])):
        k = h["name"].lower()
        if k not in seen:
            seen.add(k)
            uniq.append(h)
    return uniq[:10]


def main() -> int:
    today = dt.datetime.now(run.TZ).date()
    db = json.loads(run.DB.read_text(encoding="utf-8"))
    done = 0
    for e in sorted(db["events"], key=lambda x: x["start"]):
        if done >= MAX_PER_RUN:
            break
        if e.get("city") in (None, "", "Онлайн", "уточняется", "Вся страна") or e.get("type") == "webinar":
            continue
        start, end = dt.date.fromisoformat(e["start"]), dt.date.fromisoformat(e.get("end") or e["start"])
        if end < today or start > today + dt.timedelta(days=150):
            continue
        at = e.get("hotels_at")
        if at and (today - dt.date.fromisoformat(at[:10])).days < 30:
            continue
        try:
            g = geocode(e)
            if not g:
                e["hotels_at"] = today.isoformat()
                continue
            e["geo"] = g
            e["hotels"] = hotels_near(g["lat"], g["lon"])
            e["hotels_at"] = today.isoformat()
            done += 1
            print(f"  🏨 {e['title'][:55]}: {len(e['hotels'])} (точно: {g['precise']})")
            time.sleep(2)
        except Exception as ex:
            print(f"  ✗ {e['title'][:55]}: {ex}")
    run.DB.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Отели: обновлено событий {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

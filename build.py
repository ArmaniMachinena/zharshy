"""Собирает dist/index.html — одну страницу со встроенной базой (для просмотра без сервера)."""
import json, pathlib
root = pathlib.Path(__file__).parent
data = json.loads((root/"data/events.json").read_text(encoding="utf-8"))
st = root/"data/sources_status.json"
status = json.loads(st.read_text(encoding="utf-8")) if st.exists() else None
html = (root/"index.html").read_text(encoding="utf-8").replace("__SEED__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")).replace("__STATUS__", json.dumps(status, ensure_ascii=False).replace("</", "<\\/"))
(root/"dist").mkdir(exist_ok=True); (root/"dist/index.html").write_text(html, encoding="utf-8")
print("dist/index.html", len(html))

# --- Подписной календарь zharshy.ics (предстоящие + последние 30 дней) ---
import datetime as _dt
def _esc(x): return str(x or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
def _fold(line):
    b = line.encode("utf-8"); out = []
    while len(b) > 74:
        cut = 74
        while (b[cut] & 0xC0) == 0x80: cut -= 1
        out.append(b[:cut].decode("utf-8")); b = b[cut:]
    out.append(b.decode("utf-8")); return "\r\n ".join(out)
today = _dt.date.today(); cutoff = (today - _dt.timedelta(days=30)).isoformat()
stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Zharshy//KZ events//RU", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
     "X-WR-CALNAME:Жаршы — мероприятия Казахстана", "X-WR-TIMEZONE:Asia/Almaty", "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
for e in data["events"]:
    end = e.get("end") or e["start"]
    if end < cutoff: continue
    dtend = (_dt.date.fromisoformat(end) + _dt.timedelta(days=1)).strftime("%Y%m%d")
    loc = ", ".join(x for x in [e.get("venue"), e.get("city"), e.get("country") if e.get("country") not in (None, "Казахстан") else ""] if x and x != "уточняется")
    desc = "\n".join(x for x in [e.get("description"), e.get("organizer") and "Организатор: " + e["organizer"], e.get("site")] if x)
    L += ["BEGIN:VEVENT", f"UID:{e['id']}@zharshy", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{e['start'].replace('-', '')}", f"DTEND;VALUE=DATE:{dtend}",
          _fold("SUMMARY:" + _esc(e["title"])), _fold("LOCATION:" + _esc(loc)), _fold("DESCRIPTION:" + _esc(desc))]
    if e.get("site"): L.append(_fold("URL:" + e["site"]))
    L += ["END:VEVENT"]
L.append("END:VCALENDAR")
(root/"dist/zharshy.ics").write_text("\r\n".join(L) + "\r\n", encoding="utf-8")
print("dist/zharshy.ics", sum(1 for x in L if x == "BEGIN:VEVENT"), "событий")

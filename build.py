"""Собирает dist/index.html — одну страницу со встроенной базой (для просмотра без сервера)."""
import json, pathlib
root = pathlib.Path(__file__).parent
data = json.loads((root/"data/events.json").read_text(encoding="utf-8"))
st = root/"data/sources_status.json"
status = json.loads(st.read_text(encoding="utf-8")) if st.exists() else None
html = (root/"index.html").read_text(encoding="utf-8").replace("__SEED__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")).replace("__STATUS__", json.dumps(status, ensure_ascii=False).replace("</", "<\\/"))
(root/"dist").mkdir(exist_ok=True); (root/"dist/index.html").write_text(html, encoding="utf-8")
print("dist/index.html", len(html))

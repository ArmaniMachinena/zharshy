"""WhatsApp через GREEN-API (green-api.com, Астана).

Номер Жаршы подключён к GREEN-API по QR-коду и состоит в группах с анонсами.
Каждый час паук забирает входящие сообщения за последние ~75 минут (lastIncomingMessages),
оставляет только групповые сообщения, похожие на анонс, и отдаёт их Claude на разбор.

Секреты (GitHub → Settings → Secrets → Actions):
  GREEN_API_URL    — apiUrl из консоли, например https://7105.api.greenapi.com
  GREEN_ID_INSTANCE — idInstance
  GREEN_API_TOKEN  — apiTokenInstance
Номера телефонов и имена участников никуда не сохраняются — только текст анонса.
"""
from __future__ import annotations

import os
import re

KEYWORDS = re.compile(
    r"форум|конференц|выставк|вебинар|семинар|саммит|конгресс|круглый стол|регистрац|мероприят|"
    r"көрме|іс-шара|тіркел|жиын|"
    r"forum|conference|webinar|expo|summit|congress|registration|event|zoom\.us|teams\.microsoft",
    re.I,
)


def configured() -> bool:
    return all(os.environ.get(k) for k in ("GREEN_API_URL", "GREEN_ID_INSTANCE", "GREEN_API_TOKEN"))


def message_text(m: dict) -> str:
    for k in ("textMessage", "caption"):
        if m.get(k):
            return str(m[k])
    ext = m.get("extendedTextMessage") or {}
    return str(ext.get("text") or "")


def fetch_messages(minutes: int = 75) -> list[dict]:
    import requests
    base = os.environ["GREEN_API_URL"].rstrip("/")
    url = f"{base}/waInstance{os.environ['GREEN_ID_INSTANCE']}/lastIncomingMessages/{os.environ['GREEN_API_TOKEN']}"
    r = requests.get(url, params={"minutes": minutes}, timeout=40)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def group_announcements(messages: list[dict]) -> dict[str, list[str]]:
    """{название группы: [тексты анонсов]} — только группы (@g.us) и только похожее на анонс."""
    out: dict[str, list[str]] = {}
    for m in messages:
        chat = str(m.get("chatId", ""))
        if not chat.endswith("@g.us"):
            continue
        text = message_text(m).strip()
        if len(text) < 40 or not KEYWORDS.search(text):
            continue
        name = str(m.get("chatName") or "группа")
        out.setdefault(name, []).append(text)
    return out


def jobs() -> list[tuple[str, str]]:
    """Тексты для разбора: [(имя источника, текст)]."""
    msgs = fetch_messages()
    groups = group_announcements(msgs)
    print(f"  WhatsApp: {len(msgs)} входящих, анонсов в {len(groups)} группах")
    return [(f"WhatsApp: {name}", "\n\n---\n\n".join(texts)) for name, texts in groups.items()]

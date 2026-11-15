#!/usr/bin/env python3
"""Жаршы — закрытая публичная версия сайта.

Шифрует dist/index.html паролем (AES-256-GCM, ключ из пароля через PBKDF2-SHA256, 310 000 итераций)
и пишет dist/public/index.html — страницу с полем пароля. Без пароля в публичном репозитории
лежит только шифр: ни кода, ни базы мероприятий, ни списка источников прочитать нельзя.
Расшифровка — в браузере (WebCrypto), пароль никуда не отправляется.

Пароль: переменная окружения ZHARSHY_WEB_PASSWORD или файл .web_password (только в приватном репозитории).
"""
from __future__ import annotations

import base64
import os
import secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ROOT = Path(__file__).parent
ITER = 310_000

PAGE = r"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Жаршы — закрытый доступ</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;background:linear-gradient(125deg,#004f61,#006f87 45%,#0094b3);color:#14232b}
.box{background:#fff;border-radius:18px;padding:30px 28px 24px;width:min(380px,92vw);box-shadow:0 20px 60px rgba(0,40,55,.35)}
.logo{display:flex;align-items:center;gap:10px;font-weight:800;letter-spacing:.12em;color:#006f87;font-size:18px}
h1{font-size:19px;margin:18px 0 4px}p{margin:0 0 16px;color:#5d7079;font-size:14px;line-height:1.45}
input[type=password]{width:100%;padding:12px 14px;border:1px solid #dde7ea;border-radius:10px;font-size:16px;outline:none}
input[type=password]:focus{border-color:#0094b3;box-shadow:0 0 0 3px rgba(0,148,179,.15)}
label.rm{display:flex;gap:8px;align-items:center;font-size:13px;color:#5d7079;margin:12px 0 16px}
button{width:100%;padding:12px;border:0;border-radius:10px;background:#0094b3;color:#fff;font-weight:700;font-size:15px;cursor:pointer}
button:disabled{opacity:.6}.err{color:#d6453d;font-size:13px;min-height:18px;margin-top:10px}
.lang{display:flex;gap:6px;justify-content:flex-end;margin-top:-6px}.lang b{font-size:11px;color:#9aaab0;cursor:pointer}.lang b.on{color:#006f87}
.foot{font-size:11.5px;color:#9aaab0;margin-top:14px;text-align:center}
</style></head><body>
<form class="box" id="f" autocomplete="off">
 <div class="lang"><b data-l="kk">ҚАЗ</b><b data-l="ru" class="on">РУС</b><b data-l="en">ENG</b></div>
 <div class="logo"><svg width="30" height="30" viewBox="0 0 40 40"><circle cx="20" cy="20" r="19" fill="#e3f5f9" stroke="#e8a900" stroke-width="2"/><path d="M11 17h5l9-6v18l-9-6h-5z" fill="#006f87"/><path d="M28 15c2 1.5 2 8.5 0 10M31 12c4 3 4 13 0 16" stroke="#e8a900" stroke-width="2" fill="none" stroke-linecap="round"/></svg>ЖАРШЫ</div>
 <h1 id="t1"></h1><p id="t2"></p>
 <input type="password" id="pw" autofocus>
 <label class="rm"><input type="checkbox" id="rm" checked> <span id="t3"></span></label>
 <button id="go"></button><div class="err" id="err"></div>
 <div class="foot">© 2026 Жаршы</div>
</form>
<script>
const D={salt:"__SALT__",iv:"__IV__",ct:"__CT__",it:__ITER__};
const T={ru:["Закрытое тестирование","Сайт доступен участникам тестирования. Введите пароль — его выдаёт Арман.","Запомнить на этом устройстве","Войти","Неверный пароль","Пароль"],
kk:["Жабық тестілеу","Сайт тестілеуге қатысушыларға қолжетімді. Құпиясөзді енгізіңіз — оны Арман береді.","Осы құрылғыда есте сақтау","Кіру","Құпиясөз қате","Құпиясөз"],
en:["Private testing","This site is open to testers only. Enter the password — Arman will give it to you.","Remember on this device","Sign in","Wrong password","Password"]};
let L=(navigator.language||"ru").slice(0,2);if(!T[L])L="ru";try{L=localStorage.getItem("zh-lang")||L}catch(e){}
const $=i=>document.getElementById(i);
function tr(){const t=T[L];$("t1").textContent=t[0];$("t2").textContent=t[1];$("t3").textContent=t[2];$("go").textContent=t[3];$("pw").placeholder=t[5];
 document.querySelectorAll("[data-l]").forEach(b=>b.classList.toggle("on",b.dataset.l===L))}
document.querySelectorAll("[data-l]").forEach(b=>b.onclick=()=>{L=b.dataset.l;try{localStorage.setItem("zh-lang",L)}catch(e){}tr()});tr();
const b64=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
async function keyFrom(pw){const base=await crypto.subtle.importKey("raw",new TextEncoder().encode(pw),"PBKDF2",false,["deriveBits"]);
 return new Uint8Array(await crypto.subtle.deriveBits({name:"PBKDF2",salt:b64(D.salt),iterations:D.it,hash:"SHA-256"},base,256))}
async function open_(raw,remember){const k=await crypto.subtle.importKey("raw",raw,"AES-GCM",false,["decrypt"]);
 const html=new TextDecoder().decode(await crypto.subtle.decrypt({name:"AES-GCM",iv:b64(D.iv)},k,b64(D.ct)));
 if(remember){try{localStorage.setItem(KEY,btoa(String.fromCharCode(...raw)))}catch(e){}}
 document.open();document.write(html);document.close()}
const KEY="zh-access-"+D.salt.slice(0,8);
(async()=>{try{const s=localStorage.getItem(KEY);if(s){await open_(b64(s));return}}catch(e){try{localStorage.removeItem(KEY)}catch(_){}}})();
$("f").onsubmit=async ev=>{ev.preventDefault();$("go").disabled=true;$("err").textContent="";
 const remember=$("rm").checked;let raw;
 try{raw=await keyFrom($("pw").value);await open_(raw,remember)}
 catch(e){$("err").textContent=T[L][4];$("go").disabled=false;$("pw").select()}};
</script></body></html>
"""


def main() -> int:
    pw = os.environ.get("ZHARSHY_WEB_PASSWORD") or (ROOT / ".web_password").read_text(encoding="utf-8").strip()
    html = (ROOT / "dist" / "index.html").read_bytes()
    # соль постоянная (хранится рядом с паролем) — «Запомнить на этом устройстве» переживает обновления сайта
    sp = ROOT / ".web_salt"
    if os.environ.get("ZHARSHY_WEB_SALT"):
        sp.write_text(os.environ["ZHARSHY_WEB_SALT"], encoding="utf-8")
    if not sp.exists():
        sp.write_text(base64.b64encode(secrets.token_bytes(16)).decode(), encoding="utf-8")
    salt, iv = base64.b64decode(sp.read_text(encoding="utf-8").strip()), secrets.token_bytes(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(pw.encode())
    ct = AESGCM(key).encrypt(iv, html, None)
    b = lambda x: base64.b64encode(x).decode()
    out = (PAGE.replace("__SALT__", b(salt)).replace("__IV__", b(iv)).replace("__CT__", b(ct))
           .replace("__ITER__", str(ITER)))
    dst = ROOT / "dist" / "public"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "index.html").write_text(out, encoding="utf-8")
    print(f"Зашифровано: dist/public/index.html ({len(out)//1024} КБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

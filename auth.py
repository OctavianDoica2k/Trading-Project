"""
Autentificarea cu parolă, pentru serverul accesibil de pe internet (VM + Tailscale Funnel).

Activă doar dacă .env are APP_PASSWORD. Fără ea (ex. serverul de pe PC, accesibil doar local
sau prin Tailscale), totul merge ca înainte, fără login.

După login, browserul primește un cookie semnat (valabil SESSION_DAYS zile). Semnătura folosește
o cheie secretă păstrată în session_secret.key (generată la prima pornire) plus parola: dacă
schimbi APP_PASSWORD, toate sesiunile vechi devin invalide.
"""

# =====================================================================================
#  Ce conține fișierul:
#  - Auth : verifică parola, creează / verifică cookie-ul de sesiune și limitează încercările
#           greșite (protecție împotriva ghicirii parolei).
#  - LOGIN_PAGE : pagina de login (HTML simplu, fără fișiere externe).
# =====================================================================================

# hmac / hashlib : semnătura cookie-ului și compararea sigură a parolei.
# secrets        : cheia secretă aleatoare.
import hashlib
import hmac
import html
import os
import secrets
import time
from pathlib import Path

COOKIE = "nexus_session"
SESSION_DAYS = 30
# Câte parole greșite acceptăm de la aceeași adresă IP în FAIL_WINDOW secunde, înainte de blocare.
MAX_FAILS = 10
FAIL_WINDOW = 15 * 60


class Auth:
    def __init__(self, root: Path):
        self.password = os.getenv("APP_PASSWORD", "")
        self.enabled = bool(self.password)
        self.fails: dict[str, list[float]] = {}   # IP -> momentele încercărilor greșite
        if not self.enabled:
            return
        key_path = root / "session_secret.key"
        if not key_path.exists():
            key_path.write_text(secrets.token_hex(32), encoding="ascii")
        # Parola face parte din cheie: altă parolă = alte semnături = sesiunile vechi expiră.
        self.key = (key_path.read_text(encoding="ascii").strip() + self.password).encode()

    def _sign(self, issued: str) -> str:
        return hmac.new(self.key, issued.encode(), hashlib.sha256).hexdigest()

    # Valoarea cookie-ului: momentul login-ului + semnătura lui.
    def new_session(self) -> str:
        issued = str(int(time.time()))
        return f"{issued}.{self._sign(issued)}"

    # True dacă cererea are voie (login dezactivat sau cookie valid și neexpirat).
    def ok(self, cookies) -> bool:
        if not self.enabled:
            return True
        issued, _, sig = (cookies.get(COOKIE) or "").partition(".")
        if not issued.isdigit() or not hmac.compare_digest(sig, self._sign(issued)):
            return False
        return time.time() - int(issued) < SESSION_DAYS * 86400

    # Adresa IP reală a vizitatorului: Tailscale Funnel o pune în X-Forwarded-For.
    @staticmethod
    def client_ip(request) -> str:
        fwd = request.headers.get("x-forwarded-for", "")
        return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")

    def blocked(self, ip: str) -> bool:
        now = time.time()
        recent = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
        self.fails[ip] = recent
        return len(recent) >= MAX_FAILS

    # Verifică parola; o încercare greșită se notează pentru IP-ul respectiv.
    def check(self, ip: str, password: str) -> bool:
        if hmac.compare_digest(password.encode(), self.password.encode()):
            self.fails.pop(ip, None)
            return True
        self.fails.setdefault(ip, []).append(time.time())
        return False


# Pagina de login. {error} = mesajul de eroare (deja escapat), gol la prima afișare.
LOGIN_PAGE = """<!doctype html>
<html lang="ro">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nexus – login</title>
<style>
  :root { --bg: #f4f5f7; --card: #fff; --text: #1d2330; --muted: #667085; --accent: #2962ff; --err: #d92d20; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #131722; --card: #1e222d; --text: #d1d4dc; --muted: #8a93a6; --accent: #2962ff; --err: #f97066; }
  }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg);
         color: var(--text); font: 15px/1.4 system-ui, sans-serif; }
  form { background: var(--card); padding: 28px 24px; border-radius: 10px; width: min(320px, 100vw - 32px);
         box-sizing: border-box; box-shadow: 0 4px 20px rgba(0,0,0,.12); }
  h1 { margin: 0 0 18px; font-size: 20px; }
  input, button { width: 100%; box-sizing: border-box; font: inherit; padding: 10px 12px; border-radius: 6px; }
  input { border: 1px solid var(--muted); background: transparent; color: inherit; margin-bottom: 12px; }
  button { border: 0; background: var(--accent); color: #fff; cursor: pointer; }
  .err { color: var(--err); margin: 0 0 12px; min-height: 1em; }
</style>
</head>
<body>
<form method="post" action="/login">
  <h1>Nexus Live Chart</h1>
  <p class="err">{error}</p>
  <input type="password" name="password" placeholder="Parola" autocomplete="current-password" autofocus required>
  <button type="submit">Intră</button>
</form>
</body>
</html>"""


def login_page(error: str = "") -> str:
    return LOGIN_PAGE.replace("{error}", html.escape(error))

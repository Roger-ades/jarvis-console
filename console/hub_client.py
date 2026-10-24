"""Appels HTTPS vers le hub. Le jeton ne quitte pas ce module, et le certificat est épinglé."""
from __future__ import annotations

import hashlib
import http.client
import json
import socket
import ssl
import urllib.parse
from pathlib import Path

# Le hub de l'équipe, écrit ici pour que chacun s'inscrive au premier lancement sans rien saisir
# (docs/equipe.md). Un autre hub reste possible dans Configuration → Équipe.
HUB_URL = "https://10.20.10.249:8443"
HUB_FINGERPRINT = "531a7cbbdd2e3fcb995102ee6896d8ce0c2a9a436b55729dcece601d8e02eba0"
# Seule une copie de JARVIS peut créer un compte : le hub en garde l'empreinte (hub/app.py, REGISTER_KEY_HASH).
REGISTER_KEY = "1arQQ8m8R0f_5mnfh0KOgGkFPz0MnbWVzFMtNdqodzY"


class HubError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def normalize_fingerprint(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch in "0123456789abcdef")


def check_url(url: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlparse((url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HubError("Adresse du hub attendue : https://serveur:8443")
    return parsed


class HubClient:
    def __init__(self, data_dir: Path):
        self.file = Path(data_dir) / "hub-token"

    def token(self) -> str:
        try:
            return self.file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def set_token(self, token: str):
        token = (token or "").strip()
        if len(token) < 20:
            raise HubError("Jeton trop court.")
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(token, encoding="utf-8")

    def probe(self, url: str, timeout: float = 30) -> str:
        """Empreinte SHA-256 du certificat, sans envoyer le jeton."""
        return self._exchange(url, "GET", "/health", token="", fingerprint="", want="fingerprint", timeout=timeout)

    def register(self, url: str, fingerprint: str, user_id: str, name: str, color: str) -> dict:
        """Crée le compte de ce poste et garde le jeton rendu. Rend le compte."""
        if not normalize_fingerprint(fingerprint):
            raise HubError("Empreinte du hub inconnue.", 409)
        answer = self._exchange(url, "POST", "/api/v1/register", token="", fingerprint=fingerprint,
                                body={"key": REGISTER_KEY, "id": user_id, "name": name, "color": color})
        self.set_token(str(answer.get("token") or ""))
        return answer.get("user") or {}

    def call(self, url: str, fingerprint: str, method: str, path: str, body=None,
             content: bytes | None = None, content_type: str | None = None, query: str = "",
             raw: bool = False):
        if not normalize_fingerprint(fingerprint):
            raise HubError("Confirme d'abord l'empreinte du certificat.", 409)
        token = self.token()
        if not token:
            raise HubError("Jeton d'équipe manquant.", 409)
        target = path if not query else f"{path}?{query}"
        return self._exchange(url, method, target, token=token, fingerprint=fingerprint,
                              body=body, content=content, content_type=content_type,
                              want="bytes" if raw else "json")

    def _exchange(self, url: str, method: str, path: str, *, token: str, fingerprint: str,
                  body=None, content: bytes | None = None, content_type: str | None = None, want: str = "json",
                  timeout: float = 30):
        parsed = check_url(url)
        port = parsed.port or 443
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(parsed.hostname, port, timeout=timeout, context=context)
        try:
            conn.connect()
            der = conn.sock.getpeercert(binary_form=True)
            seen = hashlib.sha256(der).hexdigest()
            expected = normalize_fingerprint(fingerprint)
            if want == "fingerprint":
                return seen
            if seen != expected:
                raise HubError("Le certificat n'est pas celui qui a été confirmé.", 409)
            payload = content
            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            elif content is not None:
                headers["Content-Type"] = content_type or "application/octet-stream"
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
        except HubError:
            raise
        except (OSError, socket.timeout, ssl.SSLError) as exc:
            raise HubError(f"Hub injoignable ({exc.__class__.__name__}).") from exc
        finally:
            conn.close()
        if response.status >= 400:
            try:
                detail = json.loads(raw.decode("utf-8")).get("error") or response.reason
            except ValueError:
                detail = response.reason
            raise HubError(str(detail), response.status)
        if want == "bytes":
            return raw
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise HubError("Réponse du hub illisible.") from exc

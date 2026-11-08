"""Bridge to the local WhatsApp Web sidecar; never connects to Meta APIs."""
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ProviderNotConfigured(Exception):
    pass


class ProviderError(Exception):
    pass


class WhatsAppWebAdapter:
    def __init__(self):
        self.base_url = os.getenv("WHATSAPP_WEB_URL", "http://127.0.0.1:8765").rstrip("/")

    @property
    def bridge_token(self):
        root = Path(__file__).resolve().parents[3]
        try:
            return (root / ".run" / "bridge-token").read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    @property
    def configured(self):
        return bool(self.bridge_token)

    def _request(self, path, method="GET", payload=None):
        token = self.bridge_token
        if not token:
            raise ProviderNotConfigured("Local WhatsApp bridge token is missing. Start the app with start.ps1.")
        body = json.dumps(payload).encode() if payload is not None else None
        req = Request(self.base_url + path, data=body, method=method, headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        })
        try:
            with urlopen(req, timeout=8) as response:
                return json.loads(response.read().decode())
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode()).get("error", "WhatsApp Web sidecar rejected the request.")
            except (ValueError, OSError):
                detail = "WhatsApp Web sidecar rejected the request."
            raise ProviderError(detail) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ProviderError("WhatsApp Web sidecar is unavailable. Start or reconnect the local service.") from None

    def status(self):
        try:
            result = self._request("/status")
            result["configured"] = True
            return result
        except ProviderNotConfigured as exc:
            return {"status": "DISCONNECTED", "configured": False, "detail": str(exc)}
        except ProviderError as exc:
            return {"status": "CONNECTION_FAILED", "configured": self.configured, "detail": str(exc)}

    def connect(self):
        return self._request("/connect", "POST", {})

    def disconnect(self):
        return self._request("/disconnect", "POST", {})

    def send_text(self, recipient, text):
        jid = recipient if "@" in recipient else "".join(c for c in recipient if c.isdigit()) + "@c.us"
        result = self._request("/send", "POST", {"recipient": jid, "text": text})
        if not result.get("id"):
            raise ProviderError("WhatsApp did not confirm a message ID; check the chat before retrying.")
        return result["id"]


adapter = WhatsAppWebAdapter()

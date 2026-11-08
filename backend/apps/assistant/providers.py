import json
import os
import urllib.error
import urllib.request

class ProviderNotConfigured(Exception):
    pass

class ProviderError(Exception):
    pass

class WhatsAppCloudAdapter:
    """Official WhatsApp Business Platform Cloud API adapter."""
    def __init__(self):
        self.token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
        self.phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        self.version = os.getenv("WHATSAPP_GRAPH_VERSION", "v26.0").strip()

    @property
    def configured(self):
        return bool(self.token and self.phone_id)

    def _request(self, path, *, method="GET", payload=None):
        if not self.configured:
            raise ProviderNotConfigured("WhatsApp Cloud API credentials are not configured.")
        url = f"https://graph.facebook.com/{self.version}/{path.lstrip('/')}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=body, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "Provider rejected the request")
            except Exception:
                detail = "Provider rejected the request"
            raise ProviderError(f"WhatsApp API returned HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise ProviderError("Could not confirm the WhatsApp API response. Check connection status before retrying.") from None

    def status(self):
        if not self.configured:
            return {"status": "DISCONNECTED", "configured": False, "detail": "Set the WhatsApp Cloud API values in backend/.env."}
        try:
            profile = self._request(f"{self.phone_id}?fields=display_phone_number,verified_name")
            return {"status": "CONNECTED", "configured": True, "detail": "Connected to WhatsApp Business Platform.", "phone": profile.get("display_phone_number", "")}
        except ProviderNotConfigured:
            return {"status": "DISCONNECTED", "configured": False, "detail": "WhatsApp Cloud API credentials are not configured."}
        except ProviderError as exc:
            return {"status": "CONNECTION_FAILED", "configured": True, "detail": str(exc)}

    def send_text(self, recipient, text):
        result = self._request(f"{self.phone_id}/messages", method="POST", payload={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        })
        messages = result.get("messages", [])
        if not messages or not messages[0].get("id"):
            raise ProviderError("Provider response did not include a message ID; verify delivery before retrying.")
        return messages[0]["id"]

adapter = WhatsAppCloudAdapter()

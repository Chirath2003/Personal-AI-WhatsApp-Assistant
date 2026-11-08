import hashlib
import hmac
import json
import os
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import ActivityLog, Contact, Conversation, Message, WhatsAppConnection
from .providers import adapter, ProviderError
from .safety import classify

def connection_record():
    return WhatsAppConnection.objects.first() or WhatsAppConnection.objects.create()

@api_view(["GET"])
def connection_status(request):
    saved = connection_record()
    provider = adapter.status()
    if saved.enabled and provider["status"] != "CONNECTED":
        saved.enabled = False
        saved.status = provider["status"]
        saved.detail = provider["detail"]
        saved.save()
    enabled = saved.enabled and provider["status"] == "CONNECTED"
    return Response({**provider, "enabled": enabled, "status": provider["status"] if enabled else "DISCONNECTED"})

@api_view(["POST"])
def connect(request):
    provider = adapter.status()
    if provider["status"] != "CONNECTED":
        return Response(provider, status=503)
    saved = connection_record()
    saved.enabled = True
    saved.status = "CONNECTED"
    saved.detail = "Cloud API connection verified."
    saved.save()
    ActivityLog.objects.create(event="WHATSAPP_CONNECTED", detail="Cloud API connection enabled")
    return Response({**provider, "enabled": True})

@api_view(["POST"])
def disconnect(request):
    saved = connection_record()
    saved.enabled = False
    saved.status = "DISCONNECTED"
    saved.detail = "Disconnected by user."
    saved.save()
    ActivityLog.objects.create(event="WHATSAPP_DISCONNECTED", detail="Cloud API connection disabled")
    return Response({"status": "DISCONNECTED", "enabled": False, "detail": saved.detail})

@csrf_exempt
def webhook(request):
    if request.method == "GET":
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge", "")
        expected = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
        if mode == "subscribe" and expected and hmac.compare_digest(token or "", expected):
            return HttpResponse(challenge, content_type="text/plain")
        return HttpResponse("Webhook verification failed", status=403)
    if request.method != "POST":
        return HttpResponse(status=405)
    secret = os.getenv("WHATSAPP_APP_SECRET", "")
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not secret or not signature.startswith("sha256="):
        return HttpResponse("Invalid signature", status=403)
    expected = "sha256=" + hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return HttpResponse("Invalid signature", status=403)
    try:
        payload = json.loads(request.body)
        saved = connection_record()
        if saved.enabled and payload.get("object") == "whatsapp_business_account":
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
                    for incoming in value.get("messages", []):
                        if incoming.get("type") == "text":
                            content = incoming.get("text", {}).get("body", "")
                        else:
                            content = "[{} message received]".format(incoming.get("type", "Unsupported"))
                        sender = incoming.get("from", "").strip()
                        external_id = incoming.get("id")
                        if not sender or not external_id or not content:
                            continue
                        try:
                            with transaction.atomic():
                                contact, _ = Contact.objects.get_or_create(identifier=sender, defaults={"name": names.get(sender) or sender})
                                conversation, _ = Conversation.objects.get_or_create(contact=contact, defaults={"external_id": sender})
                                if contact.excluded or classify(content)["action"] == "IGNORE":
                                    initial_status = "IGNORED"
                                elif incoming.get("type") != "text":
                                    initial_status = "PENDING"
                                elif classify(content)["category"] != "GENERAL":
                                    initial_status = "PENDING"
                                else:
                                    initial_status = "RECEIVED"
                                Message.objects.create(conversation=conversation, external_id=external_id, direction="IN", content=content, message_type=incoming.get("type", "TEXT").upper(), status=initial_status, ai_analysis=classify(content))
                                conversation.save()
                        except IntegrityError:
                            continue
        return HttpResponse("EVENT_RECEIVED", status=200)
    except (ValueError, TypeError, KeyError):
        return HttpResponse("Invalid event", status=400)

@api_view(["GET"])
def webhook_status(request):
    return Response({"configured": bool(os.getenv("WHATSAPP_APP_SECRET") and os.getenv("WHATSAPP_VERIFY_TOKEN")), "callback_path": "/api/whatsapp/webhook/", "requires_public_https": True})

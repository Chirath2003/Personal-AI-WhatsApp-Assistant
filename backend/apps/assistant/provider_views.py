import hmac
import json
import os
from pathlib import Path
from django.db import IntegrityError, transaction
from django.http import HttpResponse
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
    provider = adapter.status()
    saved = connection_record()
    connected = provider.get("status") == "CONNECTED"
    if connected and not saved.enabled:
        saved.enabled = True
        saved.status = "CONNECTED"
        saved.detail = "Personal WhatsApp Web session connected."
        saved.save(update_fields=["enabled", "status", "detail", "updated_at"])
        ActivityLog.objects.create(event="WHATSAPP_CONNECTED", detail="Personal WhatsApp Web session connected")
    elif not connected and saved.enabled:
        saved.enabled = False
        saved.status = provider.get("status", "DISCONNECTED")
        saved.detail = provider.get("detail", "WhatsApp Web disconnected.")
        saved.save(update_fields=["enabled", "status", "detail", "updated_at"])
    return Response({**provider, "enabled": bool(saved.enabled and connected)})


@api_view(["POST"])
def connect(request):
    try:
        provider = adapter.connect()
    except ProviderError as exc:
        return Response({"status": "CONNECTION_FAILED", "detail": str(exc)}, status=503)
    saved = connection_record()
    saved.enabled = provider.get("status") == "CONNECTED"
    saved.status = provider.get("status", "CONNECTING")
    saved.detail = provider.get("detail", "Scan the QR code from WhatsApp on your phone.")
    saved.save(update_fields=["enabled", "status", "detail", "updated_at"])
    return Response({**provider, "enabled": saved.enabled})


@api_view(["POST"])
def disconnect(request):
    saved = connection_record()
    saved.enabled = False
    saved.status = "DISCONNECTED"
    saved.detail = "Disconnecting and removing this device session."
    saved.save(update_fields=["enabled", "status", "detail", "updated_at"])
    try:
        provider = adapter.disconnect()
    except ProviderError as exc:
        saved.detail = str(exc)
        saved.save(update_fields=["detail", "updated_at"])
        return Response({"status": "CONNECTION_FAILED", "enabled": False, "detail": str(exc)}, status=503)
    ActivityLog.objects.create(event="WHATSAPP_DISCONNECTED", detail="Personal WhatsApp Web session disconnected")
    return Response({**provider, "enabled": False})


def _bridge_token():
    root = Path(__file__).resolve().parents[3]
    try:
        return (root / ".run" / "bridge-token").read_text(encoding="utf-8").strip()
    except OSError:
        return os.getenv("WHATSAPP_BRIDGE_TOKEN", "")


@csrf_exempt
def incoming(request):
    """Authenticated loopback event endpoint used only by the Node sidecar."""
    if request.method != "POST":
        return HttpResponse(status=405)
    token = _bridge_token()
    supplied = request.headers.get("X-Bridge-Token", "")
    if not token or not hmac.compare_digest(token, supplied):
        return HttpResponse("Unauthorized", status=401)
    try:
        data = json.loads(request.body or b"{}")
        if data.get("from_me"):
            return HttpResponse("Ignored", status=200)
        if not data.get("identifier"):
            return HttpResponse("Missing identifier", status=400)
        if not data.get("external_id"):
            return HttpResponse("Missing external message ID", status=400)
        content = str(data.get("content", "")).strip()
        if not content:
            return HttpResponse("Missing message content", status=400)
        try:
            with transaction.atomic():
                contact, _ = Contact.objects.get_or_create(identifier=str(data["identifier"]), defaults={"name": str(data.get("contact_name") or data["identifier"])[:120]})
                conversation, _ = Conversation.objects.get_or_create(contact=contact, defaults={"external_id": str(data["identifier"])})
                analysis = classify(content)
                if contact.excluded or analysis["action"] == "IGNORE":
                    initial_status = "IGNORED"
                elif str(data.get("type", "TEXT")).upper() != "TEXT" or analysis["category"] != "GENERAL":
                    initial_status = "PENDING"
                else:
                    initial_status = "RECEIVED"
                message = Message.objects.create(conversation=conversation, external_id=str(data["external_id"]), direction="IN", content=content, message_type=str(data.get("type", "TEXT")).upper(), status=initial_status, ai_analysis=analysis)
                conversation.save()
                ActivityLog.objects.create(event="MESSAGE_RECEIVED", detail="Incoming personal WhatsApp message saved locally.")
                saved = connection_record()
                saved.enabled = True
                saved.status = "CONNECTED"
                saved.detail = "Personal WhatsApp Web session connected."
                saved.save(update_fields=["enabled", "status", "detail", "updated_at"])
        except IntegrityError:
            return HttpResponse("Duplicate", status=200)
        return HttpResponse("EVENT_RECEIVED", status=201)
    except (ValueError, TypeError, KeyError):
        return HttpResponse("Invalid event", status=400)

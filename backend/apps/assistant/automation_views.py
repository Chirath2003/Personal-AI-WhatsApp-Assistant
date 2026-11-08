from datetime import time
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import AutomationSettings, Contact, Preferences, WhatsAppConnection
from .providers import adapter

@api_view(["GET", "PUT"])
def automation(request):
    settings = AutomationSettings.objects.first() or AutomationSettings.objects.create()
    if request.method == "PUT":
        for key in ("emergency_stop", "working_hours_enabled", "enabled"):
            if key in request.data and not isinstance(request.data[key], bool):
                return Response({"error": key + " must be true or false."}, status=400)
        if "reply_delay_seconds" in request.data:
            try:
                delay = int(request.data["reply_delay_seconds"])
            except (TypeError, ValueError):
                return Response({"error": "Reply delay must be a whole number of seconds."}, status=400)
            if delay < 0 or delay > 60:
                return Response({"error": "Reply delay must be from 0 to 60 seconds."}, status=400)
            settings.reply_delay_seconds = delay
        for key in ("mode", "emergency_stop", "working_hours_enabled"):
            if key in request.data:
                setattr(settings, key, request.data[key])
        if "working_hours_start" in request.data:
            try:
                settings.working_hours_start = time.fromisoformat(request.data["working_hours_start"])
            except (TypeError, ValueError):
                return Response({"error": "Working-hours start must be a valid HH:MM time."}, status=400)
        if "working_hours_end" in request.data:
            try:
                settings.working_hours_end = time.fromisoformat(request.data["working_hours_end"])
            except (TypeError, ValueError):
                return Response({"error": "Working-hours end must be a valid HH:MM time."}, status=400)
        if "enabled" in request.data:
            settings.enabled = bool(request.data["enabled"])
        if settings.mode not in ("APPROVAL", "SMART", "AUTOMATIC"):
            return Response({"error": "Invalid reply mode."}, status=400)
        if settings.emergency_stop:
            settings.enabled = False
        if settings.enabled:
            if settings.mode == "APPROVAL":
                return Response({"error": "Select Smart or Automatic mode before enabling automation."}, status=400)
            connection = WhatsAppConnection.objects.filter(enabled=True).first()
            if not connection:
                return Response({"error": "Link your personal WhatsApp account before enabling automation."}, status=409)
            live = adapter.status()
            if live["status"] != "CONNECTED":
                return Response({"error": "WhatsApp connection check failed: " + live["detail"]}, status=409)
            if not Preferences.objects.filter(share_with_ai=True).exists():
                return Response({"error": "Enable message sharing in Personalisation before enabling AI automation."}, status=409)
            authorized = any(bool(contact.allowed_categories) for contact in Contact.objects.filter(automation_enabled=True, excluded=False))
            if not authorized:
                return Response({"error": "Authorize at least one safe reply category for a contact first."}, status=409)
        settings.save()
    return Response({"enabled": settings.enabled, "mode": settings.mode, "emergency_stop": settings.emergency_stop, "reply_delay_seconds": settings.reply_delay_seconds, "working_hours_enabled": settings.working_hours_enabled, "working_hours_start": settings.working_hours_start.strftime("%H:%M"), "working_hours_end": settings.working_hours_end.strftime("%H:%M")})

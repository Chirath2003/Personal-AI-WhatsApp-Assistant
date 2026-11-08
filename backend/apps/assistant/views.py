from django.db import IntegrityError, transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import ActivityLog, AutomationSettings, Contact, Conversation, Message, Preferences, WhatsAppConnection
from .ai_engine import analyze
from .providers import adapter as whatsapp_adapter, ProviderError
from .safety import classify

def msg_json(message):
    return {"id": message.id, "reply_to_id": message.reply_to_id, "direction": message.direction, "content": message.content, "status": message.status, "type": message.message_type, "analysis": message.ai_analysis, "created_at": message.created_at}

def contact_json(contact):
    return {"id": contact.id, "name": contact.name, "identifier": contact.identifier, "excluded": contact.excluded, "automation_enabled": contact.automation_enabled, "allowed_categories": contact.allowed_categories, "reply_mode": contact.reply_mode, "instructions": contact.instructions, "last_message_at": getattr(contact, "last_message_at", None)}

def singleton(model):
    return model.objects.first() or model.objects.create()

@api_view(["GET"])
def dashboard(request):
    settings = singleton(AutomationSettings)
    metrics = {
        "messages_received": Message.objects.filter(direction="IN").count(),
        "ai_suggestions": Message.objects.filter(direction="OUT", status="PENDING", ai_analysis__has_key="action").count(),
        "pending_approvals": Message.objects.filter(direction="OUT", status="PENDING").count(),
        "auto_replies": Message.objects.filter(direction="OUT", status="SENT", ai_analysis__automation="AUTO").count(),
        "ignored": Message.objects.filter(status="IGNORED").count(),
    }
    recent = list(Conversation.objects.select_related("contact").order_by("-last_message_at")[:8].values("id", "contact__name", "last_message_at"))
    activity = list(ActivityLog.objects.order_by("-created_at").values("event", "detail", "created_at")[:8])
    return Response({"mode": settings.mode, "automation_enabled": settings.enabled, "metrics": metrics, "recent": recent, "activity": activity})

@api_view(["GET", "POST"])
def contacts(request):
    if request.method == "GET":
        qs = Contact.objects.annotate(last_message_at=Max("conversations__last_message_at")).order_by("name")
        return Response([contact_json(contact) for contact in qs])
    name = str(request.data.get("name", "")).strip()
    identifier = str(request.data.get("identifier", "")).strip()
    if not name or not identifier:
        return Response({"error": "name and identifier are required"}, status=400)
    contact, created = Contact.objects.update_or_create(identifier=identifier, defaults={"name": name, "instructions": request.data.get("instructions", "")})
    return Response(contact_json(contact), status=201 if created else 200)

@api_view(["PATCH", "DELETE"])
def contact_detail(request, pk):
    contact = get_object_or_404(Contact, pk=pk)
    if request.method == "DELETE":
        contact.delete()
        return Response(status=204)
    if request.data.get("reply_mode", contact.reply_mode) not in ("APPROVAL", "SMART", "AUTOMATIC"):
        return Response({"error": "Invalid reply mode"}, status=400)
    for field in ("excluded", "automation_enabled"):
        if field in request.data and not isinstance(request.data[field], bool):
            return Response({"error": field + " must be true or false."}, status=400)
    allowed = {"GREETING", "THANKS", "CASUAL"}
    categories = request.data.get("allowed_categories", contact.allowed_categories)
    if not isinstance(categories, list) or any(category not in allowed for category in categories):
        return Response({"error": "Invalid authorized reply categories"}, status=400)
    for key in ("name", "excluded", "automation_enabled", "allowed_categories", "reply_mode", "instructions"):
        if key in request.data:
            setattr(contact, key, request.data[key])
    contact.save()
    return Response(contact_json(contact))

@api_view(["GET"])
def conversations(request):
    qs = Conversation.objects.select_related("contact").prefetch_related("messages").order_by("-last_message_at")
    query = request.query_params.get("q", "").strip()
    if query:
        qs = qs.filter(contact__name__icontains=query) | qs.filter(contact__identifier__icontains=query)
    return Response([{"id": conversation.id, "contact": contact_json(conversation.contact), "last_message_at": conversation.last_message_at, "messages": [msg_json(message) for message in conversation.messages.all().order_by("created_at")]} for conversation in qs.distinct()[:100]])

@api_view(["POST"])
def ingest(request):
    data = request.data
    if data.get("from_me"):
        return Response({"ignored": "outgoing"})
    content = str(data.get("content", "")).strip()
    identifier = str(data.get("identifier", "")).strip()
    external_id = str(data.get("external_id", "")).strip() or None
    if not content or not identifier:
        return Response({"error": "identifier and content are required"}, status=400)
    if external_id and Message.objects.filter(external_id=external_id).exists():
        return Response({"duplicate": True})
    contact, _ = Contact.objects.get_or_create(identifier=identifier, defaults={"name": data.get("contact_name") or identifier})
    conversation, _ = Conversation.objects.get_or_create(contact=contact, defaults={"external_id": identifier})
    analysis = classify(content)
    if contact.excluded or analysis["action"] == "IGNORE":
        initial_status = "IGNORED"
    elif analysis["category"] != "GENERAL":
        initial_status = "PENDING"
    else:
        initial_status = "RECEIVED"
    try:
        with transaction.atomic():
            message = Message.objects.create(conversation=conversation, external_id=external_id, direction="IN", content=content, message_type=str(data.get("type", "TEXT")).upper(), status=initial_status, ai_analysis=analysis)
            conversation.save()
    except IntegrityError:
        if external_id and Message.objects.filter(external_id=external_id).exists():
            return Response({"duplicate": True})
        raise
    ActivityLog.objects.create(event="MESSAGE_RECEIVED", detail="Incoming message saved locally.")
    return Response({"message": msg_json(message), "contact": contact_json(contact)}, status=201)

@api_view(["POST"])
def draft(request, pk):
    incoming = get_object_or_404(Message, pk=pk, direction="IN")
    existing_reply = incoming.outgoing_replies.filter(status__in=("PENDING", "SENDING", "SENT", "FAILED")).order_by("-created_at").first()
    if existing_reply:
        return Response({"analysis": existing_reply.ai_analysis, "draft": msg_json(existing_reply)})
    if incoming.status == "IGNORED":
        return Response({"analysis": incoming.ai_analysis, "draft": None})
    if incoming.message_type != "TEXT":
        return Response({"analysis": {"action": "HUMAN_ATTENTION", "reason": "Only text messages can be drafted; review this attachment manually.", "requires_approval": True}, "draft": None})
    preferences = singleton(Preferences)
    context_messages = incoming.conversation.messages.order_by("-created_at").values_list("content", flat=True)[:12]
    context = "\n".join(text for text in context_messages if classify(text)["category"] == "GENERAL")
    result = analyze(incoming.content, context, preferences, incoming.conversation.contact.instructions)
    incoming.ai_analysis = result
    incoming.save(update_fields=["ai_analysis"])
    if result.get("action") == "IGNORE":
        incoming.status = "IGNORED"
        incoming.save(update_fields=["status"])
    reply = result.get("suggested_reply", "")
    outgoing = None
    if result.get("action") == "REPLY" and reply:
        try:
            with transaction.atomic():
                outgoing = Message.objects.create(conversation=incoming.conversation, reply_to=incoming, direction="OUT", content=reply, status="PENDING", ai_analysis=result)
        except IntegrityError:
            outgoing = incoming.outgoing_replies.filter(status__in=("PENDING", "SENDING", "SENT", "FAILED")).order_by("-created_at").first()
        ActivityLog.objects.create(event="DRAFT_CREATED", detail="AI reply draft created for review.")
    return Response({"analysis": result, "draft": msg_json(outgoing) if outgoing else None})

@api_view(["PATCH"])
def edit_draft(request, pk):
    message = get_object_or_404(Message, pk=pk, direction="OUT", status="PENDING")
    content = str(request.data.get("content", "")).strip()
    if not content:
        return Response({"error": "Reply cannot be empty"}, status=400)
    if classify(content)["category"] != "GENERAL":
        return Response({"error": "Safety rules block sensitive or high-impact content."}, status=400)
    message.content = content
    message.save(update_fields=["content"])
    return Response(msg_json(message))

@api_view(["POST"])
def reject(request, pk):
    message = get_object_or_404(Message, pk=pk, direction="OUT", status="PENDING")
    message.status = "REJECTED"
    message.save(update_fields=["status"])
    ActivityLog.objects.create(event="DRAFT_REJECTED", detail="User rejected an outgoing draft.")
    return Response(msg_json(message))

def deliver_outgoing(message):
    connection = WhatsAppConnection.objects.first()
    if not connection or not connection.enabled:
        return None, "WhatsApp is not connected.", 503
    global_settings = AutomationSettings.objects.first()
    if global_settings and global_settings.emergency_stop:
        return None, "Emergency stop is active. No messages can be sent from the assistant.", 409
    provider_state = whatsapp_adapter.status()
    if provider_state["status"] != "CONNECTED":
        connection.enabled = False
        connection.status = provider_state["status"]
        connection.detail = provider_state["detail"][:240]
        connection.save()
        return None, "WhatsApp connection is unavailable: " + provider_state["detail"], 503
    if classify(message.content)["category"] != "GENERAL":
        return None, "Safety rules block this message. Handle it manually.", 400
    if not Message.objects.filter(pk=message.pk, status="PENDING").update(status="SENDING"):
        return None, "This message is already being processed.", 409
    try:
        external_id = whatsapp_adapter.send_text(message.conversation.contact.identifier, message.content)
    except ProviderError as exc:
        Message.objects.filter(pk=message.pk, status="SENDING").update(status="FAILED")
        ActivityLog.objects.create(event="SEND_FAILED", detail="Provider send failed; inspect WhatsApp before retrying.")
        return None, str(exc), 502
    Message.objects.filter(pk=message.pk, status="SENDING").update(status="SENT", external_id=external_id)
    ActivityLog.objects.create(event="REPLY_SENT", detail="User-approved message accepted by the provider.")
    message.refresh_from_db()
    return message, None, 200

@api_view(["POST"])
def approve(request, pk):
    message = get_object_or_404(Message, pk=pk, direction="OUT", status="PENDING")
    sent, error, code = deliver_outgoing(message)
    if error:
        return Response({"error": error, "retryable": False}, status=code)
    return Response(msg_json(sent))

@api_view(["POST"])
def send_manual(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk)
    content = str(request.data.get("content", "")).strip()
    request_id = str(request.headers.get("Idempotency-Key", "")).strip()
    if not content:
        return Response({"error": "Message cannot be empty."}, status=400)
    if len(content) > 4096:
        return Response({"error": "Message exceeds the provider text limit."}, status=400)
    connection = WhatsAppConnection.objects.first()
    if not connection or not connection.enabled:
        return Response({"error": "WhatsApp is not connected."}, status=503)
    if not request_id:
        return Response({"error": "Idempotency-Key header is required."}, status=400)
    existing = Message.objects.filter(client_request_id=request_id).first()
    if existing:
        return Response(msg_json(existing), status=200 if existing.status in ("SENT", "SENDING", "FAILED") else 202)
    try:
        message = Message.objects.create(conversation=conversation, direction="OUT", content=content, status="PENDING", client_request_id=request_id)
    except IntegrityError:
        message = Message.objects.get(client_request_id=request_id)
        return Response(msg_json(message), status=200)
    sent, error, code = deliver_outgoing(message)
    if error:
        return Response({"error": error, "message": msg_json(Message.objects.get(pk=message.pk)), "retryable": False}, status=code)
    return Response(msg_json(sent), status=201)

@api_view(["GET", "PUT"])
def preferences(request):
    preference = singleton(Preferences)
    fields = ("style", "language", "length", "emoji_usage", "formality", "phrases", "avoid_phrases", "instructions", "share_with_ai")
    if request.method == "PUT":
        for key in fields:
            if key in request.data:
                setattr(preference, key, request.data[key])
        preference.save()
    return Response({key: getattr(preference, key) for key in fields})


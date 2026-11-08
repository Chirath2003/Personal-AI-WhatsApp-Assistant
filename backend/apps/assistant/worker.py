import logging
import time
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .ai_engine import analyze
from .models import ActivityLog, AutomationSettings, Contact, Message, Preferences, WhatsAppConnection
from .safety import classify
from .views import deliver_outgoing

logger = logging.getLogger(__name__)
AUTO_CATEGORIES = {"GREETING", "THANKS", "CASUAL"}

def within_working_hours(settings, now=None):
    if not settings.working_hours_enabled:
        return True
    local_now = timezone.localtime(now or timezone.now()).time().replace(tzinfo=None)
    start, end = settings.working_hours_start, settings.working_hours_end
    if start == end:
        return True
    if start < end:
        return start <= local_now < end
    return local_now >= start or local_now < end

def process_one():
    settings = AutomationSettings.objects.first()
    if not settings or settings.emergency_stop or settings.mode not in ("APPROVAL", "SMART", "AUTOMATIC"):
        return False
    if settings.mode != "APPROVAL" and not settings.enabled:
        return False
    if not within_working_hours(settings):
        return False
    preferences = Preferences.objects.filter(share_with_ai=True).first()
    if not preferences:
        return False
    connection = WhatsAppConnection.objects.filter(enabled=True).first()
    if settings.mode != "APPROVAL" and not connection:
        return False
    candidates = Message.objects.filter(direction="IN", status="RECEIVED", message_type="TEXT").order_by("created_at")[:20]
    for candidate in candidates:
        if not Message.objects.filter(pk=candidate.pk, status="RECEIVED").update(status="ANALYZING", updated_at=timezone.now()):
            continue
        incoming = Message.objects.select_related("conversation__contact", "conversation").get(pk=candidate.pk)
        contact = incoming.conversation.contact
        if contact.excluded:
            incoming.status = "IGNORED"
            incoming.ai_analysis = {"action": "IGNORE", "reason": "Contact is excluded from assistant processing."}
            incoming.save(update_fields=["status", "ai_analysis", "updated_at"])
            continue
        if classify(incoming.content)["category"] != "GENERAL":
            incoming.status = "PENDING"
            incoming.ai_analysis = classify(incoming.content)
            incoming.save(update_fields=["status", "ai_analysis", "updated_at"])
            continue
        try:
            previous = incoming.conversation.messages.exclude(pk=incoming.pk).order_by("-created_at").values_list("content", flat=True)[:12]
            context = "\n".join(text for text in previous if classify(text)["category"] == "GENERAL")
            result = analyze(incoming.content, context, preferences, contact.instructions)
            incoming.ai_analysis = result
            action = result.get("action")
            if action == "IGNORE":
                incoming.status = "IGNORED"
                incoming.save(update_fields=["status", "ai_analysis", "updated_at"])
                ActivityLog.objects.create(event="MESSAGE_IGNORED", detail="Assistant filtered a message as unnecessary.")
                return True
            reply = str(result.get("suggested_reply", "")).strip()
            if action != "REPLY" or not reply or classify(reply)["category"] != "GENERAL":
                incoming.status = "PENDING"
                incoming.save(update_fields=["status", "ai_analysis", "updated_at"])
                ActivityLog.objects.create(event="REVIEW_REQUIRED", detail="Message needs a human review; no automatic reply was sent.")
                return True
            category = result.get("message_type", "OTHER")
            authorized = bool(contact.automation_enabled and contact.reply_mode != "APPROVAL" and not contact.excluded and category in contact.allowed_categories and category in AUTO_CATEGORIES)
            high_confidence = result.get("confidence", 0) >= 0.95 and result.get("requires_approval", True) is False
            automatic = authorized and high_confidence and settings.enabled and settings.mode in ("SMART", "AUTOMATIC")
            if automatic:
                result["automation"] = "AUTO"
                incoming.ai_analysis = result
            request_id = "auto-incoming-{}".format(incoming.pk)
            with transaction.atomic():
                outgoing, created = Message.objects.get_or_create(client_request_id=request_id, defaults={"conversation": incoming.conversation, "reply_to": incoming, "direction": "OUT", "content": reply, "status": "PENDING", "ai_analysis": result})
                incoming.status = "PROCESSED"
                incoming.save(update_fields=["status", "ai_analysis", "updated_at"])
            ActivityLog.objects.create(event="DRAFT_CREATED", detail="AI reply draft created for review." if not automatic else "Authorized low-risk reply queued for sending.")
            if not automatic:
                return True
            if settings.reply_delay_seconds:
                time.sleep(settings.reply_delay_seconds)
            settings.refresh_from_db()
            contact.refresh_from_db()
            if (not settings.enabled or settings.emergency_stop or contact.excluded or not contact.automation_enabled or category not in contact.allowed_categories or not within_working_hours(settings)):
                ActivityLog.objects.create(event="AUTO_REPLY_HELD", detail="Automation authorization changed before send; reply remains pending review.")
                return True
            sent, error, _ = deliver_outgoing(outgoing)
            if error:
                ActivityLog.objects.create(event="AUTO_REPLY_HELD", detail="Provider or safety check blocked the automatic reply; review it manually.")
            return True
        except Exception:
            logger.exception("Assistant worker failed while processing message id=%s", incoming.pk)
            Message.objects.filter(pk=incoming.pk, status="ANALYZING").update(status="PENDING", ai_analysis={"action": "REQUIRE_APPROVAL", "reason": "Assistant processing failed. Review manually."}, updated_at=timezone.now())
            ActivityLog.objects.create(event="PROCESSING_FAILED", detail="Assistant processing failed; a human review is required.")
            return True
    return False

def recover_stale_claims():
    cutoff = timezone.now() - timedelta(minutes=5)
    return Message.objects.filter(direction="IN", status="ANALYZING", updated_at__lt=cutoff).update(status="RECEIVED", updated_at=timezone.now())

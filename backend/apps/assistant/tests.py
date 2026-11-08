import hashlib
import hmac
import json
import os
from unittest.mock import patch
from django.test import TestCase, override_settings
from .models import ActivityLog, Contact, Conversation, Message, Preferences, WhatsAppConnection

@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class AssistantApiTests(TestCase):
    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "localhost"

    def ingest(self, external_id="wamid.test.1", content="Hello"):
        return self.client.post("/api/messages/ingest/", data={"identifier":"94770000000","contact_name":"Test Contact","external_id":external_id,"content":content}, content_type="application/json")

    def test_health_and_dashboard(self):
        self.assertEqual(self.client.get("/api/health/").status_code, 200)
        self.assertEqual(self.client.get("/api/dashboard/").status_code, 200)

    def test_ingest_deduplicates_and_ignores_outgoing(self):
        first = self.ingest()
        duplicate = self.ingest()
        outgoing = self.client.post("/api/messages/ingest/", data={"identifier":"94770000000","content":"self","from_me":True}, content_type="application/json")
        self.assertEqual(first.status_code, 201)
        self.assertTrue(duplicate.json()["duplicate"])
        self.assertEqual(outgoing.json()["ignored"], "outgoing")
        self.assertEqual(Message.objects.count(), 1)

    def test_sensitive_incoming_never_creates_draft(self):
        incoming = self.ingest(content="Here is my OTP 129384")
        message_id = incoming.json()["message"]["id"]
        self.assertEqual(incoming.json()["message"]["status"], "PENDING")
        result = self.client.post(f"/api/messages/{message_id}/draft/")
        self.assertIsNone(result.json()["draft"])
        self.assertEqual(Message.objects.filter(direction="OUT").count(), 0)

    def test_approval_requires_provider_and_remains_pending(self):
        contact = Contact.objects.create(name="Test", identifier="94771111111")
        conversation = Conversation.objects.create(contact=contact)
        draft = Message.objects.create(conversation=conversation, direction="OUT", content="Hi", status="PENDING")
        response = self.client.post(f"/api/messages/{draft.id}/approve/")
        draft.refresh_from_db()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(draft.status, "PENDING")

    @patch("apps.assistant.views.whatsapp_adapter.status", return_value={"status": "CONNECTED", "configured": True, "detail": "ok"})
    @patch("apps.assistant.views.whatsapp_adapter.send_text", return_value="wamid.sent.1")
    def test_manual_send_is_idempotent(self, send_text, provider_status):
        contact = Contact.objects.create(name="Test", identifier="94772222222")
        conversation = Conversation.objects.create(contact=contact)
        WhatsAppConnection.objects.create(enabled=True)
        kwargs = {"HTTP_IDEMPOTENCY_KEY":"request-unique-1"}
        body = json.dumps({"content":"Thanks, got it."})
        first = self.client.post(f"/api/conversations/{conversation.id}/send/", data=body, content_type="application/json", **kwargs)
        retry = self.client.post(f"/api/conversations/{conversation.id}/send/", data=body, content_type="application/json", **kwargs)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(send_text.call_count, 1)
        self.assertEqual(Message.objects.filter(direction="OUT").count(), 1)
        self.assertEqual(Message.objects.get(direction="OUT").status, "SENT")

    @patch("apps.assistant.views.whatsapp_adapter.status", return_value={"status": "CONNECTED", "configured": True, "detail": "ok"})
    @patch("apps.assistant.views.whatsapp_adapter.send_text")
    def test_emergency_stop_blocks_all_assistant_sends(self, send_text, provider_status):
        contact = Contact.objects.create(name="Test", identifier="94775555555")
        conversation = Conversation.objects.create(contact=contact)
        WhatsAppConnection.objects.create(enabled=True)
        from .models import AutomationSettings
        AutomationSettings.objects.create(emergency_stop=True)
        response = self.client.post(f"/api/conversations/{conversation.id}/send/", data=json.dumps({"content":"Hello"}), content_type="application/json", HTTP_IDEMPOTENCY_KEY="emergency-stop-1")
        self.assertEqual(response.status_code, 409)
        send_text.assert_not_called()
    @patch.dict(os.environ, {"WHATSAPP_VERIFY_TOKEN":"verify-me","WHATSAPP_APP_SECRET":"app-secret"})
    def test_webhook_verification_and_signature_check(self):
        verified = self.client.get("/api/whatsapp/webhook/?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=hello")
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.content, b"hello")
        invalid = self.client.post("/api/whatsapp/webhook/", data=b"{}", content_type="application/json", HTTP_X_HUB_SIGNATURE_256="sha256=bad")
        self.assertEqual(invalid.status_code, 403)

    @patch.dict(os.environ, {"WHATSAPP_VERIFY_TOKEN":"verify-me","WHATSAPP_APP_SECRET":"app-secret"})
    def test_signed_webhook_ingests_once(self):
        WhatsAppConnection.objects.create(enabled=True)
        payload = {"object":"whatsapp_business_account","entry":[{"changes":[{"value":{"contacts":[{"wa_id":"94773333333","profile":{"name":"Webhook User"}}],"messages":[{"from":"94773333333","id":"wamid.webhook.1","type":"text","text":{"body":"Hello from webhook"}}]}}]}]}
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
        for _ in range(2):
            response = self.client.post("/api/whatsapp/webhook/", data=body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(Message.objects.filter(external_id="wamid.webhook.1").count(), 1)
        self.assertEqual(Contact.objects.get(identifier="94773333333").name, "Webhook User")

    @patch("apps.assistant.ai_engine.urllib.request.urlopen")
    def test_ai_is_not_called_without_explicit_sharing_consent(self, openai):
        response = self.ingest(external_id="wamid.no-consent", content="Can we talk tomorrow?")
        message_id = response.json()["message"]["id"]
        result = self.client.post(f"/api/messages/{message_id}/draft/")
        self.assertIsNone(result.json()["draft"])
        self.assertFalse(openai.called)

    def test_promotional_message_is_ignored(self):
        response = self.ingest(external_id="wamid.spam", content="Limited time offer! Buy now, claim your prize.")
        self.assertEqual(response.json()["message"]["status"], "IGNORED")
        self.assertEqual(Message.objects.filter(direction="OUT").count(), 0)

    def test_non_text_message_is_never_sent_to_ai(self):
        response = self.client.post("/api/messages/ingest/", data={"identifier":"94774444444","external_id":"wamid.image","content":"[image message received]","type":"IMAGE"}, content_type="application/json")
        message_id = response.json()["message"]["id"]
        result = self.client.post(f"/api/messages/{message_id}/draft/")
        self.assertIsNone(result.json()["draft"])
        self.assertEqual(result.json()["analysis"]["action"], "HUMAN_ATTENTION")

    def _prepare_auto_message(self, allowed_categories=None):
        from .models import AutomationSettings
        settings = AutomationSettings.objects.create(enabled=True, mode="AUTOMATIC")
        contact = Contact.objects.create(name="Auto Contact", identifier="94776666666", automation_enabled=True, reply_mode="AUTOMATIC", allowed_categories=allowed_categories or [])
        conversation = Conversation.objects.create(contact=contact)
        incoming = Message.objects.create(conversation=conversation, direction="IN", content="Hello!", status="RECEIVED", message_type="TEXT")
        Preferences.objects.create(share_with_ai=True)
        WhatsAppConnection.objects.create(enabled=True)
        return settings, contact, incoming

    @patch("apps.assistant.worker.analyze", return_value={"action":"REPLY","confidence":0.99,"message_type":"GREETING","suggested_reply":"Hi there!","requires_approval":False})
    @patch("apps.assistant.views.whatsapp_adapter.status", return_value={"status":"CONNECTED","configured":True,"detail":"ok"})
    @patch("apps.assistant.views.whatsapp_adapter.send_text", return_value="wamid.auto.1")
    def test_automatic_reply_requires_contact_category_and_sends_once(self, send_text, provider_status, analyze):
        from .worker import process_one
        _, _, incoming = self._prepare_auto_message(["GREETING"])
        self.assertTrue(process_one())
        incoming.refresh_from_db()
        outgoing = Message.objects.get(direction="OUT")
        self.assertEqual(incoming.status, "PROCESSED")
        self.assertEqual(outgoing.status, "SENT")
        self.assertEqual(send_text.call_count, 1)

    @patch("apps.assistant.worker.analyze", return_value={"action":"REPLY","confidence":0.99,"message_type":"GREETING","suggested_reply":"Hi there!","requires_approval":False})
    @patch("apps.assistant.views.whatsapp_adapter.send_text")
    def test_unauthorized_category_stays_as_approval_draft(self, send_text, analyze):
        from .worker import process_one
        _, _, incoming = self._prepare_auto_message([])
        process_one()
        incoming.refresh_from_db()
        outgoing = Message.objects.get(direction="OUT")
        self.assertEqual(incoming.status, "PROCESSED")
        self.assertEqual(outgoing.status, "PENDING")
        send_text.assert_not_called()

    def test_emergency_stop_prevents_worker_processing(self):
        from .models import AutomationSettings
        from .worker import process_one
        _, _, incoming = self._prepare_auto_message(["GREETING"])
        settings = AutomationSettings.objects.get()
        settings.emergency_stop = True
        settings.save()
        self.assertFalse(process_one())
        incoming.refresh_from_db()
        self.assertEqual(incoming.status, "RECEIVED")

    @patch("apps.assistant.worker.analyze", return_value={"action":"REPLY","confidence":0.91,"message_type":"GREETING","suggested_reply":"Hello!","requires_approval":True})
    @patch("apps.assistant.views.whatsapp_adapter.send_text")
    def test_default_approval_worker_creates_draft_without_sending(self, send_text, analyze):
        from .models import AutomationSettings
        from .worker import process_one
        AutomationSettings.objects.create(mode="APPROVAL", enabled=False)
        Preferences.objects.create(share_with_ai=True)
        contact = Contact.objects.create(name="Review Contact", identifier="94777777777")
        conversation = Conversation.objects.create(contact=contact)
        incoming = Message.objects.create(conversation=conversation, direction="IN", content="Hello?", status="RECEIVED", message_type="TEXT")
        self.assertTrue(process_one())
        incoming.refresh_from_db()
        outgoing = Message.objects.get(direction="OUT")
        self.assertEqual(incoming.status, "PROCESSED")
        self.assertEqual(outgoing.status, "PENDING")
        send_text.assert_not_called()

    @patch("apps.assistant.worker.analyze")
    def test_approval_worker_never_calls_ai_without_consent(self, analyze):
        from .models import AutomationSettings
        from .worker import process_one
        AutomationSettings.objects.create(mode="APPROVAL", enabled=False)
        contact = Contact.objects.create(name="Private Contact", identifier="94778888888")
        conversation = Conversation.objects.create(contact=contact)
        incoming = Message.objects.create(conversation=conversation, direction="IN", content="Hello?", status="RECEIVED", message_type="TEXT")
        self.assertFalse(process_one())
        analyze.assert_not_called()
        incoming.refresh_from_db()
        self.assertEqual(incoming.status, "RECEIVED")

    @patch("apps.assistant.automation_views.adapter.status", return_value={"status":"CONNECTED","detail":"ok"})
    def test_automation_requires_provider_consent_and_contact_authorization(self, provider_status):
        from .models import AutomationSettings
        contact = Contact.objects.create(name="Authorized", identifier="94779999991", automation_enabled=True, allowed_categories=["GREETING"])
        Preferences.objects.create(share_with_ai=True)
        WhatsAppConnection.objects.create(enabled=True)
        response = self.client.put("/api/automation/", data=json.dumps({"mode":"SMART","enabled":True}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        settings = AutomationSettings.objects.get()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.mode, "SMART")

    def test_automation_fails_closed_without_contact_authorization(self):
        from .models import AutomationSettings
        Preferences.objects.create(share_with_ai=True)
        WhatsAppConnection.objects.create(enabled=True)
        response = self.client.put("/api/automation/", data=json.dumps({"mode":"AUTOMATIC","enabled":True}), content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(AutomationSettings.objects.get().enabled)

    @patch("apps.assistant.views.analyze", return_value={"action":"REPLY","confidence":0.98,"message_type":"GREETING","suggested_reply":"Hi!","requires_approval":True})
    def test_repeat_draft_request_returns_existing_reply(self, analyze):
        contact = Contact.objects.create(name="Draft Contact", identifier="94770000001")
        conversation = Conversation.objects.create(contact=contact)
        incoming = Message.objects.create(conversation=conversation, direction="IN", content="Hello", status="RECEIVED", message_type="TEXT")
        Preferences.objects.create(share_with_ai=True)
        first = self.client.post(f"/api/messages/{incoming.id}/draft/")
        second = self.client.post(f"/api/messages/{incoming.id}/draft/")
        self.assertEqual(first.json()["draft"]["id"], second.json()["draft"]["id"])
        self.assertEqual(Message.objects.filter(reply_to=incoming, direction="OUT").count(), 1)
        self.assertEqual(analyze.call_count, 1)

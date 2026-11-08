from datetime import time
from django.db import models

class Contact(models.Model):
 name=models.CharField(max_length=120)
 identifier=models.CharField(max_length=160,unique=True)
 excluded=models.BooleanField(default=False)
 automation_enabled=models.BooleanField(default=False)
 allowed_categories=models.JSONField(default=list,blank=True)
 reply_mode=models.CharField(max_length=16,default="APPROVAL")
 instructions=models.TextField(blank=True)
 created_at=models.DateTimeField(auto_now_add=True)
 updated_at=models.DateTimeField(auto_now=True)
 def __str__(self): return self.name

class Conversation(models.Model):
 contact=models.ForeignKey(Contact,on_delete=models.CASCADE,related_name="conversations")
 external_id=models.CharField(max_length=200,blank=True)
 last_message_at=models.DateTimeField(auto_now=True,db_index=True)
 created_at=models.DateTimeField(auto_now_add=True)
 class Meta:
  constraints=[models.UniqueConstraint(fields=["contact"],name="one_conversation_per_contact")]

class Message(models.Model):
 class Direction(models.TextChoices): IN="IN","Incoming"; OUT="OUT","Outgoing"
 class Status(models.TextChoices): RECEIVED="RECEIVED"; ANALYZING="ANALYZING"; PROCESSED="PROCESSED"; DRAFT="DRAFT"; PENDING="PENDING"; SENDING="SENDING"; APPROVED="APPROVED"; SENT="SENT"; REJECTED="REJECTED"; IGNORED="IGNORED"; FAILED="FAILED"
 conversation=models.ForeignKey(Conversation,on_delete=models.CASCADE,related_name="messages")
 reply_to=models.ForeignKey("self",null=True,blank=True,on_delete=models.SET_NULL,related_name="outgoing_replies")
 external_id=models.CharField(max_length=200,null=True,blank=True,unique=True)
 client_request_id=models.CharField(max_length=64,null=True,blank=True,unique=True)
 direction=models.CharField(max_length=3,choices=Direction.choices)
 content=models.TextField()
 message_type=models.CharField(max_length=24,default="TEXT")
 status=models.CharField(max_length=12,choices=Status.choices,default="RECEIVED")
 ai_analysis=models.JSONField(default=dict,blank=True)
 created_at=models.DateTimeField(auto_now_add=True,db_index=True)
 updated_at=models.DateTimeField(auto_now=True,db_index=True)
 class Meta:
  constraints=[models.UniqueConstraint(fields=["reply_to"],condition=models.Q(reply_to__isnull=False,direction="OUT",status__in=["PENDING","SENDING","SENT","FAILED"]),name="one_active_reply_per_message")]

class Preferences(models.Model):
 style=models.CharField(max_length=80,default="Natural, friendly, casual")
 language=models.CharField(max_length=60,default="English")
 length=models.CharField(max_length=24,default="Concise")
 emoji_usage=models.CharField(max_length=24,default="Occasional")
 formality=models.CharField(max_length=24,default="Casual")
 phrases=models.TextField(blank=True)
 avoid_phrases=models.TextField(blank=True)
 instructions=models.TextField(blank=True)
 share_with_ai=models.BooleanField(default=False)
 updated_at=models.DateTimeField(auto_now=True)

class AutomationSettings(models.Model):
 enabled=models.BooleanField(default=False)
 mode=models.CharField(max_length=16,default="APPROVAL")
 emergency_stop=models.BooleanField(default=False)
 reply_delay_seconds=models.PositiveSmallIntegerField(default=0)
 working_hours_enabled=models.BooleanField(default=False)
 working_hours_start=models.TimeField(default=time(9, 0))
 working_hours_end=models.TimeField(default=time(17, 0))
 updated_at=models.DateTimeField(auto_now=True)


class WhatsAppConnection(models.Model):
 enabled=models.BooleanField(default=False)
 status=models.CharField(max_length=32,default="DISCONNECTED")
 detail=models.CharField(max_length=240,blank=True)
 updated_at=models.DateTimeField(auto_now=True)

class ActivityLog(models.Model):
 event=models.CharField(max_length=48,db_index=True)
 detail=models.CharField(max_length=240,blank=True)
 created_at=models.DateTimeField(auto_now_add=True,db_index=True)

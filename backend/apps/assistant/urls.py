from django.urls import path
from . import views, provider_views, automation_views

urlpatterns=[
    path("dashboard/",views.dashboard),
    path("contacts/",views.contacts),
    path("contacts/<int:pk>/",views.contact_detail),
    path("conversations/",views.conversations),
    path("conversations/<int:pk>/send/",views.send_manual),
    path("messages/ingest/",views.ingest),
    path("messages/<int:pk>/draft/",views.draft),
    path("messages/<int:pk>/edit/",views.edit_draft),
    path("messages/<int:pk>/approve/",views.approve),
    path("messages/<int:pk>/reject/",views.reject),
    path("preferences/",views.preferences),
    path("automation/",automation_views.automation),
    path("whatsapp/status/",provider_views.connection_status),
    path("whatsapp/connect/",provider_views.connect),
    path("whatsapp/disconnect/",provider_views.disconnect),
    path("whatsapp/incoming/",provider_views.incoming)
]

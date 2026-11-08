from django.contrib import admin
from django.urls import path, include
from rest_framework.decorators import api_view
from rest_framework.response import Response
@api_view(["GET"])
def health(request): return Response({"status":"ok","service":"personal-assistant-api"})
urlpatterns=[path("admin/",admin.site.urls),path("api/health/",health),path("api/",include("apps.assistant.urls"))]

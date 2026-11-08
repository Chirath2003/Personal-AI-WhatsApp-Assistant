import os
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
for env_line in ((BASE_DIR / ".env").read_text(encoding="utf-8").splitlines() if (BASE_DIR / ".env").exists() else []):
    if env_line.strip() and not env_line.strip().startswith("#") and "=" in env_line:
        env_key, env_value = env_line.split("=", 1)
        os.environ.setdefault(env_key.strip(), env_value.strip().strip(chr(34)).strip(chr(39)))
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "local-only-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "rest_framework", "corsheaders", "apps.assistant"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "corsheaders.middleware.CorsMiddleware", "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND":"django.template.backends.django.DjangoTemplates","DIRS":[],"APP_DIRS":True,"OPTIONS":{"context_processors":["django.template.context_processors.request","django.contrib.auth.context_processors.auth","django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith(("postgres://", "postgresql://")):
    from urllib.parse import urlparse
    db = urlparse(DATABASE_URL)
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": db.path.lstrip("/"), "USER": db.username or "", "PASSWORD": db.password or "", "HOST": db.hostname or "localhost", "PORT": db.port or 5432, "CONN_MAX_AGE": 60}}
else:
    DATABASES = {"default":{"ENGINE":"django.db.backends.sqlite3","NAME":BASE_DIR / "db.sqlite3"}}
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Colombo"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
CORS_ALLOWED_ORIGINS = list(dict.fromkeys(["http://localhost:5173", "http://127.0.0.1:5173"] + [origin.strip() for origin in os.getenv("FRONTEND_ORIGINS", os.getenv("FRONTEND_ORIGIN", "")).split(",") if origin.strip()]))
REST_FRAMEWORK = {"DEFAULT_PERMISSION_CLASSES":["rest_framework.permissions.AllowAny"],"DEFAULT_AUTHENTICATION_CLASSES":[]}

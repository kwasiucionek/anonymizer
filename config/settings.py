"""
Ustawienia Django dla Web UI anonimizatora.

Konfiguracja przez zmienne środowiskowe (plik .env.example zawiera komplet).
Plik .env w katalogu projektu jest wczytywany automatycznie przy starcie;
zmienne ustawione w powłoce / systemd EnvironmentFile mają pierwszeństwo.

Proces web NIE importuje silnika NER — robi to wyłącznie worker django-q2
(patrz dokumenty/tasks.py), dzięki czemu gunicorn pozostaje lekki.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Wczytuje plik .env do os.environ — bez zależności zewnętrznych.

    Zasady:
    - zmienne już obecne w środowisku mają PIERWSZEŃSTWO przed plikiem
      (spójnie z systemd EnvironmentFile i python-dotenv override=False),
    - puste linie i linie zaczynające się od "#" są pomijane,
    - opcjonalny prefiks "export " jest ignorowany,
    - wartość można ująć w cudzysłowy ("..." lub '...'); bez cudzysłowów
      wszystko od " #" w prawo traktowane jest jako komentarz końca linii,
      więc wartości zawierające "#" należy zacytować.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(BASE_DIR / ".env")


def env(key, default=None, cast=str):
    """Zmienna środowiskowa z domyślną wartością i rzutowaniem typu."""
    value = os.environ.get(key, default)
    if value is None:
        return None
    if cast is bool:
        return str(value).lower() in {"1", "true", "yes", "on"}
    return cast(value)


# --- Bezpieczeństwo ---------------------------------------------------------

DEBUG = env("DEBUG", default=False, cast=bool)

SECRET_KEY = env("SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-tylko-do-developmentu"
    else:
        raise ImproperlyConfigured("Ustaw SECRET_KEY w środowisku (DEBUG=False).")

ALLOWED_HOSTS = [
    h.strip()
    for h in env("ALLOWED_HOSTS", default="localhost,127.0.0.1").split(",")
    if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in env("CSRF_TRUSTED_ORIGINS", default="").split(",") if o.strip()
]

# Za nginx/Cloudflare (https) — włącz w środowisku produkcyjnym.
if env("BEHIND_PROXY", default=False, cast=bool):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

# --- Aplikacje --------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_q",
    "dokumenty",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Baza danych ------------------------------------------------------------
# Domyślnie SQLite (szybki start). Ustaw DB_NAME, by przełączyć na PostgreSQL.

if env("DB_NAME"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME"),
            "USER": env("DB_USER", default=""),
            "PASSWORD": env("DB_PASSWORD", default=""),
            "HOST": env("DB_HOST", default="localhost"),
            "PORT": env("DB_PORT", default="5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Uwierzytelnianie -------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dokumenty:pulpit"
LOGOUT_REDIRECT_URL = "login"

# --- Lokalizacja ------------------------------------------------------------

LANGUAGE_CODE = "pl"
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True

# --- Pliki statyczne i media ------------------------------------------------
# UWAGA: katalog media zawiera dokumenty z danymi osobowymi. NIE serwuj go
# bezpośrednio z nginx — pobieranie idzie wyłącznie przez chronione widoki.

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = env("STATIC_ROOT", default=str(BASE_DIR / "staticfiles"))

MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))

# --- Kolejka zadań (django-q2, broker ORM — bez Redisa) ----------------------
# workers=1 celowo: każdy worker to osobny proces, a każdy proces ładuje
# własną kopię modelu NER (~1,7 GB). Zwiększaj tylko świadomie.

Q_CLUSTER = {
    "name": "anonymizer",
    "label": "Kolejka anonimizacji",
    "workers": env("Q_WORKERS", default=1, cast=int),
    "timeout": env("Q_TIMEOUT", default=1800, cast=int),  # 30 min na dokument
    "retry": env("Q_RETRY", default=2100, cast=int),  # musi być > timeout
    "max_attempts": 1,  # ciężkich zadań nie ponawiamy automatycznie
    "recycle": 500,  # rzadki restart workera = rzadkie przeładowanie modelu
    "save_limit": 250,
    "orm": "default",
    "sync": env("Q_SYNC", default=False, cast=bool),  # True tylko do testów
}

# --- Silnik anonimizacji ----------------------------------------------------

# Moduł silnika importowany leniwie w workerze — plik anonymizer.py z repo.
ANONYMIZER_ENGINE = env("ANONYMIZER_ENGINE", default="anonymizer")

# Ścieżka do wytrenowanego modelu NER (katalog z model.safetensors,
# config.json ORAZ label_config.json — nowy silnik czyta mapę etykiet).
ANONYMIZER_MODEL_PATH = env(
    "ANONYMIZER_MODEL_PATH",
    default=str(BASE_DIR / "anonymization-modelv7" / "final"),
)

# Przesunięcie inicjałów o N liter (szyfr Cezara) — odpowiednik CLI --shift.
ANONYMIZER_LETTER_SHIFT = env("ANONYMIZER_LETTER_SHIFT", default=0, cast=int)

# Limity uploadu (na pojedynczy plik).
ANONYMIZER_MAX_UPLOAD_MB = env("ANONYMIZER_MAX_UPLOAD_MB", default=20, cast=int)

# --- Logowanie --------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}

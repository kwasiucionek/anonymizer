# Web UI anonimizatora (Django)

Interfejs webowy nad istniejącym silnikiem (`anonymizer.py` — NER + Morfeusz 2).
Upload plików `.xml`/`.txt`, przetwarzanie w tle z możliwością zatrzymania,
masowe usuwanie zaznaczonych dokumentów, podgląd wyniku z blokami
redakcyjnymi oraz surowy wynik do skopiowania wprost z GUI, sprawy
z wykazem wykrytych osób (scalanym po każdym dokumencie).

## Zatrzymywanie przetwarzania

- Dokument **oczekujący** znika z kolejki ORM natychmiast (status „Anulowany”).
- Dokumentu **w trakcie** nie da się przerwać w połowie wywołania silnika
  (`process_file` to jedno atomowe wejście, a ubicie procesu workera
  oznaczałoby ponowne ładowanie modelu ~1,7 GB) — worker sprawdza flagę
  kooperacyjnie przed i po wywołaniu: bieżący przebieg dokończy pracę,
  ale jego **wynik zostaje odrzucony**, wykaz sprawy nietknięty, a dokument
  ląduje jako „Anulowany”. W GUI widać wtedy fazę „Przerywanie…”.

## Architektura

```
przeglądarka ──> Django (gunicorn/runserver)   ← LEKKI proces, bez torch/spacy
                    │  zapis Document + async_task (broker ORM, po commicie)
                    ▼
                 django-q2 qcluster            ← TU ładuje się model NER
                    │  leniwy singleton NERAnonymizer (raz na proces workera)
                    ▼
                 anonymizer.process_file (sam rozpoznaje .xml/.txt)
                    │  wynik → FileField, statystyki → Document
                    ▼
                 Case.persons_cache (JSONField) ← wykaz osób scalany po przebiegu
```

Decyzje, które warto znać:

- **Silnik importowany wyłącznie w workerze** — import `anonymizer` ładuje
  transformers/torch i Morfeusz 2 (a konstruktor `SimpleAnonymizer` model
  ~1,7 GB), więc web nie dotyka go nigdy. Brak zależności silnik kwituje
  `sys.exit()` — `get_engine()` zamienia to na `RuntimeError`, dzięki czemu
  dokument ląduje jako FAILED z czytelnym komunikatem zamiast cichej
  śmierci workera.
- **`Q_WORKERS=1` celowo** — każdy worker django-q2 to osobny proces z własną
  kopią modelu w RAM/VRAM. Zwiększaj tylko świadomie.
- **Broker ORM, bez Redisa** — kolejka w tej samej bazie; `enqueue_document`
  używa `transaction.on_commit`, żeby worker nie wystartował przed commitem.
- **`Case.persons_cache` to WYKAZ, nie cache** — nowy silnik nie przyjmuje
  cache z zewnątrz i resetuje inicjały na początku każdego pliku
  (`process_file` → `reset_cache()`), więc spójność inicjałów obowiązuje
  w obrębie dokumentu. Po każdym przebiegu zbieramy `entity_counter`
  silnika i scalamy do sprawy jako informacyjny wykaz wykrytych osób
  (z eksportem do JSON).
- **Media są chronione** — pobieranie idzie przez widoki z `login_required`;
  katalogu `media/` **nie wystawiaj w nginx**.
- **Silnik wskazywany po nazwie modułu** — `ANONYMIZER_ENGINE=anonymizer`
  (plik `anonymizer.py` w katalogu projektu); `ANONYMIZER_LETTER_SHIFT`
  odpowiada opcji CLI `--shift` (szyfr Cezara na inicjałach).

## Uruchomienie deweloperskie

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # zawiera morfeusz2

export DEBUG=1
python manage.py migrate
python manage.py createsuperuser

# terminal 1 — web
python manage.py runserver

# terminal 2 — worker (tu ładuje się model)
python manage.py qcluster
```

Bez GPU/modelu można rozwijać UI na sztucznym silniku z `dokumenty/tests.py`
albo po prostu: `python manage.py test` (32 testy, torch niepotrzebny).

## Wdrożenie (Mikrus / systemd / nginx)

`.env.example` → `/etc/anonymizer/env` (uzupełnij `SECRET_KEY`,
`ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `BEHIND_PROXY=1`, ścieżki).

```ini
# /etc/systemd/system/anonymizer-web.service
[Unit]
Description=Anonimizator Web UI
After=network.target

[Service]
User=anonymizer
WorkingDirectory=/opt/anonymizer
EnvironmentFile=/etc/anonymizer/env
ExecStart=/opt/anonymizer/.venv/bin/gunicorn config.wsgi:application \
          --bind 127.0.0.1:44340 --workers 2 --timeout 60
Restart=always

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/anonymizer-worker.service
[Unit]
Description=Anonimizator worker (django-q2, model NER)
After=network.target

[Service]
User=anonymizer
WorkingDirectory=/opt/anonymizer
EnvironmentFile=/etc/anonymizer/env
ExecStart=/opt/anonymizer/.venv/bin/python manage.py qcluster
Restart=always
# model + torch potrafią zjeść sporo pamięci przy starcie:
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```

```bash
python manage.py collectstatic --noinput
systemctl enable --now anonymizer-web anonymizer-worker
```

W nginx serwuj **tylko** `STATIC_ROOT` pod `/static/`; całą resztę (w tym
pobieranie plików) proxuj do gunicorna. `media/` nie dostaje własnego
`location` — dokumenty zawierają dane osobowe i wychodzą wyłącznie przez
chronione widoki.

## Zmienne środowiskowe

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `SECRET_KEY` | — (wymagane przy `DEBUG=0`) | klucz Django |
| `DEBUG` | `0` | tryb deweloperski |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | lista po przecinku |
| `CSRF_TRUSTED_ORIGINS` | — | np. `https://anon.example.pl` |
| `BEHIND_PROXY` | `0` | `1` za nginx/Cloudflare (nagłówek X-Forwarded-Proto) |
| `DB_NAME` (+USER/PASSWORD/HOST/PORT) | — | ustawione ⇒ PostgreSQL, puste ⇒ SQLite |
| `MEDIA_ROOT`, `STATIC_ROOT` | katalog projektu | ścieżki plików |
| `ANONYMIZER_ENGINE` | `anonymizer` | moduł silnika |
| `ANONYMIZER_MODEL_PATH` | `./anonymization-modelv7/final` | katalog modelu (z `label_config.json`!) |
| `ANONYMIZER_LETTER_SHIFT` | `0` | przesunięcie inicjałów (CLI `--shift`) |
| `ANONYMIZER_MAX_UPLOAD_MB` | `20` | limit pojedynczego pliku |
| `Q_WORKERS` / `Q_TIMEOUT` / `Q_RETRY` / `Q_SYNC` | `1 / 1800 / 2100 / 0` | kolejka |

## Struktura nowych plików

```
manage.py
config/                  # settings, urls, wsgi, asgi
dokumenty/
├── models.py            # Case (JSONField wykaz osób), Document (statusy, statystyki)
├── forms.py             # upload wielu plików, walidacja .xml/.txt/UTF-8/rozmiar
├── services.py          # get_engine (guard SystemExit), scalanie wykazu, podgląd xAnon
├── tasks.py             # process_document + singleton modelu na proces workera
├── views.py             # pulpit, sprawy, chronione pobieranie, partial HTMX
├── admin.py             # badge statusu, akcja „Przetwórz ponownie”
└── tests.py             # 32 testy na sztucznym silniku (bez torch)
templates/ + static/     # UI: papier + fiolet sędziowski, lokalny htmx.min.js
```

## Pomysły na rozbudowę

- trwały cache osób w silniku (spójne inicjały między dokumentami sprawy),
- diff oryginał↔wynik zamiast samego podglądu,
- ZIP wyników całej sprawy,
- API (DRF/ninja) do integracji z innymi systemami,
- kolejka priorytetowa / osobna grupa dla dużych plików.

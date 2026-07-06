# Anonimizator dokumentów prawnych z modelem NER

System automatycznej anonimizacji polskich dokumentów prawnych wykorzystujący
model **polish-roberta-8k** z fine-tuningiem do Named Entity Recognition (NER)
oraz **Morfeusz 2** do normalizacji odmian nazwisk. Dostępny jako skrypt CLI
(`anonymizer.py`) oraz **Web UI w Django** (upload, kolejka w tle,
zatrzymywanie, podgląd wyników, sprawy z wykazem osób).

## 📋 Spis treści

- [Charakterystyka](#-charakterystyka)
- [Struktura projektu](#-struktura-projektu)
- [Wymagania i instalacja](#-wymagania-i-instalacja)
- [Użycie — CLI](#-użycie--cli)
- [Web UI (Django)](#-web-ui-django)
  - [Architektura](#architektura)
  - [Uruchomienie deweloperskie](#uruchomienie-deweloperskie)
  - [Zatrzymywanie przetwarzania](#zatrzymywanie-przetwarzania)
  - [Wdrożenie (systemd / nginx)](#wdrożenie-systemd--nginx)
  - [Zmienne środowiskowe](#zmienne-środowiskowe)
- [Trening modelu](#-trening-modelu)

---

## 🎯 Charakterystyka

### Rozpoznawane encje

Model wykrywa 4 kategorie danych osobowych:

| Kategoria | Opis | Przykład wejścia | Przykład wyjścia |
|-----------|------|------------------|------------------|
| **PERSON** | Imiona i nazwiska osób fizycznych lub pojedyncze nazwisko | Jan Kowalski | `<xAnon xSubst='J. K.'>Jan Kowalski</xAnon>` |
| **LOCATION** | Miejscowości i lokalizacje (pisane dużą literą, także w formie przymiotnika) | Warszawa | `<xAnon xSubst='W.'>Warszawa</xAnon>` |
| **PRODUCT** | Nazwy produktów | Audi Q6 | `<xAnon xSubst='A. (...)'>Audi Q6</xAnon>` |
| **SENSITIVE** | Pozostałe dane wrażliwe: PESEL, telefony, daty urodzenia, nazwy województw i gmin, organizacje i firmy | 92010112345 | `<xAnon xSubst='(...)'>92010112345</xAnon>` |

### Zasady anonimizacji

- **PERSON** — inicjały („Jan Kowalski” → „J. K.”); opcja `--shift N` przesuwa
  litery szyfrem Cezara (np. `--shift 2`: „Jan Kowalski” → „L. M.”).
  Różne osoby o tych samych inicjałach dostają licznik: „J. K. (1)”,
  „J. K. (2)” — a gdy inicjały są w dokumencie unikalne, post-processing
  zdejmuje zbędne „(1)”. Odmiany tego samego nazwiska („Jana Kowalskiego”)
  są sprowadzane do formy bazowej przez Morfeusz 2, więc dostają te same
  inicjały **w obrębie pliku** (każdy plik zaczyna z czystym cache).
- **LOCATION** — pierwsza litera („Warszawa” → „W.”).
- **PRODUCT** — pierwsza litera („Audi” → „A.”), dla wyrażeń wieloczłonowych
  pierwsza litera + „(...)” („Super Ekstra Krem” → „S. (...)”).
- **SENSITIVE** — zastąpienie „(...)”.

### Wyjątki

Model **NIE** anonimizuje:

- ❌ sędziów i prokuratorów,
- ❌ nazw sądów i instytucji publicznych,
- ❌ ekspertów sądowych i biegłych (w kontekście).

---

## 📁 Struktura projektu

```
anonymizer/
│
├── anonymizer.py            # ⭐ Silnik: NER + Morfeusz 2 (CLI i API)
├── anon-test_runner2.py     # Skrypt testowy silnika
├── data/                    # Przykładowe dokumenty do anonimizacji
├── test/                    # Dokumenty testowe (wyroki)
├── ewaluacja/               # Materiały ewaluacyjne
│
├── anonymization-model/     # Wytrenowany model (poza repo — za duży)
│   ├── config.json
│   ├── model.safetensors
│   ├── tokenizer_config.json
│   ├── vocab.json / merges.txt
│   └── label_config.json    # ⚠ wymagany — mapa etykiet dla silnika
│
│                            # --- Web UI (Django) ---
├── manage.py
├── config/                  # settings (czyta .env), urls, wsgi/asgi
├── dokumenty/               # aplikacja: modele, zadania, widoki, testy
├── templates/  static/      # szablony i zasoby (HTMX lokalnie)
├── requirements.txt         # zależności silnika + Web UI
└── .env.example             # wzorzec konfiguracji
```

---

## 🔧 Wymagania i instalacja

- **Python:** 3.10+ (Django 5.2); sam silnik działa od 3.8
- **RAM:** 8 GB (CPU) / 4 GB (GPU); **GPU** CUDA opcjonalnie
- **Model:** katalog z wagami i `label_config.json` (silnik czyta z niego
  mapę etykiet — bez tego pliku ładowanie modelu się nie powiedzie)

```bash
git clone https://github.com/kwasiucionek/anonymizer.git
cd anonymizer
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # Django, django-q2, transformers, torch, morfeusz2
```

Szybki test silnika (pokaże pomoc CLI):

```bash
python3 anonymizer.py
```

---

## 🚀 Użycie — CLI

```bash
python3 anonymizer.py <model_path> <plik|katalog> [opcje]
```

**Opcje:**

- `--test` — uruchom test modelu i inicjałów,
- `--debug` — tryb debugowania z dodatkowymi informacjami,
- `--shift N` — przesuń inicjały o N liter (domyślnie 0, bez przesunięcia).

**Przykłady:**

```bash
# katalog plików .xml/.txt
python3 anonymizer.py ./anonymization-model data/

# pojedynczy plik z przesunięciem inicjałów o 2
python3 anonymizer.py ./anonymization-model data/wyrok.xml --shift 2
```

Wyniki lądują obok źródeł, w podkatalogu z sygnaturą czasu:
`data/output_minimal_YYYYMMDD_HHMMSS/wyrok_anon.xml`.

Testy silnika: `python3 anon-test_runner2.py`.

---

## 🖥 Web UI (Django)

Upload wielu plików `.xml`/`.txt`, przetwarzanie w tle z możliwością
zatrzymania, masowe usuwanie zaznaczonych dokumentów, podgląd wyniku
z blokami redakcyjnymi oraz surowy plik do skopiowania wprost z GUI,
sprawy z wykazem wykrytych osób (scalanym po każdym dokumencie)
i eksportem do JSON.

### Architektura

```
przeglądarka ──> Django (gunicorn/runserver)   ← LEKKI proces, bez torch/morfeusz2
                    │  zapis Document + async_task (broker ORM, po commicie)
                    ▼
                 django-q2 qcluster            ← TU ładuje się model NER
                    │  leniwy singleton SimpleAnonymizer (raz na proces workera)
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
- **`Q_WORKERS=1` celowo** — każdy worker django-q2 to osobny proces
  z własną kopią modelu w RAM/VRAM. Zwiększaj tylko świadomie.
- **Broker ORM, bez Redisa** — kolejka w tej samej bazie; kolejkowanie przez
  `transaction.on_commit`, żeby worker nie wystartował przed commitem.
- **`Case.persons_cache` to WYKAZ, nie cache** — silnik nie przyjmuje cache
  z zewnątrz i resetuje inicjały na początku każdego pliku, więc spójność
  inicjałów obowiązuje w obrębie dokumentu. Po każdym przebiegu zbieramy
  `entity_counter` silnika i scalamy do sprawy jako informacyjny wykaz
  wykrytych osób (z eksportem do JSON).
- **Media są chronione** — pobieranie idzie przez widoki z `login_required`;
  katalogu `media/` **nie wystawiaj w nginx**.

### Uruchomienie deweloperskie

```bash
cp .env.example .env    # settings czytają .env automatycznie
export DEBUG=1          # albo DEBUG=1 w .env
python manage.py migrate
python manage.py createsuperuser

# terminal 1 — web
python manage.py runserver

# terminal 2 — worker (tu ładuje się model)
python manage.py qcluster
```

Bez GPU/modelu można rozwijać UI na sztucznym silniku z `dokumenty/tests.py`
albo po prostu: `python manage.py test` (32 testy, torch niepotrzebny).

### Zatrzymywanie przetwarzania

- Dokument **oczekujący** znika z kolejki ORM natychmiast (status „Anulowany”).
- Dokumentu **w trakcie** nie da się przerwać w połowie wywołania silnika
  (`process_file` to jedno atomowe wejście, a ubicie procesu workera
  oznaczałoby ponowne ładowanie modelu ~1,7 GB) — worker sprawdza flagę
  kooperacyjnie przed i po wywołaniu: bieżący przebieg dokończy pracę,
  ale jego **wynik zostaje odrzucony**, wykaz sprawy nietknięty, a dokument
  ląduje jako „Anulowany”. W GUI widać wtedy fazę „Przerywanie…”.

### Wdrożenie (systemd / nginx)

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

### Zmienne środowiskowe

Plik `.env` w katalogu projektu jest wczytywany automatycznie; zmienne
z powłoki / systemd `EnvironmentFile` mają pierwszeństwo.

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `SECRET_KEY` | — (wymagane przy `DEBUG=0`) | klucz Django |
| `DEBUG` | `0` | tryb deweloperski |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | lista po przecinku |
| `CSRF_TRUSTED_ORIGINS` | — | np. `https://anon.example.pl` |
| `BEHIND_PROXY` | `0` | `1` za nginx/Cloudflare (X-Forwarded-Proto) |
| `DB_NAME` (+USER/PASSWORD/HOST/PORT) | — | ustawione ⇒ PostgreSQL, puste ⇒ SQLite |
| `MEDIA_ROOT`, `STATIC_ROOT` | katalog projektu | ścieżki plików |
| `ANONYMIZER_ENGINE` | `anonymizer` | moduł silnika |
| `ANONYMIZER_MODEL_PATH` | `./anonymization-modelv7/final` | katalog modelu (z `label_config.json`!) |
| `ANONYMIZER_LETTER_SHIFT` | `0` | przesunięcie inicjałów (CLI `--shift`) |
| `ANONYMIZER_MAX_UPLOAD_MB` | `20` | limit pojedynczego pliku |
| `Q_WORKERS` / `Q_TIMEOUT` / `Q_RETRY` / `Q_SYNC` | `1 / 1800 / 2100 / 0` | kolejka django-q2 |

### Pomysły na rozbudowę

- trwały cache osób w silniku (spójne inicjały między dokumentami sprawy),
- diff oryginał↔wynik zamiast samego podglądu,
- ZIP wyników całej sprawy,
- API (DRF/ninja) do integracji z innymi systemami,
- kolejka priorytetowa / osobna grupa dla dużych plików.

---

## 🎓 Trening modelu

Model bazowy: **PKOBP/polish-roberta-8k**, fine-tuning do token classification
najlepiej w **Google Colab** (darmowe GPU T4).

### Format datasetu (BIO)

```json
{
  "text": "W sprawie o sygnaturze akt I C 123/25, powód Jan Kowalski domagał się od firmy \"Pol-Bud\" S.A. (KRS: 0000123456) zapłaty.",
  "tokens": ["W", "sprawie", "o", "sygnaturze", "akt", "I", "C", "123/25", ",", "powód", "Jan", "Kowalski", "domagał", "się", "od", "firmy", "\"", "Pol-Bud", "\"", "S.A.", "(", "KRS:", "0000123456", ")", "zapłaty", "."],
  "labels": ["O", "O", "O", "O", "O", "O", "O", "O", "O", "O", "B-PERSON", "I-PERSON", "O", "O", "O", "O", "O", "B-ORG", "O", "O", "O", "O", "B-SENSITIVE", "O", "O", "O"]
}
```

### Hiperparametry wyjściowe

```python
MODEL_NAME    = "PKOBP/polish-roberta-8k"
SIMPLE_MODE   = True     # 4 kategorie
NUM_EPOCHS    = 10       # 8-15 optymalnie
BATCH_SIZE    = 8        # 4-16
LEARNING_RATE = 1e-5
MAX_LENGTH    = 512
TRAIN_SPLIT   = 0.8
```

Przebieg treningu: wczytanie datasetu → podział 80/20 → tokenizacja →
trening z early stopping → ewaluacja → zapis najlepszego checkpointu
(najwyższy F1) → testy jakościowe. Przykładowy przebieg:

```
Step  Training Loss  Validation Loss  Precision  Recall  F1      Accuracy
50    0.3985        0.4354           0.5802     0.7633  0.6593  0.8428
200   0.0291        0.2414           0.8075     0.8316  0.8193  0.9354
400   0.0077        0.2428           0.8665     0.8721  0.8693  0.9474
```

Po treningu spakuj katalog modelu (musi zawierać `label_config.json`),
rozpakuj lokalnie i wskaż w `ANONYMIZER_MODEL_PATH` albo podaj w CLI:

```bash
python3 anonymizer.py ./anonymization-model/final data/
```

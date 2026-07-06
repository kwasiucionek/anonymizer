"""
Zadania kolejki django-q2 + operacje na kolejce (kolejkowanie, zatrzymywanie).

Silnik NER (RoBERTa ~1,7 GB + Morfeusz 2) ładuje się WYŁĄCZNIE tutaj,
leniwie, przy pierwszym zadaniu — i pozostaje w pamięci procesu workera
jako singleton. Proces web nigdy nie importuje tego modułu poza funkcjami
kolejkowymi.

Nowy silnik (anonymizer.py):
- SimpleAnonymizer(model_path, debug, letter_shift) — bez PersonCache;
- process_file(input, anonymizer, output) sam rozpoznaje .xml/.txt i na
  starcie woła reset_cache(), więc inicjały są spójne W OBRĘBIE pliku,
  a nie między dokumentami — stąd wykaz osób sprawy jest informacyjny
  (zbieramy go PO przebiegu z anonymizer.entity_counter).

Semantyka zatrzymywania (request_stop):
- dokument PENDING → status „Anulowany” + wpis znika z kolejki ORM (natychmiast);
- dokument PROCESSING → ustawiamy flagę cancel_requested, którą worker
  sprawdza kooperacyjnie: przed wywołaniem silnika i zaraz po nim. Samego
  wywołania process_file nie da się przerwać w połowie (to jedno atomowe
  wejście w silnik), a ubijanie procesu workera oznaczałoby ponowne ładowanie
  modelu i ryzyko redelivery — dlatego bieżący przebieg dokończy pracę,
  ale jego wynik zostanie ODRZUCONY i dokument wyląduje jako „Anulowany”.
"""

import logging
import tempfile
import time
import traceback
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.utils import timezone
from django_q.tasks import async_task

from .models import Document
from .services import get_engine, merge_persons_into_case

logger = logging.getLogger(__name__)

# Stan procesu workera: jeden załadowany model na proces.
_state = {"engine_name": None, "anonymizer": None}


# --- Operacje na kolejce (wywoływane z procesu web) ---------------------------


def enqueue_document(document):
    """Zakolejkuj dokument po commicie transakcji (broker ORM czyta z tej
    samej bazy — bez on_commit worker mógłby nie zobaczyć jeszcze wiersza).
    Id zadania zapisujemy na dokumencie, by dało się zdjąć wpis z kolejki."""
    pk = document.pk

    def _enqueue():
        task_id = async_task(
            "dokumenty.tasks.process_document",
            pk,
            task_name=f"dokument-{pk}",
            group="anonimizacja",
        )
        Document.objects.filter(pk=pk).update(task_id=task_id)

    transaction.on_commit(_enqueue)


def remove_from_queue(task_id):
    """Usuń oczekujące zadanie z kolejki ORM. Zwraca True, gdy coś zdjęto."""
    from django_q.models import OrmQ

    removed = False
    for ormq in OrmQ.objects.all():
        # OrmQ.task to zdekodowany słownik zadania (cached_property).
        if ormq.task.get("id") == task_id:
            ormq.delete()
            removed = True
    return removed


def request_stop(document):
    """Zatrzymaj dokument. Zwraca: 'cancelled' (zdjęty z kolejki),
    'stopping' (worker przerwie przy najbliższej okazji) albo 'noop'."""
    with transaction.atomic():
        try:
            doc = Document.objects.select_for_update().get(pk=document.pk)
        except Document.DoesNotExist:
            return "noop"

        if doc.status == Document.Status.PENDING:
            doc.status = Document.Status.CANCELLED
            doc.finished_at = timezone.now()
            doc.cancel_requested = False
            doc.save(
                update_fields=["status", "finished_at", "cancel_requested", "modified"]
            )
            if doc.task_id:
                # Best effort: jeśli worker zdążył pobrać zadanie, wpisu już
                # nie ma — wtedy _claim_document i tak zobaczy status ≠ PENDING.
                remove_from_queue(doc.task_id)
            return "cancelled"

        if doc.status == Document.Status.PROCESSING:
            if not doc.cancel_requested:
                doc.cancel_requested = True
                doc.save(update_fields=["cancel_requested", "modified"])
            return "stopping"

    return "noop"


# --- Wnętrze workera ----------------------------------------------------------


def _get_anonymizer(engine):
    """Singleton anonimizatora na proces workera (model ładuje się raz)."""
    if (
        _state["anonymizer"] is None
        or _state["engine_name"] != settings.ANONYMIZER_ENGINE
    ):
        logger.info(
            "Ładuję model NER: %s (silnik %s)",
            settings.ANONYMIZER_MODEL_PATH,
            settings.ANONYMIZER_ENGINE,
        )
        _state["anonymizer"] = engine.SimpleAnonymizer(
            model_path=settings.ANONYMIZER_MODEL_PATH,
            debug=False,
            letter_shift=settings.ANONYMIZER_LETTER_SHIFT,
        )
        _state["engine_name"] = settings.ANONYMIZER_ENGINE
    # letter_shift czytany jest przy każdej podmianie — odśwież z ustawień,
    # gdyby singleton przeżył zmianę konfiguracji.
    _state["anonymizer"].letter_shift = settings.ANONYMIZER_LETTER_SHIFT
    return _state["anonymizer"]


def _collect_persons(anonymizer):
    """Wykaz osób z bieżącego przebiegu: {znormalizowana osoba: inicjały}.

    Silnik trzyma wartości w postaci „J. K. (1)” — licznik zdejmujemy,
    gdy inicjały są unikalne (dokładnie tak, jak post_process robi to
    w tekście wynikowym)."""
    persons = {}
    for name, replacement in anonymizer.entity_counter.items():
        initials, sep, _ = replacement.rpartition(" (")
        if sep and anonymizer.initials_counter.get(initials, 0) == 1:
            persons[name] = initials
        else:
            persons[name] = replacement
    return persons


def _claim_document(document_id):
    """Atomowo przejmij dokument PENDING → PROCESSING (idempotencja przy
    podwójnym zakolejkowaniu; anulowane/usunięte dokumenty są pomijane)."""
    with transaction.atomic():
        document = (
            Document.objects.select_for_update()
            .select_related("case")
            .filter(pk=document_id)
            .first()
        )
        if document is None or document.status != Document.Status.PENDING:
            return None
        document.status = Document.Status.PROCESSING
        document.save(update_fields=["status", "modified"])
        return document


def _stop_requested(document_id):
    """Świeży odczyt flagi zatrzymania (mogła zmienić się w trakcie)."""
    return Document.objects.filter(pk=document_id, cancel_requested=True).exists()


def _finish_cancelled(document, started):
    """Zamknij dokument jako anulowany — bez zapisu wyniku i scalania wykazu."""
    document.status = Document.Status.CANCELLED
    document.cancel_requested = False
    document.error_message = ""
    document.duration = round(time.monotonic() - started, 2)
    document.finished_at = timezone.now()
    document.save(
        update_fields=[
            "status",
            "cancel_requested",
            "error_message",
            "duration",
            "finished_at",
            "modified",
        ]
    )
    logger.info("Dokument %s anulowany na żądanie.", document.pk)
    return {"cancelled": True}


def process_document(document_id):
    """Główne zadanie: anonimizuje jeden dokument i aktualizuje wykaz sprawy."""
    document = _claim_document(document_id)
    if document is None:
        logger.info("Dokument %s nie oczekuje — pomijam.", document_id)
        return {"skipped": True}

    started = time.monotonic()
    try:
        engine = get_engine()
        anonymizer = _get_anonymizer(engine)

        # Ostatni moment na tanie przerwanie — model już w pamięci,
        # ale silnik jeszcze nie ruszył.
        if _stop_requested(document_id):
            return _finish_cancelled(document, started)

        source_path = Path(document.source_file.path)

        with tempfile.TemporaryDirectory(prefix="anon-") as tmp_dir:
            output_path = Path(tmp_dir) / document.result_name
            # process_file sam rozpoznaje .xml/.txt po rozszerzeniu
            # i na starcie resetuje inicjały (spójność w obrębie pliku).
            ok, entity_count = engine.process_file(source_path, anonymizer, output_path)
            if not ok:
                raise RuntimeError(
                    "Silnik zgłosił błąd przetwarzania (szczegóły w logu workera)."
                )

            # Silnik skończył — sprawdź, czy dokument wciąż istnieje i czy
            # w międzyczasie nie zażądano zatrzymania (wtedy wynik odrzucamy).
            fresh = Document.objects.filter(pk=document_id).first()
            if fresh is None:
                logger.info(
                    "Dokument %s usunięty w trakcie — porzucam wynik.", document_id
                )
                return {"skipped": "deleted"}
            if fresh.cancel_requested:
                return _finish_cancelled(fresh, started)

            with output_path.open("rb") as fh:
                document.result_file.save(document.result_name, File(fh), save=False)

        persons = _collect_persons(anonymizer)

        document.status = Document.Status.DONE
        document.entity_count = entity_count
        document.person_count = len(persons)
        document.error_message = ""
        document.duration = round(time.monotonic() - started, 2)
        document.finished_at = timezone.now()
        document.save()

        if document.case and persons:
            merge_persons_into_case(document.case.pk, persons)

        logger.info(
            "Dokument %s gotowy: %s encji, %s osób, %.2f s",
            document.pk,
            document.entity_count,
            document.person_count,
            document.duration,
        )
        return {"total": document.entity_count, "persons": document.person_count}

    except Exception as exc:
        if not Document.objects.filter(pk=document_id).exists():
            # Usunięty w trakcie — nie wskrzeszaj wiersza zapisem po pk.
            logger.info("Dokument %s usunięty w trakcie — porzucam.", document_id)
            return {"skipped": "deleted"}
        document.status = Document.Status.FAILED
        document.cancel_requested = False
        document.error_message = f"{exc}\n\n{traceback.format_exc()[-2000:]}"
        document.duration = round(time.monotonic() - started, 2)
        document.finished_at = timezone.now()
        document.save()
        logger.exception("Błąd przetwarzania dokumentu %s", document.pk)
        raise  # niech django-q2 też odnotuje porażkę (max_attempts=1)

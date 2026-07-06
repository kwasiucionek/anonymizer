"""
Modele domenowe Web UI anonimizatora.

Case grupuje dokumenty i przechowuje WYKAZ wykrytych osób (JSONField:
znormalizowana osoba → inicjały), scalany po każdym dokumencie. Nowy silnik
(anonymizer.py) resetuje inicjały per plik i nie przyjmuje cache z zewnątrz,
więc wykaz jest informacyjny — nie zasila przetwarzania.
"""

from pathlib import Path

from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse


class TimeStampedModel(models.Model):
    """Abstrakcyjna baza ze znacznikami czasu."""

    created = models.DateTimeField("utworzono", auto_now_add=True)
    modified = models.DateTimeField("zmodyfikowano", auto_now=True)

    class Meta:
        abstract = True
        get_latest_by = "created"


class Case(TimeStampedModel):
    """Sprawa — grupuje dokumenty i wykaz wykrytych osób."""

    case_id = models.CharField(
        "sygnatura / identyfikator sprawy",
        max_length=120,
        unique=True,
        help_text="Np. I C 123/24 — odpowiednik --case-id z wersji CLI.",
    )
    description = models.TextField("opis", blank=True)
    persons_cache = models.JSONField(
        "wykaz osób",
        default=dict,
        blank=True,
        help_text="Znormalizowana osoba → inicjały; scalany po każdym dokumencie.",
    )

    class Meta:
        verbose_name = "sprawa"
        verbose_name_plural = "sprawy"
        ordering = ["-created"]

    def __str__(self):
        return self.case_id

    def get_absolute_url(self):
        return reverse("dokumenty:sprawa", args=[self.pk])

    @property
    def person_count(self):
        return len(self.persons_cache)

    def cache_as_cli_json(self):
        """Eksport wykazu osób sprawy jako JSON: {case_id, persons, count}."""
        return {
            "case_id": self.case_id,
            "persons": self.persons_cache,
            "count": len(self.persons_cache),
        }


class DocumentQuerySet(models.QuerySet):
    def pending(self):
        return self.filter(status=Document.Status.PENDING)

    def in_progress(self):
        return self.filter(
            status__in=[Document.Status.PENDING, Document.Status.PROCESSING]
        )

    def finished(self):
        return self.filter(
            status__in=[
                Document.Status.DONE,
                Document.Status.FAILED,
                Document.Status.CANCELLED,
            ]
        )


class Document(TimeStampedModel):
    """Pojedynczy dokument przekazany do anonimizacji."""

    class Status(models.TextChoices):
        PENDING = "pending", "Oczekuje"
        PROCESSING = "processing", "Przetwarzanie"
        DONE = "done", "Gotowy"
        FAILED = "failed", "Błąd"
        CANCELLED = "cancelled", "Anulowany"

    case = models.ForeignKey(
        Case,
        verbose_name="sprawa",
        related_name="documents",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    original_name = models.CharField("nazwa oryginalna", max_length=255)
    source_file = models.FileField(
        "plik źródłowy", upload_to="dokumenty/zrodlowe/%Y/%m/"
    )
    result_file = models.FileField(
        "plik wynikowy", upload_to="dokumenty/wyniki/%Y/%m/", blank=True
    )
    status = models.CharField(
        "status", max_length=20, choices=Status.choices, default=Status.PENDING
    )
    error_message = models.TextField("komunikat błędu", blank=True)
    entity_count = models.PositiveIntegerField("wykryte encje", default=0)
    person_count = models.PositiveIntegerField("wykryte osoby", default=0)
    duration = models.FloatField("czas przetwarzania [s]", null=True, blank=True)
    finished_at = models.DateTimeField("zakończono", null=True, blank=True)

    # Zatrzymywanie: id zadania django-q2 (pozwala zdjąć wpis z kolejki ORM,
    # póki dokument oczekuje) oraz flaga sprawdzana kooperacyjnie przez worker.
    task_id = models.CharField("id zadania kolejki", max_length=64, blank=True)
    cancel_requested = models.BooleanField("zażądano zatrzymania", default=False)

    objects = DocumentQuerySet.as_manager()

    class Meta:
        verbose_name = "dokument"
        verbose_name_plural = "dokumenty"
        ordering = ["-created"]
        indexes = [models.Index(fields=["status"])]

    def __str__(self):
        return self.original_name

    def get_absolute_url(self):
        return reverse("dokumenty:dokument", args=[self.pk])

    @property
    def is_in_progress(self):
        return self.status in {self.Status.PENDING, self.Status.PROCESSING}

    @property
    def is_stopping(self):
        """Worker dostał żądanie przerwania, ale jeszcze go nie obsłużył."""
        return self.status == self.Status.PROCESSING and self.cancel_requested

    @property
    def suffix(self):
        """Rozszerzenie pliku wg nazwy oryginalnej (.xml / .txt)."""
        return Path(self.original_name).suffix.lower()

    @property
    def result_name(self):
        """Nazwa pliku wynikowego w konwencji CLI: nazwa_anon.xml."""
        p = Path(self.original_name)
        return f"{p.stem}_anon{p.suffix}"

    def reset_for_retry(self):
        """Wyczyść wynik i statystyki przed ponownym uruchomieniem."""
        if self.result_file:
            self.result_file.delete(save=False)
        self.result_file = ""
        self.status = self.Status.PENDING
        self.error_message = ""
        self.entity_count = 0
        self.person_count = 0
        self.duration = None
        self.finished_at = None
        self.task_id = ""
        self.cancel_requested = False
        self.save()


@receiver(post_delete, sender=Document, dispatch_uid="document_files_cleanup")
def delete_document_files(sender, instance, **kwargs):
    """Usuń pliki z dysku po skasowaniu rekordu (działa też przy bulk delete
    w adminie, w przeciwieństwie do nadpisanego Model.delete)."""
    for field_file in (instance.source_file, instance.result_file):
        if field_file:
            field_file.delete(save=False)

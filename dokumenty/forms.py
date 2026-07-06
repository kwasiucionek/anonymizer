"""Formularze uploadu dokumentów."""

from pathlib import Path

from django import forms
from django.conf import settings

from .models import Case

ALLOWED_SUFFIXES = {".xml", ".txt"}


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Pole na wiele plików — udokumentowany wzorzec dla Django 5.x."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_file_clean(item, initial) for item in data]
        return [single_file_clean(data, initial)]


class UploadForm(forms.Form):
    files = MultipleFileField(
        label="Pliki do anonimizacji",
        help_text="Pliki .xml lub .txt w kodowaniu UTF-8. Można wybrać wiele naraz.",
    )
    case = forms.ModelChoiceField(
        label="Istniejąca sprawa",
        queryset=Case.objects.all(),
        required=False,
        empty_label="— bez sprawy —",
        help_text="Dokumenty tej samej sprawy współdzielą cache osób.",
    )
    new_case_id = forms.CharField(
        label="…lub nowa sygnatura",
        max_length=120,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "np. I C 123/24"}),
    )

    def clean_files(self):
        files = self.cleaned_data["files"]
        max_bytes = settings.ANONYMIZER_MAX_UPLOAD_MB * 1024 * 1024

        for f in files:
            suffix = Path(f.name).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                raise forms.ValidationError(
                    f"„{f.name}”: dozwolone są tylko pliki .xml i .txt."
                )
            if f.size > max_bytes:
                raise forms.ValidationError(
                    f"„{f.name}”: plik przekracza limit "
                    f"{settings.ANONYMIZER_MAX_UPLOAD_MB} MB."
                )
            try:
                f.read().decode("utf-8")
            except UnicodeDecodeError:
                raise forms.ValidationError(
                    f"„{f.name}”: plik nie jest tekstem w kodowaniu UTF-8."
                )
            finally:
                f.seek(0)
        return files

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("case") and cleaned.get("new_case_id", "").strip():
            raise forms.ValidationError(
                "Wybierz istniejącą sprawę albo podaj nową sygnaturę — nie oba naraz."
            )
        return cleaned

    def resolve_case(self):
        """Zwróć sprawę z formularza; nową utwórz przy pierwszym użyciu sygnatury."""
        new_case_id = self.cleaned_data.get("new_case_id", "").strip()
        if new_case_id:
            case, _created = Case.objects.get_or_create(case_id=new_case_id)
            return case
        return self.cleaned_data.get("case")

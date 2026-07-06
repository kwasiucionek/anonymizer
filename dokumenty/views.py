"""Widoki Web UI. Wszystkie wymagają zalogowania — dokumenty zawierają
dane osobowe, więc nic nie jest publiczne (łącznie z plikami media)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import UploadForm
from .models import Case, Document
from .services import remove_person_from_case, render_anonymization_preview
from .tasks import enqueue_document, remove_from_queue, request_stop

PREVIEW_MAX_BYTES = 400_000


@login_required
def pulpit(request):
    """Strona główna: upload + lista ostatnich dokumentów."""
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            case = form.resolve_case()
            files = form.cleaned_data["files"]
            for f in files:
                document = Document.objects.create(
                    case=case, original_name=f.name, source_file=f
                )
                enqueue_document(document)
            messages.success(
                request,
                f"Przyjęto {len(files)} plik(ów) do anonimizacji."
                + (f" Sprawa: {case}." if case else ""),
            )
            return redirect("dokumenty:pulpit")
    else:
        form = UploadForm()

    documents = Document.objects.select_related("case")
    paginator = Paginator(documents, 25)
    page = paginator.get_page(request.GET.get("strona"))

    return render(
        request,
        "dokumenty/pulpit.html",
        {"form": form, "page": page, "in_progress": documents.in_progress().exists()},
    )


@login_required
def dokument_wiersz(request, pk):
    """Partial HTMX — pojedynczy wiersz tabeli (polling statusu)."""
    document = get_object_or_404(Document.objects.select_related("case"), pk=pk)
    return render(request, "dokumenty/partials/_dokument_wiersz.html", {"d": document})


@login_required
def dokument_detail(request, pk):
    document = get_object_or_404(Document.objects.select_related("case"), pk=pk)

    preview = raw_text = None
    truncated = False
    if document.status == Document.Status.DONE and document.result_file:
        with document.result_file.open("rb") as fh:
            raw = fh.read(PREVIEW_MAX_BYTES + 1)
        truncated = len(raw) > PREVIEW_MAX_BYTES
        raw_text = raw[:PREVIEW_MAX_BYTES].decode("utf-8", errors="replace")
        preview = render_anonymization_preview(raw_text)

    return render(
        request,
        "dokumenty/dokument_detail.html",
        {
            "d": document,
            "preview": preview,
            "raw_text": raw_text,
            "truncated": truncated,
        },
    )


@login_required
@require_POST
def dokument_zatrzymaj(request, pk):
    """Zatrzymaj przetwarzanie: PENDING znika z kolejki od razu, PROCESSING
    dostaje flagę — worker odrzuci wynik bieżącego przebiegu."""
    document = get_object_or_404(Document, pk=pk)
    outcome = request_stop(document)

    if request.headers.get("HX-Request"):
        # Wywołanie z wiersza tabeli — odśwież sam wiersz, bez komunikatów.
        document.refresh_from_db()
        return render(
            request, "dokumenty/partials/_dokument_wiersz.html", {"d": document}
        )

    if outcome == "cancelled":
        messages.success(
            request, f"Anulowano „{document.original_name}” — zdjęto z kolejki."
        )
    elif outcome == "stopping":
        messages.info(
            request,
            "Zażądano zatrzymania. Bieżące wywołanie silnika dokończy pracę, "
            "ale jego wynik zostanie odrzucony, a dokument oznaczony jako anulowany.",
        )
    else:
        messages.warning(request, "Dokument nie jest w toku — nie ma czego zatrzymać.")
    return redirect(request.POST.get("next") or document.get_absolute_url())


@login_required
@require_POST
def dokument_ponow(request, pk):
    """Ponowne przetworzenie — po błędzie, anulowaniu albo zmianie cache sprawy."""
    document = get_object_or_404(Document, pk=pk)
    if document.status == Document.Status.PROCESSING:
        messages.warning(request, "Dokument jest właśnie przetwarzany.")
    else:
        document.reset_for_retry()
        enqueue_document(document)
        messages.success(request, f"„{document.original_name}” wrócił do kolejki.")
    return redirect(request.POST.get("next") or document.get_absolute_url())


@login_required
@require_POST
def dokument_usun(request, pk):
    document = get_object_or_404(Document, pk=pk)
    name = document.original_name
    if document.status == Document.Status.PENDING and document.task_id:
        remove_from_queue(document.task_id)
    document.delete()  # pliki sprząta sygnał post_delete
    messages.success(request, f"Usunięto „{name}”.")
    return redirect("dokumenty:pulpit")


@login_required
@require_POST
def dokumenty_usun_wybrane(request):
    """Masowe usuwanie zaznaczonych dokumentów (checkboxy w tabeli).
    Dokumenty w trakcie przetwarzania są pomijane — najpierw je zatrzymaj."""
    ids = request.POST.getlist("dokumenty")
    next_url = request.POST.get("next") or reverse("dokumenty:pulpit")

    if not ids:
        messages.warning(request, "Nie zaznaczono żadnych dokumentów.")
        return redirect(next_url)

    selected = Document.objects.filter(pk__in=ids)
    skipped = selected.filter(status=Document.Status.PROCESSING).count()

    deleted = 0
    for document in selected.exclude(status=Document.Status.PROCESSING):
        if document.status == Document.Status.PENDING and document.task_id:
            remove_from_queue(document.task_id)
        document.delete()  # pliki sprząta sygnał post_delete
        deleted += 1

    if deleted:
        messages.success(request, f"Usunięto dokumentów: {deleted}.")
    if skipped:
        messages.warning(
            request,
            f"Pominięto {skipped} dokument(ów) w trakcie przetwarzania — "
            "najpierw je zatrzymaj.",
        )
    return redirect(next_url)


def _serve_file(field_file, filename):
    if not field_file:
        raise Http404("Plik nie istnieje.")
    return FileResponse(field_file.open("rb"), as_attachment=True, filename=filename)


@login_required
def dokument_oryginal(request, pk):
    document = get_object_or_404(Document, pk=pk)
    return _serve_file(document.source_file, document.original_name)


@login_required
def dokument_wynik(request, pk):
    document = get_object_or_404(Document, pk=pk)
    return _serve_file(document.result_file, document.result_name)


# --- Sprawy -----------------------------------------------------------------


@login_required
def sprawa_lista(request):
    cases = Case.objects.annotate(
        documents_total=Count("documents"),
        documents_done=Count(
            "documents", filter=Q(documents__status=Document.Status.DONE)
        ),
    )
    return render(request, "dokumenty/sprawa_lista.html", {"cases": cases})


@login_required
def sprawa_detail(request, pk):
    case = get_object_or_404(Case, pk=pk)
    documents = case.documents.select_related("case")
    persons = sorted(case.persons_cache.items())
    return render(
        request,
        "dokumenty/sprawa_detail.html",
        {"case": case, "documents": documents, "persons": persons},
    )


@login_required
@require_POST
def sprawa_osoba_usun(request, pk):
    """Usuń wpis z wykazu osób sprawy (porządkowo)."""
    case = get_object_or_404(Case, pk=pk)
    person_key = request.POST.get("person", "")
    if remove_person_from_case(case.pk, person_key):
        messages.success(request, f"Usunięto z wykazu: {person_key}.")
    else:
        messages.warning(request, "Takiej osoby nie ma w wykazie.")
    return redirect(case.get_absolute_url())


@login_required
def sprawa_cache_json(request, pk):
    """Wykaz osób sprawy jako plik JSON ({case_id, persons, count})."""
    case = get_object_or_404(Case, pk=pk)
    response = JsonResponse(
        case.cache_as_cli_json(), json_dumps_params={"ensure_ascii": False, "indent": 2}
    )
    response["Content-Disposition"] = (
        f'attachment; filename="persons_cache_{case.pk}.json"'
    )
    return response

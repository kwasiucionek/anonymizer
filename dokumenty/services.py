"""
Warstwa serwisowa — logika niezależna od widoków i zadań.

Uwaga: get_engine() importuje moduł silnika (transformers/torch/morfeusz2
ładują się przy imporcie), więc wolno go wołać wyłącznie z workera django-q2.
"""

import importlib
import re

from django.conf import settings
from django.db import transaction
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

from .models import Case

# --- Silnik -----------------------------------------------------------------


def get_engine():
    """Zaimportuj moduł silnika wskazany w ANONYMIZER_ENGINE (anonymizer.py).

    Moduł przy braku zależności woła sys.exit() — zamieniamy to na zwykły
    wyjątek, żeby worker nie umierał po cichu, tylko oznaczył dokument
    jako FAILED z czytelnym komunikatem.
    """
    try:
        return importlib.import_module(settings.ANONYMIZER_ENGINE)
    except SystemExit as exc:  # silnik: sys.exit(1) przy braku bibliotek
        raise RuntimeError(
            f"Import silnika „{settings.ANONYMIZER_ENGINE}” przerwany — "
            "sprawdź, czy zainstalowano transformers, torch i morfeusz2 "
            "(szczegóły w logu workera)."
        ) from exc


# --- Wykaz osób sprawy --------------------------------------------------------


@transaction.atomic
def merge_persons_into_case(case_pk, persons):
    """Scal wykryte osoby z wykazem sprawy (blokada wiersza — brak wyścigu,
    gdyby kiedyś działało więcej niż jeden worker)."""
    case = Case.objects.select_for_update().get(pk=case_pk)
    merged = {**case.persons_cache, **persons}
    if merged != case.persons_cache:
        case.persons_cache = merged
        case.save(update_fields=["persons_cache", "modified"])
    return len(merged)


@transaction.atomic
def remove_person_from_case(case_pk, person_key):
    """Usuń wpis z wykazu osób sprawy (porządkowo — nowy silnik i tak
    resetuje inicjały per plik, więc wykaz nie zasila przetwarzania)."""
    case = Case.objects.select_for_update().get(pk=case_pk)
    if person_key in case.persons_cache:
        del case.persons_cache[person_key]
        case.save(update_fields=["persons_cache", "modified"])
        return True
    return False


# --- Podgląd wyniku ---------------------------------------------------------

# Nowy silnik pisze xSubst w apostrofach ('J. K.'), stare wyniki mają
# cudzysłowy ("J. K.") — backreference (?P=q) obsługuje oba warianty.
XANON_RE = re.compile(
    r"<xAnon(?:\s+xSubst=(?P<q>[\"'])(?P<subst>.*?)(?P=q))?\s*>(?P<orig>.*?)</xAnon>",
    re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")
TOKEN_RE = re.compile(r"\x00(\d+)\x01")


def render_anonymization_preview(content, limit=20_000):
    """
    Uproszczony podgląd zanonimizowanego dokumentu jako bezpieczny HTML.

    Znaczniki <xAnon xSubst='J. K.'>Jan Kowalski</xAnon> stają się blokami
    redakcyjnymi (podmiana widoczna, oryginał ukryty do podejrzenia),
    pozostałe tagi XML są usuwane, a cała reszta tekstu — escapowana.
    """
    replacements = []

    def stash(match):
        subst = match.group("subst") or "(...)"
        replacements.append((subst, match.group("orig")))
        return f"\x00{len(replacements) - 1}\x01"

    text = XANON_RE.sub(stash, content)
    text = TAG_RE.sub(" ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    truncated = len(text) > limit
    text = text[:limit]
    text = re.sub(r"\x00\d*$", "", text)  # nie zostawiaj przeciętego tokenu

    text = escape(text)

    def unstash(match):
        subst, orig = replacements[int(match.group(1))]
        return format_html(
            '<mark class="redaction"><span class="subst">{}</span>'
            '<span class="orig">{}</span></mark>',
            subst,
            orig,
        )

    html = TOKEN_RE.sub(unstash, text)
    if truncated:
        html += escape("\n… (podgląd skrócony)")
    return mark_safe(html)  # noqa: S308 — treść escapowana powyżej

"""
Testy Web UI.

Prawdziwy silnik NER (torch/transformers/morfeusz2) NIE jest tu potrzebny —
pipeline zadania testujemy na sztucznym module silnika o identycznym API
(anonymizer.py: SimpleAnonymizer + process_file), podstawianym przez
ANONYMIZER_ENGINE. To samo przełączenie pozwala testować bez GPU.
"""

import shutil
import sys
import tempfile
import types
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import UploadForm
from .models import Case, Document
from .services import (
    merge_persons_into_case,
    remove_person_from_case,
    render_anonymization_preview,
)
from .tasks import (
    _collect_persons,
    process_document,
    remove_from_queue,
    request_stop,
)

FAKE_ENGINE = "fake_engine_for_tests"


def _install_fake_engine():
    """Moduł o API zgodnym z anonymizer.py, bez ciężkich zależności."""
    module = types.ModuleType(FAKE_ENGINE)

    class SimpleAnonymizer:
        def __init__(self, model_path, debug=False, letter_shift=0):
            self.model_path = model_path
            self.debug = debug
            self.letter_shift = letter_shift
            self.entity_counter = {}
            self.initials_counter = {}

        def reset_cache(self):
            self.entity_counter = {}
            self.initials_counter = {}

    def process_file(input_path, anonymizer, output_path):
        anonymizer.reset_cache()  # jak w prawdziwym silniku
        anonymizer.entity_counter["jan kowalski"] = "J. K. (1)"
        anonymizer.initials_counter["J. K."] = 1
        output_path.write_text(
            "<w><xAnon xSubst='J. K.'>Jana Kowalskiego</xAnon> pozwał "
            "<xAnon xSubst='(...)'>92010112345</xAnon></w>",
            encoding="utf-8",
        )
        return True, 2

    module.SimpleAnonymizer = SimpleAnonymizer
    module.process_file = process_file
    sys.modules[FAKE_ENGINE] = module


_install_fake_engine()

TMP_MEDIA = tempfile.mkdtemp(prefix="anon-test-media-")


def make_document(case=None, name="wyrok.xml", content=b"<w>Jan Kowalski</w>", **kw):
    return Document.objects.create(
        case=case,
        original_name=name,
        source_file=SimpleUploadedFile(name, content),
        **kw,
    )


@override_settings(MEDIA_ROOT=TMP_MEDIA, ANONYMIZER_ENGINE=FAKE_ENGINE)
class ProcessDocumentTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def test_pipeline_xml_konczy_sie_sukcesem_i_scala_wykaz_sprawy(self):
        case = Case.objects.create(case_id="I C 123/24")
        document = make_document(case=case)

        result = process_document(document.pk)

        document.refresh_from_db()
        case.refresh_from_db()
        self.assertEqual(document.status, Document.Status.DONE)
        self.assertEqual(document.entity_count, 2)
        self.assertEqual(document.person_count, 1)
        # Storage może dokleić sufiks przy kolizji nazw — sprawdzamy wzorzec.
        self.assertRegex(document.result_file.name, r"wyrok_anon\S*\.xml$")
        self.assertIn("xAnon", document.result_file.read().decode())
        # „(1)” zdjęte z unikalnych inicjałów, jak w post_process silnika.
        self.assertEqual(case.persons_cache, {"jan kowalski": "J. K."})
        self.assertEqual(result, {"total": 2, "persons": 1})

    def test_istniejacy_wykaz_sprawy_przezywa_scalanie(self):
        case = Case.objects.create(
            case_id="II K 9/24", persons_cache={"anna nowak": "A. N."}
        )
        document = make_document(case=case)

        process_document(document.pk)

        case.refresh_from_db()
        # Stare wpisy przetrwały, nowe doszły.
        self.assertEqual(
            case.persons_cache,
            {"anna nowak": "A. N.", "jan kowalski": "J. K."},
        )

    def test_dokument_w_innym_statusie_jest_pomijany(self):
        document = make_document()
        Document.objects.filter(pk=document.pk).update(
            status=Document.Status.PROCESSING
        )
        self.assertEqual(process_document(document.pk), {"skipped": True})

    def test_blad_silnika_ustawia_status_failed(self):
        document = make_document()
        broken = sys.modules[FAKE_ENGINE]
        with patch.object(broken, "process_file", side_effect=RuntimeError("kaboom")):
            with self.assertRaises(RuntimeError):
                process_document(document.pk)
        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.FAILED)
        self.assertIn("kaboom", document.error_message)

    def test_process_file_zwracajacy_false_daje_failed(self):
        document = make_document()
        broken = sys.modules[FAKE_ENGINE]
        with patch.object(broken, "process_file", return_value=(False, 0)):
            with self.assertRaises(RuntimeError):
                process_document(document.pk)
        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.FAILED)

    def test_flaga_zatrzymania_przed_silnikiem_anuluje_bez_wyniku(self):
        document = make_document(cancel_requested=True)

        result = process_document(document.pk)

        document.refresh_from_db()
        self.assertEqual(result, {"cancelled": True})
        self.assertEqual(document.status, Document.Status.CANCELLED)
        self.assertFalse(document.cancel_requested)
        self.assertFalse(document.result_file)

    def test_flaga_zatrzymania_w_trakcie_odrzuca_wynik(self):
        """Żądanie zatrzymania nadchodzi, gdy silnik już mieli dokument —
        wynik przebiegu ma zostać odrzucony, wykaz sprawy nietknięty."""
        case = Case.objects.create(case_id="III K 5/26")
        document = make_document(case=case)
        fake = sys.modules[FAKE_ENGINE]
        original = fake.process_file

        def stop_mid_run(input_path, anonymizer, output_path):
            Document.objects.filter(pk=document.pk).update(cancel_requested=True)
            return original(input_path, anonymizer, output_path)

        with patch.object(fake, "process_file", side_effect=stop_mid_run):
            result = process_document(document.pk)

        document.refresh_from_db()
        case.refresh_from_db()
        self.assertEqual(result, {"cancelled": True})
        self.assertEqual(document.status, Document.Status.CANCELLED)
        self.assertFalse(document.result_file)
        self.assertEqual(case.persons_cache, {})  # scalanie pominięte

    def test_dokument_usuniety_w_trakcie_nie_jest_wskrzeszany(self):
        document = make_document()
        pk = document.pk
        fake = sys.modules[FAKE_ENGINE]
        original = fake.process_file

        def delete_mid_run(input_path, anonymizer, output_path):
            Document.objects.filter(pk=pk).delete()
            return original(input_path, anonymizer, output_path)

        with patch.object(fake, "process_file", side_effect=delete_mid_run):
            result = process_document(pk)

        self.assertEqual(result, {"skipped": "deleted"})
        self.assertFalse(Document.objects.filter(pk=pk).exists())


class CollectPersonsTests(TestCase):
    """Zbieranie wykazu z entity_counter silnika (zdejmowanie liczników)."""

    def test_licznik_zdjety_tylko_z_unikalnych_inicjalow(self):
        fake = sys.modules[FAKE_ENGINE]
        anonymizer = fake.SimpleAnonymizer("model")
        anonymizer.entity_counter = {
            "jan kowalski": "J. K. (1)",
            "janina kowalska": "J. K. (2)",
            "anna nowak": "A. N. (1)",
        }
        anonymizer.initials_counter = {"J. K.": 2, "A. N.": 1}

        persons = _collect_persons(anonymizer)

        self.assertEqual(
            persons,
            {
                "jan kowalski": "J. K. (1)",
                "janina kowalska": "J. K. (2)",
                "anna nowak": "A. N.",
            },
        )


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class StopRequestTests(TestCase):
    """request_stop + zdejmowanie zadań z kolejki ORM django-q2."""

    def test_pending_znika_z_kolejki_orm(self):
        from django_q.models import OrmQ
        from django_q.tasks import async_task

        document = make_document()
        task_id = async_task("dokumenty.tasks.process_document", document.pk)
        Document.objects.filter(pk=document.pk).update(task_id=task_id)
        document.refresh_from_db()
        self.assertEqual(OrmQ.objects.count(), 1)

        outcome = request_stop(document)

        document.refresh_from_db()
        self.assertEqual(outcome, "cancelled")
        self.assertEqual(document.status, Document.Status.CANCELLED)
        self.assertEqual(OrmQ.objects.count(), 0)
        self.assertIsNotNone(document.finished_at)

    def test_processing_dostaje_flage(self):
        document = make_document()
        Document.objects.filter(pk=document.pk).update(
            status=Document.Status.PROCESSING
        )
        outcome = request_stop(document)

        document.refresh_from_db()
        self.assertEqual(outcome, "stopping")
        self.assertEqual(document.status, Document.Status.PROCESSING)
        self.assertTrue(document.cancel_requested)

    def test_zakonczony_to_noop(self):
        document = make_document()
        Document.objects.filter(pk=document.pk).update(status=Document.Status.DONE)
        self.assertEqual(request_stop(document), "noop")

    def test_remove_from_queue_zdejmuje_tylko_wskazane_zadanie(self):
        from django_q.models import OrmQ
        from django_q.tasks import async_task

        keep = async_task("dokumenty.tasks.process_document", 111)
        drop = async_task("dokumenty.tasks.process_document", 222)
        self.assertEqual(OrmQ.objects.count(), 2)

        self.assertTrue(remove_from_queue(drop))
        self.assertFalse(remove_from_queue("nie-ma-takiego"))

        remaining = [q.task.get("id") for q in OrmQ.objects.all()]
        self.assertEqual(remaining, [keep])


class PreviewRendererTests(TestCase):
    def test_xanon_w_apostrofach_staje_sie_blokiem_redakcyjnym(self):
        """Nowy silnik pisze xSubst='...' w apostrofach."""
        html = render_anonymization_preview(
            "<p>Powód <xAnon xSubst='J. K.'>Jan Kowalski</xAnon> & spółka</p>"
        )
        self.assertIn('<mark class="redaction">', html)
        self.assertIn('<span class="subst">J. K.</span>', html)
        self.assertIn('<span class="orig">Jan Kowalski</span>', html)
        self.assertIn("&amp; spółka", html)  # escaping działa
        self.assertNotIn("<p>", html)  # obce tagi usunięte

    def test_xanon_w_cudzyslowach_nadal_dziala(self):
        """Stare wyniki (v2/v3) mają cudzysłowy — wsteczna zgodność."""
        html = render_anonymization_preview('<xAnon xSubst="A. N.">Anna</xAnon>')
        self.assertIn('<span class="subst">A. N.</span>', html)

    def test_xanon_z_licznikiem_inicjalow(self):
        html = render_anonymization_preview(
            "<xAnon xSubst='J. K. (2)'>Janina Kowalska</xAnon>"
        )
        self.assertIn('<span class="subst">J. K. (2)</span>', html)

    def test_xanon_bez_xsubst_dostaje_domyslna_podmiane(self):
        html = render_anonymization_preview("<xAnon>92010112345</xAnon>")
        self.assertIn('<span class="subst">(...)</span>', html)

    def test_dlugi_tekst_jest_przycinany(self):
        html = render_anonymization_preview("a" * 30_000, limit=100)
        self.assertIn("podgląd skrócony", html)


class UploadFormTests(TestCase):
    def _form(self, filename, content=b"tekst"):
        return UploadForm(
            data={},
            files={"files": [SimpleUploadedFile(filename, content)]},
        )

    def test_odrzuca_niedozwolone_rozszerzenie(self):
        form = self._form("wyrok.pdf")
        self.assertFalse(form.is_valid())
        self.assertIn("dozwolone", str(form.errors["files"]))

    def test_odrzuca_plik_spoza_utf8(self):
        form = self._form("wyrok.txt", content=b"\xff\xfe\x00zle")
        self.assertFalse(form.is_valid())
        self.assertIn("UTF-8", str(form.errors["files"]))

    def test_przyjmuje_poprawny_xml(self):
        form = self._form("wyrok.xml", content="<w>zażółć</w>".encode())
        self.assertTrue(form.is_valid(), form.errors)


@override_settings(MEDIA_ROOT=TMP_MEDIA, ANONYMIZER_ENGINE=FAKE_ENGINE)
class ViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("krzysztof", password="tajne-haslo-123")

    def test_pulpit_wymaga_logowania(self):
        response = self.client.get(reverse("dokumenty:pulpit"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    @patch("dokumenty.views.enqueue_document")
    def test_upload_tworzy_dokumenty_i_kolejkuje(self, mock_enqueue):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("dokumenty:pulpit"),
            {
                "files": [
                    SimpleUploadedFile("a.xml", b"<w>x</w>"),
                    SimpleUploadedFile("b.txt", b"tekst"),
                ],
                "new_case_id": "III RC 7/26",
            },
        )
        self.assertRedirects(response, reverse("dokumenty:pulpit"))
        self.assertEqual(Document.objects.count(), 2)
        self.assertEqual(Case.objects.get().case_id, "III RC 7/26")
        self.assertEqual(mock_enqueue.call_count, 2)

    def test_pobranie_wyniku_gdy_brak_pliku_daje_404(self):
        self.client.force_login(self.user)
        document = make_document()
        response = self.client.get(
            reverse("dokumenty:dokument-wynik", args=[document.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_detail_pokazuje_podglad_i_surowy_wynik(self):
        self.client.force_login(self.user)
        document = make_document()
        process_document(document.pk)

        response = self.client.get(reverse("dokumenty:dokument", args=[document.pk]))

        self.assertContains(response, 'id="panel-preview"')
        self.assertContains(response, 'id="panel-raw"')
        # Surowy wynik jest w <pre> zescapowany — tagi xAnon widać jako tekst.
        self.assertContains(response, "&lt;xAnon")
        self.assertContains(response, "Kopiuj do schowka")

    def test_zatrzymanie_pending_przez_widok(self):
        self.client.force_login(self.user)
        document = make_document()

        response = self.client.post(
            reverse("dokumenty:dokument-zatrzymaj", args=[document.pk])
        )

        document.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(document.status, Document.Status.CANCELLED)

    def test_zatrzymanie_htmx_zwraca_wiersz(self):
        self.client.force_login(self.user)
        document = make_document()
        Document.objects.filter(pk=document.pk).update(
            status=Document.Status.PROCESSING
        )

        response = self.client.post(
            reverse("dokumenty:dokument-zatrzymaj", args=[document.pk]),
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, f'id="dok-{document.pk}"')
        self.assertContains(response, "Przerywanie…")

    def test_masowe_usuwanie_pomija_processing(self):
        self.client.force_login(self.user)
        done = make_document(name="a.xml")
        Document.objects.filter(pk=done.pk).update(status=Document.Status.DONE)
        pending = make_document(name="b.xml")
        processing = make_document(name="c.xml")
        Document.objects.filter(pk=processing.pk).update(
            status=Document.Status.PROCESSING
        )

        response = self.client.post(
            reverse("dokumenty:dokumenty-usun-wybrane"),
            {"dokumenty": [done.pk, pending.pk, processing.pk]},
        )

        self.assertEqual(response.status_code, 302)
        remaining = list(Document.objects.values_list("pk", flat=True))
        self.assertEqual(remaining, [processing.pk])

    def test_masowe_usuwanie_bez_zaznaczenia(self):
        self.client.force_login(self.user)
        make_document()
        response = self.client.post(reverse("dokumenty:dokumenty-usun-wybrane"), {})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Document.objects.count(), 1)

    def test_eksport_wykazu_json(self):
        self.client.force_login(self.user)
        case = Case.objects.create(
            case_id="I C 1/26", persons_cache={"jan kowalski": "J. K."}
        )
        response = self.client.get(reverse("dokumenty:sprawa-cache", args=[case.pk]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["case_id"], "I C 1/26")
        self.assertEqual(payload["persons"], {"jan kowalski": "J. K."})
        self.assertEqual(payload["count"], 1)


class CaseCacheServiceTests(TestCase):
    def test_merge_dokleja_bez_kasowania(self):
        case = Case.objects.create(case_id="X 1/1", persons_cache={"a b": "A. B."})
        merge_persons_into_case(case.pk, {"c d": "C. D."})
        case.refresh_from_db()
        self.assertEqual(case.persons_cache, {"a b": "A. B.", "c d": "C. D."})

    def test_remove_usuwa_tylko_istniejace(self):
        case = Case.objects.create(case_id="X 2/2", persons_cache={"a b": "A. B."})
        self.assertTrue(remove_person_from_case(case.pk, "a b"))
        self.assertFalse(remove_person_from_case(case.pk, "nie ma"))
        case.refresh_from_db()
        self.assertEqual(case.persons_cache, {})

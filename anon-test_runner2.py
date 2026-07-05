#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skrypt testowy dla minimalnego anonimizatora.
Dostosowany do nowej wersji używającej tylko NER + Stanza.
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple

# --- KONFIGURACJA ---
MODEL_PATH = "./anon-v3a/final"  # <-- ZMIEŃ TĘ ŚCIEŻKĘ NA SWOJĄ
TEST_FILES_DIR = Path("anon-test") / "court"
ANONYMIZER_SCRIPT = "anonymizer-v2-morf.py"  # Nazwa pliku z minimalnym anonimizatorem
# --- Koniec konfiguracji ---

# Importuj klasę z minimalnego anonimizatora
try:
    # Dynamiczny import na podstawie nazwy pliku
    import importlib.util

    spec = importlib.util.spec_from_file_location("Anonymizer", ANONYMIZER_SCRIPT)
    minimal_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(minimal_module)
    SimpleAnonymizer = minimal_module.SimpleAnonymizer
    print(f"✅ Zaimportowano SimpleAnonymizer z {ANONYMIZER_SCRIPT}")
except Exception as e:
    print(f"❌ Błąd importu: {e}")
    print(f"Upewnij się, że plik '{ANONYMIZER_SCRIPT}' jest w tym samym folderze.")
    sys.exit(1)

# Wzorzec do parsowania testów
TEST_CASE_PATTERN = re.compile(
    r'ANONYMIZE\("(?P<input>.*?)"\)\s*=\s*(?:"(?P<expected_quoted>.*?)"|(?P<expected_nochange>NO_CHANGE))',
    re.DOTALL,
)


def parse_test_file(file_path: Path) -> List[Tuple[str, List[str]]]:
    """Parsuj plik testowy i wyciągnij przypadki testowe."""
    test_cases = []
    content = file_path.read_text(encoding="utf-8")

    for match in TEST_CASE_PATTERN.finditer(content):
        # Wyciągnij tekst wejściowy - ZACHOWAJ ORYGINALNE FORMATOWANIE
        # Tylko usuń nadmiarowe białe znaki, ale zachowaj pojedyncze spacje
        input_raw = match.group("input")
        # Zamień wielokrotne spacje/taby/nowe linie na pojedyncze spacje
        input_text = re.sub(r"\s+", " ", input_raw).strip()

        # Sprawdź oczekiwany wynik
        expected_quoted = match.group("expected_quoted")
        expected_nochange = match.group("expected_nochange")

        if expected_nochange:
            expected_outputs = ["NO_CHANGE"]
        else:
            # Obsłuż możliwe alternatywy rozdzielone przez " OR "
            # Również zachowaj formatowanie w oczekiwanych wynikach
            expected_outputs = [
                re.sub(r"\s+", " ", e).strip() for e in expected_quoted.split('" OR "')
            ]

        test_cases.append((input_text, expected_outputs))

    return test_cases


def normalize_for_comparison(text: str) -> str:
    """
    Normalizuj tekst do porównania:
    - Usuń wszystkie znaki interpunkcyjne
    - Usuń wszystkie białe znaki
    - Pozostaw tylko litery i cyfry
    """
    # Usuń wszystkie znaki oprócz liter (w tym polskie) i cyfr
    text = re.sub(r"[^\w]", "", text, flags=re.UNICODE)
    return text


def convert_xml_to_plain(xml_text: str) -> str:
    """Konwertuj XML z tagami <xAnon> na czysty tekst z podstawieniami."""
    pattern = re.compile(r'<xAnon xSubst="(?P<subst>.*?)">(?P<text>.*?)</xAnon>')
    return pattern.sub(lambda m: m.group("subst"), xml_text)


def run_tests(debug_mode: bool = False):
    """Główna funkcja uruchamiająca testy."""

    # Sprawdź ścieżkę modelu
    if not Path(MODEL_PATH).exists():
        print("\n" + "!" * 80)
        print(f" BŁĄD: Model nie znaleziony w: {MODEL_PATH}")
        print(f" Zmień 'MODEL_PATH' w skrypcie na poprawną ścieżkę do modelu!")
        print("!" * 80)
        return

    # Sprawdź folder z testami
    if not TEST_FILES_DIR.is_dir():
        print(f"❌ Błąd: Folder z testami nie został znaleziony: {TEST_FILES_DIR}")
        return

    print("=" * 80)
    print("🧪 TEST MINIMALNEGO ANONIMIZATORA")
    print("=" * 80)
    print(f"📁 Model: {MODEL_PATH}")
    print(f"📁 Testy: {TEST_FILES_DIR}")
    if debug_mode:
        print(f"🔍 Tryb DEBUG: WŁĄCZONY")
    print("-" * 80)

    # Inicjalizuj anonimizator
    print("🔧 Inicjalizuję anonimizator...")
    anonymizer = SimpleAnonymizer(MODEL_PATH, debug=debug_mode)
    print("✅ Anonimizator gotowy do testów")
    print("=" * 80)

    # Znajdź pliki testowe
    test_files = sorted(TEST_FILES_DIR.glob("*.test"))
    if not test_files:
        print(f"❌ Nie znaleziono plików .test w folderze {TEST_FILES_DIR}")
        return

    total_passed = 0
    total_failed = 0

    # Przetwarzaj każdy plik testowy
    for test_file in test_files:
        print(f"\n📄 Plik testowy: {test_file.name}")
        print("-" * 80)

        test_cases = parse_test_file(test_file)
        if not test_cases:
            print("  ⚠️ Brak przypadków testowych w pliku.")
            continue

        file_passed = 0
        file_failed = 0

        # Testuj każdy przypadek
        for i, (input_text, expected_outputs) in enumerate(test_cases, 1):
            # RESET CACHE przed każdym testem - każdy test zaczyna od (1)
            anonymizer.reset_cache()

            is_passed = False

            # Anonimizuj
            anon_xml_output, count = anonymizer.anonymize(input_text)

            # DODAJ TO: Post-processing aby usunąć (1) dla pojedynczych wystąpień
            anon_xml_output = anonymizer.post_process(anon_xml_output)

            # Konwertuj wynik do czystego tekstu
            plain_output = convert_xml_to_plain(anon_xml_output)

            # Sprawdź czy test przeszedł
            if expected_outputs == ["NO_CHANGE"]:
                # Test NO_CHANGE - sprawdź czy nie było żadnych anonimizacji
                is_passed = count == 0
                if debug_mode and not is_passed:
                    print(
                        f"\n  🔍 DEBUG Test #{i}: NO_CHANGE expected but found {count} entities"
                    )
            else:
                # Porównaj z oczekiwanymi wynikami
                actual_normalized = normalize_for_comparison(plain_output)
                for expected in expected_outputs:
                    if actual_normalized == normalize_for_comparison(expected):
                        is_passed = True
                        break

            if is_passed:
                file_passed += 1

            else:
                file_failed += 1
                print(f"  ❌ Test #{i:3d}: FAILED")
                print(f"     Wejście:    '{input_text}'")
                print(f"     Otrzymano:  '{plain_output}'")
                print(f"     Oczekiwano: '{expected_outputs[0]}'")

                if debug_mode:
                    print(f"     XML output: '{anon_xml_output}'")
                    print(f"     Znaleziono {count} encji")
                    # Pokaż dokładnie gdzie są różnice
                    print(f"     Normalizowane otrzymano:  '{actual_normalized}'")
                    print(
                        f"     Normalizowane oczekiwano: '{normalize_for_comparison(expected_outputs[0])}'"
                    )

        # Podsumowanie dla pliku
        print("-" * 80)
        if file_failed == 0:
            print(
                f"  ✅ SUKCES: Wszystkie {file_passed}/{len(test_cases)} testy przeszły"
            )
        else:
            print(
                f"  📊 Wynik: {file_passed} zaliczonych, {file_failed} niezaliczonych"
            )

        total_passed += file_passed
        total_failed += file_failed

    # Podsumowanie końcowe
    print("\n" + "=" * 80)
    print("🏁 PODSUMOWANIE KOŃCOWE")
    print("=" * 80)
    print(f"  📁 Przetworzono plików: {len(test_files)}")
    print(f"  📝 Łączna liczba testów: {total_passed + total_failed}")
    print(f"  ✅ ZALICZONE: {total_passed}")
    print(f"  ❌ NIEZALICZONE: {total_failed}")

    if total_failed == 0 and total_passed > 0:
        print("\n  🎉 WSZYSTKIE TESTY PRZESZŁY POMYŚLNIE! 🎉")
    elif total_passed > 0:
        success_rate = (total_passed / (total_passed + total_failed)) * 100
        print(f"\n  📊 Wskaźnik sukcesu: {success_rate:.1f}%")

    print("=" * 80)


def test_single_sentence(model_path: str = MODEL_PATH):
    """Funkcja do szybkiego testowania pojedynczych zdań."""
    print("\n" + "=" * 80)
    print("🔬 TEST POJEDYNCZYCH ZDAŃ")
    print("=" * 80)

    anonymizer = SimpleAnonymizer(model_path)

    test_sentences = [
        "Jan Kowalski z Warszawy mieszka przy ul. Pięknej 15.",
        "Sędzia Anna Nowak ustaliła następujące fakty",
        "reprezentowanego przez radcę prawnego Annę Wiśniewską",
        "Świadek Krzysztof Wasiucionek, urodzony 23.01.1985 r.",
        "Jan Nowak-Jeziorański, Janina Kowalska, Zenon Nowak z domu Kowalska",
        "W IMIENIU RZECZYPOSPOLITEJ POLSKIEJ",
        "WYROK",
    ]

    for text in test_sentences:
        result, count = anonymizer.anonymize(text)
        plain = convert_xml_to_plain(result)

        print(f"\nWejście: {text}")
        print(f"Wynik:   {plain}")
        print(f"Encje:   {count}")
        if count > 0:
            print(f"XML:     {result}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    # Sprawdź argumenty
    if len(sys.argv) > 1:
        if sys.argv[1] == "--test-single":
            # Tryb testowania pojedynczych zdań
            test_single_sentence()
        elif sys.argv[1] == "--debug":
            # Tryb debug dla testów
            run_tests(debug_mode=True)
        elif sys.argv[1] == "--help":
            print("Użycie:")
            print(f"  python {sys.argv[0]}              - uruchom wszystkie testy")
            print(
                f"  python {sys.argv[0]} --debug       - uruchom testy z debugowaniem"
            )
            print(f"  python {sys.argv[0]} --test-single - testuj pojedyncze zdania")
        else:
            print(f"Nieznany argument: {sys.argv[1]}")
            print(f"Użyj --help aby zobaczyć dostępne opcje")
    else:
        # Domyślnie uruchom wszystkie testy
        run_tests(debug_mode=False)

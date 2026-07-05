#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Anonimizator dokumentów - WERSJA Z MORFEUSZEM 2
Używa wyłącznie Morfeusz 2 do lemmatyzacji i normalizacji
"""

import sys
import re
import json
import time
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from functools import lru_cache

try:
    from transformers import AutoTokenizer, AutoModelForTokenClassification
    import torch
except ImportError:
    print("❌ Brak bibliotek. Zainstaluj:")
    print("   pip install transformers torch")
    sys.exit(1)

try:
    import morfeusz2

    morf = morfeusz2.Morfeusz()
    print("✅ Morfeusz 2 załadowany")
except ImportError:
    print("❌ Morfeusz 2 jest wymagany!")
    print("   Zainstaluj: pip install morfeusz2")
    sys.exit(1)

POLISH_TO_BASIC = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "ACELNOSZZacelnoszz")


class PolishNERInference:
    """Dokładna kopia z kodu testowego."""

    def __init__(self, model_path: str, device: str = None, debug: bool = False):
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.model = AutoModelForTokenClassification.from_pretrained(model_path)
        self.debug = debug

        if device is None:
            device = "cuda:0" if torch.cuda.is_available() else "cpu"

        self.model.to(device)
        self.model.eval()
        self.device = device

        with open(f"{model_path}/label_config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
            self.id2label = {int(k): v for k, v in config["id2label"].items()}

    def tokenize_text(self, text: str) -> Tuple[List[str], List[Tuple[int, int]]]:
        """Tokenizuj tekst na słowa z mapą pozycji."""
        words = []
        word_positions = []

        pattern = r"([a-ząćęłńóśźż]{1,4}\.|[\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+(?:-[\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+)*|[^\w\s])"

        for match in re.finditer(pattern, text):
            words.append(match.group())
            word_positions.append((match.start(), match.end()))

        return words, word_positions

    def _clean_entity_boundaries(
        self, text: str, start: int, end: int
    ) -> Tuple[int, int]:
        """Czyści granice encji ze spacji."""
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        return start, end

    def predict(self, text: str, confidence_threshold: float = 0.7) -> List[Dict]:
        """Przewiduj entitety na tekście."""
        words, word_positions = self.tokenize_text(text)

        if not words:
            return []

        encoding = self.tokenizer(
            words,
            is_split_into_words=True,
            truncation=True,
            padding=True,
            max_length=512,
            return_tensors="pt",
        )

        with torch.no_grad():
            outputs = self.model(
                input_ids=encoding["input_ids"].to(self.device),
                attention_mask=encoding["attention_mask"].to(self.device),
            )
            logits = outputs.logits

        predictions = torch.argmax(logits, dim=-1)[0].cpu()
        word_ids = encoding.word_ids()

        word_predictions = {}
        word_confidence = {}

        for token_idx, word_idx in enumerate(word_ids):
            if word_idx is None:
                continue

            if word_idx not in word_predictions:
                pred_label = self.id2label[predictions[token_idx].item()]
                confidence = torch.softmax(logits[0, token_idx], dim=-1).max().item()
                word_predictions[word_idx] = pred_label
                word_confidence[word_idx] = confidence

        if self.debug:
            print("\n=== DEBUG PREDYKCJI ===")
            for word_idx, word in enumerate(words):
                pred = word_predictions.get(word_idx, "O")
                conf = word_confidence.get(word_idx, 0.0)
                start, end = word_positions[word_idx]
                print(
                    f"  Słowo {word_idx:2d}: '{word:20s}' -> {pred:12s} (conf: {conf:.3f}) [{start}:{end}]"
                )
            print("======================\n")

        results = []
        current_entity = None
        current_indices = []
        current_confidence = []

        for word_idx in range(len(words)):
            pred_label = word_predictions.get(word_idx, "O")
            confidence = word_confidence.get(word_idx, 1.0)

            if pred_label == "O" or confidence < confidence_threshold:
                if current_entity and current_indices:
                    start_idx = word_positions[current_indices[0]][0]
                    end_idx = word_positions[current_indices[-1]][1]
                    start_idx, end_idx = self._clean_entity_boundaries(
                        text, start_idx, end_idx
                    )
                    entity_text = text[start_idx:end_idx]

                    results.append(
                        {
                            "word": entity_text,
                            "label": current_entity,
                            "confidence": sum(current_confidence)
                            / len(current_confidence),
                            "start": start_idx,
                            "end": end_idx,
                        }
                    )
                    current_entity = None
                    current_indices = []
                    current_confidence = []

            elif pred_label.startswith("B-"):
                if current_entity and current_indices:
                    start_idx = word_positions[current_indices[0]][0]
                    end_idx = word_positions[current_indices[-1]][1]
                    start_idx, end_idx = self._clean_entity_boundaries(
                        text, start_idx, end_idx
                    )
                    entity_text = text[start_idx:end_idx]

                    results.append(
                        {
                            "word": entity_text,
                            "label": current_entity,
                            "confidence": sum(current_confidence)
                            / len(current_confidence),
                            "start": start_idx,
                            "end": end_idx,
                        }
                    )

                entity_type = pred_label[2:]
                current_entity = entity_type
                current_indices = [word_idx]
                current_confidence = [confidence]

            elif pred_label.startswith("I-"):
                entity_type = pred_label[2:]
                if current_entity == entity_type:
                    current_indices.append(word_idx)
                    current_confidence.append(confidence)
                else:
                    if current_entity and current_indices:
                        start_idx = word_positions[current_indices[0]][0]
                        end_idx = word_positions[current_indices[-1]][1]
                        start_idx, end_idx = self._clean_entity_boundaries(
                            text, start_idx, end_idx
                        )
                        entity_text = text[start_idx:end_idx]

                        results.append(
                            {
                                "word": entity_text,
                                "label": current_entity,
                                "confidence": sum(current_confidence)
                                / len(current_confidence),
                                "start": start_idx,
                                "end": end_idx,
                            }
                        )

                    current_entity = entity_type
                    current_indices = [word_idx]
                    current_confidence = [confidence]

        if current_entity and current_indices:
            start_idx = word_positions[current_indices[0]][0]
            end_idx = word_positions[current_indices[-1]][1]
            start_idx, end_idx = self._clean_entity_boundaries(text, start_idx, end_idx)
            entity_text = text[start_idx:end_idx]

            results.append(
                {
                    "word": entity_text,
                    "label": current_entity,
                    "confidence": sum(current_confidence) / len(current_confidence),
                    "start": start_idx,
                    "end": end_idx,
                }
            )

        results = [r for r in results if r["confidence"] >= confidence_threshold]
        return results


def get_best_lemma(word: str) -> str:
    """
    Wybiera najlepszy lemmat dla pojedynczego słowa, preferując
    rzeczowniki w mianowniku i liczbie pojedynczej.
    """
    try:
        analysis = morf.analyse(word)
        subst_interpretations = []

        for _, _, (orth, lemma, tag, _, _) in analysis:
            if tag.startswith("subst"):
                lemma_base = lemma.split(":")[0].lower()
                score = 0
                if ":sg:" in tag:
                    score += 2
                if ":nom:" in tag:
                    score += 1
                subst_interpretations.append((lemma_base, score))

        if not subst_interpretations:
            if analysis:
                return analysis[0][2][1].split(":")[0].lower()
            return word.lower()

        subst_interpretations.sort(key=lambda x: x[1], reverse=True)
        return subst_interpretations[0][0]

    except Exception:
        return word.lower()


def _get_interpretations(word: str) -> List[Dict]:
    """
    Analizuje słowo i zwraca listę jego możliwych
    interpretacji gramatycznych jako słowniki.
    """
    interpretations = []
    analysis = morf.analyse(word)

    for _, _, (orth, lemma, tag, _, _) in analysis:
        if not tag.startswith("subst"):
            continue

        lemma_base = lemma.split(":")[0].lower()

        gender = None
        if "m1" in tag:
            gender = "m1"
        elif ":f" in tag:
            gender = "f"

        case = None
        for c in ["nom", "gen", "dat", "acc", "inst", "loc", "voc"]:
            if f":{c}" in tag:
                case = c
                break

        number = "sg" if ":sg:" in tag else "pl"

        if gender and case and number:
            interpretations.append(
                {
                    "lemma": lemma_base,
                    "gender": gender,
                    "case": case,
                    "number": number,
                    "tag": tag,
                }
            )
    return interpretations


def normalize_name_with_morfeusz(name: str) -> Tuple[str, str]:
    """
    Normalizuje imię i nazwisko, wybierając parę interpretacji
    z najlepszym dopasowaniem gramatycznym (rodzaj, przypadek, liczba).
    """
    parts = name.strip().split()
    if len(parts) == 0:
        return ("", "")
    if len(parts) == 1:
        return (get_best_lemma(parts[0]), "")

    first_word, last_word = parts[0], parts[-1]

    first_interps = _get_interpretations(first_word)
    last_interps = _get_interpretations(last_word)

    if not first_interps or not last_interps:
        return (get_best_lemma(first_word), get_best_lemma(last_word))

    best_score = -1
    best_pair = None

    for f_interp in first_interps:
        for l_interp in last_interps:
            score = 0
            if f_interp["gender"] == l_interp["gender"]:
                score += 100
            else:
                score -= 1000

            if f_interp["case"] == l_interp["case"]:
                score += 50

            if f_interp["number"] == l_interp["number"]:
                score += 20
                if f_interp["number"] == "sg":
                    score += 10

            if f_interp["case"] == "nom":
                score += 5

            if score > best_score:
                best_score = score
                best_pair = (f_interp["lemma"], l_interp["lemma"])

    if best_pair:
        return best_pair

    return (get_best_lemma(first_word), get_best_lemma(last_word))


@lru_cache(maxsize=500)
def normalize_names_for_comparison(name: str) -> Tuple[str, str]:
    """
    Normalizuje imię i nazwisko dla porównania.
    """
    name_clean = name.strip()

    prefixes_to_remove = [
        r"\b(?:dr\.?|prof\.?|mgr\.?|inż\.?|hab\.?|adw\.?|r\.pr\.?)\s+",
        r"\b(?:pan|pani|minister|sędzi[aeo]|radcy?|radcę|świadek|mał\.?|małoletniego?)\s+",
    ]
    for pattern in prefixes_to_remove:
        name_clean = re.sub(pattern, "", name_clean, flags=re.IGNORECASE)

    name_clean = name_clean.strip()
    if not name_clean:
        return ("", "")

    parts = name_clean.split()
    if not parts:
        return ("", "")

    return normalize_name_with_morfeusz(name_clean)


def quick_debug_specific_name(name1: str, name2: str):
    """Szybki debug dla konkretnego problemu."""
    print("\n" + "=" * 70)
    print(f"ANALIZA: '{name1}' vs '{name2}'")
    print("=" * 70)

    for name in [name1, name2]:
        print(f"\n>>> '{name}'")

        try:
            parts = name.strip().split()
            print("    Możliwe interpretacje (rzeczowniki):")
            for part in parts:
                interps = _get_interpretations(part)
                if interps:
                    print(f"      '{part}':")
                    for i in interps[:5]:
                        print(
                            f"        - {i['lemma']:15s} ({i['gender']}, {i['case']}, {i['number']})"
                        )
                else:
                    print(f"      '{part}': Brak interpretacji rzeczownikowych.")

        except Exception as e:
            print(f"    Błąd Morfeusza: {e}")

        normalized = normalize_names_for_comparison(name)
        print(f"    → Normalized: {normalized}")

    norm1 = normalize_names_for_comparison(name1)
    norm2 = normalize_names_for_comparison(name2)

    if norm1 == norm2:
        print(f"\n✅ IDENTYCZNE: {norm1}")
    else:
        print(f"\n❌ RÓŻNE: {norm1} != {norm2}")

    print("=" * 70 + "\n")


class SimpleAnonymizer:
    """Minimalny anonimizator używający tylko NER."""

    def __init__(self, model_path: str, debug: bool = False, letter_shift: int = 0):
        print(f"🔧 Ładuję model z: {model_path}")
        self.ner = PolishNERInference(model_path, debug=debug)
        print(f"✅ Model załadowany")
        self.debug = debug
        self.letter_shift = letter_shift

        self.entity_counter = {}
        self.initials_counter = {}

        if letter_shift > 0:
            print(f"🔀 Przesunięcie inicjałów: {letter_shift}")

    def _get_base_letter_ord(self, char: str) -> int:
        """Pomocnicza funkcja do generowania inicjałów."""
        return ord(char.upper().translate(POLISH_TO_BASIC)) - ord("A")

    def _make_substitute(self, original: str, entity_type: str) -> str:
        """Generuj zamiennik dla encji."""
        if entity_type == "PERSON":
            return self._person_substitute(original)
        elif entity_type == "LOCATION":
            return self._location_substitute(original)
        elif entity_type == "SENSITIVE":
            return "(...)"
        elif entity_type == "PRODUCT":
            return self._product_substitute(original)
        return "(...)"

    def _person_substitute(self, name: str) -> str:
        """Generuj inicjały dla osoby - format ze spacją: "F. L." """
        parts = name.strip().split()
        if len(parts) < 1:
            return "X."

        first_name_norm, last_name_norm = normalize_names_for_comparison(name)
        normalized_name = f"{first_name_norm} {last_name_norm}".strip()

        if normalized_name in self.entity_counter:
            return self.entity_counter[normalized_name]

        if len(parts) < 2:
            if parts:
                if self.letter_shift > 0:
                    base_ord = self._get_base_letter_ord(parts[0][0])
                    shifted_ord = (base_ord + self.letter_shift) % 26
                    letter = chr(ord("A") + shifted_ord)
                else:
                    letter = parts[0][0].upper() if parts[0] else "X"
                initials = f"{letter}."
            else:
                initials = "X."
        else:
            has_hyphenated_surname = any("-" in part for part in parts[1:])

            if has_hyphenated_surname:
                if self.letter_shift > 0:
                    base_ord_first = self._get_base_letter_ord(parts[0][0])
                    shifted_ord_first = (base_ord_first + self.letter_shift) % 26
                    first_name_initial = chr(ord("A") + shifted_ord_first)
                else:
                    first_name_initial = parts[0][0].upper() if parts[0] else "X"

                surname_part = None
                for part in parts[1:]:
                    if "-" in part:
                        surname_part = part.split("-")[0].strip()
                        if not surname_part and len(parts) > 2:
                            surname_idx = parts.index(part)
                            if surname_idx > 1:
                                surname_part = parts[surname_idx - 1]
                        break
                    else:
                        surname_part = part

                if surname_part and surname_part.strip():
                    if self.letter_shift > 0:
                        base_ord_surname = self._get_base_letter_ord(surname_part[0])
                        shifted_ord_surname = (
                            base_ord_surname + self.letter_shift
                        ) % 26
                        surname_initial = chr(ord("A") + shifted_ord_surname)
                    else:
                        surname_initial = surname_part[0].upper()
                else:
                    surname_initial = "X"

                initials = f"{first_name_initial}. {surname_initial}."
            else:
                if self.letter_shift > 0:
                    base_ord_first = self._get_base_letter_ord(parts[0][0])
                    shifted_ord_first = (base_ord_first + self.letter_shift) % 26
                    first_name_initial = chr(ord("A") + shifted_ord_first)

                    base_ord_last = self._get_base_letter_ord(parts[-1][0])
                    shifted_ord_last = (base_ord_last + self.letter_shift) % 26
                    last_name_initial = chr(ord("A") + shifted_ord_last)
                else:
                    first_name_initial = parts[0][0].upper() if parts[0] else "X"
                    last_name = parts[-1]
                    last_name_initial = last_name[0].upper() if last_name else "X"

                initials = f"{first_name_initial}. {last_name_initial}."

        self.initials_counter[initials] = self.initials_counter.get(initials, 0) + 1
        replacement = f"{initials} ({self.initials_counter[initials]})"
        self.entity_counter[normalized_name] = replacement

        if self.debug:
            print(f"  👤 '{name}' -> lemmy: '{normalized_name}' -> '{replacement}'")

        return replacement

    def _location_substitute(self, location: str) -> str:
        """Generuj zamiennik dla lokalizacji."""
        loc = location.strip()
        if not loc:
            return "(...)"
        return f"{loc[0].upper()}."

    def _product_substitute(self, product: str) -> str:
        """Generuj zamiennik dla produktu."""
        clean_product = product.strip()
        if not clean_product:
            return "(...)"

        words = clean_product.split()
        if len(words) == 1:
            return f"{words[0][0].upper()}."
        else:
            return f"{words[0][0].upper()}. (...)"

    def anonymize(self, text: str, use_tags: bool = False) -> Tuple[str, int]:
        """Anonimizuj tekst."""
        entities = self.ner.predict(text)

        if not entities:
            return text, 0

        entities.sort(key=lambda e: e["start"], reverse=True)

        result = text
        for entity in entities:
            original = text[entity["start"] : entity["end"]]
            substitute = self._make_substitute(original, entity["label"])

            if use_tags:
                tag = f"<xAnon xSubst='{substitute}'>{original}</xAnon>"
            else:
                tag = substitute

            result = result[: entity["start"]] + tag + result[entity["end"] :]

        return result, len(entities)

    def reset_cache(self):
        """Resetuj cache inicjałów."""
        self.entity_counter = {}
        self.initials_counter = {}

    def post_process(self, text: str) -> str:
        """Post-processing: usuń (1) dla pojedynczych wystąpień."""
        for initials, count in list(self.initials_counter.items()):
            if count == 1:
                text = text.replace(f"{initials} (1)", initials)
        return text


def process_file(
    input_path: Path, anonymizer: SimpleAnonymizer, output_path: Path
) -> Tuple[bool, int]:
    """Przetwórz pojedynczy plik."""
    try:
        anonymizer.reset_cache()

        content = input_path.read_text(encoding="utf-8")
        is_xml = input_path.suffix.lower() == ".xml"

        if is_xml:
            fragments = extract_text_from_xml(content)
            if not fragments:
                output_path.write_text(content, encoding="utf-8")
                return True, 0

            new_content = list(content)
            offset = 0
            total_count = 0

            for original_text, start, end in fragments:
                anon_text, count = anonymizer.anonymize(original_text, use_tags=True)
                total_count += count

                if original_text != anon_text:
                    start_pos = start + offset
                    end_pos = end + offset
                    new_content[start_pos:end_pos] = list(anon_text)
                    offset += len(anon_text) - len(original_text)

            result = "".join(new_content)
            result = anonymizer.post_process(result)

            output_path.write_text(result, encoding="utf-8")
            return True, total_count
        else:
            anon_text, count = anonymizer.anonymize(content, use_tags=False)
            anon_text = anonymizer.post_process(anon_text)

            output_path.write_text(anon_text, encoding="utf-8")
            return True, count

    except Exception as e:
        print(f"❌ Błąd przy przetwarzaniu {input_path.name}: {e}")
        import traceback

        traceback.print_exc()
        return False, 0


def extract_text_from_xml(xml: str) -> List[Tuple[str, int, int]]:
    """Wyciągnij fragmenty tekstowe z XML."""
    fragments = []
    pattern = r">(?P<content>[^<>]+)<"

    for match in re.finditer(pattern, xml):
        content = match.group("content")
        if content.strip():
            if (
                not xml[max(0, match.start() - 10) : match.start()]
                .strip()
                .endswith('="')
            ):
                fragments.append(
                    (content, match.start("content"), match.end("content"))
                )

    return fragments


def test_model(model_path: str, letter_shift: int = 0):
    """Testuj model i sprawdź działanie inicjałów."""
    print("\n" + "=" * 70)
    print("TEST MODELU I INICJAŁÓW")
    print("=" * 70)

    test_cases = [
        ("Oskar Ruciński", "Oskara Rucińskiego"),
        ("Jan Kowalski", "Jana Kowalskiego"),
        ("Zenon Nowak", "Zenona Nowaka"),
        ("Borys Kamiński", "Borysa Kamińskiego"),
        ("Anna Kowalska", "Anny Kowalskiej"),
        ("Piotr Wiśniewski", "Piotrowi Wiśniewskiemu"),
        ("Krzysztof Wasiucionek", "Krzysztofa Wasiucionka"),
        ("Ewa Nowak", "Ewy Nowak"),
        ("Maria Kowalska", "Marii Kowalskiej"),
    ]

    for name1, name2 in test_cases:
        quick_debug_specific_name(name1, name2)

    ner = PolishNERInference(model_path, debug=True)

    test_sentences = [
        "Jan Kowalski z Warszawy mieszka przy ul. Pięknej 15.",
        "Sędzia Anna Nowak ustaliła następujące fakty",
        "reprezentowanego przez radcę prawnego Annę Wiśniewską",
        "Świadek Krzysztof Wasiucionek, urodzony 23.01.1985 r.",
        "Małoletni Oskar Ruciński ma 6 miesięcy.",
        "odpis skrócony aktu rodzenia mał. Oskara Rucińskiego k. 7;",
    ]

    print("\n--- TEST NER ---")
    for text in test_sentences:
        entities = ner.predict(text)
        print(f"\nTekst: {text}")
        if entities:
            for ent in entities:
                print(
                    f"  → {ent['word']:30s} | {ent['label']:12s} | {ent['confidence']:.3f}"
                )
        else:
            print("  (brak wykrytych encji)")

    print("\n" + "=" * 70)
    print("--- TEST INICJAŁÓW I KONFLIKTÓW ---")
    print("=" * 70)

    anonymizer = SimpleAnonymizer(model_path, debug=True, letter_shift=letter_shift)

    test_names = [
        "Jan Kowalski",
        "Jana Kowalskiego",
        "Oskar Ruciński",
        "Oskara Rucińskiego",
        "Anna Kowalska",
        "Anny Kowalskiej",
        "Kundegunda Hermenegildowa",
        "Kosma Milewicz",
    ]

    print(f"Przesunięcie liter: {letter_shift}\n")
    for name in test_names:
        initials = anonymizer._person_substitute(name)
        first, last = normalize_names_for_comparison(name)
        print(f"  {name:30s} -> lemmy: ({first:10s}, {last:15s}) -> {initials}")

    print("\n--- POST-PROCESSING ---")
    for initials, count in anonymizer.initials_counter.items():
        if count == 1:
            print(f"  {initials} (1) -> {initials}")
        else:
            print(f"  {initials} ({count})")

    print("\n" + "=" * 70 + "\n")


def main():
    print("=" * 70)
    print("🤖 ANONIMIZATOR (NER + Morfeusz 2)")
    print("=" * 70)

    args = sys.argv[1:]
    if len(args) < 2:
        print(f"Użycie: python3 {sys.argv[0]} <model_path> <plik|katalog> [opcje]")
        print("\nOpcje:")
        print("  --test         : uruchom test modelu")
        print("  --debug        : tryb debug z dodatkowymi informacjami")
        print("  --shift N      : przesuń inicjały o N liter (domyślnie 0)")
        sys.exit(1)

    model_path = Path(args[0])
    input_path = Path(args[1])
    run_test = "--test" in args
    debug_mode = "--debug" in args

    letter_shift = 0
    if "--shift" in args:
        try:
            shift_idx = args.index("--shift")
            letter_shift = int(args[shift_idx + 1])
        except (ValueError, IndexError):
            print("❌ Błąd: --shift wymaga liczby całkowitej")
            sys.exit(1)

    if not model_path.exists():
        print(f"❌ Model nie istnieje: {model_path}")
        sys.exit(1)

    if run_test:
        test_model(str(model_path), letter_shift)
        return

    files = []
    if input_path.is_file():
        files = [input_path]
    elif input_path.is_dir():
        files = sorted(list(input_path.glob("*.xml")) + list(input_path.glob("*.txt")))
    else:
        print(f"❌ Ścieżka nie istnieje: {input_path}")
        sys.exit(1)

    if not files:
        print("❌ Brak plików do przetworzenia.")
        sys.exit(1)

    print(f"\n📋 Znaleziono plików: {len(files)}")
    if debug_mode:
        print("🔍 Tryb DEBUG włączony")
    if letter_shift > 0:
        print(f"🔀 Przesunięcie inicjałów: {letter_shift}")

    anonymizer = SimpleAnonymizer(
        str(model_path), debug=debug_mode, letter_shift=letter_shift
    )

    start_time = time.time()
    success_count = 0
    total_entities = 0
    output_dir_name = f"output_minimal_{time.strftime('%Y%m%d_%H%M%S')}"

    for file in files:
        print(f"\n--- Przetwarzanie: {file.name} ---")
        output_dir = file.parent / output_dir_name
        output_dir.mkdir(exist_ok=True)
        output_file = output_dir / f"{file.stem}_anon{file.suffix}"

        ok, count = process_file(file, anonymizer, output_file)
        if ok:
            success_count += 1
            total_entities += count
            print(f"    Znaleziono encji: {count}")

    elapsed = time.time() - start_time

    print(f"\n{'=' * 70}")
    print("📊 PODSUMOWANIE")
    print(f"{'=' * 70}")
    print(f"✅ Przetworzono: {success_count}/{len(files)} plików")
    print(f"💾 Wyniki w: {output_dir_name}")
    print(f"🔢 Łącznie encji: {total_entities}")
    print(f"👤 Unikalnych osób: {len(anonymizer.entity_counter)}")
    print(f"🔤 Unikalnych zestawów inicjałów: {len(anonymizer.initials_counter)}")
    print(f"⏱️ Czas: {elapsed:.2f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()

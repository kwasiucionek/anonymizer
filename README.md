# Anonimizator dokumentów prawnych z modelem NER

System automatycznej anonimizacji polskich dokumentów prawnych wykorzystujący model **polish-roberta-8k** z fine-tuningiem do Named Entity Recognition (NER).

## 📋 Spis treści

- [Charakterystyka](#-charakterystyka)
- [Struktura projektu](#-struktura-projektu)
- [Wymagania](#-wymagania)
- [Instalacja](#-instalacja)
- [Użycie - Anonimizacja](#-użycie---anonimizacja)
- [Użycie - Testy](#-użycie---testy)
- [Użycie - Trening modelu](#-użycie---trening-modelu)



---

## 🎯 Charakterystyka

### Rozpoznawane encje

Model wykrywa 4 kategorie danych osobowych:

| Kategoria | Opis | Przykład wejścia | Przykład wyjścia |
|-----------|------|------------------|------------------|
| **PERSON** | Imiona i nazwiska osób fizycznych lub pojedyńcze nazwisko | Jan Kowalski | `<xAnon xSubst="B. Z.">Jan Kowalski</xAnon>` |
| **LOCATION** | Miejscowości i lokalizacje, ale tylko te pisane dużą literą i w formie przymiotnika | Warszawa | `<xAnon xSubst="W.">Warszawa</xAnon>` |
| **PRODUCT** | Nazwy produktów  | `<xAnon xSubst=" A. (...)">Audi Q6</xAnon>` |
| **SENSITIVE** | Pozostałe dane wrażliwe, np.: PESEL, telefony, daty urodzenia, także nazwy województw, gmin i inne przymiotnikowe określenia (np. województwo wielkopolskie > województwo), organizacje i firmy  | 92010112345 | `<xAnon xSubst="(...)">92010112345</xAnon>` |

### Zasady anonimizacji
"
✅ **PERSON** - inicjały przesunięte według Szyfru Cezara (np. o 2 - "Jan Kowalski" → "L. M.")  
✅ **LOCATION** - Pierwsza litera (np. "Warszawa" → "W.")  
✅ **PRODUCT** - Pierwsza litera (np. "Audi" → "A.") lub dla wyrażeń wieloczłonowych: pierwsza litera + (...) np. "Super Ekstra Krem" → S. (...)    
✅ **SENSITIVE** - Zastąpienie "(...)"  

### Wyjątki

Model **NIE** anonimizuje:
- ❌ Sędziów i prokuratorów
- ❌ Nazw sądów i instytucji publicznych
- ❌ Ekspertów sądowych, biegłych (w kontekście)


---

## 📁 Struktura projektu

```
anonymizer/
│
├── anonymizer.py                    # ⭐ Główny skrypt anonimizacji
├── anon-test_runner2.py             # Skrypt testowy
├── dataset.json                     # Dataset treningowy (1000+ przykładów)
├── README.md                        # Ten plik
│
├── anonymization-model/           # ⭐ Wytrenowany model (najnowszy)
│       ├── config.json              # Konfiguracja modelu
│       ├── pytorch_model.bin        # Wagi modelu (~500MB)
│       ├── tokenizer_config.json    # Konfiguracja tokenizera
│       ├── vocab.txt                # Słownik tokenów
│       └── label_config.json        # Mapowanie etykiet
│
├── colab/                           # Notebooki do treningu
├── anon-test/                       # Dane testowe ze starego anonimizatora
│   
│
└── data/                            # ⭐ Katalog z dokumentami do anonimizacji
    ├── wyrok1.xml
    ├── wyrok2.xml
    ├── dokument.txt
    └── output/                      # Pliki zanonimizowane (tworzone automatycznie)
        ├── wyrok1_anon.xml
        ├── wyrok2_anon.xml
        └── dokument_anon.txt
```

---

## 🔧 Wymagania

### Minimalne wymagania

- **Python:** 3.8+
- **RAM:** 8 GB (CPU) / 4 GB (GPU)
- **Dysk:** 1 GB wolnego miejsca
- **System:** Linux, macOS, Windows

### Zależności Python

```
transformers>=4.30.0
torch>=2.0.0
scikit-learn>=1.0.0
pandas>=1.5.0
```

### Opcjonalne (dla szybszego działania)

- **GPU:** CUDA-compatible (NVIDIA)
- **VRAM:** 4 GB+
- **CUDA:** 11.7+ i cuDNN

---

## 📦 Instalacja

### 1. Sklonuj repozytorium

```bash
git clone <url-repozytorium>
cd anonymizer
```

### 2. Zainstaluj zależności

```bash
pip install -r requirements.txt
```

Lub ręcznie:

```bash
pip install transformers torch scikit-learn pandas
```

### 3. Sprawdź instalację

```bash
python3 anonymizer.py
```

Powinno wyświetlić się:
```
Użycie: python3 anonymizer.py <model_path> <plik.xml|katalog> [--debug]
```

---

## 🚀 Użycie - Anonimizacja

### Podstawowe użycie

```bash
python3 anonymizer.py ./anonymization-model data/
```

**Parametry:**
- `./anonymization-model` - ścieżka do wytrenowanego modelu
- `data/` - katalog z plikami `.xml` lub `.txt` do anonimizacji
- `--debug` (opcjonalne) - tryb debugowania z dodatkowymi informacjami

### Przykłady

#### 1. Anonimizacja wszystkich plików w katalogu

```bash
python3 anonymizer.py ./anonymization-model data/
```

**Rezultat:**
```
data/wyrok1.xml  →  data/output/wyrok1_anon.xml
data/wyrok2.xml  →  data/output/wyrok2_anon.xml
data/tekst.txt   →  data/output/tekst_anon.txt
```

#### 2. Anonimizacja pojedynczego pliku

```bash
python3 anonymizer.py ./anonymization-model data/wyrok.xml
```

#### 3. Tryb debugowania

```bash
python3 anonymizer.py ./anonymization-model data/ --debug
```

Wyświetla dodatkowe informacje o błędach i przetwarzaniu.



### Anonimizacja osób

Skrypt automatycznie przesuwa domyślne inicjały o określoną liczbę znaków (ustawienie LETTER_SHIFT = 2) co daje np. wynik:

```xml
<xAnon xSubst="L. M.">Jan Kowalski</xAnon>
```


## 🎓 Użycie - Testy

```bash
python3 anon-test_runner2.py
```


## 🎓 Użycie - Trening modelu

### Przygotowanie środowiska

Model najlepiej trenować w **Google Colab** (darmowe GPU):

1. Przejdź do [Google Colab](https://colab.research.google.com/)
2. Otwórz `colab/train_ner_model.ipynb`
3. Runtime → Change runtime type → **GPU (T4)**
4. Uruchom wszystkie komórki

### Krok 1: Przygotowanie datasetu

Edytuj plik `dataset.json` według formatu:

**Przykładowy obiekt JSON:**

```json
{
  "text": "W sprawie o sygnaturze akt I C 123/25, powód Jan Kowalski domagał się od firmy \"Pol-Bud\" S.A. (KRS: 0000123456) zapłaty.",
  "tokens": ["W", "sprawie", "o", "sygnaturze", "akt", "I", "C", "123/25", ",", "powód", "Jan", "Kowalski", "domagał", "się", "od", "firmy", "\"", "Pol-Bud", "\"", "S.A.", "(", "KRS:", "0000123456", ")", "zapłaty", "."],
  "labels": ["O", "O", "O", "O", "O", "O", "O", "O", "O", "O", "B-PERSON", "I-PERSON", "O", "O", "O", "O", "O", "B-ORG", "O", "O", "O", "O", "B-SENSITIVE", "O", "O", "O"]
}
```
```

**Szczegóły:** Zobacz `ner_dataset_description.md`

### Krok 2: Wgraj dataset do Colab

W notebooku:

```python
from google.colab import files
uploaded = files.upload()  # Wybierz dataset.json
```

### Krok 3: Konfiguracja treningu

Edytuj hiperparametry w notebooku:

```python
# ============================================================================
# KONFIGURACJA
# ============================================================================

# Ścieżki
DATASET_PATH = "dataset.json"
OUTPUT_DIR = "./anonymization-model"

# Model bazowy
MODEL_NAME = "PKOBP/polish-roberta-8k"

# Tryb (uproszczony z 4 kategoriami)
SIMPLE_MODE = True

# Hiperparametry
NUM_EPOCHS = 10          # Liczba epok (8-15 optymalnie)
BATCH_SIZE = 8           # Rozmiar batcha (4-16)
LEARNING_RATE = 1e-5     # Współczynnik uczenia
MAX_LENGTH = 512         # Maks. długość sekwencji

# Podział danych
TRAIN_SPLIT = 0.8        # 80% trening
TEST_SPLIT = 0.2         # 20% test
```

### Krok 4: Uruchom trening

Wykonaj wszystkie komórki notebooka. Proces obejmuje:

1. ✅ Wczytanie datasetu
2. ✅ Podział train/test (80/20)
3. ✅ Tokenizacja
4. ✅ Trening modelu (z early stopping)
5. ✅ Ewaluacja
6. ✅ Zapis najlepszego modelu
7. ✅ Testy jakościowe

### Krok 5: Pobierz wytrenowany model

Notebook automatycznie spakuje i udostępni model do pobrania:

```python
# Pobieranie (ostatnia komórka)
!zip -r anonymization_model.zip ./anonymization-model/final
from google.colab import files
files.download('anonymization_model.zip')
```

### Krok 6: Użyj nowego modelu

Rozpakuj i użyj:

```bash
unzip anonymization_model.zip
python3 anonymizer.py ./anonymization-model/final data/
```

### Monitorowanie treningu

Podczas treningu zobaczysz:

```
🚀 ROZPOCZYNAM TRENING
================================================================================

 [416/416 02:40, Epoch 8/8]
Step  Training Loss  Validation Loss  Precision  Recall  F1      Accuracy
50    0.3985        0.4354           0.5802     0.7633  0.6593  0.8428
100   0.1113        0.2356           0.7337     0.8166  0.7730  0.9280
150   0.0436        0.2436           0.7753     0.8166  0.7954  0.9367
200   0.0291        0.2414           0.8075     0.8316  0.8193  0.9354
250   0.0237        0.2592           0.8105     0.8849  0.8461  0.9417
300   0.0184        0.2431           0.8387     0.8870  0.8622  0.9439
350   0.0161        0.2455           0.8450     0.8721  0.8583  0.9458
400   0.0077        0.2428           0.8665     0.8721  0.8693  0.9474

================================================================================
✅ TRENING ZAKOŃCZONY
================================================================================
```

**Najlepszy model** zostanie automatycznie zapisany (krok z najwyższym F1 Score).

---


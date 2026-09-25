# Anonimizator dokumentów prawnych z modelem NER

System automatycznej anonimizacji polskich dokumentów prawnych wykorzystujący
model **polish-roberta-8k** z fine-tuningiem do Named Entity Recognition (NER)
oraz **Morfeusz 2** do normalizacji odmian nazwisk. Dostępny jako skrypt CLI
(`anonymizer.py`) oraz **Web UI w Django** (upload, kolejka w tle,
zatrzymywanie, podgląd wyników, sprawy z wykazem osób).


**Demo:** [anonymizer.cytr.us](https://anonymizer.cytr.us)

**Login:** demo
**Hasło:** anonymizer_demo


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

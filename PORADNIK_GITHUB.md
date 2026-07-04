# Poradnik: jak wrzucić SIEKACZA 9000 na GitHub (Windows, krok po kroku)

Ten poradnik zakłada, że nigdy wcześniej nie używałeś Gita. Wszystko robisz raz,
potem aktualizacja kodu to już tylko 3 komendy.

---

## 0. Co będzie potrzebne

- **Konto GitHub** — załóż na https://github.com (darmowe).
- **Git for Windows** — pobierz z https://git-scm.com/download/win i zainstaluj
  (klikaj „Next”, domyślne ustawienia są OK).
- Po instalacji uruchom **Git Bash** albo zwykły **PowerShell** — obie zadziałają.

Sprawdź, że Git działa:

```powershell
git --version
```

---

## 1. Jednorazowa konfiguracja Gita (Twoje imię i e-mail)

```powershell
git config --global user.name "Kewin"
git config --global user.email "amaterasu.ember@gmail.com"
```

---

## 2. Utwórz repozytorium na GitHub

1. Wejdź na https://github.com → kliknij **+** (prawy górny róg) → **New repository**.
2. **Repository name**: np. `siekacz-9000`.
3. **Description**: np. „Optymalizator rozkroju płyt”.
4. Wybierz **Public** (publiczne — każdy może pobrać; pasuje do licencji „darmowe użycie, bez odsprzedaży”)
   albo **Private** (prywatne — tylko Ty; aktualizacje przez GitHub Releases nadal działają).
5. **NIE** zaznaczaj „Add a README / .gitignore / license” — masz już te pliki lokalnie.
6. Kliknij **Create repository**.

Skopiuj adres repo — będzie wyglądał tak:
`https://github.com/TWOJA-NAZWA/siekacz-9000.git`

---

## 3. Wyślij projekt na GitHub

Otwórz terminal **w folderze projektu** (folder `SIEKACZ 9000`):

```powershell
cd "C:\Users\Kewin\Desktop\SIEKACZ 9000"
git init
git add .
git commit -m "Pierwsza wersja SIEKACZ 9000"
git branch -M main
git remote add origin https://github.com/TWOJA-NAZWA/siekacz-9000.git
git push -u origin main
```

> Przy pierwszym `push` Git poprosi o zalogowanie do GitHub — otworzy się okno
> przeglądarki, kliknij **Authorize**. Gotowe.

Plik `.gitignore` (już w projekcie) sprawia, że na GitHub **nie** trafią foldery
`build/`, `SIEKACZ9000/`, `INSTALLER/`, `__pycache__/`, logi ani baza — tylko kod.

---

## 4. Jak wgrywać późniejsze zmiany (codzienny rytm)

Po każdej zmianie w kodzie:

```powershell
git add .
git commit -m "Krótki opis co zmieniłem"
git push
```

To wszystko. Trzy komendy.

---

## 5. Włączenie zdalnych aktualizacji (GitHub Releases)

Program ma już wbudowany mechanizm aktualizacji — wystarczy go skonfigurować.

### 5a. Wskaż programowi Twoje repo

Otwórz plik `app/updater.py` i ustaw na górze:

```python
GITHUB_OWNER = "TWOJA-NAZWA"   # Twój login z GitHub
GITHUB_REPO = "siekacz-9000"   # nazwa repozytorium
```

Zapisz, zacommituj i wypchnij (`git add . && git commit -m "Konfiguracja aktualizacji" && git push`).

### 5b. Podnieś numer wersji

W pliku `app/version.py` zmień np. `APP_VERSION = "2.0.0"` na `"2.1.0"` za każdym
razem, gdy chcesz wypuścić nowość. Program porównuje tę liczbę z najnowszym
release na GitHub.

### 5c. Zbuduj instalator i opublikuj Release

1. Zbuduj wersję do rozdania:
   ```powershell
   .\BUILD_INSTALLER.bat
   ```
   (albo `.\BUILD_EXE.bat` dla wersji przenośnej .exe)
2. Na GitHub: zakładka **Releases** → **Draft a new release**.
3. **Choose a tag** → wpisz `v2.1.0` (musi pasować do `APP_VERSION`) → **Create new tag**.
4. **Release title**: np. „SIEKACZ 9000 v2.1.0”.
5. **Describe this release**: wpisz opis zmian — TO POKAŻE SIĘ użytkownikom w oknie
   aktualizacji w programie. Pisz po ludzku, np.:
   ```
   - Nowy przycisk „Wyślij” (e-mail / PDF / drukarka)
   - Realistyczne metryki cięcia (mb piły, czas)
   - Poprawki rozkroju
   ```
6. **Attach binaries** → przeciągnij plik instalatora (`.exe`) z folderu `INSTALLER/`
   (lub `SIEKACZ9000/`). Program pobierze właśnie ten plik.
7. Kliknij **Publish release**.

Od tej chwili każdy, kto ma starszą wersję, zobaczy w programie dzwoneczek
**„🔔 Aktualizacja v2.1.0”** na górnym pasku. Po kliknięciu zobaczy Twój opis
zmian i będzie mógł pobrać oraz zainstalować nową wersję.

---

## 6. Najczęstsze problemy

| Problem | Rozwiązanie |
|---|---|
| `git: command not found` | Zainstaluj Git for Windows i otwórz terminal na nowo. |
| `Permission denied` przy push | Zaloguj się ponownie do GitHub w oknie przeglądarki. |
| Wgrały się foldery `build/`/`__pycache__/` | Upewnij się, że plik `.gitignore` jest w repo (jest dołączony). |
| Aktualizacja się nie pokazuje | Sprawdź, czy tag releasu (`v2.1.0`) jest **wyższy** niż `APP_VERSION` poprzedniej wersji i czy `GITHUB_OWNER`/`GITHUB_REPO` są poprawne. |

---

## 7. Ściąga (gdy już wszystko ustawione)

```powershell
# zmiany w kodzie:
git add . && git commit -m "opis" && git push

# nowa wersja dla użytkowników:
# 1) zmień APP_VERSION w app/version.py
# 2) .\BUILD_INSTALLER.bat
# 3) GitHub → Releases → Draft new release → tag vX.Y.Z → dodaj .exe → Publish
```

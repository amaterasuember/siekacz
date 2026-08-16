# Wydania wieloplatformowe

Wydanie produkcyjne uruchamia workflow `.github/workflows/release-multiplatform.yml`.
Każdy instalator jest budowany natywnie na właściwym runnerze, ponieważ PyInstaller
nie obsługuje bezpiecznego cross-compilingu między Windows, macOS i Linuksem.

## Tworzone paczki

- Windows x64: instalator Inno Setup `.exe`, testowany w CI na Windows Server 2022.
- macOS Intel oraz Apple Silicon: obrazy `.dmg`.
- Linux x86_64 oraz ARM64: samodzielne pliki `.AppImage`.

Windows Server wymaga wersji z interfejsem graficznym (Desktop Experience).
Server Core nie uruchomi aplikacji PySide6, ponieważ nie ma kompletnego pulpitu GUI.

## Publikacja

Okno publikatora w Siekaczu wywołuje przez GitHub API workflow
`release-multiplatform.yml`. Token musi mieć `Contents: write` oraz `Actions: write`.
Workflow podbija `app/version.py`, tworzy tag, buduje pięć paczek i dopiero po
powodzeniu wszystkich zadań publikuje jedno wydanie GitHub.

## Podpisywanie

Lokalne i CI-owe skrypty tworzą kompletne, ale domyślnie niepodpisane paczki.
Przed dystrybucją poza zaufaną siecią zalecane jest dodanie:

- certyfikatu Authenticode do instalatora Windows;
- Apple Developer ID, hardened runtime i notaryzacji DMG;
- podpisu GPG lub minisign dla AppImage.

Brak podpisu nie zmienia działania programu, ale system może wyświetlać ostrzeżenie
przy pierwszym uruchomieniu.

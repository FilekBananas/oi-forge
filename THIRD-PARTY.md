# Komponenty zewnętrzne

Te elementy **nie są** częścią OI Forge i nie obejmuje ich licencja OI Forge (`LICENSE`, pkt 7). Każdy podlega własnej
licencji. Pełne teksty licencji są w `web/vendor/licenses/`.

## Dołączone do projektu (`web/vendor/`)

| Komponent | Do czego | Licencja | Plik licencji |
|---|---|---|---|
| [CodeMirror 5](https://codemirror.net/5/) (5.65.16) — `web/vendor/codemirror/` | edytor kodu | MIT, © Marijn Haverbeke i współtwórcy | `CodeMirror-MIT.txt` |
| [pdf.js](https://mozilla.github.io/pdf.js/) — `web/vendor/pdfjs/` | wyciąganie tekstu z PDF-ów z treściami zadań (w przeglądarce, offline) | Apache License 2.0, © Mozilla Foundation | `pdfjs-Apache-2.0.txt` |
| [Inter](https://rsms.me/inter/) — `web/vendor/fonts/inter-*` | font interfejsu | SIL Open Font License 1.1, © The Inter Project Authors | `Inter-OFL.txt` |
| [JetBrains Mono](https://www.jetbrains.com/lp/mono/) — `web/vendor/fonts/jetbrains-mono-*` | font kodu | SIL Open Font License 1.1, © The JetBrains Mono Project Authors | `JetBrainsMono-OFL.txt` |
| [Lucide](https://lucide.dev) — `web/vendor/icons/`, `web/js/icons.js` | ikony | ISC, © Lucide Icons and Contributors | `Lucide-ISC.txt` |

## Używane, ale nie dołączone

Te programy instalujesz sam; OI Forge tylko je uruchamia:

- **Python 3** (biblioteka standardowa — żadnych dodatkowych paczek),
- **kompilatory i debuggery**: GCC (`g++`), Clang, LLDB, GDB,
- **przeglądarka** (Chrome, Edge, Chromium albo domyślna) — w niej otwiera się okno programu.

## Treści, które importujesz

Rozwiązania, treści zadań i strony pobrane przez Ciebie z serwerów SIO2 albo ze Szkopułu (Moje programy → import)
zostają u Ciebie w `user_data/` i podlegają prawom ich autorów oraz regulaminom tych serwisów. Nie są częścią
OI Forge i nie trafiają do repozytorium.

# Building the Windows .exe

This project was developed and tested (analytics + rendering + GUI logic)
inside a Linux sandbox with no Windows machine available. **The steps below
have not been executed on real Windows 10/11** -- do that before shipping.
PyInstaller does not cross-compile: you must run this on an actual Windows
x64 machine (or a Windows CI runner/VM).

## 1. Prerequisites (on Windows)

- Python 3.11+ (64-bit), added to PATH.
- Git (to clone the repo) -- or just copy the source tree over.
- **WeasyPrint's native dependencies.** WeasyPrint (used for PDF export)
  depends on Pango/Cairo/GDK-Pixbuf, which are not pure-Python and are the
  single most common source of "it works on my dev machine, not on the
  built .exe" pain for this stack. On Windows you need the GTK3 runtime
  libraries on the PATH before building AND on the target machine. The
  simplest option: install the standalone **"GTK3 runtime for Windows"**
  installer (search for `gtk3-runtime` releases, e.g. the
  tschoonj/GTK-for-Windows-Runtime-Environment-Installer project), then
  confirm `python -c "import weasyprint"` works in a plain `cmd.exe` before
  attempting to freeze it. If that import fails, the .exe will fail the
  same way, just with a less obvious error.
  - If bundling GTK turns out to be impractical for the actual release
    machine, the fallback documented in the product spec is acceptable:
    generate DOCX via python-docx (already implemented, no native deps) and
    convert to PDF via `soffice --headless --convert-to pdf` if LibreOffice
    is guaranteed to be present -- but that reintroduces an external
    dependency the spec explicitly wanted to avoid. Try the WeasyPrint path
    first.

```
pip install -r requirements.txt
pip install pyinstaller
```

## 2. Build

From the repository root:

```
pyinstaller packaging/repair_report.spec --distpath dist --workpath build
```

This produces `dist/RepairReportApp/RepairReportApp.exe` plus its
supporting files in the same folder (**onedir** build, not onefile -- see
below for why).

## 3. Why onedir, not --onefile

`--onefile` unpacks the entire bundle (including matplotlib, Qt, and
WeasyPrint's native libraries) into a temp directory on every single
launch, which is slow and has repeatedly caused native-library loading
issues for WeasyPrint/Cairo specifically in other PyInstaller projects.
`onedir` starts faster and is easier to debug (you can see exactly which
DLL is missing rather than guessing at a temp-extraction failure). If a
single distributable file is a hard requirement, wrap the onedir output in
a conventional installer (Inno Setup / NSIS) instead of switching to
PyInstaller's `--onefile` mode.

## 4. Test on a CLEAN Windows machine

Before shipping, run `dist/RepairReportApp/RepairReportApp.exe` on a
Windows 10/11 machine that has **no** Python, Office, or LibreOffice
installed, and confirm:

1. The app launches (window opens, no console flashes).
2. Loading `samples/WR_Consolidated_List_20260902_весь.xlsx` (or the
   `tests/fixtures` copy) succeeds and lists 4 periods.
3. Generating all three formats (HTML/PDF/DOCX) for July 2026 succeeds and
   the numbers match `tests/` (1535 repairs, 104 ASCs, etc.)
4. The PDF actually opens and renders text/charts (this is the step most
   likely to fail first if the WeasyPrint/GTK dependency above wasn't
   satisfied).

## 5. Data files bundled

`repair_report/config/*.json` and `repair_report/render/templates/*.jinja`
are added explicitly in the .spec's `datas` list. If you add a new config
file or template, add it there too -- PyInstaller only bundles what it's
told about (plus whatever `collect_data_files`/import analysis can infer).

## 6. What is intentionally NOT bundled

`tests/fixtures/*` (including the Kosovov pivot workbook) must never be
added to `datas` -- it is a dev-only test fixture, not a runtime
dependency. If you find yourself tempted to bundle it "just in case", stop
and re-read docs/REVERSE_ENGINEERING.md §0/§Runtime-vs-test-data-separation.

# Building the Windows .exe

This project was developed and tested (analytics + rendering + GUI logic)
inside a Linux sandbox with no Windows machine available. **The steps below
have not been executed on real Windows 10/11** -- do that before shipping.
PyInstaller does not cross-compile: you must run this on an actual Windows
x64 machine (or a Windows CI runner/VM).

**The built `.exe` is intentionally NOT committed to this repo** -- it's a
build output (like `dist/`/`build/`, both gitignored), not source code, and
a 100+ MB binary doesn't belong in git history. Two ways to actually get
the file:

- **GitHub Actions (no Windows machine needed):** `.github/workflows/build-windows-exe.yml`
  builds it on a real `windows-latest` runner. Trigger it from the repo's
  Actions tab ("Build Windows .exe" -> "Run workflow"), wait for the run to
  finish, then download `RepairReportApp-windows-exe` from the run's
  Artifacts section -- that's the real Windows .exe, not a Linux stand-in.
- **Build it yourself** by following the steps below on an actual Windows
  10/11 x64 machine.

Two build specs are provided -- pick one:

| Spec | Result | Use when |
|---|---|---|
| `repair_report_onefile.spec` | **one** `RepairReportApp.exe`, nothing else | you need a single file to hand off/email/copy -- **this is the default recommendation now** |
| `repair_report.spec` | a folder (`RepairReportApp/RepairReportApp.exe` + DLLs) | debugging a build issue, or startup speed matters more than file count |

Both are built and tested the same way (steps 1, 2, 4 below); §3 explains
the trade-off if you're unsure which to pick.

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

From the repository root, for the single-file .exe (default recommendation):

```
pyinstaller packaging/repair_report_onefile.spec --distpath dist --workpath build
```

This produces **one file**, `dist/RepairReportApp.exe` -- copy/email/hand
off just that file, nothing else needs to travel with it.

Or, for the onedir build (a folder instead of one file -- see §3 for why
you might still want this one):

```
pyinstaller packaging/repair_report.spec --distpath dist --workpath build
```

This produces `dist/RepairReportApp/RepairReportApp.exe` plus its
supporting files in the same folder.

## 3. onefile vs. onedir

`--onefile` (what `repair_report_onefile.spec` uses) packs everything --
matplotlib, Qt, WeasyPrint's native libraries, all config/templates --
into that single .exe. On every launch it self-extracts to a per-run temp
directory (`%TEMP%\_MEI...`) and cleans up on a normal exit; this makes
first launch noticeably slower than onedir (a few seconds, most of it
disk I/O unpacking ~100+ MB) and is the one place native-library loading
(WeasyPrint/Cairo/Pango specifically) has, in other PyInstaller projects,
occasionally behaved differently than onedir -- so §4's "does the PDF
actually render" check matters even more for a onefile build than usual.
`onedir` avoids both of those (starts faster, and is easier to debug --
you can see exactly which DLL is missing in the folder rather than
guessing at a temp-extraction failure), at the cost of being a folder
instead of one file.

If `--onefile` turns out to be unworkable for some Windows target (e.g. a
locked-down machine that blocks temp-directory execution, or antivirus
flagging the self-extracting bootloader -- both known PyInstaller
onefile pain points, not specific to this app), fall back to onedir and
wrap that folder in a conventional installer (Inno Setup / NSIS) to still
hand users one file (a setup.exe) to run.

**Validated (see git history for this change):** the onefile spec was
built and smoke-tested (freezing a throwaway entry point through the same
`datas`/`hiddenimports`, run headless) to confirm the frozen bundle
correctly resolves `repair_report/config/*.json`, the Jinja template, and
WeasyPrint's bundled CSS/ICC data under `sys._MEIPASS`, and produces
byte-for-byte-equivalent HTML/PDF/DOCX output to a from-source run (same
period count, same row/ticket counts, same page count). That was done on
Linux (producing an ELF binary, not tested was the actual Windows .exe) --
it proves the packaging *logic* (what's bundled, how paths resolve when
frozen) is correct, but the Windows-specific risk -- WeasyPrint's
Pango/Cairo/GDK-Pixbuf native libraries specifically -- is untested until
someone runs §4 on a real Windows box.

## 4. Test on a CLEAN Windows machine

Before shipping, run `dist/RepairReportApp.exe` (onefile) or
`dist/RepairReportApp/RepairReportApp.exe` (onedir) on a Windows 10/11
machine that has **no** Python, Office, or LibreOffice installed, and
confirm:

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
are added explicitly in the `datas` list -- of **both** spec files (they're
independent PyInstaller specs, not one inheriting from the other). If you
add a new config file or template, add it to both
`repair_report_onefile.spec` and `repair_report.spec`, or the one you
forgot will build "successfully" and then fail at runtime with a missing
file error -- PyInstaller only bundles what it's told about (plus whatever
`collect_data_files`/import analysis can infer).

## 6. What is intentionally NOT bundled

`tests/fixtures/*` (including the Kosovov pivot workbook) must never be
added to `datas` -- it is a dev-only test fixture, not a runtime
dependency. If you find yourself tempted to bundle it "just in case", stop
and re-read docs/REVERSE_ENGINEERING.md §0/§Runtime-vs-test-data-separation.

# Repair Report Automation

A Windows desktop app that turns a monthly service-network repair export
(`WR_Consolidated_List_*.xlsx`) into the same monthly report a human
currently builds by hand -- HTML, PDF and DOCX, with the same tables,
charts, and narrative text as the reference report.

For end users: see [README_USER.md](README_USER.md).
For the full evidence trail behind every formula in this codebase
(including the things that turned out NOT to be true and why): see
[docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md).

## Project layout

```
repair_report/
  ingest/            Reads the source .xlsx by NAMED column (not position),
                      validates required columns, raises a clear error if
                      the file doesn't look like the expected export.
  analytics/          One module per report section (summary, categories,
                      regions_asc, sla_quality, manufacturers, tv_analysis,
                      iris_defects, fraud, detail, parts_support, periods,
                      registries) plus shared helpers (common.py, tables.py)
                      and the orchestrator (engine.py). This layer has NO
                      UI/rendering dependencies -- it's a plain library,
                      independently testable and tested.
  charts/             matplotlib chart rendering -> PNG, shared by all 3
                      output formats so they never visually disagree.
  render/             HTML (Jinja2, the source-of-truth layout), PDF
                      (weasyprint, renders the SAME HTML), and DOCX
                      (python-docx, same structure, native Word tables).
  config/             Editable JSON: category mappings, repair-level
                      labels, tunable thresholds (golden standard/risk zone/
                      DOA/fraud/top-N), IRIS validity rules (currently
                      empty -- see docs).
  ui/                 PySide6 desktop GUI (file picker/drop -> period
                      picker -> profile + format selection -> progress ->
                      save).
  profile.py          Per-client report-header requisites (Исполнитель/
                      Заказчик/Договор/подписант), persisted as JSON under
                      the user's local app-data directory.

tests/
  fixtures/           Dev-only copies of the reference files, INCLUDING the
                      client's intermediate pivot workbook. Nothing under
                      repair_report/ imports from here -- see
                      docs/REVERSE_ENGINEERING.md §0 for why that boundary
                      matters and must not be crossed.
  test_*.py           pytest suite; every section is checked against either
                      the reference DOCX's own numbers or (preferably, where
                      available) the FULL non-truncated pivot tables in the
                      Kosovov workbook.

packaging/
  repair_report_onefile.spec  PyInstaller build spec -- single-file .exe
                               (default recommendation; must be run ON Windows).
  repair_report.spec          PyInstaller build spec -- onedir (folder) build,
                               kept for debugging/startup-speed cases.
  BUILD.md                    Build steps, onefile vs. onedir trade-off, and
                               the WeasyPrint/GTK caveat.

main.py               Entry point (`python main.py`, and what PyInstaller freezes).
```

## Running from source

```
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
python main.py
```

## Running the tests

```
pip install pytest
pytest tests/ -v
```

All tests validate against the real reference files under
`tests/fixtures/` -- there are no synthetic/mocked datasets. If a test
fails after a change to `analytics/`, treat that as a real regression
against verified ground truth, not a flaky test.

## Known limitations / honesty notes

- **Sections 10 (spare parts) and 11 (tech support) both have real data
  sources now.** Each is a second/third OPTIONAL file the app can load,
  distinct from the main `WR_Consolidated_List_*.xlsx` -- see
  `repair_report/ingest/parts_reader.py` + `repair_report/analytics/parts.py`
  (`docs/REVERSE_ENGINEERING.md` §13) for spare parts, and
  `repair_report/ingest/support_reader.py` + `repair_report/analytics/support.py`
  (`docs/REVERSE_ENGINEERING.md` §14) for tech support. The UI presents
  three equal-weight, parallel drag-and-drop windows (main / parts /
  support); whichever of the optional files are supplied, that section's
  real figures appear automatically -- no checkboxes anywhere. A section
  whose file wasn't supplied is simply omitted from the report, nothing
  fabricated in its place. Loading all three files produces the full
  12-section report in one pass.
- **Section 11.2 "Инженеры поддержки" is intentionally left as a bare
  subheading with an explanatory note, never a table.** The tech-support
  source's `Кто ответил` column is 100% empty, and the reference report's
  own raw XML confirms it has no table there either -- reproducing that
  absence is correct, not a gap. See `docs/REVERSE_ENGINEERING.md` §14.
- **The Windows .exe build has not been run on real Windows.** Two
  PyInstaller specs are provided -- `repair_report_onefile.spec` (single
  `.exe`, the default recommendation) and `repair_report.spec` (onedir
  folder build). The onefile spec's packaging *logic* was validated in the
  Linux sandbox this was built in (frozen bundle correctly resolves its
  config/templates/WeasyPrint data files and reproduces byte-for-byte
  equivalent HTML/PDF/DOCX output to a from-source run), but that only
  proves what's bundled and how paths resolve when frozen -- it does not
  touch the Windows-specific risk. See `packaging/BUILD.md` for the build
  steps and, especially, the WeasyPrint/GTK native-dependency caveat,
  which is the most likely thing to need attention on a real Windows box.
- A few very-fine-grained tie-break orderings (the bottom two rows of the
  15-row DOA table; the one specific TV-model "laggard" name in section
  7.2's narrative blurb) could not be reverse-engineered to an exact rule
  despite multiple attempts -- see `docs/REVERSE_ENGINEERING.md` §4/§6 for
  what was tried. These affect cosmetic ordering only, not the underlying
  figures.

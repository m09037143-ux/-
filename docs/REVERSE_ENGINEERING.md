# Reverse-engineering log

This document records what was independently verified (and rejected) while
building the analytics engine against the four reference files, so this
investigation is never repeated from scratch. Every claim below was checked
by direct computation against the source files, not copied from the
original product brief -- where the brief's assumption turned out wrong,
that is noted explicitly.

Reference files used (see `tests/fixtures/` for the copies the test suite
reads -- **never** read from application code, see §0 below):

- `WR_Consolidated_List_20260902_июль.xlsx` -- single-month export, July 2026, 1535 rows.
- `WR_Consolidated_List_20260902_весь.xlsx` -- the real runtime input shape: the same export accumulated across April-July 2026, 5124 rows.
- `Final_Report_2026_июль_Косовов.xlsx` -- the preparer's own intermediate pivot workbook. **Dev/test fixture only**, see §0.
- `Final_Report_2026_июль.DOCX` -- the manually-produced reference report for July 2026.

## §0. Runtime data vs. test fixtures -- do not blur this line

The application's ONLY runtime input is one `WR_Consolidated_List_*.xlsx`
file (`repair_report/ingest/excel_reader.py`). The Kosovov pivot workbook is
not a file format the application ever reads or offers a slot for -- it
exists purely as a source of verified expected values for the test suite
(`tests/fixtures/`, wired up only via `tests/conftest.py`). No module under
`repair_report/{ingest,analytics,ui,render}` may import from or reference
`tests/`. If a future change seems to require reading a second file at
runtime to "get the right numbers", that is a sign the underlying formula
hasn't actually been found yet -- go back to reproducing it from the one
real input instead.

## §1. Continuation rows (multi-part repairs) -- CONFIRMED, not a bug

**Finding.** A small number of rows have every column blank except
`№ п/п`, `Наименование артикула`, `Артикул`, and the `int` sequence column
(values 1 or 2). These are extra spare-part lines for a repair whose "main"
row already exists elsewhere with the same `№ п/п` (e.g. `№ п/п` 190 in the
July file: a washing-machine repair with two part lines, one fully
populated, one blank apart from the part fields).

**Verified rule** (matches the Kosovov workbook's own pivots and the
DOCX's headline counts exactly):

- Do **not** deduplicate or merge continuation rows back into their
  "parent" row for any counting purpose. Each row -- continuation rows
  included -- is one unit for `COUNT(*)`-style aggregates.
- Blank categorical fields on such a row are replaced with an explicit
  placeholder label **before** grouping:
  - `Наименование АСЦ` blank -> `"Неизвестный АСЦ"`
  - `Регион АСЦ` blank -> `"—"`
  - `Изготовитель` blank -> `"Не указан"`
  - `Бренд` blank -> `"Бренд не указан"`
  - `Категория техники` blank -> literal string `"nan"` (yes, literally the
    text a naive `str(NaN)` would produce -- verified against the
    reference DOCX's section-3 table, which shows a real `nan` row, not a
    hidden or relabeled one).
- **Exception:** `Уровень ремонта` blank is folded into the `ANR` bucket,
  NOT given its own placeholder. Verified by exact reproduction of the
  reference's ANR count (699) and sum (999 575 руб.): 698 rows literally
  coded `AN` + the one blank-level continuation row (whose own amount is 0)
  = 699/999 575, and the network-wide ANR share text ("Ср. по сети: 46%")
  only reproduces at 699/1535 (45.54%, rounds to 46), not 698/1535 (45.47%,
  rounds to 45). This is a genuinely different rule from the other four
  fields above -- don't "fix" it to be consistent, it was checked
  independently and confirmed by three separate figures.
- Where a continuation row needs a period assigned (its own `Дата акта` is
  blank), resolve it via its `№ п/п` match to the nearest dated row sharing
  that number. This is the ONE place `№ п/п` linkage is used at all --
  never to inherit ASC/region/brand/etc.

Target numbers this reproduces exactly: July = 1535 repairs, 104 ASCs (103
real + placeholder), 67 regions (66 real + placeholder), 10 manufacturers
(9 real + placeholder), 23 brands (22 real + placeholder); June (as
"previous period", computed from the same multi-month file, no separate
history store) = 1213 repairs, sum 2 086 875 руб.

Implementation: `repair_report/analytics/common.py`
(`fill_missing_categoricals`, `repair_level_code`),
`repair_report/analytics/periods.py` (`assign_periods`). Tests:
`tests/test_continuation_rows.py`, `tests/test_periods.py`.

## §2. Period detection -- `Дата акта` is a batch-closing date, not a repair date

Every row in one month's batch carries the SAME `Дата акта` (the last
calendar day of that month). Group by `(year, month)` of `Дата акта`
directly -- do not try to derive a period from the day-of-month, it's
always the last day and carries no extra signal. The real per-repair dates
are `Дата поступления` / `Дата ремонта` (used for SLA/duration/DOA), which
vary normally.

The multi-month file is strictly append-only (no `Номер акта` appears in
two different months), so filtering by period needs no deduplication
across periods. "Previous period" is always the calendar month immediately
before the selected current one, computed by filtering the SAME uploaded
file -- there is no separate history store, and if that calendar month
isn't present in the file the UI says so explicitly rather than falling
back to some other older period.

## §3. Equipment category classification has a documented, data-driven fallback

Base rule (§1.4 of the product spec): map `Категория техники` via
`config/category_mapping.json`; blank -> `"nan"`.

**Finding requiring a fix to that base rule:** in July, 4 rows for brand
`TOPDEVICE` / model `TDWC24ВН3260V` have a **blank** `Категория техники`,
yet the reference's TV total is 1344, not 1340 -- exactly +4. The client's
own Kosovov `ТВ_Модели` sheet lists this exact model under TV with count 4,
confirming these rows are meant to count as TV despite the blank category.

No column-only rule recovers this (every occurrence of this exact model
anywhere in the 4-month file has a blank category -- there's no non-blank
sibling to borrow from). What *does* work, verified: this brand's every
OTHER (non-blank-category) row in the same period maps to the single
consolidated group "ТВ" -- i.e. the brand is behaviorally homogeneous. So
the implemented fallback is: **a blank category is resolved to a brand's
consolidated group only when 100% of that brand's other rows this period
map to exactly one group.** This is data-driven (no hardcoded model names)
and deliberately conservative -- a brand that legitimately makes more than
one kind of appliance (e.g. `General Electronics (GE)`, which spans СВЧ, ТВ,
Холодильники, Духовой шкаф) is left as `"nan"` for its own blank rows,
same as a genuine continuation row.

The same fallback had to be extended to section 7.1's TV-diagonal
breakdown: those 4 rows also have no diagonal-range text to parse, but the
reference's 24-39" bucket is 595, not 591 (+4, same 4 rows). Fix: when a
row is ТВ via the brand fallback, parse a plausible diagonal number out of
the **model code** instead (`TDWC24...` -> `24` -> falls in the 24-39"
bucket) using the same numeric ranges the raw categories themselves use.

Implementation: `repair_report/analytics/common.py`
(`add_equipment_category_group`), `repair_report/analytics/tv_analysis.py`
(`_diagonal_label_from_row`).

**Update, 2026-09-09 -- the `"nan"` row itself is now dropped from section
3.** The finding above (and §1's continuation-row rule) still stands: a
blank category resolves to `"nan"` in `equipment_category_group()`, and
that's still the right underlying classification. What changed is what
section 3 DOES with rows that resolve to `"nan"`: the July reference report
literally shows a `nan` line in its category tables, and the original
implementation reproduced that exactly (verified: count 1, sum 0 ₽,
matching the DOCX 1:1). After hands-on testing against real August data,
the client explicitly asked for that row to be removed -- it reads as a
meaningless artifact, not a real equipment type, in day-to-day use even
though it happened to match the one reference month available during
development. `repair_report/analytics/categories.py` now filters `"nan"`
rows out of both category tables (and their own ИТОГ, and the section-3
chart) before building them. This is a deliberate, client-directed
divergence from the literal July DOCX for this one row -- not a mistake in
the earlier verification -- and does not touch any other section (overall
KPI counts, and the ASC/region/manufacturer/brand placeholder labels from
§1, are unaffected). See `categories.py`'s module docstring and
`tests/test_categories.py` for the updated expectations.

## §4. Tie-break order for group ranking tables -- two DIFFERENT, both confirmed, rules

When two groups in a ranking table (sections 3/4/6/7) share the same value
in the primary sort column, the reference report does not break the tie
alphabetically. Two different tie-break rules were confirmed by direct
comparison, for two different purposes -- do not conflate them:

1. **Display-order tie-break** (which row prints first when two groups are
   tied): **earliest first-appearance row in the source file, ascending.**
   Confirmed on section 3's category table: `'Плиты'` (first seen at row 16)
   sorts ahead of `'Планшет'` (first seen at row 657) despite `'Планшет' <
   'Плиты'` alphabetically, because both have count=2.
   Implementation: `repair_report/analytics/tables.py`
   (`build_dynamics_table`'s `first_seen_order` + stable `mergesort`).

   **Caveat found later:** this rule does NOT hold for the FULL
   region/ASC/manufacturer listings in the Kosovov workbook specifically --
   several tie-break attempts (first-occurrence within-period,
   first-occurrence across the whole multi-month file, alphabetical
   ascending/descending, ASC-code ascending/descending, sum-descending,
   delta-descending) were tried against concrete tied pairs (e.g.
   `Свердловская область` vs `Воронежская область`, both count=30; `АО
   "ВТТЦ "ОРБИТА-СЕРВИС""` vs `ООО "СЕРВИСНЫЙ ЦЕНТР "РЕВАНШ""`, both
   count=23) and NONE reproduced Kosovov's specific order consistently.
   Likely an Excel-internal artifact (manual pivot refresh order) rather
   than a derivable rule. **Decision:** ship the first-seen-ascending rule
   anyway (it's the one rule that IS derivable and consistent, and it's
   what produces the section-3 table correctly); for regions/ASC/
   manufacturers, tests compare table CONTENT against Kosovov
   (order-independent), not row order, and the DISPLAY order (top-15) is
   instead verified directly against the DOCX's own top-15 tables, which
   this implementation reproduces exactly. See `tests/test_regions_asc.py`,
   `tests/test_manufacturers.py` docstrings.

2. **Leader/laggard blurb tie-break** (the "Абсолютным лидером
   является... / Наименьшие показатели... зафиксированы у..." narrative
   text in sections 4/4.1/6/7.2/8.1): a SEPARATE, independently-confirmed
   rule -- rank by the table's own primary metric (count for
   regions/ASC/brands/visits, sum for manufacturers), and among ties,
   pick by **earliest first-occurrence row, both for the leader AND the
   laggard end.** Confirmed on THREE independent cases:
   - ASC laggard: multiple ASCs tied at count=1; `'ИП Лавринов Алексей
     Владимирович'` (first occurrence row 119) wins over `'ИП Важенин
     Вячеслав Александрович'` (lower sum, 1000 vs 1100) and over
     `'Неизвестный АСЦ'` (which must ALSO be excluded from candidacy --
     placeholder labels never win leader/laggard).
   - Brand laggard: `'Home'` (sum 1100) vs `'Maunfeld'` (sum 1200), both
     count=1; reference picks `'Maunfeld'`, which occurs earlier in the raw
     file (row 279 vs row 1097) -- this independently RULES OUT any
     sum-based tie-break (min-sum would pick Home; max-sum would still be
     coincidental) and confirms first-occurrence.
   - ASC-visits laggard: many ASCs tied at 1 visit; `'ООО "МОРОЗКО"'`
     (earliest occurrence among visit rows, row 94) wins over the
     numerically-smallest-sum candidate (`'ООО "СИНТЕЗ"'`, 2125 руб.).

   **Known unresolved case:** section 7.2's TV-model laggard (117 models
   tied at count=1 in July) does NOT reproduce with first-occurrence,
   alphabetical, or smallest-TV-brand tie-breaks -- the reference picks
   `'CRL430'`, which is none of those. Given three failed hypotheses and
   the sheer tie-pool size (117-way tie), this is left undetermined; the
   test (`tests/test_tv_analysis.py::test_tv_model_leader_follower`) only
   asserts the (unambiguous) leader and the laggard's count, not its name.

   Implementation: `repair_report/analytics/regions_asc.py`
   (`leader_follower_from_dynamics`, `leader_follower_from_visits`),
   `repair_report/analytics/tables.py` (`DynamicsRow.first_seen_rank`).

## §5. KPI dynamics must use full-precision inputs, not rounded display values

The reference's "Средний чек" dynamic ("+9 ₽ (+0.5%)") only reproduces from
the UNROUNDED average-check values (1729.64 -> 1720.42, diff 9.22 -> rounds
to 9). Naively subtracting the ROUNDED display values (1730 - 1720 = 10)
gives the wrong answer. Always feed `format_dynamics()` full-precision
numbers and let it round only the final diff/percentage for display.

## §6. DOA (dead-on-arrival) formula

`(Дата поступления - Дата продажи)` in days, **inclusive on both ends**:
`0 <= diff <= 30`. Verified against all 15 of the reference's top-15 DOA
rows matching exactly at this exact boundary (`<=30`, not `<30`) -- e.g.
`TUVIO/TD55UFBHH12` is 24 at `<=30` vs 23 at `<30`, and the reference shows
24. `Дата поступления` is the correct basis column, not `Дата ремонта`
(the latter gives materially different, non-matching counts).
Implementation/tests: `repair_report/analytics/sla_quality.py::doa_table`,
`tests/test_sla_quality.py`.

Minor unresolved detail: which two specific models occupy the last two
(5-count) slots of the top-15 varies depending on tie-break order among
many DOA models sharing that count; the top-13 unique-value rows match
exactly, and the total DOA row count (377) matches exactly.

## §7. Golden standard (5.1) / Risk zone (5.2) both need a claims-count floor

The product spec's original text suggested the risk-zone claims floor might
be unnecessary ("looks unconditional in the example"). Direct computation
shows this is wrong for BOTH 5.1 and 5.2:

- Without a floor, risk-zone naively finds 34 qualifying ASCs (many with
  1-4 total claims, where a single ANR repair trivially produces a 100%
  share); the reference table has exactly 11 rows, all with >=5 claims.
- Without a floor, golden-standard naively finds 7 qualifying ASCs (1-3
  claims each); the reference text is "не выявлено" (none) -- only a
  min-claims floor of >=5 correctly excludes all 7 and reproduces zero.

Both now share `min_claims_threshold: 5` in `config/app_settings.json`,
confirmed to be the exact threshold (a floor of >=4 would still let some
low-claim entries qualify; the smallest included ASC in the reference risk
zone table, `'ИП Гилазова Наталья Магомедаминовна'`, has exactly 5 claims).

The network-wide ANR share used as the risk-zone baseline is computed with
the blank-level-folds-into-ANR rule from §1 (699/1535, not 698/1535 or
698/1534) -- see §1's rounding discussion.

## §8. IRIS chain table (8.1): column order, and the "full" reference list is itself capped

Chain = `IRIS код секции -> IRIS код дефекта -> IRIS код ремонта` (verified
exactly against the reference's `ТВ_IRIS` sheet codes -- this matches the
raw column identities, not the more ambiguous prose in the original product
spec, which quoted a different apparent field order).

**Important:** the Kosovov workbook's "full" `ТВ_IRIS` sheet has exactly 50
rows summing to 1144 -- but July actually has 194 distinct
(section,defect,repair) combinations among 1342 fully-coded TV rows. The
50-row reference list is ITSELF a top-50 truncation, not an exhaustive
list. Confirmed: this implementation's own top-50 (by the same count-desc
ranking) sums to exactly 1144 and matches the Kosovov sheet row-for-row.
When testing against this fixture, use `top_n=50`, not "no limit" -- there
is no larger number that will ever match, because the fixture itself was
already truncated by whatever tool produced it.

## §9. Section 8.2 (defect text) is genuinely case-sensitive in the reference

`"не включается"` (118 occurrences) and `"Не включается"` (91) are kept as
distinct groups in the reference table -- confirmed by reproducing the same
split exactly. `config/app_settings.json`'s `defect_text_case_insensitive`
flag exists to fold them together, default `false` to match the reference.

## §10. Section 8.3 (IRIS coding errors) -- do not guess a validity rule

An earlier draft of this implementation defaulted `iris_rules.json`'s
`required_fields` to the three IRIS code columns (flag a TV row missing any
of them as an error). This was WRONG: July has TV rows with a genuinely
blank `IRIS код секции` that the reference does NOT flag (reference: zero
errors for July). `required_fields` now ships EMPTY. The output *shape* (7
columns: АСЦ/Бренд/Модель/Секция/Дефект/Ремонт/Ошибки) is still confirmed
against the Kosovov `IRIS_ошибки` sheet's headers, but the actual validity
rule set for what counts as a "coding error" remains genuinely unknown --
July simply has none, so the one period of ground truth available proves
the shape but not the rule.

## §11. Sections 10 (spare parts) and 11 (tech support) -- confirmed NOT derivable from this export

See `repair_report/analytics/parts_support.py`'s module docstring for the
full evidence trail (row-count mismatches, a status vocabulary that exists
nowhere in the 38 columns, ASC-naming-convention mismatches, and -- the
strongest signal -- the preparer's OWN Kosovov pivot workbook, which
covers every other section in full, has no sheet related to parts or
support at all). These two sections ship OFF by default
(`experimental_sections.enabled: false`) and, even when enabled, render as
an explicit "needs clarification" placeholder rather than fabricated
numbers. Do not re-open this investigation without a new data source from
the client -- the `int` column and the ASC-naming mismatch were both
already run down and are dead ends, not oversights.

## §12. Column resolution fuzzy-match cutoff

`difflib.get_close_matches` needs a cutoff high enough to avoid false
positives between genuinely different columns that happen to share a long
common prefix. `"Наименование АСЦ"` vs `"Наименование артикула"` scores
0.757 similarity -- close enough to falsely resolve if the cutoff is 0.72
(an earlier draft's value), silently corrupting the loaded data instead of
raising `ColumnResolutionError`. Cutoff raised to 0.85, which still comfortably
tolerates real cosmetic drift (a trailing-space + lowercase rename of the
same header scores 0.94+) while rejecting the above collision. See
`tests/test_ingest.py`.

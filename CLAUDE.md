# CLAUDE.md — conventions for stats.clinbolt.com

Static multi-dashboard site published at https://stats.clinbolt.com, built from
the ClinicalTrials.gov registry. Read this before changing anything.

## Shape of the project

```
site/       everything served, exactly as it appears on disk
pipeline/   Python that turns the registry into small precomputed JSON
deploy/     Caddy config, VM bootstrap, publish scripts, weekly refresh
```

The split is the core idea: **all aggregation happens at build time**, the
browser only filters an already-computed file. A dashboard page must never
compute statistics from raw records, and must never call the ClinicalTrials.gov
API at runtime.

## Hard rules

- **No build step, no framework, no bundler.** Plain HTML, CSS and ES5-compatible
  vanilla JS. `python -m http.server` from `site/` has to be enough to preview.
- **No CDN at runtime.** Third-party code is vendored into
  `site/assets/vendor/`. ECharts is pinned at **5.5.1**; if you change it, update
  this line and re-test every chart.
- **Python: standard library plus `requests` only.** No pandas, no numpy. The VM
  has 1 GB of RAM.
- **Aggregate in a streaming fashion.** Never build a list of every observation.
  The established pattern is a `collections.Counter` of value → frequency per
  cell, which keeps percentiles exact while staying small (`pipeline/common/stats.py`).
- **Never SSH into or run commands against the production VM.** Write the script,
  document the command, let the owner run it.
- **Every excluded record is counted and surfaced.** A number dropped silently is
  a bug. Exclusion reasons live in `CONFIG` and are rendered in the page's
  methodology section.

## The ClinicalTrials.gov API (verified live, 2026-09-15)

`GET https://clinicaltrials.gov/api/v2/studies`, `pageSize=1000` (honoured),
follow `nextPageToken`, `countTotal=true` on the first page only.
At the time of writing the registry holds ~602,900 studies, about 603 pages.

Confirmed field paths:

```
protocolSection.identificationModule.nctId
protocolSection.statusModule.overallStatus
protocolSection.statusModule.startDateStruct.date
protocolSection.statusModule.lastUpdatePostDateStruct.date
protocolSection.designModule.enrollmentInfo.count
protocolSection.designModule.enrollmentInfo.type
protocolSection.designModule.studyType
protocolSection.designModule.phases
```

Things that will bite you, all confirmed against live data:

- **`phases` is an array, and combinations have no enum value of their own.** The
  `Phase` enum is only `NA, EARLY_PHASE1, PHASE1, PHASE2, PHASE3, PHASE4`.
  "Phase 1/2" is `["PHASE1","PHASE2"]`. Map from the *sorted tuple*, never a scalar.
- **`phases` is absent entirely for ~24% of studies**, nearly all observational.
  That is the `NOT_SPECIFIED` group, not an error.
- **`enrollmentInfo.type` can be missing while `count` is present** (~1.4%).
  Example: NCT00081835 reports 400 with no type.
- **`overallStatus` has 14 values including `WITHHELD`**, which belongs to no
  status group and is excluded with its own counted reason.
- **`studyType` can be null** (~0.2%).
- Enrollment reaches into the millions, so any log axis or ECDF grid must be
  sized from the observed maximum (`stats.grid_ceiling`), not hard-coded.

Be polite to the API: ~1 request/second, descriptive User-Agent, exponential
backoff on 429/5xx. Raw pages are cached as gzipped JSONL under `.cache/ctgov/`;
a run is only cached as complete once a `.meta.json` sidecar is written, and
`--limit-pages` runs get their own cache file so a sample is never mistaken for
a full pull. `--refresh` bypasses the cache.

## Working on the pipeline

Develop with `--limit-pages 2` first. Only run the full pull once the small run
produces valid output and the page renders.

```bash
python pipeline/study_size/build.py --limit-pages 2   # ~2000 records, seconds
python -m pytest pipeline/tests -q                    # must stay green
cd pipeline && python -m common.validate ../site/dashboards/study-size/data/study_size.json --min-records 0
```

Classification lives in exactly one place: the `CONFIG` dict at the top of
`build.py`. Exclusions are applied as an ordered ladder, first match wins, so the
accounting never double-counts. If you add a rule, add a test in
`pipeline/tests/test_classify.py` next to the existing ones.

Output budget: **under 2 MB**. The build prints the size and exits non-zero if it
is exceeded. Keep it small by omitting empty cells and by sharing one ECDF x-grid
across every series in `metadata.ecdf_grid` rather than storing x values per cell.

## Working on the front end

- Shared theme and layout: `site/assets/css/common.css`. Dashboard stylesheets
  hold **overrides only** — if another dashboard would want the rule, it belongs
  in the shared file.
- Shared chart theme, formatting and colours: `site/assets/js/charts-common.js`.
  Read colours through `ClinBoltCharts.segmentColor()`, never hard-code a hex in a
  dashboard, so light/dark switching keeps working.
- Header and footer are injected by `site/assets/js/nav.js` from
  `site/dashboards.json`. A page declares itself with
  `<body data-dashboard-id="...">` so the nav can highlight it.
- **Colour is never the only signal.** Estimated series are hatched (bars),
  dashed (lines) and hollow (box plots); Actual series are solid. Legend text
  always names the type. The palette is Okabe-Ito derived and colour-blind safe.
- Both light and dark mode must work, via `prefers-color-scheme`. Chart colours
  come from CSS custom properties and are re-read on theme change.
- Every page needs a loading state and a real error state. A missing data file
  must produce a clear message, not a blank panel or a console stack trace.
- Format every number with thousands separators (`ClinBoltCharts.fmtInt`).
- Responsive down to 390px with no horizontal scrolling on the page body.

## Adding a dashboard

1. `cp -r site/dashboards/_template site/dashboards/<name>` and replace every
   `CHANGEME`, including the three `/dashboards/_template/` asset paths and
   `data-dashboard-id`.
2. If it needs data, add `pipeline/<name>/build.py`, reusing
   `common/ctgov_client.py` and `common/stats.py`. Write both
   `<name>.json` and the small `meta.json` sidecar the landing page reads.
3. Add one entry to `site/dashboards.json`.
4. Add the dashboard to the `DASHBOARDS` array in `deploy/refresh.sh`.
5. Add tests under `pipeline/tests/`.

Nothing else should need touching. If it does, the abstraction is wrong — fix
that instead of special-casing.

## Verifying before you call it done

```bash
python -m pytest pipeline/tests -q
cd site && python -m http.server 8000
```

Check the landing page and every dashboard with the browser console open, at
desktop and mobile widths, in light and dark mode. Zero console errors is the
bar, including 404s for favicons and data files.

## Editing notes

Large source files with quotes and braces have repeatedly broken shell heredocs
in this project; write them with the file-writing tool instead of `cat <<EOF`.

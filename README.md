# ClinBolt Stats

Static dashboards on the clinical trial landscape, built from the
[ClinicalTrials.gov](https://clinicaltrials.gov/) public API and published at
**https://stats.clinbolt.com**.

The first dashboard, *How Big Are Studies?*, shows how many participants studies
enrol — separating the sponsor's **estimated** target from the **actual**
reported figure, and studies still **active** from those already **closed**.

No build step, no framework, no CDN. A Python pipeline precomputes a small JSON
summary; the pages are plain HTML, CSS and vanilla JavaScript that filter that
file in the browser.

---

## Layout

```
site/                       everything that gets served
  index.html                landing page, renders cards from dashboards.json
  404.html
  dashboards.json           the dashboard registry (drives nav and cards)
  assets/
    css/common.css          shared theme, layout, header/footer, light + dark
    js/nav.js               injects the shared header and footer
    js/charts-common.js     shared ECharts theme, colours, formatting
    vendor/echarts.min.js   ECharts 5.5.1, vendored (no CDN at runtime)
    favicon.svg
  dashboards/
    _template/              copyable skeleton; renders as-is from sample data
    study-size/             "How Big Are Studies?"
      index.html  app.js  style.css
      data/                 generated, gitignored

pipeline/
  common/ctgov_client.py    paging, throttling, retries, gzipped JSONL cache
  common/stats.py           binning, exact percentiles, ECDF (pure functions)
  common/validate.py        output checks, run before anything is published
  study_size/build.py       classification CONFIG + aggregation
  tests/                    pytest
  requirements.txt

deploy/
  Caddyfile                 HTTPS, compression, cache and security headers
  setup_vm.sh               idempotent VM bootstrap
  update.sh                 publish from a checkout on the VM (git flow)
  deploy.sh                 push site/ and pipeline/ from a workstation (rsync)
  refresh.sh                rebuild, validate, publish atomically
  stats-refresh.service     systemd unit
  stats-refresh.timer       weekly schedule
```

---

## Local development

Requires Python 3.11+.

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r pipeline/requirements.txt
```

**1. Build some data.** Start small — this pulls 2 pages (~2,000 studies) in a
few seconds and caches the raw responses:

```bash
python pipeline/study_size/build.py --limit-pages 2
```

**2. Serve the site.** Pages reference assets with absolute paths, so serve from
`site/`, not from the repository root:

```bash
cd site
python -m http.server 8000
```

Then open <http://localhost:8000/>.

**3. Run the tests.**

```bash
python -m pytest pipeline/tests -q
```

### Full data build

Only after the small build and the page both look right. The full registry is
~603 pages at roughly one request per second, so allow **10–20 minutes**:

```bash
python pipeline/study_size/build.py
```

Raw pages are cached under `.cache/ctgov/`, so re-running the aggregation after a
code change is instant. Use `--refresh` to force a refetch.

| Flag | Effect |
| --- | --- |
| `--limit-pages N` | stop after N API pages (development) |
| `--refresh` | ignore the cache and refetch from the API |
| `--out DIR` | write the JSON somewhere other than the dashboard's `data/` |
| `--cache-dir DIR` | where to keep the raw gzipped JSONL cache |

---

## Adding a new dashboard

1. **Copy the template.**

   ```bash
   cp -r site/dashboards/_template site/dashboards/my-dashboard
   ```

   Replace every `CHANGEME`, set `<body data-dashboard-id="my-dashboard">`, and
   update the three `/dashboards/_template/` asset paths to your folder. The
   template renders immediately from inline sample data, so you can check it in
   the browser before writing any pipeline code.

2. **Add a pipeline, if it needs data.** Create `pipeline/my_dashboard/build.py`,
   reusing `common/ctgov_client.py` for fetching and `common/stats.py` for
   binning, percentiles and ECDFs. Write the summary JSON plus the small
   `meta.json` sidecar the landing page reads for the "Updated" badge. Then point
   `DATA_URL` at it and set `USE_SAMPLE_DATA = false` in your `app.js`.

3. **Register it.** Add one entry to `site/dashboards.json`:

   ```json
   {
     "id": "my-dashboard",
     "title": "My Dashboard",
     "nav_title": "Short Name",
     "description": "One sentence for the card.",
     "path": "/dashboards/my-dashboard/",
     "category": "Study design",
     "status": "live",
     "meta_file": "/dashboards/my-dashboard/data/meta.json"
   }
   ```

   `status: "coming-soon"` renders a muted, non-clickable placeholder card and
   keeps the dashboard out of the nav.

4. **Add it to the weekly refresh.** One line in the `DASHBOARDS` array in
   `deploy/refresh.sh`, as `"<pipeline dir>:<site dir>"`.

5. **Add tests** under `pipeline/tests/`.

The header, footer, nav highlighting, theming, card grid and freshness badge all
follow from the registry entry. Nothing else needs editing.

---

## Conventions worth knowing

- **All statistics are precomputed.** The browser filters a finished summary; it
  never aggregates raw records and never calls the API.
- **Exact percentiles without the memory.** Each segment keeps a `Counter` of
  enrollment value → frequency rather than a list of observations, so a 600,000
  record pull stays comfortable on a 1 GB VM while percentiles remain exact.
- **Every dropped record is counted.** Exclusion reasons are defined in the
  `CONFIG` dict in `build.py` and rendered, with counts, in the page's
  methodology section.
- **Colour is never the only signal.** Estimated series are hatched, dashed or
  hollow; Actual series are solid. The palette is colour-blind safe.
- **Output budget is 2 MB.** The build prints the file size and fails if exceeded.

See [CLAUDE.md](CLAUDE.md) for the full conventions, including the
ClinicalTrials.gov API quirks that the pipeline works around.

---

## Deployment

See **[DEPLOY.md](DEPLOY.md)** for the full Oracle Cloud checklist, from
creating the instance onwards. The short version, once the VM exists, ports 80
and 443 are open in the OCI Security List, and DNS points at it:

```bash
ssh ubuntu@<VM_IP>
git clone https://github.com/TechDlx/stats.clinbolt.git ~/stats.clinbolt
sudo bash ~/stats.clinbolt/deploy/setup_vm.sh    # packages, Caddy, firewall, timer
sudo bash ~/stats.clinbolt/deploy/update.sh      # publish site/ and pipeline/
sudo systemctl start --no-block stats-refresh.service   # first data build, 10-20 min
```

Later updates are one command:

```bash
ssh ubuntu@<VM_IP> 'sudo bash ~/stats.clinbolt/deploy/update.sh --pull'
```

`deploy/deploy.sh ubuntu@<VM_IP>` is the alternative for pushing an uncommitted
working tree from a machine that has `rsync`.

---

## Data source and disclaimer

Data comes from ClinicalTrials.gov, a service of the U.S. National Library of
Medicine, read through its public API v2. Registry data is **sponsor-reported**
and is not independently verified.

This site is for research and informational purposes only. It is **not medical
advice** and must not be used to guide diagnosis or treatment.

/* "How Big Are Studies?" -- all rendering runs client-side from one
 * precomputed JSON file.  Changing a filter never refetches anything.
 */
(function () {
  "use strict";

  var DATA_URL = "/dashboards/study-size/data/study_size.json";
  var C = window.ClinBoltCharts;

  var data = null;
  var charts = {};

  var state = {
    studyType: "all",
    phase: "all",
    showUnknown: false,
    histMode: "count" // or "pct"
  };

  var CORE_SEGMENTS = [
    "active_estimated",
    "active_actual",
    "closed_estimated",
    "closed_actual"
  ];

  // ------------------------------------------------------------- data access
  function cellKey() {
    return state.studyType + "|" + state.phase;
  }

  function currentCell() {
    return data.cells[cellKey()] || {};
  }

  /** Segments to draw, honouring the Unknown-status toggle. */
  function activeSegments() {
    return C.SEGMENT_KEYS.filter(function (key) {
      if (key.indexOf("unknown") === 0 && !state.showUnknown) return false;
      var entry = currentCell()[key];
      return entry && (entry.n > 0 || entry.zero > 0);
    });
  }

  function segment(key) {
    return currentCell()[key] || null;
  }

  function segLabel(key) {
    return data.dimensions.segment_labels[key] || key;
  }

  /** Segment described as a noun phrase, for tooltips that read as sentences. */
  function segPhrase(key) {
    var meta = C.SEGMENT_META[key];
    if (!meta) return segLabel(key);
    var who = meta.status === "unknown" ? "unknown-status studies" : meta.status + " studies";
    var what =
      meta.kind === "estimated"
        ? "reporting an estimated target"
        : "reporting actual enrollment";
    return who + " " + what;
  }

  /** Studies in a segment, including the zero-enrollment ones. */
  function segTotal(key) {
    var entry = segment(key);
    return entry ? entry.n + entry.zero : 0;
  }

  // ------------------------------------------------------------- filters
  function buildFilters() {
    var typeSelect = document.getElementById("filter-study-type");
    var phaseSelect = document.getElementById("filter-phase");
    var dims = data.dimensions;

    dims.study_types.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = dims.study_type_labels[value] || value;
      typeSelect.appendChild(option);
    });

    dims.phases.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = dims.phase_labels[value] || value;
      phaseSelect.appendChild(option);
    });

    typeSelect.value = state.studyType;
    phaseSelect.value = state.phase;

    typeSelect.addEventListener("change", function () {
      state.studyType = typeSelect.value;
      renderAll();
    });
    phaseSelect.addEventListener("change", function () {
      state.phase = phaseSelect.value;
      renderAll();
    });
    document.getElementById("filter-unknown").addEventListener("change", function (event) {
      state.showUnknown = event.target.checked;
      renderAll();
    });

    var countButton = document.getElementById("mode-count");
    var pctButton = document.getElementById("mode-pct");
    function setMode(mode) {
      state.histMode = mode;
      countButton.setAttribute("aria-pressed", String(mode === "count"));
      pctButton.setAttribute("aria-pressed", String(mode === "pct"));
      renderHistogram();
    }
    countButton.addEventListener("click", function () {
      setMode("count");
    });
    pctButton.addEventListener("click", function () {
      setMode("pct");
    });
  }

  function renderFilterSummary() {
    var dims = data.dimensions;
    var total = 0;
    activeSegments().forEach(function (key) {
      total += segTotal(key);
    });

    var parts = [
      dims.study_type_labels[state.studyType],
      dims.phase_labels[state.phase]
    ];
    var node = document.getElementById("filter-summary");

    if (total === 0) {
      node.textContent =
        "No studies match this combination (" + parts.join(" · ") + ").";
      return;
    }
    node.textContent =
      "Showing " + C.fmtInt(total) + " studies — " + parts.join(" · ") +
      (state.showUnknown ? " · including Unknown status" : "");
  }

  // ------------------------------------------------------------- KPIs
  function renderKPIs() {
    var container = document.getElementById("kpis");
    container.innerHTML = "";

    var included = 0;
    var estimated = 0;
    var actual = 0;
    activeSegments().forEach(function (key) {
      var n = segTotal(key);
      included += n;
      if (C.isEstimated(key)) {
        estimated += n;
      } else {
        actual += n;
      }
    });

    function card(label, value, sub, color) {
      var node = document.createElement("div");
      node.className = "kpi";
      if (color) node.style.borderLeftColor = color;
      var labelNode = document.createElement("span");
      labelNode.className = "kpi-label";
      labelNode.textContent = label;
      var valueNode = document.createElement("span");
      valueNode.className = "kpi-value";
      valueNode.textContent = value;
      node.appendChild(labelNode);
      node.appendChild(valueNode);
      if (sub) {
        var subNode = document.createElement("span");
        subNode.className = "kpi-sub";
        subNode.textContent = sub;
        node.appendChild(subNode);
      }
      container.appendChild(node);
    }

    card("Studies included", C.fmtInt(included), "matching the current filters");

    CORE_SEGMENTS.forEach(function (key) {
      var entry = segment(key);
      var median = entry && entry.pct && entry.pct.p50 !== undefined ? entry.pct.p50 : null;
      card(
        "Median — " + segLabel(key),
        median === null ? "—" : C.fmtCount(median),
        entry ? "n = " + C.fmtInt(entry.n) : "no studies",
        C.segmentColor(key)
      );
    });

    var typed = estimated + actual;
    card(
      "Estimated vs Actual",
      typed ? C.fmtPct((estimated / typed) * 100, 0) + " / " + C.fmtPct((actual / typed) * 100, 0) : "—",
      "share reporting each enrollment type"
    );
  }

  // ------------------------------------------------------------- histogram
  function binLabels() {
    return data.metadata.bins.map(function (bin) {
      return bin.label;
    });
  }

  function renderHistogram() {
    var chart = charts.histogram;
    if (!chart) return;
    var base = C.baseOption();
    var segments = activeSegments();
    var asPct = state.histMode === "pct";

    var series = segments.map(function (key) {
      var entry = segment(key);
      var values = entry.bins.map(function (count) {
        if (!asPct) return count;
        return entry.n ? (count / entry.n) * 100 : 0;
      });
      return {
        name: segLabel(key),
        type: "bar",
        data: values,
        itemStyle: C.barItemStyle(key),
        barMaxWidth: 26,
        emphasis: { focus: "series" }
      };
    });

    chart.setOption(
      {
        textStyle: base.textStyle,
        legend: base.legend,
        grid: Object.assign({}, base.grid, { top: 44, bottom: 16 }),
        tooltip: Object.assign({}, base.tooltip, {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          formatter: function (params) {
            if (!params.length) return "";
            var lines = ["<strong>" + params[0].axisValue + " participants</strong>"];
            params.forEach(function (item) {
              var value = asPct ? C.fmtPct(item.value) : C.fmtInt(item.value) + " studies";
              lines.push(item.marker + " " + item.seriesName + ": <strong>" + value + "</strong>");
            });
            return lines.join("<br>");
          }
        }),
        xAxis: Object.assign({}, base.axisCommon, {
          type: "category",
          data: binLabels(),
          name: "Enrollment (participants)",
          nameLocation: "middle",
          nameGap: 34,
          splitLine: { show: false },
          axisLabel: Object.assign({}, base.axisCommon.axisLabel, {
            interval: 0,
            rotate: window.innerWidth < 760 ? 45 : 0
          })
        }),
        yAxis: Object.assign({}, base.axisCommon, {
          type: "value",
          name: asPct ? "% of segment" : "Studies",
          nameLocation: "end",
          nameTextStyle: { color: C.cssVar("--text-muted"), fontSize: 11, align: "left" },
          nameGap: 14,
          axisLabel: Object.assign({}, base.axisCommon.axisLabel, {
            formatter: asPct
              ? function (value) {
                  return value + "%";
                }
              : C.logAxisLabel
          })
        }),
        series: series
      },
      true
    );
  }

  // ------------------------------------------------------------- small multiples
  function renderSmallMultiples() {
    // One shared vertical scale so the four panels are directly comparable.
    var maxPct = 0;
    CORE_SEGMENTS.forEach(function (key) {
      var entry = segment(key);
      if (!entry || !entry.n) return;
      entry.bins.forEach(function (count) {
        maxPct = Math.max(maxPct, (count / entry.n) * 100);
      });
    });
    maxPct = Math.ceil((maxPct + 2) / 5) * 5 || 10;

    CORE_SEGMENTS.forEach(function (key) {
      var chart = charts["sm_" + key];
      if (!chart) return;
      var entry = segment(key);
      var base = C.baseOption();
      var values = entry && entry.n
        ? entry.bins.map(function (count) {
            return (count / entry.n) * 100;
          })
        : [];

      chart.setOption(
        {
          textStyle: base.textStyle,
          grid: { left: 6, right: 10, top: 12, bottom: 4, containLabel: true },
          tooltip: Object.assign({}, base.tooltip, {
            trigger: "axis",
            axisPointer: { type: "shadow" },
            formatter: function (params) {
              if (!params.length) return "";
              var item = params[0];
              var count = entry.bins[item.dataIndex];
              return (
                "<strong>" + item.axisValue + " participants</strong><br>" +
                segLabel(key) + ": <strong>" + C.fmtPct(item.value) + "</strong>" +
                "<br>" + C.fmtInt(count) + " of " + C.fmtInt(entry.n) + " studies"
              );
            }
          }),
          xAxis: Object.assign({}, base.axisCommon, {
            type: "category",
            data: binLabels(),
            splitLine: { show: false },
            axisLabel: Object.assign({}, base.axisCommon.axisLabel, {
              interval: 1,
              fontSize: 9,
              rotate: 40
            })
          }),
          yAxis: Object.assign({}, base.axisCommon, {
            type: "value",
            max: maxPct,
            axisLabel: Object.assign({}, base.axisCommon.axisLabel, {
              fontSize: 9,
              formatter: function (value) {
                return value + "%";
              }
            })
          }),
          series: [
            {
              type: "bar",
              data: values,
              itemStyle: C.barItemStyle(key),
              barMaxWidth: 18
            }
          ]
        },
        true
      );
    });
  }

  // ------------------------------------------------------------- box plot
  function renderBox() {
    var chart = charts.box;
    if (!chart) return;
    var base = C.baseOption();
    var segments = activeSegments().filter(function (key) {
      var entry = segment(key);
      return entry && entry.n > 0;
    });

    var categories = segments.map(segLabel);
    var boxes = segments.map(function (key) {
      var p = segment(key).pct;
      return {
        // ECharts reads [min, Q1, median, Q3, max]; we feed the percentiles
        // the dashboard actually reports, so whiskers are P10 and P90.
        value: [p.p10, p.p25, p.p50, p.p75, p.p90],
        itemStyle: {
          // Estimated boxes are hollow, actual boxes filled -- a second,
          // non-colour cue.  Filled boxes need a darker outline or the median
          // line disappears into the fill.
          color: C.isEstimated(key) ? "transparent" : C.segmentColor(key),
          borderColor: C.isEstimated(key)
            ? C.segmentColor(key)
            : C.shade(C.segmentColor(key), 0.5),
          borderWidth: 2
        }
      };
    });

    chart.setOption(
      {
        textStyle: base.textStyle,
        grid: { left: 8, right: 20, top: 20, bottom: 8, containLabel: true },
        tooltip: Object.assign({}, base.tooltip, {
          trigger: "item",
          formatter: function (params) {
            var key = segments[params.dataIndex];
            var p = segment(key).pct;
            return (
              "<strong>" + segLabel(key) + "</strong><br>" +
              "n = " + C.fmtInt(segment(key).n) + " studies<br>" +
              "P90: " + C.fmtCount(p.p90) + "<br>" +
              "P75: " + C.fmtCount(p.p75) + "<br>" +
              "<strong>Median: " + C.fmtCount(p.p50) + "</strong><br>" +
              "P25: " + C.fmtCount(p.p25) + "<br>" +
              "P10: " + C.fmtCount(p.p10) + "<br>" +
              "<span style='opacity:.7'>mean " + C.fmtCount(p.mean) +
              " · max " + C.fmtCount(p.max) + "</span>"
            );
          }
        }),
        xAxis: Object.assign({}, base.axisCommon, {
          type: "category",
          data: categories,
          splitLine: { show: false },
          axisLabel: Object.assign({}, base.axisCommon.axisLabel, {
            interval: 0,
            rotate: window.innerWidth < 760 ? 40 : 0
          })
        }),
        yAxis: Object.assign({}, base.axisCommon, {
          type: "log",
          name: "Enrollment (participants, log scale)",
          nameLocation: "middle",
          nameGap: 46,
          axisLabel: Object.assign({}, base.axisCommon.axisLabel, {
            formatter: C.logAxisLabel
          })
        }),
        series: [
          {
            type: "boxplot",
            data: boxes,
            boxWidth: [12, 46]
          }
        ]
      },
      true
    );
  }

  // ------------------------------------------------------------- ECDF
  function renderECDF() {
    var chart = charts.ecdf;
    if (!chart) return;
    var base = C.baseOption();
    var grid = data.metadata.ecdf_grid;
    var segments = activeSegments().filter(function (key) {
      var entry = segment(key);
      return entry && entry.n > 0;
    });

    // Map the series label back to its readable phrase for the tooltip.
    var phraseByLabel = {};
    segments.forEach(function (key) {
      phraseByLabel[segLabel(key)] = segPhrase(key);
    });

    var series = segments.map(function (key) {
      var entry = segment(key);
      return {
        name: segLabel(key),
        type: "line",
        showSymbol: false,
        smooth: false,
        lineStyle: C.lineStyle(key),
        itemStyle: { color: C.segmentColor(key) },
        emphasis: { focus: "series" },
        data: grid.map(function (x, index) {
          return [x, entry.ecdf[index] * 100];
        })
      };
    });

    // A handful of records carry implausible enrollments (placeholders such as
    // 99,999,999), which would stretch a log axis across nine decades and
    // squash the range where studies actually sit.  Clip the axis at the first
    // grid point where every curve has essentially reached 100%; the underlying
    // data still covers the full range.
    var xMax = grid[grid.length - 1];
    for (var i = 0; i < grid.length; i++) {
      var converged = segments.every(function (key) {
        return segment(key).ecdf[i] >= 0.999;
      });
      if (converged) {
        xMax = grid[i];
        break;
      }
    }
    // Round up to a power of ten so the axis ends on a clean tick (1k, 10k, 1M)
    // rather than an arbitrary grid value.
    xMax = Math.pow(10, Math.ceil(Math.log10(xMax)));

    chart.setOption(
      {
        textStyle: base.textStyle,
        legend: base.legend,
        grid: { left: 8, right: 22, top: 44, bottom: 8, containLabel: true },
        tooltip: Object.assign({}, base.tooltip, {
          trigger: "axis",
          axisPointer: { type: "line" },
          formatter: function (params) {
            if (!params.length) return "";
            var size = params[0].value[0];
            var lines = [
              "<strong>Studies enrolling at most " +
                C.fmtInt(size) + " participants</strong>"
            ];
            params.forEach(function (item) {
              lines.push(
                item.marker + " <strong>" + C.fmtPct(item.value[1]) + "</strong> of " +
                  (phraseByLabel[item.seriesName] || item.seriesName)
              );
            });
            return lines.join("<br>");
          }
        }),
        xAxis: Object.assign({}, base.axisCommon, {
          type: "log",
          name: "Enrollment (participants, log scale)",
          nameLocation: "middle",
          nameGap: 34,
          min: 1,
          max: xMax,
          axisLabel: Object.assign({}, base.axisCommon.axisLabel, {
            formatter: C.logAxisLabel
          })
        }),
        yAxis: Object.assign({}, base.axisCommon, {
          type: "value",
          name: "Cumulative % of studies",
          nameLocation: "end",
          nameTextStyle: { color: C.cssVar("--text-muted"), fontSize: 11, align: "left" },
          nameGap: 14,
          min: 0,
          max: 100,
          axisLabel: Object.assign({}, base.axisCommon.axisLabel, {
            formatter: function (value) {
              return value + "%";
            }
          })
        }),
        series: series
      },
      true
    );
  }

  // ------------------------------------------------------------- crosstab
  function renderCrosstab() {
    var table = document.getElementById("crosstab");
    var dims = data.dimensions;
    var missing = data.missing_type[cellKey()] || {};

    var rows = dims.status_groups.map(function (group) {
      var estimated = segTotal(group + "_estimated");
      var actual = segTotal(group + "_actual");
      var noType = missing[group] || 0;
      return {
        group: group,
        label: dims.status_group_labels[group],
        estimated: estimated,
        actual: actual,
        noType: noType,
        total: estimated + actual + noType
      };
    });

    var totals = rows.reduce(
      function (acc, row) {
        acc.estimated += row.estimated;
        acc.actual += row.actual;
        acc.noType += row.noType;
        acc.total += row.total;
        return acc;
      },
      { estimated: 0, actual: 0, noType: 0, total: 0 }
    );

    var html =
      "<thead><tr><th>Status group</th><th>Estimated</th><th>Actual</th>" +
      "<th>No type reported</th><th>Total</th></tr></thead><tbody>";

    rows.forEach(function (row) {
      html +=
        "<tr><td>" + row.label + "</td>" +
        "<td>" + C.fmtInt(row.estimated) + "</td>" +
        "<td>" + C.fmtInt(row.actual) + "</td>" +
        "<td>" + C.fmtInt(row.noType) + "</td>" +
        "<td>" + C.fmtInt(row.total) + "</td></tr>";
    });

    html +=
      "</tbody><tfoot><tr><td>All</td>" +
      "<td>" + C.fmtInt(totals.estimated) + "</td>" +
      "<td>" + C.fmtInt(totals.actual) + "</td>" +
      "<td>" + C.fmtInt(totals.noType) + "</td>" +
      "<td>" + C.fmtInt(totals.total) + "</td></tr></tfoot>";

    table.innerHTML = html;

    // Data-quality call-out: a closed study should normally report an actual figure.
    var closed = rows.filter(function (row) {
      return row.group === "closed";
    })[0];
    var note = document.getElementById("crosstab-note");
    if (closed && closed.total) {
      var share = (closed.estimated / (closed.estimated + closed.actual)) * 100;
      note.textContent =
        C.fmtInt(closed.estimated) + " closed studies (" + C.fmtPct(share) +
        " of closed studies with a reported type) still list enrollment as " +
        "Estimated rather than Actual. A completed or terminated study would " +
        "normally report the number it actually enrolled, so this is a sign of " +
        "records that were never updated after the study ended. The " +
        "“No type reported” column is excluded from every chart above.";
    } else {
      note.textContent = "";
    }
  }

  // ------------------------------------------------------------- methodology
  function renderMethodology() {
    var meta = data.metadata;
    var rules = meta.classification_rules;
    var container = document.getElementById("methodology");

    function statusList(group) {
      return rules.status_groups[group]
        .map(function (status) {
          return status.replace(/_/g, " ").toLowerCase();
        })
        .join(", ");
    }

    var exclusionRows = Object.keys(meta.exclusions)
      .sort(function (a, b) {
        return meta.exclusions[b] - meta.exclusions[a];
      })
      .map(function (reason) {
        return (
          "<tr><td>" + (meta.exclusion_labels[reason] || reason) + "</td><td>" +
          C.fmtInt(meta.exclusions[reason]) + "</td></tr>"
        );
      })
      .join("");

    container.innerHTML =
      "<h3>Where the data comes from</h3>" +
      "<p>Every figure is derived from the " +
      '<a href="https://clinicaltrials.gov/" rel="noopener">ClinicalTrials.gov</a> ' +
      "registry, read through its public API v2 on <strong>" +
      window.ClinBoltNav.formatDate(meta.generated_at) + "</strong>. " +
      "The registry reported <strong>" + C.fmtInt(meta.source.registry_total_count) +
      "</strong> studies at that time; this build read <strong>" +
      C.fmtInt(meta.total_fetched) + "</strong> records and included <strong>" +
      C.fmtInt(meta.records_included) + "</strong> of them." +
      "</p>" +

      "<h3>How studies are grouped</h3>" +
      "<ul>" +
      "<li><strong>Active</strong> &mdash; " + statusList("active") + ".</li>" +
      "<li><strong>Closed</strong> &mdash; " + statusList("closed") + ".</li>" +
      "<li><strong>Unknown</strong> &mdash; the sponsor has not verified the status " +
      "recently. Shown only when you switch it on, because these records are stale " +
      "by definition.</li>" +
      "</ul>" +
      "<p>Phase combinations are reported by the API as arrays, so " +
      "<code>[PHASE1, PHASE2]</code> is shown here as Phase 1/2. Studies with no " +
      "phase recorded &mdash; nearly all of them observational &mdash; are grouped as " +
      "<em>Not Specified</em>.</p>" +

      "<h3>What is left out, and why</h3>" +
      '<div class="table-scroll"><table class="data">' +
      "<thead><tr><th>Reason</th><th>Studies</th></tr></thead><tbody>" +
      exclusionRows +
      "</tbody></table></div>" +
      "<p class='small muted' style='margin-top:.7rem'>" +
      "A further <strong>" + C.fmtInt(meta.zero_enrollment_total) + "</strong> studies " +
      "report an enrollment of exactly zero &mdash; most of them withdrawn before " +
      "enrolling anyone. They are counted in the totals and the crosstab, but cannot " +
      "be drawn on a logarithmic axis, so they are left out of the distribution charts." +
      "</p>" +

      "<h3>Caveats</h3>" +
      "<ul>" +
      "<li>Registry data is <strong>sponsor-reported</strong> and is not independently " +
      "verified. Errors, omissions and stale records are all present in the source.</li>" +
      "<li>An <em>Estimated</em> figure is a target, not an outcome. Comparing estimated " +
      "and actual distributions compares two different things about different studies, " +
      "not before-and-after values for the same study.</li>" +
      "<li>Studies whose records were never updated after completion keep whatever " +
      "figures they last had, which inflates the estimated share among closed studies.</li>" +
      "<li>Percentiles are computed from the exact reported values; the binning above " +
      "is only for display.</li>" +
      "<li>A few records report clearly implausible enrollments &mdash; placeholder " +
      "values such as 99,999,999. They are left in rather than silently removed, " +
      "so they show up in the maximum reported per segment. They barely move the " +
      "percentiles, but the cumulative chart's axis is clipped at the point where " +
      "every curve has reached 100%, so those few records do not stretch it across " +
      "nine orders of magnitude.</li>" +
      "</ul>";
  }

  // ------------------------------------------------------------- orchestration
  function renderCharts() {
    renderHistogram();
    renderSmallMultiples();
    renderBox();
    renderECDF();
  }

  function renderAll() {
    renderFilterSummary();
    renderKPIs();
    renderCharts();
    renderCrosstab();
  }

  function initCharts() {
    charts.histogram = C.init(document.getElementById("chart-histogram"));
    charts.box = C.init(document.getElementById("chart-box"));
    charts.ecdf = C.init(document.getElementById("chart-ecdf"));
    CORE_SEGMENTS.forEach(function (key) {
      charts["sm_" + key] = C.init(document.getElementById("sm-" + key));
    });
    C.onThemeChange(renderCharts);
    // Bar label rotation depends on viewport width, so redraw on resize too.
    window.addEventListener("resize", C.debounce(renderCharts, 250));
  }

  function showError(message) {
    document.getElementById("loading").hidden = true;
    var box = document.getElementById("error");
    box.hidden = false;
    document.getElementById("error-detail").textContent = message;
  }

  function start() {
    fetch(DATA_URL, { cache: "no-cache" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error(
            "The data file returned HTTP " + response.status + "."
          );
        }
        return response.json();
      })
      .then(function (payload) {
        data = payload;
        document.getElementById("loading").hidden = true;
        document.getElementById("dashboard").hidden = false;

        buildFilters();
        initCharts();
        renderAll();
        renderMethodology();

        if (window.ClinBoltNav) {
          window.ClinBoltNav.setLastUpdated(data.metadata.generated_at);
        }
      })
      .catch(function (error) {
        console.error("ClinBolt: failed to load study-size data.", error);
        showError(String(error.message || error));
      });
  }

  // The footer is built by nav.js; wait for it so the refresh date lands in it.
  if (window.ClinBoltNav && window.ClinBoltNav.ready) {
    window.ClinBoltNav.ready.then(start, start);
  } else {
    document.addEventListener("DOMContentLoaded", start);
  }
})();

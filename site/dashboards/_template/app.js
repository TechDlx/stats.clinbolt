/* TEMPLATE dashboard logic.
 *
 * Copy this alongside index.html and style.css, then:
 *   1. point DATA_URL at your generated file
 *   2. set USE_SAMPLE_DATA to false
 *   3. replace renderChart() with your real charts
 *
 * As shipped it renders from a small inline sample so the copied folder works
 * immediately, before any pipeline exists.
 */
(function () {
  "use strict";

  var DATA_URL = "/dashboards/_template/data/template.json";
  var USE_SAMPLE_DATA = true;

  var C = window.ClinBoltCharts;
  var data = null;
  var charts = {};

  // Shaped like a real pipeline output, so swapping in the real file is a
  // one-line change rather than a rewrite.
  var SAMPLE_DATA = {
    schema_version: 1,
    metadata: {
      generated_at: new Date().toISOString(),
      source: { name: "Sample data (not real)" },
      records_included: 1234,
      categories: ["Group A", "Group B", "Group C", "Group D"]
    },
    series: {
      estimated: [42, 88, 61, 25],
      actual: [35, 74, 58, 19]
    }
  };

  function renderKPIs() {
    var container = document.getElementById("kpis");
    container.innerHTML = "";

    var cards = [
      { label: "Records included", value: C.fmtInt(data.metadata.records_included) },
      { label: "Groups", value: C.fmtInt(data.metadata.categories.length) },
      { label: "Data source", value: data.metadata.source.name }
    ];

    cards.forEach(function (card) {
      var node = document.createElement("div");
      node.className = "kpi";
      var label = document.createElement("span");
      label.className = "kpi-label";
      label.textContent = card.label;
      var value = document.createElement("span");
      value.className = "kpi-value";
      value.textContent = card.value;
      node.appendChild(label);
      node.appendChild(value);
      container.appendChild(node);
    });
  }

  function renderChart() {
    var chart = charts.sample;
    if (!chart) return;
    var base = C.baseOption();

    chart.setOption(
      {
        textStyle: base.textStyle,
        legend: base.legend,
        grid: Object.assign({}, base.grid, { top: 44 }),
        tooltip: Object.assign({}, base.tooltip, {
          trigger: "axis",
          axisPointer: { type: "shadow" }
        }),
        xAxis: Object.assign({}, base.axisCommon, {
          type: "category",
          data: data.metadata.categories,
          splitLine: { show: false }
        }),
        yAxis: Object.assign({}, base.axisCommon, { type: "value" }),
        series: [
          {
            // Estimated series get a hatched fill so the two are distinguishable
            // without relying on colour.
            name: "Estimated",
            type: "bar",
            data: data.series.estimated,
            itemStyle: C.barItemStyle("active_estimated"),
            barMaxWidth: 34
          },
          {
            name: "Actual",
            type: "bar",
            data: data.series.actual,
            itemStyle: C.barItemStyle("active_actual"),
            barMaxWidth: 34
          }
        ]
      },
      true
    );
  }

  function renderAll() {
    renderKPIs();
    renderChart();
  }

  function showError(message) {
    document.getElementById("loading").hidden = true;
    var box = document.getElementById("error");
    box.hidden = false;
    document.getElementById("error-detail").textContent = message;
  }

  function onReady(payload) {
    data = payload;
    document.getElementById("loading").hidden = true;
    document.getElementById("dashboard").hidden = false;

    charts.sample = C.init(document.getElementById("chart-sample"));
    C.onThemeChange(renderChart);

    var download = document.getElementById("download-json");
    if (download) download.setAttribute("href", DATA_URL);

    renderAll();

    if (window.ClinBoltNav) {
      window.ClinBoltNav.setLastUpdated(data.metadata.generated_at);
    }
  }

  function start() {
    if (USE_SAMPLE_DATA) {
      onReady(SAMPLE_DATA);
      return;
    }
    fetch(DATA_URL, { cache: "no-cache" })
      .then(function (response) {
        if (!response.ok) throw new Error("The data file returned HTTP " + response.status + ".");
        return response.json();
      })
      .then(onReady)
      .catch(function (error) {
        console.error("Template dashboard: failed to load data.", error);
        showError(String(error.message || error));
      });
  }

  if (window.ClinBoltNav && window.ClinBoltNav.ready) {
    window.ClinBoltNav.ready.then(start, start);
  } else {
    document.addEventListener("DOMContentLoaded", start);
  }
})();

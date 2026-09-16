/* Shared ECharts theme, segment styling and formatting helpers.
 *
 * Colours are read from the CSS custom properties in common.css so charts and
 * page furniture stay in step in both light and dark mode.
 *
 * Estimated vs Actual is never signalled by colour alone: estimated series get
 * a hatched fill (bars) or a dashed stroke (lines) plus explicit legend wording.
 */
(function () {
  "use strict";

  var SEGMENT_KEYS = [
    "active_estimated",
    "active_actual",
    "closed_estimated",
    "closed_actual",
    "unknown_estimated",
    "unknown_actual"
  ];

  var SEGMENT_META = {
    active_estimated: { cssVar: "--seg-active-estimated", status: "active", kind: "estimated" },
    active_actual: { cssVar: "--seg-active-actual", status: "active", kind: "actual" },
    closed_estimated: { cssVar: "--seg-closed-estimated", status: "closed", kind: "estimated" },
    closed_actual: { cssVar: "--seg-closed-actual", status: "closed", kind: "actual" },
    unknown_estimated: { cssVar: "--seg-unknown-estimated", status: "unknown", kind: "estimated" },
    unknown_actual: { cssVar: "--seg-unknown-actual", status: "unknown", kind: "actual" }
  };

  var charts = [];
  var themeListeners = [];

  // ----------------------------------------------------------- formatting
  function fmtInt(value) {
    if (value === null || value === undefined || isNaN(value)) return "—";
    return Math.round(value).toLocaleString("en-US");
  }

  /** Enrollment figures: whole numbers read better, but keep one decimal
   *  for interpolated percentiles below 10. */
  function fmtCount(value) {
    if (value === null || value === undefined || isNaN(value)) return "—";
    if (value < 10 && value % 1 !== 0) return value.toFixed(1);
    return Math.round(value).toLocaleString("en-US");
  }

  function fmtPct(value, digits) {
    if (value === null || value === undefined || isNaN(value)) return "—";
    return value.toFixed(digits === undefined ? 1 : digits) + "%";
  }

  // ----------------------------------------------------------- theme
  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function isDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function palette() {
    var out = {};
    SEGMENT_KEYS.forEach(function (key) {
      out[key] = cssVar(SEGMENT_META[key].cssVar);
    });
    return out;
  }

  function segmentColor(key) {
    return cssVar(SEGMENT_META[key].cssVar);
  }

  function isEstimated(key) {
    return SEGMENT_META[key] && SEGMENT_META[key].kind === "estimated";
  }

  /** Hatched fill for estimated bars, so the two enrollment types stay
   *  distinguishable in greyscale or for colour-blind readers. */
  function barItemStyle(key) {
    var style = { color: segmentColor(key), borderRadius: [2, 2, 0, 0] };
    if (isEstimated(key)) {
      style.decal = {
        symbol: "rect",
        symbolSize: 1,
        color: "rgba(255, 255, 255, 0.55)",
        dashArrayX: [1, 0],
        dashArrayY: [3, 4],
        rotation: -Math.PI / 4
      };
    }
    return style;
  }

  /** Scale a hex colour towards black (factor < 1) or white (factor > 1). */
  function shade(hex, factor) {
    var match = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(String(hex).trim());
    if (!match) return hex;
    var channels = [1, 2, 3].map(function (index) {
      var value = parseInt(match[index], 16) * factor;
      return Math.max(0, Math.min(255, Math.round(value)));
    });
    return "rgb(" + channels.join(",") + ")";
  }

  function lineStyle(key) {
    return {
      color: segmentColor(key),
      width: 2.4,
      type: isEstimated(key) ? "dashed" : "solid"
    };
  }

  /** Axis, grid and tooltip defaults shared by every chart on the site. */
  function baseOption() {
    var text = cssVar("--text");
    var muted = cssVar("--text-muted");
    var border = cssVar("--border");
    var surface = cssVar("--surface");

    return {
      textStyle: {
        fontFamily:
          '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
        color: text
      },
      grid: { left: 8, right: 16, top: 34, bottom: 8, containLabel: true },
      legend: {
        type: "scroll",
        top: 0,
        itemGap: 14,
        textStyle: { color: muted, fontSize: 12 },
        inactiveColor: cssVar("--text-faint")
      },
      tooltip: {
        confine: true,
        backgroundColor: surface,
        borderColor: border,
        borderWidth: 1,
        padding: [8, 11],
        textStyle: { color: text, fontSize: 12 },
        extraCssText: "box-shadow: 0 4px 14px rgba(0,0,0,0.16); max-width: 320px;"
      },
      axisCommon: {
        axisLine: { lineStyle: { color: border } },
        axisTick: { show: false },
        axisLabel: { color: muted, fontSize: 11, hideOverlap: true },
        splitLine: { lineStyle: { color: border, type: "dashed" } },
        nameTextStyle: { color: muted, fontSize: 11, padding: [6, 0, 0, 0], align: "center" }
      }
    };
  }

  /** Log-axis tick labels: 1, 10, 100, 1k, 10k ... */
  function logAxisLabel(value) {
    if (value >= 1000000) return value / 1000000 + "M";
    if (value >= 1000) return value / 1000 + "k";
    return String(value);
  }

  // ----------------------------------------------------------- lifecycle
  function init(node) {
    if (!node) return null;
    var instance = window.echarts.init(node, null, { renderer: "canvas" });
    charts.push(instance);
    return instance;
  }

  function disposeAll() {
    charts.forEach(function (instance) {
      instance.dispose();
    });
    charts = [];
  }

  function resizeAll() {
    charts.forEach(function (instance) {
      instance.resize();
    });
  }

  function onThemeChange(callback) {
    themeListeners.push(callback);
  }

  window.addEventListener("resize", debounce(resizeAll, 120));

  if (window.matchMedia) {
    var query = window.matchMedia("(prefers-color-scheme: dark)");
    var handler = function () {
      themeListeners.forEach(function (callback) {
        callback();
      });
    };
    if (query.addEventListener) {
      query.addEventListener("change", handler);
    } else if (query.addListener) {
      query.addListener(handler);
    }
  }

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      window.clearTimeout(timer);
      timer = window.setTimeout(fn, wait);
    };
  }

  window.ClinBoltCharts = {
    SEGMENT_KEYS: SEGMENT_KEYS,
    SEGMENT_META: SEGMENT_META,
    fmtInt: fmtInt,
    fmtCount: fmtCount,
    fmtPct: fmtPct,
    cssVar: cssVar,
    isDark: isDark,
    palette: palette,
    segmentColor: segmentColor,
    isEstimated: isEstimated,
    barItemStyle: barItemStyle,
    lineStyle: lineStyle,
    shade: shade,
    baseOption: baseOption,
    logAxisLabel: logAxisLabel,
    init: init,
    disposeAll: disposeAll,
    resizeAll: resizeAll,
    onThemeChange: onThemeChange,
    debounce: debounce
  };
})();

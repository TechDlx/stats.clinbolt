/* Shared site chrome for every ClinBolt Stats page.
 *
 * Each page ships an empty <header class="site-header"> and
 * <footer class="site-footer">; this script fills both from /dashboards.json so
 * adding a dashboard to the registry adds it to every page's nav.
 *
 * A page identifies itself with <body data-dashboard-id="study-size">.
 */
(function () {
  "use strict";

  var REGISTRY_URL = "/dashboards.json";
  var registryPromise = null;
  // A page may report its data date before or after the footer exists, so the
  // value is held here and applied whichever way the race goes.
  var lastUpdatedISO = null;

  function loadRegistry() {
    if (!registryPromise) {
      registryPromise = fetch(REGISTRY_URL, { cache: "no-cache" }).then(function (response) {
        if (!response.ok) {
          throw new Error("registry request failed: HTTP " + response.status);
        }
        return response.json();
      });
    }
    return registryPromise;
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        if (key === "text") {
          node.textContent = attrs[key];
        } else if (attrs[key] !== null && attrs[key] !== undefined) {
          node.setAttribute(key, attrs[key]);
        }
      });
    }
    (children || []).forEach(function (child) {
      node.appendChild(child);
    });
    return node;
  }

  function buildHeader(registry, activeId) {
    var header = document.querySelector(".site-header");
    if (!header) return;

    var brand = el("a", { class: "brand", href: "/" });
    var name = registry.site.name.split(" ");
    brand.appendChild(document.createTextNode(name[0] + " "));
    brand.appendChild(el("span", { text: name.slice(1).join(" ") || "Stats" }));

    var nav = el("nav", { class: "site-nav", "aria-label": "Dashboards" });
    nav.appendChild(
      el("a", {
        href: "/",
        text: "All dashboards",
        "aria-current": activeId ? null : "page"
      })
    );

    registry.dashboards.forEach(function (dashboard) {
      if (dashboard.status !== "live") return;
      nav.appendChild(
        el("a", {
          href: dashboard.path,
          text: dashboard.nav_title || dashboard.title,
          "aria-current": dashboard.id === activeId ? "page" : null
        })
      );
    });

    var wrap = el("div", { class: "wrap" }, [brand, nav]);
    header.innerHTML = "";
    header.appendChild(wrap);
  }

  function buildFooter(registry) {
    var footer = document.querySelector(".site-footer");
    if (!footer) return;

    var source = el("p", {});
    source.appendChild(document.createTextNode("Data source: "));
    source.appendChild(
      el("a", {
        href: "https://clinicaltrials.gov/",
        rel: "noopener",
        text: "ClinicalTrials.gov"
      })
    );
    source.appendChild(
      document.createTextNode(
        " (U.S. National Library of Medicine), retrieved through the public API v2. " +
          "Registry data is sponsor-reported and not independently verified."
      )
    );

    var updated = el("p", {
      class: "small",
      id: "footer-updated",
      text: "Data last refreshed: " + formatDate(lastUpdatedISO)
    });

    var disclaimer = el("p", {
      class: "disclaimer small",
      text:
        "For research and informational purposes only. This site is not medical advice " +
        "and must not be used to guide diagnosis or treatment."
    });

    var wrap = el("div", { class: "wrap" }, [source, updated, disclaimer]);
    footer.innerHTML = "";
    footer.appendChild(wrap);
  }

  /** Format an ISO timestamp as a readable UTC date. */
  function formatDate(iso) {
    if (!iso) return "—";
    var date = new Date(iso);
    if (isNaN(date.getTime())) return iso;
    return date.toLocaleDateString(undefined, {
      year: "numeric",
      month: "long",
      day: "numeric",
      timeZone: "UTC"
    });
  }

  function setLastUpdated(iso) {
    lastUpdatedISO = iso;
    var node = document.getElementById("footer-updated");
    if (node) {
      node.textContent = "Data last refreshed: " + formatDate(iso);
    }
    // If the footer has not been built yet, buildFooter picks up the stored value.
  }

  function init() {
    var activeId = document.body.getAttribute("data-dashboard-id") || null;
    return loadRegistry()
      .then(function (registry) {
        buildHeader(registry, activeId);
        buildFooter(registry);
        document.dispatchEvent(
          new CustomEvent("clinbolt:registry", { detail: registry })
        );
        return registry;
      })
      .catch(function (error) {
        // The chrome is not worth breaking a page over; log and carry on.
        console.error("ClinBolt: could not load the dashboard registry.", error);
      });
  }

  window.ClinBoltNav = {
    loadRegistry: loadRegistry,
    setLastUpdated: setLastUpdated,
    formatDate: formatDate,
    el: el,
    ready: null
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      window.ClinBoltNav.ready = init();
    });
  } else {
    window.ClinBoltNav.ready = init();
  }
})();

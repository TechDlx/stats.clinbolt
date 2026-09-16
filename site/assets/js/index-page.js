/* Landing page: renders the dashboard card grid from the registry.
 * Kept in its own file so the Content-Security-Policy can forbid
 * inline scripts entirely (script-src 'self').
 */
(function () {
  "use strict";

  var container = document.getElementById("cards");

  function el(tag, attrs, children) {
    return window.ClinBoltNav.el(tag, attrs, children);
  }

  function buildCard(dashboard) {
    var isLive = dashboard.status === "live";

    var tag = el("span", {
      class: "tag" + (isLive ? " tag-live" : ""),
      text: isLive ? "Live" : "Coming soon"
    });

    var meta = el("div", { class: "card-meta" }, [tag]);
    meta.appendChild(el("span", { text: dashboard.category || "" }));

    if (isLive) {
      meta.appendChild(
        el("span", {
          class: "card-updated",
          "data-id": dashboard.id,
          text: ""
        })
      );
    }

    var children = [
      el("h3", { text: dashboard.title }),
      el("p", { text: dashboard.description }),
      meta
    ];

    if (isLive) {
      return el("a", { class: "card", href: dashboard.path }, children);
    }
    // Coming-soon cards are muted and deliberately not links.
    return el("div", { class: "card is-coming-soon", "aria-disabled": "true" }, children);
  }

  function showFreshness(dashboard) {
    if (!dashboard.meta_file) return;
    fetch(dashboard.meta_file, { cache: "no-cache" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (meta) {
        var node = container.querySelector('.card-updated[data-id="' + dashboard.id + '"]');
        if (node && meta.generated_at) {
          node.textContent = "Updated " + window.ClinBoltNav.formatDate(meta.generated_at);
        }
        window.ClinBoltNav.setLastUpdated(meta.generated_at);
      })
      .catch(function () {
        // A missing sidecar just means no freshness badge; the card still works.
      });
  }

  function render(registry) {
    container.innerHTML = "";
    registry.dashboards.forEach(function (dashboard) {
      container.appendChild(buildCard(dashboard));
    });
    registry.dashboards.forEach(function (dashboard) {
      if (dashboard.status === "live") showFreshness(dashboard);
    });
  }

  document.addEventListener("clinbolt:registry", function (event) {
    render(event.detail);
  });

  // If the registry failed to load, nav.js logs it; show the user something too.
  window.setTimeout(function () {
    if (container.querySelector(".spinner")) {
      container.innerHTML =
        '<div class="state state-error"><strong>Could not load the dashboard list.</strong>' +
        "Check that <code>/dashboards.json</code> is being served, then reload.</div>";
    }
  }, 8000);
})();

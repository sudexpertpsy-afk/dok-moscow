(function () {
  const palette = document.getElementById("gs-palette");
  if (!palette) return;

  const input = document.getElementById("gs-input");
  const results = document.getElementById("gs-results");
  const emptyBox = document.getElementById("gs-empty");
  const sitemapBody = document.getElementById("gs-sitemap-body");
  let items = [];
  let active = -1;
  let timer = null;
  let lastSitemap = [];

  function open() {
    palette.hidden = false;
    document.body.classList.add("gs-open");
    input.value = "";
    results.innerHTML = "";
    emptyBox.hidden = true;
    items = [];
    active = -1;
    input.focus();
    fetchResults("");
  }

  function close() {
    palette.hidden = true;
    document.body.classList.remove("gs-open");
  }

  function setActive(idx) {
    const nodes = results.querySelectorAll(".gs-item");
    nodes.forEach((n) => n.classList.remove("is-active"));
    if (!nodes.length) {
      active = -1;
      return;
    }
    active = (idx + nodes.length) % nodes.length;
    nodes[active].classList.add("is-active");
    nodes[active].scrollIntoView({ block: "nearest" });
  }

  function go(url) {
    if (!url) return;
    close();
    window.location.href = url;
  }

  function render(data) {
    lastSitemap = data.sitemap || [];
    items = [];
    results.innerHTML = "";
    emptyBox.hidden = true;

    if (data.empty && (data.query || "").length >= 2) {
      emptyBox.hidden = false;
      let html = "";
      for (const block of lastSitemap) {
        html += `<div class="gs-map-group"><strong>${escapeHtml(block.group)}</strong><ul>`;
        for (const it of block.items || []) {
          const badge = it.badge
            ? `<span class="gs-badge">${escapeHtml(it.badge)}</span>`
            : "";
          html += `<li><a href="${escapeAttr(it.url)}">${escapeHtml(it.title)}</a> ${badge}</li>`;
        }
        html += "</ul></div>";
      }
      sitemapBody.innerHTML = html || "<p class='muted'>Нет доступных разделов</p>";
      return;
    }

    for (const g of data.groups || []) {
      const head = document.createElement("div");
      head.className = "gs-group-title";
      head.textContent = g.title;
      results.appendChild(head);
      for (const it of g.items || []) {
        const el = document.createElement("a");
        el.className = "gs-item";
        el.href = it.url;
        el.setAttribute("role", "option");
        el.innerHTML =
          `<span class="gs-item-title">${escapeHtml(it.title)}</span>` +
          (it.subtitle
            ? `<span class="gs-item-sub">${escapeHtml(it.subtitle)}</span>`
            : "") +
          (it.badge
            ? `<span class="gs-badge">${escapeHtml(it.badge)}</span>`
            : "");
        el.addEventListener("mouseenter", () => {
          const idx = items.indexOf(el);
          setActive(idx);
        });
        el.addEventListener("click", (e) => {
          e.preventDefault();
          go(it.url);
        });
        results.appendChild(el);
        items.push(el);
      }
    }
    if (items.length) setActive(0);
  }

  function fetchResults(q) {
    fetch("/api/global-search?q=" + encodeURIComponent(q), {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then((r) => {
        if (r.status === 401) {
          window.location.href = "/login";
          return null;
        }
        return r.json();
      })
      .then((data) => {
        if (data) render(data);
      })
      .catch(() => {});
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  document.querySelectorAll("[data-gs-open]").forEach((btn) => {
    btn.addEventListener("click", open);
  });
  palette.querySelectorAll("[data-gs-close]").forEach((el) => {
    el.addEventListener("click", close);
  });

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => fetchResults(input.value.trim()), 160);
  });

  document.addEventListener("keydown", (e) => {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      if (palette.hidden) open();
      else close();
      return;
    }
    if (palette.hidden) return;
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive(active + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(active - 1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const nodes = results.querySelectorAll(".gs-item");
      if (active >= 0 && nodes[active]) go(nodes[active].getAttribute("href"));
    }
  });

  // autofocus после быстрых действий
  if (new URLSearchParams(location.search).get("autofocus") === "1") {
    const main = document.getElementById("main");
    const focusable = main
      ? main.querySelector(
          "input:not([type=hidden]), select, textarea, a.btn, button"
        )
      : null;
    if (focusable) {
      setTimeout(() => focusable.focus(), 50);
    }
  }
})();

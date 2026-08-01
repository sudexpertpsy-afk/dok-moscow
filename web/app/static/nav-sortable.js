/**
 * Перетаскивание пунктов бокового меню.
 * Сохраняет порядок через POST /api/nav-order.
 */
(function () {
  "use strict";

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  }

  function collectKeys(nav) {
    return Array.prototype.map.call(nav.querySelectorAll("[data-nav-key]"), function (el) {
      return el.getAttribute("data-nav-key");
    });
  }

  function saveOrder(nav) {
    var area = nav.getAttribute("data-nav-area");
    if (!area) return;
    var order = collectKeys(nav);
    fetch("/api/nav-order", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken(),
        Accept: "application/json",
      },
      credentials: "same-origin",
      body: JSON.stringify({ area: area, order: order }),
    }).catch(function () {
      /* сеть недоступна — порядок уже в DOM до перезагрузки */
    });
  }

  function initNav(nav) {
    if (nav.getAttribute("data-nav-sortable-ready") === "1") return;
    nav.setAttribute("data-nav-sortable-ready", "1");

    var dragEl = null;

    nav.addEventListener("dragstart", function (e) {
      var item = e.target.closest("[data-nav-key]");
      if (!item || !nav.contains(item)) return;
      // тянем только за ручку — иначе клик по ссылке ломается
      if (!e.target.closest(".nav-handle")) {
        e.preventDefault();
        return;
      }
      dragEl = item;
      item.classList.add("nav-dragging");
      e.dataTransfer.effectAllowed = "move";
      try {
        e.dataTransfer.setData("text/plain", item.getAttribute("data-nav-key") || "");
      } catch (_) {}
    });

    nav.addEventListener("dragend", function () {
      if (dragEl) dragEl.classList.remove("nav-dragging");
      dragEl = null;
      nav.querySelectorAll(".nav-drag-over").forEach(function (el) {
        el.classList.remove("nav-drag-over");
      });
    });

    nav.addEventListener("dragover", function (e) {
      if (!dragEl) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      var over = e.target.closest("[data-nav-key]");
      if (!over || over === dragEl || !nav.contains(over)) return;
      nav.querySelectorAll(".nav-drag-over").forEach(function (el) {
        if (el !== over) el.classList.remove("nav-drag-over");
      });
      over.classList.add("nav-drag-over");
      var rect = over.getBoundingClientRect();
      var before = e.clientY < rect.top + rect.height / 2;
      if (before) {
        nav.insertBefore(dragEl, over);
      } else {
        nav.insertBefore(dragEl, over.nextSibling);
      }
    });

    nav.addEventListener("drop", function (e) {
      e.preventDefault();
      if (!dragEl) return;
      nav.querySelectorAll(".nav-drag-over").forEach(function (el) {
        el.classList.remove("nav-drag-over");
      });
      dragEl.classList.remove("nav-dragging");
      dragEl = null;
      saveOrder(nav);
    });
  }

  function boot() {
    document.querySelectorAll("nav.nav[data-nav-sortable]").forEach(initNav);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
  document.body.addEventListener("htmx:afterSettle", boot);
})();

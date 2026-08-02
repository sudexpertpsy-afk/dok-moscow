/**
 * Перетаскивание пунктов бокового меню.
 * Сохраняет порядок через POST /api/nav-order.
 *
 * Важно: тянем только за .nav-handle; <button> в браузерах ломает HTML5 DnD,
 * поэтому ручка — span; drag разрешаем флагом с mousedown на ручке.
 */
(function () {
  "use strict";

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  }

  function elFromEventTarget(t) {
    if (!t) return null;
    return t.nodeType === 1 ? t : t.parentElement;
  }

  function closest(el, sel) {
    return el && el.closest ? el.closest(sel) : null;
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
    var allowDrag = false;
    var startOrder = "";
    var moved = false;

    // Без этого Chrome/Safari часто не начинают drag с <button>/текста внутри
    nav.addEventListener("pointerdown", function (e) {
      var t = elFromEventTarget(e.target);
      var handle = closest(t, ".nav-handle");
      allowDrag = !!(handle && nav.contains(handle));
      if (allowDrag) {
        var item = closest(handle, "[data-nav-key]");
        if (item) item.setAttribute("draggable", "true");
      }
    });

    function clearAllow() {
      allowDrag = false;
      nav.querySelectorAll("[data-nav-key][draggable='true']").forEach(function (el) {
        // оставляем draggable=true в разметке — снимать не обязательно
      });
    }
    nav.addEventListener("pointerup", clearAllow);
    nav.addEventListener("pointercancel", clearAllow);

    nav.addEventListener("dragstart", function (e) {
      var t = elFromEventTarget(e.target);
      var item = closest(t, "[data-nav-key]");
      if (!item || !nav.contains(item)) {
        e.preventDefault();
        return;
      }
      var fromHandle = !!closest(t, ".nav-handle") || allowDrag;
      if (!fromHandle) {
        e.preventDefault();
        return;
      }
      dragEl = item;
      moved = false;
      startOrder = collectKeys(nav).join("\0");
      item.classList.add("nav-dragging");
      try {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", item.getAttribute("data-nav-key") || "");
        // прозрачный drag-image иногда ломает drop — не трогаем
      } catch (_) {}
    });

    nav.addEventListener("dragend", function () {
      if (dragEl) dragEl.classList.remove("nav-dragging");
      nav.querySelectorAll(".nav-drag-over").forEach(function (el) {
        el.classList.remove("nav-drag-over");
      });
      var endOrder = collectKeys(nav).join("\0");
      var shouldSave = !!dragEl && (moved || endOrder !== startOrder);
      dragEl = null;
      allowDrag = false;
      startOrder = "";
      moved = false;
      // сохраняем на dragend: drop часто не приходит после insertBefore в dragover
      if (shouldSave) saveOrder(nav);
    });

    nav.addEventListener("dragover", function (e) {
      if (!dragEl) return;
      e.preventDefault();
      try {
        e.dataTransfer.dropEffect = "move";
      } catch (_) {}
      var t = elFromEventTarget(e.target);
      var over = closest(t, "[data-nav-key]");
      if (!over || over === dragEl || !nav.contains(over)) return;
      nav.querySelectorAll(".nav-drag-over").forEach(function (el) {
        if (el !== over) el.classList.remove("nav-drag-over");
      });
      over.classList.add("nav-drag-over");
      var rect = over.getBoundingClientRect();
      var before = e.clientY < rect.top + rect.height / 2;
      var next = before ? over : over.nextSibling;
      if (dragEl.nextSibling !== next && dragEl !== next) {
        nav.insertBefore(dragEl, next);
        moved = true;
      }
    });

    nav.addEventListener("drop", function (e) {
      e.preventDefault();
      // сохранение делает dragend
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
  // document — чтобы слушатель переживал hx-boost замену body
  document.addEventListener("htmx:afterSettle", boot);
  document.addEventListener("htmx:afterSwap", boot);
})();

/**
 * Лендинг dok.moscow: Метрика, демо ИНН, образцы, калькулятор, sticky CTA.
 * Без тяжёлых зависимостей.
 */
(function () {
  "use strict";

  var METRIKA_ID = 0;
  try {
    var el = document.documentElement;
    var raw = el.getAttribute("data-metrika-id") || "";
    METRIKA_ID = parseInt(raw, 10) || 0;
  } catch (e) {
    METRIKA_ID = 0;
  }

  function track(goal, params) {
    if (!goal) return;
    try {
      if (typeof ym === "function" && METRIKA_ID) {
        if (params && typeof params === "object") {
          ym(METRIKA_ID, "reachGoal", goal, params);
        } else {
          ym(METRIKA_ID, "reachGoal", goal);
        }
      }
    } catch (err) {
      /* блокировщик / нет ym */
    }
  }

  window.dokTrack = track;

  /* ---------- engaged ---------- */
  var engagedSent = false;
  function sendEngaged() {
    if (engagedSent) return;
    engagedSent = true;
    track("engaged");
  }

  function onScrollEngaged() {
    var doc = document.documentElement;
    var body = document.body;
    var scrollTop = window.scrollY || doc.scrollTop || 0;
    var height = Math.max(body.scrollHeight, doc.scrollHeight) - window.innerHeight;
    if (height > 0 && scrollTop / height > 0.5) {
      sendEngaged();
      window.removeEventListener("scroll", onScrollEngaged);
    }
  }
  window.addEventListener("scroll", onScrollEngaged, { passive: true });

  document.querySelectorAll('.lp-nav-links a[href^="#"], .lp-nav-links a[href*="/#"]').forEach(function (a) {
    a.addEventListener("click", function () {
      sendEngaged();
    });
  });

  document.querySelectorAll(".lp-faq details, .lp-faq-cms details").forEach(function (d) {
    d.addEventListener("toggle", function () {
      if (!d.open) return;
      sendEngaged();
      var q = (d.querySelector("summary") && d.querySelector("summary").textContent) || "";
      track("faq_open", { question: String(q).trim().slice(0, 120) });
    });
  });

  document.querySelectorAll(".lp-period-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      sendEngaged();
    });
  });

  var loginLink = document.querySelector(".lp-nav-login");
  if (loginLink) {
    loginLink.addEventListener("click", function () {
      track("login_click");
    });
  }

  var applyForm = document.querySelector("#apply form.lp-form");
  if (applyForm) {
    var firstField = applyForm.querySelector("input, select, textarea");
    var started = false;
    function onStart() {
      if (started) return;
      started = true;
      track("apply_form_start");
    }
    if (firstField) {
      firstField.addEventListener("focus", onStart);
    }
    applyForm.addEventListener("submit", function () {
      track("apply_form_submit");
    });
  }

  if (document.querySelector(".lp-alert-ok")) {
    track("apply_form_submit");
  }

  /* ---------- period toggle (was inline) ---------- */
  (function () {
    var root = document.getElementById("pricing");
    if (!root) return;
    var buttons = root.querySelectorAll(".lp-period-btn");
    var prices = root.querySelectorAll(".lp-price[data-month]");
    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var period = btn.getAttribute("data-period");
        buttons.forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
        prices.forEach(function (p) {
          p.textContent = p.getAttribute("data-" + period) || p.textContent;
        });
        root.querySelectorAll("[data-signup-cta]").forEach(function (a) {
          var tariff = a.getAttribute("data-tariff");
          if (!tariff) return;
          var base = a.getAttribute("href").split("?")[0];
          a.setAttribute("href", base + "?tariff=" + encodeURIComponent(tariff) + "&period=" + encodeURIComponent(period));
        });
      });
    });
  })();

  /* track signup CTA clicks */
  document.querySelectorAll("[data-signup-cta]").forEach(function (a) {
    a.addEventListener("click", function () {
      track("signup_cta_click", {
        tariff: a.getAttribute("data-tariff") || "",
        href: a.getAttribute("href") || "",
      });
    });
  });

  /* ---------- sample modal ---------- */
  var modal = document.getElementById("lp-sample-modal");
  var modalFrame = modal && modal.querySelector("iframe");
  var modalTitle = modal && modal.querySelector(".lp-modal-title");
  var modalDownload = modal && modal.querySelector(".lp-modal-download");
  var lastFocus = null;

  function openSample(doc, title, href) {
    if (!modal || !modalFrame) return;
    lastFocus = document.activeElement;
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("lp-modal-open");
    if (modalTitle) modalTitle.textContent = title || "Образец";
    if (modalDownload) {
      modalDownload.href = href;
      modalDownload.setAttribute("download", "");
    }
    modalFrame.src = href;
    track("sample_pdf", { doc: doc });
    var closeBtn = modal.querySelector(".lp-modal-close");
    if (closeBtn) closeBtn.focus();
  }

  function closeSample() {
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("lp-modal-open");
    if (modalFrame) modalFrame.src = "about:blank";
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  document.querySelectorAll("[data-sample-doc]").forEach(function (node) {
    node.addEventListener("click", function (ev) {
      ev.preventDefault();
      openSample(
        node.getAttribute("data-sample-doc"),
        node.getAttribute("data-sample-title") || node.textContent,
        node.getAttribute("href") || node.getAttribute("data-sample-href")
      );
    });
  });

  if (modal) {
    modal.querySelectorAll("[data-modal-close]").forEach(function (btn) {
      btn.addEventListener("click", closeSample);
    });
    modal.addEventListener("click", function (ev) {
      if (ev.target === modal || ev.target.classList.contains("lp-modal-backdrop")) {
        closeSample();
      }
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") closeSample();
    });
    var applyCta = modal.querySelector("[data-sample-apply]");
    if (applyCta) {
      applyCta.addEventListener("click", function () {
        track("sample_to_apply_click");
        closeSample();
      });
    }
  }

  /* ---------- demo INN ---------- */
  var demoRoot = document.getElementById("lp-demo");
  if (demoRoot) {
    var demoInput = demoRoot.querySelector("#lp-demo-inn");
    var demoCard = demoRoot.querySelector(".lp-demo-card");
    var demoStatus = demoRoot.querySelector(".lp-demo-status");
    var demoExamples = [];
    try {
      demoExamples = JSON.parse(demoRoot.getAttribute("data-examples") || "[]");
    } catch (e2) {
      demoExamples = [];
    }

    function renderCard(data, animate) {
      if (!demoCard || !data) return;
      var name = data.name || data.name_short || data.name_full || "—";
      var rows = [
        ["Наименование", name],
        ["ИНН", data.inn || "—"],
        ["ОГРН", data.ogrn || "—"],
        ["Адрес", data.address || "—"],
        ["Руководитель", data.management || data.management_name || "—"],
        ["Статус", data.status_label || data.status || "—"],
      ];
      demoCard.hidden = false;
      demoCard.innerHTML = rows
        .map(function (r) {
          return (
            '<div class="lp-demo-row"><dt>' +
            escapeHtml(r[0]) +
            "</dt><dd" +
            (animate ? ' class="lp-demo-type"' : "") +
            ">" +
            escapeHtml(r[1]) +
            "</dd></div>"
          );
        })
        .join("");
      if (animate && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        demoCard.classList.add("is-typing");
        setTimeout(function () {
          demoCard.classList.remove("is-typing");
        }, 1400);
      }
    }

    function escapeHtml(s) {
      return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function setStatus(msg, isError) {
      if (!demoStatus) return;
      demoStatus.textContent = msg || "";
      demoStatus.classList.toggle("is-error", !!isError);
    }

    function findExample(inn) {
      for (var i = 0; i < demoExamples.length; i++) {
        if (String(demoExamples[i].inn) === String(inn)) return demoExamples[i];
      }
      return null;
    }

    function showFromExample(inn, animate) {
      var ex = findExample(inn);
      if (!ex) return false;
      renderCard(ex, animate);
      setStatus("Демо-данные (без запроса к ЕГРЮЛ).");
      track("demo_inn_success", { source: "fallback", inn: inn });
      return true;
    }

    function submitDemo(inn) {
      inn = String(inn || "").replace(/\D/g, "");
      if (!(inn.length === 10 || inn.length === 12)) {
        setStatus("Введите ИНН из 10 или 12 цифр.", true);
        return;
      }
      if (demoInput) demoInput.value = inn;
      track("demo_inn_submit", { inn: inn });
      setStatus("Загрузка…");

      fetch("/api/demo/egrul?inn=" + encodeURIComponent(inn), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      })
        .then(function (r) {
          if (!r.ok) throw new Error("api");
          return r.json();
        })
        .then(function (data) {
          if (!data || !data.ok || !data.party) throw new Error("empty");
          renderCard(
            {
              name: data.party.name_short || data.party.name_full,
              inn: data.party.inn,
              ogrn: data.party.ogrn,
              address: data.party.address,
              management: data.party.management,
              status_label: data.party.status_label,
            },
            true
          );
          setStatus("Данные из ЕГРЮЛ (как в кабинете).");
          track("demo_inn_success", { source: "api", inn: inn });
        })
        .catch(function () {
          if (!showFromExample(inn, true)) {
            if (demoExamples.length) {
              showFromExample(demoExamples[0].inn, true);
              setStatus("API недоступен — показан пример. Нажмите ИНН ниже.", true);
            } else {
              setStatus("Не удалось загрузить карточку. Попробуйте позже.", true);
            }
          }
        });
    }

    var form = demoRoot.querySelector("form");
    if (form) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        submitDemo(demoInput && demoInput.value);
      });
    }

    demoRoot.querySelectorAll("[data-demo-inn]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var inn = btn.getAttribute("data-demo-inn");
        submitDemo(inn);
      });
    });

    var toApply = demoRoot.querySelector("[data-demo-apply]");
    if (toApply) {
      toApply.addEventListener("click", function () {
        track("demo_to_apply_click");
      });
    }
  }

  /* ---------- calculator ---------- */
  var calc = document.getElementById("lp-calc");
  if (calc) {
    var interacted = false;
    var kits = calc.querySelector("#lp-calc-kits");
    var mins = calc.querySelector("#lp-calc-mins");
    var rate = calc.querySelector("#lp-calc-rate");
    var outHours = calc.querySelector("[data-calc-hours]");
    var outDok = calc.querySelector("[data-calc-dok]");
    var outSave = calc.querySelector("[data-calc-save]");
    var outMoney = calc.querySelector("[data-calc-money]");
    var kitsLabel = calc.querySelector("[data-calc-kits-label]");
    var minsLabel = calc.querySelector("[data-calc-mins-label]");

    function recalc() {
      var k = parseInt(kits && kits.value, 10) || 1;
      var m = parseInt(mins && mins.value, 10) || 60;
      var z = parseInt(rate && rate.value, 10) || 2000;
      if (kitsLabel) kitsLabel.textContent = String(k);
      if (minsLabel) minsLabel.textContent = String(m);
      var hours = (k * m) / 60;
      var dokMin = k * 5;
      var saveH = Math.max(0, hours - dokMin / 60);
      var money = Math.round(saveH * z);
      if (outHours) outHours.textContent = hours.toFixed(1).replace(".0", "");
      if (outDok) outDok.textContent = String(dokMin);
      if (outSave) outSave.textContent = saveH.toFixed(1).replace(".0", "");
      if (outMoney) outMoney.textContent = money.toLocaleString("ru-RU");
    }

    function onInteract() {
      if (!interacted) {
        interacted = true;
        track("calc_interact");
      }
      recalc();
    }

    [kits, mins, rate].forEach(function (input) {
      if (!input) return;
      input.addEventListener("input", onInteract);
      input.addEventListener("change", onInteract);
    });
    recalc();

    var calcApply = calc.querySelector("[data-calc-apply]");
    if (calcApply) {
      calcApply.addEventListener("click", function () {
        track("calc_to_apply_click");
      });
    }
  }

  /* ---------- content cards ---------- */
  document.querySelectorAll("[data-content-card]").forEach(function (a) {
    a.addEventListener("click", function () {
      track("content_card_click", { url: a.getAttribute("href") || "" });
    });
  });

  /* ---------- sticky apply ---------- */
  var sticky = document.getElementById("lp-sticky-apply");
  var applySection = document.getElementById("apply");
  if (sticky && applySection) {
    function updateSticky() {
      var heroH = window.innerHeight * 1.5;
      var scrolled = window.scrollY || document.documentElement.scrollTop || 0;
      var applyRect = applySection.getBoundingClientRect();
      var nearApply = applyRect.top < window.innerHeight && applyRect.bottom > 0;
      var show = scrolled > heroH && !nearApply;
      sticky.hidden = !show;
      sticky.setAttribute("aria-hidden", show ? "false" : "true");
    }
    window.addEventListener("scroll", updateSticky, { passive: true });
    window.addEventListener("resize", updateSticky);
    updateSticky();
    sticky.addEventListener("click", function () {
      track("sticky_apply_click");
    });
  }
})();

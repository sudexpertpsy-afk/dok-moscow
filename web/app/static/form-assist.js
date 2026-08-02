/* T8: history-suggest, linked-поля, адаптер DaData на формах документов. */
(function () {
  function qs(sel, root) {
    return (root || document).querySelector(sel);
  }
  function qsa(sel, root) {
    return Array.from((root || document).querySelectorAll(sel));
  }

  function fieldByName(name, form) {
    return form.querySelector('[name="' + CSS.escape(name) + '"]');
  }

  function currentValues(form) {
    var out = {};
    qsa(".form-field", form).forEach(function (el) {
      if (el.name) out[el.name] = el.value || "";
    });
    return out;
  }

  function applyLinked(form, srcEl) {
    var src = srcEl.getAttribute("data-field") || srcEl.name;
    var targets = qsa("[data-linked-target]", form)
      .map(function (el) {
        return el.getAttribute("data-field") || el.name;
      })
      .join(",");
    if (!targets) return;
    var url =
      "/cabinet/form-assist/linked?src=" +
      encodeURIComponent(src) +
      "&value=" +
      encodeURIComponent(srcEl.value || "") +
      "&targets=" +
      encodeURIComponent(targets) +
      "&only_empty=0" +
      "&current=" +
      encodeURIComponent(JSON.stringify(currentValues(form)));
    fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        Object.keys(data || {}).forEach(function (name) {
          var el = fieldByName(name, form);
          if (!el) return;
          var auto = el.getAttribute("data-linked-auto");
          if (auto === "0" && (el.value || "").trim()) return;
          el.value = data[name];
          el.setAttribute("data-linked-auto", "1");
        });
      })
      .catch(function () {});
  }

  function bindLinked(form) {
    qsa("[data-linked-src]", form).forEach(function (el) {
      el.addEventListener("change", function () {
        applyLinked(form, el);
      });
      el.addEventListener("blur", function () {
        applyLinked(form, el);
      });
    });
    qsa("[data-linked-target]", form).forEach(function (el) {
      el.addEventListener("input", function () {
        el.setAttribute("data-linked-auto", "0");
      });
    });
  }

  function bindHistory(form) {
    qsa("[data-history]", form).forEach(function (el) {
      var box = qs('[data-suggest-for="' + el.name + '"]', el.closest(".field-block"));
      if (!box) return;
      var timer = null;
      function load() {
        var url =
          "/cabinet/form-assist/suggest?field=" +
          encodeURIComponent(el.name) +
          "&q=" +
          encodeURIComponent(el.value || "");
        fetch(url, { credentials: "same-origin", headers: { "HX-Request": "true" } })
          .then(function (r) {
            return r.text();
          })
          .then(function (html) {
            box.innerHTML = html || "";
            qsa(".suggest-pick", box).forEach(function (btn) {
              btn.addEventListener("click", function () {
                el.value = btn.getAttribute("data-value") || "";
                box.innerHTML = "";
                if (el.hasAttribute("data-linked-src")) applyLinked(form, el);
                el.dispatchEvent(new Event("change", { bubbles: true }));
              });
            });
          })
          .catch(function () {});
      }
      el.addEventListener("focus", load);
      el.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(load, 220);
      });
      el.addEventListener("blur", function () {
        setTimeout(function () {
          box.innerHTML = "";
        }, 200);
      });
    });
  }

  function dadataPath(kind) {
    if (kind === "party") return "/cabinet/counterparties/suggest/party";
    if (kind === "address") return "/cabinet/counterparties/suggest/address";
    if (kind === "bank") return "/cabinet/counterparties/suggest/bank";
    return "";
  }

  function applyPartyFields(form, data, el) {
    var map = [
      ["название_заказчика", data.name],
      ["инн_заказчика", data.inn],
      ["кпп_заказчика", data.kpp],
      ["огрн_заказчика", data.ogrn],
      ["юр_адрес_заказчика", data.address],
      ["фио_подписанта", data.manager || data.fio],
    ];
    map.forEach(function (pair) {
      var f = fieldByName(pair[0], form);
      if (f && pair[1]) f.value = pair[1];
    });
    if (el.name === "инн_заказчика" && data.inn) el.value = data.inn;
    if (el.name === "название_заказчика" && data.name) el.value = data.name;
    var nameEl = fieldByName("название_заказчика", form);
    if (nameEl && nameEl.hasAttribute("data-linked-src")) applyLinked(form, nameEl);
  }

  function applyBankFields(form, data, el) {
    var map = [
      ["бик_заказчика", data.bank_bik || data.bic],
      ["банк_заказчика", data.bank_name || data.value],
      ["к_с_заказчика", data.bank_corr_account || data.correspondent_account],
    ];
    map.forEach(function (pair) {
      var f = fieldByName(pair[0], form);
      if (f && pair[1]) f.value = pair[1];
    });
    if (el.name === "бик_заказчика" && (data.bank_bik || data.bic)) {
      el.value = data.bank_bik || data.bic;
    }
    if (el.name === "банк_заказчика" && (data.bank_name || data.value)) {
      el.value = data.bank_name || data.value;
    }
  }

  function bindDadata(form) {
    qsa("[data-dadata]", form).forEach(function (el) {
      var kind = el.getAttribute("data-dadata");
      var box = qs('[data-suggest-for="' + el.name + '"]', el.closest(".field-block"));
      var path = dadataPath(kind);
      if (!box || !path) return;
      var timer = null;
      function load() {
        var q = (el.value || "").trim();
        if (q.length < 2) {
          box.innerHTML = "";
          return;
        }
        fetch(path + "?q=" + encodeURIComponent(q), {
          credentials: "same-origin",
          headers: { "HX-Request": "true" },
        })
          .then(function (r) {
            return r.text();
          })
          .then(function (html) {
            box.innerHTML = html || "";
            qsa("button", box).forEach(function (btn) {
              btn.addEventListener(
                "click",
                function (ev) {
                  ev.preventDefault();
                  ev.stopImmediatePropagation();
                  try {
                    if (kind === "party" && btn.hasAttribute("data-json")) {
                      applyPartyFields(
                        form,
                        JSON.parse(btn.getAttribute("data-json") || "{}"),
                        el
                      );
                    } else if (kind === "bank" && btn.hasAttribute("data-json")) {
                      applyBankFields(
                        form,
                        JSON.parse(btn.getAttribute("data-json") || "{}"),
                        el
                      );
                    } else if (kind === "address") {
                      var addr = btn.getAttribute("data-value") || "";
                      if (addr) el.value = addr;
                    }
                  } catch (e) {}
                  box.innerHTML = "";
                },
                true
              );
            });
          })
          .catch(function () {});
      }
      el.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(load, 300);
      });
    });
  }

  function init(root) {
    qsa("form", root || document).forEach(function (form) {
      if (!form.querySelector(".form-field")) return;
      if (form.getAttribute("data-form-assist")) return;
      form.setAttribute("data-form-assist", "1");
      bindLinked(form);
      bindHistory(form);
      bindDadata(form);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    init(document);
  });
  document.addEventListener("htmx:afterSettle", function (ev) {
    init(ev.target);
  });
})();

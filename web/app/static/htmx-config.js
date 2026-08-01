/* Конфиг HTMX для кабинетных HTML-форм.
 * Без этого hx-boost глотает ответы 400/422 и кнопка «Сохранить» выглядит мёртвой.
 */
(function () {
  if (!window.htmx) return;
  htmx.config.responseHandling = [
    { code: "204", swap: false },
    { code: "[23]..", swap: true },
    { code: "400", swap: true },
    { code: "401", swap: true },
    { code: "403", swap: true },
    { code: "422", swap: true },
    { code: "[45]..", swap: false, error: true },
    { code: "...", swap: false },
  ];
})();

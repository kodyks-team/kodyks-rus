
"use strict";

(function () {
    // Подтверждение отправки форм с атрибутом data-confirm.
    document.addEventListener("submit", function (event) {
        const form = event.target;

        if (!(form instanceof HTMLFormElement)) {
            return;
        }

        const message = form.getAttribute("data-confirm");

        if (message && !window.confirm(message)) {
            event.preventDefault();
        }
    });

    // Отмена смены пароля.
    document.addEventListener("click", function (event) {
        const target = event.target;

        if (!(target instanceof Element)) {
            return;
        }

        const button = target.closest("[data-cancel-password]");

        if (!button) {
            return;
        }

        const details = button.closest("details");

        if (!details) {
            return;
        }

        const passwordForm = details.querySelector(".admin-password-form");

        if (passwordForm) {
            passwordForm.reset();

            passwordForm
                .querySelectorAll('input[type="password"]')
                .forEach(function (input) {
                    input.value = "";
                });
        }

        details.open = false;

        const summary = details.querySelector("summary");

        if (summary) {
            summary.focus();
        }
    });

    // Переключение темы без перезагрузки страницы.
    const form = document.getElementById("theme-form");
    const btn = document.getElementById("theme-toggle-btn");

    if (!form || !btn) {
        return;
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();

        const html = document.documentElement;
        const current = html.getAttribute("data-theme") || "dark";
        const next = current === "light" ? "dark" : "light";

        html.setAttribute("data-theme", next);
        btn.textContent = next === "light" ? "🌙" : "☀️";

        const meta = document.querySelector('meta[name="theme-color"]');

        if (meta) {
            meta.content = next === "light" ? "#eef2fa" : "#0a0e1a";
        }

        const secure = location.protocol === "https:" ? "; Secure" : "";

        document.cookie =
            "theme=" + next +
            "; path=/; max-age=31536000; SameSite=Lax" + secure;
    });
})();

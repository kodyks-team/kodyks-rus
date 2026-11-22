"use strict";

(function () {
    // Подтверждение действий перед отправкой формы.
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            const message = form.getAttribute("data-confirm");

            if (message && !window.confirm(message)) {
                event.preventDefault();
            }
        });
    });

    // Отмена смены пароля: очищаем поле и закрываем раскрывающуюся форму.
    document.querySelectorAll("[data-cancel-password]").forEach(function (button) {
        button.addEventListener("click", function () {
            const details = button.closest("details");

            if (!details) {
                return;
            }

            const form = details.querySelector("form");
            if (form) {
                form.reset();
            }

            details.open = false;

            const summary = details.querySelector("summary");
            if (summary) {
                summary.focus();
            }
        });
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
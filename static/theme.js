(function () {
  var form = document.getElementById('theme-form');
  var btn  = document.getElementById('theme-toggle-btn');
  if (!form || !btn) return;

  form.addEventListener('submit', function (e) {
    e.preventDefault();

    var html    = document.documentElement;
    var current = html.getAttribute('data-theme') || 'dark';
    var next    = current === 'light' ? 'dark' : 'light';

    html.setAttribute('data-theme', next);
    btn.textContent = next === 'light' ? '🌙' : '☀️';

    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = next === 'light' ? '#eef2fa' : '#0a0e1a';

    var secure = location.protocol === 'https:' ? '; Secure' : '';
    document.cookie =
      'theme=' + next +
      '; path=/; max-age=31536000; SameSite=Lax' + secure;
  });
})();
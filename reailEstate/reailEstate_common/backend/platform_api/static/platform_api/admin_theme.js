"use strict";
(() => {
  const cookieKey = 'mmr-theme';
  const preference = () => {
    const cookie = document.cookie.split('; ').find(item => item.startsWith(`${cookieKey}=`))?.slice(cookieKey.length + 1);
    if (cookie === 'auto' || cookie === 'light' || cookie === 'dark') return cookie;
    try { const saved = localStorage.getItem('theme'); return saved === 'light' || saved === 'dark' ? saved : 'auto'; } catch { return 'auto'; }
  };
  const apply = () => {
    const chosen = preference();
    try { localStorage.setItem('theme', chosen); } catch {}
    document.documentElement.dataset.theme = chosen === 'auto'
      && !matchMedia('(prefers-color-scheme: dark)').matches
      && !matchMedia('(prefers-color-scheme: light)').matches ? 'dark' : chosen;
    return chosen;
  };
  apply();
  addEventListener('DOMContentLoaded', () => {
    const select = document.getElementById('admin-theme-select');
    if (!select) return;
    const sync = () => { select.value = apply(); };
    select.addEventListener('change', () => {
      document.cookie = `${cookieKey}=${select.value}; Path=/; Max-Age=${400 * 24 * 60 * 60}; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
      try { localStorage.setItem('theme', select.value); } catch {}
      sync();
    });
    addEventListener('focus', sync);
    addEventListener('storage', event => { if (event.key === 'theme' || event.key === null) sync(); });
    for (const scheme of ['dark', 'light']) matchMedia(`(prefers-color-scheme: ${scheme})`).addEventListener('change', sync);
    sync();
  });
})();

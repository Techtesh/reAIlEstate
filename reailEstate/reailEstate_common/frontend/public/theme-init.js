const cookieTheme = document.cookie.split('; ').find(item => item.startsWith('mmr-theme='))?.slice('mmr-theme='.length);
const validCookie = cookieTheme === 'auto' || cookieTheme === 'light' || cookieTheme === 'dark';
let chosen = validCookie ? cookieTheme : 'auto';
if (!validCookie) {
  try { chosen = localStorage.getItem('theme') || 'auto'; } catch {}
}
document.documentElement.dataset.theme = chosen === 'light' || chosen === 'dark' ? chosen
  : matchMedia('(prefers-color-scheme: dark)').matches ? 'dark'
  : matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';

'use strict';
try {
  const savedTheme = localStorage.getItem('nodeseek-theme');
  if (savedTheme === 'light' || savedTheme === 'dark') {
    document.querySelector('meta[name="color-scheme"]').content = savedTheme;
  }
} catch { /* System colors remain usable when storage is unavailable. */ }

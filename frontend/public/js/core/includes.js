// Partials loader (core) - no global pollution besides window.dh.utils.include if needed
(function () {
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {};
  window.dh.core = window.dh.core || {};
  function runScripts(container) {
    container.querySelectorAll('script').forEach((old) => {
      const s = document.createElement('script');
      if (old.src) {
        s.src = old.src;
        s.defer = old.defer;
        s.async = old.async;
      } else {
        s.textContent = old.textContent;
      }
      document.head.appendChild(s);
      old.remove();
    });
  }
  function highlightActiveLinks(root) {
    const path = location.pathname.split('/').pop() || 'index.html';
    (root || document).querySelectorAll('a[data-active-on]').forEach((a) => {
      if (a.getAttribute('data-active-on') === path) {
        a.classList.add('text-[#f46f47]', 'underline', 'underline-offset-4');
      }
    });
  }
  async function injectIncludes() {
    const nodes = document.querySelectorAll('[data-include]');
    await Promise.all(
      Array.from(nodes).map(async (el) => {
        const url = el.getAttribute('data-include');
        if (!url) return;
        try {
          const res = await fetch(url, { cache: 'no-cache' });
          if (!res.ok) throw new Error('Failed ' + url);
          const html = await res.text();
          const tmp = document.createElement('div');
          tmp.innerHTML = html.trim();
          const repl = tmp.firstElementChild || tmp;
          el.replaceWith(repl);
          highlightActiveLinks(repl);
          runScripts(repl);
        } catch (e) {
          console.error('Include failed', url, e);
        }
      })
    );
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectIncludes);
  } else {
    injectIncludes();
  }
  window.dh.core.injectIncludes = injectIncludes;
})();

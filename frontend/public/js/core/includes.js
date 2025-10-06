// Partials loader (core) — supports multiple root nodes and executes scripts from partials
(function () {
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {};
  window.dh.core = window.dh.core || {};
  function isDbg() {
    try { return !!window.DEBUG_BANNER; } catch { return false; }
  }
  function dlog() {
    if (!isDbg()) return; try { console.log.apply(console, arguments); } catch {}
  }
  function derror() {
    if (!isDbg()) return; try { console.error.apply(console, arguments); } catch {}
  }

  function executeScriptNode(old) {
    const s = document.createElement('script');
    if (old.src) {
      s.src = old.src;
      s.defer = old.defer;
      s.async = old.async;
      s.type = old.type || '';
      dlog('[includes] appending external script', s.src);
    } else {
      s.textContent = old.textContent || '';
      s.type = old.type || '';
      dlog('[includes] appending inline script', (old.textContent || '').slice(0, 80));
    }
    document.head.appendChild(s);
  }

  function runScriptNodes(scriptNodes) {
    if (!scriptNodes || !scriptNodes.length) return;
    dlog('[includes] executing', scriptNodes.length, 'extracted script node(s)');
    scriptNodes.forEach(executeScriptNode);
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
    dlog('[includes] init injectIncludes');
    const nodes = document.querySelectorAll('[data-include]');
    await Promise.all(
      Array.from(nodes).map(async (el) => {
        const url = el.getAttribute('data-include');
        if (!url) return;
        try {
          dlog('[includes] fetching include', url);
          const res = await fetch(url, { cache: 'no-cache' });
          if (!res.ok) throw new Error('Failed ' + url);
          const html = await res.text();
          // Parse HTML allowing multiple root nodes
          const tpl = document.createElement('template');
          tpl.innerHTML = html.trim();
          // Extract all scripts (any depth) before insertion
          const scripts = Array.from(tpl.content.querySelectorAll('script'));
          scripts.forEach((s) => s.remove());
          // Insert all remaining nodes where the placeholder was
          const frag = document.createDocumentFragment();
          Array.from(tpl.content.childNodes).forEach((n) => frag.appendChild(n));
          el.replaceWith(frag);
          highlightActiveLinks();
          dlog('[includes] injected', url, 'scripts found:', scripts.length);
          // Execute extracted scripts
          runScriptNodes(scripts);
        } catch (e) {
          derror('Include failed', url, e);
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

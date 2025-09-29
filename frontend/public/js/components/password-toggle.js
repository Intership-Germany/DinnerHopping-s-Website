/** Password visibility toggle component
 * Usage: dh.components.initPasswordToggle('#signup-password');
 */
(function () {
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {};
  window.dh.components = window.dh.components || {};
  function initPasswordToggle(selector) {
    const input = typeof selector === 'string' ? document.querySelector(selector) : selector;
    if (!input) return;
    const container = input.parentElement;
    if (!container) return;
    container.classList.add('relative');
    if (container.querySelector('.pwd-toggle-btn')) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className =
      'pwd-toggle-btn absolute right-3 top-9 -translate-y-1/2 text-gray-500 hover:text-gray-700 focus:outline-none';
    btn.setAttribute('aria-label', 'Show password');
    btn.innerHTML =
      '<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="w-5 h-5"><path d="M12 5c-7 0-11 7-11 7s4 7 11 7 11-7 11-7-4-7-11-7Zm0 12a5 5 0 1 1 0-10 5 5 0 0 1 0 10Z"/></svg>';
    btn.addEventListener('click', () => {
      const hidden = input.type === 'password';
      input.type = hidden ? 'text' : 'password';
      btn.setAttribute('aria-label', hidden ? 'Hide password' : 'Show password');
    });
    container.appendChild(btn);
  }
  window.dh.components.initPasswordToggle = initPasswordToggle;
})();

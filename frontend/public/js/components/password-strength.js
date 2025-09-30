/** Password strength meter component
 * Usage: dh.components.initPasswordStrength('#signup-password');
 */
(function () {
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {};
  window.dh.components = window.dh.components || {};
  function score(pwd) {
    let s = 0;
    if (!pwd) return 0;
    const len = pwd.length;
    const variations = [/[a-z]/, /[A-Z]/, /\d/, /[^\w\s]/].reduce(
      (acc, r) => acc + (r.test(pwd) ? 1 : 0),
      0
    );
    if (len >= 8) s += 1;
    if (len >= 12) s += 1;
    s += Math.min(2, variations);
    return Math.max(0, Math.min(4, s));
  }
  function initPasswordStrength(selector) {
    const input = typeof selector === 'string' ? document.querySelector(selector) : selector;
    if (!input) return;
    if (input.parentElement.querySelector('.pwd-strength-wrap')) return;
    const meterWrap = document.createElement('div');
    meterWrap.className = 'pwd-strength-wrap mt-2';
    const barBg = document.createElement('div');
    barBg.className = 'w-full h-2 bg-gray-200 rounded-full overflow-hidden';
    const bar = document.createElement('div');
    bar.className = 'h-2 w-0 bg-red-500 transition-all duration-300';
    barBg.appendChild(bar);
    const label = document.createElement('div');
    label.className = 'mt-1 text-xs text-gray-600';
    meterWrap.appendChild(barBg);
    meterWrap.appendChild(label);
    input.parentElement.appendChild(meterWrap);
    const colors = ['bg-red-500', 'bg-orange-500', 'bg-yellow-500', 'bg-lime-500', 'bg-green-600'];
    const texts = ['Very weak', 'Weak', 'Fair', 'Strong', 'Very strong'];
    function render() {
      const sc = score(input.value);
      bar.style.width = ['0', '25', '50', '75', '100'][sc] + '%';
      bar.className = 'h-2 transition-all duration-300 ' + colors[sc];
      label.textContent = input.value ? texts[sc] : '';
    }
    input.addEventListener('input', render);
  }
  window.dh.components.initPasswordStrength = initPasswordStrength;
  window.dh.components.scorePassword = score;
})();

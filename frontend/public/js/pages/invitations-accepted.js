// Invitations accepted page script: show message and optionally auto-redirect to a safe `next` path
(function () {
  document.addEventListener('DOMContentLoaded', () => {
    const msgEl = document.getElementById('ia-msg');
    const countdownWrap = document.getElementById('ia-countdown');
    const secondsEl = document.getElementById('ia-seconds');
    const goNow = document.getElementById('ia-go-now');
    const signin = document.getElementById('ia-signin');
    const events = document.getElementById('ia-events');

    const urlParams = new URLSearchParams(window.location.search);
    const nextParam = urlParams.get('next') || urlParams.get('redirect');
    // allow callers to disable auto-redirect by setting redirect=0
    const autoRedirect = urlParams.get('redirect') !== '0';

    function isSafe(n) {
      if (!n) return false;
      if (n.includes('://')) return false;
      if (!n.startsWith('/')) return false;
      if (n.indexOf('//', 1) !== -1) return false;
      if (/\r|\n/.test(n)) return false;
      return true;
    }

    let target = null;
    if (nextParam && isSafe(nextParam)) target = nextParam;

    // If a safe next target is provided, show countdown and redirect after a short delay
    if (target && autoRedirect) {
      countdownWrap.hidden = false;
      let seconds = 6;
      secondsEl.textContent = String(seconds);
      const t = setInterval(() => {
        seconds -= 1;
        secondsEl.textContent = String(seconds);
        if (seconds <= 0) {
          clearInterval(t);
          window.location.href = target;
        }
      }, 1000);

      // allow immediate navigation
      goNow && goNow.addEventListener('click', (e) => {
        e.preventDefault();
        clearInterval(t);
        window.location.href = target;
      });
    } else {
      // No redirect target, wire go-now to events
      goNow && goNow.addEventListener('click', (e) => {
        e.preventDefault();
        window.location.href = '/home.html';
      });
    }

    // If user is signed in, prefer to send them to profile instead of login
    try {
      if (window.auth && window.auth.getCurrentUser) {
        window.auth.getCurrentUser().then((u) => {
          if (u && u.email) {
            signin.href = '/profile.html';
          }
        }).catch(() => {});
      }
    } catch (e) {}
  });
})();

/**
 * Reset password page (formerly reset-password.js)
 * Validates token from URL and posts new password.
 */
document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('reset-form');
  const msg = document.getElementById('rp-msg');
  const pwd = document.getElementById('rp-password');
  const conf = document.getElementById('rp-password-confirm');
  const BACKEND_BASE = window.BACKEND_BASE_URL;
  function setMsg(text, type = 'info') {
    if (!msg) return;
    msg.textContent = text || '';
    msg.className =
      'mb-4 text-sm text-center ' + (type === 'error' ? 'text-red-600' : 'text-green-700');
  }
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token');
  if (!token) {
    setMsg('Invalid or missing reset link. Please request a new one from the login page.', 'error');
    if (form) form.querySelector('button[type="submit"]').disabled = true;
    return;
  }
  if (pwd && conf) {
    const hint = document.createElement('div');
    hint.className = 'mt-1 text-xs';
    conf.parentElement.appendChild(hint);
    function update() {
      if (!conf.value) {
        hint.textContent = '';
        return;
      }
      if (pwd.value && conf.value === pwd.value) {
        hint.textContent = 'Passwords match';
        hint.style.color = '#059669';
      } else {
        hint.textContent = 'Passwords do not match';
        hint.style.color = '#dc2626';
      }
    }
    pwd.addEventListener('input', update);
    conf.addEventListener('input', update);
  }
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const p = pwd.value;
    const c = conf.value;
    if (!p || !c) {
      setMsg('Please fill both password fields.', 'error');
      return;
    }
    if (p !== c) {
      setMsg('Passwords do not match.', 'error');
      return;
    }
    if (p.length < 10) {
      setMsg('Password must be at least 10 characters.', 'error');
      return;
    }
    try {
      const btn = form.querySelector('button[type="submit"]');
      if (btn) {
        btn.disabled = true;
        btn.textContent = 'Saving…';
      }
      const res = await fetch(`${BACKEND_BASE}/reset-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, new_password: p }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail =
          data && data.detail
            ? Array.isArray(data.detail)
              ? data.detail.map((d) => d.msg).join(' ')
              : data.detail
            : 'Unable to reset password.';
        setMsg(detail, 'error');
        if (btn) {
          btn.disabled = false;
          btn.textContent = 'Set new password';
        }
        return;
      }
      setMsg(
        'Your password has been reset. You can now log in with your new password. Redirecting…'
      );
      setTimeout(() => {
        window.location.href = 'login.html';
      }, 1500);
    } catch {
      setMsg('Network error while resetting password.', 'error');
    }
  });
});

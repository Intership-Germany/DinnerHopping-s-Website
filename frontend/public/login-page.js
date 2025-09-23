document.addEventListener('DOMContentLoaded', () => {
  const loginPage = document.getElementById('login-page');
  const loginForm = document.getElementById('login-form');
  const signupForm = document.getElementById('signup-form');
  const showLoginBtn = document.getElementById('show-login-btn');
  const showSignupBtn = document.getElementById('show-signup-btn');
  const resendBtn = document.getElementById('resend-verif-btn');
  if (!window.dbg) {
    window.dbg = { logReq: (...args) => console.log('[dbg]', ...args) };
  }
  loginPage.hidden = false;
  showLoginBtn.addEventListener('click', () => {
    loginForm.hidden = false;
    signupForm.hidden = true;
    showLoginBtn.classList.add('bg-[#f46f47]', 'text-white');
    showLoginBtn.classList.remove('text-[#4c4c4c]', 'hover:bg-white');
    showSignupBtn.classList.remove('bg-[#f46f47]', 'text-white');
    showSignupBtn.classList.add('text-[#4c4c4c]', 'hover:bg-white');
  });
  showSignupBtn.addEventListener('click', () => {
    loginForm.hidden = true;
    signupForm.hidden = false;
    showSignupBtn.classList.add('bg-[#f46f47]', 'text-white');
    showSignupBtn.classList.remove('text-[#4c4c4c]', 'hover:bg-white');
    showLoginBtn.classList.remove('bg-[#f46f47]', 'text-white');
    showLoginBtn.classList.add('text-[#4c4c4c]', 'hover:bg-white');
  });
  const BACKEND_BASE = window.BACKEND_BASE_URL || 'http://localhost:8000';
  function showMessage(text, type = 'info') {
    let el = document.getElementById('global-msg');
    if (!el) {
      el = document.createElement('div');
      el.id = 'global-msg';
      el.className = 'mt-4 mb-4 text-center';
      loginForm.parentElement.prepend(el);
    }
    if (Array.isArray(text)) text = text.join(' ');
    if (typeof text === 'object') text = JSON.stringify(text);
    el.textContent = text;
    el.style.color = type === 'error' ? '#dc2626' : '#059669';
    if (type === 'error') {
      el.style.background = '#fff5f5';
      el.style.padding = '8px';
      el.style.borderRadius = '6px';
      el.style.border = '1px solid #fecaca';
    } else {
      el.style.background = '';
      el.style.border = '';
    }
  }
  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;
    try {
      const res = await fetch(`${BACKEND_BASE}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: email, password })
      });
      const data = await res.json();
      dbg.logReq(`POST /login ${email}`, { status: res.status, body: data });
      if (!res.ok) {
        if (data.detail) {
          if (Array.isArray(data.detail)) {
            const msgs = data.detail.map(d => d.msg || JSON.stringify(d)).join(' ');
            showMessage(msgs, 'error');
          } else if (typeof data.detail === 'string') {
            if (res.status === 401 && data.detail.toLowerCase().includes('not verified')) {
              showMessage('Email not verified. Click the link sent to your email or request a new one.', 'error');
              if (resendBtn) {
                resendBtn.classList.remove('hidden');
                resendBtn.disabled = false;
              }
            } else {
              showMessage(data.detail, 'error');
            }
          } else {
            showMessage(JSON.stringify(data.detail), 'error');
          }
        } else {
          showMessage('Login failed', 'error');
        }
        return;
      }
      const token = data.access_token || data.token || data.accessToken;
      if (!token) {
        showMessage('Login did not return a token', 'error');
        return;
      }
      if (window.auth && typeof window.auth.setCookie === 'function') {
        window.auth.setCookie('dh_token', token, 7);
      } else {
        const maxAge = `; Max-Age=${7 * 86400}`;
        const attrs = `Path=/; SameSite=Strict${location.protocol === 'https:' ? '; Secure' : ''}${maxAge}`;
        document.cookie = `dh_token=${encodeURIComponent(token)}; ${attrs}`;
      }
      showMessage('Logged in successfully');
      // If a safe next parameter was provided, navigate there. Otherwise go to profile.
      const urlParams = new URLSearchParams(window.location.search);
      const nextParam = urlParams.get('next');
      function isSafeNext(n) {
        if (!n) return false;
        try {
          // Only allow path-relative redirects starting with '/'
          // Reject anything that looks like a full URL (contains ://) or contains a double-slash after the first char
          if (n.includes('://')) return false;
          if (!n.startsWith('/')) return false;
          if (n.indexOf('//', 1) !== -1) return false;
          // prevent CRLF
          if (n.includes('\n') || n.includes('\r')) return false;
          return true;
        } catch (e) {
          return false;
        }
      }
      if (nextParam && isSafeNext(nextParam)) {
        // If the next path looks like it should be handled by the frontend, navigate on current origin
        // If the backend expects the next to be a backend path, the path will still resolve properly.
        const dest = nextParam;
        window.location.href = dest.startsWith('/') ? dest : ('/' + dest);
      } else {
        window.location.href = 'profile.html';
      }
    } catch (err) {
      showMessage('Network error', 'error');
      console.error(err);
    }
  });
  if (resendBtn) {
    resendBtn.addEventListener('click', async () => {
      const email = document.getElementById('login-email').value;
      if (!email) {
        showMessage('Please enter your email in the field above before resending.', 'error');
        return;
      }
      resendBtn.disabled = true;
      resendBtn.textContent = 'Sending...';
      try {
        const res = await fetch(`${BACKEND_BASE}/resend-verification`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email })
        });
        dbg.logReq('POST /resend-verification', { status: res.status });
        if (!res.ok) {
          showMessage('Unable to resend. Please try again later.', 'error');
        } else {
          showMessage('If this account exists and is not verified, a new email has just been sent. Please check your inbox.', 'info');
        }
      } catch (e) {
        showMessage('Network error during resend', 'error');
      } finally {
        resendBtn.disabled = false;
        resendBtn.textContent = 'Resend verification email';
      }
    });
  }
  signupForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const firstname = document.getElementById('signup-firstname').value;
    const lastname = document.getElementById('signup-lastname').value;
    const email = document.getElementById('signup-email').value;
    const password = document.getElementById('signup-password').value;
    const name = `${firstname} ${lastname}`.trim();
    try {
      const res = await fetch(`${BACKEND_BASE}/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email, password })
      });
      const data = await res.json();
      dbg.logReq(`POST /register ${email}`, { status: res.status, body: data });
      if (!res.ok) {
        if (data.detail) {
          if (Array.isArray(data.detail)) {
            const msgs = data.detail.map(d => d.msg || JSON.stringify(d)).join(' ');
            showMessage(msgs, 'error');
          } else if (typeof data.detail === 'string') {
            showMessage(data.detail, 'error');
          } else {
            showMessage(JSON.stringify(data.detail), 'error');
          }
        } else {
          showMessage('Signup failed', 'error');
        }
        return;
      }
      showMessage('Account created. Please check your email for a verification link to verify your account.');
    } catch (err) {
      showMessage('Network error', 'error');
      console.error(err);
    }
  });
});

// Login & Signup page logic (refactored, using dh namespace + components)
(function () {
  document.addEventListener('DOMContentLoaded', () => {
    const els = {
      loginPage: document.getElementById('login-page'),
      loginForm: document.getElementById('login-form'),
      signupForm: document.getElementById('signup-form'),
      showLoginBtn: document.getElementById('show-login-btn'),
      showSignupBtn: document.getElementById('show-signup-btn'),
      resendBtn: document.getElementById('resend-verif-btn'),
      forgotToggle: document.getElementById('forgotpw-toggle'),
      forgotForm: document.getElementById('forgotpw-form'),
      forgotSubmit: document.getElementById('forgotpw-submit'),
      forgotEmail: document.getElementById('forgot-email'),
    };
    const BACKEND_BASE = window.BACKEND_BASE_URL;
    window.dh = window.dh || {};
    window.dh.debug = window.dh.debug || { log: (...a) => console.log('[dh]', ...a) };

    function showMessage(text, type = 'info') {
      let el = document.getElementById('global-msg');
      if (!el) {
        el = document.createElement('div');
        el.id = 'global-msg';
        el.className = 'mt-4 mb-4 text-center text-sm';
        // make it an accessible live region so screen readers announce updates
        el.setAttribute('aria-live', 'polite');
        el.setAttribute('role', 'status');
        el.setAttribute('aria-atomic', 'true');
        (els.loginForm || document.body).parentElement.prepend(el);
      }
      if (Array.isArray(text)) text = text.join(' ');
      if (typeof text === 'object') text = JSON.stringify(text);
      el.textContent = text;
      el.style.color = type === 'error' ? '#dc2626' : '#059669';
      el.style.fontWeight = '500';
    }

    // Tabs
    els.loginPage && (els.loginPage.hidden = false);
    function activate(tab) {
      const isLogin = tab === 'login';
      els.loginForm.hidden = !isLogin;
      els.signupForm.hidden = isLogin;
      const a = els.showLoginBtn,
        b = els.showSignupBtn;
      if (a && b) {
        if (isLogin) {
          a.classList.add('bg-[#f46f47]', 'text-white');
          b.classList.remove('bg-[#f46f47]', 'text-white');
        } else {
          b.classList.add('bg-[#f46f47]', 'text-white');
          a.classList.remove('bg-[#f46f47]', 'text-white');
        }
      }
      if (!isLogin && els.forgotForm) els.forgotForm.classList.add('hidden');
    }
    els.showLoginBtn && els.showLoginBtn.addEventListener('click', () => activate('login'));
    els.showSignupBtn && els.showSignupBtn.addEventListener('click', () => activate('signup'));

    // Forgot password
    if (els.forgotToggle && els.forgotForm) {
      els.forgotToggle.addEventListener('click', () => {
        els.forgotForm.classList.toggle('hidden');
        if (!els.forgotForm.classList.contains('hidden')) {
          const loginEmail = document.getElementById('login-email');
          if (loginEmail && loginEmail.value) els.forgotEmail.value = loginEmail.value;
          els.forgotEmail && els.forgotEmail.focus();
        }
      });
    }
    if (els.forgotSubmit) {
      els.forgotSubmit.addEventListener('click', async () => {
        const email = (els.forgotEmail.value || '').trim();
        if (!email) {
          showMessage('Please enter your registered email.', 'error');
          return;
        }
        try {
          els.forgotSubmit.disabled = true;
          els.forgotSubmit.textContent = 'Sending…';
          const res = await fetch(`${BACKEND_BASE}/forgot-password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) {
            const msg = data.detail
              ? Array.isArray(data.detail)
                ? data.detail.map((d) => d.msg).join(' ')
                : data.detail
              : 'Unable to process request.';
            showMessage(msg, 'error');
          } else {
            showMessage('If an account exists, a password reset link has been sent.');
          }
        } catch {
          showMessage('Network error while requesting reset link.', 'error');
        } finally {
          els.forgotSubmit.disabled = false;
          els.forgotSubmit.textContent = 'Send reset link';
        }
      });
    }

    // Password helpers (components)
    if (window.dh.components) {
      ['#login-password', '#signup-password', '#signup-password-confirm'].forEach((sel) =>
        window.dh.components.initPasswordToggle(sel)
      );
      window.dh.components.initPasswordStrength('#signup-password');
    }

    // Address autocomplete signup
    if (window.dh.components) {
      window.dh.components.initAddressAutocomplete({
        mode: 'signup',
        selectors: {
          street: '#signup-street',
          number: '#signup-number',
          postal: '#signup-postal',
          city: '#signup-city',
        },
      });
    }

    // Form submit: login
    els.loginForm &&
      els.loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = document.getElementById('login-email').value;
        const password = document.getElementById('login-password').value;
        try {
          const data = await window.auth.login(email, password);
          window.dh.debug.log('POST /login', 200, data);
          showMessage('Logged in successfully');
          const urlParams = new URLSearchParams(location.search);
          const nextParam = urlParams.get('next');
          const invitationState = urlParams.get('invitation_state');
          function isSafe(n) {
            if (!n) return false;
            if (n.includes('://')) return false;
            if (!n.startsWith('/')) return false;
            if (n.indexOf('//', 1) !== -1) return false;
            if (/\r|\n/.test(n)) return false;
            return true;
          }
          // If the login flow was started because of an invitation temporary state,
          // redirect back to the invitation page which understands the state param.
          if (invitationState) {
            // invitationState should be a URL-safe token (no scheme). Do a simple safety check.
            if (/^[A-Za-z0-9-_]+$/.test(invitationState)) {
              const loc = window.location;
              const basePath = loc.pathname.replace(/\/[^/]*$/, ''); // strip filename
              const encoded = encodeURIComponent(invitationState);
              const candidates = [
                // same directory as current document: preserves /api/ prefix
                `${loc.origin}${basePath}/invitation.html?state=${encoded}`,
                // root-level invitation page
                `${loc.origin}/invitation.html?state=${encoded}`,
              ];

              // Probe candidates via HEAD request and navigate to the first that returns OK.
              for (const c of candidates) {
                try {
                  const r = await fetch(c, { method: 'HEAD' });
                  if (r.ok) {
                    location.href = c;
                    return;
                  }
                } catch (_) {
                  // ignore network/CORS errors and try next candidate
                }
              }

              // As a last resort, try probing the backend API for the state directly. This helps
              // when HEAD requests to static files are blocked (CORS/proxy).
              try {
                const backendCheck = await fetch((window.BACKEND_BASE_URL || '') + `/invitations/by-state/${invitationState}`, { method: 'GET', credentials: 'include', headers: { Accept: 'application/json' } });
                if (backendCheck && backendCheck.ok) {
                  // Backend knows about the state; redirect to root-level frontend invitation page.
                  location.href = `${loc.origin}/invitation.html?state=${encoded}`;
                  return;
                }
              } catch (e) {
                // ignore and fall back to root navigation
              }

              // Fallback: navigate to root-level invitation page even if probes failed. User can then
              // see the invitation UI (it may show expired/invalid state if the server rejects it).
              location.href = `${loc.origin}/invitation.html?state=${encoded}`;
              return;
            }
          }
          if (nextParam && isSafe(nextParam)) {
            location.href = nextParam.startsWith('/') ? nextParam : '/' + nextParam;
          } else {
            location.href = 'profile.html';
          }
        } catch (err) {
          const msg = (err && err.message) || 'Network / login error';
          if (/not verified/i.test(msg)) {
            showMessage('Email not verified. Request a new link.', 'error');
            els.resendBtn && (els.resendBtn.classList.remove('hidden'), (els.resendBtn.disabled = false));
          } else {
            showMessage(msg, 'error');
          }
        }
      });

    // Resend verification
    els.resendBtn &&
      els.resendBtn.addEventListener('click', async () => {
        const email = document.getElementById('login-email').value;
        if (!email) {
          showMessage('Enter your email above first.', 'error');
          return;
        }
        els.resendBtn.disabled = true;
        els.resendBtn.textContent = 'Sending...';
        try {
          const res = await fetch(`${BACKEND_BASE}/resend-verification`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email }),
          });
          if (!res.ok) showMessage('Unable to resend. Try later.', 'error');
          else showMessage('If the account exists, a new email has been sent.');
        } catch {
          showMessage('Network error during resend', 'error');
        } finally {
          els.resendBtn.disabled = false;
          els.resendBtn.textContent = 'Resend verification email';
        }
      });

    // Signup
    els.signupForm &&
      els.signupForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const firstname = document.getElementById('signup-firstname').value.trim();
        const lastname = document.getElementById('signup-lastname').value.trim();
        const email = document.getElementById('signup-email').value.trim();
  const phoneRaw = document.getElementById('signup-phone').value.trim();
        const password = document.getElementById('signup-password').value;
        const confirm = document.getElementById('signup-password-confirm').value;
        const street = document.getElementById('signup-street').value.trim();
        const number = document.getElementById('signup-number').value.trim();
        const postal = document.getElementById('signup-postal').value.trim();
        const city = document.getElementById('signup-city').value.trim();
        const gender = document.getElementById('signup-gender').value;
        if (!firstname || !lastname) {
          showMessage('Enter your first and last name.', 'error');
          return;
        }
        if (!email) {
          showMessage('Enter a valid email.', 'error');
          return;
        }
        if (!phoneRaw) {
          showMessage('Enter your phone number.', 'error');
          return;
        }
        let normalizedPhone = phoneRaw.replace(/[^0-9+]/g, '');
        if (normalizedPhone.startsWith('+')) {
          normalizedPhone = '+' + normalizedPhone.slice(1).replace(/\+/g, '');
        } else {
          normalizedPhone = normalizedPhone.replace(/\+/g, '');
        }
        const phoneDigits = normalizedPhone.startsWith('+')
          ? normalizedPhone.slice(1)
          : normalizedPhone;
        if (!/^[0-9]+$/.test(phoneDigits) || phoneDigits.length < 6) {
          showMessage('Enter a valid phone number with at least 6 digits.', 'error');
          return;
        }
        if (!password || !confirm) {
          showMessage('Enter and confirm your password.', 'error');
          return;
        }
        if (password !== confirm) {
          showMessage('Passwords do not match.', 'error');
          return;
        }
        if (password.length < 8) {
          showMessage('Password must be at least 8 characters.', 'error');
          return;
        }
        const missing = [];
        if (!/[a-z]/.test(password)) missing.push('lowercase');
        if (!/[A-Z]/.test(password)) missing.push('uppercase');
        if (!/[^A-Za-z0-9]/.test(password)) missing.push('special');
        if (missing.length) {
          showMessage('Password needs ' + missing.join(', '), 'error');
          return;
        }
        if (!street || !number || !postal || !city) {
          showMessage('Provide full address.', 'error');
          return;
        }
        if (!/^[0-9A-Za-z \-]{3,10}$/.test(postal)) {
          showMessage('Invalid postal code.', 'error');
          return;
        }
        if (!gender) {
          showMessage('Select gender.', 'error');
          return;
        }
        const payload = {
          email,
          password,
          password_confirm: confirm,
          first_name: firstname,
          last_name: lastname,
          street,
          street_no: number,
          postal_code: postal,
          city,
          gender,
          phone_number: normalizedPhone,
          preferences: {},
        };
        // Helper to toggle loading state for the form
        const setLoading = (isLoading) => {
          const btn = els.signupForm.querySelector('[type="submit"]');
          const inputs = Array.from(els.signupForm.querySelectorAll('input,select,button'));
          if (isLoading) {
            if (btn) {
              btn.dataset.prevLabel = btn.textContent;
              btn.textContent = 'Creating account…';
              btn.disabled = true;
            }
            inputs.forEach((i) => {
              if (i !== btn) i.disabled = true;
            });
            // simple aria-live region update so screen readers announce processing
            showMessage('Processing your registration…');
          } else {
            if (btn) {
              btn.textContent = btn.dataset.prevLabel || 'Sign up';
              btn.disabled = false;
            }
            inputs.forEach((i) => (i.disabled = false));
          }
        };

        try {
          setLoading(true);
          const res = await fetch(`${BACKEND_BASE}/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) {
            const d = data.detail || data.message || data.error;
            if (d) {
              if (Array.isArray(d)) showMessage(d.map((x) => x.msg || x).join(' '), 'error');
              else if (typeof d === 'string') showMessage(d, 'error');
              else showMessage(JSON.stringify(d), 'error');
            } else showMessage('Signup failed', 'error');
            return;
          }
          // Prefer server-provided message when available (backend may include info about email sending)
          const successMsg = data.message || data.detail || 'Account created. Check your email for verification link.';
          showMessage(successMsg);
          // Switch to login tab and prefill the email used for signup
          activate('login');
          try {
            const loginEmailEl = document.getElementById('login-email');
            if (loginEmailEl) {
              loginEmailEl.value = email || '';
              loginEmailEl.focus();
            }
          } catch (e) {}
        } catch {
          showMessage('Network error', 'error');
        } finally {
          setLoading(false);
        }
      });

    // Password match hint
    (function () {
      const pwd = document.getElementById('signup-password');
      const conf = document.getElementById('signup-password-confirm');
      if (!pwd || !conf) return;
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
    })();
  });
})();

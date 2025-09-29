document.addEventListener('DOMContentLoaded', () => {
  const loginPage = document.getElementById('login-page');
  const loginForm = document.getElementById('login-form');
  const signupForm = document.getElementById('signup-form');
  const showLoginBtn = document.getElementById('show-login-btn');
  const showSignupBtn = document.getElementById('show-signup-btn');
  const resendBtn = document.getElementById('resend-verif-btn');
  const forgotToggle = document.getElementById('forgotpw-toggle');
  const forgotForm = document.getElementById('forgotpw-form');
  const forgotSubmit = document.getElementById('forgotpw-submit');
  const forgotEmail = document.getElementById('forgot-email');
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
    // hide forgot form if switching tabs
    if (forgotForm) forgotForm.classList.add('hidden');
  });
  const BACKEND_BASE = window.BACKEND_BASE_URL; // fallback removed
  // Keep geocode state for signup
  let signupGeo = { lat: null, lon: null, matchedLabel: null };
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
  // Utilities: password toggle and strength meter
  function addPasswordToggle(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const container = input.parentElement; // label wrapper div
    if (!container) return;
    container.classList.add('relative');
    // Avoid duplicating
    if (container.querySelector('.pwd-toggle-btn')) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'pwd-toggle-btn absolute right-3 top-9 -translate-y-1/2 text-gray-500 hover:text-gray-700 focus:outline-none';
    btn.setAttribute('aria-label', 'Show password');
    btn.innerHTML = '<svg aria-hidden="true" xmlns="https://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="w-5 h-5"><path d="M12 5c-7 0-11 7-11 7s4 7 11 7 11-7 11-7-4-7-11-7Zm0 12a5 5 0 1 1 0-10 5 5 0 0 1 0 10Z"/></svg>';
    btn.addEventListener('click', () => {
      const isHidden = input.type === 'password';
      input.type = isHidden ? 'text' : 'password';
      btn.setAttribute('aria-label', isHidden ? 'Hide password' : 'Show password');
      // Swap icon to eye-off for hidden? Keep simple for now.
    });
    container.appendChild(btn);
  }

  function attachStrengthMeter(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    // Create meter container once
    const meterWrap = document.createElement('div');
    meterWrap.className = 'mt-2';
    const barBg = document.createElement('div');
    barBg.className = 'w-full h-2 bg-gray-200 rounded-full overflow-hidden';
    const bar = document.createElement('div');
    bar.className = 'h-2 w-0 bg-red-500 transition-all duration-300';
    barBg.appendChild(bar);
    const label = document.createElement('div');
    label.className = 'mt-1 text-xs text-gray-600';
    label.textContent = '';
    meterWrap.appendChild(barBg);
    meterWrap.appendChild(label);
    input.parentElement.appendChild(meterWrap);

    function scorePassword(pwd) {
      let score = 0;
      if (!pwd) return 0;
      const length = pwd.length;
      const variations = [/[a-z]/, /[A-Z]/, /\d/, /[^\w\s]/].reduce((acc, r) => acc + (r.test(pwd) ? 1 : 0), 0);
      if (length >= 8) score += 1;
      if (length >= 12) score += 1;
      score += Math.min(2, variations); // up to +2
      // Cap 0..4
      return Math.max(0, Math.min(4, score));
    }
    function setMeter(score) {
      const pct = [0, 25, 50, 75, 100][score];
      bar.style.width = pct + '%';
      const colors = ['bg-red-500','bg-orange-500','bg-yellow-500','bg-lime-500','bg-green-600'];
      bar.className = 'h-2 transition-all duration-300 ' + colors[score];
      const texts = ['Very weak','Weak','Fair','Strong','Very strong'];
      label.textContent = pwdInput.value ? texts[score] : '';
    }
    const pwdInput = input;
    pwdInput.addEventListener('input', () => setMeter(scorePassword(pwdInput.value)));
  }

  // Address verification using Pelias (https://pelias.cephlabs.de/v1)
  function setupAddressVerification() {
    const streetEl = document.getElementById('signup-street');
    const numEl = document.getElementById('signup-number');
    const postalEl = document.getElementById('signup-postal');
    const cityEl = document.getElementById('signup-city');
    if (!streetEl || !numEl || !postalEl || !cityEl) return;
    let debounce;
    // status/suggestions container
    let status = document.getElementById('addr-status');
    if (!status) {
      status = document.createElement('div');
      status.id = 'addr-status';
      status.className = 'text-xs text-gray-500 mt-1';
      cityEl.parentElement.parentElement.appendChild(status);
    }
    let list = document.getElementById('addr-suggestions');
    if (!list) {
      list = document.createElement('div');
      list.id = 'addr-suggestions';
      list.className = 'mt-2 space-y-1';
      status.after(list);
    }

    // Autocomplete dropdowns for Street and City using Pelias /v1/autocomplete
    function createDropdown(anchorEl) {
      const wrap = document.createElement('div');
      wrap.className = 'relative';
      // ensure anchor has relative parent
      if (!anchorEl.parentElement.classList.contains('relative')) {
        anchorEl.parentElement.classList.add('relative');
      }
      const dd = document.createElement('div');
      dd.className = 'absolute z-20 left-0 right-0 mt-1 bg-white border rounded-md shadow max-h-60 overflow-auto hidden';
      anchorEl.parentElement.appendChild(dd);
      return dd;
    }

    const peliasBase = 'https://pelias.cephlabs.de/v1';
  const streetDD = createDropdown(streetEl);
  const cityDD = createDropdown(cityEl);
  const postalDD = createDropdown(postalEl);

    function hideDD(dd) { dd.classList.add('hidden'); }
    function showDD(dd) { dd.classList.remove('hidden'); }
    function renderDD(dd, features, onPick) {
      dd.innerHTML = '';
      if (!features || features.length === 0) { hideDD(dd); return; }
      features.slice(0, 6).forEach(f => {
        const props = f.properties || {};
        const label = props.label || props.name || [props.housenumber, props.street, props.postalcode, props.locality || props.city].filter(Boolean).join(' ');
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'block w-full text-left px-3 py-2 hover:bg-gray-50';
        item.textContent = label || 'Unknown';
        item.addEventListener('click', () => onPick(f, props));
        dd.appendChild(item);
      });
      showDD(dd);
    }

    async function fetchAutocomplete(params) {
      const url = `${peliasBase}/autocomplete?${new URLSearchParams(params).toString()}`;
      const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
      if (!res.ok) return [];
      const json = await res.json();
      return (json && Array.isArray(json.features)) ? json.features : [];
    }

  let streetDeb, cityDeb, postalDeb;
    streetEl.addEventListener('input', () => {
      clearTimeout(streetDeb);
      hideDD(streetDD);
      streetDeb = setTimeout(async () => {
        const q = [numEl.value.trim(), streetEl.value.trim()].filter(Boolean).join(' ');
        const nearCity = cityEl.value.trim();
        if (q.length < 2) return;
        const features = await fetchAutocomplete({ text: q + (nearCity ? ` ${nearCity}` : ''), size: '6', layers: 'address,street' });
        renderDD(streetDD, features, (f, props) => {
          // Try to split housenumber/street
          if (props.housenumber) numEl.value = props.housenumber;
          if (props.street) streetEl.value = props.street;
          if (props.locality || props.city) cityEl.value = props.locality || props.city;
          if (props.postalcode) postalEl.value = props.postalcode;
          hideDD(streetDD);
        });
      }, 300);
    });
    cityEl.addEventListener('input', () => {
      clearTimeout(cityDeb);
      hideDD(cityDD);
      cityDeb = setTimeout(async () => {
        const q = cityEl.value.trim();
        if (q.length < 2) return;
        const features = await fetchAutocomplete({ text: q, size: '6', layers: 'locality,localadmin,borough,county,region,macroregion' });
        renderDD(cityDD, features, (f, props) => {
          if (props.locality || props.city) cityEl.value = props.locality || props.city;
          if (props.postalcode) postalEl.value = props.postalcode;
          hideDD(cityDD);
        });
      }, 300);
    });
    // Postal code -> suggest cities
    let postalCityCache = [];
    postalEl.addEventListener('input', () => {
      clearTimeout(postalDeb);
      hideDD(postalDD);
      postalDeb = setTimeout(async () => {
        const postal = postalEl.value.trim();
        if (postal.length < 2) return;
        // Use structured search to bias results by postalcode only
        const params = new URLSearchParams({ postalcode: postal, size: '10' });
        const url = `${peliasBase}/search/structured?${params.toString()}`;
        try {
          const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
          if (!res.ok) return;
          const json = await res.json();
          const feats = (json && Array.isArray(json.features)) ? json.features : [];
          // Deduplicate by city/locality name
          const byCity = new Map();
          for (const f of feats) {
            const p = f.properties || {};
            const cityName = p.locality || p.city || p.name;
            if (!cityName) continue;
            const key = cityName.toLowerCase();
            if (!byCity.has(key)) {
              byCity.set(key, {
                type: 'Feature',
                properties: {
                  locality: cityName,
                  postalcode: p.postalcode || postal,
                  label: `${cityName}${p.postalcode ? ` (${p.postalcode})` : ''}`
                },
                geometry: f.geometry || null,
                center: f.center || null,
              });
            }
          }
          const uniqueCities = Array.from(byCity.values());
          postalCityCache = uniqueCities;
          // If the postal maps uniquely to a single city, auto-fill
          if (uniqueCities.length === 1) {
            const props = uniqueCities[0].properties || {};
            if (props.locality) cityEl.value = props.locality;
            if (props.postalcode) postalEl.value = props.postalcode;
            hideDD(postalDD);
            return;
          }
          renderDD(postalDD, uniqueCities, (f, props) => {
            if (props.locality) cityEl.value = props.locality;
            if (props.postalcode) postalEl.value = props.postalcode;
            hideDD(postalDD);
          });
        } catch (e) {
          // ignore
        }
      }, 300);
    });
    // Enter on postal when unique city exists
    postalEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        if (postalDD.classList.contains('hidden') && postalCityCache && postalCityCache.length === 1) {
          const props = (postalCityCache[0] && postalCityCache[0].properties) || {};
          if (props.locality) cityEl.value = props.locality;
          if (props.postalcode) postalEl.value = props.postalcode;
          e.preventDefault();
        }
      } else if (e.key === 'Escape') {
        hideDD(postalDD);
      }
    });
    // Hide dropdowns when clicking outside
    document.addEventListener('click', (ev) => {
      if (!streetEl.parentElement.contains(ev.target)) hideDD(streetDD);
      if (!cityEl.parentElement.contains(ev.target)) hideDD(cityDD);
      if (!postalEl.parentElement.contains(ev.target)) hideDD(postalDD);
    });
  }

  // Forgot password toggle + submit
  if (forgotToggle && forgotForm) {
    forgotToggle.addEventListener('click', () => {
      forgotForm.classList.toggle('hidden');
      if (!forgotForm.classList.contains('hidden')) {
        // prefill with login email if present
        const loginEmail = document.getElementById('login-email');
        if (loginEmail && loginEmail.value && forgotEmail) {
          forgotEmail.value = loginEmail.value;
        }
        if (forgotEmail) forgotEmail.focus();
      }
    });
  }

  if (forgotSubmit) {
    forgotSubmit.addEventListener('click', async () => {
      const email = (forgotEmail && forgotEmail.value) ? forgotEmail.value.trim() : '';
      if (!email) { showMessage('Please enter your registered email.', 'error'); return; }
      try {
        forgotSubmit.disabled = true;
        forgotSubmit.textContent = 'Sending…';
        const res = await fetch(`${BACKEND_BASE}/forgot-password`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email })
        });
        const data = await res.json().catch(() => ({}));
        dbg.logReq('POST /forgot-password', { status: res.status, body: data });
        if (!res.ok) {
          const msg = (data && data.detail) ? (Array.isArray(data.detail) ? data.detail.map(d=>d.msg).join(' ') : data.detail) : 'Unable to process request.';
          showMessage(msg, 'error');
        } else {
          showMessage('If an account exists for this email, a password reset link has been sent. Please check your inbox.', 'info');
          // keep the form open but reset button state
        }
      } catch (e) {
        showMessage('Network error while requesting reset link.', 'error');
      } finally {
        forgotSubmit.disabled = false;
        forgotSubmit.textContent = 'Send reset link';
      }
    });
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
      try { localStorage.setItem('dh_access_token', token); } catch {}
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
    const firstname = document.getElementById('signup-firstname').value.trim();
    const lastname = document.getElementById('signup-lastname').value.trim();
    const email = document.getElementById('signup-email').value.trim();
    const password = document.getElementById('signup-password').value;
    const passwordConfirm = document.getElementById('signup-password-confirm').value;
    const street = document.getElementById('signup-street').value.trim();
    const number = document.getElementById('signup-number').value.trim();
    const postal = document.getElementById('signup-postal').value.trim();
    const city = document.getElementById('signup-city').value.trim();
    const gender = document.getElementById('signup-gender').value;
    const name = `${firstname} ${lastname}`.trim();
    // Client-side validations
    if (!firstname || !lastname) {
      showMessage('Please enter your first and last name.', 'error');
      return;
    }
    if (!email) {
      showMessage('Please enter a valid email address.', 'error');
      return;
    }
    if (!password || !passwordConfirm) {
      showMessage('Please enter and confirm your password.', 'error');
      return;
    }
    if (password !== passwordConfirm) {
      showMessage('Passwords do not match.', 'error');
      return;
    }
    if (password.length < 8) {
      showMessage('Password must be at least 8 characters.', 'error');
      return;
    }
    // Enforce password complexity: require lowercase, uppercase, and special character
    const missingReqs = [];
    if (!/[a-z]/.test(password)) missingReqs.push('a lowercase letter');
    if (!/[A-Z]/.test(password)) missingReqs.push('an uppercase letter');
    if (!/[^A-Za-z0-9]/.test(password)) missingReqs.push('a special character');
    if (missingReqs.length > 0) {
      // Build a readable list like "an uppercase letter, a lowercase letter and a special character"
      const prettyList = missingReqs.length === 1
        ? missingReqs[0]
        : missingReqs.length === 2
          ? missingReqs.join(' and ')
          : missingReqs.slice(0, -1).join(', ') + ' and ' + missingReqs.slice(-1);
      showMessage(`Password must include ${prettyList}.`, 'error');
      return;
    }
    if (!street || !number || !postal || !city) {
      showMessage('Please provide your full address: street, number, postal code, and city.', 'error');
      return;
    }
    if (!/^[0-9A-Za-z \-]{3,10}$/.test(postal)) {
      // light validation to avoid blocking legit formats; backend will accept any string
      showMessage('Please enter a valid postal code.', 'error');
      return;
    }
    if (!gender) {
      showMessage('Please select your gender.', 'error');
      return;
    }
    // Attach lat/lon from verification if available (optional)
    const payload = {
      email,
      password,
      password_confirm: passwordConfirm,
      first_name: firstname,
      last_name: lastname,
      street,
      street_no: number,
      postal_code: postal,
      city,
      gender,
      preferences: {},
    };
    if (typeof signupGeo.lat === 'number' && typeof signupGeo.lon === 'number') {
      payload.lat = signupGeo.lat;
      payload.lon = signupGeo.lon;
    }
    try {
      const res = await fetch(`${BACKEND_BASE}/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
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
  // Confirm password match hint
  (function setupConfirmHint(){
    const pwd = document.getElementById('signup-password');
    const conf = document.getElementById('signup-password-confirm');
    if (!pwd || !conf) return;
    const hint = document.createElement('div');
    hint.className = 'mt-1 text-xs';
    conf.parentElement.appendChild(hint);
    function update(){
      if (!conf.value) { hint.textContent=''; return; }
      if (pwd.value && conf.value === pwd.value) {
        hint.textContent = 'Passwords match';
        hint.style.color = '#059669'; // green
      } else {
        hint.textContent = 'Passwords do not match';
        hint.style.color = '#dc2626'; // red
      }
    }
    pwd.addEventListener('input', update);
    conf.addEventListener('input', update);
  })();
  // Initialize enhanced UI behaviors
  addPasswordToggle('login-password');
  addPasswordToggle('signup-password');
  addPasswordToggle('signup-password-confirm');
  attachStrengthMeter('signup-password');
  setupAddressVerification();
});

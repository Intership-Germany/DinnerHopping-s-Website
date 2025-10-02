// Debug banner utility: shows current frontend origin and backend URL in a small
// fixed banner at the bottom-left of the screen. Opt-in via config.js
// window.DEBUG_BANNER = true before loading this script. Opt-out via
// setting window.DEBUG_BANNER = false (overrides true).

(function () {
  try {
    if (typeof window !== 'undefined' && window.DEBUG_BANNER === false) {
      return; // opt-out via env flag
    }

    var origin = window.location.origin;
    var el = document.createElement('div');
    el.setAttribute('id', 'dh-debug-banner');
    el.style.position = 'fixed';
    el.style.bottom = '10px';
    el.style.left = '10px';
    el.style.zIndex = '9999';
    el.style.fontFamily = 'Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial';
    el.style.fontSize = '12px';
    el.style.padding = '6px 10px';
    el.style.borderRadius = '10px';
    el.style.background = 'rgba(23,42,58,0.9)';
    el.style.color = 'white';
    el.style.boxShadow = '0 4px 12px rgba(0,0,0,0.25)';
    el.style.backdropFilter = 'blur(4px)';
    el.style.opacity = '0.85';
    el.style.pointerEvents = 'none';
    el.style.lineHeight = '1.3';

    var backendStatus = 'pending'; // 'pending' | 'ok' | 'fail' | 'unknown'
    var lastLatencyMs = null;
    var profileFetched = false;
    var userRoles = null; // array or null
    var userEmail = null;
    var csrfPresent = false;
    var online = typeof navigator !== 'undefined' ? navigator.onLine : true;
    var collapsed = false;
    var secureCookies = false;
    var authMode = 'unknown'; // cookie | bearer-ls | bearer-dh | none | unknown
    var bearerSource = null; // 'localStorage' | 'dh_cookie'
    var lastProfileStatus = null; // HTTP status or error code string

    function detectCsrf() {
      try {
        var c = document.cookie || '';
        csrfPresent = /(?:^|; )(__Host-)?csrf_token=/.test(c);
      } catch {
        csrfPresent = false;
      }
    }
    function detectSecureCookies() {
      try {
        var c = document.cookie || '';
        secureCookies = /__Host-/.test(c); // __Host- prefix implies Secure + Path=/ + no Domain
      } catch {
        secureCookies = false;
      }
    }
    function detectAuthMode() {
      try {
        var c = document.cookie || '';
        var hasAccessCookie = /(?:^|; )(__Host-)?access_token=/.test(c);
        var hasRefreshCookie = /(?:^|; )(__Host-)?refresh_token=/.test(c);
        var dhCookieMatch = c.match(/(?:^|; )dh_token=([^;]+)/);
        var lsToken = null;
        try {
          lsToken = window.localStorage ? window.localStorage.getItem('dh_access_token') : null;
        } catch {}
        if (hasAccessCookie || hasRefreshCookie) {
          authMode = 'cookie';
          bearerSource = null;
          return;
        }
        if (lsToken) {
          authMode = 'bearer-ls';
          bearerSource = 'localStorage';
          return;
        }
        if (dhCookieMatch) {
          authMode = 'bearer-dh';
          bearerSource = 'dh_cookie';
          return;
        }
        authMode = 'none';
        bearerSource = null;
      } catch {
        authMode = 'unknown';
      }
    }
    detectAuthMode();
    detectCsrf();
    detectSecureCookies();

    window.addEventListener('online', function () {
      online = true;
      render();
    });
    window.addEventListener('offline', function () {
      online = false;
      render();
    });

    function dot(color, title) {
      return (
        '<span title="' +
        (title || '') +
        '" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' +
        color +
        ';margin-right:4px;box-shadow:0 0 2px rgba(0,0,0,.6);"></span>'
      );
    }

    function statusDot(type) {
      switch (type) {
        case 'ok':
          return dot('#22c55e', 'OK'); // green
        case 'fail':
          return dot('#ef4444', 'Fail'); // red
        case 'pending':
          return dot('#facc15', 'Pending'); // yellow
        default:
          return dot('#9ca3af', 'Unknown'); // gray
      }
    }

    function escapeHtml(str) {
      return (str || '').replace(/[&<>\"]/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
      });
    }

    function shortEmail(e) {
      if (!e) return '';
      if (e.length <= 22) return e;
      var parts = e.split('@');
      if (parts[0].length > 10) {
        parts[0] = parts[0].slice(0, 10) + '…';
      }
      return parts.join('@');
    }

    function render() {
      var current = typeof window !== 'undefined' ? window.BACKEND_BASE_URL : undefined;
      var backendDisplay = current === undefined ? 'undefined' : escapeHtml(current);
      var backendTitle;
      if (current === undefined) {
        backendTitle = 'Backend base URL is not defined yet';
        backendStatus = backendStatus === 'pending' ? 'unknown' : backendStatus;
      } else if (backendStatus === 'pending') {
        backendTitle = 'Pinging backend...';
      } else if (backendStatus === 'ok') {
        backendTitle = 'Backend reachable';
      } else if (backendStatus === 'fail') {
        backendTitle = 'Backend NOT reachable';
      } else {
        backendTitle = 'No status';
      }
      detectCsrf();
      detectSecureCookies();
      detectAuthMode();
      var roleStr = userRoles && userRoles.length ? userRoles.join(',') : 'anonymous';
      var roleDotColor =
        roleStr.indexOf('admin') !== -1
          ? '#6366f1'
          : roleStr === 'anonymous'
            ? '#9ca3af'
            : '#0ea5e9';
      var csrfColor = csrfPresent ? '#22c55e' : '#ef4444';
      var onlineColor = online ? '#22c55e' : '#ef4444';
      var secureColor = secureCookies ? '#22c55e' : '#f59e0b';
      var latencyPart = lastLatencyMs != null ? ' ' + lastLatencyMs + 'ms' : '';
      var emailDisplay = shortEmail(userEmail || '');
      var authColorMap = {
        cookie: '#22c55e',
        'bearer-ls': '#0ea5e9',
        'bearer-dh': '#f97316',
        none: '#ef4444',
        unknown: '#9ca3af',
      };
      var authColor = authColorMap[authMode] || '#9ca3af';
      var authLabel = (function (m) {
        switch (m) {
          case 'cookie':
            return 'cookie';
          case 'bearer-ls':
            return 'bearer(ls)';
          case 'bearer-dh':
            return 'bearer(dh_token)';
          case 'none':
            return 'none';
          default:
            return 'unknown';
        }
      })(authMode);

      if (collapsed) {
        el.innerHTML =
          '<div style="display:flex;align-items:center;gap:4px;pointer-events:auto;">' +
          '<button data-act="toggle" style="background:transparent;border:none;color:#fff;font-size:11px;cursor:pointer;padding:0 4px;">▸</button>' +
          statusDot(backendStatus) +
          dot(onlineColor, 'Online status') +
          dot(csrfColor, 'CSRF ' + (csrfPresent ? 'present' : 'absent')) +
          dot(authColor, 'Auth mode: ' + authLabel) +
          '</div>';
        wireEvents();
        return;
      }

      el.innerHTML =
        '' +
        '<div style="display:flex;flex-direction:column;gap:2px;pointer-events:auto;">' +
        '<div style="display:flex;align-items:center;gap:6px;">' +
        '<button data-act="toggle" style="background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.25);color:#fff;font-size:11px;cursor:pointer;border-radius:4px;padding:0 4px;line-height:16px;">▾</button>' +
        dot('#22c55e', 'Frontend origin') +
        '<span>FE: ' +
        escapeHtml(origin) +
        '</span>' +
        '</div>' +
        '<div style="white-space:nowrap;">' +
        statusDot(backendStatus) +
        '<span title="' +
        backendTitle +
        '">BE: ' +
        backendDisplay +
        latencyPart +
        '</span></div>' +
        '<div style="white-space:nowrap;">' +
        dot(roleDotColor, 'Roles') +
        'User: ' +
        escapeHtml(roleStr) +
        (emailDisplay ? ' (' + escapeHtml(emailDisplay) + ')' : '') +
        '</div>' +
        '<div style="white-space:nowrap;">' +
        dot(csrfColor, 'CSRF token ' + (csrfPresent ? 'present' : 'absent')) +
        'CSRF: ' +
        (csrfPresent ? 'present' : 'absent') +
        '</div>' +
        '<div style="white-space:nowrap;">' +
        dot(onlineColor, 'Navigator online state') +
        'Net: ' +
        (online ? 'online' : 'offline') +
        '</div>' +
        '<div style="white-space:nowrap;">' +
        dot(secureColor, 'Secure cookie heuristic (__Host-)') +
        'Cookies: ' +
        (secureCookies ? 'secure' : 'insecure/dev') +
        '</div>' +
        '<div style="white-space:nowrap;">' +
        dot(authColor, 'Auth mode') +
        'Auth: ' +
        authLabel +
        '</div>' +
        '<div style="margin-top:4px;display:flex;gap:6px;flex-wrap:wrap;">' +
        '<button data-act="snapshot" style="background:#334155;border:1px solid #475569;color:#fff;font-size:11px;cursor:pointer;border-radius:4px;padding:2px 6px;">Snapshot</button>' +
        '<button data-act="refresh-prof" style="background:#334155;border:1px solid #475569;color:#fff;font-size:11px;cursor:pointer;border-radius:4px;padding:2px 6px;">↻ Profile</button>' +
        '</div>' +
        '</div>';
      wireEvents();
    }

    function pingBackend() {
      var base = window.BACKEND_BASE_URL;
      if (!base) {
        render();
        return;
      }
      backendStatus = 'pending';
      render();
      var controller = new AbortController();
      var to = setTimeout(function () {
        controller.abort();
      }, 4000);
      // Use openapi.json as a lightweight endpoint that should exist
      var t0 = performance.now();
      fetch(base.replace(/\/$/, '') + '/openapi.json', {
        method: 'GET',
        mode: 'cors',
        cache: 'no-store',
        signal: controller.signal,
      })
        .then(function (r) {
          lastLatencyMs = Math.round(performance.now() - t0);
          backendStatus = r.ok ? 'ok' : 'fail';
          if (r.ok && !profileFetched) {
            fetchProfile();
          }
        })
        .catch(function () {
          backendStatus = 'fail';
          lastLatencyMs = null;
        })
        .finally(function () {
          clearTimeout(to);
          render();
        });
    }

    function fetchProfile() {
      var base = window.BACKEND_BASE_URL;
      if (!base) return;
      profileFetched = true; // avoid repeated attempts; we can expose manual refresh
      var url = base.replace(/\/$/, '') + '/profile';
      var lsBearer = null;
      try {
        lsBearer = (window.localStorage && window.localStorage.getItem('dh_access_token')) || null;
      } catch {}
      var dhBearer = null;
      var m = document.cookie.match(/(?:^|; )dh_token=([^;]+)/);
      if (m) dhBearer = decodeURIComponent(m[1]);

      var triedCookie = false;
      var triedBearer = false;

      function doBearer(token) {
        if (!token) return Promise.resolve(null);
        triedBearer = true;
        return fetch(url, {
          method: 'GET',
          credentials: 'omit',
          headers: { Accept: 'application/json', Authorization: 'Bearer ' + token },
        })
          .then(function (r) {
            lastProfileStatus = r.status;
            return r;
          })
          .catch(function (e) {
            lastProfileStatus = 'bearer_err';
            return null;
          });
      }
      function doCookie() {
        triedCookie = true;
        return fetch(url, {
          method: 'GET',
          credentials: 'include',
          headers: { Accept: 'application/json' },
        })
          .then(function (r) {
            lastProfileStatus = r.status;
            return r;
          })
          .catch(function (e) {
            lastProfileStatus = 'cookie_err';
            return null;
          });
      }

      var startWithBearer = authMode === 'bearer-dh' || authMode === 'bearer-ls';
      var bearerFirst = lsBearer || dhBearer;
      var chain;
      if (startWithBearer && bearerFirst) {
        chain = doBearer(bearerFirst).then(function (r) {
          if (!r || r.status === 401 || r.status === 419) {
            // fallback to cookie if possible
            return doCookie();
          }
          return r;
        });
      } else {
        chain = doCookie().then(function (r) {
          if (!r || r.status === 401 || r.status === 419) {
            return doBearer(lsBearer || dhBearer || null);
          }
          return r;
        });
      }

      chain
        .then(function (r) {
          return r && r.ok ? r.json() : null;
        })
        .then(function (data) {
          if (data) {
            if (Array.isArray(data.roles)) userRoles = data.roles.slice();
            if (data.email) userEmail = data.email;
          }
        })
        .catch(function () {
          /* ignore */
        })
        .finally(function () {
          detectAuthMode();
          render();
        });
    }

    function manualRefreshProfile() {
      profileFetched = false; // allow new fetch
      fetchProfile();
    }

    function snapshot() {
      var snap = {
        ts: new Date().toISOString(),
        origin: origin,
        backend_url: window.BACKEND_BASE_URL,
        backend_status: backendStatus,
        latency_ms: lastLatencyMs,
        roles: userRoles,
        email: userEmail,
        csrf_present: csrfPresent,
        online: online,
        secure_cookies: secureCookies,
        auth_mode: authMode,
        last_profile_status: lastProfileStatus,
        collapsed: collapsed,
      };
      try {
        navigator.clipboard.writeText(JSON.stringify(snap, null, 2));
        // brief visual feedback by flashing border
        el.style.boxShadow = '0 0 0 2px #10b981, 0 4px 12px rgba(0,0,0,0.25)';
        setTimeout(function () {
          el.style.boxShadow = '0 4px 12px rgba(0,0,0,0.25)';
        }, 600);
      } catch (e) {
        console.warn('snapshot copy failed', e);
      }
    }

    function wireEvents() {
      // Only need to attach once per render cycle
      var btnToggle = el.querySelector('[data-act="toggle"]');
      if (btnToggle) {
        btnToggle.onclick = function (ev) {
          ev.stopPropagation();
          collapsed = !collapsed;
          render();
        };
      }
      var btnSnap = el.querySelector('[data-act="snapshot"]');
      if (btnSnap) {
        btnSnap.onclick = function (ev) {
          ev.stopPropagation();
          snapshot();
        };
      }
      var btnRef = el.querySelector('[data-act="refresh-prof"]');
      if (btnRef) {
        btnRef.onclick = function (ev) {
          ev.stopPropagation();
          manualRefreshProfile();
        };
      }
    }

    render();

    document.addEventListener('DOMContentLoaded', function () {
      document.body.appendChild(el);
      // Retry resolving BACKEND_BASE_URL if not yet defined
      if (window.BACKEND_BASE_URL === undefined) {
        var attempts = 0;
        var maxAttempts = 10;
        var iv = setInterval(function () {
          attempts++;
          if (window.BACKEND_BASE_URL !== undefined) {
            pingBackend();
            clearInterval(iv);
          } else if (attempts >= maxAttempts) {
            console.warn(
              '[debug-banner] BACKEND_BASE_URL still undefined after',
              attempts,
              'attempts'
            );
            backendStatus = 'unknown';
            render();
            clearInterval(iv);
          }
        }, 250);
      } else {
        pingBackend();
      }

      // Re-ping every 5s to update status (lightweight)
      setInterval(function () {
        if (window.BACKEND_BASE_URL) {
          pingBackend();
        }
      }, 5000);
    });
  } catch (e) {
    console.error('debug-banner error', e);
  }
})();

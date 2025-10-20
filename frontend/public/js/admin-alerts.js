// Simple admin alerts UI helper
// Usage: include this script in admin-dashboard.html and call initAdminAlerts()

const JSON_MIME = 'application/json';

function getDialog(){
  return (window.dh && window.dh.dialog) || null;
}

function showDialogAlert(message, options){
  const dlg = getDialog();
  if (dlg && typeof dlg.alert === 'function'){
    return dlg.alert(message, Object.assign({ tone: 'error', title: 'Action error' }, options || {}));
  }
  window.alert(message);
  return Promise.resolve();
}

function mergeRequestOptions(opts) {
  const base = Object.assign({ headers: {} }, opts || {});
  base.headers = Object.assign({ Accept: JSON_MIME }, base.headers || {});
  if (
    base.body &&
    typeof base.body !== 'string' &&
    !(base.body instanceof FormData) &&
    !(base.body instanceof URLSearchParams)
  ) {
    if (!base.headers['Content-Type']) base.headers['Content-Type'] = JSON_MIME;
    if (base.headers['Content-Type'] === JSON_MIME) {
      base.body = JSON.stringify(base.body);
    }
  }
  return base;
}

const fallbackFetch = (path, opts = {}) => {
  const merged = mergeRequestOptions(Object.assign({ credentials: 'include' }, opts));
  return fetch(path, merged);
};

function getApiFetch() {
  const candidate = (window.dh && window.dh.apiFetch) || window.apiFetch;
  if (typeof candidate === 'function') return candidate;
  return fallbackFetch;
}

async function requestJson(path, opts, contextLabel = 'Request') {
  const res = await getApiFetch()(path, mergeRequestOptions(opts));
  if (!res || typeof res.ok !== 'boolean') {
    throw new Error(`${contextLabel} returned an unexpected response`);
  }
  if (!res.ok) {
    let detail = '';
    try {
      detail = await res.text();
    } catch (err) {
      // Swallow body parsing issues; status code is sufficient context.
    }
    const statusText = res.statusText || 'unknown status';
    throw new Error(
      `${contextLabel} failed (${res.status} ${statusText})${detail ? `: ${detail}` : ''}`
    );
  }
  try {
    return await res.clone().json();
  } catch {
    return null;
  }
}

function formatCurrency(amountCents, currencyCode = 'EUR') {
  if (typeof amountCents !== 'number') return '-';
  const amount = amountCents / 100;
  try {
    const code = (currencyCode || 'EUR').toUpperCase();
    return new Intl.NumberFormat(undefined, { style: 'currency', currency: code }).format(
      amount
    );
  } catch {
    const suffix = currencyCode && currencyCode.toUpperCase() !== 'EUR' ? ` ${currencyCode.toUpperCase()}` : ' €';
    return `${amount.toFixed(2)}${suffix}`;
  }
}

function createMeta(label, value) {
  const span = document.createElement('span');
  span.className = 'me-3';
  span.textContent = `${label}: ${value ?? '-'}`;
  return span;
}

function createActionButton(label, action, { errorPrefix = `${label} failed:` } = {}) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn btn-sm btn-secondary me-2';
  btn.textContent = label;
  btn.addEventListener('click', async () => {
    if (btn.disabled) return;
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = `${label}…`;
    try {
      await action();
    } catch (err) {
      await showDialogAlert(`${errorPrefix} ${err?.message || 'unknown error'}`);
      btn.disabled = false;
      btn.textContent = original;
    }
  });
  return btn;
}

function renderAlert(container, alertItem) {
  const wrapper = document.createElement('article');
  wrapper.className = 'admin-alert p-2 border mb-2';

  const heading = document.createElement('div');
  heading.className = 'fw-bold';
  const status = alertItem.status || 'open';
  const headingTitle = alertItem.event_title || alertItem.type || 'Alert';
  heading.textContent = `${headingTitle} — ${formatCurrency(
    alertItem.amount_cents,
    alertItem.currency
  )} — ${status}`;
  wrapper.appendChild(heading);

  const meta = document.createElement('div');
  meta.className = 'small text-muted mt-1';
  meta.appendChild(createMeta('Payment', alertItem.payment_id));
  meta.appendChild(createMeta('Registration', alertItem.registration_id));
  meta.appendChild(createMeta('User', alertItem.user_email));
  if (alertItem.user_name) meta.appendChild(createMeta('Name', alertItem.user_name));
  if (alertItem.team_size !== undefined && alertItem.team_size !== null)
    meta.appendChild(createMeta('Team size', alertItem.team_size));
  if (alertItem.event_id) meta.appendChild(createMeta('Event id', alertItem.event_id));
  wrapper.appendChild(meta);
  if (alertItem.user_message) {
    const msgWrap = document.createElement('div');
    msgWrap.className = 'mt-2 p-2 bg-white border rounded text-sm';
    const pre = document.createElement('pre');
    pre.style.whiteSpace = 'pre-wrap';
    pre.style.margin = '0';
    pre.textContent = alertItem.user_message;
    msgWrap.appendChild(pre);
    wrapper.appendChild(msgWrap);
  }

  const actions = document.createElement('div');
  actions.className = 'mt-2';
  const removeAlert = () => {
    wrapper.remove();
    if (!container.querySelector('.admin-alert')) {
      container.textContent = 'No alerts';
    }
  };

  if (alertItem.payment_id) {
    actions.appendChild(
      createActionButton('Confirm payment', async () => {
        await requestJson(
          `/admin/alerts/${alertItem.id}/confirm_payment`,
          { method: 'POST' },
          'Confirm payment'
        );
        removeAlert();
      })
    );
  }

  actions.appendChild(
    createActionButton(
      'Close',
      async () => {
        await requestJson(
          `/admin/alerts/${alertItem.id}/close`,
          { method: 'POST' },
          'Close alert'
        );
        removeAlert();
      },
      { errorPrefix: 'Close failed:' }
    )
  );

  wrapper.appendChild(actions);
  return wrapper;
}

async function initAdminAlerts(containerId = 'admin-alerts') {
  const container = document.getElementById(containerId);
  if (!container) return;

  container.textContent = 'Loading alerts…';
  try {
    const alerts = (await requestJson('/admin/alerts', { method: 'GET' }, 'Load alerts')) || [];
    if (!alerts.length) {
      container.textContent = 'No alerts';
      return;
    }

    container.innerHTML = '';
    alerts.forEach((alertItem) => {
      container.appendChild(renderAlert(container, alertItem));
    });
  } catch (err) {
    container.textContent = 'Error loading alerts';
    console.error(err);
  }
}

if (typeof window !== 'undefined') {
  window.initAdminAlerts = initAdminAlerts;
}

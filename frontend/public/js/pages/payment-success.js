// Payment success landing page: show status and optionally redirect to a safe path
(function(){
  document.addEventListener('DOMContentLoaded', async () => {
    const params = new URLSearchParams(window.location.search);
  const status = params.get('status');
  const paymentId = params.get('payment_id');
  const token = params.get('token'); // PayPal token for approve/cancel
    const nextParam = params.get('next') || params.get('redirect');
    const autoRedirect = params.get('redirect') !== '0';

    const successState = document.getElementById('success-state');
    const failedState = document.getElementById('failed-state');
    const cancelledState = document.getElementById('cancelled-state');
    const goDashboard = document.getElementById('go-dashboard');

    if (status === 'failed') {
      successState && successState.classList.add('hidden');
      failedState && failedState.classList.remove('hidden');
    } else if (status === 'cancelled') {
      successState && successState.classList.add('hidden');
      cancelledState && cancelledState.classList.remove('hidden');
    }

    // If PayPal redirected to the frontend with a token and payment_id, forward
    // the token to the backend so it can complete or cancel the payment.
    // This covers the case where PayPal uses the frontend domain for redirects.
    async function handlePayPalToken(){
      if(!token || !paymentId) return;

      // best-effort detection: if the current path contains '/cancel' assume it's a cancel
      const pathLooksLikeCancel = window.location.pathname.includes('/cancel');
      const backendBase = window.BACKEND_BASE_URL || '';

      try{
        if(pathLooksLikeCancel || status === 'cancelled'){
          // call backend cancel endpoint (it will mark payment failed)
          await fetch(`${backendBase}/payments/${encodeURIComponent(paymentId)}/cancel?token=${encodeURIComponent(token)}`, { method: 'GET', credentials: 'include' });
          // normalize URL to show cancelled status
          const newUrl = `/payement?payment_id=${encodeURIComponent(paymentId)}&status=cancelled`;
          history.replaceState({}, '', newUrl);
          successState && successState.classList.add('hidden');
          cancelledState && cancelledState.classList.remove('hidden');
        } else {
          // attempt to call backend PayPal return handler which will capture the order
          // backend will typically redirect to /payement; here we call it
          // and then show a success/failed UI depending on result
          const resp = await fetch(`${backendBase}/payments/paypal/return?payment_id=${encodeURIComponent(paymentId)}&token=${encodeURIComponent(token)}`, { method: 'GET', credentials: 'include', redirect: 'follow' });
          // If backend redirected to /payement with a status param, read final URL
          const finalUrl = resp && resp.url ? new URL(resp.url) : null;
          if(finalUrl && finalUrl.searchParams.get('status')){
            const finalStatus = finalUrl.searchParams.get('status');
            history.replaceState({}, '', `/payement?payment_id=${encodeURIComponent(paymentId)}&status=${encodeURIComponent(finalStatus)}`);
            if(finalStatus === 'failed'){
              successState && successState.classList.add('hidden');
              failedState && failedState.classList.remove('hidden');
            } else if(finalStatus === 'cancelled'){
              successState && successState.classList.add('hidden');
              cancelledState && cancelledState.classList.remove('hidden');
            } else {
              // default to success
              successState && successState.classList.remove('hidden');
              failedState && failedState.classList.add('hidden');
              cancelledState && cancelledState.classList.add('hidden');
            }
          } else {
            // no redirect info — assume success and show page
            history.replaceState({}, '', `/payement?payment_id=${encodeURIComponent(paymentId)}`);
            successState && successState.classList.remove('hidden');
            failedState && failedState.classList.add('hidden');
            cancelledState && cancelledState.classList.add('hidden');
          }
        }
      }catch(e){
        // network/backend error — show failed state conservatively
  history.replaceState({}, '', `/payement?payment_id=${encodeURIComponent(paymentId)}&status=failed`);
        successState && successState.classList.add('hidden');
        failedState && failedState.classList.remove('hidden');
      }
    }

    // If there's a PayPal token present and no explicit status set, handle it.
    if(token && !status){
      // don't block the rest of initialization — run in background
      handlePayPalToken();
    }

    function isSafe(n){
      if(!n) return false;
      if(n.includes('://')) return false;
      if(!n.startsWith('/')) return false;
      if(n.indexOf('//',1) !== -1) return false;
      if(/\r|\n/.test(n)) return false;
      return true;
    }

    let target = null;
    if(nextParam && isSafe(nextParam)) target = nextParam;

    // populate dashboard link with latest registration (best-effort)
    try{
      const res = await (window.apiFetch ? window.apiFetch('/events/my', { method: 'GET' }) : fetch((window.BACKEND_BASE_URL||'') + '/events/my'));
      if(res && res.ok){
        const data = await res.json();
        const latest = Array.isArray(data) && data.length ? data[0] : null;
        if(latest && latest.id && goDashboard){
          const encodedId = encodeURIComponent(latest.id);
          goDashboard.href = `event.html?id=${encodedId}`;
          if(!target) target = `event.html?id=${encodedId}`;
        }
      }
    }catch{}

    if(target && autoRedirect){
      // show a small countdown element if present, otherwise redirect after short delay
      let seconds = 6;
      const countdownEl = document.getElementById('ps-countdown');
      if(countdownEl) countdownEl.hidden = false;
      const tick = setInterval(()=>{
        seconds -= 1;
        if(countdownEl) countdownEl.textContent = String(seconds);
        if(seconds <= 0){
          clearInterval(tick);
          window.location.href = target;
        }
      }, 1000);
      const goNow = document.getElementById('ps-go-now');
      if(goNow) goNow.addEventListener('click', (e)=>{ e.preventDefault(); clearInterval(tick); window.location.href = target; });
    }
  });
})();

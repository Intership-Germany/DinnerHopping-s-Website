// Payment success landing page: show status and optionally redirect to a safe path
(function(){
  document.addEventListener('DOMContentLoaded', async () => {
    const params = new URLSearchParams(window.location.search);
    const status = params.get('status');
    const paymentId = params.get('payment_id');
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
          goDashboard.href = `/event?id=${encodeURIComponent(latest.id)}`;
          if(!target) target = `/event?id=${encodeURIComponent(latest.id)}`;
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

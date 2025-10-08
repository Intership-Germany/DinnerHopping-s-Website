// Email verification page script
// Reads token from query string and calls backend /verify-email endpoint.
(function(){
  const VE_MSG = document.getElementById('ve-msg');
  const VE_SPINNER = document.getElementById('ve-spinner');
  const VE_ACTIONS = document.getElementById('ve-actions');

  async function setMessage(text, kind) {
    if(!VE_MSG) return;
    VE_MSG.textContent = text;
    VE_MSG.classList.remove('text-green-600','text-red-600','text-gray-600');
    if(kind === 'success') VE_MSG.classList.add('text-green-600');
    else if(kind === 'error') VE_MSG.classList.add('text-red-600');
    else VE_MSG.classList.add('text-gray-600');
  }

  function hideSpinner(){ if(VE_SPINNER) VE_SPINNER.hidden = true; }
  function showActions(){ if(VE_ACTIONS) VE_ACTIONS.hidden = false; }

  async function verify(){
    const params = new URLSearchParams(window.location.search);
    const token = params.get('token');
    if(!token){
      hideSpinner();
      await setMessage('Missing verification token.', 'error');
      showActions();
      return;
    }
    try{
      const backend = (window.BACKEND_BASE_URL || window.location.origin).replace(/\/$/, '');
      const res = await fetch(backend + '/verify-email?token=' + encodeURIComponent(token), { credentials: 'include' });
      const data = await res.json().catch(()=>({}));
      hideSpinner();
      showActions();
      if(res.ok && data && data.status === 'verified'){
        await setMessage('Your email has been verified successfully! You can now log in.', 'success');
      } else {
        const detail = data && data.detail ? data.detail : 'Verification failed or token invalid.';
        await setMessage(detail, 'error');
      }
    }catch(e){
      hideSpinner();
      showActions();
      await setMessage('Network error while verifying. Please try again later.', 'error');
    }
  }

  // Wait for DOM and includes to load. includes.js will replace data-include and then dispatch 'includes.ready'.
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    // small defer to allow includes to run if present
    setTimeout(verify, 50);
  } else {
    document.addEventListener('DOMContentLoaded', verify);
  }
})();
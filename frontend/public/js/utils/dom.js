(function(){
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {};
  const utils = window.dh.utils = window.dh.utils || {};

  function tpl(id){
    const t = document.getElementById(id);
    return t && 'content' in t ? t.content.firstElementChild : null;
  }
  function cloneTpl(id){
    const node = tpl(id);
    return node ? node.cloneNode(true) : null;
  }
  function formatFeeCents(cents){
    if (typeof cents !== 'number') return '';
    if (cents <= 0) return 'Free';
    return (cents / 100).toFixed(2) + ' \u20ac';
  }
  /** Generic modal from <template>. Adds close on overlay click & .modal-close buttons */
  function openModalFromTemplate(templateId, { onClose } = {}) {
    const t = document.getElementById(templateId);
    if (!t) return null;
    const frag = t.content.cloneNode(true);
    // assume first element is overlay wrapper
    const overlay = frag.querySelector('.fixed, .dh-modal') || frag.firstElementChild;
    if (!overlay) return null;
    function close(){ overlay.remove(); if (onClose) try { onClose(); } catch {} }
    overlay.addEventListener('click', (e)=>{ if (e.target === overlay) close(); });
    overlay.querySelectorAll('.modal-close, .provider-close, .reg-close').forEach(btn=>{
      btn.addEventListener('click', close);
    });
    document.body.appendChild(overlay);
    return overlay;
  }

  utils.tpl = utils.template = tpl;
  utils.cloneTpl = cloneTpl;
  utils.openModalFromTemplate = openModalFromTemplate;
  utils.formatFeeCents = formatFeeCents;
})();

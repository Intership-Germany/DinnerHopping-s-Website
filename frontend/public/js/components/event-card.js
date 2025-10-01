(function(){
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {};
  const ns = window.dh.components = window.dh.components || {};

  /**
   * Renders event cards.
   * @param {Object} opts
   * @param {HTMLElement} opts.container Target container
   * @param {Array} opts.events Events array
   * @param {string|null} [opts.userZip] Current user postal code
   * @param {Function} opts.cloneTpl Template cloner (id)=>Node
   * @param {Function} opts.formatFeeCents Fee formatter (cents)=>string
   * @param {Function} opts.onRegister Callback when user clicks register ({event,eventId,spotsEl,ctaEl,placeLeft})
   */
  function renderEventCards(opts){
    const { container, events, userZip, cloneTpl, formatFeeCents, onRegister } = opts || {};
    if (!container) return;
    container.innerHTML = '';
    if (!Array.isArray(events) || events.length === 0){
      // Let caller decide to show empty state; keep generic here.
      return;
    }
    events.forEach(e => {
      const node = cloneTpl ? cloneTpl('tpl-event-card') : null;
      if (!node) return;
      const titleEl = node.querySelector('.event-title');
      const dateWrapEl = node.querySelector('.event-date');
      const dateTextEl = node.querySelector('.event-date-text') || dateWrapEl;
      const feeBadgeEl = node.querySelector('.event-fee-badge');
      const feeTextEl = node.querySelector('.event-fee-text') || feeBadgeEl;
      const descEl = node.querySelector('.event-desc');
      const spotsEl = node.querySelector('.event-spots');
      const ctaEl = node.querySelector('.event-cta');
      const zipBadgeEl = node.querySelector('.event-zip-badge');
      if (titleEl) titleEl.textContent = e.title || e.name || 'Untitled Event';
      const d = e.registration_deadline ? new Date(e.registration_deadline) : null;
      const dateStr = d && !isNaN(d) ? d.toLocaleDateString(undefined,{year:'numeric',month:'short',day:'numeric'}) : 'N/A';
      if (dateTextEl) dateTextEl.textContent = `Registration deadline · ${dateStr}`;
      const fee = formatFeeCents ? formatFeeCents(typeof e.fee_cents === 'number' ? e.fee_cents : 0) : '';
      if (feeTextEl) feeTextEl.textContent = fee ? `Fee · ${fee}` : 'Free';
      if (descEl){
        const desc = e.description || e.summary || '';
        descEl.textContent = desc;
        if (!desc) descEl.classList.add('hidden');
      }
      const capacity = e.capacity && Number.isInteger(e.capacity) && e.capacity > 0 ? e.capacity : 6;
      const placeLeft = capacity - (Number(e.attendee_count) || 0);
      if (spotsEl){
        if (placeLeft <= 0){
          spotsEl.textContent = 'Event Full';
          spotsEl.className = 'event-spots text-sm font-semibold text-red-600';
        } else if (placeLeft === 1){
          spotsEl.textContent = 'Last spot!';
          spotsEl.className = 'event-spots text-sm font-semibold text-red-600';
        } else {
          spotsEl.textContent = `${placeLeft} spots left`;
          spotsEl.className = 'event-spots text-sm font-semibold text-green-600';
        }
      }
      try {
        if (zipBadgeEl && userZip && Array.isArray(e.valid_zip_codes) && e.valid_zip_codes.length>0 && !e.valid_zip_codes.includes(userZip)) {
          zipBadgeEl.classList.remove('hidden');
        }
      } catch {}
      const eventId = e.id || e._id || e.eventId || (e.event && (e.event.id || e.event._id));
      if (ctaEl){
        ctaEl.href = '#';
        if (placeLeft <= 0){
          ctaEl.classList.add('opacity-60','cursor-not-allowed');
          ctaEl.setAttribute('aria-disabled','true');
          ctaEl.tabIndex = -1;
        }
        ctaEl.addEventListener('click', ev => {
          ev.preventDefault();
            if (!eventId || ctaEl.getAttribute('aria-disabled')==='true') return;
            if (typeof onRegister === 'function') onRegister({ event: e, eventId, spotsEl, ctaEl, placeLeft });
        });
      }
      container.appendChild(node);
    });
  }

  ns.renderEventCards = renderEventCards;
})();

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
      // Description with multi-line clamp (CSS) + expandable smooth animation
      if (descEl){
        const fullDesc = (e.description || e.summary || '').trim();
        if (!fullDesc) {
          descEl.classList.add('hidden');
        } else {
          descEl.textContent = fullDesc;
          // Apply clamp class initially (3 lines)
          descEl.classList.add('dh-line-clamp-3');
          const cardRoot = node.closest('.dh-event-card');
          // Create wrapper for height animation
          const wrapper = document.createElement('div');
          wrapper.className = 'event-desc-wrapper overflow-hidden';
          descEl.parentNode.insertBefore(wrapper, descEl);
          wrapper.appendChild(descEl);
          // Compute collapsed height (clamped)
          function measureHeight(){ return wrapper.scrollHeight; }
          // Add toggle only if content actually overflows (heuristic: more than ~220 chars or has newline)
          const needsToggle = fullDesc.length > 220 || /\n/.test(fullDesc);
          if (needsToggle){
            const toggleBtn = document.createElement('button');
            toggleBtn.type = 'button';
            toggleBtn.className = 'event-more ml-1 text-xs font-medium text-[#008080] underline';
            toggleBtn.textContent = 'See more';
            wrapper.appendChild(toggleBtn);
            // First frame: set explicit collapsed height
            requestAnimationFrame(()=>{ wrapper.style.maxHeight = measureHeight() + 'px'; });
            let expanded = false;
            toggleBtn.addEventListener('click', () => {
              expanded = !expanded;
              // Remove clamp before measuring expanded height
              if (expanded){
                descEl.classList.remove('dh-line-clamp-3');
              } else {
                descEl.classList.add('dh-line-clamp-3');
              }
              requestAnimationFrame(()=>{
                const target = measureHeight();
                wrapper.classList.add('transition-all','duration-300');
                wrapper.style.maxHeight = target + 'px';
              });
              if (cardRoot){
                if (expanded){
                  cardRoot.classList.add('md:col-span-2');
                  cardRoot.classList.add('col-span-1');
                } else {
                  cardRoot.classList.remove('md:col-span-2');
                }
              }
              toggleBtn.textContent = expanded ? 'See less' : 'See more';
            });
          }
        }
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
      // Refund eligibility badge
      const metaWrap = node.querySelector('.event-meta');
      const refundEligible = !!e.refund_on_cancellation && (e.fee_cents || 0) > 0;
      if (refundEligible && metaWrap){
        const refundSpan = document.createElement('span');
        refundSpan.className = 'inline-flex items-center gap-1 px-2 py-1 text-xs font-medium rounded-full bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200';
        refundSpan.innerHTML = '<svg viewBox="0 0 20 20" fill="currentColor" class="w-3.5 h-3.5"><path d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414L9 13.414l4.707-4.707z"/></svg><span>Refundable</span>';
        metaWrap.appendChild(refundSpan);
      }
      // Capacity usage bar (only if event has explicit capacity >0 to avoid misleading defaults)
      if (e.capacity && Number.isInteger(e.capacity) && e.capacity > 0){
        const pct = Math.min(100, Math.max(0, ((Number(e.attendee_count)||0) / e.capacity) * 100));
        const bar = document.createElement('div');
        bar.className = 'w-full h-2 bg-gray-100 rounded overflow-hidden mt-3 relative';
        bar.setAttribute('role','progressbar');
        bar.setAttribute('aria-valuemin','0');
        bar.setAttribute('aria-valuemax', String(e.capacity));
        bar.setAttribute('aria-valuenow', String(Number(e.attendee_count)||0));
        const fill = document.createElement('div');
        fill.className = 'h-full bg-[#008080] transition-all';
        fill.style.width = pct + '%';
        bar.appendChild(fill);
        // Optional label
        const label = document.createElement('div');
        label.className = 'mt-1 text-[11px] text-gray-500';
        label.textContent = `${Number(e.attendee_count)||0}/${e.capacity} registered`;
        // Insert after description wrapper or at end of left column
        const left = node.querySelector('.event-left') || node;
        left.appendChild(bar);
        left.appendChild(label);
      }
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

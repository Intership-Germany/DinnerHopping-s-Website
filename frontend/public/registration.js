// Minimal UI wiring for listing active events and registering (solo/team)
(function(){
  const BASE = window.BACKEND_BASE_URL; // fallback removed

  async function fetchActiveEvents(){
    const res = await fetch(`${BASE}/registrations/events/active`, { credentials: 'include' });
    if (!res.ok) throw new Error('Failed to load events');
    return await res.json();
  }

  function el(tag, attrs, ...children){
    const n = document.createElement(tag);
    if (attrs){
      Object.entries(attrs).forEach(([k,v])=>{
        if (k === 'class') n.className = v; else if (k.startsWith('on') && typeof v==='function') n.addEventListener(k.slice(2), v); else n.setAttribute(k, v);
      });
    }
    children.flat().forEach(ch => {
      if (ch == null) return;
      n.appendChild(typeof ch === 'string' ? document.createTextNode(ch) : ch);
    });
    return n;
  }

  async function init(){
    const list = document.getElementById('events-list');
    if (!list) return;
    try {
      const events = await fetchActiveEvents();
      if (!events.length){
        list.appendChild(el('p', {class:'text-gray-600'}, 'No active events right now.'))
        return;
      }
      events.forEach(ev => {
        const row = el('div', {class:'p-3 border rounded mb-2 flex items-center justify-between'},
          el('div', null, el('div', {class:'font-semibold'}, ev.title || 'Event'), el('div', {class:'text-xs text-gray-500'}, (ev.date || ev.start_at || ''))),
          el('div', {class:'space-x-2'},
            el('button', {class:'px-3 py-1 bg-emerald-600 text-white rounded', onclick: ()=> startSolo(ev.id)}, 'Register Solo'),
            el('button', {class:'px-3 py-1 bg-indigo-600 text-white rounded', onclick: ()=> startTeam(ev.id)}, 'Register Team')
          )
        );
        list.appendChild(row);
      })
    } catch (e){
      list.appendChild(el('p', {class:'text-red-600'}, 'Failed to load events.'))
    }
  }

  async function startSolo(eventId){
    const payload = { event_id: eventId };
    const res = await fetch(`${BASE}/registrations/solo`, { method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
    const data = await res.json();
    if (!res.ok){ alert(data.detail || 'Failed to register'); return; }
    // Create payment using default provider
    const payRes = await fetch(`${BASE}/payments/create`, { method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ registration_id: data.registration_id, amount_cents: data.amount_cents }) });
    const pay = await payRes.json();
    if (pay.payment_link) window.location.href = pay.payment_link;
    else alert('Payment created. Please follow provider instructions.');
  }

  async function startTeam(eventId){
    const cooking_location = 'creator';
    const payload = { event_id: eventId, cooking_location };
    const res = await fetch(`${BASE}/registrations/team`, { method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
    const data = await res.json();
    if (!res.ok){ alert(data.detail || 'Failed to register team'); return; }
    const payRes = await fetch(`${BASE}/payments/create`, { method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ registration_id: data.registration_id, amount_cents: data.amount_cents }) });
    const pay = await payRes.json();
    if (pay.payment_link) window.location.href = pay.payment_link;
    else alert('Team created. Payment pending.');
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
  window.registrationUI = { startSolo, startTeam };
})();

(function(){
  if (typeof window === 'undefined') return;
  function $(sel, root){ return (root||document).querySelector(sel); }
  const peliasBase = 'https://pelias.cephlabs.de/v1';
  const form = document.getElementById('create-event-form');
  if (!form) return; // page not loaded yet / not present
  const cityInput = form.querySelector('input[name="city"]');
  const zipsInput = form.querySelector('input[name="valid_zip_codes"]');
  const statusBox = document.getElementById('zip-codes-status');
  if (!cityInput || !zipsInput) return;

  // Build dropdown container
  function ensureRelative(el){ if (el && el.parentElement && !el.parentElement.classList.contains('relative')) el.parentElement.classList.add('relative'); }
  ensureRelative(cityInput);
  const dd = document.createElement('div');
  dd.className = 'absolute z-20 left-0 right-0 mt-1 bg-white border rounded-md shadow max-h-60 overflow-auto hidden';
  cityInput.parentElement.appendChild(dd);

  function hide(){ dd.classList.add('hidden'); }
  function show(){ dd.classList.remove('hidden'); }
  function clearDD(){ dd.innerHTML=''; hide(); }
  let debTimer = null;
  let userModifiedZipCodes = false;
  zipsInput.addEventListener('input', ()=>{ userModifiedZipCodes = true; });

  async function fetchJson(url){
    try { const r = await fetch(url, { headers: { 'Accept': 'application/json' } }); if (!r.ok) return null; return await r.json(); } catch { return null; }
  }

  function render(feats){
    dd.innerHTML='';
    if (!feats || !feats.length){ hide(); return; }
    feats.slice(0,8).forEach(f=>{
      const p = f.properties || {};
      const label = p.label || p.name || p.locality || p.city;
      if (!label) return;
      const btn = document.createElement('button');
      btn.type='button';
      btn.className='block w-full text-left px-3 py-2 hover:bg-gray-50';
      btn.textContent=label;
      btn.addEventListener('click', ()=>{
        if (p.locality || p.city) cityInput.value = p.locality || p.city;
        hide();
        // Trigger zip code enrichment
        enrichZipCodes();
      });
      dd.appendChild(btn);
    });
    show();
  }

  async function peliasCitySuggest(q){
    const url = `${peliasBase}/autocomplete?` + new URLSearchParams({
      text: q,
      size: '8',
      layers: 'locality,localadmin,borough,county,region,macroregion'
    });
    const json = await fetchJson(url);
    return (json && json.features) || [];
  }

  async function enrichZipCodes(){
    const city = cityInput.value.trim();
    if (!city){ if (statusBox) statusBox.textContent=''; return; }
    if (statusBox) statusBox.textContent='Recherche des codes postaux…';
    try {
      const rootPath = (window.__API_ROOT_PATH__ || '');
      const resp = await fetch(`${rootPath}/geo/zip-codes?city=${encodeURIComponent(city)}`, { credentials: 'include' });
      if (!resp.ok){ if (statusBox) statusBox.textContent='(zip codes indisponibles)'; return; }
      const data = await resp.json();
      if (Array.isArray(data.zip_codes) && data.zip_codes.length){
        // Populate only if user has not changed manually since last auto fill
        if (!userModifiedZipCodes){
          zipsInput.value = data.zip_codes.join(', ');
        }
        if (statusBox) statusBox.textContent = `Codes postaux trouvés: ${data.zip_codes.length}`;
      } else {
        if (statusBox) statusBox.textContent = 'Aucun code postal trouvé pour cette ville';
      }
    } catch (e){
      if (statusBox) statusBox.textContent='Erreur lors de la récupération des codes postaux';
    }
  }

  cityInput.addEventListener('input', ()=>{
    clearTimeout(debTimer);
    clearDD();
    const q = cityInput.value.trim();
    if (!q){ if (statusBox) statusBox.textContent=''; return; }
    debTimer = setTimeout(async ()=>{
      if (q.length < 2) return; // avoid spam
      const feats = await peliasCitySuggest(q);
      render(feats);
    }, 300);
  });

  cityInput.addEventListener('blur', ()=>{
    setTimeout(()=>{ hide(); }, 200);
  });

  // When city field loses focus and user has typed a full name, auto-fetch zip codes.
  cityInput.addEventListener('change', ()=>{ userModifiedZipCodes = false; enrichZipCodes(); });

  // If entering edit mode (handled by admin-dashboard.js), we want to re-evaluate zip codes only if empty
  document.addEventListener('dh:event_form_loaded', ()=>{
    if (cityInput.value && !zipsInput.value){ userModifiedZipCodes = false; enrichZipCodes(); }
  });
})();


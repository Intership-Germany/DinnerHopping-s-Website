(function(){
  if (typeof window === 'undefined') return;
  const apiFetch = (window.dh && window.dh.apiFetch) || window.apiFetch || null;
  const form = document.getElementById('create-event-form');
  if (!form) return;
  const cityInput = form.querySelector('input[name="city"]');
  const zipsInput = form.querySelector('input[name="valid_zip_codes"]');
  const statusBox = document.getElementById('zip-codes-status');
  const zipList = document.getElementById('zip-checkboxes');
  const selectAllBtn = document.getElementById('zip-select-all');
  const unselectAllBtn = document.getElementById('zip-unselect-all');
  const manualInput = document.getElementById('zip-manual-input');
  const manualAddBtn = document.getElementById('zip-add-btn');
  if (!cityInput || !zipsInput) return;

  function ensureRelative(el){
    if (!el || !el.parentElement) return;
    if (!el.parentElement.classList.contains('relative')) el.parentElement.classList.add('relative');
  }
  ensureRelative(cityInput);

  const dropdown = document.createElement('div');
  dropdown.className = 'absolute z-20 left-0 right-0 mt-1 bg-white border rounded-md shadow max-h-60 overflow-auto hidden';
  cityInput.parentElement.appendChild(dropdown);

  const state = {
    debounce: null,
    requestId: 0,
    userModifiedZips: false,
    suppressSuggestions: false,
    codeHints: [],
    availableZips: new Set(),
    selectedZips: new Set(),
    manualZips: new Set(),
  };

  zipsInput.addEventListener('input', ()=>{ state.userModifiedZips = true; });

  function showStatus(message, tone){
    if (!statusBox) return;
    statusBox.textContent = message || '';
    if (tone === 'error') statusBox.classList.add('text-red-600');
    else statusBox.classList.remove('text-red-600');
  }

  function sortZipCodes(list){
    return Array.from(new Set((Array.isArray(list) ? list : []).filter(Boolean))).sort((a, b)=>{
      const numA = /^[0-9]+$/.test(a) ? Number(a) : Number.NaN;
      const numB = /^[0-9]+$/.test(b) ? Number(b) : Number.NaN;
      if (!Number.isNaN(numA) && !Number.isNaN(numB)){ return numA - numB; }
      return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
    });
  }

  function syncSelectedZips(){
    const sorted = sortZipCodes(Array.from(state.selectedZips));
    zipsInput.value = sorted.join(', ');
    return sorted;
  }

  function updateSelectionSummary(prefix){
    const sorted = syncSelectedZips();
    const base = sorted.length ? `${sorted.length} zip code${sorted.length>1?'s':''} selected` : 'No zip codes selected';
    showStatus(prefix ? `${prefix} - ${base}` : base);
  }

  function renderZipCheckboxes(){
    if (!zipList) return;
    zipList.innerHTML = '';
    const combined = new Set();
    state.availableZips.forEach((zip)=> combined.add(zip));
    state.manualZips.forEach((zip)=> combined.add(zip));
    state.selectedZips.forEach((zip)=> combined.add(zip));
    const sorted = sortZipCodes(Array.from(combined));
    if (!sorted.length){
      const empty = document.createElement('div');
      empty.className = 'text-xs text-[#64748b] italic';
      empty.textContent = cityInput.value ? 'No ZIP codes loaded yet.' : 'Choose a city to load ZIP codes.';
      zipList.appendChild(empty);
      return;
    }
    sorted.forEach((zip)=>{
      const label = document.createElement('label');
      label.className = 'flex items-center justify-between gap-2 rounded-lg border border-[#e2e8f0] bg-white px-3 py-2 text-sm shadow-sm';
      const group = document.createElement('span');
      group.className = 'flex items-center gap-2';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = zip;
      checkbox.className = 'accent-[#f46f47] h-4 w-4';
      checkbox.checked = state.selectedZips.has(zip);
      const text = document.createElement('span');
      text.textContent = zip;
      group.appendChild(checkbox);
      group.appendChild(text);
      label.appendChild(group);
      if (state.manualZips.has(zip) && !state.availableZips.has(zip)){
        const badge = document.createElement('span');
        badge.className = 'rounded-full bg-[#2563eb]/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[#1d4ed8]';
        badge.textContent = 'Manual';
        label.appendChild(badge);
      }
      zipList.appendChild(label);
    });
  }

  function hydrateSelectionsFromHidden(options){
    if (!zipsInput) return;
    const cfg = options || {};
    const current = sortZipCodes(collectCodes(zipsInput.value));
    state.selectedZips = new Set(current);
    if (cfg.resetManual){
      const manual = new Set();
      current.forEach((zip)=>{ if (!state.availableZips.has(zip)) manual.add(zip); });
      state.manualZips = manual;
    }
  }

  function addManualZip(raw){
    if (!raw){
      showStatus('Enter a ZIP code to add.', 'error');
      return;
    }
    const cleaned = String(raw).replace(/[^0-9]/g, '');
    if (!cleaned){
      showStatus('ZIP codes must be numeric.', 'error');
      return;
    }
    if (cleaned.length < 3 || cleaned.length > 10){
      showStatus('ZIP codes must contain between 3 and 10 digits.', 'error');
      return;
    }
    const alreadyPresent = state.availableZips.has(cleaned) || state.manualZips.has(cleaned);
    state.manualZips.add(cleaned);
    state.selectedZips.add(cleaned);
    state.userModifiedZips = true;
    renderZipCheckboxes();
    updateSelectionSummary(alreadyPresent ? `ZIP ${cleaned} selected` : `ZIP ${cleaned} added`);
    if (manualInput){
      manualInput.value = '';
      manualInput.focus();
    }
  }

  function clearSuggestions(){ dropdown.innerHTML = ''; dropdown.classList.add('hidden'); }
  function showSuggestions(){ dropdown.classList.remove('hidden'); }

  function buildUrl(path){
    const root = String(window.__API_ROOT_PATH__ || '');
    if (!root) return path;
    const base = root.replace(/\/$/, '');
    if (path.startsWith('/')) return `${base}${path}`;
    return `${base}/${path}`;
  }

  async function requestZipData(city, codeHints){
    const trimmed = city.trim();
    if (!trimmed) return null;
    const params = new URLSearchParams({ city: trimmed });
    const hintSet = new Set(Array.isArray(codeHints) ? codeHints : []);
    hintSet.forEach((hint)=>{
      const cleaned = String(hint || '').trim();
      if (cleaned) params.append('codes', cleaned);
    });
    const endpoint = `/geo/zip-codes?${params.toString()}`;
    try {
      if (apiFetch){
        const res = await apiFetch(endpoint, { method: 'GET', headers: { Accept: 'application/json' } });
        if (!res.ok) return { error: true };
        return await res.clone().json().catch(()=>null);
      }
      const res = await fetch(buildUrl(endpoint), { credentials: 'include', headers: { Accept: 'application/json' } });
      if (!res.ok) return { error: true };
      return await res.json().catch(()=>null);
    } catch (err){
      console.warn('ZIP lookup failed', err);
      return { error: true };
    }
  }

  function normalizeKey(value){
    if (!value) return '';
    try {
      return String(value)
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .trim();
    } catch {
      return String(value).toLowerCase().trim();
    }
  }

  function collectCodes(value){
    const codes = new Set();
    const push = (code)=>{
      if (!code) return;
      const str = String(code).trim();
      if (!str) return;
      // Very permissive: allow digits and letters (some EU postal codes include letters)
      const parts = str.split(/[;,\s\/]+/);
      parts.forEach((part)=>{
        const cleaned = part.trim();
        if (cleaned) codes.add(cleaned);
      });
    };
    if (Array.isArray(value)) value.forEach(push);
    else if (value != null) push(value);
    return Array.from(codes);
  }

  hydrateSelectionsFromHidden({ resetManual: true });
  renderZipCheckboxes();
  updateSelectionSummary();

  function buildZipMap(records){
    const map = new Map();
    (Array.isArray(records) ? records : []).forEach((rec)=>{
      if (!rec) return;
      const cityName = String(rec.plz_name_long || rec.plz_name || '').trim();
      if (!cityName) return;
      const key = normalizeKey(cityName);
      let entry = map.get(key);
      if (!entry){
        entry = { city: cityName, zips: new Set(), meta: rec };
        map.set(key, entry);
      }
      if (rec.plz_code){ entry.zips.add(String(rec.plz_code).trim()); }
    });
    return map;
  }

  function buildSuggestions(zipMap, query){
    const list = [];
    if (!zipMap) return list;
    const normalizedQuery = query ? normalizeKey(query) : '';
    const collect = (entries, forcedLabel)=>{
      const union = new Set();
      let label = forcedLabel || '';
      entries.forEach((entry)=>{
        entry.zips.forEach((zip)=> union.add(zip));
        if (!label) label = entry.city;
      });
      if (!label) label = query || '';
      if (!label) return null;
      return { label, city: label, zips: Array.from(union), pelias: null, codeHints: [] };
    };

    if (normalizedQuery){
      const exact = zipMap.get(normalizedQuery);
      if (exact){
        const combined = collect([exact], query || exact.city);
        if (combined) list.push(combined);
        return list;
      }
      const candidates = [];
      zipMap.forEach((entry, key)=>{ if (key.includes(normalizedQuery)) candidates.push(entry); });
      if (candidates.length){
        const combined = collect(candidates, query);
        if (combined) list.push(combined);
        return list;
      }
    }

    const byCity = new Map();
    zipMap.forEach((entry)=>{
      if (!byCity.has(entry.city)) byCity.set(entry.city, new Set());
      const bucket = byCity.get(entry.city);
      entry.zips.forEach((zip)=> bucket.add(zip));
    });
    Array.from(byCity.entries())
      .sort((a, b)=> a[0].localeCompare(b[0], undefined, { sensitivity: 'base' }))
      .forEach(([cityName, bucket])=>{
        list.push({ label: cityName, city: cityName, zips: Array.from(bucket), pelias: null, codeHints: [] });
      });
    return list;
  }

  function updateZipField(zipList, cityName, overrideUser){
    const list = sortZipCodes(Array.isArray(zipList) ? zipList.filter(Boolean) : []);
    if (!list.length && overrideUser && state.availableZips.size){
      renderZipCheckboxes();
      updateSelectionSummary(`Reusing existing ZIP codes for ${cityName || 'this city'}`);
      return;
    }
    state.availableZips = new Set(list);
    const cityLabel = cityName ? cityName : 'this city';
    if (!state.userModifiedZips || overrideUser){
      const fresh = new Set(list);
      state.manualZips.forEach((zip)=> fresh.add(zip));
      state.selectedZips = fresh;
      state.userModifiedZips = false;
      renderZipCheckboxes();
      updateSelectionSummary(list.length ? `${list.length} zip code${list.length>1?'s':''} loaded for ${cityLabel}` : `No zip codes found for ${cityLabel}`);
    } else {
      state.selectedZips = new Set(Array.from(state.selectedZips).filter((zip)=> state.availableZips.has(zip) || state.manualZips.has(zip)));
      renderZipCheckboxes();
      updateSelectionSummary(list.length ? `${list.length} zip code${list.length>1?'s':''} available for ${cityLabel}` : `No zip codes found for ${cityLabel}`);
    }
  }

  function renderSuggestions(items){
    if (!Array.isArray(items) || !items.length){
      clearSuggestions();
      return;
    }
    dropdown.innerHTML = '';
    items.slice(0, 12).forEach((item)=>{
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'block w-full text-left px-3 py-2 hover:bg-gray-50';
  btn.textContent = item.label || item.city || '';
      btn.addEventListener('click', ()=>{
        if (state.debounce){
          clearTimeout(state.debounce);
          state.debounce = null;
        }
        state.suppressSuggestions = true;
        cityInput.value = item.city || cityInput.value;
  const codes = Array.isArray(item.zips) ? item.zips : [];
  const adminHints = Array.isArray(item.codeHints) ? item.codeHints : [];
  state.codeHints = Array.from(new Set(adminHints));
        clearSuggestions();
        updateZipField(codes, item.city, true);
        state.userModifiedZips = false;
        handleCityChange();
      });
      dropdown.appendChild(btn);
    });
    showSuggestions();
  }

  async function handleCityChange(){
    const city = cityInput.value.trim();
    if (!city){
      state.availableZips.clear();
      renderZipCheckboxes();
      updateSelectionSummary();
      clearSuggestions();
      state.suppressSuggestions = false;
      return;
    }
    showStatus('Searching for zip codes…');
    const data = await requestZipData(city, state.codeHints);
    if (!data || data.error){
      showStatus('Zip code lookup unavailable', 'error');
      clearSuggestions();
      state.suppressSuggestions = false;
      return;
    }
    const zipMap = buildZipMap(data.records);
    const normalizedCity = normalizeKey(city);
    const directEntry = zipMap.get(normalizedCity);
    let effectiveEntry = directEntry || null;
    let usedCombined = false;
    if (!effectiveEntry){
      const matches = [];
      zipMap.forEach((entry, key)=>{ if (key.includes(normalizedCity)) matches.push(entry); });
      if (matches.length){
        const union = new Set();
        matches.forEach((entry)=>{
          entry.zips.forEach((zip)=> union.add(zip));
        });
        effectiveEntry = { city: city, zips: union };
        usedCombined = true;
      }
    }
    let zips = effectiveEntry ? Array.from(effectiveEntry.zips) : [];
    if (!zips.length && Array.isArray(data.zip_codes)){
      zips = sortZipCodes(collectCodes(data.zip_codes));
    }
    const displayCity = effectiveEntry ? effectiveEntry.city : city;
    updateZipField(zips, displayCity, usedCombined || !directEntry);
    if (!state.suppressSuggestions && document.activeElement === cityInput){
      const baseSuggestions = buildSuggestions(zipMap, city);
      if (baseSuggestions.length){
        renderSuggestions(baseSuggestions);
      } else {
        clearSuggestions();
      }
    } else {
      clearSuggestions();
    }
    state.suppressSuggestions = false;
  }

  if (zipList){
    zipList.addEventListener('change', (ev)=>{
      const target = ev.target;
      if (!target || target.type !== 'checkbox') return;
      const zip = target.value;
      if (!zip) return;
      if (target.checked){
        state.selectedZips.add(zip);
      } else {
        state.selectedZips.delete(zip);
      }
      state.userModifiedZips = true;
      updateSelectionSummary();
    });
  }

  if (selectAllBtn){
    selectAllBtn.addEventListener('click', (ev)=>{
      ev.preventDefault();
      const combined = new Set([...state.availableZips, ...state.manualZips]);
      if (!combined.size){
        showStatus('No ZIP codes to select.', 'error');
        return;
      }
      state.selectedZips = new Set(combined);
      state.userModifiedZips = true;
      renderZipCheckboxes();
      updateSelectionSummary('All ZIP codes selected');
    });
  }

  if (unselectAllBtn){
    unselectAllBtn.addEventListener('click', (ev)=>{
      ev.preventDefault();
      if (!state.selectedZips.size){
        showStatus('No ZIP codes are currently selected.', 'error');
        return;
      }
      state.selectedZips.clear();
      state.userModifiedZips = true;
      renderZipCheckboxes();
      updateSelectionSummary('Selection cleared');
    });
  }

  function handleManualSubmit(){
    if (!manualInput) return;
    addManualZip(manualInput.value.trim());
  }

  if (manualAddBtn){
    manualAddBtn.addEventListener('click', (ev)=>{
      ev.preventDefault();
      handleManualSubmit();
    });
  }

  if (manualInput){
    manualInput.addEventListener('keydown', (ev)=>{
      if (ev.key === 'Enter'){
        ev.preventDefault();
        handleManualSubmit();
      }
    });
  }

  cityInput.addEventListener('input', ()=>{
    if (state.debounce) clearTimeout(state.debounce);
    const query = cityInput.value.trim();
    if (!state.suppressSuggestions) state.codeHints = [];
    if (!query){
      clearSuggestions();
      state.availableZips.clear();
      renderZipCheckboxes();
      updateSelectionSummary();
      return;
    }
    state.debounce = setTimeout(async ()=>{
      if (query.length < 2){
        clearSuggestions();
        return;
      }
      const currentId = ++state.requestId;
      const zipData = await requestZipData(query, state.codeHints);
      if (currentId !== state.requestId) return;
      let zipMap = null;
      if (zipData && !zipData.error){
        zipMap = buildZipMap(zipData.records);
        const entry = zipMap.get(normalizeKey(query));
        if (entry && entry.zips.size){
          updateZipField(Array.from(entry.zips), entry.city, false);
        }
      }
      const suggestions = buildSuggestions(zipMap, query);
      if (!suggestions.length){
        if (zipMap && zipMap.size){
          renderSuggestions(buildSuggestions(zipMap, ''));
        } else {
          clearSuggestions();
        }
      } else {
        renderSuggestions(suggestions);
      }
    }, 250);
  });

  cityInput.addEventListener('blur', ()=>{
    setTimeout(clearSuggestions, 200);
  });

  cityInput.addEventListener('change', ()=>{
    state.userModifiedZips = false;
    state.suppressSuggestions = true;
    handleCityChange();
  });

  document.addEventListener('dh:event_form_loaded', ()=>{
    hydrateSelectionsFromHidden({ resetManual: true });
    renderZipCheckboxes();
    updateSelectionSummary();
    if (cityInput.value){
      state.userModifiedZips = !!zipsInput.value;
      state.suppressSuggestions = true;
      handleCityChange();
    } else {
      state.userModifiedZips = false;
    }
  });

  document.addEventListener('click', (ev)=>{
    if (ev.target !== cityInput && !dropdown.contains(ev.target)){
      clearSuggestions();
    }
  });
})();


(function(){
  if (typeof window === 'undefined') return;
  const apiFetch = (window.dh && window.dh.apiFetch) || window.apiFetch || null;
  const peliasBase = String(window.PELIAS_BASE_URL || 'https://pelias.cephlabs.de/v1').replace(/\/$/, '');
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
    cityCache: new Map(),
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

  function cleanCityLabel(raw){
    if (!raw) return '';
    const base = String(raw)
      .replace(/\b\d{3,}\b/g, ' ')
      .replace(/\s{2,}/g, ' ')
      .replace(/[,-]\s*$/g, '')
      .trim();
    return base || String(raw).trim();
  }

  function describeRegion(props){
    if (!props) return '';
    const seen = new Set();
    const add = (value)=>{
      if (!value) return;
      const cleaned = cleanCityLabel(value);
      if (!cleaned) return;
      const key = cleaned.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
    };
    add(props.region);
    add(props.state);
    add(props.county);
    add(props.macroregion);
    add(props.localadmin);
    if (!seen.size && props.country){ add(props.country); }
    const [first] = Array.from(seen.values());
    return first || '';
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

  async function fetchPeliasSuggestions(query){
    const trimmed = query.trim();
    if (!trimmed) return [];
    const params = new URLSearchParams({
      text: trimmed,
      size: '8',
      layers: 'locality,localadmin,borough,county,region,macroregion',
    });
    params.set('lang', 'de');
    try {
      const res = await fetch(`${peliasBase}/autocomplete?${params.toString()}`, { headers: { Accept: 'application/json' } });
      if (!res.ok) return [];
      const data = await res.json().catch(()=>null);
      return (data && Array.isArray(data.features)) ? data.features : [];
    } catch (err){
      console.warn('Pelias autocomplete failed', err);
      return [];
    }
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
      const cityName = cleanCityLabel(rec.plz_name_long || rec.plz_name || '');
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

  function buildSuggestions(peliasFeatures, zipMap, query){
    const normalizedQuery = query ? normalizeKey(query) : '';
    const merged = new Map();

    const ensureEntry = (key, cityName, label, regionHint)=>{
      if (!key) return null;
      const existing = merged.get(key);
      if (existing){
        if (label && !existing.label) existing.label = cleanCityLabel(label);
        if (cityName && !existing.city) existing.city = cleanCityLabel(cityName);
        if (regionHint && !existing.region) existing.region = regionHint;
        return existing;
      }
      const entry = {
        city: cleanCityLabel(cityName || label || query || ''),
        label: cleanCityLabel(label || cityName || query || ''),
        zips: new Set(),
        pelias: null,
        codeHints: [],
        region: regionHint || '',
      };
      merged.set(key, entry);
      return entry;
    };

    const incorporateZipEntry = (entry)=>{
      if (!entry) return;
      const key = normalizeKey(entry.city);
      const target = ensureEntry(key, entry.city, entry.city, entry.region || '');
      if (!target) return;
      entry.zips.forEach((zip)=> target.zips.add(zip));
    };

    if (zipMap){
      zipMap.forEach((entry)=>{
        incorporateZipEntry(entry);
      });
    }

    state.cityCache.forEach((entry, key)=>{
      const target = ensureEntry(key, entry.city, entry.city);
      if (!target) return;
      entry.zips.forEach((zip)=> target.zips.add(zip));
    });

    if (Array.isArray(peliasFeatures)){
      peliasFeatures.forEach((feature)=>{
        const props = feature && feature.properties ? feature.properties : {};
        const localizedCity = props.localname || props.locality || props.city || props.name || '';
        const cityName = cleanCityLabel(localizedCity);
        if (!cityName) return;
        const key = normalizeKey(cityName);
        const regionHint = describeRegion(props) || '';
        const label = regionHint ? `${cityName} (${regionHint})` : cityName;
        const target = ensureEntry(key, cityName, label, regionHint);
        if (target) target.pelias = feature;
      });
    }

    const results = Array.from(merged.entries()).map(([key, entry])=>{
      if (!entry.city) entry.city = cleanCityLabel(entry.label || query || key);
      if (!entry.label){
        entry.label = entry.region ? `${entry.city} (${entry.region})` : entry.city;
      }
      if (entry.region && entry.label === entry.city){
        entry.label = `${entry.city} (${entry.region})`;
      }
      entry.zips = Array.from(entry.zips);
      return entry;
    });

    results.sort((a, b)=>{
      const ka = normalizeKey(a.city);
      const kb = normalizeKey(b.city);
      const score = (k)=>{
        if (!normalizedQuery) return 2;
        if (k === normalizedQuery) return 0;
        if (k.startsWith(normalizedQuery)) return 1;
        if (k.includes(normalizedQuery)) return 2;
        return 3;
      };
      const sa = score(ka);
      const sb = score(kb);
      if (sa !== sb) return sa - sb;
      return a.city.localeCompare(b.city, undefined, { sensitivity: 'base' });
    });

    return results;
  }

  function updateZipField(zipList, cityName, overrideUser){
    const list = sortZipCodes(Array.isArray(zipList) ? zipList.filter(Boolean) : []);
    if (!list.length && overrideUser && state.availableZips.size){
      renderZipCheckboxes();
      updateSelectionSummary(`Reusing existing ZIP codes for ${cityName || 'this city'}`);
      return;
    }
    state.availableZips = new Set(list);
    const cityLabel = cityName ? cleanCityLabel(cityName) : 'this city';
    if (list.length){
      const cacheKey = normalizeKey(cityLabel);
      const cacheEntry = state.cityCache.get(cacheKey) || { city: cityLabel, zips: new Set() };
      cacheEntry.city = cityLabel;
      cacheEntry.zips = new Set(list);
      state.cityCache.set(cacheKey, cacheEntry);
    }
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
        if (codes.length){
          updateZipField(codes, item.city, true);
        } else {
          showStatus('Searching for zip codes…');
        }
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
    zipMap.forEach((entry, key)=>{
      const cacheKey = normalizeKey(entry.city);
      const existing = state.cityCache.get(cacheKey);
      if (!existing){
        state.cityCache.set(cacheKey, { city: entry.city, zips: new Set(entry.zips) });
      } else {
        entry.zips.forEach((zip)=> existing.zips.add(zip));
        existing.city = cleanCityLabel(existing.city || entry.city);
      }
    });
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
      const baseSuggestions = buildSuggestions(null, zipMap, city);
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
      const [zipData, peliasFeatures] = await Promise.all([
        requestZipData(query, state.codeHints),
        fetchPeliasSuggestions(query),
      ]);
      if (currentId !== state.requestId) return;
      let zipMap = null;
      if (zipData && !zipData.error){
        zipMap = buildZipMap(zipData.records);
        if (zipMap){
          zipMap.forEach((entry, key)=>{
            const cacheKey = normalizeKey(entry.city);
            const existing = state.cityCache.get(cacheKey);
            if (!existing){
              state.cityCache.set(cacheKey, { city: entry.city, zips: new Set(entry.zips) });
            } else {
              entry.zips.forEach((zip)=> existing.zips.add(zip));
              existing.city = cleanCityLabel(existing.city || entry.city);
            }
          });
        }
        const entry = zipMap.get(normalizeKey(query));
        if (entry && entry.zips.size){
          updateZipField(Array.from(entry.zips), entry.city, false);
        }
      }
  const suggestions = buildSuggestions(peliasFeatures, zipMap, query);
      if (!suggestions.length){
        if ((zipMap && zipMap.size) || state.cityCache.size){
          renderSuggestions(buildSuggestions(peliasFeatures, zipMap, ''));
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


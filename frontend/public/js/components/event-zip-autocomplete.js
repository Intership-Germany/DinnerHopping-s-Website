(function(){
  if (typeof window === 'undefined') return;
  const apiFetch = (window.dh && window.dh.apiFetch) || window.apiFetch || null;
  const peliasBase = String(window.PELIAS_BASE_URL || 'https://pelias.cephlabs.de/v1').replace(/\/$/, '');
  const form = document.getElementById('create-event-form');
  if (!form) return;
  const cityInput = form.querySelector('input[name="city"]');
  const zipsInput = form.querySelector('input[name="valid_zip_codes"]');
  const statusBox = document.getElementById('zip-codes-status');
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
  };

  zipsInput.addEventListener('input', ()=>{ state.userModifiedZips = true; });

  function showStatus(message){ if (statusBox) statusBox.textContent = message || ''; }

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

  async function fetchPeliasPostalCodes(city){
    const trimmed = city.trim();
    if (!trimmed) return [];
    const params = new URLSearchParams({ locality: trimmed, size: '50' });
    try {
      const res = await fetch(`${peliasBase}/search/structured?${params.toString()}`, { headers: { Accept: 'application/json' } });
      if (!res.ok) return [];
      const data = await res.json().catch(()=>null);
      if (!data || !Array.isArray(data.features)) return [];
      const codes = new Set();
      data.features.forEach((feat)=>{
        extractCodesFromFeature(feat).forEach((code)=> codes.add(code));
      });
      return Array.from(codes);
    } catch (err){
      console.warn('Pelias postal lookup failed', err);
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

  function extractCodesFromFeature(feature){
    if (!feature || !feature.properties) return [];
    const props = feature.properties;
    const collected = new Set();
    collectCodes(props.postalcode).forEach((c)=> collected.add(c));
    if (props.postalcode_array) collectCodes(props.postalcode_array).forEach((c)=> collected.add(c));
    if (props.addendum){
      const add = props.addendum;
      if (add['whosonfirst'] && add['whosonfirst'].postalcode){
        collectCodes(add['whosonfirst'].postalcode).forEach((c)=> collected.add(c));
      }
      if (add.openaddresses && add.openaddresses.zip){
        collectCodes(add.openaddresses.zip).forEach((c)=> collected.add(c));
      }
    }
    return Array.from(collected);
  }

  function extractAdminCodeHints(feature){
    if (!feature || !feature.properties) return [];
    const props = feature.properties;
    const hints = new Set();
    const addDigitSeq = (value)=>{
      if (value == null) return;
      if (Array.isArray(value)){
        value.forEach(addDigitSeq);
        return;
      }
      if (typeof value === 'object'){
        Object.values(value).forEach(addDigitSeq);
        return;
      }
      const str = String(value);
      const matches = str.match(/\d{5,}/g);
      if (!matches) return;
      matches.forEach((match)=>{
        const variants = new Set([match]);
        const stripped = match.replace(/^0+/, '');
        if (stripped) variants.add(stripped);
        variants.forEach((variant)=>{
          hints.add(variant);
          if (variant.length >= 8) hints.add(variant.slice(0, 8));
          if (variant.length >= 5) hints.add(variant.slice(0, 5));
        });
      });
    };

    const addHint = (value)=>{
      if (value == null) return;
      if (Array.isArray(value)){
        value.forEach(addHint);
        return;
      }
      if (typeof value === 'object'){
        Object.values(value).forEach(addHint);
        return;
      }
      addDigitSeq(value);
    };

    const primaryKeys = [
      'gisco_id',
      'eg:gisco_id',
      'eurostat:nuts_2021_id',
      'eurostat:nuts_2016_id',
      'nuts_2021_id',
      'nuts_2016_id',
      'nuts_id',
      'nuts',
      'source_id',
      'localadmin_id',
      'county_id',
      'krs_code',
      'id',
    ];
    primaryKeys.forEach((key)=> addHint(props[key]));

    if (props.addendum && typeof props.addendum === 'object'){
      Object.values(props.addendum).forEach((section)=>{
        if (!section || typeof section !== 'object') return;
        addHint(section);
        ['gisco_id','nuts_2021_id','nuts_2016_id','nuts_id','code','id','krs_code'].forEach((innerKey)=>{
          addHint(section[innerKey]);
        });
      });
    }

    if (Array.isArray(props.hierarchy)){
      props.hierarchy.forEach((level)=>{
        if (!level || typeof level !== 'object') return;
        ['id','localadmin_id','county_id','source_id'].forEach((innerKey)=> addHint(level[innerKey]));
      });
    }

    return Array.from(hints);
  }

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

  function buildSuggestions(peliasFeatures, zipMap){
    const used = new Set();
    const list = [];
    if (Array.isArray(peliasFeatures)){
      peliasFeatures.forEach((feature)=>{
        const props = feature && feature.properties ? feature.properties : {};
        const cityName = String(props.locality || props.city || props.name || '').trim();
        if (!cityName) return;
        const key = normalizeKey(cityName);
        const entry = zipMap && zipMap.get(key);
        const merged = new Set();
        if (entry){ Array.from(entry.zips).forEach((z)=> merged.add(z)); }
        extractCodesFromFeature(feature).forEach((z)=> merged.add(z));
        const zips = Array.from(merged);
        const label = props.label || [cityName, props.region || props.county, props.country].filter(Boolean).join(' · ');
        list.push({ label: label || cityName, city: cityName, zips, pelias: feature, codeHints: extractAdminCodeHints(feature) });
        used.add(key);
      });
    }
    if (zipMap){
      zipMap.forEach((entry, key)=>{
        if (used.has(key)) return;
        const zips = Array.from(entry.zips);
        const teaser = zips.length ? ` · ${zips.slice(0, 4).join(', ')}${zips.length > 4 ? ', …' : ''}` : '';
        list.push({ label: `${entry.city}${teaser}`, city: entry.city, zips, pelias: null, codeHints: [] });
      });
    }
    return list;
  }

  function updateZipField(zipList, cityName, overrideUser){
    const list = Array.isArray(zipList) ? zipList.filter(Boolean) : [];
    if (list.length && (!state.userModifiedZips || overrideUser)){
      zipsInput.value = list.join(', ');
      state.userModifiedZips = false;
    }
    if (statusBox){
      if (list.length){
        statusBox.textContent = `${list.length} zip code${list.length>1?'s':''} for ${cityName || 'the city'}`;
      } else if (!overrideUser) {
        statusBox.textContent = 'No zip code found for this city';
      }
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
      const suffix = item.zips && item.zips.length ? ` · ${item.zips.slice(0, 4).join(', ')}${item.zips.length > 4 ? ', …' : ''}` : '';
      btn.textContent = `${item.label || item.city || ''}${suffix}`;
      btn.addEventListener('click', ()=>{
        if (state.debounce){
          clearTimeout(state.debounce);
          state.debounce = null;
        }
        state.suppressSuggestions = true;
        cityInput.value = item.city || cityInput.value;
        const codes = (item.zips && item.zips.length) ? item.zips : extractCodesFromFeature(item.pelias);
        const adminHints = Array.isArray(item.codeHints) && item.codeHints.length ? item.codeHints : extractAdminCodeHints(item.pelias);
        state.codeHints = Array.from(new Set(adminHints));
        clearSuggestions();
        updateZipField(codes, item.city, true);
        showStatus('');
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
      showStatus('');
      clearSuggestions();
      state.suppressSuggestions = false;
      return;
    }
    showStatus('Searching for zip codes…');
    const data = await requestZipData(city, state.codeHints);
    if (!data || data.error){
      const fallbackCodes = await fetchPeliasPostalCodes(city);
      if (fallbackCodes.length){
        updateZipField(fallbackCodes, city, true);
        showStatus(`${fallbackCodes.length} zip code${fallbackCodes.length>1?'s':''} for ${city}`);
      } else {
        showStatus('Zip code lookup unavailable');
      }
      clearSuggestions();
      state.suppressSuggestions = false;
      return;
    }
    const zipMap = buildZipMap(data.records);
    const entry = zipMap.get(normalizeKey(city));
    let zips = entry ? Array.from(entry.zips) : (data.zip_codes || []);
    if (!zips.length){
      zips = await fetchPeliasPostalCodes(city);
    }
    updateZipField(zips, entry ? entry.city : city, !entry);
    if (!state.suppressSuggestions && document.activeElement === cityInput){
      const baseSuggestions = buildSuggestions([], zipMap);
      if (baseSuggestions.length){
        renderSuggestions(baseSuggestions);
      } else if (zips.length){
        renderSuggestions([{ label: city, city, zips, pelias: null, codeHints: [] }]);
      } else {
        clearSuggestions();
      }
    } else {
      clearSuggestions();
    }
    state.suppressSuggestions = false;
  }

  cityInput.addEventListener('input', ()=>{
    if (state.debounce) clearTimeout(state.debounce);
    const query = cityInput.value.trim();
    if (!state.suppressSuggestions) state.codeHints = [];
    if (!query){
      clearSuggestions();
      showStatus('');
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
        const entry = zipMap.get(normalizeKey(query));
        if (entry && entry.zips.size){
          updateZipField(Array.from(entry.zips), entry.city, false);
        }
      }
      const suggestions = buildSuggestions(peliasFeatures, zipMap);
      if (!suggestions.length){
        if (zipMap && zipMap.size){
          renderSuggestions(buildSuggestions([], zipMap));
        } else if (zipData && Array.isArray(zipData.zip_codes) && zipData.zip_codes.length){
          renderSuggestions([{ label: zipData.city || query, city: zipData.city || query, zips: zipData.zip_codes, pelias: null, codeHints: [] }]);
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
    if (cityInput.value && !zipsInput.value){
      state.userModifiedZips = false;
      state.suppressSuggestions = true;
      handleCityChange();
    }
  });

  document.addEventListener('click', (ev)=>{
    if (ev.target !== cityInput && !dropdown.contains(ev.target)){
      clearSuggestions();
    }
  });
})();


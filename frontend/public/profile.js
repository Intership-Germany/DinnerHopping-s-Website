(function(){
  // Page script for profile.html extracted from inline <script>
  document.addEventListener('DOMContentLoaded', () => {
    (async function(){
      const BACKEND_BASE = window.BACKEND_BASE_URL || 'http://localhost:8000';
      const token = (window.auth && window.auth.getCookie) ? window.auth.getCookie('dh_token') : (function(){
        const m = document.cookie.match(/(?:^|; )dh_token=([^;]*)/); return m?decodeURIComponent(m[1]):null;
      })();

      const el = {
        headerName: document.getElementById('header-name'),
        headerEmail: document.getElementById('header-email'),
        name: document.getElementById('profile-name'),
        email: document.getElementById('profile-email'),
        address: document.getElementById('profile-address'),
  // Address edit inputs
  addressEditGroup: document.getElementById('address-edit-group'),
  addrCity: document.getElementById('profile-city'),
  addrPostal: document.getElementById('profile-postal'),
  addrStreet: document.getElementById('profile-street'),
  addrNumber: document.getElementById('profile-number'),
        preferences: document.getElementById('profile-preferences'),
        kitchenAvailable: document.getElementById('kitchen-available'),
        mainCoursePossible: document.getElementById('main-course-possible'),
        dietary: document.getElementById('default-dietary'),
        fieldOfStudy: document.getElementById('field-of-study'),
        editBtn: document.getElementById('edit-btn'),
        saveBtn: document.getElementById('save-btn'),
        cancelBtn: document.getElementById('cancel-btn'),
        editActions: document.getElementById('edit-actions'),
        incompleteBanner: document.getElementById('incomplete-banner'),
        incompleteDetails: document.getElementById('incomplete-details'),
        unsavedBanner: document.getElementById('unsaved-banner'),
        skeletonHeader: document.getElementById('skeleton-header'),
        skeletonMain: document.getElementById('skeleton-main'),
        profileHeader: document.getElementById('profile-header'),
        profileMain: document.getElementById('profile-main'),
        onboardingModal: document.getElementById('onboarding-modal'),
        onboardingMissing: document.getElementById('onboarding-missing'),
        onboardingSkip: document.getElementById('onboarding-skip'),
        onboardingFill: document.getElementById('onboarding-fill'),
        mainCourseGroup: document.getElementById('main-course-group'),
      };

      let initialData = null;
      let isEditing = false;
      let hasUnsaved = false;

      function formatPreferences(pref){
        try {
          if (!pref) return '';
          // Filter out optional-only keys so textarea shows only the user's food prefs
          const OPTIONAL_KEYS = new Set(['kitchen_available','main_course_possible','default_dietary','field_of_study','onboarding_seen']);
          if (typeof pref === 'object' && !Array.isArray(pref)) {
            const filtered = {};
            for (const [k,v] of Object.entries(pref)) {
              if (!OPTIONAL_KEYS.has(k)) filtered[k] = v;
            }
            pref = filtered;
          }
          if (Array.isArray(pref)) return pref.join(', ');
          if (typeof pref === 'string') return pref;
          if (typeof pref === 'object') {
            const keys = Object.keys(pref);
            if (keys.length === 1 && keys[0] === 'tags') {
              const v = pref.tags;
              if (Array.isArray(v)) return v.join(', ');
              if (typeof v === 'string') return v;
            }
            const parts = [];
            for (const [k,v] of Object.entries(pref)){
              if (v === true) parts.push(k);
              else if (Array.isArray(v) && v.length) parts.push(`${k}: ${v.join(', ')}`);
              else if (typeof v === 'string' && v.trim()) parts.push(`${k}: ${v}`);
              else if (typeof v === 'number') parts.push(`${k}: ${v}`);
            }
            return parts.join(', ');
          }
          return String(pref);
        } catch(e){
          return '';
        }
      }

      function isEmptyPreferences(pref){
        if (!pref) return true;
        if (Array.isArray(pref)) return pref.length === 0;
        if (typeof pref === 'string') return pref.trim().length === 0;
        if (typeof pref === 'object') return Object.keys(pref).length === 0;
        return true;
      }

      function getOptionalFromPrefs(prefs){
        const p = prefs || {};
        return {
          kitchen_available: typeof p.kitchen_available === 'boolean' ? p.kitchen_available : null,
          main_course_possible: typeof p.main_course_possible === 'boolean' ? p.main_course_possible : null,
          default_dietary: (typeof p.default_dietary === 'string' && ['vegan','vegetarian','omnivore'].includes(p.default_dietary)) ? p.default_dietary : null,
          field_of_study: (typeof p.field_of_study === 'string' && p.field_of_study.trim()) ? p.field_of_study.trim() : null,
          onboarding_seen: !!p.onboarding_seen,
        };
      }

      function setOptionalInputs(vals){
        if (!vals) vals = {};
        if (el.kitchenAvailable) el.kitchenAvailable.value = vals.kitchen_available === true ? 'yes' : vals.kitchen_available === false ? 'no' : '';
        if (el.mainCoursePossible) el.mainCoursePossible.value = vals.main_course_possible === true ? 'yes' : vals.main_course_possible === false ? 'no' : '';
        if (el.dietary) el.dietary.value = vals.default_dietary || '';
        if (el.fieldOfStudy) el.fieldOfStudy.value = vals.field_of_study || '';
        // Show/hide main course group based on kitchen availability
        if (el.mainCourseGroup) el.mainCourseGroup.classList.toggle('hidden', el.kitchenAvailable && el.kitchenAvailable.value !== 'yes');
      }

      function collectOptionalToPrefs(){
        const out = {};
        const k = el.kitchenAvailable?.value || '';
        const m = el.mainCoursePossible?.value || '';
        const d = el.dietary?.value || '';
        const f = el.fieldOfStudy?.value?.trim() || '';
        if (k === 'yes') out.kitchen_available = true; else if (k === 'no') out.kitchen_available = false;
        if (k === 'yes') { // only meaningful when kitchen is available
          if (m === 'yes') out.main_course_possible = true; else if (m === 'no') out.main_course_possible = false;
        }
        if (['vegan','vegetarian','omnivore'].includes(d)) out.default_dietary = d;
        if (f) out.field_of_study = f;
        return out;
      }

      try {
        if (!window.dbg) window.dbg = { logReq: (...a)=>console.log(...a) };
        let res;
        if (token) {
          res = await fetch(`${BACKEND_BASE}/profile`, { headers: { 'Authorization': `Bearer ${token}` } });
        } else {
          res = await window.apiFetch(`/profile`, { method: 'GET' });
        }
        const data = await res.json();
        dbg.logReq('GET /profile', { status: res.status, body: data });
        if (!res.ok) {
          window.location.href = 'login.html';
          return;
        }

        if (el.headerName) el.headerName.textContent = data.name || '';
        if (el.headerEmail) el.headerEmail.textContent = data.email || '';
        if (el.name) el.name.value = data.name || '';
        if (el.email) el.email.value = data.email || '';
        if (el.address) el.address.value = data.address || '';

        const prefText = formatPreferences(data.preferences);
        if (el.preferences) el.preferences.value = prefText || '';

        // map optional fields from preferences
        const opt = getOptionalFromPrefs(data.preferences);
        setOptionalInputs(opt);

        initialData = {
          name: data.name || '',
          email: data.email || '',
          address: data.address || '',
          preferences: data.preferences ?? {}
        };

        el.skeletonHeader?.classList.add('hidden');
        el.skeletonMain?.classList.add('hidden');
        el.profileHeader?.classList.remove('hidden');
        el.profileMain?.classList.remove('hidden');

        const missing = [];
        if (!data.name) missing.push('name');
        if (!data.email) missing.push('email');
        if (!data.address) missing.push('address');
        // Optional fields aren't required, but we can highlight if none set
        const optMissing = [];
        if (opt.kitchen_available == null) optMissing.push('kitchen');
        if (opt.kitchen_available === true && opt.main_course_possible == null) optMissing.push('main course');
        if (!opt.default_dietary) optMissing.push('dietary');
        if (!opt.field_of_study) optMissing.push('field of study');
        if (isEmptyPreferences(data.preferences)) missing.push('preferences');
        if (missing.length && el.incompleteBanner){
          el.incompleteBanner.classList.remove('hidden');
          if (el.incompleteDetails) {
            el.incompleteDetails.textContent = `Missing: ${missing.join(', ')}`;
          }
        }

        // Show onboarding modal on first login (if not seen) with summary of optional gaps
        if (el.onboardingModal) {
          const shouldShow = !opt.onboarding_seen && (optMissing.length > 0);
          if (shouldShow) {
            if (el.onboardingMissing) el.onboardingMissing.textContent = optMissing.join(', ');
            el.onboardingModal.classList.remove('hidden');
          }
        }
      } catch (err) {
        console.error(err);
        window.location.href = 'login.html';
      }

      function setEditMode(on){
        isEditing = !!on;
        const toggle = (inp) => {
          if (!inp) return;
          if (isEditing) {
            inp.removeAttribute('disabled');
            inp.classList.remove('bg-gray-50');
            inp.classList.add('bg-white');
          } else {
            inp.setAttribute('disabled', '');
            inp.classList.add('bg-gray-50');
            inp.classList.remove('bg-white');
          }
        };
        if (el.name) {
          el.name.setAttribute('disabled', '');
          el.name.classList.add('bg-gray-50');
          el.name.classList.remove('bg-white');
        }
        toggle(el.email);
        // Address: view vs edit group
        if (el.addressEditGroup) el.addressEditGroup.classList.toggle('hidden', !isEditing);
        if (el.address) el.address.classList.toggle('hidden', isEditing);
        toggle(el.addrCity);
        toggle(el.addrPostal);
        toggle(el.addrStreet);
        toggle(el.addrNumber);
        // Do not allow editing the displayed anonymized address input
        if (el.address && isEditing) el.address.setAttribute('disabled', '');
        if (el.address && !isEditing) el.address.setAttribute('disabled', '');
        
        toggle(el.preferences);
        toggle(el.kitchenAvailable);
        toggle(el.mainCoursePossible);
        toggle(el.dietary);
        toggle(el.fieldOfStudy);

        if (el.editBtn) el.editBtn.classList.toggle('hidden', isEditing);
        if (el.editActions) el.editActions.classList.toggle('hidden', !isEditing);

        if (!isEditing && el.unsavedBanner) el.unsavedBanner.classList.add('hidden');
        hasUnsaved = false;
      }

      function showUnsaved(on){
        if (!el.unsavedBanner) return;
        el.unsavedBanner.classList.toggle('hidden', !on);
      }

      function parsePreferencesInput(text){
        if (!text || !text.trim()) return {};
        try {
          const parsed = JSON.parse(text);
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            // strip optional keys if present
            const OPTIONAL_KEYS = new Set(['kitchen_available','main_course_possible','default_dietary','field_of_study','onboarding_seen']);
            const out = {};
            for (const [k,v] of Object.entries(parsed)) if (!OPTIONAL_KEYS.has(k)) out[k] = v;
            return out;
          }
          if (Array.isArray(parsed)) {
            return { tags: parsed };
          }
        } catch(e){}
        const tags = text.split(',').map(s=>s.trim()).filter(Boolean);
        return tags.length ? { tags } : {};
      }

      function canonicalizePreferences(p){
        try{
          if (p == null) return '';
          if (typeof p === 'string'){
            const t = p.trim();
            if (!t) return '';
            return t.split(',').map(s=>s.trim().toLowerCase()).filter(Boolean).sort().join('|');
          }
          if (Array.isArray(p)){
            return p.map(x=>String(x).trim().toLowerCase()).filter(Boolean).sort().join('|');
          }
          if (typeof p === 'object'){
            const parts = [];
            for (const [k,v] of Object.entries(p)){
              if (v === true) parts.push(k);
              else if (Array.isArray(v) && v.length) parts.push(`${k}:${v.map(x=>String(x).trim().toLowerCase()).join(',')}`);
              else if (typeof v === 'string' && v.trim()) parts.push(`${k}:${v.trim().toLowerCase()}`);
              else if (typeof v === 'number') parts.push(`${k}:${v}`);
            }
            return parts.map(s=>s.toLowerCase()).sort().join('|');
          }
          return String(p);
        }catch{ return ''; }
      }

      function computeHasUnsaved(){
        if (!initialData) return false;
        const current = {
          name: el.name?.value ?? '',
          email: el.email?.value ?? '',
          address: (()=>{
            // If edit inputs are visible, compute from them; otherwise treat as unchanged view
            if (!el.addressEditGroup || el.addressEditGroup.classList.contains('hidden')) return initialData.address;
            const city = el.addrCity?.value?.trim() || '';
            const postal = el.addrPostal?.value?.trim() || '';
            const street = el.addrStreet?.value?.trim() || '';
            const num = el.addrNumber?.value?.trim() || '';
            const parts = [];
            if (street) parts.push(street + (num?` ${num}`:''));
            if (postal || city) parts.push([postal, city].filter(Boolean).join(' '));
            return parts.join(', ');
          })(),
          preferences: (()=>{
            const base = parsePreferencesInput(el.preferences?.value ?? '');
            const opt = collectOptionalToPrefs();
            return { ...base, ...opt };
          })()
        };
        const normalize = (v) => typeof v === 'string' ? v.trim() : v;
        const prefEqual = canonicalizePreferences(current.preferences) === canonicalizePreferences(initialData.preferences);
        return (
          normalize(current.name) !== normalize(initialData.name) ||
          normalize(current.email) !== normalize(initialData.email) ||
          normalize(current.address) !== normalize(initialData.address) ||
          !prefEqual
        );
      }

  el.editBtn?.addEventListener('click', (e)=>{ e.preventDefault(); setEditMode(true); });
      el.cancelBtn?.addEventListener('click', (e)=>{
        e.preventDefault();
        if (!initialData) return setEditMode(false);
        if (el.name) el.name.value = initialData.name;
        if (el.email) el.email.value = initialData.email;
        if (el.address) el.address.value = initialData.address;
        // clear edit address inputs
        if (el.addrCity) el.addrCity.value = '';
        if (el.addrPostal) el.addrPostal.value = '';
        if (el.addrStreet) el.addrStreet.value = '';
        if (el.addrNumber) el.addrNumber.value = '';
        if (el.preferences) el.preferences.value = formatPreferences(initialData.preferences);
        setEditMode(false);
      });

      async function saveProfile(extraPrefs){
        if (!isEditing) return;
        const parsedPrefs = parsePreferencesInput(el.preferences?.value || '');
        // Build full address from edit inputs if in edit mode
        let fullAddress;
        if (!el.addressEditGroup?.classList.contains('hidden')){
          const city = el.addrCity?.value?.trim();
          const postal = el.addrPostal?.value?.trim();
          const street = el.addrStreet?.value?.trim();
          const num = el.addrNumber?.value?.trim();
          if (street || num || postal || city) {
            // light validations similar to signup
            if (!street || !num || !postal || !city) {
              alert('Please provide your full address: street, number, postal code, and city.');
              return;
            }
            if (!/^[0-9A-Za-z \-]{3,10}$/.test(postal)){
              alert('Please enter a valid postal code.');
              return;
            }
            fullAddress = `${street} ${num}, ${postal} ${city}`;
          }
        }
        const payload = {
          email: el.email?.value?.trim() || undefined,
          address: fullAddress || undefined,
          preferences: (()=>{
            const combined = { ...parsedPrefs, ...collectOptionalToPrefs(), ...(extraPrefs||{}) };
            return Object.keys(combined).length ? combined : undefined;
          })()
        };
        try{
          let res;
          if (token) {
            res = await fetch(`${BACKEND_BASE}/profile`, {
              method: 'PUT',
              headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
              },
              body: JSON.stringify(payload)
            });
          } else {
            res = await window.apiFetch(`/profile`, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
            });
          }
          const body = await res.json().catch(()=>({}));
          dbg.logReq('PUT /profile', { status: res.status, body });
          if (!res.ok) {
            let msg = 'Failed to save';
            if (typeof body?.detail === 'string') msg = body.detail;
            else if (Array.isArray(body?.detail) && body.detail.length){
              msg = body.detail.map(d => d.msg || JSON.stringify(d)).join('\n');
            }
            throw new Error(msg);
          }

          const d = body;
          if (el.headerName) el.headerName.textContent = d.name || '';
          if (el.headerEmail) el.headerEmail.textContent = d.email || '';
          initialData = {
            name: d.name || '',
            email: d.email || '',
            address: d.address || '',
            preferences: d.preferences ?? {}
          };
          if (el.name) el.name.value = initialData.name;
          if (el.email) el.email.value = initialData.email;
          if (el.address) el.address.value = initialData.address;
          // clear edit address inputs after save
          if (el.addrCity) el.addrCity.value = '';
          if (el.addrPostal) el.addrPostal.value = '';
          if (el.addrStreet) el.addrStreet.value = '';
          if (el.addrNumber) el.addrNumber.value = '';
          if (el.preferences) el.preferences.value = formatPreferences(initialData.preferences);
          setOptionalInputs(getOptionalFromPrefs(initialData.preferences));

          setEditMode(false);

          const missing = [];
          if (!d.name) missing.push('name');
          if (!d.email) missing.push('email');
          if (!d.address) missing.push('address');
          if ((function(p){
            if (!p) return true; if (Array.isArray(p)) return p.length===0; if (typeof p==='string') return p.trim().length===0; if (typeof p==='object') return Object.keys(p).length===0; return true;
          })(d.preferences)) missing.push('preferences');
          if (missing.length && el.incompleteBanner){
            el.incompleteBanner.classList.remove('hidden');
            el.incompleteDetails && (el.incompleteDetails.textContent = `Missing: ${missing.join(', ')}`);
          } else if (el.incompleteBanner) {
            el.incompleteBanner.classList.add('hidden');
          }

          // Close onboarding modal if it was open
          el.onboardingModal?.classList.add('hidden');
        }catch(err){
          console.error(err);
          alert(err?.message || 'Could not save your changes.');
        }
      }

  el.saveBtn?.addEventListener('click', (e)=>{ e.preventDefault(); saveProfile(); });

      ['input','change'].forEach(ev=>{
        [el.email, el.preferences, el.kitchenAvailable, el.mainCoursePossible, el.dietary, el.fieldOfStudy, el.addrCity, el.addrPostal, el.addrStreet, el.addrNumber].forEach(inp=>{
          inp?.addEventListener(ev, ()=>{
            if (!isEditing) return;
            hasUnsaved = computeHasUnsaved();
            showUnsaved(hasUnsaved);
            // dynamic: show/hide main course
            if (inp === el.kitchenAvailable && el.mainCourseGroup) {
              el.mainCourseGroup.classList.toggle('hidden', el.kitchenAvailable.value !== 'yes');
            }
          });
        });
      });

      window.addEventListener('beforeunload', (e)=>{
        if (isEditing && hasUnsaved){
          e.preventDefault();
          e.returnValue = '';
        }
      });

      document.getElementById('logout-btn').addEventListener('click', async () => {
        try {
          await window.apiFetch(`/logout`, { method: 'POST' });
        } catch {}
        if (window.auth && window.auth.deleteCookie) {
          window.auth.deleteCookie('dh_token');
        } else {
          document.cookie = `dh_token=; Path=/; SameSite=Strict${location.protocol==='https:'?'; Secure':''}; Expires=Thu, 01 Jan 1970 00:00:00 GMT`;
        }
        window.location.href = 'login.html';
      });

      // Onboarding modal actions
      el.onboardingSkip?.addEventListener('click', async (e)=>{
        e.preventDefault();
        // Mark onboarding_seen in preferences and save silently
        try {
          // we don't need edit mode to save this flag
          const res = await window.apiFetch(`/profile`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ preferences: { ...(initialData?.preferences||{}), onboarding_seen: true } })
          });
          const body = await res.json().catch(()=>({}));
          dbg.logReq('PUT /profile (onboarding skip)', { status: res.status, body });
        } catch {}
        el.onboardingModal?.classList.add('hidden');
      });
      el.onboardingFill?.addEventListener('click', (e)=>{
        e.preventDefault();
        el.onboardingModal?.classList.add('hidden');
        setEditMode(true);
      });
    })();
  });
})();

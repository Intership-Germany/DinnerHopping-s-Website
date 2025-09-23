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
        preferences: document.getElementById('profile-preferences'),
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
      };

      let initialData = null;
      let isEditing = false;
      let hasUnsaved = false;

      function formatPreferences(pref){
        try {
          if (!pref) return '';
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
        if (isEmptyPreferences(data.preferences)) missing.push('preferences');
        if (missing.length && el.incompleteBanner){
          el.incompleteBanner.classList.remove('hidden');
          if (el.incompleteDetails) {
            el.incompleteDetails.textContent = `Missing: ${missing.join(', ')}`;
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
        toggle(el.address);
        toggle(el.preferences);

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
            return parsed;
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
          address: el.address?.value ?? '',
          preferences: parsePreferencesInput(el.preferences?.value ?? '')
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
        if (el.preferences) el.preferences.value = formatPreferences(initialData.preferences);
        setEditMode(false);
      });

      async function saveProfile(){
        if (!isEditing) return;
        const parsedPrefs = parsePreferencesInput(el.preferences?.value || '');
        const payload = {
          email: el.email?.value?.trim() || undefined,
          address: el.address?.value?.trim() || undefined,
          preferences: (Object.keys(parsedPrefs).length ? parsedPrefs : undefined)
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
          if (el.preferences) el.preferences.value = formatPreferences(initialData.preferences);

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
        }catch(err){
          console.error(err);
          alert(err?.message || 'Could not save your changes.');
        }
      }

      el.saveBtn?.addEventListener('click', (e)=>{ e.preventDefault(); saveProfile(); });

      ['input','change'].forEach(ev=>{
        [el.email, el.address, el.preferences].forEach(inp=>{
          inp?.addEventListener(ev, ()=>{
            if (!isEditing) return;
            hasUnsaved = computeHasUnsaved();
            showUnsaved(hasUnsaved);
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
    })();
  });
})();

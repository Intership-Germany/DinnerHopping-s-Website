// Profile page script moved under pages directory.
// NOTE: Minimal adaptation: apiFetch / initCsrf now via window.dh namespace (backwards compatibility kept).
(function () {
  document.addEventListener('DOMContentLoaded', () => {
    (async function init() {
      if (window.auth && typeof window.auth.ensureBanner === 'function') window.auth.ensureBanner();
      // DOM refs
      const el = {
        headerName: document.getElementById('header-name'),
        headerEmail: document.getElementById('header-email'),
        firstName: document.getElementById('profile-firstname'),
        lastName: document.getElementById('profile-lastname'),
        email: document.getElementById('profile-email'),
        address: document.getElementById('profile-address'),
        fullNameView: document.getElementById('profile-fullname'),
        firstNameLabel: document.querySelector('label[for="profile-firstname"]'),
        lastNameLabel: document.querySelector('label[for="profile-lastname"]'),
        fullNameLabel: document.querySelector('label[for="profile-fullname"]'),
        addressEditGroup: document.getElementById('address-edit-group'),
        addrCity: document.getElementById('profile-city'),
        addrPostal: document.getElementById('profile-postal'),
        addrStreet: document.getElementById('profile-street'),
        addrNumber: document.getElementById('profile-number'),
        preferences: document.getElementById('profile-preferences'),
        kitchenAvailable: document.getElementById('kitchen-available'),
        mainCoursePossible: document.getElementById('main-course-possible'),
        defaultDietary: document.getElementById('default-dietary'),
        fieldOfStudy: document.getElementById('field-of-study'),
        mainCourseGroup: document.getElementById('main-course-group'),
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
        logoutBtn: document.getElementById('logout-btn'),
      };
      let initial = null;
      let isEditing = false;
      let hasUnsaved = false;
      function setHidden(node, h) {
        if (!node) return;
        node.classList.toggle('hidden', !!h);
      }
      function disableInput(inp, d) {
        if (!inp) return;
        if (d) {
          inp.setAttribute('disabled', '');
          inp.classList.add('bg-gray-50');
          inp.classList.remove('bg-white');
        } else {
          inp.removeAttribute('disabled');
          inp.classList.remove('bg-gray-50');
          inp.classList.add('bg-white');
        }
      }
      function fullNameOf(u) {
        return ((u?.first_name || '') + ' ' + (u?.last_name || '')).trim() || u?.name || '';
      }
      function formatPreferences(pref) {
        try {
          if (!pref) return '';
          if (Array.isArray(pref)) return pref.join(', ');
          if (typeof pref === 'string') return pref;
          if (typeof pref === 'object') {
            if (Array.isArray(pref.tags)) return pref.tags.join(', ');
            const parts = [];
            for (const [k, v] of Object.entries(pref)) {
              if (k === 'tags') continue;
              if (v === true) parts.push(k);
              else if (Array.isArray(v)) parts.push(`${k}: ${v.join(', ')}`);
              else if (typeof v === 'string' && v.trim()) parts.push(`${k}: ${v}`);
              else if (typeof v === 'number') parts.push(`${k}: ${v}`);
            }
            return parts.join(', ');
          }
          return String(pref);
        } catch {
          return '';
        }
      }
      function parsePreferencesInput(text) {
        if (!text || !text.trim()) return {};
        try {
          const parsed = JSON.parse(text);
          if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
          if (Array.isArray(parsed)) return { tags: parsed };
        } catch {}
        const tags = text
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean);
        return tags.length ? { tags } : {};
      }
      function formatAddressStruct(a) {
        if (!a || typeof a !== 'object') return typeof a === 'string' ? a : '';
        const left = a.street ? a.street + (a.street_no ? ` ${a.street_no}` : '') : '';
        const right = [a.postal_code, a.city].filter(Boolean).join(' ');
        return [left, right].filter(Boolean).join(', ');
      }
      function computeViewAddress() {
        const city = el.addrCity?.value?.trim() || '';
        const postal = el.addrPostal?.value?.trim() || '';
        const street = el.addrStreet?.value?.trim() || '';
        const num = el.addrNumber?.value?.trim() || '';
        const left = street ? street + (num ? ` ${num}` : '') : '';
        const right = [postal, city].filter(Boolean).join(' ');
        return [left, right].filter(Boolean).join(', ');
      }
      // Initialize shared address autocomplete component (replaces former inline placeholder)
      if (window.dh?.components?.initAddressAutocomplete) {
        window.dh.components.initAddressAutocomplete({
          mode: 'profile',
          selectors: {
            street: '#profile-street',
            number: '#profile-number',
            postal: '#profile-postal',
            city: '#profile-city',
          },
        });
      }
      function canonicalizePrefs(p) {
        try {
          if (!p) return '';
          if (typeof p === 'string') return p.trim().toLowerCase();
          if (Array.isArray(p))
            return p
              .map((x) => String(x).trim().toLowerCase())
              .sort()
              .join('|');
          if (typeof p === 'object') {
            const parts = [];
            for (const [k, v] of Object.entries(p)) {
              if (v === true) parts.push(k);
              else if (Array.isArray(v))
                parts.push(`${k}:${v.map((x) => String(x).trim().toLowerCase()).join(',')}`);
              else if (typeof v === 'string' && v.trim())
                parts.push(`${k}:${v.trim().toLowerCase()}`);
              else if (typeof v === 'number') parts.push(`${k}:${v}`);
            }
            return parts.sort().join('|');
          }
          return String(p);
        } catch {
          return '';
        }
      }
      function setOptionalUI(from) {
        const d = from || {};
        if (el.kitchenAvailable)
          el.kitchenAvailable.value =
            d.kitchen_available === true ? 'yes' : d.kitchen_available === false ? 'no' : '';
        const kitchenYes = el.kitchenAvailable && el.kitchenAvailable.value === 'yes';
        if (el.mainCoursePossible)
          el.mainCoursePossible.value =
            d.main_course_possible === true ? 'yes' : d.main_course_possible === false ? 'no' : '';
        setHidden(el.mainCourseGroup, !kitchenYes);
        if (el.defaultDietary) el.defaultDietary.value = d.default_dietary_preference || '';
        if (el.fieldOfStudy) el.fieldOfStudy.value = d.field_of_study || '';
      }
      function collectOptionalPayload() {
        const out = {};
        const k = el.kitchenAvailable?.value || '';
        const m = el.mainCoursePossible?.value || '';
        const d = el.defaultDietary?.value || '';
        const f = el.fieldOfStudy?.value?.trim() || '';
        if (k === 'yes') out.kitchen_available = true;
        else if (k === 'no') out.kitchen_available = false;
        if (k === 'yes') {
          if (m === 'yes') out.main_course_possible = true;
          else if (m === 'no') out.main_course_possible = false;
        }
        if (['vegan', 'vegetarian', 'omnivore'].includes(d)) out.default_dietary_preference = d;
        if (f) out.field_of_study = f;
        return out;
      }
      function getMissingBasics(data) {
        const missing = [];
        if (!data.first_name) missing.push('first name');
        if (!data.last_name) missing.push('last name');
        if (!data.email) missing.push('email');
        if (!data.address) missing.push('address');
        if (
          !data.preferences ||
          (typeof data.preferences === 'object' && Object.keys(data.preferences).length === 0)
        )
          missing.push('preferences');
        return missing;
      }
      function getMissingOptional(data) {
        const opt = [];
        if (typeof data.kitchen_available !== 'boolean') opt.push('kitchen available');
        if (data.kitchen_available === true && typeof data.main_course_possible !== 'boolean')
          opt.push('main course');
        if (!data.default_dietary_preference) opt.push('dietary');
        if (!data.field_of_study) opt.push('field of study');
        return opt;
      }
      function updateBanners(data) {
        const missing = getMissingBasics(data);
        if (missing.length && el.incompleteBanner) {
          setHidden(el.incompleteBanner, false);
          if (el.incompleteDetails)
            el.incompleteDetails.textContent = 'Missing: ' + missing.join(', ');
        } else setHidden(el.incompleteBanner, true);
        const showOnboarding = data.profile_prompt_pending && !data.optional_profile_completed;
        if (showOnboarding && el.onboardingModal) {
          const optMissing = getMissingOptional(data);
          if (el.onboardingMissing) el.onboardingMissing.textContent = optMissing.join(', ');
          setHidden(el.onboardingModal, false);
        } else setHidden(el.onboardingModal, true);
      }
      function handleProfileData(data) {
        const fullName = fullNameOf(data);
        if (el.headerName) el.headerName.textContent = fullName;
        if (el.headerEmail) el.headerEmail.textContent = data.email || '';
        if (el.firstName) el.firstName.value = data.first_name || '';
        if (el.lastName) el.lastName.value = data.last_name || '';
        if (el.email) el.email.value = data.email || '';
        if (el.fullNameView) el.fullNameView.value = fullName;
        const addr = data.address && typeof data.address === 'object' ? data.address : null;
        if (el.address) el.address.value = formatAddressStruct(addr || data.address);
        if (addr) {
          if (el.addrStreet) el.addrStreet.value = addr.street || '';
          if (el.addrNumber) el.addrNumber.value = addr.street_no || '';
          if (el.addrPostal) el.addrPostal.value = addr.postal_code || '';
          if (el.addrCity) el.addrCity.value = addr.city || '';
        }
        if (el.preferences) el.preferences.value = formatPreferences(data.preferences);
        setOptionalUI({
          kitchen_available: data.kitchen_available,
          main_course_possible: data.main_course_possible,
          default_dietary_preference: data.default_dietary_preference,
          field_of_study: data.field_of_study,
        });
        updateBanners(data);
        initial = {
          first_name: data.first_name || '',
          last_name: data.last_name || '',
          email: data.email || '',
          address: data.address || '',
          preferences: data.preferences || {},
          optional: {
            kitchen_available: data.kitchen_available,
            main_course_possible: data.main_course_possible,
            default_dietary_preference: data.default_dietary_preference,
            field_of_study: data.field_of_study,
          },
        };
        setHidden(el.skeletonHeader, true);
        setHidden(el.skeletonMain, true);
        setHidden(el.profileHeader, false);
        setHidden(el.profileMain, false);
        setEditMode(false);
        return data;
      }
      function computeUnsaved() {
        if (!initial) return false;
        const current = {
          first_name: el.firstName?.value?.trim() || '',
          last_name: el.lastName?.value?.trim() || '',
          email: el.email?.value?.trim() || '',
          address:
            el.addressEditGroup && !el.addressEditGroup.classList.contains('hidden')
              ? computeViewAddress()
              : formatAddressStruct(initial.address),
          preferences: parsePreferencesInput(el.preferences?.value || ''),
          optional: collectOptionalPayload(),
        };
        const sameFirst = current.first_name === (initial.first_name || '');
        const sameLast = current.last_name === (initial.last_name || '');
        const sameEmail = current.email === (initial.email || '');
        const sameAddr = current.address === formatAddressStruct(initial.address || '');
        const samePrefs =
          canonicalizePrefs(current.preferences) === canonicalizePrefs(initial.preferences);
        const sameOpt =
          canonicalizePrefs(current.optional) === canonicalizePrefs(initial.optional || {});
        return !(sameFirst && sameLast && sameEmail && sameAddr && samePrefs && sameOpt);
      }
      function setEditMode(on) {
        isEditing = !!on;
        disableInput(el.email, true);
        disableInput(el.firstName, !isEditing);
        disableInput(el.lastName, !isEditing);
        if (el.fullNameView) setHidden(el.fullNameView, isEditing);
        if (el.fullNameLabel) setHidden(el.fullNameLabel, isEditing);
        if (el.firstName) setHidden(el.firstName, !isEditing);
        if (el.lastName) setHidden(el.lastName, !isEditing);
        if (el.firstNameLabel) setHidden(el.firstNameLabel, !isEditing);
        if (el.lastNameLabel) setHidden(el.lastNameLabel, !isEditing);
        setHidden(el.address, !!isEditing);
        setHidden(el.addressEditGroup, !isEditing);
        if (isEditing && initial && initial.address && typeof initial.address === 'object') {
          if (el.addrStreet) el.addrStreet.value = initial.address.street || '';
          if (el.addrNumber) el.addrNumber.value = initial.address.street_no || '';
          if (el.addrPostal) el.addrPostal.value = initial.address.postal_code || '';
          if (el.addrCity) el.addrCity.value = initial.address.city || '';
        }
        [
          'addrCity',
          'addrPostal',
          'addrStreet',
          'addrNumber',
          'preferences',
          'kitchenAvailable',
          'mainCoursePossible',
          'defaultDietary',
          'fieldOfStudy',
        ].forEach((k) => disableInput(el[k], !isEditing));
        setHidden(el.editBtn, isEditing);
        setHidden(el.editActions, !isEditing);
        setHidden(el.unsavedBanner, true);
        hasUnsaved = false;
      }
      function getDhToken() {
        // Prefer localStorage token (set during login) to support cross-origin bearer mode when cookies are blocked by CORS.
        try {
          const ls = localStorage.getItem('dh_access_token');
          if (ls) return ls;
        } catch {}
        try {
          if (window.auth && typeof window.auth.getCookie === 'function')
            return window.auth.getCookie('dh_token');
          const m = document.cookie.match(/(?:^|; )dh_token=([^;]*)/);
          return m && m[1] ? decodeURIComponent(m[1]) : null;
        } catch {
          return null;
        }
      }
      async function loadProfile() {
        try {
          await (window.dh?.initCsrf
            ? window.dh.initCsrf()
            : window.initCsrf
              ? window.initCsrf()
              : Promise.resolve());
        } catch {}
        const bearer = getDhToken();
        const baseOpts = bearer
          ? { headers: { Authorization: `Bearer ${bearer}` }, credentials: 'omit' }
          : {};
        let res = await (window.dh?.apiFetch
          ? window.dh.apiFetch('/profile', baseOpts)
          : window.apiFetch('/profile', baseOpts));
        let data = await res.json().catch(() => ({}));
        if (!res.ok) {
          if (!bearer) {
            const b2 = getDhToken();
            if (b2) {
              const res2 = await (window.dh?.apiFetch
                ? window.dh.apiFetch('/profile', {
                    headers: { Authorization: `Bearer ${b2}` },
                    credentials: 'omit',
                  })
                : window.apiFetch('/profile', {
                    headers: { Authorization: `Bearer ${b2}` },
                    credentials: 'omit',
                  }));
              const d2 = await res2.json().catch(() => ({}));
              if (res2.ok) return handleProfileData(d2);
            }
          }
          if (typeof window.handleUnauthorized === 'function')
            window.handleUnauthorized({ autoRedirect: true, delayMs: 1000 });
          else window.location.href = 'login.html';
          return null;
        }
        return handleProfileData(data);
      }
      function buildSavePayload() {
        const payload = {};
        const fName = el.firstName?.value?.trim();
        const lName = el.lastName?.value?.trim();
        if (fName !== initial.first_name) payload.first_name = fName;
        if (lName !== initial.last_name) payload.last_name = lName;
        const newEmail = el.email?.value?.trim();
        if (newEmail && newEmail !== initial.email) payload.email = newEmail;
        const city = el.addrCity?.value?.trim();
        const postal = el.addrPostal?.value?.trim();
        const street = el.addrStreet?.value?.trim();
        const num = el.addrNumber?.value?.trim();
        const anyAddrEdited = !!(city || postal || street || num);
        if (anyAddrEdited) {
          if (!street || !num || !postal || !city)
            throw new Error(
              'Please provide your full address: street, number, postal code, and city.'
            );
          if (!/^[0-9A-Za-z \-]{3,10}$/.test(postal))
            throw new Error('Please enter a valid postal code.');
          Object.assign(payload, { street, street_no: num, postal_code: postal, city });
        }
        const prefs = parsePreferencesInput(el.preferences?.value || '');
        if (prefs && Object.keys(prefs).length) payload.preferences = prefs;
        Object.assign(payload, collectOptionalPayload());
        return payload;
      }
      async function saveProfile() {
        if (!isEditing) return;
        try {
          const payload = buildSavePayload();
          const bearer = getDhToken();
          const headers = { 'Content-Type': 'application/json' };
          if (bearer) headers.Authorization = `Bearer ${bearer}`;
          const res = await (window.dh?.apiFetch
            ? window.dh.apiFetch('/profile', {
                method: 'PUT',
                headers,
                body: JSON.stringify(payload),
                credentials: bearer ? 'omit' : undefined,
              })
            : window.apiFetch('/profile', {
                method: 'PUT',
                headers,
                body: JSON.stringify(payload),
                credentials: bearer ? 'omit' : undefined,
              }));
          const body = await res.json().catch(() => ({}));
          if (!res.ok) {
            const msg =
              typeof body.detail === 'string'
                ? body.detail
                : Array.isArray(body.detail)
                  ? body.detail.map((d) => d.msg).join(' ')
                  : 'Failed to save profile';
            throw new Error(msg);
          }
          await loadProfile();
          ['addrCity', 'addrPostal', 'addrStreet', 'addrNumber'].forEach((id) => {
            if (el[id]) el[id].value = '';
          });
          setEditMode(false);
        } catch (e) {
          console.error(e);
          alert(e?.message || 'Could not save your changes.');
        }
      }
      el.editBtn &&
        el.editBtn.addEventListener('click', (e) => {
          e.preventDefault();
          setEditMode(true);
        });
      el.cancelBtn &&
        el.cancelBtn.addEventListener('click', (e) => {
          e.preventDefault();
          if (initial) {
            if (el.firstName) el.firstName.value = initial.first_name || '';
            if (el.lastName) el.lastName.value = initial.last_name || '';
            if (el.email) el.email.value = initial.email || '';
            if (el.fullNameView)
              el.fullNameView.value = (
                (initial.first_name || '') +
                ' ' +
                (initial.last_name || '')
              ).trim();
            if (el.address) el.address.value = formatAddressStruct(initial.address);
            if (el.preferences) el.preferences.value = formatPreferences(initial.preferences);
            setOptionalUI(initial.optional);
            if (initial.address && typeof initial.address === 'object') {
              if (el.addrStreet) el.addrStreet.value = initial.address.street || '';
              if (el.addrNumber) el.addrNumber.value = initial.address.street_no || '';
              if (el.addrPostal) el.addrPostal.value = initial.address.postal_code || '';
              if (el.addrCity) el.addrCity.value = initial.address.city || '';
            } else
              ['addrStreet', 'addrNumber', 'addrPostal', 'addrCity'].forEach((id) => {
                if (el[id]) el[id].value = '';
              });
          }
          setEditMode(false);
        });
      el.saveBtn &&
        el.saveBtn.addEventListener('click', (e) => {
          e.preventDefault();
          saveProfile();
        });
      ['input', 'change'].forEach((type) => {
        [
          el.firstName,
          el.lastName,
          el.email,
          el.preferences,
          el.kitchenAvailable,
          el.mainCoursePossible,
          el.defaultDietary,
          el.fieldOfStudy,
          el.addrCity,
          el.addrPostal,
          el.addrStreet,
          el.addrNumber,
        ].forEach((inp) => {
          inp &&
            inp.addEventListener(type, () => {
              if (!isEditing) return;
              if (inp === el.kitchenAvailable)
                setHidden(el.mainCourseGroup, el.kitchenAvailable.value !== 'yes');
              hasUnsaved = computeUnsaved();
              setHidden(el.unsavedBanner, !hasUnsaved);
            });
        });
      });
      window.addEventListener('beforeunload', (e) => {
        if (isEditing && hasUnsaved) {
          e.preventDefault();
          e.returnValue = '';
        }
      });
      el.logoutBtn &&
        el.logoutBtn.addEventListener('click', async () => {
          try {
            const bearer = getDhToken();
            const headers = bearer ? { Authorization: `Bearer ${bearer}` } : {};
            await (window.dh?.apiFetch
              ? window.dh.apiFetch('/logout', {
                  method: 'POST',
                  headers,
                  credentials: bearer ? 'omit' : undefined,
                })
              : window.apiFetch('/logout', {
                  method: 'POST',
                  headers,
                  credentials: bearer ? 'omit' : undefined,
                }));
          } catch {}
          try {
            if (window.auth && window.auth.deleteCookie) window.auth.deleteCookie('dh_token');
          } catch {}
          window.location.href = 'login.html';
        });
      el.onboardingSkip &&
        el.onboardingSkip.addEventListener('click', async (e) => {
          e.preventDefault();
          try {
            const bearer = getDhToken();
            const headers = bearer
              ? { 'Content-Type': 'application/json', Authorization: `Bearer ${bearer}` }
              : { 'Content-Type': 'application/json' };
            await (window.dh?.apiFetch
              ? window.dh.apiFetch('/profile/optional', {
                  method: 'PATCH',
                  headers,
                  body: JSON.stringify({ skip: true }),
                  credentials: bearer ? 'omit' : undefined,
                })
              : window.apiFetch('/profile/optional', {
                  method: 'PATCH',
                  headers,
                  body: JSON.stringify({ skip: true }),
                  credentials: bearer ? 'omit' : undefined,
                }));
          } catch {}
          setHidden(el.onboardingModal, true);
        });
      el.onboardingFill &&
        el.onboardingFill.addEventListener('click', (e) => {
          e.preventDefault();
          setHidden(el.onboardingModal, true);
          setEditMode(true);
        });
      await loadProfile();
    })();
  });
})();

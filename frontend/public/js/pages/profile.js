/*
	Profile page script
	-------------------
	Purpose: Manage viewing and editing of the user profile via backend endpoints (/profile, /profile/optional, /logout),
	using apiFetch (cookie + CSRF) with a legacy dh_token (Bearer) fallback when present.

	Highlights:
	- Load profile data and populate the UI (structured address fields and preferences).
	- Edit mode with unsaved-changes detection and a warning banner.
	- Save changes (PUT /profile), optional onboarding (PATCH /profile/optional), and logout.
	- Refactored to group helpers and avoid duplication (populate, banners, initial snapshot).

*/
(function () {
  document.addEventListener('DOMContentLoaded', () => {
    (async function init() {
      // DOM refs (centralized to avoid repeated selectors)
      const el = {
        headerName: document.getElementById('header-name'),
        headerEmail: document.getElementById('header-email'),
        firstName: document.getElementById('profile-firstname'),
        lastName: document.getElementById('profile-lastname'),
        email: document.getElementById('profile-email'),
  phone: document.getElementById('profile-phone'),
        address: document.getElementById('profile-address'),
        fullNameView: document.getElementById('profile-fullname'),
        // Name labels
        firstNameLabel: document.querySelector('label[for="profile-firstname"]'),
        lastNameLabel: document.querySelector('label[for="profile-lastname"]'),
        fullNameLabel: document.querySelector('label[for="profile-fullname"]'),
        // Address edit inputs
        addressEditGroup: document.getElementById('address-edit-group'),
        addrCity: document.getElementById('profile-city'),
        addrPostal: document.getElementById('profile-postal'),
        addrStreet: document.getElementById('profile-street'),
        addrNumber: document.getElementById('profile-number'),
        // Allergies
        allergiesGrid: document.getElementById('allergies-grid'),
        allergyCheckboxes: [], // Will be populated dynamically
        allergyOtherCheckbox: document.getElementById('allergy-other-checkbox'),
        allergyOtherInput: document.getElementById('allergy-other-input'),
        // Optional profile fields
        kitchenAvailable: document.getElementById('kitchen-available'),
        mainCoursePossible: document.getElementById('main-course-possible'),
        defaultDietary: document.getElementById('default-dietary'),
        fieldOfStudy: document.getElementById('field-of-study'),
        mainCourseGroup: document.getElementById('main-course-group'),
        // Actions / banners
        editBtn: document.getElementById('edit-btn'),
        saveBtn: document.getElementById('save-btn'),
        cancelBtn: document.getElementById('cancel-btn'),
        editActions: document.getElementById('edit-actions'),
        incompleteBanner: document.getElementById('incomplete-banner'),
        incompleteDetails: document.getElementById('incomplete-details'),
        unsavedBanner: document.getElementById('unsaved-banner'),
        // Skeletons vs content
        skeletonHeader: document.getElementById('skeleton-header'),
        skeletonMain: document.getElementById('skeleton-main'),
        profileHeader: document.getElementById('profile-header'),
        profileMain: document.getElementById('profile-main'),
        // Onboarding modal
        onboardingModal: document.getElementById('onboarding-modal'),
        onboardingMissing: document.getElementById('onboarding-missing'),
        onboardingSkip: document.getElementById('onboarding-skip'),
        onboardingFill: document.getElementById('onboarding-fill'),
        // Logout
        logoutBtn: document.getElementById('logout-btn'),
      };

      // State
      let initial = null; // snapshot of initial data for change detection (persisted fields only)
      let isEditing = false;
      let hasUnsaved = false;

      // ---------- UI helpers ----------
      /** Toggle visibility by adding/removing the 'hidden' class */
      function setHidden(node, hidden) {
        if (!node) return;
        node.classList.toggle('hidden', !!hidden);
      }

      /** (Dis)able an input and sync a simple background style between view/edit */
      function disableInput(inp, disabled) {
        if (!inp) return;
        if (disabled) {
          inp.setAttribute('disabled', '');
          inp.classList.add('bg-gray-50');
          inp.classList.remove('bg-white');
        } else {
          inp.removeAttribute('disabled');
          inp.classList.remove('bg-gray-50');
          inp.classList.add('bg-white');
        }
      }

      /** Join first+last name, fallback to name */
      function fullNameOf(u) {
        return ((u?.first_name || '') + ' ' + (u?.last_name || '')).trim() || u?.name || '';
      }

      /** Load valid allergies from API and create dynamic checkboxes */
      async function loadAllergies() {
        try {
          const response = await window.dh.apiFetch('/allergies');
          const data = await response.json();

          if (!data.allergies || !Array.isArray(data.allergies)) {
            console.error('Invalid allergies data received from API');
            return;
          }

          // Clear existing checkboxes
          el.allergiesGrid.innerHTML = '';
          el.allergyCheckboxes = [];

          // Create checkboxes for each valid allergy
          data.allergies.forEach((allergy) => {
            const label = document.createElement('label');
            label.className = 'flex items-center space-x-2';

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.id = `allergy-${allergy}`;
            checkbox.name = 'allergies';
            checkbox.value = allergy;
            checkbox.className = 'rounded border-gray-300 text-[#f46f47] focus:ring-[#f46f47]';
            checkbox.disabled = true; // Will be enabled in edit mode

            const span = document.createElement('span');
            span.className = 'text-sm';
            span.textContent = allergy.charAt(0).toUpperCase() + allergy.slice(1);

            label.appendChild(checkbox);
            label.appendChild(span);
            el.allergiesGrid.appendChild(label);

            // Keep reference to checkbox
            el.allergyCheckboxes.push(checkbox);
          });

          // Store valid allergies for validation
          window.validAllergies = data.allergies;
        } catch (error) {
          console.error('Failed to load allergies:', error);
          // Fallback to hardcoded list if API fails
          window.validAllergies = [
            'nuts',
            'shellfish',
            'dairy',
            'eggs',
            'gluten',
            'soy',
            'fish',
            'sesame',
          ];
          createFallbackAllergies();
        }
      }

      /** Create fallback allergies if API fails */
      function createFallbackAllergies() {
        const fallbackAllergies = [
          'nuts',
          'shellfish',
          'dairy',
          'eggs',
          'gluten',
          'soy',
          'fish',
          'sesame',
        ];
        el.allergiesGrid.innerHTML = '';
        el.allergyCheckboxes = [];

        fallbackAllergies.forEach((allergy) => {
          const label = document.createElement('label');
          label.className = 'flex items-center space-x-2';

          const checkbox = document.createElement('input');
          checkbox.type = 'checkbox';
          checkbox.id = `allergy-${allergy}`;
          checkbox.name = 'allergies';
          checkbox.value = allergy;
          checkbox.className = 'rounded border-gray-300 text-[#f46f47] focus:ring-[#f46f47]';
          checkbox.disabled = true;

          const span = document.createElement('span');
          span.className = 'text-sm';
          span.textContent = allergy.charAt(0).toUpperCase() + allergy.slice(1);

          label.appendChild(checkbox);
          label.appendChild(span);
          el.allergiesGrid.appendChild(label);

          el.allergyCheckboxes.push(checkbox);
        });
      }

      /** Set allergies from backend data */
      function setAllergies(allergiesList) {
        // Clear all checkboxes first
        el.allergyCheckboxes.forEach((checkbox) => {
          checkbox.checked = false;
        });
        el.allergyOtherCheckbox.checked = false;
        el.allergyOtherInput.value = '';
        el.allergyOtherInput.classList.add('hidden');

        if (!allergiesList || !Array.isArray(allergiesList)) return;

        const knownAllergies = window.validAllergies || [
          'nuts',
          'shellfish',
          'dairy',
          'eggs',
          'gluten',
          'soy',
          'fish',
          'sesame',
        ];
        const otherAllergies = [];

        allergiesList.forEach((allergy) => {
          if (knownAllergies.includes(allergy)) {
            const checkbox = document.getElementById(`allergy-${allergy}`);
            if (checkbox) checkbox.checked = true;
          } else {
            otherAllergies.push(allergy);
          }
        });

        // Handle "other" allergies
        if (otherAllergies.length > 0) {
          el.allergyOtherCheckbox.checked = true;
          el.allergyOtherInput.value = otherAllergies.join(', ');
          el.allergyOtherInput.classList.remove('hidden');
        }
      }

      /** Get current allergies from UI */
      function getAllergies() {
        const allergies = [];

        // Get checked standard allergies
        el.allergyCheckboxes.forEach((checkbox) => {
          if (checkbox.checked) {
            allergies.push(checkbox.value);
          }
        });

        // Get "other" allergies
        if (el.allergyOtherCheckbox.checked && el.allergyOtherInput.value.trim()) {
          const otherAllergies = el.allergyOtherInput.value
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean);
          allergies.push(...otherAllergies);
        }

        return allergies;
      }

      /** Format a structured address into a short readable string */
      function formatAddressStruct(a) {
        if (!a || typeof a !== 'object') return typeof a === 'string' ? a : '';
        const left = a.street ? a.street + (a.street_no ? ` ${a.street_no}` : '') : '';
        const right = [a.postal_code, a.city].filter(Boolean).join(' ');
        return [left, right].filter(Boolean).join(', ');
      }
function normalizePhoneInput(value) {
        if (!value) return '';
        let normalized = String(value).trim().replace(/[^0-9+]/g, '');
        if (!normalized) return '';
        if (normalized.startsWith('+')) {
          normalized = '+' + normalized.slice(1).replace(/\+/g, '');
        } else {
          normalized = normalized.replace(/\+/g, '');
        }
        const digits = normalized.startsWith('+') ? normalized.slice(1) : normalized;
        if (!/^[0-9]+$/.test(digits) || digits.length < 6)
          throw new Error('Please enter a valid phone number with at least 6 digits.');
        return normalized;
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

      // ---------- Address autocomplete (Pelias) ----------
      (function setupAddressAutocomplete() {
        const streetEl = document.getElementById('profile-street');
        const numEl = document.getElementById('profile-number');
        const postalEl = document.getElementById('profile-postal');
        const cityEl = document.getElementById('profile-city');
        if (!streetEl || !numEl || !postalEl || !cityEl) return;

        const peliasBase = 'https://pelias.cephlabs.de/v1';

        function ensureRelativeParent(el) {
          if (!el?.parentElement) return;
          const p = el.parentElement;
          if (!p.classList.contains('relative')) p.classList.add('relative');
        }

        function createDropdown(anchorEl) {
          ensureRelativeParent(anchorEl);
          const dd = document.createElement('div');
          dd.className =
            'absolute z-20 left-0 right-0 mt-1 bg-white border rounded-md shadow max-h-60 overflow-auto hidden';
          anchorEl.parentElement.appendChild(dd);
          return dd;
        }

        const streetDD = createDropdown(streetEl);
        const cityDD = createDropdown(cityEl);
        const postalDD = createDropdown(postalEl);

        function hideDD(dd) {
          dd && dd.classList.add('hidden');
        }
        function showDD(dd) {
          dd && dd.classList.remove('hidden');
        }
        function clearDD(dd) {
          if (dd) dd.innerHTML = '';
        }

        function renderDD(dd, items, onPick) {
          clearDD(dd);
          if (!items || !items.length) {
            hideDD(dd);
            return;
          }
          items.forEach((f) => {
            const p = f.properties || {};
            const div = document.createElement('div');
            div.className = 'px-2 py-1.5 hover:bg-gray-100 cursor-pointer text-sm';
            div.textContent = p.label || p.name || 'Unknown';
            div.addEventListener('click', () => {
              onPick && onPick(f);
              hideDD(dd);
            });
            dd.appendChild(div);
          });
          showDD(dd);
        }

        async function fetchPelias(path) {
          try {
            const res = await fetch(path, { headers: { Accept: 'application/json' } });
            if (!res.ok) return [];
            const json = await res.json();
            return Array.isArray(json?.features) ? json.features : [];
          } catch {
            return [];
          }
        }

        function setFromFeature(f) {
          const p = f?.properties || {};
          // Try to map typical Pelias props safely without overwriting good values with undefined
          if (p.street || p.name) streetEl.value = p.street || p.name || streetEl.value;
          if (p.housenumber) numEl.value = p.housenumber;
          if (p.postalcode) postalEl.value = p.postalcode;
          const cityVal = p.locality || p.localadmin || p.county || p.region;
          if (cityVal) cityEl.value = cityVal;
          // Trigger change detection and update the read-only composed address
          const evt = new Event('input', { bubbles: true });
          streetEl.dispatchEvent(evt);
          postalEl.dispatchEvent(evt);
          cityEl.dispatchEvent(evt);
        }

        let streetDeb, cityDeb, postalDeb;
        streetEl.addEventListener('input', () => {
          if (!isEditing) {
            hideDD(streetDD);
            return;
          }
          clearTimeout(streetDeb);
          hideDD(streetDD);
          const q = streetEl.value.trim();
          if (q.length < 3) {
            return;
          }
          streetDeb = setTimeout(async () => {
            const params = new URLSearchParams({
              text: [q, cityEl.value.trim(), postalEl.value.trim()].filter(Boolean).join(' '),
            });
            const features = await fetchPelias(`${peliasBase}/autocomplete?${params.toString()}`);
            renderDD(streetDD, features.slice(0, 8), setFromFeature);
          }, 250);
        });

        cityEl.addEventListener('input', () => {
          if (!isEditing) {
            hideDD(cityDD);
            return;
          }
          clearTimeout(cityDeb);
          hideDD(cityDD);
          const q = cityEl.value.trim();
          if (q.length < 2) {
            return;
          }
          cityDeb = setTimeout(async () => {
            const params = new URLSearchParams({ text: q });
            const features = await fetchPelias(`${peliasBase}/autocomplete?${params.toString()}`);
            renderDD(
              cityDD,
              features
                .filter(
                  (f) => f.properties?.locality || f.properties?.localadmin || f.properties?.region
                )
                .slice(0, 8),
              (f) => {
                const p = f.properties || {};
                cityEl.value = p.locality || p.localadmin || p.region || p.name || cityEl.value;
                // Keep postal if present on feature
                if (p.postalcode) postalEl.value = p.postalcode;
                const evt = new Event('input', { bubbles: true });
                cityEl.dispatchEvent(evt);
                postalEl.dispatchEvent(evt);
              }
            );
          }, 250);
        });

        postalEl.addEventListener('input', () => {
          if (!isEditing) {
            hideDD(postalDD);
            return;
          }
          clearTimeout(postalDeb);
          hideDD(postalDD);
          const q = postalEl.value.trim();
          if (q.length < 3) {
            return;
          }
          postalDeb = setTimeout(async () => {
            const params = new URLSearchParams({ text: q });
            const features = await fetchPelias(`${peliasBase}/autocomplete?${params.toString()}`);
            // Prefer postalcode layer results first
            const sorted = features.sort((a, b) => {
              const la = a.properties?.layer === 'postalcode' ? 0 : 1;
              const lb = b.properties?.layer === 'postalcode' ? 0 : 1;
              return la - lb;
            });
            renderDD(postalDD, sorted.slice(0, 8), (f) => {
              const p = f.properties || {};
              if (p.postalcode) postalEl.value = p.postalcode;
              const cityVal = p.locality || p.localadmin || p.county || p.region;
              if (cityVal) cityEl.value = cityVal;
              const evt = new Event('input', { bubbles: true });
              postalEl.dispatchEvent(evt);
              cityEl.dispatchEvent(evt);
            });
          }, 300);
        });

        // Enter on postal: accept first suggestion if any visible
        postalEl.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' && !postalDD.classList.contains('hidden')) {
            e.preventDefault();
            const first = postalDD.firstElementChild;
            if (first) first.click();
          }
        });

        // Hide dropdowns when clicking outside
        document.addEventListener('click', (ev) => {
          const targets = [streetEl, cityEl, postalEl, streetDD, cityDD, postalDD];
          if (!targets.some((n) => n && (n === ev.target || n.contains(ev.target)))) {
            hideDD(streetDD);
            hideDD(cityDD);
            hideDD(postalDD);
          }
        });
      })();

      // ---------- Allergy "Other" checkbox setup ----------
      if (el.allergyOtherCheckbox && el.allergyOtherInput) {
        el.allergyOtherCheckbox.addEventListener('change', function () {
          if (this.checked) {
            el.allergyOtherInput.classList.remove('hidden');
          } else {
            el.allergyOtherInput.classList.add('hidden');
            el.allergyOtherInput.value = '';
          }
        });
      }

      /** Normalize allergies value for simple comparison (change detection) */
      function canonicalizeAllergies(allergies) {
        try {
          if (!allergies || !Array.isArray(allergies)) return '';
          return allergies
            .map((s) => String(s).trim().toLowerCase())
            .sort()
            .join('|');
        } catch {
          return '';
        }
      }

      /** Populate optional fields in the UI (selects/inputs) */
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

      /** Read optional fields to build the request payload */
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

      /** List missing basic fields (for the banner) */
      function getMissingBasics(data) {
        const missing = [];
        if (!data.first_name) missing.push('first name');
        if (!data.last_name) missing.push('last name');
        if (!data.email) missing.push('email');
        if (!data.phone_number) missing.push('phone number');
        if (!data.address) missing.push('address');
        // For allergies, we don't consider it "missing" since having no allergies is valid
        // Remove the preferences check
        return missing;
      }

      /** List missing optional fields (for the onboarding modal) */
      function getMissingOptional(data) {
        const optMissing = [];
        if (typeof data.kitchen_available !== 'boolean') optMissing.push('kitchen available');
        if (data.kitchen_available === true && typeof data.main_course_possible !== 'boolean')
          optMissing.push('main course');
        if (!data.default_dietary_preference) optMissing.push('dietary');
        if (!data.field_of_study) optMissing.push('field of study');
        return optMissing;
      }

      /** Update banners based on current data */
      function updateBanners(data) {
        const missing = getMissingBasics(data);
        if (missing.length && el.incompleteBanner) {
          setHidden(el.incompleteBanner, false);
          if (el.incompleteDetails)
            el.incompleteDetails.textContent = `Missing: ${missing.join(', ')}`;
        } else {
          setHidden(el.incompleteBanner, true);
        }

        const showOnboarding = data.profile_prompt_pending && !data.optional_profile_completed;
        if (showOnboarding && el.onboardingModal) {
          const optMissing = getMissingOptional(data);
          if (el.onboardingMissing) el.onboardingMissing.textContent = optMissing.join(', ');
          setHidden(el.onboardingModal, false);
        } else {
          setHidden(el.onboardingModal, true);
        }
      }

      /** Apply profile data to the UI and initialize local state */
      function handleProfileData(data) {
        // Header + identity
        const fullName = fullNameOf(data);
        if (el.headerName) el.headerName.textContent = fullName;
        if (el.headerEmail) el.headerEmail.textContent = data.email || '';
        if (el.firstName) el.firstName.value = data.first_name || '';
        if (el.lastName) el.lastName.value = data.last_name || '';
        if (el.email) el.email.value = data.email || '';
  if (el.phone) el.phone.value = data.phone_number || '';
        if (el.fullNameView) el.fullNameView.value = fullName;

        // Address (view + prefill edit fields when structured)
        const addr = data.address && typeof data.address === 'object' ? data.address : null;
        if (el.address) el.address.value = formatAddressStruct(addr || data.address);
        if (addr) {
          if (el.addrStreet) el.addrStreet.value = addr.street || '';
          if (el.addrNumber) el.addrNumber.value = addr.street_no || '';
          if (el.addrPostal) el.addrPostal.value = addr.postal_code || '';
          if (el.addrCity) el.addrCity.value = addr.city || '';
        }

        // Allergies + optional fields
        setAllergies(data.allergies);
        setOptionalUI({
          kitchen_available: data.kitchen_available,
          main_course_possible: data.main_course_possible,
          default_dietary_preference: data.default_dietary_preference,
          field_of_study: data.field_of_study,
        });

        // Banners (incomplete + onboarding)
        updateBanners(data);

        // Initial snapshot for change detection
        initial = {
          first_name: data.first_name || '',
          last_name: data.last_name || '',
          email: data.email || '',
          phone_number: data.phone_number || '',
          address: data.address || '',
          allergies: data.allergies || [],
          optional: {
            kitchen_available: data.kitchen_available,
            main_course_possible: data.main_course_possible,
            default_dietary_preference: data.default_dietary_preference,
            field_of_study: data.field_of_study,
          },
        };

        // Show content once ready
        setHidden(el.skeletonHeader, true);
        setHidden(el.skeletonMain, true);
        setHidden(el.profileHeader, false);
        setHidden(el.profileMain, false);
        setEditMode(false);

        return data;
      }

      /** Determine whether there are unsaved changes */
      function computeUnsaved() {
        if (!initial) return false;
        const current = {
          first_name: el.firstName?.value?.trim() || '',
          last_name: el.lastName?.value?.trim() || '',
          email: el.email?.value?.trim() || '',
          phone: el.phone?.value?.trim() || '',
          address:
            el.addressEditGroup && !el.addressEditGroup.classList.contains('hidden')
              ? computeViewAddress()
              : formatAddressStruct(initial.address),
          allergies: getAllergies(),
          optional: collectOptionalPayload(),
        };
        const sameFirst = current.first_name === (initial.first_name || '');
        const sameLast = current.last_name === (initial.last_name || '');
        const sameEmail = current.email === (initial.email || '');
        let samePhone = false;
        try {
          const normalizedCurrent = normalizePhoneInput(current.phone);
          const normalizedInitial = initial.phone_number || '';
          samePhone = normalizedCurrent === (normalizedInitial || '');
          if (!normalizedCurrent && !normalizedInitial) samePhone = true;
        } catch {
          samePhone = false;
        }
        const sameAddr = current.address === formatAddressStruct(initial.address || '');
        const samePrefs =
          canonicalizePrefs(current.preferences) === canonicalizePrefs(initial.preferences);
        const sameOpt =
          canonicalizePrefs(current.optional) === canonicalizePrefs(initial.optional || {});
        return !(sameFirst && sameLast && sameEmail && samePhone && sameAddr && samePrefs && sameOpt);
      }

      /** Toggle edit mode and prepare inputs accordingly */
      function setEditMode(on) {
        isEditing = !!on;
        // Email is not editable
        disableInput(el.email, true);
        // First/last name are editable in edit mode
        disableInput(el.firstName, !isEditing);
        disableInput(el.lastName, !isEditing);
  disableInput(el.phone, !isEditing);
        if (el.fullNameView) setHidden(el.fullNameView, isEditing);
        if (el.fullNameLabel) setHidden(el.fullNameLabel, isEditing);
        if (el.firstName) setHidden(el.firstName, !isEditing);
        if (el.lastName) setHidden(el.lastName, !isEditing);
        if (el.firstNameLabel) setHidden(el.firstNameLabel, !isEditing);
        if (el.lastNameLabel) setHidden(el.lastNameLabel, !isEditing);
        // Address: show edit inputs only in edit mode; view field always disabled
        setHidden(el.address, !!isEditing);
        setHidden(el.addressEditGroup, !isEditing);
        // Prefill address inputs from initial snapshot when available
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
          'phone',
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

      /** Get the legacy dh_token if present (via helper or direct cookie) */
      function getDhToken() {
        try {
          if (window.auth && typeof window.auth.getCookie === 'function') {
            return window.auth.getCookie('dh_token');
          }
          const m = document.cookie.match(/(?:^|; )dh_token=([^;]*)/);
          return m && m[1] ? decodeURIComponent(m[1]) : null;
        } catch {
          return null;
        }
      }

      /** Load the profile (cookie+CSRF by default, dh_token Bearer fallback) */
      async function loadProfile() {
        try {
          await (window.initCsrf ? window.initCsrf() : Promise.resolve());
        } catch {}

        // Load allergies list from API first
        await loadAllergies();

        const bearer = getDhToken();
        const baseOpts = bearer
          ? { headers: { Authorization: `Bearer ${bearer}` }, credentials: 'omit' }
          : {};
        let res = await window.apiFetch('/profile', baseOpts);
        let data = await res.json().catch(() => ({}));

        if (!res.ok) {
          // If we didn't send a bearer (cookie auth) but it exists, try again with Bearer
          if (!bearer) {
            const b2 = getDhToken();
            if (b2) {
              const res2 = await window.dh.apiFetch('/profile', {
                headers: { Authorization: `Bearer ${b2}` },
                credentials: 'omit',
              });
              const d2 = await res2.json().catch(() => ({}));
              if (res2.ok) {
                return handleProfileData(d2);
              }
            }
          }
          if (typeof window.handleUnauthorized === 'function')
            window.handleUnauthorized({ autoRedirect: true, delayMs: 1000 });
          else window.location.href = 'login.html';
          return null;
        }

        return handleProfileData(data);
      }

      /** Build the payload from current fields (only what changed) */
      function buildSavePayload() {
        const payload = {};
        const fName = el.firstName?.value?.trim();
        const lName = el.lastName?.value?.trim();
        if (typeof fName === 'string' && fName !== initial.first_name) payload.first_name = fName;
        if (typeof lName === 'string' && lName !== initial.last_name) payload.last_name = lName;

        const newEmail = el.email?.value?.trim();
        if (newEmail && newEmail !== initial.email) payload.email = newEmail;

        const phoneRaw = el.phone?.value?.trim() || '';
        const initialPhone = initial.phone_number || '';
        if (phoneRaw) {
          let normalizedPhone = '';
          try {
            normalizedPhone = normalizePhoneInput(phoneRaw);
          } catch (err) {
            throw err instanceof Error ? err : new Error('Please enter a valid phone number with at least 6 digits.');
          }
          if (normalizedPhone !== initialPhone) payload.phone_number = normalizedPhone;
        } else if (initialPhone) {
          throw new Error('Phone number cannot be empty once provided.');
        }
        const city = el.addrCity?.value?.trim();
        const postal = el.addrPostal?.value?.trim();
        const street = el.addrStreet?.value?.trim();
        const num = el.addrNumber?.value?.trim();
        const anyAddrEdited = !!(city || postal || street || num);
        if (anyAddrEdited) {
          if (!street || !num || !postal || !city) {
            throw new Error(
              'Please provide your full address: street, number, postal code, and city.'
            );
          }
          if (!/^[0-9A-Za-z \-]{3,10}$/.test(postal)) {
            throw new Error('Please enter a valid postal code.');
          }
          payload.street = street;
          payload.street_no = num;
          payload.postal_code = postal;
          payload.city = city;
        }

        const allergies = getAllergies();
        if (allergies.length > 0) payload.allergies = allergies;

        const optional = collectOptionalPayload();
        Object.assign(payload, optional);

        return payload;
      }

      /** Send the update and reload the profile */
      async function saveProfile() {
        if (!isEditing) return;
        try {
          const payload = buildSavePayload();
          const bearer = getDhToken();
          const headers = { 'Content-Type': 'application/json' };
          if (bearer) headers['Authorization'] = `Bearer ${bearer}`;
          const res = await window.apiFetch('/profile', {
            method: 'PUT',
            headers,
            body: JSON.stringify(payload),
            credentials: bearer ? 'omit' : undefined,
          });
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
          // Reload to reflect derived fields (address_public, etc.)
          await loadProfile();
          // Reset address edit inputs (avoid confusion)
          if (el.addrCity) el.addrCity.value = '';
          if (el.addrPostal) el.addrPostal.value = '';
          if (el.addrStreet) el.addrStreet.value = '';
          if (el.addrNumber) el.addrNumber.value = '';
          setEditMode(false);
        } catch (e) {
          console.error(e);
          alert(e?.message || 'Could not save your changes.');
        }
      }

      // ---------- Events ----------
      // Edit/cancel/save buttons
      el.editBtn &&
        el.editBtn.addEventListener('click', (e) => {
          e.preventDefault();
          setEditMode(true);
        });
      el.cancelBtn &&
        el.cancelBtn.addEventListener('click', (e) => {
          e.preventDefault();
          // Restore fields from the initial snapshot
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
            setAllergies(initial.allergies);
            setOptionalUI(initial.optional);
          }
          if (initial && initial.address && typeof initial.address === 'object') {
            if (el.addrStreet) el.addrStreet.value = initial.address.street || '';
            if (el.addrNumber) el.addrNumber.value = initial.address.street_no || '';
            if (el.addrPostal) el.addrPostal.value = initial.address.postal_code || '';
            if (el.addrCity) el.addrCity.value = initial.address.city || '';
          } else {
            if (el.addrCity) el.addrCity.value = '';
            if (el.addrPostal) el.addrPostal.value = '';
            if (el.addrStreet) el.addrStreet.value = '';
            if (el.addrNumber) el.addrNumber.value = '';
          }
          setEditMode(false);
        });
      el.saveBtn &&
        el.saveBtn.addEventListener('click', (e) => {
          e.preventDefault();
          saveProfile();
        });

      // Change detection + dynamic rules (main course <-> kitchen)
      ['input', 'change'].forEach((type) => {
        const watchedInputs = [
          el.firstName,
          el.lastName,
          el.email,
          el.kitchenAvailable,
          el.mainCoursePossible,
          el.defaultDietary,
          el.fieldOfStudy,
          el.addrCity,
          el.addrPostal,
          el.addrStreet,
          el.addrNumber,
        ];
        // Add allergy checkboxes and other input to watch list
        el.allergyCheckboxes.forEach((checkbox) => watchedInputs.push(checkbox));
        watchedInputs.push(el.allergyOtherCheckbox, el.allergyOtherInput);

        watchedInputs.forEach((inp) => {
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

      // Navigation warning if there are unsaved changes
      window.addEventListener('beforeunload', (e) => {
        if (isEditing && hasUnsaved) {
          e.preventDefault();
          e.returnValue = '';
        }
      });

      // Logout (supports cookie+CSRF or Bearer)
      el.logoutBtn &&
        el.logoutBtn.addEventListener('click', async () => {
          try {
            const bearer = getDhToken();
            const headers = bearer ? { Authorization: `Bearer ${bearer}` } : {};
            await window.apiFetch('/logout', {
              method: 'POST',
              headers,
              credentials: bearer ? 'omit' : undefined,
            });
          } catch {}
          // Clear legacy guard cookie
          try {
            if (window.auth && window.auth.deleteCookie) window.auth.deleteCookie('dh_token');
          } catch {}
          window.location.href = 'login.html';
        });

      // Onboarding (optional)
      el.onboardingSkip &&
        el.onboardingSkip.addEventListener('click', async (e) => {
          e.preventDefault();
          try {
            const bearer = getDhToken();
            const headers = bearer
              ? { 'Content-Type': 'application/json', Authorization: `Bearer ${bearer}` }
              : { 'Content-Type': 'application/json' };
            const r = await window.apiFetch('/profile/optional', {
              method: 'PATCH',
              headers,
              body: JSON.stringify({ skip: true }),
              credentials: bearer ? 'omit' : undefined,
            });
            await r.json().catch(() => ({}));
          } catch {}
          setHidden(el.onboardingModal, true);
        });
      el.onboardingFill &&
        el.onboardingFill.addEventListener('click', (e) => {
          e.preventDefault();
          setHidden(el.onboardingModal, true);
          setEditMode(true);
        });

      // Initial load
      await loadProfile();
    })();
  });
})();

// Lightweight registration helper that builds a normalized payload from the form in `home.html`.
// It intentionally keeps shape small and predictable: solo returns { event_id, solo:true, body: { ... } }
// Team returns { event_id, body: { team_size:2, invited_emails, preferences } }
(function () {
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {}; const utils = window.dh.utils = window.dh.utils || {};

  function aggregateDiet(a, b) {
    const order = ['omnivore', 'vegetarian', 'vegan'];
    const ca = order.indexOf((a || '').toLowerCase());
    const cb = order.indexOf((b || '').toLowerCase());
    if (ca === -1) return b || a || ''; if (cb === -1) return a || b || '';
    return order[Math.max(ca, cb)];
  }

  function _valToBool(v) {
    if (v === undefined || v === null || v === '') return undefined;
    return String(v) === 'yes';
  }

  function buildRegistrationPayload(form, profile) {
    if (!form) throw new Error('Form element required');
    const event_id = form.elements.event_id?.value;
    const mode = form.dataset.mode || 'solo';

    if (mode === 'solo') {
      const dietary = form.elements.dietary?.value || undefined;
      const kitchen = _valToBool(form.elements.kitchen?.value);
      const main_course_possible = _valToBool(form.elements.main_course?.value);
      const course = form.elements.course?.value || undefined;
      // basic client-side validation for main course
      const profileMain = !!(profile && (profile.preferences?.main_course_possible || profile.main_course_possible));
      const mainPossible = (main_course_possible === true) || (main_course_possible === undefined && profileMain);
      if (course === 'main' && !mainPossible) throw new Error('Cannot select Main if main course is not possible.');
      let course_pref = course || undefined; if (course_pref === 'starter') course_pref = 'appetizer';
      return { event_id, solo: true, body: { event_id, dietary_preference: dietary || undefined, kitchen_available: kitchen, main_course_possible, course_preference: course_pref } };
    }

    // Team mode
    const partnerMode = (form.querySelector('input[name="partner_mode"]:checked') || {}).value || 'existing';
    const invited_emails = [];
    const preferences = {};

    const teamCourse = form.elements.team_course?.value; if (teamCourse) preferences.course_preference = teamCourse;
    const cookLocation = form.elements.cook_location?.value; if (cookLocation) preferences.cook_at = cookLocation === 'self' ? 'creator' : 'partner';

    // creator-side optional overrides (only relevant when cooking at partner but harmless otherwise)
    const creatorKitchen = _valToBool(form.elements.creator_kitchen?.value);
    const creatorMain = _valToBool(form.elements.creator_main_course?.value);
    if (typeof creatorKitchen !== 'undefined') preferences.kitchen_available = creatorKitchen;
    if (typeof creatorMain !== 'undefined') preferences.main_course_possible = creatorMain;

    // partner kitchen/main selects: used when external partner is added
    const partnerKitchen = _valToBool(form.elements.partner_kitchen?.value);
    const partnerMain = _valToBool(form.elements.partner_main_course?.value);

    if (partnerMode === 'existing') {
      const email = (form.elements.partner_email?.value || '').trim();
      if (!email) throw new Error('Partner email is required.');
      invited_emails.push(email);
    } else {
      const name = (form.elements.partner_name?.value || '').trim();
      const email = (form.elements.partner_email_ext?.value || '').trim();
      if (!name || !email) throw new Error('Partner name and email are required.');
      invited_emails.push(email);
      const ext = {
        name,
        email,
        gender: form.elements.partner_gender?.value || undefined,
        dietary: form.elements.partner_dietary?.value || undefined,
        field_of_study: form.elements.partner_field?.value || undefined,
      };
      if (typeof partnerKitchen !== 'undefined') ext.kitchen_available = partnerKitchen;
      if (typeof partnerMain !== 'undefined') ext.main_course_possible = partnerMain;
      preferences.partner_external = ext;
    }

    // If cook location is 'self' but profile says no kitchen, block early
    const selfKitchen = !!(profile && (profile.preferences?.kitchen_available || profile.kitchen_available));
    if (form.elements.cook_location?.value === 'self' && !selfKitchen) throw new Error('Your profile says no kitchen available, but you selected to cook at your place.');

    return { event_id, body: { team_size: 2, invited_emails, preferences } };
  }

  utils.aggregateDiet = aggregateDiet;
  utils.buildRegistrationPayload = buildRegistrationPayload;
})();

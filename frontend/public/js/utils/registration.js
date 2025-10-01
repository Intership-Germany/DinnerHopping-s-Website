(function(){
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {}; const utils = window.dh.utils = window.dh.utils || {};

  function aggregateDiet(a,b){
    const order = ['omnivore','vegetarian','vegan'];
    const ca = order.indexOf((a||'').toLowerCase());
    const cb = order.indexOf((b||'').toLowerCase());
    if (ca === -1) return b || a || ''; if (cb === -1) return a || b || '';
    return order[Math.max(ca, cb)];
  }

  function buildRegistrationPayload(form, profile){
    const event_id = form.elements.event_id.value;
    const mode = form.dataset.mode || 'solo';
    if (mode === 'solo') {
      const dietary = form.elements.dietary.value;
      const kitchenVal = form.elements.kitchen.value;
      const mainCourseVal = form.elements.main_course.value;
      const course = form.elements.course.value;
      const profileMain = !!(profile && (profile.preferences?.main_course_possible || profile.main_course_possible));
      const mainPossible = mainCourseVal === 'yes' || (mainCourseVal === '' && profileMain);
      if (course === 'main' && !mainPossible) throw new Error('Cannot select Main if main course is not possible.');
      let coursePref = course || undefined; if (coursePref === 'starter') coursePref = 'appetizer';
      return { event_id, solo:true, body:{ event_id, dietary_preference: dietary || undefined, kitchen_available: kitchenVal ? kitchenVal === 'yes' : undefined, main_course_possible: mainCourseVal ? mainCourseVal === 'yes' : undefined, course_preference: coursePref } };
    }
    const partnerMode = form.querySelector('[name="partner_mode"]:checked')?.value || 'existing';
    const preferences = {}; const invited_emails = [];
    const teamCourse = form.elements.team_course.value; if (teamCourse) preferences.course_preference = teamCourse;
    const cookLocation = form.elements.cook_location.value; if (cookLocation) preferences.cook_at = cookLocation;
    if (partnerMode === 'existing') {
      const email = (form.elements.partner_email.value || '').trim(); if (!email) throw new Error('Partner email is required.'); invited_emails.push(email);
    } else {
      const name = (form.elements.partner_name.value || '').trim(); const email = (form.elements.partner_email_ext.value || '').trim();
      if (!name || !email) throw new Error('Partner name and email are required.');
      invited_emails.push(email);
      preferences.partner_external = { name, email, gender: form.elements.partner_gender.value || undefined, dietary: form.elements.partner_dietary.value || undefined, field_of_study: form.elements.partner_field.value || undefined };
    }
    const selfKitchen = !!(profile && (profile.preferences?.kitchen_available || profile.kitchen_available));
    if (cookLocation === 'self' && !selfKitchen) throw new Error('Your profile says no kitchen available, but you selected to cook at your place.');
    return { event_id, body:{ team_size:2, invited_emails, preferences } };
  }

  utils.aggregateDiet = aggregateDiet;
  utils.buildRegistrationPayload = buildRegistrationPayload;
})();

(function () {
  if (typeof window === 'undefined') return;
  // Toggle visibility of the .team-kitchen-fields block when cooking location changes
  function toggleFieldsFromTarget(t) {
    try {
      if (!t) return;
      const form = t.closest && t.closest('form');
      if (!form) return;
      const fields = form.querySelector('.team-kitchen-fields');
      if (!fields) return;
      if (t.value === 'partner') fields.classList.remove('hidden');
      else fields.classList.add('hidden');
    } catch (err) {
      console && console.warn && console.warn('kitchen toggle error', err);
    }
  }

  document.addEventListener('change', function (e) {
    try {
      const t = e.target;
      if (!t) return;
      if (t.name === 'cook_location') toggleFieldsFromTarget(t);
    } catch (err) {
      console && console.warn && console.warn('kitchen toggle error', err);
    }
  });

  // When a registration modal is opened via a button (has class .reg-open), ensure the fields visibility matches
  document.addEventListener('click', function (e) {
    try {
      const t = e.target;
      if (!t) return;
      const isOpenTrigger = (t.matches && t.matches('.reg-open')) || (t.closest && t.closest('.reg-open'));
      if (!isOpenTrigger) return;
      // find the form inside the modal if present
      const modalForm = document.querySelector('.reg-form');
      if (!modalForm) return;
      const cook = modalForm.querySelector('[name="cook_location"]');
      const fields = modalForm.querySelector('.team-kitchen-fields');
      if (cook && fields) {
        if (cook.value === 'partner') fields.classList.remove('hidden');
        else fields.classList.add('hidden');
      }
    } catch (err) {
      /* ignore */
    }
  });

  // expose for debugging if needed
  window.dh = window.dh || {};
  window.dh.kitchenToggle = { toggleFieldsFromTarget };
})();

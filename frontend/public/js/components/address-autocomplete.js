/** Address Autocomplete (Pelias) abstraction
 * initAddressAutocomplete({ mode: 'signup'|'profile', selectors:{ street, number, postal, city } })
 */
(function () {
  if (typeof window === 'undefined') return;
  window.dh = window.dh || {};
  window.dh.components = window.dh.components || {};
  const peliasBase = 'https://pelias.cephlabs.de/v1';
  function createDropdown(anchor) {
    if (!anchor) return null;
    if (!anchor.parentElement.classList.contains('relative'))
      anchor.parentElement.classList.add('relative');
    const dd = document.createElement('div');
    dd.className =
      'absolute z-20 left-0 right-0 mt-1 bg-white border rounded-md shadow max-h-60 overflow-auto hidden';
    anchor.parentElement.appendChild(dd);
    return dd;
  }
  function hide(dd) {
    dd && dd.classList.add('hidden');
  }
  function show(dd) {
    dd && dd.classList.remove('hidden');
  }
  async function fetchJson(url) {
    try {
      const r = await fetch(url, { headers: { Accept: 'application/json' } });
      if (!r.ok) return null;
      return await r.json();
    } catch {
      return null;
    }
  }
  function render(dd, feats, onPick) {
    if (!dd) return;
    dd.innerHTML = '';
    if (!feats || !feats.length) {
      hide(dd);
      return;
    }
    feats.slice(0, 6).forEach((f) => {
      const p = f.properties || {};
      const label =
        p.label ||
        p.name ||
        [p.housenumber, p.street, p.postalcode, p.locality || p.city].filter(Boolean).join(' ');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'block w-full text-left px-3 py-2 hover:bg-gray-50';
      btn.textContent = label || 'Unknown';
      btn.addEventListener('click', () => onPick(f, p));
      dd.appendChild(btn);
    });
    show(dd);
  }
  function initAddressAutocomplete(opts) {
    const s = opts || {};
    const sel = s.selectors || {};
    const street = document.querySelector(sel.street);
    const number = document.querySelector(sel.number);
    const postal = document.querySelector(sel.postal);
    const city = document.querySelector(sel.city);
    if (!street || !number || !postal || !city) return;
    const streetDD = createDropdown(street);
    const cityDD = createDropdown(city);
    const postalDD = createDropdown(postal);
    let streetDeb, cityDeb, postalDeb;
    street.addEventListener('input', () => {
      clearTimeout(streetDeb);
      hide(streetDD);
      streetDeb = setTimeout(async () => {
        const q = [number.value.trim(), street.value.trim()].filter(Boolean).join(' ');
        if (q.length < 2) return;
        const nearCity = city.value.trim();
        const url =
          `${peliasBase}/autocomplete?` +
          new URLSearchParams({
            text: q + (nearCity ? ` ${nearCity}` : ''),
            size: '6',
            layers: 'address,street',
          });
        const json = await fetchJson(url);
        const feats = (json && json.features) || [];
        render(streetDD, feats, (f, p) => {
          if (p.housenumber) number.value = p.housenumber;
          if (p.street) street.value = p.street;
          if (p.locality || p.city) city.value = p.locality || p.city;
          if (p.postalcode) postal.value = p.postalcode;
          hide(streetDD);
        });
      }, 300);
    });
    city.addEventListener('input', () => {
      clearTimeout(cityDeb);
      hide(cityDD);
      cityDeb = setTimeout(async () => {
        const q = city.value.trim();
        if (q.length < 2) return;
        const url =
          `${peliasBase}/autocomplete?` +
          new URLSearchParams({
            text: q,
            size: '6',
            layers: 'locality,localadmin,borough,county,region,macroregion',
          });
        const json = await fetchJson(url);
        const feats = (json && json.features) || [];
        render(cityDD, feats, (f, p) => {
          if (p.locality || p.city) city.value = p.locality || p.city;
          if (p.postalcode) postal.value = p.postalcode;
          hide(cityDD);
        });
      }, 300);
    });
    postal.addEventListener('input', () => {
      clearTimeout(postalDeb);
      hide(postalDD);
      postalDeb = setTimeout(async () => {
        const pc = postal.value.trim();
        if (pc.length < 2) return;
        const url =
          `${peliasBase}/search/structured?` + new URLSearchParams({ postalcode: pc, size: '10' });
        const json = await fetchJson(url);
        const feats = (json && json.features) || [];
        const byCity = new Map();
        for (const f of feats) {
          const p = f.properties || {};
          const cityName = p.locality || p.city || p.name;
          if (!cityName) continue;
          const key = cityName.toLowerCase();
          if (!byCity.has(key)) {
            byCity.set(key, {
              properties: {
                locality: cityName,
                postalcode: p.postalcode || pc,
                label: `${cityName}${p.postalcode ? ` (${p.postalcode})` : ''}`,
              },
            });
          }
        }
        const unique = [...byCity.values()];
        if (unique.length === 1) {
          const p = unique[0].properties;
          city.value = p.locality;
          postal.value = p.postalcode;
          hide(postalDD);
          return;
        }
        render(postalDD, unique, (f, p) => {
          if (p.locality) city.value = p.locality;
          if (p.postalcode) postal.value = p.postalcode;
          hide(postalDD);
        });
      }, 300);
    });
    document.addEventListener('click', (ev) => {
      [street, city, postal].forEach((inp) => {
        if (!inp.parentElement.contains(ev.target)) {
          hide(streetDD);
          hide(cityDD);
          hide(postalDD);
        }
      });
    });
    return {
      destroy() {
        [streetDD, cityDD, postalDD].forEach((dd) => dd && dd.remove());
      },
    };
  }
  window.dh.components.initAddressAutocomplete = initAddressAutocomplete;
})();

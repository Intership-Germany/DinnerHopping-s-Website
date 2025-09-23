document.addEventListener('DOMContentLoaded', function () {
  if (typeof L === 'undefined') return;
  try {
    var entreeMap = L.map('map-entree', { zoomControl: true, attributionControl: false });
    entreeMap.setView([51.5413, 9.9345], 14);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      minZoom: 13, maxZoom: 18
    }).addTo(entreeMap);
    L.circle([51.5413, 9.9345], { radius: 500, color: '#008080', fillColor: '#008080', fillOpacity: 0.08, weight: 2, dashArray: '6 6' }).addTo(entreeMap);
    L.marker([51.5413, 9.9345], { icon: L.icon({ iconUrl: 'https://cdn.jsdelivr.net/gh/pointhi/leaflet-color-markers@master/img/marker-icon-red.png', iconSize: [25, 41], iconAnchor: [12, 41] }) }).addTo(entreeMap);

    var dessertMap = L.map('map-dessert', { zoomControl: true, attributionControl: false });
    dessertMap.setView([51.5432, 9.9367], 14);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      minZoom: 13, maxZoom: 18
    }).addTo(dessertMap);
    L.circle([51.5432, 9.9367], { radius: 500, color: '#ffc241', fillColor: '#ffc241', fillOpacity: 0.08, weight: 2, dashArray: '6 6' }).addTo(dessertMap);
    L.marker([51.5432, 9.9367], { icon: L.icon({ iconUrl: 'https://cdn.jsdelivr.net/gh/pointhi/leaflet-color-markers@master/img/marker-icon-yellow.png', iconSize: [25, 41], iconAnchor: [12, 41] }) }).addTo(dessertMap);
  } catch (e) {
    console.error('Failed to init maps', e);
  }
});

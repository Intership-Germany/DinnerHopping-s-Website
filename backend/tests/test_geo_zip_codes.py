import pytest

@pytest.mark.asyncio
async def test_geo_zip_codes_empty(client):
    # Without any data inserted it should return empty arrays but 200
    r = await client.get('/geo/zip-codes?city=Göttingen')
    assert r.status_code == 200
    data = r.json()
    assert data['city'] == 'Göttingen'
    assert data['zip_codes'] == []

@pytest.mark.asyncio
async def test_geo_zip_codes_with_data(client):
    # Insert a few zip code records directly into fake DB
    from app import db as db_mod
    docs = [
        { 'plz_name': 'Göttingen', 'plz_code': '37077', 'krs_code': '03159', 'lan_name': 'Niedersachsen', 'lan_code': '03', 'krs_name': 'Landkreis Göttingen', 'geo_point_2d': { 'lon': 9.98, 'lat': 51.56 } },
        { 'plz_name': 'Göttingen', 'plz_code': '37073', 'krs_code': '03159', 'lan_name': 'Niedersachsen', 'lan_code': '03', 'krs_name': 'Landkreis Göttingen', 'geo_point_2d': { 'lon': 9.94, 'lat': 51.53 } },
        { 'plz_name': 'Göttingen', 'plz_code': '37075', 'krs_code': '03159', 'lan_name': 'Niedersachsen', 'lan_code': '03', 'krs_name': 'Landkreis Göttingen', 'geo_point_2d': { 'lon': 9.96, 'lat': 51.55 } },
    ]
    for d in docs:
        await db_mod.db.zip_codes.insert_one(d)
    r = await client.get('/geo/zip-codes?city=Göttingen')
    assert r.status_code == 200
    data = r.json()
    assert set(data['zip_codes']) == {'37077','37073','37075'}
    assert data['count'] == 3


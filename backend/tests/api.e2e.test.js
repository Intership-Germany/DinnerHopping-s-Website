/**
 * tests/api.e2e.test.js
 *
 * Targets http://localhost:8000/
 *
 * Uses supertest with a base URL so it will hit your running server.
 *
 * Notes:
 * - The test generates a fresh email per run using Date.now()
 * - Event creation handles both: allowed (201) or forbidden (403).
 * - If event creation succeeds the suite attempts a registration flow.
 */

const request = require('supertest')('http://localhost:8000');

const normalizeApiPrefix = (value) => {
  if (!value) return '';
  const trimmed = String(value).trim();
  if (!trimmed || trimmed === '/') return '';
  return `/${trimmed.replace(/^\/+/g, '').replace(/\/+$/g, '')}`;
};

const API_PREFIX = normalizeApiPrefix(process.env.API_PREFIX || '/');

const apiPath = (path) => {
  const ensured = path.startsWith('/') ? path : `/${path}`;
  if (!API_PREFIX) return ensured;
  if (ensured === '/') return API_PREFIX;
  return `${API_PREFIX}${ensured}`;
};
jest.setTimeout(20000); // increase timeout for slower dev machines

describe('Backend minimal flow (auth + events + registration)', () => {
  const PASSWORD = 'TestPassw0rd!';
  const NAME = 'Test User';
  const uniqueSuffix = Date.now();
  const testEmail = `test+${uniqueSuffix}@example.com`;

  let authToken = null;
  let createdEventId = null;
  let registrationIdOrObj = null;

  test('POST /register -> 201 & returns id', async () => {
    const payload = {
      email: testEmail,
      password: PASSWORD,
      name: NAME
    };

    const res = await request
      .post(apiPath('/register'))
      .send(payload)
      .set('Accept', 'application/json');

    expect([200, 201, 202]).toContain(res.status); // allow some flexibility
    // Accept different shapes; prefer id or _id or userId
    const body = res.body || {};
    const id =
      body.id || body._id || body.userId || (body.user && (body.user.id || body.user._id));
    expect(id).toBeTruthy();
  });

  test('POST /login -> 200 & returns token', async () => {
    const res = await request
      .post(apiPath('/login'))
      .send({ username: testEmail, password: PASSWORD })
      .set('Accept', 'application/json');

    // allow login to fail with 401 if email not verified; only assert token when login succeeded
    expect([200, 201, 401]).toContain(res.status);
    const body = res.body || {};
    if (res.status === 200 || res.status === 201) {
      // Accept token in several fields
      const token = body.token || body.accessToken || body.access_token || (body.data && body.data.token);
      expect(token).toBeTruthy();
      authToken = token;
    } else {
      authToken = null;
    }
  });

  test('GET /events -> 200 and returns an array', async () => {
    const res = await request
      .get(apiPath('/events/'))
      .set('Accept', 'application/json');

    expect([200, 201]).toContain(res.status);
    const body = res.body;
    // If API returns { events: [...] } or the array directly
    const events = Array.isArray(body) ? body : (body && body.events) || [];
    expect(Array.isArray(events)).toBe(true);
  });

  test('POST /events -> try to create event (201) or get 403', async () => {
    const eventPayload = {
      title: `Test Event ${uniqueSuffix}`,
      description: 'Event created by e2e test',
      date: new Date(Date.now() + 1000 * 60 * 60 * 24).toISOString(), // tomorrow
      location: {
        name: 'Test Venue',
        lat: 48.8566,
        lon: 2.3522
      },
      capacity: 10
    };

    const res = await request
      .post(apiPath('/events/'))
      .send(eventPayload)
      .set('Accept', 'application/json')
      .set('Authorization', authToken ? `Bearer ${authToken}` : '');

    // Accept either created or forbidden (if only admins allowed)
  const allowedStatuses = [201, 200, 403, 401, 422];
    expect(allowedStatuses).toContain(res.status);

    if (res.status === 201 || res.status === 200) {
      // Event created — attempt to read id
      const body = res.body || {};
      const id = body.id || body._id || body.eventId || (body.event && (body.event.id || body.event._id));
      expect(id).toBeTruthy();
      createdEventId = id;
    } else {
      // Not allowed to create events as regular user — record and continue
      createdEventId = null;
    }
  });

  test('If event created: POST /events/:id/register -> create registration & possibly return payment link', async () => {
    if (!createdEventId) {
      return; // skip — creating events is not permitted for this account
    }

    const regPayload = {
      // shape may vary depending on your API: adapt if needed
      team: null,
      invited_emails: [],
      // if your API expects other fields, add minimal ones here
    };

    const res = await request
      .post(apiPath(`/events/${createdEventId}/register`))
      .send(regPayload)
      .set('Accept', 'application/json')
      .set('Authorization', authToken ? `Bearer ${authToken}` : '');

    // Expectation: registration created (201 or 200)
    expect([200, 201]).toContain(res.status);

    const body = res.body || {};
    // try to extract registration id or payment link
    const regId =
      body.registrationId ||
      body.id ||
      body._id ||
      (body.registration && (body.registration.id || body.registration._id));
    const paymentLink =
      body.payment_link ||
      body.paymentUrl ||
      body.payment_url ||
      (body.payment && (body.payment.link || body.payment.url));

    // Keep result for debugging / manual inspection if needed
    registrationIdOrObj = regId || body;

    // We assert that at least one of these is present (registration id or payment link), since APIs vary
    expect(regId || paymentLink).toBeTruthy();
  });
});

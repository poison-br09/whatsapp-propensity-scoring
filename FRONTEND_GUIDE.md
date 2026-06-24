# Frontend Build Guide — WhatsApp Monitoring (Multi-User)

## Overview

A multi-user dashboard for a WhatsApp group monitoring system. Each user monitors their own WhatsApp session and sees only their own keyword matches. Superadmins see all matches and can manage users.

---

## Auth

### Login / Register

**POST `/api/v1/auth/register`**

Request:
```json
{
  "username": "alice",
  "password": "secret123",
  "whatsapp_phone": "919876543210",
  "target_group_jid": "120363XXXXXX@g.us"
}
```

| Field | Type | Notes |
|---|---|---|
| `username` | `string` | 3–50 chars |
| `password` | `string` | Min 8 chars |
| `whatsapp_phone` | `string` | Min 6 chars, digits only recommended |
| `target_group_jid` | `string \| null` | Optional. WhatsApp group JID to monitor |

Response `200`:
```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "role": "user",
  "whatsapp_phone": "919876543210"
}
```

Error `409`: username or phone already registered.

---

**POST `/api/v1/auth/login`**

Request:
```json
{
  "username": "alice",
  "password": "secret123"
}
```

Response `200`: same shape as register.

Error `401`: invalid credentials or deactivated account.

---

### Using the token

Store the JWT in `localStorage` or in memory (not cookies — no CSRF needed).

All protected endpoints require:
```
Authorization: Bearer <access_token>
```

**Decode the JWT payload** (base64-decode the middle segment) to read `role` and `phone` — avoid re-fetching the profile on every render.

JWT payload fields:
| Field | Type | Notes |
|---|---|---|
| `sub` | `string` | User UUID |
| `username` | `string` | |
| `role` | `string` | `"user"` or `"superadmin"` |
| `phone` | `string` | `whatsapp_phone` |
| `group_jid` | `string \| null` | Target group JID |
| `exp` | `number` | Unix expiry timestamp |

---

## Auth header matrix

| Endpoint group | Auth method |
|---|---|
| `POST /api/v1/auth/register`, `POST /api/v1/auth/login` | None |
| `GET /api/v1/whatsapp/status` | `Authorization: Bearer <token>` |
| `POST /api/v1/whatsapp/pairing-code` | `Authorization: Bearer <token>` |
| `GET /admin/keyword-analysis/matches/export` | `Authorization: Bearer <token>` |
| `POST /admin/backfill/start`, `POST /admin/backfill/stop` | `Authorization: Bearer <token>` |
| `GET /admin/users`, `PATCH /admin/users/{id}/deactivate` | `Authorization: Bearer <token>` (superadmin only) |
| `GET/POST/PATCH/DELETE /admin/keyword-analysis/keywords` | `x-api-key: <API_KEY>` |
| `POST /admin/keyword-analysis/start`, `POST /admin/keyword-analysis/stop` | `x-api-key: <API_KEY>` |
| `POST /admin/propensity/start`, `POST /admin/propensity/stop` | `x-api-key: <API_KEY>` |

---

## Base URL

```
https://<your-domain>/api/v1
```

Use `VITE_API_BASE_URL` for the base URL.

---

## Pages / Sections

### User dashboard
1. **Session** — own WhatsApp status + pairing code
2. **Keyword Analysis** — export own matches only
3. **Backfill** — trigger own bridge history sync

### Superadmin dashboard
All user pages, plus:
4. **Keywords** — list, add, enable/disable, delete (requires `VITE_API_KEY`)
5. **All Matches** — export with no receiver filter
6. **Users** — list all users, deactivate accounts

---

## API Contracts

### 1. WhatsApp Session

#### GET `/whatsapp/status`
Returns global WhatsApp session state. Same response for all authenticated users.

**Response `200`**
```json
{
  "status": "connected",
  "phone_number": "919876543210",
  "target_group_jid": "120363XXXXXX@g.us",
  "last_event_at": "2025-06-21T10:00:00Z",
  "last_disconnect_code": null,
  "pairing_required": false
}
```

| Field | Type | Notes |
|---|---|---|
| `status` | `string` | `"connected"` \| `"disconnected"` \| `"connecting"` \| `"logged_out"` |
| `phone_number` | `string \| null` | Logged-in WhatsApp number |
| `target_group_jid` | `string \| null` | Group being monitored |
| `last_event_at` | `string \| null` | ISO 8601 timestamp |
| `last_disconnect_code` | `number \| null` | Baileys disconnect code |
| `pairing_required` | `boolean` | Show pairing UI when `true` |

---

#### POST `/whatsapp/pairing-code`
Restarts the calling user's WhatsApp bridge and requests a pairing code. Takes ~5–10 seconds.

**Request body**
```json
{ "phone_number": "919876543210" }
```

**Response `200`**
```json
{
  "phone_number": "919876543210",
  "pairing_code": "ABCD-EFGH",
  "status": "pairing_code_generated"
}
```

**Error responses**
| Code | Meaning |
|---|---|
| `400` | Invalid phone number |
| `404` | No WhatsApp session registered for this account |
| `503` | Bridge pool unavailable |
| `504` | Timed out waiting for code (30 s) |

---

### 2. Keywords (x-api-key only)

#### GET `/admin/keyword-analysis/keywords`
**Response `200`**
```json
{
  "keywords": [
    { "id": "uuid", "keyword": "flat", "is_active": true },
    { "id": "uuid", "keyword": "pg",   "is_active": false }
  ]
}
```

---

#### POST `/admin/keyword-analysis/keywords`
**Request body**
```json
{ "keywords": ["flat", "rent", "pg"] }
```

**Response `200`**
```json
{
  "results": [
    { "keyword": "flat", "added": false, "already_existed": true },
    { "keyword": "rent", "added": true,  "already_existed": false }
  ]
}
```

---

#### PATCH `/admin/keyword-analysis/keywords`
**Request body**
```json
{ "keywords": ["flat", "pg"], "enabled": false }
```

**Response `200`**
```json
{
  "updated": [
    { "keyword": "flat", "enabled": false, "found": true },
    { "keyword": "pg",   "enabled": false, "found": true }
  ]
}
```

`found: false` means the keyword does not exist.

---

#### DELETE `/admin/keyword-analysis/keywords`
**Request body**
```json
{ "keywords": ["flat", "pg"] }
```

**Response `200`**
```json
{
  "results": [
    { "keyword": "flat", "deleted": true },
    { "keyword": "pg",   "deleted": false }
  ]
}
```

---

### 3. Keyword Analysis Toggle (x-api-key only)

#### POST `/admin/keyword-analysis/start`
```json
{ "action": "start", "enabled": true }
```

#### POST `/admin/keyword-analysis/stop`
```json
{ "action": "stop", "enabled": false }
```

---

### 4. Matches Export

#### GET `/admin/keyword-analysis/matches/export`
Auth: `Authorization: Bearer <token>`

Role-scoped: `user` gets only their own `receiver_phone` rows; `superadmin` gets all.

**Query parameters**

| Param | Type | Required | Notes |
|---|---|---|---|
| `keyword` | `string` | Yes (one or more) | Repeat for multiple: `?keyword=flat&keyword=rent` |
| `date_from` | `string` | No | ISO 8601, e.g. `2025-01-01T00:00:00Z` |
| `date_to` | `string` | No | ISO 8601, e.g. `2025-12-31T23:59:59Z` |

**Response `200`** — binary Excel file (`.xlsx`)
- Content-Type: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- Content-Disposition: `attachment; filename="matches_flat_rent.xlsx"`

**Excel columns:** Keyword · Sender Name · Sender Phone · Receiver Phone · Message · Message Date

**Frontend note:** fetch via `axios` with `responseType: 'blob'` and trigger download programmatically (the `Authorization` header cannot be sent via `<a href>` click):

```typescript
const response = await api.get('/admin/keyword-analysis/matches/export', {
  params, responseType: 'blob'
})
const url = URL.createObjectURL(response.data)
const a = document.createElement('a')
a.href = url
a.download = 'matches.xlsx'
a.click()
URL.revokeObjectURL(url)
```

---

### 5. History Backfill

Auth: `Authorization: Bearer <token>`. Scoped to the calling user's bridge. Superadmin targets the default bridge port.

#### POST `/admin/backfill/start`
**Response `200`**
```json
{ "action": "start", "accepted": true }
```

#### POST `/admin/backfill/stop`
**Response `200`**
```json
{ "action": "stop", "accepted": true }
```

**Error responses**
| Code | Meaning |
|---|---|
| `404` | No bridge session registered for this account |
| `503` | Bridge backfill control server not reachable |

---

### 6. Propensity Scoring Toggle (x-api-key only)

#### POST `/admin/propensity/start`
```json
{ "action": "start", "enabled": true }
```

#### POST `/admin/propensity/stop`
```json
{ "action": "stop", "enabled": false }
```

---

### 7. User Management (superadmin only)

#### GET `/admin/users`
Auth: `Authorization: Bearer <token>` (superadmin role required, returns `403` otherwise)

**Response `200`**
```json
{
  "users": [
    {
      "id": "uuid",
      "username": "alice",
      "whatsapp_phone": "919876543210",
      "target_group_jid": "120363XXXXXX@g.us",
      "role": "user",
      "is_active": true,
      "created_at": "2025-06-01T10:00:00Z"
    }
  ]
}
```

---

#### PATCH `/admin/users/{user_id}/deactivate`
Auth: `Authorization: Bearer <token>` (superadmin only)

Deactivates the user's account and stops their WhatsApp bridge.

**Response `200`**
```json
{ "user_id": "uuid", "deactivated": true }
```

---

## Common Error Shape

```json
{ "detail": "Human-readable error message." }
```

| Code | Meaning |
|---|---|
| `401` | Missing/invalid/expired token or API key |
| `403` | Authenticated but insufficient role |
| `404` | Resource not found (e.g. no bridge session) |
| `409` | Conflict (duplicate username/phone on register) |
| `503` | Backend service unavailable |

---

## Recommended Tech Stack

- **Framework:** React + TypeScript (Vite)
- **HTTP client:** `axios` — create two instances: one with `Authorization: Bearer` for JWT endpoints, one with `x-api-key` for keyword management
- **UI:** Tailwind CSS + shadcn/ui
- **State:** React Query (`@tanstack/react-query`) — cache `GET /keywords` and `GET /status`, invalidate on mutations
- **Auth state:** decode JWT locally (e.g. `jwt-decode`) — do not re-fetch the profile endpoint

---

## API Client Skeleton

```typescript
// JWT client — used for most endpoints
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL + '/api/v1',
})
api.interceptors.request.use(config => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// API-key client — used only for keyword management and toggles
const adminApi = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL + '/api/v1',
  headers: { 'x-api-key': import.meta.env.VITE_API_KEY },
})

// Excel export via JWT client
export async function downloadMatches(keywords: string[], dateFrom?: string, dateTo?: string) {
  const params = new URLSearchParams()
  keywords.forEach(k => params.append('keyword', k))
  if (dateFrom) params.set('date_from', dateFrom)
  if (dateTo) params.set('date_to', dateTo)
  const response = await api.get('/admin/keyword-analysis/matches/export', {
    params, responseType: 'blob',
  })
  const url = URL.createObjectURL(response.data)
  const a = document.createElement('a')
  a.href = url
  a.download = 'matches.xlsx'
  a.click()
  URL.revokeObjectURL(url)
}
```

---

## Frontend Guidance

- **On login:** store `access_token` in `localStorage`. Decode the payload to get `role`, `phone`, `group_jid` for immediate UI decisions.
- **Role branching:** `role === 'superadmin'` shows the Users tab and sees all matches; `role === 'user'` sees their own data only.
- **Session polling:** poll `GET /whatsapp/status` every 5 seconds on the Session page. Show the pairing form when `pairing_required: true`.
- **Keyword mutations:** always invalidate the `GET /keywords` query after add, patch, or delete so the list auto-refreshes.
- **Token expiry:** on `401` from a JWT endpoint, clear `localStorage` and redirect to login.
- **Excel download:** always use the blob fetch pattern (shown above) — the `Authorization` header cannot be set on a plain `<a href>` click.

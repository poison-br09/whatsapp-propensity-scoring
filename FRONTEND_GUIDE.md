# Frontend Build Guide — WhatsApp Monitoring (Multi-User)

## Overview

A multi-user dashboard for a WhatsApp group monitoring system. Each registered user runs their
own WhatsApp bridge session. Users see only their own session state and keyword matches.
Superadmins see everything and manage users.

The backend is a FastAPI app. All client-facing endpoints are under `/api/v1`. Internal webhook
endpoints (`/internal/whatsapp/...`) are not exposed to the frontend.

---

## Environment Variables (Frontend)

| Variable | Purpose |
|---|---|
| `VITE_API_BASE_URL` | Base URL of the backend, e.g. `https://your-domain.com` |
| `VITE_API_KEY` | `x-api-key` value for keyword management endpoints |

The base URL for all API calls is `${VITE_API_BASE_URL}/api/v1`.

---

## Server Setup (One-Time, Done by the Backend Team)

These steps are required before any frontend work can be tested end-to-end. They are not
frontend tasks, but the frontend must know they exist to understand the system state.

1. **Run Supabase migrations 011 and 012** in the Supabase SQL editor.
2. **Add `JWT_SECRET`** to the server `.env` file (any long random string).
3. **Seed the first superadmin** directly in the database (no WhatsApp number needed):
   ```sql
   INSERT INTO users (username, password_hash, role)
   VALUES (
     'admin',
     '<bcrypt-hash-of-password>',
     'superadmin'
   );
   ```
4. **Rebuild and restart Docker.**

After this, the superadmin can log in via `POST /api/v1/auth/login` and register other users
via `POST /api/v1/auth/register`.

---

## Auth

### POST `/api/v1/auth/login`

Public endpoint — no token required.

**Request body**
```json
{
  "username": "alice",
  "password": "secret123"
}
```

**Response `200`**
```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "role": "user",
  "whatsapp_phone": "919876543210"
}
```

| Field | Type | Notes |
|---|---|---|
| `access_token` | `string` | JWT — store in `localStorage` |
| `token_type` | `string` | Always `"bearer"` |
| `role` | `string` | `"user"` or `"superadmin"` |
| `whatsapp_phone` | `string \| null` | The user's registered WhatsApp number, or `null` if none linked |

**Errors**
| Code | Meaning |
|---|---|
| `401` | Wrong username/password, or account deactivated |

---

### POST `/api/v1/auth/register`

**Superadmin only** — requires a valid superadmin JWT in the `Authorization` header.
Registers a new user and immediately starts their WhatsApp bridge process.

**Request body**
```json
{
  "username": "alice",
  "password": "secret123",
  "whatsapp_phone": "919876543210",
  "target_group_jid": "120363XXXXXX@g.us"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `username` | `string` | Yes | 3–50 chars, must be unique |
| `password` | `string` | Yes | Min 8 chars |
| `whatsapp_phone` | `string \| null` | No | Digits only; must be unique if provided. Omit for superadmins or users who haven't linked a number yet |
| `target_group_jid` | `string \| null` | No | WhatsApp group JID to monitor |

**Response `200`** — same shape as login.

**Errors**
| Code | Meaning |
|---|---|
| `401` | Missing or invalid superadmin token |
| `403` | Authenticated but not a superadmin |
| `409` | Username or phone number already registered |

---

### Using the Token

Store the JWT in `localStorage`. All protected endpoints require:
```
Authorization: Bearer <access_token>
```

**Decode the JWT payload locally** (base64-decode the middle segment, e.g. using `jwt-decode`)
to read user details — do not make a `/profile` API call.

JWT payload fields:
| Field | Type | Notes |
|---|---|---|
| `sub` | `string` | User UUID |
| `username` | `string` | Display name |
| `role` | `string` | `"user"` or `"superadmin"` |
| `phone` | `string \| null` | `whatsapp_phone`, or `null` if no number linked |
| `group_jid` | `string \| null` | Target group JID |
| `exp` | `number` | Unix timestamp — token expires after 24 hours by default |

On any `401` from a protected endpoint, clear `localStorage` and redirect to the login page.

---

## Auth Header Matrix

| Endpoint | Method | Auth |
|---|---|---|
| `/api/v1/auth/login` | POST | None |
| `/api/v1/auth/register` | POST | `Bearer <token>` (superadmin only) |
| `/api/v1/whatsapp/status` | GET | `Bearer <token>` |
| `/api/v1/whatsapp/pairing-code` | POST | `Bearer <token>` |
| `/api/v1/admin/keyword-analysis/matches/export` | GET | `Bearer <token>` |
| `/api/v1/admin/backfill/start` | POST | `Bearer <token>` |
| `/api/v1/admin/backfill/stop` | POST | `Bearer <token>` |
| `/api/v1/admin/users` | GET | `Bearer <token>` (superadmin only) |
| `/api/v1/admin/users/{id}/deactivate` | PATCH | `Bearer <token>` (superadmin only) |
| `/api/v1/admin/keyword-analysis/keywords` | GET/POST/PATCH/DELETE | `x-api-key: <API_KEY>` |
| `/api/v1/admin/keyword-analysis/start` | POST | `x-api-key: <API_KEY>` |
| `/api/v1/admin/keyword-analysis/stop` | POST | `x-api-key: <API_KEY>` |
| `/api/v1/admin/propensity/start` | POST | `x-api-key: <API_KEY>` |
| `/api/v1/admin/propensity/stop` | POST | `x-api-key: <API_KEY>` |

---

## Role-Based UI

| Feature | `user` | `superadmin` |
|---|---|---|
| View own WhatsApp session status | Yes | Yes |
| Pair own WhatsApp number | Yes | Yes |
| Export own keyword matches | Yes | Yes |
| Trigger backfill for own bridge | Yes | Yes |
| View keyword list | No (no `x-api-key`) | Yes |
| Add / edit / delete keywords | No | Yes |
| Export all users' matches (no phone filter) | No | Yes |
| List all users | No | Yes |
| Deactivate a user | No | Yes |
| Register a new user | No | Yes |

---

## New User Onboarding Workflow

This is the full sequence from account creation to a live WhatsApp session. The frontend must
support this end-to-end.

```
Superadmin logs in
  → POST /api/v1/auth/login
  → Receives access_token (role: superadmin)

Superadmin registers a new user
  → POST /api/v1/auth/register  (with superadmin Bearer token)
  → Backend creates DB record + starts the user's WhatsApp bridge process immediately
  → Returns access_token for the new user (role: user)

New user logs in (or superadmin hands over the token)
  → POST /api/v1/auth/login
  → Receives access_token (role: user)

User visits Session page
  → GET /api/v1/whatsapp/status  (poll every 5 s)
  → status = "disconnected", pairing_required = true  (bridge started but not paired)

User clicks "Get Pairing Code"
  → POST /api/v1/whatsapp/pairing-code  { "phone_number": "919876543210" }
  → Receives pairing_code = "ABCD-EFGH"  (takes ~5–10 s)

User opens WhatsApp on their phone
  → Settings → Linked Devices → Link a device → enter pairing code

Backend bridge connects automatically
  → GET /api/v1/whatsapp/status starts returning status = "connected"
  → pairing_required = false

User's bridge is now live — keyword matches start flowing in
```

---

## API Contracts

### 1. WhatsApp Session

#### GET `/api/v1/whatsapp/status`

Returns the calling user's own WhatsApp session state. Each user sees only their own bridge.

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
| `phone_number` | `string \| null` | The user's logged-in WhatsApp number |
| `target_group_jid` | `string \| null` | Group being monitored |
| `last_event_at` | `string \| null` | ISO 8601 timestamp of last bridge event |
| `last_disconnect_code` | `number \| null` | Baileys disconnect reason code |
| `pairing_required` | `boolean` | `true` when the bridge is running but not linked to a phone |

**Errors**
| Code | Meaning |
|---|---|
| `401` | Missing or invalid token |
| `404` | No WhatsApp bridge found for this account (user not registered yet) |
| `503` | Bridge pool service unavailable |

**Frontend note:** Poll this endpoint every 5 seconds on the Session page. Show the pairing
UI whenever `pairing_required === true`. Hide it and show "Connected" when
`status === "connected"`.

---

#### POST `/api/v1/whatsapp/pairing-code`

Restarts the calling user's WhatsApp bridge and requests a pairing code.
Takes approximately 5–10 seconds to respond.

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

| Field | Type | Notes |
|---|---|---|
| `phone_number` | `string` | Digits only, leading `+` stripped |
| `pairing_code` | `string` | 8-character code to enter on the phone |
| `status` | `string` | Always `"pairing_code_generated"` on success |

**Errors**
| Code | Meaning |
|---|---|
| `400` | Invalid phone number format |
| `401` | Missing or invalid token |
| `404` | No WhatsApp bridge registered for this account |
| `503` | Bridge pool service unavailable |
| `504` | Timed out waiting for the pairing code (30 s) |

---

### 2. Link / Update WhatsApp Phone

#### PATCH `/api/v1/auth/profile/phone`

Auth: `Bearer <token>` — any authenticated user can call this.

Links a WhatsApp phone number to the calling user's account. Also starts their bridge process
immediately. Can be called again if the user needs to change their number.

**Request body**
```json
{ "whatsapp_phone": "919876543210" }
```

**Response `200`** — same shape as login. **The returned `access_token` is a fresh token with
the new phone embedded — the frontend must replace the stored token with this new one.**

```json
{
  "access_token": "<new-jwt>",
  "token_type": "bearer",
  "role": "user",
  "whatsapp_phone": "919876543210"
}
```

**Errors**
| Code | Meaning |
|---|---|
| `400` | Phone number is empty after stripping non-digits |
| `401` | Missing or invalid token |
| `404` | User record not found |
| `409` | Phone number already linked to a different account |

---

### 3. Keywords (`x-api-key` only)

These endpoints use `x-api-key` — show them in the superadmin UI only.

---

#### GET `/api/v1/admin/keyword-analysis/keywords`

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

#### POST `/api/v1/admin/keyword-analysis/keywords`

Add one or more keywords. Normalised to lowercase. Silently skips duplicates.

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

#### PATCH `/api/v1/admin/keyword-analysis/keywords`

Enable or disable a set of keywords.

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

`found: false` means the keyword does not exist in the database — treat it as a no-op.

---

#### DELETE `/api/v1/admin/keyword-analysis/keywords`

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

`deleted: false` means the keyword was not found.

---

### 4. Keyword Analysis Toggle (`x-api-key` only)

#### POST `/api/v1/admin/keyword-analysis/start`

Enables keyword matching globally (affects all bridges).

**Response `200`**
```json
{ "action": "start", "enabled": true }
```

#### POST `/api/v1/admin/keyword-analysis/stop`

**Response `200`**
```json
{ "action": "stop", "enabled": false }
```

---

### 5. Keyword Match Export

#### GET `/api/v1/admin/keyword-analysis/matches/export`

Auth: `Bearer <token>`

Role-scoped:
- `user` — only rows where `receiver_phone` matches their own registered phone number
- `superadmin` — all rows, no phone filter

**Query parameters**

| Param | Type | Required | Notes |
|---|---|---|---|
| `keyword` | `string` | Yes (repeat for multiple) | e.g. `?keyword=flat&keyword=rent` |
| `date_from` | `string` | No | ISO 8601, e.g. `2025-01-01T00:00:00Z` |
| `date_to` | `string` | No | ISO 8601, e.g. `2025-12-31T23:59:59Z` |

**Response `200`** — binary Excel file

- Content-Type: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- Content-Disposition: `attachment; filename="matches_flat_rent.xlsx"`

**Excel columns (in order):**
`Keyword` · `Sender Name` · `Sender Phone` · `Receiver Phone` · `Message` · `Message Date`

**Errors**
| Code | Meaning |
|---|---|
| `401` | Missing or invalid token |
| `422` | `keyword` parameter missing |

**Frontend implementation — always use blob fetch:**

The `Authorization` header cannot be sent via a plain `<a href>` click, so the download must
be triggered programmatically:

```typescript
export async function downloadMatches(
  keywords: string[],
  dateFrom?: string,
  dateTo?: string,
) {
  const params = new URLSearchParams()
  keywords.forEach(k => params.append('keyword', k))
  if (dateFrom) params.set('date_from', dateFrom)
  if (dateTo) params.set('date_to', dateTo)

  const response = await api.get('/admin/keyword-analysis/matches/export', {
    params,
    responseType: 'blob',
  })

  const url = URL.createObjectURL(response.data)
  const a = document.createElement('a')
  a.href = url
  a.download = `matches_${keywords.join('_')}.xlsx`
  a.click()
  URL.revokeObjectURL(url)
}
```

---

### 6. History Backfill

Scoped to the calling user's bridge. Superadmins target the default bridge port.

Auth: `Bearer <token>`

#### POST `/api/v1/admin/backfill/start`

**Response `200`**
```json
{ "action": "start", "accepted": true }
```

#### POST `/api/v1/admin/backfill/stop`

**Response `200`**
```json
{ "action": "stop", "accepted": true }
```

**Errors**
| Code | Meaning |
|---|---|
| `401` | Missing or invalid token |
| `404` | No bridge session found for this account |
| `502` | Bridge rejected the control token (server config issue) |
| `503` | Bridge backfill control server is not reachable |

---

### 7. Propensity Scoring Toggle (`x-api-key` only)

#### POST `/api/v1/admin/propensity/start`

**Response `200`**
```json
{ "action": "start", "enabled": true }
```

#### POST `/api/v1/admin/propensity/stop`

**Response `200`**
```json
{ "action": "stop", "enabled": false }
```

---

### 8. User Management (superadmin only)

#### GET `/api/v1/admin/users`

Returns all registered users.

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

**Errors**
| Code | Meaning |
|---|---|
| `401` | Missing or invalid token |
| `403` | Not a superadmin |

---

#### PATCH `/api/v1/admin/users/{user_id}/deactivate`

Deactivates the user's account in the database and immediately stops their WhatsApp bridge
process. The user's JWT will continue to be structurally valid, but login will fail with `401`
because `is_active = false`.

**Response `200`**
```json
{ "user_id": "uuid", "deactivated": true }
```

**Errors**
| Code | Meaning |
|---|---|
| `401` | Missing or invalid token |
| `403` | Not a superadmin |

---

## Common Error Shape

All errors return:
```json
{ "detail": "Human-readable error message." }
```

| Code | Meaning |
|---|---|
| `401` | Missing / invalid / expired token or API key |
| `403` | Authenticated but insufficient role |
| `404` | Resource not found (e.g. no bridge session for this user) |
| `409` | Conflict (duplicate username or phone on register) |
| `422` | Request body or query param validation failed |
| `503` | Backend service unavailable (bridge pool, state service) |

---

## Recommended Tech Stack

- **Framework:** React + TypeScript (Vite)
- **HTTP client:** `axios` — two instances (see skeleton below)
- **UI:** Tailwind CSS + shadcn/ui
- **State:** React Query (`@tanstack/react-query`) — cache `GET /keywords` and
  `GET /whatsapp/status`, invalidate on mutations
- **Auth state:** decode JWT locally with `jwt-decode` — no profile endpoint exists
- **Token storage:** `localStorage` — clear on logout or `401`

---

## API Client Skeleton

```typescript
import axios from 'axios'

const BASE = import.meta.env.VITE_API_BASE_URL + '/api/v1'

// JWT client — used for most endpoints
export const api = axios.create({ baseURL: BASE })
api.interceptors.request.use(config => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})
api.interceptors.response.use(
  res => res,
  err => {
    if (err.response?.status === 401) {
      localStorage.clear()
      window.location.href = '/login'
    }
    return Promise.reject(err)
  },
)

// API-key client — used only for keyword management and toggles
export const adminApi = axios.create({
  baseURL: BASE,
  headers: { 'x-api-key': import.meta.env.VITE_API_KEY },
})

// Auth helpers
export const login = (username: string, password: string) =>
  api.post('/auth/login', { username, password })

export const register = (payload: {
  username: string
  password: string
  whatsapp_phone: string
  target_group_jid?: string
}) => api.post('/auth/register', payload)

// Excel export
export async function downloadMatches(
  keywords: string[],
  dateFrom?: string,
  dateTo?: string,
) {
  const params = new URLSearchParams()
  keywords.forEach(k => params.append('keyword', k))
  if (dateFrom) params.set('date_from', dateFrom)
  if (dateTo) params.set('date_to', dateTo)
  const response = await api.get('/admin/keyword-analysis/matches/export', {
    params,
    responseType: 'blob',
  })
  const url = URL.createObjectURL(response.data)
  const a = document.createElement('a')
  a.href = url
  a.download = `matches_${keywords.join('_')}.xlsx`
  a.click()
  URL.revokeObjectURL(url)
}
```

---

## Page-by-Page Frontend Guide

### Login Page (`/login`)

- Single form: `username` + `password`
- Call `POST /api/v1/auth/login`
- On success: store `access_token` in `localStorage`, decode JWT for `role` and `phone`,
  redirect to dashboard
- On `401`: show "Invalid credentials"

---

### Session Page — all users

- If the user's JWT `phone` field is `null`, show a **"Link your WhatsApp number"** form:
  - Input: phone number (digits only, with country code e.g. `919876543210`)
  - Submit calls `PATCH /api/v1/auth/profile/phone`
  - On success: the response contains a **new `access_token`** — store it in `localStorage`,
    replacing the old one (the new token has the phone embedded)
  - After saving the new token, reload/re-enter the Session page — polling starts automatically
  - On `409`: show "This phone number is already linked to another account"
- Otherwise poll `GET /api/v1/whatsapp/status` every 5 seconds
- Show current `status`, `phone_number`, `target_group_jid`, `last_event_at`
- On `404`: bridge hasn't started yet — show "Session not initialised" (no pairing UI)
- **Pairing flow:** when `pairing_required === true`, show a "Get Pairing Code" button
  - On click: `POST /api/v1/whatsapp/pairing-code` with the user's phone (from JWT `phone` field)
  - Show a loading state — this takes ~5–10 seconds
  - Display the returned `pairing_code` prominently (format: `ABCD-EFGH`)
  - Instruct the user to open WhatsApp → Settings → Linked Devices → Link a device → enter code
  - Continue polling status; hide the pairing UI when `status === "connected"`
- **Reconnect:** if `status === "logged_out"`, show "Get Pairing Code" again

---

### Keyword Matches Page — all users

- Filter form: keyword(s) (multi-select or tag input), date range (optional)
- At least one keyword required
- Download button calls `downloadMatches(keywords, dateFrom, dateTo)`
- Users see only their own matches; superadmins see all

---

### Backfill Page — all users

- Two buttons: "Start Backfill" and "Stop Backfill"
- Call `POST /api/v1/admin/backfill/start` / `stop`
- Show error message on `404` ("No bridge session") or `503` ("Bridge unreachable")

---

### Keywords Page — superadmin only

- List: `GET /api/v1/admin/keyword-analysis/keywords` via `adminApi`
- Add: text input accepting comma-separated or tag-style entry →
  `POST /api/v1/admin/keyword-analysis/keywords`
- Toggle active: checkbox or toggle per row →
  `PATCH /api/v1/admin/keyword-analysis/keywords` `{ keywords: [kw], enabled: bool }`
- Delete: button per row →
  `DELETE /api/v1/admin/keyword-analysis/keywords`
- Always invalidate/refetch the keyword list after any mutation

---

### Register User Page — superadmin only

- Form: `username`, `password`, `whatsapp_phone`, `target_group_jid` (optional)
- Call `POST /api/v1/auth/register` (with superadmin JWT — the interceptor adds it automatically)
- On `409`: show "Username or phone already registered"
- On success: show confirmation; the new user's bridge starts immediately in the background

---

### Users Page — superadmin only

- List from `GET /api/v1/admin/users`
- Show: username, phone, role, status badge (active / deactivated), created date
- Deactivate button → `PATCH /api/v1/admin/users/{user_id}/deactivate`
  - Confirm with a dialog before calling
  - On success: update the row's badge to "deactivated" or remove from list

---

## Key Behaviour Notes

- **Token expiry:** JWT expires after 24 hours. On `401`, clear storage and redirect to login.
  Optionally check `exp` from the decoded payload on app load to pre-empt the redirect.
- **`pairing_required` polling:** Do not stop polling after a pairing code is shown — the
  backend sets `pairing_required = false` via a session event from the bridge, not from the
  pairing-code response itself.
- **Session state is per-user:** `GET /whatsapp/status` returns the session of the token
  holder, not a global state. Two users logged in at the same time see their own statuses.
- **Keyword mutations invalidate cache:** After any add / patch / delete on keywords, call
  `queryClient.invalidateQueries(['keywords'])` so the list auto-refreshes.
- **Excel download must use `responseType: 'blob'`:** The `Authorization` header cannot be
  attached to a plain `<a href>` navigation — always use the programmatic fetch pattern.
- **`x-api-key` vs Bearer:** Never use the `x-api-key` interceptor for JWT endpoints or vice
  versa. Use `api` for user-facing endpoints, `adminApi` for keyword management.

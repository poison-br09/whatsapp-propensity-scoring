# Frontend Build Guide — WhatsApp Propensity Scoring Admin

## Overview

This is a single-page admin dashboard. It manages a WhatsApp bot that monitors group messages for configured keywords and exports matches. It also manages the WhatsApp session (login via pairing code) and controls background services.

---

## Auth

Every request requires the header:

```
x-api-key: <API_KEY>
```

All endpoints return `401` if the key is missing or wrong.

---

## Base URL

```
https://<your-domain>/api/v1
```

Use an environment variable `VITE_API_BASE_URL` (or equivalent) for the base URL.

---

## Pages / Sections

Suggested layout:

1. **Session** — WhatsApp login status + pairing code
2. **Keywords** — list, add, enable/disable, delete
3. **Keyword Analysis** — on/off toggle + export matches
4. **Backfill** — trigger history sync
5. **Propensity Scoring** — on/off toggle

---

## API Contracts

### 1. WhatsApp Session

#### GET `/whatsapp/status`
Returns current WhatsApp session state.

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
Triggers a new pairing code for the given phone number. This restarts the WhatsApp bridge — takes ~5–10 seconds.

**Request body**
```json
{
  "phone_number": "919876543210"
}
```

| Field | Type | Validation |
|---|---|---|
| `phone_number` | `string` | Min 6 chars. Digits only (no `+` or spaces needed — stripped automatically) |

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
| `400` | Invalid phone number (no digits) |
| `503` | Bridge process unavailable |
| `504` | Timed out waiting for code (30 s) |

---

### 2. Keywords

#### GET `/admin/keyword-analysis/keywords`
Returns all keywords — active and inactive.

**Response `200`**
```json
{
  "keywords": [
    { "id": "uuid", "keyword": "flat", "is_active": true },
    { "id": "uuid", "keyword": "pg", "is_active": false }
  ]
}
```

---

#### POST `/admin/keyword-analysis/keywords`
Add one or more new keywords. Already-existing keywords are not duplicated.

**Request body**
```json
{
  "keywords": ["flat", "rent", "pg"]
}
```

**Response `200`**
```json
{
  "results": [
    { "keyword": "flat", "added": false, "already_existed": true },
    { "keyword": "rent", "added": true,  "already_existed": false },
    { "keyword": "pg",   "added": true,  "already_existed": false }
  ]
}
```

---

#### PATCH `/admin/keyword-analysis/keywords`
Enable or disable existing keywords.

**Request body**
```json
{
  "keywords": ["flat", "pg"],
  "enabled": false
}
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

`found: false` means the keyword does not exist in the table.

---

#### DELETE `/admin/keyword-analysis/keywords`
Permanently delete keywords.

**Request body**
```json
{
  "keywords": ["flat", "pg"]
}
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

`deleted: false` means the keyword was not found (already gone).

---

### 3. Keyword Analysis Toggle

#### POST `/admin/keyword-analysis/start`
Enable live keyword matching.

**Response `200`**
```json
{ "action": "start", "enabled": true }
```

---

#### POST `/admin/keyword-analysis/stop`
Disable live keyword matching.

**Response `200`**
```json
{ "action": "stop", "enabled": false }
```

---

### 4. Matches Export

#### GET `/admin/keyword-analysis/matches/export`
Download matched messages as an Excel file (`.xlsx`).

**Query parameters**

| Param | Type | Required | Notes |
|---|---|---|---|
| `keyword` | `string` | Yes (one or more) | Repeat for multiple: `?keyword=flat&keyword=rent` |
| `date_from` | `string` | No | ISO 8601, e.g. `2025-01-01T00:00:00Z` |
| `date_to` | `string` | No | ISO 8601, e.g. `2025-12-31T23:59:59Z` |

**Response `200`**
- Content-Type: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- Content-Disposition: `attachment; filename="matches_flat_rent.xlsx"`
- Body: binary Excel file

**Excel columns (in order):** Keyword · Sender Name · Sender Phone · Receiver Phone · Message · Message Date

**Frontend note:** trigger the download by constructing a URL with the query params and either setting `window.location.href` or using a hidden `<a download>` click. Do not use `fetch` unless you handle the binary blob manually.

---

### 5. History Backfill

#### POST `/admin/backfill/start`
Tell the WhatsApp bridge to start syncing message history.

**Response `200`**
```json
{ "action": "start", "accepted": true }
```

**Error responses**
| Code | Meaning |
|---|---|
| `503` | Bridge backfill control server not reachable |

---

#### POST `/admin/backfill/stop`
Stop the history sync.

**Response `200`**
```json
{ "action": "stop", "accepted": true }
```

---

### 6. Propensity Scoring Toggle

#### POST `/admin/propensity/start`
Enable propensity scoring on poll votes.

**Response `200`**
```json
{ "action": "start", "enabled": true }
```

---

#### POST `/admin/propensity/stop`
Disable propensity scoring.

**Response `200`**
```json
{ "action": "stop", "enabled": false }
```

---

## Common Error Shape

All errors return JSON:

```json
{ "detail": "Human-readable error message." }
```

---

## Recommended Tech Stack

- **Framework:** React + TypeScript (Vite)
- **HTTP client:** `axios` or native `fetch` with a wrapper that injects `x-api-key`
- **UI:** Tailwind CSS + shadcn/ui
- **State:** React Query (`@tanstack/react-query`) for server state — cache `GET /keywords` and `GET /status`, invalidate on mutations
- **Excel download:** construct URL directly, use `<a href={url} download>` pattern (avoid fetching binary through React Query)
- **Date pickers:** use ISO 8601 strings — backend expects UTC timestamps

---

## API Client Skeleton

```typescript
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL + '/api/v1',
  headers: { 'x-api-key': import.meta.env.VITE_API_KEY },
})

// Excel export — do NOT go through axios
export function buildExportUrl(keywords: string[], dateFrom?: string, dateTo?: string): string {
  const params = new URLSearchParams()
  keywords.forEach(k => params.append('keyword', k))
  if (dateFrom) params.set('date_from', dateFrom)
  if (dateTo) params.set('date_to', dateTo)
  return `${import.meta.env.VITE_API_BASE_URL}/api/v1/admin/keyword-analysis/matches/export?${params}`
}
```

---

## Notes for the Frontend Agent

- The `x-api-key` header must be sent on **every** request including GETs.
- `GET /whatsapp/status` should be polled every 5 seconds on the Session page to reflect connection changes.
- When `pairing_required: true` is returned from `/status`, show the pairing code form prominently.
- Keyword operations (add, patch, delete) should all invalidate the `GET /keywords` query so the list refreshes automatically.
- The Excel export URL must include the `x-api-key` as a query param OR the download must be done via `fetch` + blob — since headers cannot be set on `<a href>` clicks. Recommended approach: fetch the file through axios, create a blob URL, and trigger download programmatically:

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

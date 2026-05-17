# Project Architecture

## Overview

This project ingests WhatsApp poll participation, predicts the probability that a voter will buy the product mentioned in the poll, and stores both the raw WhatsApp-derived entities and the scoring output in Supabase.

There are two main operating modes:

1. Batch CSV scoring
   - A client uploads a WhatsApp poll CSV through the public API.
   - The app parses the CSV, fetches user purchase/voting history from Supabase, computes prediction scores, and writes `poll_prediction` rows.

2. Live WhatsApp bridge ingestion
   - A Node/TypeScript bridge connects to WhatsApp Web through Baileys.
   - It watches poll creation messages and poll vote updates from WhatsApp groups.
   - It forwards those events to the Python app through internal endpoints.
   - The Python app persists poll metadata, vote events, vote snapshots, and live prediction output.

## High-Level Architecture

### Components

- FastAPI application
  - Public API for CSV scoring
  - Public API for WhatsApp pairing code generation
  - Internal API for WhatsApp bridge webhooks
  - SMTP-based logout alerting

- WhatsApp bridge (`whatsapp_bridge/poll-bridge.ts`)
  - Uses Baileys to connect to WhatsApp Web
  - Supports:
    - all-groups ingestion by default
    - optional single-group targeting
    - pairing-code based login
    - reporting session logout events back to Python

- Supabase
  - Stores prediction records
  - Stores WhatsApp poll definitions
  - Stores WhatsApp vote events
  - Stores latest vote snapshots
  - Exposes the history RPC used by the scoring logic

## Current Runtime Flow

### 1. App startup

- `app/main.py`
  - Configures Python logging
  - Creates the FastAPI app
  - Registers:
    - `/api/v1/polls`
    - `/api/v1/whatsapp`
    - `/internal/whatsapp`
  - Starts `BaileysBridgeProcessManager` in the app lifespan

### 2. WhatsApp bridge startup

- `app/core/whatsapp_bridge.py`
  - Launches `whatsapp_bridge/poll-bridge.ts`
  - Injects environment variables:
    - group targeting
    - pairing mode
    - current phone number
    - internal token
    - auth path
    - log levels

- `whatsapp_bridge/poll-bridge.ts`
  - Connects to WhatsApp Web using Baileys
  - If no target group is configured, it processes all WhatsApp groups
  - If pairing mode is enabled, it generates a pairing code for the configured phone number

### 3. Live poll ingestion

#### Poll created

- Bridge detects a poll creation message
- Bridge sends `POST /internal/whatsapp/poll-created`
- Python app:
  - upserts the poll definition into `whatsapp_poll`

#### Poll voted

- Bridge detects vote updates on a known poll
- Bridge sends `POST /internal/whatsapp/poll-vote`
- Python app:
  - upserts the poll definition again defensively
  - inserts a deduplicated vote event into `whatsapp_poll_vote_event`
  - upserts the latest vote snapshot into `whatsapp_poll_vote_snapshot`
  - if the vote can be normalized and the voter phone is known:
    - fetches user history from Supabase RPC
    - computes prediction score
    - upserts into `poll_prediction`

### 4. Session logout alerting

- If WhatsApp logs the session out, the bridge sends:
  - `POST /internal/whatsapp/session-event`
- Python app:
  - checks whether the event is `logged_out`
  - sends an SMTP email to configured recipients

### 5. Pairing flow

- Client calls:
  - `POST /api/v1/whatsapp/pairing-code`
- Protected with `x-api-key`
- Python app:
  - stops the existing bridge
  - deletes the old Baileys auth session
  - starts a fresh pairing-mode bridge session
  - waits for the pairing code
  - returns the pairing code to the caller

## Current Public and Internal Endpoints

### Public endpoints

#### `POST /api/v1/polls/score`

Purpose:
- Batch scoring for uploaded WhatsApp poll CSVs

Protection:
- `x-api-key`

Output:
- scored CSV
- `above_threshold_count`

#### `POST /api/v1/whatsapp/pairing-code`

Purpose:
- Start a fresh WhatsApp login flow for a supplied number

Protection:
- `x-api-key`

Behavior:
- resets older session
- starts new pairing flow
- returns pairing code

### Internal endpoints

#### `POST /internal/whatsapp/poll-created`

Purpose:
- Receive poll definition from bridge

Protection:
- `x-internal-token`

#### `POST /internal/whatsapp/poll-vote`

Purpose:
- Receive vote update from bridge

Protection:
- `x-internal-token`

#### `POST /internal/whatsapp/session-event`

Purpose:
- Receive bridge session lifecycle events

Protection:
- `x-internal-token`

Current use:
- logout email alerting

## Prediction Logic

The current prediction model is still the original heuristic approach. The recent WhatsApp automation changes did not change the scoring idea itself.

### Scoring entry point

- `app/services/scoring.py`
  - `calculate_prediction_score(history, poll_date)`

### Inputs used

The current score uses:

- `total_purchases`
- `last_purchase_date`
- `purchases_last_30_days`
- `purchases_last_60_days`
- `total_yes_votes`
- `last_vote_converted`
- `n_2_vote_converted`

### Heuristic behavior

#### First-time voter

If `total_yes_votes <= 0`:

- base score starts at `50`
- recency of last purchase can increase it:
  - `< 15 days` => `+20`
  - `< 30 days` => `+15`
  - otherwise => `+10`

#### Returning voter

Starts at `20`, then adds:

- purchase velocity points
  - `purchases_last_30_days >= 3` => `+20`
  - `purchases_last_60_days >= 5` => `+10`

- recency points
  - last purchase `< 15 days` => `+15`
  - last purchase `< 30 days` => `+10`

- behavior points
  - `last_vote_converted == True` => `+20`
  - `last_vote_converted == False` => `-5`
  - `n_2_vote_converted == True` => `+15`
  - `n_2_vote_converted == False` => `-5`

- conversion ratio bonus
  - `min((total_purchases / total_yes_votes) * 10, 10)`

Final score:
- clamped to `0..100`
- rounded to 2 decimals

## Supabase Tables and RPC

The code currently depends on one RPC and four tables.

### 1. `get_poll_user_history` RPC

Configured by:
- `SUPABASE_POLL_HISTORY_RPC`

Default:
- `get_poll_user_history`

Purpose:
- given a list of phone numbers, return historical purchase and vote features needed for scoring

Expected output fields:

- `mobile`
- `total_purchases`
- `last_purchase_date`
- `purchases_last_30_days`
- `purchases_last_60_days`
- `total_yes_votes`
- `last_vote_converted`
- `n_2_vote_converted`

### 2. `poll_prediction`

Purpose:
- canonical storage for scored poll participation

Columns inferred from code:

- `mobile`
- `product_name`
- `poll_date`
- `vote`
- `prediction_score`
- `source_filename`

Write behavior:

- batch inserts for CSV scoring
- upsert for live WhatsApp scoring

Conflict rule used by code:
- `mobile,source_filename`

### 3. `whatsapp_poll`

Configured by:
- `SUPABASE_WHATSAPP_POLL_TABLE`

Default:
- `whatsapp_poll`

Purpose:
- stores one record per WhatsApp poll definition

Columns inferred from code:

- `group_jid`
- `poll_message_id`
- `poll_title`
- `poll_options`
- `poll_created_at`

Conflict rule used by code:
- `poll_message_id`

### 4. `whatsapp_poll_vote_event`

Configured by:
- `SUPABASE_WHATSAPP_VOTE_EVENT_TABLE`

Default:
- `whatsapp_poll_vote_event`

Purpose:
- append-only event log of vote updates

Columns inferred from code:

- `dedupe_key`
- `group_jid`
- `poll_message_id`
- `poll_title`
- `voter_jid`
- `voter_phone`
- `selected_options`
- `normalized_vote`
- `vote_timestamp`

Behavior:
- checked first for deduplication
- inserted only if `dedupe_key` is new

Natural key:
- `dedupe_key`

### 5. `whatsapp_poll_vote_snapshot`

Configured by:
- `SUPABASE_WHATSAPP_VOTE_SNAPSHOT_TABLE`

Default:
- `whatsapp_poll_vote_snapshot`

Purpose:
- stores the latest known vote state per `(poll_message_id, voter_jid)`

Columns inferred from code:

- `group_jid`
- `poll_message_id`
- `poll_title`
- `voter_jid`
- `voter_phone`
- `selected_options`
- `normalized_vote`
- `last_vote_timestamp`

Conflict rule used by code:
- `poll_message_id,voter_jid`

## Table Relationships

These relationships are logical relationships used by the application. They are inferred from the code and may or may not be enforced as physical foreign keys in Supabase.

### Relationship graph

- `whatsapp_poll`
  - one poll definition
  - primary application key: `poll_message_id`

- `whatsapp_poll_vote_event`
  - many event rows per poll
  - links to `whatsapp_poll.poll_message_id`

- `whatsapp_poll_vote_snapshot`
  - one latest row per `(poll_message_id, voter_jid)`
  - links to `whatsapp_poll.poll_message_id`

- `poll_prediction`
  - derived scoring output
  - in live mode, `source_filename = whatsapp:<poll_message_id>`
  - in CSV mode, `source_filename` comes from the uploaded filename

### Practical relation summary

- One WhatsApp poll can have many vote events
- One WhatsApp poll can have many vote snapshots, one per voter
- One voter can generate multiple event rows for the same poll over time
- One `(poll, voter)` pair has at most one latest snapshot row
- One live WhatsApp vote can generate one derived prediction row in `poll_prediction`

## Current Project Structure

### Python app

- `app/main.py`
  - app creation and lifespan

- `app/core/config.py`
  - environment-driven configuration

- `app/core/logging_setup.py`
  - file-based logging

- `app/core/whatsapp_bridge.py`
  - manages the bridge subprocess
  - session reset
  - pairing-code orchestration

- `app/api/v1/routes/polls.py`
  - public batch scoring API

- `app/api/v1/routes/whatsapp.py`
  - public pairing-code API

- `app/api/internal/routes/whatsapp.py`
  - internal bridge ingestion APIs

- `app/repositories/supabase_poll_repository.py`
  - all Supabase reads/writes

- `app/services/poll_scoring_service.py`
  - batch CSV scoring pipeline

- `app/services/whatsapp_poll_ingestion_service.py`
  - live poll/vote ingestion pipeline

- `app/services/scoring.py`
  - scoring heuristic

- `app/services/email_service.py`
  - SMTP logout alerts

### Node/TypeScript bridge

- `whatsapp_bridge/poll-bridge.ts`
  - Baileys connection
  - poll detection
  - vote detection
  - pairing code generation
  - logout event reporting

## Environment Variables

### Core API

- `API_KEY`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_POLL_HISTORY_RPC`

### WhatsApp bridge

- `WHATSAPP_INTERNAL_TOKEN`
- `WHATSAPP_INTERNAL_BASE_URL`
- `WHATSAPP_BRIDGE_ENABLED`
- `WHATSAPP_GROUP_JID`
- `WHATSAPP_GROUP_NAME`
- `WHATSAPP_USE_PAIRING_CODE`
- `WHATSAPP_PHONE_NUMBER`
- `BAILEYS_PROJECT_DIR`
- `BAILEYS_AUTH_DIR`
- `BAILEYS_LOG_LEVEL`
- `BAILEYS_PROTOCOL_LOG_LEVEL`
- `BAILEYS_LOG_DIR`

### SMTP alerts

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`
- `SMTP_USE_TLS`
- `SMTP_USE_SSL`
- `WHATSAPP_ALERT_RECIPIENTS`

## Operational Notes

### All-groups mode

If both of these are empty:

- `WHATSAPP_GROUP_JID`
- `WHATSAPP_GROUP_NAME`

then the bridge processes polls from all WhatsApp groups.

### New login replaces old login

The pairing endpoint intentionally deletes the old Baileys auth directory before starting the new session. That means only one active WhatsApp login is supported at a time.

### Logout detection

Logout emails are only sent when WhatsApp explicitly logs the session out. Temporary reconnects do not trigger alerts.

### SMTP configuration

If SMTP is not configured fully, the app will:

- accept the logout event
- log a warning
- skip email sending

It will not crash the app.

## Current Limitations

- There is no public WhatsApp status endpoint yet
- Only one active bridge session is supported
- The scoring model is heuristic, not ML-based
- Table relationships are application-level assumptions unless enforced in Supabase separately
- The bridge currently reports logout events by calling the Python internal API, so the Python app must be reachable from the bridge process

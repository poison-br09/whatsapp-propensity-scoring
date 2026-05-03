# WhatsApp Propensity Scoring API

FastAPI service for scoring WhatsApp poll CSV exports. A user uploads a WhatsApp poll CSV, waits for the request to finish, and receives a scored CSV directly.

The API:

- Parses WhatsApp poll exports.
- Ignores rows where `Name` is not a valid phone number.
- Scores valid `Yes` voters using Supabase purchase/poll history.
- Stores valid phone rows in `poll_prediction`.
- Returns all input rows in the output CSV with vote, prediction, and ignored reason.

## 1. Requirements

- Python 3.11+
- Supabase project access
- Docker and Docker Compose, if running containerized

## 2. Environment Setup

Create a local `.env` file:

```bash
cp .env.example .env
```

Fill these values:

```env
API_KEY=replace-with-a-strong-api-key
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
SUPABASE_POLL_HISTORY_RPC=get_poll_user_history
```

Where:

- `API_KEY`: secret value clients must send as the `x-api-key` header.
- `SUPABASE_URL`: Supabase Project URL from Project Settings > API.
- `SUPABASE_SERVICE_ROLE_KEY`: Supabase `service_role` key from Project Settings > API.
- `SUPABASE_POLL_HISTORY_RPC`: Supabase RPC function name. Keep `get_poll_user_history` unless you rename the SQL function.

## 3. Supabase Setup

Run these SQL files in Supabase SQL Editor, in this order:

```text
supabase/migrations/001_create_poll_prediction.sql
supabase/migrations/002_create_get_poll_user_history_rpc.sql
```

`001_create_poll_prediction.sql` creates `public.poll_prediction`, where the API stores valid phone poll rows and prediction scores.

`002_create_get_poll_user_history_rpc.sql` creates `public.get_poll_user_history(phone_numbers text[])`, which returns purchase/poll history for scoring.

The RPC expects these production tables and columns to exist:

```text
"Auth".id
"Auth".mobile
"User".id
"User"."authId"
"LPoolOrder".id
"LPoolOrder"."userId"
"LPoolOrder"."createdAt"
"LPoolOrder"."paymentStatus"
"LPoolOrder"."settlementStatus"
"LPoolOrder"."exceptionType"
public.poll_prediction.mobile
public.poll_prediction.poll_date
public.poll_prediction.vote
public.poll_prediction.created_at
```

If your production schema differs, update `002_create_get_poll_user_history_rpc.sql` before running it.

## 4. Run Locally

Install dependencies into the existing virtualenv:

```bash
ppvenv/bin/pip install -e ".[dev]"
```

Start the API:

```bash
ppvenv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open docs:

```text
http://127.0.0.1:8000/docs
```

## 5. Run With Docker

Build the image:

```bash
docker compose build
```

Start the API:

```bash
docker compose up
```

Run in the background:

```bash
docker compose up -d
```

Stop:

```bash
docker compose down
```

The API will be available at:

```text
http://127.0.0.1:8000
```

## 6. API Usage

Endpoint:

```text
POST /api/v1/polls/score
```

Headers:

```text
x-api-key: your-api-key
```

Multipart form field:

```text
file: WhatsApp poll CSV
```

The filename must end with a date in `YYYY-MM-DD` format:

```text
pintola-peanut-butter-1kg-dark-chocolate-crunchy-2026-05-03.csv
```

Example:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/polls/score" \
  -H "x-api-key: $API_KEY" \
  -F "file=@pintola-peanut-butter-1kg-dark-chocolate-crunchy-2026-05-03.csv" \
  --output scored-poll.csv
```

Output CSV columns:

```text
raw_name
mobile
vote
prediction_percentage
product_name
poll_date
ignored_reason
```

## 7. Tests

The local machine has system pytest plugins that can interfere with this project, so run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ppvenv/bin/python -m pytest
```

Expected result:

```text
12 passed
```

## 8. Logs

The app logs everything from `DEBUG` and above:

```text
DEBUG
INFO
WARNING
ERROR
```

Logs show the main checkpoints:

- API request received
- file read
- metadata parsed
- CSV row counts
- Supabase RPC call
- scoring applied
- predictions inserted
- output CSV rendered


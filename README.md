# WhatsApp Propensity Scoring API

FastAPI service for two related jobs:
- score uploaded WhatsApp poll CSV exports
- ingest live WhatsApp polls and poll votes through an embedded Baileys bridge

## Requirements

- Python 3.11+
- Node 20+
- Supabase project access

## Environment Setup

Create `.env` from `.env.example` and fill the values.

Important live-ingestion settings:
- `WHATSAPP_BRIDGE_ENABLED=true` to let FastAPI spawn the JS bridge
- `WHATSAPP_GROUP_JID` for the target group
- `WHATSAPP_INTERNAL_TOKEN` shared by FastAPI and the bridge
- `BAILEYS_PROJECT_DIR` defaults to `PP/whatsapp_bridge`

## Supabase Setup

Run these migrations in order:
- `001_create_poll_prediction.sql`
- `002_create_get_poll_user_history_rpc.sql`
- `003_create_whatsapp_poll_vote_event.sql`
- `004_add_poll_prediction_source_mobile_unique.sql`
- `005_create_whatsapp_poll.sql`
- `006_alter_whatsapp_poll_vote_event_add_normalized_vote.sql`
- `007_create_whatsapp_poll_vote_snapshot.sql`

The live-ingestion schema is:
- `whatsapp_poll`: raw poll definitions
- `whatsapp_poll_vote_event`: immutable raw vote events
- `whatsapp_poll_vote_snapshot`: latest vote per `poll_message_id + voter_jid`
- `poll_prediction`: scoring projection for polls whose options map cleanly to a positive/negative pair

## Local Run

Install Python deps:

```bash
ppvenv/bin/pip install -e ".[dev]"
```

Install bridge deps:

```bash
cd whatsapp_bridge
npm install
cd ..
```

Start the API:

```bash
ppvenv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

When `WHATSAPP_BRIDGE_ENABLED=true`, FastAPI will spawn the bridge automatically.

## Pairing

The first bridge run will create auth state in `whatsapp_bridge/baileys_auth_info`. Keep that directory safe.

## Notes

- All polls are stored generically.
- Only polls with a recognizable binary intent pair are projected into scoring.
- Later vote changes overwrite the latest snapshot, while raw events are still retained.

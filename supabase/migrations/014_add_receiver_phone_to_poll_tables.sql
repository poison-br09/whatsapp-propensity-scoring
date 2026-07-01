-- Add receiver_phone to poll tables so we can track which connected
-- WhatsApp number (logged-in user) observed each poll and vote.
ALTER TABLE whatsapp_poll ADD COLUMN IF NOT EXISTS receiver_phone text;
ALTER TABLE whatsapp_poll_vote_event ADD COLUMN IF NOT EXISTS receiver_phone text;
ALTER TABLE whatsapp_poll_vote_snapshot ADD COLUMN IF NOT EXISTS receiver_phone text;

CREATE INDEX IF NOT EXISTS idx_whatsapp_poll_receiver_phone ON whatsapp_poll (receiver_phone);
CREATE INDEX IF NOT EXISTS idx_whatsapp_poll_vote_snapshot_receiver_phone ON whatsapp_poll_vote_snapshot (receiver_phone);

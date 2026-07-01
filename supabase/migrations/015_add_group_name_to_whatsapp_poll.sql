-- Store the human-readable group name (subject) alongside the JID so the
-- frontend can display group names without a separate lookup.
ALTER TABLE whatsapp_poll ADD COLUMN IF NOT EXISTS group_name text;

ALTER TABLE whatsapp_keyword_match ADD COLUMN IF NOT EXISTS receiver_phone TEXT;

CREATE INDEX IF NOT EXISTS whatsapp_keyword_match_receiver_phone_idx ON whatsapp_keyword_match (receiver_phone);

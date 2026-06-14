CREATE TABLE whatsapp_keyword_match (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    keyword_id UUID NOT NULL REFERENCES whatsapp_keywords(id),
    keyword TEXT NOT NULL,
    group_jid TEXT NOT NULL,
    sender_jid TEXT NOT NULL,
    sender_name TEXT,
    sender_phone TEXT,
    message TEXT NOT NULL,
    message_id TEXT NOT NULL,
    message_date TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT whatsapp_keyword_match_message_id_keyword_id_key UNIQUE (message_id, keyword_id)
);

CREATE INDEX whatsapp_keyword_match_keyword_id_idx ON whatsapp_keyword_match (keyword_id);
CREATE INDEX whatsapp_keyword_match_sender_phone_idx ON whatsapp_keyword_match (sender_phone);
CREATE INDEX whatsapp_keyword_match_message_date_idx ON whatsapp_keyword_match (message_date);
CREATE INDEX whatsapp_keyword_match_group_jid_idx ON whatsapp_keyword_match (group_jid);

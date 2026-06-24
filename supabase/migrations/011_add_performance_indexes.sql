-- Composite: receiver_phone + message_date
-- Covers user-scoped date-range queries: WHERE receiver_phone = ? AND message_date BETWEEN x AND y
CREATE INDEX IF NOT EXISTS whatsapp_keyword_match_receiver_date_idx
    ON whatsapp_keyword_match (receiver_phone, message_date DESC);

-- Composite: keyword + message_date
-- Covers keyword-filtered exports without receiver_phone (superadmin): WHERE keyword IN (...) AND message_date BETWEEN x AND y
CREATE INDEX IF NOT EXISTS whatsapp_keyword_match_keyword_date_idx
    ON whatsapp_keyword_match (keyword, message_date DESC);

-- Composite: receiver_phone + keyword + message_date
-- Covers the full export query (user-scoped): WHERE receiver_phone = ? AND keyword IN (...) AND message_date BETWEEN x AND y ORDER BY message_date DESC
CREATE INDEX IF NOT EXISTS whatsapp_keyword_match_receiver_keyword_date_idx
    ON whatsapp_keyword_match (receiver_phone, keyword, message_date DESC);

-- Partial index on active keywords only
-- Covers keyword lookup during live message processing: WHERE keyword = ? AND is_active = TRUE
CREATE INDEX IF NOT EXISTS whatsapp_keywords_active_idx
    ON whatsapp_keywords (keyword)
    WHERE is_active = TRUE;

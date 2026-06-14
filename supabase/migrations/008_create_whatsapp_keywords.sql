CREATE TABLE whatsapp_keywords (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    keyword TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT whatsapp_keywords_keyword_key UNIQUE (keyword)
);

INSERT INTO whatsapp_keywords (keyword, is_active) VALUES
    ('flat', TRUE),
    ('flats', TRUE),
    ('flatmates', TRUE);

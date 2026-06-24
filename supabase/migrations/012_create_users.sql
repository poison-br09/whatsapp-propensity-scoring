CREATE TABLE users (
    id               UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    username         TEXT        NOT NULL UNIQUE,
    password_hash    TEXT        NOT NULL,
    whatsapp_phone   TEXT        NOT NULL UNIQUE,
    target_group_jid TEXT,
    role             TEXT        NOT NULL DEFAULT 'user',  -- 'user' | 'superadmin'
    is_active        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX users_username_idx  ON users (username);
CREATE INDEX users_is_active_idx ON users (is_active);
CREATE INDEX users_role_idx      ON users (role);

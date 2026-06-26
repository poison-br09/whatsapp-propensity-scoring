-- whatsapp_phone is now optional: superadmins and users without a linked number
-- can exist without it. NULLs are distinct under the existing UNIQUE constraint.
ALTER TABLE users ALTER COLUMN whatsapp_phone DROP NOT NULL;

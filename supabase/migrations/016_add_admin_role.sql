-- Introduce 'admin' role alongside 'user' and 'superadmin'.
-- The role column is plain TEXT with no check constraint, so no DDL change is required.
-- This migration records the new role's introduction for audit purposes.
COMMENT ON COLUMN users.role IS '''user'' | ''admin'' | ''superadmin''';

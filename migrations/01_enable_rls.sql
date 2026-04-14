-- Enable Row Level Security on both tables.
-- The service_role key used by the backend bypasses RLS automatically,
-- so no backend code changes are needed.
-- This blocks any direct access via the anon/public key (e.g. accidental
-- exposure of the Supabase URL + anon key).

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE expenses ENABLE ROW LEVEL SECURITY;

-- Explicit deny-all for non-service roles (belt and suspenders).
-- service_role is exempt from all RLS policies.
CREATE POLICY "deny_public_users"    ON users    FOR ALL USING (false);
CREATE POLICY "deny_public_expenses" ON expenses FOR ALL USING (false);

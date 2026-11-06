-- Run by hand in the Supabase SQL editor (never by the scraper or the API).
-- Creates the least-privilege role the Rust API connects as. Replace the password first.
CREATE ROLE prices_reader LOGIN PASSWORD 'CHANGE_ME' NOINHERIT;
ALTER ROLE prices_reader SET default_transaction_read_only = on;
GRANT USAGE ON SCHEMA public TO prices_reader;
GRANT SELECT ON TABLE public.daily_stats TO prices_reader;
-- If RLS is enabled on daily_stats (recommended, see Spec 0003 section 2), also allow reads:
CREATE POLICY prices_reader_select ON public.daily_stats FOR SELECT TO prices_reader USING (true);
-- Deliberately NOT granted: anything on public.listing, any INSERT/UPDATE/DELETE/DDL.

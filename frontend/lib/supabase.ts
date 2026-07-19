import { createClient } from "@supabase/supabase-js";

// Read-only public client. The anon key is safe to ship: RLS on every table
// allows SELECT only, and writes return HTTP 401. Server-side cron uses the
// service_role key (never exposed here).
const url = process.env.NEXT_PUBLIC_SUPABASE_URL as string;
const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY as string;

export const supabase = createClient(url, anon, {
  auth: { persistSession: false },
});

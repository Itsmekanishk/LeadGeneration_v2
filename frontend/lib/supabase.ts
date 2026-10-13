"use client";

import { createClient, SupabaseClient } from "@supabase/supabase-js";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";

let client: SupabaseClient | null = null;

export function getSupabaseClient(): SupabaseClient | null {
  if (typeof window === "undefined") {
    return null;
  }
  if (!supabaseUrl || !supabaseAnonKey) {
    return null;
  }
  if (!client) {
    client = createClient(supabaseUrl, supabaseAnonKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
        flowType: "pkce",
      },
    });
  }
  return client;
}

export function isSupabaseConfigured(): boolean {
  return Boolean(supabaseUrl && supabaseAnonKey);
}

export interface GoogleSignInResult {
  url?: string;
  error?: string;
}

/**
 * Initiates Gmail / Google OAuth flow via Supabase.
 * Redirects user to Google OAuth consent screen, which returns to /auth/callback.
 */
export async function signInWithGoogle(redirectTo?: string): Promise<GoogleSignInResult> {
  const sb = getSupabaseClient();
  if (!sb) {
    return {
      error:
        "Supabase credentials are not configured. Please set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY in your .env file.",
    };
  }

  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const targetRedirect = redirectTo || `${origin}/auth/callback`;

  try {
    const { data, error } = await sb.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: targetRedirect,
        queryParams: {
          access_type: "offline",
          prompt: "consent",
        },
      },
    });

    if (error) {
      return { error: error.message };
    }

    if (data?.url) {
      // If client-side, Next.js or browser will follow the OAuth URL
      if (typeof window !== "undefined") {
        window.location.href = data.url;
      }
      return { url: data.url };
    }

    return {};
  } catch (err) {
    return {
      error: err instanceof Error ? err.message : "Failed to initiate Google sign-in",
    };
  }
}

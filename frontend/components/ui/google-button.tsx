"use client";

import { useState } from "react";
import { signInWithGoogle, isSupabaseConfigured } from "@/lib/supabase";
import { useToast } from "@/components/legacy-ui";
import { Loader2 } from "lucide-react";

interface GoogleSignInButtonProps {
  label?: string;
  className?: string;
  redirectTo?: string;
}

export function GoogleIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M23.745 12.27c0-.7-.06-1.4-.19-2.07H12v4.51h6.6c-.29 1.52-1.14 2.82-2.4 3.68v3.05h3.88c2.27-2.09 3.66-5.17 3.66-9.17z"
      />
      <path
        fill="#34A853"
        d="M12 24c3.24 0 5.95-1.08 7.93-2.91l-3.88-3.05c-1.08.72-2.45 1.16-4.05 1.16-3.12 0-5.77-2.1-6.72-4.93H1.25v3.15C3.26 21.36 7.33 24 12 24z"
      />
      <path
        fill="#FBBC05"
        d="M5.28 14.27c-.25-.72-.38-1.49-.38-2.27s.13-1.55.38-2.27V6.58H1.25C.45 8.18 0 9.99 0 12s.45 3.82 1.25 5.42l4.03-3.15z"
      />
      <path
        fill="#EA4335"
        d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.33 0 3.26 2.64 1.25 6.58l4.03 3.15c.95-2.83 3.6-4.98 6.72-4.98z"
      />
    </svg>
  );
}

export function GoogleSignInButton({
  label = "Continue with Google",
  className = "",
  redirectTo,
}: GoogleSignInButtonProps) {
  const { push } = useToast();
  const [loading, setLoading] = useState(false);

  async function handleClick() {
    if (!isSupabaseConfigured()) {
      push(
        "Supabase is not configured with valid credentials in .env. Please configure NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY.",
        "error"
      );
      return;
    }

    setLoading(true);
    try {
      const result = await signInWithGoogle(redirectTo);
      if (result.error) {
        push(result.error, "error");
        setLoading(false);
      }
      // If result has url, redirection is handled
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to connect to Google";
      push(msg, "error");
      setLoading(false);
    }
  }

  return (
    <button
      id="google-signin-btn"
      type="button"
      onClick={handleClick}
      disabled={loading}
      className={`relative flex w-full items-center justify-center gap-3 rounded-lg border border-border bg-card px-4 py-2.5 text-sm font-medium text-foreground transition-all duration-150 hover:bg-accent/60 hover:text-accent-foreground active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-primary/40 ${className}`}
    >
      {loading ? (
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
      ) : (
        <GoogleIcon className="h-4 w-4 shrink-0" />
      )}
      <span>{loading ? "Connecting to Google..." : label}</span>
    </button>
  );
}

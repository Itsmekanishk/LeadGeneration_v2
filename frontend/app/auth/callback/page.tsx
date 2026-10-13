"use client";

import { useEffect, useState, useRef, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { getSupabaseClient } from "@/lib/supabase";
import { api, setAuth } from "@/lib/api";
import type { TokenResponse } from "@/types";
import { Card, useToast } from "@/components/legacy-ui";
import { Loader2, AlertCircle, ArrowLeft } from "lucide-react";

function CallbackContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { push } = useToast();
  const [error, setError] = useState<string | null>(null);
  const [statusText, setStatusText] = useState("Verifying your Google authentication...");
  const processedRef = useRef(false);

  useEffect(() => {
    if (processedRef.current) return;

    async function handleExchange(accessToken: string, emailHint?: string, nameHint?: string) {
      setStatusText("Finalizing your account session...");
      try {
        const tokens = await api.post<TokenResponse>("/auth/supabase-login", {
          access_token: accessToken,
          email: emailHint,
          full_name: nameHint,
        });
        setAuth(tokens);
        push(`Welcome back, ${tokens.user.full_name || tokens.user.email}!`, "success");
        router.replace("/dashboard");
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Failed to authenticate session";
        setError(msg);
        push(msg, "error");
      }
    }

    async function processAuth() {
      // 1. Check for mock/direct test token in search params (for headless testing/CI)
      const mockToken = searchParams.get("mock_token") || searchParams.get("test_token");
      if (mockToken) {
        processedRef.current = true;
        const email = searchParams.get("email") || "test.gmail.user@gmail.com";
        const name = searchParams.get("name") || "Test Gmail User";
        await handleExchange(mockToken, email, name);
        return;
      }

      // 2. Check for OAuth error params returned in hash or search
      const hashParams = typeof window !== "undefined"
        ? new URLSearchParams(window.location.hash.replace(/^#/, ""))
        : new URLSearchParams();
      const errorDescription =
        searchParams.get("error_description") ||
        searchParams.get("error") ||
        hashParams.get("error_description") ||
        hashParams.get("error");

      if (errorDescription) {
        processedRef.current = true;
        setError(decodeURIComponent(errorDescription));
        return;
      }

      // 3. Check for access_token directly in hash (implicit grant flow)
      const hashAccessToken = hashParams.get("access_token");
      if (hashAccessToken) {
        processedRef.current = true;
        await handleExchange(hashAccessToken);
        return;
      }

      // 4. Use Supabase client to exchange code or fetch session
      const sb = getSupabaseClient();
      if (!sb) {
        processedRef.current = true;
        setError("Supabase client is not configured.");
        return;
      }

      try {
        // Check if there is an active session
        const { data, error: sessionErr } = await sb.auth.getSession();
        if (sessionErr) {
          throw sessionErr;
        }

        if (data?.session?.access_token) {
          processedRef.current = true;
          const userMeta = data.session.user.user_metadata || {};
          const full_name = userMeta.full_name || userMeta.name || "";
          await handleExchange(data.session.access_token, data.session.user.email, full_name);
          return;
        }

        // Check if PKCE code parameter is present in URL
        const code = searchParams.get("code");
        if (code) {
          setStatusText("Exchanging OAuth authorization code...");
          const { data: exchangeData, error: exchangeErr } = await sb.auth.exchangeCodeForSession(code);
          if (exchangeErr) {
            throw exchangeErr;
          }
          if (exchangeData?.session?.access_token) {
            processedRef.current = true;
            const userMeta = exchangeData.session.user.user_metadata || {};
            const full_name = userMeta.full_name || userMeta.name || "";
            await handleExchange(exchangeData.session.access_token, exchangeData.session.user.email, full_name);
            return;
          }
        }

        // Fallback: listen for authStateChange
        const { data: authListener } = sb.auth.onAuthStateChange(async (event, newSession) => {
          if (processedRef.current) return;
          if (newSession?.access_token) {
            processedRef.current = true;
            authListener.subscription.unsubscribe();
            const userMeta = newSession.user.user_metadata || {};
            const full_name = userMeta.full_name || userMeta.name || "";
            await handleExchange(newSession.access_token, newSession.user.email, full_name);
          }
        });

        // Timeout after 4 seconds if no session is detected
        setTimeout(() => {
          if (!processedRef.current) {
            processedRef.current = true;
            authListener.subscription.unsubscribe();
            setError("No active authentication session was found. Please try signing in again.");
          }
        }, 4000);
      } catch (err) {
        processedRef.current = true;
        const msg = err instanceof Error ? err.message : "Authentication error occurred";
        setError(msg);
      }
    }

    processAuth();
  }, [searchParams, router, push]);

  return (
    <Card className="w-full max-w-sm text-center">
      {error ? (
        <div className="space-y-4">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-red-500/10 text-red-400">
            <AlertCircle className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-foreground">Authentication Failed</h1>
            <p className="mt-1 text-xs text-muted-foreground">{error}</p>
          </div>
          <Link
            href="/login"
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-card px-4 py-2.5 text-sm font-medium text-foreground hover:bg-accent"
          >
            <ArrowLeft className="h-4 w-4" />
            Return to Sign in
          </Link>
        </div>
      ) : (
        <div className="space-y-4 py-4">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
            <Loader2 className="h-6 w-6 animate-spin" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-foreground">Completing Sign-in</h1>
            <p className="mt-1 text-xs text-muted-foreground">{statusText}</p>
          </div>
        </div>
      )}
    </Card>
  );
}

export default function AuthCallbackPage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Suspense
        fallback={
          <Card className="w-full max-w-sm text-center py-8">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Loader2 className="h-6 w-6 animate-spin" />
            </div>
            <p className="mt-4 text-xs text-muted-foreground">Loading session...</p>
          </Card>
        }
      >
        <CallbackContent />
      </Suspense>
    </main>
  );
}

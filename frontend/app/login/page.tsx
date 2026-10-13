"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, setAuth } from "@/lib/api";
import type { TokenResponse } from "@/types";
import { Button, Card, Input, useToast } from "@/components/legacy-ui";
import { GoogleSignInButton } from "@/components/ui/google-button";

export default function LoginPage() {
  const router = useRouter();
  const { push } = useToast();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!email.trim() || !password) {
      setError("Enter your email and password to sign in.");
      return;
    }
    setLoading(true);
    try {
      const tokens = await api.post<TokenResponse>("/auth/login", { email, password });
      setAuth(tokens);
      push(`Welcome back, ${tokens.user.full_name || tokens.user.email}`, "success");
      router.replace("/dashboard");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Login failed";
      setError(msg);
      push(msg, "error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary font-bold text-primary-foreground">
            L
          </div>
          <div>
            <h1 className="text-lg font-bold leading-tight">LocalLead Intelligence</h1>
            <p className="text-xs text-muted-foreground">Sign in to your workspace</p>
          </div>
        </div>
        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="label" htmlFor="email">Email</label>
            <Input
              id="email" type="email" required autoComplete="email"
              placeholder="you@company.com" value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label className="label" htmlFor="password">Password</label>
            <Input
              id="password" type="password" required autoComplete="current-password"
              placeholder="••••••••" value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error && (
            <p role="alert" className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
              {error}
            </p>
          )}
          <Button type="submit" loading={loading} className="w-full">Sign in</Button>
        </form>

        <div className="relative my-4 flex items-center justify-center">
          <div className="w-full border-t border-border"></div>
          <span className="bg-card px-2 text-xs uppercase tracking-wider text-muted-foreground">Or</span>
        </div>

        <GoogleSignInButton label="Continue with Gmail" />

        <div className="mt-4 flex justify-between text-sm">
          <Link className="text-primary hover:underline" href="/register">
            Create account
          </Link>
          <Link className="text-muted-foreground hover:underline" href="/forgot-password">
            Forgot password?
          </Link>
        </div>
      </Card>
    </main>
  );
}

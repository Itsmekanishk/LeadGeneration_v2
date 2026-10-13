"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, setAuth } from "@/lib/api";
import type { TokenResponse } from "@/types";
import { Button, Card, Input, useToast } from "@/components/legacy-ui";
import { GoogleSignInButton } from "@/components/ui/google-button";

export default function RegisterPage() {
  const router = useRouter();
  const { push } = useToast();
  const [form, setForm] = useState({ full_name: "", email: "", password: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!form.full_name.trim() || !form.email.trim() || form.password.length < 8) {
      setError("Fill in your name, email and a password of at least 8 characters.");
      return;
    }
    setLoading(true);
    try {
      const tokens = await api.post<TokenResponse>("/auth/register", form);
      setAuth(tokens);
      push("Account created", "success");
      router.replace("/dashboard");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Registration failed";
      setError(msg);
      push(msg, "error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <h1 className="mb-1 text-lg font-bold">Create your account</h1>
        <p className="mb-6 text-xs text-muted-foreground">
          Start finding verified business contacts in minutes.
        </p>
        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="label" htmlFor="name">Full name</label>
            <Input
              id="name" required autoComplete="name" placeholder="Aditi Sharma"
              value={form.full_name}
              onChange={(e) => setForm({ ...form, full_name: e.target.value })}
            />
          </div>
          <div>
            <label className="label" htmlFor="email">Email</label>
            <Input
              id="email" type="email" required autoComplete="email"
              placeholder="you@company.com" value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
            />
          </div>
          <div>
            <label className="label" htmlFor="password">Password</label>
            <Input
              id="password" type="password" required minLength={8}
              autoComplete="new-password" placeholder="At least 8 characters"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
          </div>
          {error && (
            <p role="alert" className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
              {error}
            </p>
          )}
          <Button type="submit" loading={loading} className="w-full">
            Create account
          </Button>
        </form>

        <div className="relative my-4 flex items-center justify-center">
          <div className="w-full border-t border-border"></div>
          <span className="bg-card px-2 text-xs uppercase tracking-wider text-muted-foreground">Or</span>
        </div>

        <GoogleSignInButton label="Sign up with Gmail" />

        <p className="mt-4 text-sm">
          Already have an account?{" "}
          <Link className="text-primary hover:underline" href="/login">
            Sign in
          </Link>
        </p>
      </Card>
    </main>
  );
}

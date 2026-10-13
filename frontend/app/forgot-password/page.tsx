"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { Button, Card, Input, useToast } from "@/components/legacy-ui";

export default function ForgotPasswordPage() {
  const { push } = useToast();
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [stage, setStage] = useState<"request" | "reset">("request");
  const [devToken, setDevToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function requestReset(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!email.trim()) {
      setError("Enter the email you registered with.");
      return;
    }
    setLoading(true);
    try {
      const res = await api.post<{ message: string; dev_token?: string }>(
        "/auth/forgot-password",
        { email },
      );
      push(res.message, "info");
      if (res.dev_token) {
        setDevToken(res.dev_token);
        push("Development mode: reset token pre-filled below.", "info");
        setToken(res.dev_token);
      }
      setStage("reset");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Request failed";
      setError(msg);
      push(msg, "error");
    } finally {
      setLoading(false);
    }
  }

  async function doReset(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      await api.post("/auth/reset-password", { token, new_password: newPassword });
      push("Password updated. You can sign in now.", "success");
      setStage("request");
      setToken("");
      setNewPassword("");
    } catch (err) {
      push(err instanceof Error ? err.message : "Reset failed", "error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <h1 className="mb-1 text-lg font-bold">Reset your password</h1>
        <p className="mb-6 text-xs text-muted-foreground">
          {stage === "request"
            ? "We'll email you a secure reset link."
            : "Enter the token from the email and choose a new password."}
        </p>
        {stage === "request" ? (
          <form onSubmit={requestReset} className="space-y-4">
            <div>
              <label className="label" htmlFor="email">Email</label>
              <Input
                id="email" type="email" required placeholder="you@company.com"
                value={email} onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            {error && (
              <p role="alert" className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
                {error}
              </p>
            )}
            <Button type="submit" loading={loading} className="w-full">
              Send reset link
            </Button>
          </form>
        ) : (
          <form onSubmit={doReset} className="space-y-4">
            <div>
              <label className="label" htmlFor="token">Reset token</label>
              <Input
                id="token" required value={token}
                onChange={(e) => setToken(e.target.value)}
              />
            </div>
            <div>
              <label className="label" htmlFor="new-password">New password</label>
              <Input
                id="new-password" type="password" required minLength={8}
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
            </div>
            <Button type="submit" loading={loading} className="w-full">
              Set new password
            </Button>
          </form>
        )}
        <p className="mt-4 text-sm">
          <Link className="text-primary hover:underline" href="/login">
            ← Back to sign in
          </Link>
        </p>
      </Card>
    </main>
  );
}

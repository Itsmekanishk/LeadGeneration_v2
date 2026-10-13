"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, getStoredUser } from "@/lib/api";
import type { User } from "@/types";
import { Badge, Card } from "@/components/legacy-ui";

export default function SettingsPage() {
  const [profile, setProfile] = useState<User | null>(null);

  useEffect(() => {
    api.get<User>("/auth/me").then(setProfile).catch(() => setProfile(getStoredUser()));
  }, []);

  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Settings</h1>

      <Card>
        <h2 className="mb-3 font-semibold">Profile</h2>
        {profile ? (
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Email</dt>
              <dd className="font-medium">{profile.email}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Name</dt>
              <dd className="font-medium">{profile.full_name || "—"}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Role</dt>
              <dd>
                <Badge tone={profile.role === "ADMIN" ? "info" : "neutral"}>
                  {profile.role}
                </Badge>
              </dd>
            </div>
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">Loading…</p>
        )}
      </Card>

      <Card>
        <h2 className="mb-2 font-semibold">Security</h2>
        <p className="mb-3 text-sm text-muted-foreground">
          Use a strong, unique password. You can rotate it any time via the secure reset flow.
        </p>
        <Link className="btn-outline" href="/forgot-password">
          Change password
        </Link>
      </Card>

      <Card>
        <h2 className="mb-2 font-semibold">Data &amp; compliance</h2>
        <p className="text-sm text-muted-foreground">
          Business data is retrieved from the configured official provider. Website contact
          details are collected only from publicly accessible pages, respecting robots.txt and
          rate limits. Every stored contact keeps its source URL and confidence level.
        </p>
      </Card>
    </div>
  );
}

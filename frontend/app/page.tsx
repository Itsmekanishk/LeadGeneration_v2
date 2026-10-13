"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getStoredUser } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    const user = getStoredUser();
    router.replace(user ? "/dashboard" : "/login");
    setChecked(true);
  }, [router]);

  return (
    <main className="flex min-h-screen items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <span className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        <p className="text-sm text-muted-foreground">Loading LocalLead…</p>
      </div>
    </main>
  );
}

"use client";

import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  className?: string;
}

export function Drawer({ open, onClose, title, description, children, className }: DrawerProps) {
  React.useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    if (open) document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            key="backdrop"
            className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            aria-hidden
          />
          <motion.aside
            key="panel"
            role="dialog"
            aria-modal="true"
            aria-label={title}
            className={cn(
              "fixed right-0 top-0 z-50 h-full w-full max-w-md overflow-y-auto border-l ll-border",
              "bg-ll-surface-elevated/95 backdrop-blur-xl",
              className
            )}
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 340, damping: 34 }}
          >
            <div className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b ll-border bg-ll-surface-elevated/95 p-5 backdrop-blur-xl">
              <div>
                <h2 className="text-sm font-medium text-slate-100">{title}</h2>
                {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
              </div>
              <button
                onClick={onClose}
                aria-label="Close panel"
                className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-white/[0.06] hover:text-slate-200"
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="p-5">{children}</div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}

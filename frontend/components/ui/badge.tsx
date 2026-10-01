import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-sm border px-2 py-0.5 text-xs font-medium",
  {
    variants: {
      variant: {
        neutral: "border-border bg-surface-alt text-muted",
        connected: "border-profit/30 bg-profit/10 text-profit",
        pending: "border-warning/30 bg-warning/10 text-warning",
        disconnected: "border-loss/30 bg-loss/10 text-loss",
        accent: "border-accent/30 bg-accent/10 text-accent",
        caution: "border-warning bg-warning text-accent-foreground",
      },
    },
    defaultVariants: {
      variant: "neutral",
    },
  }
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

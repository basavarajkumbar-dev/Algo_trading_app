import Link from "next/link";
import type { ReactNode } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/** A dashboard card with a title, an optional "open page" link and load/error states. */
export function Panel({
  title,
  href,
  linkLabel = "Open",
  aside,
  className,
  bodyClassName,
  loading,
  error,
  children,
  testId,
}: {
  title: string;
  href?: string;
  linkLabel?: string;
  aside?: ReactNode;
  className?: string;
  bodyClassName?: string;
  loading?: boolean;
  error?: string | null;
  children?: ReactNode;
  testId?: string;
}) {
  return (
    <Card className={cn("flex min-w-0 flex-col", className)} data-testid={testId}>
      <CardHeader className="gap-3">
        <CardTitle>{title}</CardTitle>
        <div className="flex items-center gap-3">
          {aside}
          {href && (
            <Link href={href} className="text-xs text-accent hover:underline">
              {linkLabel} →
            </Link>
          )}
        </div>
      </CardHeader>
      <CardContent className={cn("min-h-0 flex-1", bodyClassName)}>
        {error ? (
          <p className="text-sm text-loss">{error}</p>
        ) : loading ? (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        ) : (
          children
        )}
      </CardContent>
    </Card>
  );
}

export function errorText(error: unknown): string | null {
  if (!error) return null;
  return error instanceof Error ? error.message : "Request failed";
}

/** label / value / optional hint, for dense key figures. */
export function Figure({
  label,
  children,
  hint,
  className,
  testId,
}: {
  label: string;
  children: ReactNode;
  hint?: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1", className)} data-testid={testId}>
      <span className="text-[10px] uppercase tracking-wide text-muted">{label}</span>
      <span className="tabular truncate text-base font-medium">{children}</span>
      {hint && <span className="truncate text-[11px] text-muted">{hint}</span>}
    </div>
  );
}

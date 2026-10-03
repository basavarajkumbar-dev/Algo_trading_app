"use client";

import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { useApprovals } from "@/lib/queries";

/** Top bar: how many trades wait for approval (APPROVAL mode only). */
export function PendingApprovals() {
  const { data } = useApprovals();
  const n = data?.items.filter((a) => a.status === "PENDING").length ?? 0;
  return (
    <Link href="/approvals" data-testid="topbar-approvals">
      <Badge variant={n ? "caution" : "neutral"}>{n} to approve</Badge>
    </Link>
  );
}

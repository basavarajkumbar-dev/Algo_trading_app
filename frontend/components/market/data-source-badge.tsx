"use client";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { dataNoun, dataSourceLabel } from "@/lib/market-data-format";
import { useMarketDataStatus } from "@/lib/queries";
import { cn } from "@/lib/utils";

/** "DATA SOURCE: ZERODHA LIVE" or "SIMULATED" — shown on every page (header). */
export function DataSourceBadge({ className }: { className?: string }) {
  const md = useMarketDataStatus().data;
  const label = dataSourceLabel(md);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant={label.variant} className={cn("whitespace-nowrap", className)} data-testid="data-source-badge">
          <span className="text-[9px] opacity-70">DATA SOURCE</span>
          {label.text}
        </Badge>
      </TooltipTrigger>
      <TooltipContent>{label.detail}</TooltipContent>
    </Tooltip>
  );
}

/** "simulated" or "Zerodha" for page descriptions. */
export function useDataNoun(): string {
  return dataNoun(useMarketDataStatus().data);
}

/** Inline "simulated" / "Zerodha" for descriptive text. */
export function DataNoun() {
  return <>{useDataNoun()}</>;
}

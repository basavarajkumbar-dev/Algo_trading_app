import { Badge } from "@/components/ui/badge";
import { REGIME_META } from "@/lib/intelligence-format";
import type { MarketRegime } from "@/types/intelligence";

export function RegimeBadge({
  regime,
  short = false,
  className,
}: {
  regime: MarketRegime;
  short?: boolean;
  className?: string;
}) {
  const meta = REGIME_META[regime];
  return (
    <Badge variant={meta.variant} className={className} title={meta.description}>
      {short ? meta.short : meta.label}
    </Badge>
  );
}

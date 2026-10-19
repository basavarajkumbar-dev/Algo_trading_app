import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import type { RootStackParamList } from "../navigation/types";

/** Polling query: refreshes while the screen is shown; failures are kept
 * visible (never replaced by stale or invented numbers). */
export function usePoll<T>(key: string[], fn: () => Promise<T>, intervalMs = 10_000): UseQueryResult<T> {
  return useQuery({ queryKey: key, queryFn: fn, refetchInterval: intervalMs });
}

export function useStackNav() {
  return useNavigation<NativeStackNavigationProp<RootStackParamList>>();
}

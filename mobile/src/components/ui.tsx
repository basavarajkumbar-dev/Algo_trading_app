/** Shared building blocks: screen frame, cards, rows, badges, buttons and
 * the loading / empty / error / offline states every screen uses. */

import React from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type TextStyle,
  type ViewStyle,
} from "react-native";
import Svg, { Line, Polyline } from "react-native-svg";

import { ApiError, describeError } from "../api/client";
import { colors, fonts, radius, space, toneColors, type Tone } from "../theme";

export function Screen({
  children,
  refreshing = false,
  onRefresh,
  testID,
}: {
  children: React.ReactNode;
  refreshing?: boolean;
  onRefresh?: () => void;
  testID?: string;
}) {
  return (
    <ScrollView
      testID={testID}
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={
        onRefresh ? (
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={colors.accent}
            colors={[colors.accent]}
            progressBackgroundColor={colors.surface}
          />
        ) : undefined
      }
    >
      {children}
    </ScrollView>
  );
}

export function Card({
  title,
  right,
  children,
  style,
}: {
  title?: string;
  right?: React.ReactNode;
  children?: React.ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  return (
    <View style={[styles.card, style]}>
      {title || right ? (
        <View style={styles.cardHeader}>
          {title ? <Text style={styles.cardTitle}>{title}</Text> : <View />}
          {right}
        </View>
      ) : null}
      {children}
    </View>
  );
}

export function Row({
  label,
  value,
  tone = "neutral",
  mono = true,
}: {
  label: string;
  value: React.ReactNode;
  tone?: Tone;
  mono?: boolean;
}) {
  const fg = tone === "neutral" ? colors.text : toneColors(tone).fg;
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      {typeof value === "string" || typeof value === "number" ? (
        <Text style={[styles.rowValue, mono && styles.mono, { color: fg }]} numberOfLines={2}>
          {value}
        </Text>
      ) : (
        value
      )}
    </View>
  );
}

export function Stat({
  label,
  value,
  tone = "neutral",
  hint,
}: {
  label: string;
  value: string;
  tone?: Tone;
  hint?: string;
}) {
  const fg = tone === "neutral" ? colors.text : toneColors(tone).fg;
  return (
    <View style={styles.stat}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={[styles.statValue, { color: fg }]} numberOfLines={1} adjustsFontSizeToFit>
        {value}
      </Text>
      {hint ? <Text style={styles.statHint}>{hint}</Text> : null}
    </View>
  );
}

export function StatGrid({ children }: { children: React.ReactNode }) {
  return <View style={styles.statGrid}>{children}</View>;
}

export function Badge({ label, tone = "neutral" }: { label: string; tone?: Tone }) {
  const { fg, bg } = toneColors(tone);
  return (
    <View style={[styles.badge, { backgroundColor: bg }]}>
      <Text style={[styles.badgeText, { color: fg }]}>{label}</Text>
    </View>
  );
}

/** Where the numbers on a screen come from. Simulated data is always said. */
export function SourceBadge({ simulated, source }: { simulated: boolean | null; source?: string }) {
  if (simulated === null) return <Badge label="SOURCE UNKNOWN" tone="warn" />;
  return simulated ? (
    <Badge label="SIMULATED DATA" tone="warn" />
  ) : (
    <Badge label={`REAL DATA${source ? ` · ${source}` : ""}`} tone="good" />
  );
}

export function Button({
  title,
  onPress,
  tone = "accent",
  disabled,
  busy,
  testID,
}: {
  title: string;
  onPress: () => void;
  tone?: Tone;
  disabled?: boolean;
  busy?: boolean;
  testID?: string;
}) {
  const { fg, bg } = toneColors(tone);
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityState={{ disabled: !!disabled || !!busy }}
      onPress={onPress}
      disabled={disabled || busy}
      style={({ pressed }) => [
        styles.button,
        { backgroundColor: bg, borderColor: fg, opacity: disabled ? 0.45 : pressed ? 0.75 : 1 },
      ]}
    >
      {busy ? <ActivityIndicator color={fg} /> : <Text style={[styles.buttonText, { color: fg }]}>{title}</Text>}
    </Pressable>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <View style={styles.state} testID="state-loading">
      <ActivityIndicator color={colors.accent} />
      <Text style={styles.stateText}>{label}</Text>
    </View>
  );
}

export function Empty({ title, detail }: { title: string; detail?: string }) {
  return (
    <View style={styles.state} testID="state-empty">
      <Text style={styles.stateTitle}>{title}</Text>
      {detail ? <Text style={styles.stateText}>{detail}</Text> : null}
    </View>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const offline = error instanceof ApiError && (error.kind === "OFFLINE" || error.kind === "TIMEOUT");
  return (
    <View style={[styles.state, styles.errorBox]} testID={offline ? "state-offline" : "state-error"}>
      <Text style={[styles.stateTitle, { color: offline ? colors.warn : colors.loss }]}>
        {offline ? "Backend unreachable" : "Could not load"}
      </Text>
      <Text style={styles.stateText}>{describeError(error)}</Text>
      {onRetry ? <Button title="Retry" onPress={onRetry} tone="neutral" /> : null}
    </View>
  );
}

/** Renders loading / error states for a query, or the children with data. */
export function QueryView<T>({
  query,
  children,
}: {
  query: { data?: T; error: unknown; isLoading: boolean; refetch: () => unknown };
  children: (data: T) => React.ReactNode;
}) {
  if (query.isLoading && query.data === undefined) return <Loading />;
  if (query.error && query.data === undefined) {
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  }
  if (query.data === undefined) return <Empty title="No data" />;
  return (
    <>
      {query.error ? <ErrorState error={query.error} onRetry={() => void query.refetch()} /> : null}
      {children(query.data)}
    </>
  );
}

export function Note({ children, style }: { children: React.ReactNode; style?: StyleProp<TextStyle> }) {
  return <Text style={[styles.note, style]}>{children}</Text>;
}

export function SectionTitle({ children }: { children: React.ReactNode }) {
  return <Text style={styles.section}>{children}</Text>;
}

export function Sparkline({
  values,
  width = 300,
  height = 64,
  baseline,
}: {
  values: number[];
  width?: number;
  height?: number;
  baseline?: number;
}) {
  if (values.length < 2) return null;
  const lo = Math.min(...values, baseline ?? Infinity);
  const hi = Math.max(...values, baseline ?? -Infinity);
  const span = hi - lo || 1;
  const x = (i: number) => (i / (values.length - 1)) * width;
  const y = (v: number) => height - ((v - lo) / span) * (height - 4) - 2;
  const up = values.at(-1)! >= values[0];
  const points = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <Svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      {baseline !== undefined ? (
        <Line x1={0} x2={width} y1={y(baseline)} y2={y(baseline)} stroke={colors.border} strokeDasharray="4 4" />
      ) : null}
      <Polyline points={points} fill="none" stroke={up ? colors.profit : colors.loss} strokeWidth={2} />
    </Svg>
  );
}

export const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: space.lg, gap: space.md, paddingBottom: space.xl * 2 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: space.lg,
    gap: space.sm,
  },
  cardHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  cardTitle: { color: colors.text, fontSize: 15, fontWeight: "700" },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: space.md,
    paddingVertical: 3,
  },
  rowLabel: { color: colors.muted, fontSize: 13, flexShrink: 1 },
  rowValue: { color: colors.text, fontSize: 13, textAlign: "right", flexShrink: 1 },
  mono: { fontFamily: fonts.mono },
  stat: {
    flexBasis: "47%",
    flexGrow: 1,
    backgroundColor: colors.surfaceAlt,
    borderRadius: radius.md,
    padding: space.md,
  },
  statLabel: { color: colors.muted, fontSize: 11, textTransform: "uppercase", letterSpacing: 0.6 },
  statValue: { color: colors.text, fontSize: 20, fontWeight: "700", fontFamily: fonts.mono, marginTop: 2 },
  statHint: { color: colors.faint, fontSize: 11, marginTop: 2 },
  statGrid: { flexDirection: "row", flexWrap: "wrap", gap: space.sm },
  badge: { borderRadius: 999, paddingHorizontal: 8, paddingVertical: 3, alignSelf: "flex-start" },
  badgeText: { fontSize: 11, fontWeight: "700", letterSpacing: 0.4 },
  button: {
    minHeight: 44,
    borderRadius: radius.md,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: space.lg,
  },
  buttonText: { fontSize: 15, fontWeight: "700" },
  state: { alignItems: "center", gap: space.sm, padding: space.xl },
  stateTitle: { color: colors.text, fontSize: 15, fontWeight: "700", textAlign: "center" },
  stateText: { color: colors.muted, fontSize: 13, textAlign: "center" },
  errorBox: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
  },
  note: { color: colors.faint, fontSize: 12, lineHeight: 17 },
  section: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.8,
    marginTop: space.sm,
  },
});

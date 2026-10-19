import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { DarkTheme, NavigationContainer, type Theme } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import React from "react";
import { Text } from "react-native";

import { useAuth } from "../auth/AuthProvider";
import { BacktestDetailScreen, BacktestsScreen, PerformanceScreen, StrategyAnalyticsScreen, StrategyConfigScreen } from "../screens/analysis";
import { BrokerConnectionsScreen, ZerodhaScreen } from "../screens/broker";
import { DashboardScreen, LoginScreen, SettingsScreen, SplashView } from "../screens/core";
import { MarketScreen, ScannerScreen, SignalDetailScreen, SignalsScreen, UniverseScreen } from "../screens/market";
import { MoreScreen, NotificationsScreen, SystemHealthScreen } from "../screens/system";
import { OrdersScreen, PaperTradingScreen, PositionsScreen, RiskScreen, TradeHistoryScreen } from "../screens/trading";
import { colors } from "../theme";
import type { RootStackParamList, TabParamList } from "./types";

const Stack = createNativeStackNavigator<RootStackParamList>();
const Tab = createBottomTabNavigator<TabParamList>();

const theme: Theme = {
  ...DarkTheme,
  colors: {
    ...DarkTheme.colors,
    primary: colors.accent,
    background: colors.bg,
    card: colors.surface,
    text: colors.text,
    border: colors.border,
    notification: colors.loss,
  },
};

const ICONS: Record<keyof TabParamList, string> = {
  Command: "◉",
  Scanner: "⌕",
  Paper: "⇅",
  Backtests: "⟲",
  More: "☰",
};

function Tabs() {
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        headerStyle: { backgroundColor: colors.surface },
        headerTintColor: colors.text,
        tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border },
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.muted,
        tabBarIcon: ({ color }) => <Text style={{ color, fontSize: 18 }}>{ICONS[route.name]}</Text>,
      })}
    >
      <Tab.Screen name="Command" component={DashboardScreen} options={{ title: "Command Center" }} />
      <Tab.Screen name="Scanner" component={ScannerScreen} />
      <Tab.Screen name="Paper" component={PaperTradingScreen} options={{ title: "Paper trading" }} />
      <Tab.Screen name="Backtests" component={BacktestsScreen} />
      <Tab.Screen name="More" component={MoreScreen} />
    </Tab.Navigator>
  );
}

export function RootNavigator() {
  const { ready, user } = useAuth();
  if (!ready) return <SplashView />;
  return (
    <NavigationContainer theme={theme}>
      {user ? (
        <Stack.Navigator
          screenOptions={{
            headerStyle: { backgroundColor: colors.surface },
            headerTintColor: colors.text,
            contentStyle: { backgroundColor: colors.bg },
          }}
        >
          <Stack.Screen name="Tabs" component={Tabs} options={{ headerShown: false }} />
          <Stack.Screen name="Market" component={MarketScreen} options={{ title: "Market overview" }} />
          <Stack.Screen name="Universe" component={UniverseScreen} options={{ title: "Stock universe" }} />
          <Stack.Screen name="Signals" component={SignalsScreen} options={{ title: "Signals" }} />
          <Stack.Screen name="SignalDetail" component={SignalDetailScreen} options={{ title: "Signal" }} />
          <Stack.Screen name="Positions" component={PositionsScreen} options={{ title: "Open positions" }} />
          <Stack.Screen name="Orders" component={OrdersScreen} />
          <Stack.Screen name="TradeHistory" component={TradeHistoryScreen} options={{ title: "Trade history" }} />
          <Stack.Screen name="BacktestDetail" component={BacktestDetailScreen} options={{ title: "Backtest" }} />
          <Stack.Screen name="StrategyAnalytics" component={StrategyAnalyticsScreen} options={{ title: "Strategy analytics" }} />
          <Stack.Screen name="Performance" component={PerformanceScreen} options={{ title: "Performance" }} />
          <Stack.Screen name="Risk" component={RiskScreen} options={{ title: "Risk management" }} />
          <Stack.Screen name="Notifications" component={NotificationsScreen} options={{ title: "Notifications" }} />
          <Stack.Screen name="SystemHealth" component={SystemHealthScreen} options={{ title: "System health" }} />
          <Stack.Screen name="StrategyConfig" component={StrategyConfigScreen} options={{ title: "Strategy configuration" }} />
          <Stack.Screen name="Settings" component={SettingsScreen} />
          <Stack.Screen name="BrokerConnections" component={BrokerConnectionsScreen} options={{ title: "Broker connections" }} />
          <Stack.Screen name="Zerodha" component={ZerodhaScreen} options={{ title: "Zerodha Kite Connect" }} />
        </Stack.Navigator>
      ) : (
        <LoginScreen />
      )}
    </NavigationContainer>
  );
}

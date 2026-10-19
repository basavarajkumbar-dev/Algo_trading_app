import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import React, { useEffect } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AuthProvider, useAuth } from "./src/auth/AuthProvider";
import { RootNavigator } from "./src/navigation/RootNavigator";

// Keep the native splash until the session is restored.
void SplashScreen.preventAutoHideAsync().catch(() => undefined);

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 2_000, refetchOnWindowFocus: true, retry: 1 } },
});

function SplashGate() {
  const { ready } = useAuth();
  useEffect(() => {
    if (ready) SplashScreen.hide();
  }, [ready]);
  return <RootNavigator />;
}

export default function App() {
  return (
    <SafeAreaProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <StatusBar style="light" />
          <SplashGate />
        </AuthProvider>
      </QueryClientProvider>
    </SafeAreaProvider>
  );
}

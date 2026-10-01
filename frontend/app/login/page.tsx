"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import type { FormEvent, ReactNode } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api";

type Mode = "login" | "register";

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-background text-sm text-muted">
          Loading…
        </div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const { login, register } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();

  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, password, fullName);
      }
      const redirectTo = searchParams.get("from") || "/";
      router.replace(redirectTo);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2">
          <span className="h-2 w-2 rounded-full bg-accent" aria-hidden />
          <span className="text-sm font-semibold tracking-wide">GAINZALGO</span>
          <span className="text-xs text-muted">PRO MAX</span>
        </div>

        <Card>
          <CardHeader className="flex-col items-start gap-1">
            <CardTitle className="text-base text-foreground">
              {mode === "login" ? "Sign in" : "Create an account"}
            </CardTitle>
            <p className="text-xs text-muted">
              Paper-trading demo platform. No real market data or broker is connected.
            </p>
          </CardHeader>
          <CardContent>
            <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
              {mode === "register" && (
                <Field label="Full name (optional)">
                  <input
                    type="text"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    className="w-full rounded border border-border bg-surface-alt px-3 py-2 text-sm outline-none focus:border-accent"
                  />
                </Field>
              )}

              <Field label="Email">
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full rounded border border-border bg-surface-alt px-3 py-2 text-sm outline-none focus:border-accent"
                />
              </Field>

              <Field label="Password">
                <input
                  type="password"
                  required
                  minLength={mode === "register" ? 8 : undefined}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full rounded border border-border bg-surface-alt px-3 py-2 text-sm outline-none focus:border-accent"
                />
              </Field>

              {error && <p className="text-xs text-loss">{error}</p>}

              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting
                  ? "Please wait…"
                  : mode === "login"
                    ? "Sign in"
                    : "Create account"}
              </Button>
            </form>

            <button
              type="button"
              onClick={() => {
                setError(null);
                setMode(mode === "login" ? "register" : "login");
              }}
              className="mt-4 w-full text-center text-xs text-muted hover:text-foreground"
            >
              {mode === "login"
                ? "Need an account? Register"
                : "Already have an account? Sign in"}
            </button>
          </CardContent>
        </Card>

        <p className="mt-4 text-center text-xs text-muted">
          Demo login: <span className="tabular">demo@example.com</span> /{" "}
          <span className="tabular">demo-paper-trading-123</span> (seeded via{" "}
          <code className="tabular">make seed</code>)
        </p>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-xs text-muted">
      {label}
      {children}
    </label>
  );
}

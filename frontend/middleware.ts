import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Redirects unauthenticated requests to /login before any page renders.
 *
 * This checks a non-sensitive `gainzalgo_authed` flag cookie (see
 * lib/auth-storage.ts) rather than a real token — middleware runs on
 * the edge and cannot read localStorage, and this flag is enough to
 * avoid a client-side redirect flash. The actual bearer tokens are
 * still validated (or rejected) by the backend on every real API call,
 * so this is a UX improvement, not the security boundary.
 */
const PUBLIC_PATHS = ["/login"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const isPublicPath = PUBLIC_PATHS.some((path) => pathname === path);
  const isAuthed = request.cookies.get("gainzalgo_authed")?.value === "1";

  if (!isPublicPath && !isAuthed) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("from", pathname);
    return NextResponse.redirect(loginUrl);
  }

  if (isPublicPath && isAuthed) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * Match all paths except:
     * - _next/static, _next/image (Next.js internals)
     * - favicon.ico and other static assets
     */
    "/((?!_next/static|_next/image|favicon.ico).*)",
  ],
};

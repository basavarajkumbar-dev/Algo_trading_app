import { ApiError, configureClient, describeError, get, post } from "../src/api/client";

function response(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

describe("api client", () => {
  beforeEach(() => configureClient({ baseUrl: "https://api.example.com", tokens: null, onTokens: () => undefined }));

  it("refuses to call without a backend address", async () => {
    configureClient({ baseUrl: null });
    await expect(get("/system/health")).rejects.toMatchObject({ kind: "NO_BACKEND" });
  });

  it("sends the access token and parses JSON", async () => {
    const fetchImpl = jest.fn(async () => response(200, { ok: true }));
    configureClient({ fetchImpl: fetchImpl as never, tokens: { accessToken: "A", refreshToken: "R" } });
    await expect(get<{ ok: boolean }>("/trading/status")).resolves.toEqual({ ok: true });
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.example.com/api/v1/trading/status");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer A");
  });

  it("maps a network failure to OFFLINE", async () => {
    configureClient({ fetchImpl: (async () => { throw new TypeError("Network request failed"); }) as never });
    const error = (await get("/market/status").catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.kind).toBe("OFFLINE");
    expect(describeError(error)).toMatch(/unreachable/i);
  });

  it("refreshes once on 401, retries, and stores the new tokens", async () => {
    const onTokens = jest.fn();
    const calls: string[] = [];
    const fetchImpl = jest.fn(async (url: string, init: RequestInit) => {
      calls.push(`${init.method} ${url.split("/api/v1")[1]} ${new Headers(init.headers).get("Authorization") ?? "-"}`);
      if (url.endsWith("/auth/refresh")) return response(200, { access_token: "A2", refresh_token: "R2" });
      return new Headers(init.headers).get("Authorization") === "Bearer A2" ? response(200, { ok: 1 }) : response(401, { detail: "expired" });
    });
    configureClient({ fetchImpl: fetchImpl as never, tokens: { accessToken: "A1", refreshToken: "R1" }, onTokens });
    await expect(get("/trading/status")).resolves.toEqual({ ok: 1 });
    expect(calls).toEqual(["GET /trading/status Bearer A1", "POST /auth/refresh -", "GET /trading/status Bearer A2"]);
    expect(onTokens).toHaveBeenCalledWith({ accessToken: "A2", refreshToken: "R2" });
  });

  it("signs out when the refresh fails", async () => {
    const onTokens = jest.fn();
    const fetchImpl = jest.fn(async () => response(401, { detail: "expired" }));
    configureClient({ fetchImpl: fetchImpl as never, tokens: { accessToken: "A", refreshToken: "R" }, onTokens });
    await expect(get("/trading/status")).rejects.toMatchObject({ kind: "UNAUTHORIZED" });
    expect(onTokens).toHaveBeenCalledWith(null);
  });

  it("never retries a POST that failed (no duplicate actions)", async () => {
    const fetchImpl = jest.fn(async () => response(503, { detail: "not ready" }));
    configureClient({ fetchImpl: fetchImpl as never, tokens: { accessToken: "A", refreshToken: "R" } });
    await expect(post("/trading/signals/x/execute")).rejects.toMatchObject({ kind: "UNAVAILABLE" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("explains validation errors from FastAPI", async () => {
    const fetchImpl = jest.fn(async () => response(422, { detail: [{ loc: ["body", "mode"], msg: "bad value" }] }));
    configureClient({ fetchImpl: fetchImpl as never });
    await expect(post("/trading/mode", { mode: "X" })).rejects.toThrow("mode: bad value");
  });
});

import { isEncrypted, normalizeUrl } from "../src/config";
import { DASH, ageLabel, inr, num, pnlTone, rMultiple, signed, timeIst, titleCase } from "../src/lib/format";

describe("format", () => {
  it("never invents a number", () => {
    expect(num(null)).toBe(DASH);
    expect(inr(undefined)).toBe(DASH);
    expect(rMultiple(null)).toBe(DASH);
  });

  it("formats rupees, signs and R", () => {
    expect(inr(-1234.5)).toBe("-₹1,234.50");
    expect(signed(2.5, 1, "%")).toBe("+2.5%");
    expect(rMultiple(-1.2)).toBe("-1.20R");
    expect(pnlTone(-1)).toBe("bad");
    expect(pnlTone(0)).toBe("neutral");
  });

  it("shows IST times", () => {
    const now = new Date("2026-10-19T09:00:00Z");
    expect(timeIst("2026-10-19T08:35:00Z", now)).toBe("14:05 IST");
    expect(timeIst("2026-10-17T08:35:00Z", now)).toBe("17 Oct 14:05 IST");
    expect(timeIst(null)).toBe(DASH);
  });

  it("labels ages and enum words", () => {
    expect(ageLabel(null)).toBe("no data");
    expect(ageLabel(42)).toBe("42s ago");
    expect(ageLabel(600)).toBe("10m ago");
    expect(titleCase("STRONG_BEARISH")).toBe("Strong bearish");
  });
});

describe("backend address", () => {
  it("normalises and validates", () => {
    expect(normalizeUrl(" https://api.example.com/api/v1/ ")).toBe("https://api.example.com");
    expect(normalizeUrl("https://api.example.com/gainz")).toBe("https://api.example.com/gainz");
    expect(normalizeUrl("ftp://x")).toBeNull();
    expect(normalizeUrl("not a url")).toBeNull();
    expect(normalizeUrl("https://user:pass@api.example.com")).toBeNull();
  });

  it("flags unencrypted addresses", () => {
    expect(isEncrypted("https://a.b")).toBe(true);
    expect(isEncrypted("http://192.168.1.5:8000")).toBe(false);
    expect(isEncrypted(null)).toBe(false);
  });
});

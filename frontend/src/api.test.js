import { beforeEach, describe, expect, it, vi } from "vitest";

import { getInvoice } from "./api.js";

describe("401 handling", () => {
  let reload;

  beforeEach(() => {
    sessionStorage.clear();
    reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 401 })));
  });

  it("reloads once to re-run IAP sign-in, then stops instead of looping", async () => {
    await expect(getInvoice("a")).rejects.toThrow(/sign in/i);
    await expect(getInvoice("b")).rejects.toThrow(/sign in/i);
    await expect(getInvoice("c")).rejects.toThrow(/sign in/i);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("reloads again once the guard window has passed", async () => {
    sessionStorage.setItem("intakehub:auth-reload-at", String(Date.now() - 60_000));
    await expect(getInvoice("a")).rejects.toThrow();
    expect(reload).toHaveBeenCalledTimes(1);
  });
});

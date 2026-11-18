// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET, POST } from "@/app/api/[...path]/route";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
const context = (path: string) => ({ params: Promise.resolve({ path: path.split("/") }) });

describe("same-origin API proxy", () => {
  it("forwards to the runtime backend URL and preserves upstream errors", async () => {
    vi.stubEnv("API_BASE_URL", "http://api:8000");
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Unknown source" }), { status: 422 }));
    vi.stubGlobal("fetch", fetch);
    const response = await POST(new NextRequest("http://localhost/api/extract", { method: "POST", body: JSON.stringify({ url: "https://company.example/" }) }), context("extract"));
    expect(fetch).toHaveBeenCalledWith("http://api:8000/api/extract", expect.objectContaining({ method: "POST" }));
    expect(response.status).toBe(422);
    expect(await response.json()).toEqual({ detail: "Unknown source" });
    expect(response.headers.get("cache-control")).toContain("no-store");
  });
  it("rejects arbitrary paths and wrong methods before contacting a backend", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    expect((await GET(new NextRequest("http://localhost/api/admin"), context("admin"))).status).toBe(404);
    expect((await GET(new NextRequest("http://localhost/api/extract"), context("extract"))).status).toBe(405);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("limits streamed bodies even when Content-Length is absent", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const request = new NextRequest("http://localhost/api/extract", { method: "POST", body: "x".repeat(4097) });
    expect((await POST(request, context("extract"))).status).toBe(413);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("turns network failures into an actionable response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const response = await GET(new NextRequest("http://localhost/api/health"), context("health"));
    expect(response.status).toBe(502);
    expect((await response.json()).detail).toContain("Check the backend");
  });
  it("passes NDJSON chunks before completion with buffering disabled", async () => {
    let source!: ReadableStreamDefaultController<Uint8Array>;
    const upstream = new ReadableStream<Uint8Array>({ start(controller) { source = controller; } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(upstream)));
    const response = await POST(new NextRequest("http://localhost/api/extract-stream", {
      method: "POST", body: JSON.stringify({ url: "https://company.example/" }),
    }), context("extract-stream"));
    expect(response.headers.get("content-type")).toContain("application/x-ndjson");
    expect(response.headers.get("x-accel-buffering")).toBe("no");
    expect(response.headers.get("cache-control")).toContain("no-transform");
    const reader = response.body!.getReader();
    source.enqueue(new TextEncoder().encode('{"type":"progress"}\n'));
    expect(new TextDecoder().decode((await reader.read()).value)).toContain('"progress"');
    source.enqueue(new TextEncoder().encode('{"type":"complete"}\n'));
    source.close();
    expect(new TextDecoder().decode((await reader.read()).value)).toContain('"complete"');
    expect((await reader.read()).done).toBe(true);
  });
});

// @vitest-environment node
import { describe, expect, it } from "vitest";
import { EMPTY_PROFILE } from "@/lib/profile";
import { readExtractionStream } from "@/lib/stream";

const snapshot = { profile: { ...EMPTY_PROFILE, businessName: "Кафе Ёж" }, provenance: {}, warnings: [], missingFields: [], elapsedMs: 1, pagesFetched: 1, firstFoundMs: 1, lastFoundMs: 1, discoverySpanMs: 0 };
describe("NDJSON decoding", () => {
  it("handles split UTF-8 bytes, multiple lines in a chunk, CRLF and an unterminated final line", async () => {
    const events = [{ type: "progress", ...snapshot }, { type: "progress", ...snapshot }, { type: "complete", ...snapshot }];
    const bytes = new TextEncoder().encode(events.map(event => JSON.stringify(event)).join("\r\n"));
    const split = bytes.findIndex(byte => byte > 127) + 1;
    const stream = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(bytes.slice(0, split));
      controller.enqueue(bytes.slice(split, split + 1));
      controller.enqueue(bytes.slice(split + 1));
      controller.close();
    } });
    const received: unknown[] = [];
    await readExtractionStream(stream, event => received.push(event));
    expect(received).toEqual(events);
  });
  it("rejects malformed events without fabricating a completion", async () => {
    const stream = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode('{"type":"progress"}\n')); controller.close();
    } });
    await expect(readExtractionStream(stream, () => undefined)).rejects.toThrow("Incompatible response format");
  });
});

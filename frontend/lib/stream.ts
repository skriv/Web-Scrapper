import { isExtractionEvent, type ExtractionEvent } from "./profile";

/** A chunk can split either a JSON line or a UTF-8 character. */
export async function readExtractionStream(body: ReadableStream<Uint8Array>, onEvent: (event: ExtractionEvent) => void, signal?: AbortSignal) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let terminal = false;
  const abort = () => { void reader.cancel().catch(() => undefined); };
  signal?.addEventListener("abort", abort, { once: true });
  function parse(line: string) {
    if (!line.trim()) return;
    if (line.length > 1_000_000) throw new Error("The response is too large.");
    let event: unknown;
    try { event = JSON.parse(line); }
    catch { throw new Error("Could not read the response."); }
    if (!isExtractionEvent(event)) throw new Error("Incompatible response format.");
    onEvent(event);
    terminal = event.type === "complete" || event.type === "error";
  }
  try {
    while (!terminal) {
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      const { done, value } = await reader.read();
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      pending += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let end: number;
      while (!terminal && (end = pending.indexOf("\n")) !== -1) {
        parse(pending.slice(0, end));
        pending = pending.slice(end + 1);
      }
      if (terminal) break;
      if (pending.length > 1_000_000) throw new Error("The response is too large.");
      if (done) {
        if (pending.trim()) parse(pending);
        if (!terminal) throw new Error("Connection interrupted. Received data was kept; try again.");
        break;
      }
    }
  } finally {
    signal?.removeEventListener("abort", abort);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

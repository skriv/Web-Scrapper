import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const LIMIT = 4096;

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const endpoint = path.join("/");
  const methods = endpoint === 'scraping/settings' ? ['GET','PUT']
    : /^scraping\/providers(\/health)?$/.test(endpoint) || ['scraping/history','scraping/metrics'].includes(endpoint) ? ['GET']
    : /^scraping\/(extract|test|compare)$/.test(endpoint) ? ['POST']
    : /^scraping\/jobs\/[a-f0-9]{32}\/events$/.test(endpoint) ? ['GET']
    : /^scraping\/jobs\/[a-f0-9]{32}$/.test(endpoint) ? ['GET','DELETE']
    : endpoint === 'scraping/cache' ? ['DELETE']
    : ['extract','extract-stream'].includes(endpoint) ? ['POST']
    : ['demo','health'].includes(endpoint) ? ['GET'] : [];
  if (!methods.length) {
    return NextResponse.json({ detail: "Route not found." }, { status: 404 });
  }
  if (!methods.includes(request.method)) {
    return NextResponse.json({ detail: "Method not allowed." }, { status: 405 });
  }
  if (request.headers.get('origin') && request.headers.get('origin') !== request.nextUrl.origin && request.method !== 'GET') {
    return NextResponse.json({detail:'Cross-origin mutation denied.'},{status:403});
  }
  const isSSE = endpoint.endsWith('/events');
  try {
    let body: string | undefined;
    if (["POST","PUT"].includes(request.method)) {
      if (Number(request.headers.get("content-length")) > LIMIT) {
        return NextResponse.json({ detail: "Request is too large." }, { status: 413 });
      }
      const chunks: Uint8Array[] = [];
      let size = 0;
      const reader = request.body?.getReader();
      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > LIMIT) {
            await reader.cancel();
            return NextResponse.json({ detail: "Request is too large." }, { status: 413 });
          }
          chunks.push(value);
        }
      }
      body = Buffer.concat(chunks).toString("utf8");
    }
    const origin = (process.env.API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
    const after = request.nextUrl.searchParams.get('after');
    const query = isSSE && after && /^\d{1,8}$/.test(after) ? `?after=${after}` : '';
    const upstream = await fetch(`${origin}/api/${endpoint}${query}`, {
      method: request.method,
      headers: { "Content-Type": "application/json", Accept: isSSE ? "text/event-stream" : endpoint === "extract-stream" ? "application/x-ndjson" : "application/json", "Last-Event-ID": /^\d{1,8}$/.test(request.headers.get("last-event-id") || "") ? request.headers.get("last-event-id")! : "0" },
      body,
      cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(300_000)]),
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": isSSE && upstream.ok ? "text/event-stream; charset=utf-8" : endpoint === "extract-stream" && upstream.ok ? "application/x-ndjson; charset=utf-8" : "application/json; charset=utf-8",
        "Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no",
      },
    });
  } catch (error) {
    const timedOut = error instanceof Error && ["TimeoutError", "AbortError"].includes(error.name);
    return NextResponse.json({ detail: timedOut
      ? "Source timed out. Try again or use the business website."
      : "Extraction service is unavailable. Check the backend." }, { status: timedOut ? 504 : 502 });
  }
}
export const GET = proxy;
export const POST = proxy;

export const PUT = proxy;
export const DELETE = proxy;

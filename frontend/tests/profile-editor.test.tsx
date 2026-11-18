import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ProfileEditor from "@/components/profile-editor";
import {draft} from "@/lib/draft";
import { EMPTY_PROFILE, normalizeUrl, type ExtractionEvent } from "@/lib/profile";

const encoder = new TextEncoder();
function snapshot(type: "progress" | "complete", extra = {}) {
  return {
    type, profile: { ...EMPTY_PROFILE, businessName: "Клиника" },
    provenance: {}, warnings: [], missingFields: [], pagesFetched: 1,
    elapsedMs: 100, firstFoundMs: 100, lastFoundMs: 100, discoverySpanMs: 0, ...extra,
  } as ExtractionEvent;
}
function streamResponse() {
  let source!: ReadableStreamDefaultController<Uint8Array>;
  const cancel = vi.fn();
  const stream = new ReadableStream<Uint8Array>({ start(controller) { source = controller; }, cancel });
  return { response: new Response(stream), cancel,
    push: (event: unknown, newline = true) => source.enqueue(encoder.encode(JSON.stringify(event) + (newline ? "\n" : ""))),
    close: () => source.close(),
  };
}
function search(value = "clinic.example") {
  fireEvent.change(screen.getByLabelText("Website or Google Maps URL"), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Find" }));
}
afterEach(() => { draft.profile={...EMPTY_PROFILE}; draft.edited.clear(); draft.pending=null;draft.result=null; vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("minimal streaming editor", () => {
  it("shows only the input, editable profile and JSON with safe URL handling", () => {
    expect(normalizeUrl("company.com")).toBe("https://company.com/");
    expect(() => normalizeUrl("ftp://company.com")).toThrow();
    expect(() => normalizeUrl("https://user:pass@company.com")).toThrow();
    render(<ProfileEditor/>);
    expect(screen.getByRole("heading", { name: "Profile" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "JSON" })).toBeVisible();
    expect(screen.getAllByRole("button").map(button => button.textContent)).toEqual(["Find", "Copy", "Download"]);
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.getByTestId("first-found")).toHaveTextContent("—");
    expect(screen.getByTestId("total-time")).toHaveTextContent("—");
  });
  it("updates fields and JSON BEFORE completion and freezes discovery timing at the last finding", async () => {
    const stream = streamResponse();
    const fetch = vi.fn().mockResolvedValue(stream.response); vi.stubGlobal("fetch", fetch);
    render(<ProfileEditor/>); search();
    expect(screen.getByLabelText("Business name")).toBeEnabled();
    await act(async () => stream.push(snapshot("progress")));
    await waitFor(() => expect(screen.getByLabelText("Business name")).toHaveValue("Клиника"));
    expect(screen.getByLabelText("Profile JSON")).toHaveTextContent('"businessName": "Клиника"');
    expect(screen.getByRole("button", { name: "Searching…" })).toBeDisabled();
    expect(screen.getByTestId("discovery-span")).toHaveTextContent("0.000 s");
    await act(async () => stream.push(snapshot("progress", {
      profile: { ...EMPTY_PROFILE, businessName: "Клиника", phone: "+996555111222" },
      firstFoundMs: 100, lastFoundMs: 320, discoverySpanMs: 220,
    })));
    await waitFor(() => expect(screen.getByLabelText("Phone number")).toHaveValue("+996555111222"));
    const final = snapshot("complete", {
      profile: { ...EMPTY_PROFILE, businessName: "Клиника", phone: "+996555111222" },
      firstFoundMs: 100, lastFoundMs: 320, discoverySpanMs: 220, elapsedMs: 9876,
    });
    await act(async () => { stream.push(final, false); stream.close(); });
    await waitFor(() => expect(screen.getByLabelText("Business name")).toBeEnabled());
    expect(screen.getByTestId("first-found")).toHaveTextContent("0.100 s");
    expect(screen.getByTestId("last-found")).toHaveTextContent("0.320 s");
    expect(screen.getByTestId("total-time")).toHaveTextContent("9.876 s");
    expect(screen.getByTestId("discovery-span")).toHaveTextContent("0.220 s");
    expect(fetch).toHaveBeenCalledWith("/api/extract-stream", expect.objectContaining({ method: "POST", body: JSON.stringify({ url: "https://clinic.example/", followLinks: true }) }));
  });
  it("edits and exports the current profile while JSON stays visible", async () => {
    const copy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: copy }, configurable: true });
    render(<ProfileEditor/>);
    fireEvent.change(screen.getByLabelText("Business name"), { target: { value: "Моя клиника" } });
    fireEvent.change(screen.getByLabelText("Services"), { target: { value: "Первая\n\nВторая" } });
    expect(screen.getByLabelText("Profile JSON")).toHaveTextContent("Моя клиника");
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(copy).toHaveBeenCalled());
    expect(JSON.parse(copy.mock.calls[0][0]).services).toEqual(["Первая", "Вторая"]);
    const createObjectURL = vi.fn().mockReturnValue("blob:test");
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    expect(click).toHaveBeenCalled();
  });
  it("preserves the previous profile on failure before any partial data", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Website недоступен." }), { status: 502 })));
    render(<ProfileEditor/>);
    fireEvent.change(screen.getByLabelText("Business name"), { target: { value: "Сохранить меня" } });
    search("broken.example");
    expect(await screen.findByRole("alert")).toHaveTextContent("Website недоступен.");
    expect(screen.getByLabelText("Business name")).toHaveValue("Сохранить меня");
  });
  it("preserves manually edited fields through partial snapshots and stream errors", async () => {
    const stream = streamResponse(); vi.stubGlobal("fetch", vi.fn().mockResolvedValue(stream.response));
    render(<ProfileEditor/>);
    fireEvent.change(screen.getByLabelText("Phone number"), { target: { value: "old phone" } });
    search();
    expect(screen.getByLabelText("Phone number")).toHaveValue("old phone");
    await act(async () => stream.push(snapshot("progress")));
    await waitFor(() => expect(screen.getByLabelText("Phone number")).toHaveValue("old phone"));
    await act(async () => stream.push({ type: "error", detail: "Источник прервал соединение." }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Источник прервал соединение.");
    expect(screen.getByLabelText("Business name")).toHaveValue("Клиника");
    expect(screen.getByLabelText("Business name")).toBeEnabled();
  });
  it("reports an interrupted stream and keeps the last snapshot", async () => {
    const stream = streamResponse(); vi.stubGlobal("fetch", vi.fn().mockResolvedValue(stream.response));
    render(<ProfileEditor/>); search();
    await act(async () => { stream.push(snapshot("progress")); stream.close(); });
    expect(await screen.findByRole("alert")).toHaveTextContent("Connection interrupted");
    expect(screen.getByLabelText("Business name")).toHaveValue("Клиника");
  });
  it("aborts the request and cancels its reader on unmount", async () => {
    const stream = streamResponse();
    const fetch = vi.fn().mockResolvedValue(stream.response); vi.stubGlobal("fetch", fetch);
    const view = render(<ProfileEditor/>); search();
    await act(async () => stream.push(snapshot("progress")));
    view.unmount();
    await waitFor(() => expect(stream.cancel).toHaveBeenCalled());
    expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
  });
});

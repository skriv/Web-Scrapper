"use client";

import { useEffect, useRef, useState, useSyncExternalStore, type FormEvent } from "react";
import {
  EMPTY_PROFILE, greeting, profileForForm, FIELDS, FIELD_LABELS, exportProfile, filled, normalizeUrl,
  type ExtractionResult, type Field, type Profile,
} from "@/lib/profile";
import Link from "next/link";
import ProfileFields from "./profile-fields";
import DiscoveryTimeline from "./discovery-timeline";
import AIDebug from "./ai-debug";
import { draft, mergePreservingEdits, subscribeDraft, getDraftProfile, getServerProfile, setDraftProfile, applyDraft } from "@/lib/draft";
import { readExtractionStream } from "@/lib/stream";

const seconds = (ms: number | null | undefined) => ms == null ? "—" : `${(ms / 1000).toLocaleString("en-US", { minimumFractionDigits: 3, maximumFractionDigits: 3 })} s`;
const PLACEHOLDERS: Partial<Record<Field, string>> = { website: "https://company.com", timezone: "Asia/Bishkek" };

export default function ProfileEditor() {
  const profile = useSyncExternalStore(subscribeDraft,getDraftProfile,getServerProfile);
  const setProfile = setDraftProfile;
  const [url, setUrl] = useState("");
  const [localResult, setLocalResult] = useState<ExtractionResult | null>(null);
  const result=draft.result ?? localResult;
  const setResult=(value:ExtractionResult)=>{draft.result=value;setLocalResult(value);};
  const [busy, setBusy] = useState(false);
  const [hasProgress, setHasProgress] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const edited = useRef(draft.edited);
  const controller = useRef<AbortController | null>(null);
  const populated = FIELDS.some(key => filled(profile[key]));
  const locations = result?.locations ?? [];
  const json = JSON.stringify({ ...JSON.parse(exportProfile(profile)), ...(result?.industries?.length ? {industries:result.industries} : {}), ...(locations.length ? { locations } : {}), ...(result?.mapsCandidates?.length ? { mapsCandidates: result.mapsCandidates } : {}) }, null, 2);

  useEffect(() => {
    if (draft.pending) { applyDraft(draft.pending); draft.pending=null; setNotice('Test result applied. Manually edited fields preserved.'); }
    return () => { controller.current?.abort(); };
  }, []);
  useEffect(() => {
    if (!busy) return;
    const start = performance.now();
    const timer = setInterval(() => setElapsed(performance.now() - start), 100);
    return () => clearInterval(timer);
  }, [busy]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 3000);
    return () => clearTimeout(timer);
  }, [notice]);

  async function extract(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    let target: string;
    try { target = normalizeUrl(url); }
    catch { setError("Enter a website or Google Maps URL."); return; }
    setBusy(true); setHasProgress(false); setElapsed(0); setError(""); setNotice("");
    const active = new AbortController();
    controller.current = active;
    let receivedSnapshot = false;
    try {
      const response = await fetch("/api/extract-stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
        body: JSON.stringify({ url: target, followLinks: true }),
        signal: active.signal,
      });
      if (!response.ok) {
        const data: unknown = await response.json().catch(() => null);
        const detail = data && typeof data === "object" && "detail" in data ? data.detail : undefined;
        throw new Error(typeof detail === "string" ? detail : `Search failed (${response.status}).`);
      }
      if (!response.body) throw new Error("No data received. Try again.");
      await readExtractionStream(response.body, data => {
        if (active.signal.aborted) return;
        if (data.type === "error") { setError(data.detail); return; }
        // Each event is a full snapshot. Discovery time comes from the backend,
        // so network delivery and job completion never advance the last finding.
        receivedSnapshot = true;
        setHasProgress(true);
        setProfile(current=>mergePreservingEdits(current,profileForForm(data),edited.current));
        setResult(data);
      }, active.signal);
      if (receivedSnapshot) setUrl(target);
    } catch (cause) {
      if (!active.signal.aborted) setError(cause instanceof Error ? cause.message : "Could not connect to the service.");
    } finally {
      if (!active.signal.aborted) setBusy(false);
      if (controller.current === active) controller.current = null;
    }
  }
  function update(field: Field, value: string) {
    edited.current.add(field);
    if(field==='business_name')edited.current.add('businessName');
    if(field==='phone_number')edited.current.add('phone');
    setProfile(current => {
      const next={...current,[field]:Array.isArray(current[field])?value.split("\n"):value};
      if(field==='business_name'){
        next.businessName=value;
        if(!edited.current.has('voice_greeting_phrase'))next.voice_greeting_phrase=greeting(value);
      }
      if(field==='phone_number')next.phone=value;
      return next;
    });
  }
  async function copy() {
    try { await navigator.clipboard.writeText(json); setNotice("Copied"); }
    catch { setError("Could not copy JSON. Download the file instead."); }
  }
  function download() {
    const href = URL.createObjectURL(new Blob([json], { type: "application/json;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = href; anchor.download = "business-profile.json"; anchor.click();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  }
  const currentResult = busy && !hasProgress ? null : result;
  const warnings = currentResult?.warnings.filter(warning => warning.trim()) ?? [];

  return <main className="workspace">
    <nav className="page-nav"><span>SKRIV Scrapper</span><Link href="/settings/scraping">Scraping settings & tests</Link></nav>
    <form className="url-form" onSubmit={extract} noValidate aria-label="Business search">
      <label className="sr-only" htmlFor="source-url">Website or Google Maps URL</label>
      <input id="source-url" name="url" inputMode="url" autoComplete="url"
        placeholder="Website or Google Maps URL" value={url}
        onChange={event => setUrl(event.target.value)} disabled={busy}
        aria-describedby={error ? "search-error" : undefined}/>
      <button type="submit" disabled={busy || !url.trim()}>{busy ? "Searching…" : "Find"}</button>
    </form>
    <div className="timing" aria-label="Discovery timing">
      <span>First: <output data-testid="first-found">{seconds(currentResult?.firstFoundMs)}</output></span>
      <span>Last: <output data-testid="last-found">{seconds(currentResult?.lastFoundMs)}</output></span>
      <span>First to last: <output data-testid="discovery-span">{seconds(currentResult?.discoverySpanMs)}</output></span>
      <span className={busy ? "running" : undefined}>Total: <output data-testid="total-time" aria-live={busy ? "off" : "polite"}>{seconds(busy ? elapsed : currentResult?.elapsedMs)}</output></span>
    </div>
    {result?.mapsStatus ? <p className="timing">Google Maps: {result.mapsStatus.replaceAll("_", " ")}</p> : null}
    {result?.ai ? <p className="timing" role="status">AI: {result.ai.status} · {result.ai.model}{result.ai.detail ? ` · ${result.ai.detail}` : ""}</p> : null}
    <AIDebug ai={currentResult?.ai}/>
    {error ? <p id="search-error" className="error" role="alert">{error}</p> : null}
    {result?.fieldTimeline?.length?<details className="diagnostics"><summary>Discovery report</summary><DiscoveryTimeline rows={result.fieldTimeline} note={result.fieldTimelineNote} profile={result.profile} ai={result.ai}/></details>:null}
    <div className="editor-layout">
      <section aria-labelledby="profile-title">
        <h1 id="profile-title">Profile</h1>
        {(result?.locationCount || locations.length) ? <p className="location-count">Locations: {result?.locationCount || locations.length}</p> : null}
        {result?.provenance?.services?.some(source=>source.method==="ollama-inferred")?<p className="subtle">Services: inferred by LLM from the business description. Review before use.</p>:null}
        <ProfileFields profile={profile} onChange={update} timezoneHint={result?.timezoneResolution?.detail}/>
        {edited.current.size ? <button className="text-button" type="button" onClick={()=>{edited.current.clear(); draft.edited.clear(); setNotice("Edited fields unlocked for future results.");}}>Unlock edited fields</button> : null}
        {result?.mapsCandidates?.length ? <section className="locations"><h2>Google Maps matches</h2>{result.mapsCandidates.map((candidate,index)=><div className="location" key={index}><button type="button" disabled={busy} onClick={()=>setProfile(current=>mergePreservingEdits(current,profileForForm({profile:candidate}),edited.current))}>{candidate.businessName}</button><p>{candidate.address}</p><p>{candidate.phone}</p></div>)}</section> : null}
        {locations.length > 0 ? <section className="locations"><h2>Locations ({locations.length})</h2>{locations.map((location, index) => <div className="location" key={`${location.address}-${index}`}><button type="button" disabled={busy} onClick={() => setProfile(current=>mergePreservingEdits(current,profileForForm({profile:location}),edited.current))}>{location.businessName}</button><p>{location.address}</p><p>{location.phone}</p><p>{location.openingHours.join(" · ")}</p></div>)}</section> : null}
      </section>
      <section className="json-section" aria-labelledby="json-title">
        <div className="json-heading"><h2 id="json-title">JSON</h2><div className="json-actions">
          <button type="button" onClick={() => void copy()}>Copy</button>
          <button type="button" onClick={download} disabled={!populated}>Download</button>
        </div></div>
        <pre tabIndex={0} aria-label="Profile JSON">{json}</pre>
        {notice ? <p className="notice" role="status">{notice}</p> : null}
        {warnings.length ? <details className="warnings"><summary>Warnings</summary><ul>{warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details> : null}
      </section>
    </div>
  </main>;
}

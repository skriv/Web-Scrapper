export type AIState={requestedFields?:string[];inferredFields?:string[];reason?:string;status:string;model:string;trigger?:string;startedMs?:number;durationMs?:number;detail?:string;fieldsAdded?:string[];scrapingComplete?:boolean;acceptedFields?:number;debug?:{temporary:boolean;stage:string;message:string;phaseStartedMs:number;phaseElapsedMs:number;inputCharacters?:number;sourceCount?:number;responseCharacters?:number;modelStats?:{loadMs?:number;promptMs?:number;generationMs?:number;totalModelMs?:number;inputTokens?:number;outputTokens?:number};history:{stage:string;elapsedMs:number;message:string}[]}};
export type FieldDiscovery = {sequence:number;field:string;value:string|string[];elapsedMs:number;source:'web'|'maps'|'llm'|'generated'|'derived'|'cache'|'unknown';sourceUrl:string;method:string;event:'found'|'updated'};
export type Profile = {
  source: string;
  business_name: string;
  voice_greeting_phrase: string;
  full_name: string;
  phone_number: string;
  email: string;
  businessName: string;
  description: string;
  address: string;
  phone: string;
  website: string;
  timezone: string;
  openingHours: string[];
  services: string[];
  paymentMethods: string[];
};
export type Field = keyof Profile;
export type Source = { url: string; method: string };
export type Location = Profile & { sourceUrl: string };
export type ExtractionResult = {
  industries?:string[];
  navigationOfferings?:{kind:string;label:string;service:string;url:string;sourceUrl:string;section:string;position:string;method:string}[];
  fieldTimeline?:FieldDiscovery[];
  fieldTimelineNote?:string;
  ai?:AIState;
  timezoneResolution?: {value:string;method:string;detail:string;evidenceStatus:string};
  locationCount?: number;
  locations?: Location[];
  mapsCandidates?: Location[];
  mapsStatus?: string;
  profile: Profile;
  provenance: Partial<Record<Field, Source[]>>;
  warnings: string[];
  missingFields: string[];
  elapsedMs: number;
  pagesFetched: number;
  firstFoundMs: number | null;
  lastFoundMs: number | null;
  discoverySpanMs: number | null;
};
export type ExtractionEvent = (ExtractionResult & { type: "progress" | "complete" }) | { type: "error"; detail: string };
export const EMPTY_PROFILE: Profile = {
  source: "", business_name: "", voice_greeting_phrase: "", full_name: "", phone_number: "", email: "",
  businessName: "", description: "", address: "", phone: "", website: "", timezone: "",
  openingHours: [], services: [], paymentMethods: [],
};
export const FIELD_LABELS: Record<Field, string> = {
  source: "Source URL", business_name: "Business name", voice_greeting_phrase: "Voice greeting phrase", full_name: "Contact full name", phone_number: "Phone number", email: "Email",
  businessName: "Business name", description: "Description", address: "Address",
  phone: "Phone", website: "Website", timezone: "Time zone", openingHours: "Opening hours",
  services: "Services", paymentMethods: "Payment methods",
};
export const FIELDS = Object.keys(FIELD_LABELS) as Field[];
export const DISPLAY_FIELDS = FIELDS.filter(field=>field!=="businessName"&&field!=="phone");
export function greeting(name:string){return name.trim()?`Hello, this is ${name.trim()}. How can I help you today?`:"";}

export function filled(value: string | string[]) {
  return Array.isArray(value) ? value.some(item => item.trim()) : Boolean(value.trim());
}
export function normalizeUrl(raw: string): string {
  const url = new URL(raw.includes("://") ? raw.trim() : `https://${raw.trim()}`);
  if (!["https:", "http:"].includes(url.protocol) || !url.hostname || url.username || url.password) {
    throw new Error("Enter a website or Google Maps URL: https://example.com");
  }
  return url.toString();
}
export function safeSourceUrl(raw: string): string | undefined {
  try {
    const url = new URL(raw);
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch { return undefined; }
}
export function isResult(value: unknown): value is ExtractionResult {
  if (!value || typeof value !== "object") return false;
  const data = value as ExtractionResult;
  if (!data.profile || !data.provenance || !Array.isArray(data.warnings) || !data.warnings.every(item => typeof item === "string")) return false;
  return FIELDS.every(field => {
    const value = data.profile[field];
    return ["openingHours", "services", "paymentMethods"].includes(field)
      ? Array.isArray(value) && value.every(item => typeof item === "string")
      : typeof value === "string";
  });
}
export function exportProfile(profile: Profile) {
  return JSON.stringify({
    ...profile,
    openingHours: profile.openingHours.map(value => value.trim()).filter(Boolean),
    services: profile.services.map(value => value.trim()).filter(Boolean),
    paymentMethods: profile.paymentMethods.map(value => value.trim()).filter(Boolean),
  }, null, 2);
}
export function isExtractionEvent(value: unknown): value is ExtractionEvent {
  if (!value || typeof value !== "object" || !("type" in value)) return false;
  if (value.type === "error") return "detail" in value && typeof value.detail === "string";
  if (!["progress", "complete"].includes(String(value.type)) || !isResult(value)) return false;
  return [value.firstFoundMs, value.lastFoundMs, value.discoverySpanMs].every(time => time === null || (typeof time === "number" && Number.isFinite(time) && time >= 0));
}

// Both response representations are valid sources for the same editable form.
export function profileForForm(result: {profile?:Profile;businessProfile?:unknown}):Profile {
 const normalized=result.businessProfile && typeof result.businessProfile==='object' ? result.businessProfile as Record<string,unknown> : {};
 const aliases:Record<Field,string>={source:'source',business_name:'business_name',voice_greeting_phrase:'voice_greeting_phrase',full_name:'full_name',phone_number:'phone_number',email:'email',businessName:'businessName',description:'businessDescription',address:'address',phone:'businessPhone',website:'website',timezone:'timeZone',openingHours:'openingHours',services:'services',paymentMethods:'paymentMethods'};
 const output={...EMPTY_PROFILE,...result.profile};
 for(const field of FIELDS){
  if(filled(output[field]))continue;
  const value=normalized[aliases[field]];
  if(Array.isArray(EMPTY_PROFILE[field])&&Array.isArray(value)) (output[field] as string[])=value.map(v=>typeof v==='string'?v:field==='services'&&v&&typeof v.name==='string'?v.name:'').filter(Boolean);
  else if(typeof value==='string') (output[field] as string)=value;
 }
 const profileAliases=(result.profile||{}) as unknown as Record<string,unknown>;
 const zoneValues=[output.timezone,profileAliases.timeZone,profileAliases.time_zone,normalized.timeZone,normalized.timezone,normalized.time_zone];
 output.timezone='';
 for(const zone of zoneValues){
  if(typeof zone!=='string'||!zone.trim())continue;
  try{new Intl.DateTimeFormat('en-US',{timeZone:zone.trim()}).format(0);output.timezone=zone.trim();break;}catch{}
 }
 output.business_name=output.business_name||output.businessName;
 output.phone_number=output.phone_number||output.phone;
 output.voice_greeting_phrase=output.voice_greeting_phrase||greeting(output.business_name);
 return output;
}

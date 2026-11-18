import {FIELD_LABELS,safeSourceUrl,type Field,type FieldDiscovery,type Profile} from '@/lib/profile';
const ORIGINS={web:'Web',maps:'Maps',llm:'LLM',generated:'Generated',derived:'Derived',cache:'Cache',unknown:'Unknown'};
export default function DiscoveryTimeline({rows,note,profile,ai}:{rows?:FieldDiscovery[];note?:string;profile?:Profile;ai?:{status:string;fieldsAdded?:string[]}}){
 if(!rows?.length)return <p className="subtle">No field discoveries recorded yet.</p>;
 return <><div className="table-scroll"><table><caption>Field discovery timeline</caption><thead><tr><th>#</th><th>Field</th><th>Time</th><th>Origin</th><th>Value</th><th>Source</th></tr></thead><tbody>{rows.map(row=>{
 const url=safeSourceUrl(row.sourceUrl);
 const text=Array.isArray(row.value)?row.value.join("; "):row.value;
 return <tr key={row.sequence}><td>{row.sequence}</td><th>{FIELD_LABELS[row.field as Field]||row.field}{row.event==='updated'?<small> · updated</small>:null}</th><td>{(row.elapsedMs/1000).toFixed(3)} s</td><td>{row.method==="ollama-inferred"?"LLM inferred":ORIGINS[row.source]||row.source}</td><td className="timeline-value" title={text}>{text.length>160?text.slice(0,160)+"…":text}</td><td>{url?<a href={url} target="_blank" rel="noreferrer">{row.source==='maps'?'Google Maps':new URL(url).hostname}</a>:row.source==='generated'?'Company-name template':'—'}<small className="timeline-method">{row.method}</small></td></tr>;
 })}</tbody></table></div>{ai?<p className="subtle">LLM: {ai.status} · {ai.fieldsAdded?.length??0} fields added.</p>:null}{profile?<p className="subtle">Not found: {(["source","business_name","voice_greeting_phrase","full_name","phone_number","email"] as Field[]).filter(key=>!profile[key]).map(key=>FIELD_LABELS[key]).join(", ")||"None of the six contact fields"}.</p>:null}<p className="subtle">{note||'Time from request start. Fields in the same snapshot share a timestamp. Updated values retain earlier discoveries.'}</p></>;
}

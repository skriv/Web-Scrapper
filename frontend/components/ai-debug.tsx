import type {AIState} from '@/lib/profile';
const seconds=(value?:number)=>value==null?'N/A':`${(value/1000).toFixed(3)} s`;
export default function AIDebug({ai}:{ai?:AIState}){
 const debug=ai?.debug;if(!ai||!debug)return null;
 const stats=debug.modelStats;
 return <section className="ai-debug" aria-label="LLM debug (temporary)">
  <p><strong>LLM debug (temporary)</strong> · {debug.message} {ai.status==='running'?<span>Current stage: {seconds(debug.phaseElapsedMs)}</span>:null}</p>
  <p className="subtle">Scraping: {ai.scrapingComplete?'finished':'in progress'} · Model: {ai.model} · Accepted fields: {ai.acceptedFields??(['waiting','waiting-for-data','running'].includes(ai.status)?'pending':'N/A')} · Added fields: {ai.fieldsAdded?.length??0}</p>
  {ai.requestedFields?<p className="subtle">Requested fields: {ai.requestedFields.join(", ")||"None"}{ai.inferredFields?.includes("services")?" · Services: inference from business description":""}</p>:null}
  {stats?<p className="subtle">Ollama timings: load {seconds(stats.loadMs)} · input processing {seconds(stats.promptMs)} · generation {seconds(stats.generationMs)} · output tokens {stats.outputTokens??'N/A'}</p>:null}
  <details><summary>LLM stages ({debug.history.length})</summary><ol>{debug.history.map((item,index)=><li key={index}><time>{seconds(item.elapsedMs)}</time> — {item.message}</li>)}</ol><p className="subtle">Stage times are observed by the application. Ollama reports load, input-processing and generation durations only after its non-streaming response finishes.</p></details>
 </section>;
}

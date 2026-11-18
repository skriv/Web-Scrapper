import { EMPTY_PROFILE, FIELDS, greeting, type Field, type Profile, type ExtractionResult } from "./profile";
// Observable tab-memory store survives Next.js route activity restoration.
export const draft:{profile:Profile;edited:Set<Field>;pending:Profile|null;result:ExtractionResult|null}={profile:{...EMPTY_PROFILE},edited:new Set(),pending:null,result:null};
const listeners=new Set<()=>void>();
export const subscribeDraft=(listener:()=>void)=>{listeners.add(listener);return()=>{listeners.delete(listener);};};
export const getDraftProfile=()=>draft.profile;
export const getServerProfile=()=>EMPTY_PROFILE;
export function setDraftProfile(update:Profile|((current:Profile)=>Profile)){
 draft.profile=typeof update==='function'?update(draft.profile):update;
 listeners.forEach(listener=>listener());
}
export function mergePreservingEdits(current:Profile,incoming:Profile,edited:ReadonlySet<Field>):Profile {
 const result=Object.fromEntries(FIELDS.map(key=>[key,edited.has(key)?current[key]:incoming[key]])) as Profile;
 for(const [alias,legacy] of [['business_name','businessName'],['phone_number','phone']] as const){
  const value=edited.has(alias)?current[alias]:edited.has(legacy)?current[legacy]:(incoming[alias]||incoming[legacy]);
  result[alias]=value;result[legacy]=value;
 }
 if(!edited.has('voice_greeting_phrase'))result.voice_greeting_phrase=greeting(result.business_name);
 return result;
}
export function applyDraft(profile:Profile,result:ExtractionResult|null=null){draft.result=result;setDraftProfile(current=>mergePreservingEdits(current,profile,draft.edited));}

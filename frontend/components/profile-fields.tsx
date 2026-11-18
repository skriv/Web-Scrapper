"use client";
import { DISPLAY_FIELDS, FIELD_LABELS, type Field, type Profile } from "@/lib/profile";
export default function ProfileFields({profile,onChange,disabled=false,prefix="",readOnly=false,timezoneHint}:{profile:Profile;onChange?:(field:Field,value:string)=>void;disabled?:boolean;prefix?:string;readOnly?:boolean;timezoneHint?:string}) {
  return <div className="profile-fields">{DISPLAY_FIELDS.map(key=>{
    const value=profile[key], id=prefix+key;
    return <div key={key} className={`field field-${key}`}><label htmlFor={id}>{FIELD_LABELS[key]}</label>
      {key==="description" || key==="voice_greeting_phrase" || Array.isArray(value) ? <textarea id={id} value={Array.isArray(value)?value.join("\n"):value} rows={key==="openingHours"?3:2} disabled={disabled} readOnly={readOnly} onChange={e=>onChange?.(key,e.target.value)}/> : <input id={id} value={typeof value==="string"?value:""} type={key==="phone_number"?"tel":key==="email"?"email":"text"} disabled={disabled} readOnly={readOnly} onChange={e=>onChange?.(key,e.target.value)}/>}
      {key==="full_name"?<small className="subtle">Published contact person, if explicitly identified. Otherwise enter manually.</small>:null}
      {key==="timezone"&&timezoneHint?<small className="subtle">{timezoneHint}</small>:null}
    </div>;
  })}</div>;
}

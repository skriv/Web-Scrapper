import {fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import ProfileEditor from '@/components/profile-editor';
import {draft,mergePreservingEdits} from '@/lib/draft';
import {EMPTY_PROFILE,exportProfile} from '@/lib/profile';
afterEach(()=>{draft.profile={...EMPTY_PROFILE};draft.edited.clear();draft.result=null;draft.pending=null;});
it('exports six requested fields, generates greeting on rename, and preserves a custom greeting',()=>{
 render(<ProfileEditor/>);
 fireEvent.change(screen.getByLabelText('Business name'),{target:{value:'Bevri'}});
 expect(screen.getByLabelText('Voice greeting phrase')).toHaveValue('Hello, this is Bevri. How can I help you today?');
 fireEvent.change(screen.getByLabelText('Email'),{target:{value:'office@example.com'}});
 const json=JSON.parse(exportProfile(draft.profile));
 expect(json.business_name).toBe('Bevri');expect(json.email).toBe('office@example.com');
 expect(Object.keys(json)).toEqual(expect.arrayContaining(['source','business_name','voice_greeting_phrase','full_name','phone_number','email']));
 expect(json).not.toHaveProperty('external_logo_id');expect(json).not.toHaveProperty('consent');
 fireEvent.change(screen.getByLabelText('Voice greeting phrase'),{target:{value:'Welcome!'}});
 fireEvent.change(screen.getByLabelText('Business name'),{target:{value:'New Name'}});
 expect(screen.getByLabelText('Voice greeting phrase')).toHaveValue('Welcome!');
});
it('keeps an edited business name, phone, and greeting coherent across incoming snapshots',()=>{
 const current={...EMPTY_PROFILE,business_name:'My Clinic',businessName:'My Clinic',phone_number:'123',phone:'123'};
 const incoming={...EMPTY_PROFILE,business_name:'Other',businessName:'Other',phone_number:'456',phone:'456',email:'office@example.com'};
 const result=mergePreservingEdits(current,incoming,new Set(['business_name','phone_number']));
 expect(result.businessName).toBe('My Clinic');expect(result.phone).toBe('123');
 expect(result.voice_greeting_phrase).toBe('Hello, this is My Clinic. How can I help you today?');
 expect(result.email).toBe('office@example.com');
});

import {StrictMode} from 'react';
import {act,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import ScrapingPanel from '@/components/scraping-panel';
import ProfileEditor from '@/components/profile-editor';
import {draft,mergePreservingEdits,applyDraft} from '@/lib/draft';
import {EMPTY_PROFILE,profileForForm} from '@/lib/profile';
const push=vi.fn();vi.mock('next/navigation',()=>({useRouter:()=>({push})}));
class Source extends EventTarget{
 static current:Source;static CLOSED=2;readyState=1;
 constructor(public url:string){super();Source.current=this;}
 close(){this.readyState=2;}
 send(type:string,data:unknown){this.dispatchEvent(new MessageEvent(type,{data:JSON.stringify(data)}));}
}
const settings={maxPages:10,enabledProviders:['scrapling'],renderJs:false,ollamaEnabled:false};
function setup(){
 vi.stubGlobal('EventSource',Source);
 const fetch=vi.fn().mockImplementation(async(path:string,init?:RequestInit)=>{
  const body=path.endsWith('/settings')?(init?.method==='PUT'?JSON.parse(String(init.body)):settings):path.endsWith('/providers/health')?{providers:[{id:'scrapling',name:'Scrapling',capabilities:['website','http'],status:'ready',detail:'Installed'}],infrastructure:{database:'sqlite',redis:'not configured'}}:path.endsWith('/metrics')?{recent:[]}:{id:'abcdef',eventsUrl:'/api/scraping/jobs/abcdef/events'};
  return new Response(JSON.stringify(body));
 });vi.stubGlobal('fetch',fetch);return fetch;
}
afterEach(()=>{draft.profile={...EMPTY_PROFILE};draft.edited.clear();draft.pending=null;draft.result=null;vi.unstubAllGlobals();vi.clearAllMocks();});
describe('v2 settings and application',()=>{
 it('persists edited settings to the backend rather than browser storage',async()=>{
  const fetch=setup();render(<ScrapingPanel/>);await waitFor(()=>expect(screen.queryByText('Loading server settings…')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button',{name:'Configuration'}));
  fireEvent.change(screen.getByLabelText('Maximum HTML pages'),{target:{value:'6'}});
  fireEvent.click(screen.getByRole('button',{name:'Save settings *'}));
  await screen.findByText('Settings saved on the server.');
  expect(fetch).toHaveBeenCalledWith('/api/scraping/settings',expect.objectContaining({method:'PUT',body:expect.stringContaining('"maxPages":6')}));
 });
 it('populates form from SSE and applies the result to the business form',async()=>{
  const fetch=setup();render(<ScrapingPanel/>);await waitFor(()=>expect(screen.queryByText('Loading server settings…')).not.toBeInTheDocument());
  fireEvent.change(screen.getByLabelText('Website, Google Maps URL, place ID, or company query'),{target:{value:'https://example.com'}});
  fireEvent.click(screen.getByRole('button',{name:'Run test'}));await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/scraping/test',expect.anything()));
  const result={profile:{...EMPTY_PROFILE,businessName:'Fixture Clinic'},metrics:{firstFieldMs:12,totalMs:20,fieldsExtracted:1,completeness:1/9,success:true}};
  await act(async()=>Source.current.send('progress',result));
  expect(screen.getByLabelText('Business name')).toHaveValue('Fixture Clinic');
  expect(screen.getByRole('button',{name:'Apply to profile'})).toBeDisabled();
  await act(async()=>Source.current.send('complete',result));
  fireEvent.click(screen.getByRole('button',{name:'Apply to profile'}));
  expect(draft.profile.businessName).toBe('Fixture Clinic');expect(push).toHaveBeenCalledWith('/');
 });
 it('merges test results while retaining a manually edited field across navigation',()=>{
  draft.profile={...EMPTY_PROFILE,businessName:'My custom name'};draft.edited.add('businessName');draft.pending={...EMPTY_PROFILE,businessName:'Scraped name',phone:'+16506000433'};
  render(<StrictMode><ProfileEditor/></StrictMode>);
  expect(screen.getByLabelText('Business name')).toHaveValue('My custom name');expect(screen.getByLabelText('Phone number')).toHaveValue('+16506000433');
 });
 it('preserves deliberately cleared fields during enrichment',()=>{
  expect(mergePreservingEdits({...EMPTY_PROFILE,phone:''},{...EMPTY_PROFILE,phone:'external'},new Set(['phone'])).phone).toBe('');
 });
 it('shows unavailable provider failures and lets a running job be cancelled',async()=>{
  const fetch=setup();render(<ScrapingPanel/>);await waitFor(()=>expect(screen.queryByText('Loading server settings…')).not.toBeInTheDocument());
  fireEvent.change(screen.getByLabelText('Website, Google Maps URL, place ID, or company query'),{target:{value:'https://example.com'}});fireEvent.click(screen.getByRole('button',{name:'Run test'}));
  await screen.findByRole('button',{name:'Cancel'});fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
  await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/scraping/jobs/abcdef',expect.objectContaining({method:'DELETE'})));
 });
});

it('shows normalized JSON values in form and retains location metadata when applying',()=>{
 const result={profile:{...EMPTY_PROFILE},businessProfile:{businessName:'Clinic',businessDescription:'Published description',businessPhone:'+14155550100',services:[{name:'Consultation'}]},locations:[{...EMPTY_PROFILE,businessName:'Branch A',address:'1 First St',sourceUrl:'https://example.com/a'},{...EMPTY_PROFILE,businessName:'Branch B',address:'2 Second St',sourceUrl:'https://example.com/b'}],locationCount:2,provenance:{},warnings:[],missingFields:[],pagesFetched:1,elapsedMs:10,firstFoundMs:1,lastFoundMs:10,discoverySpanMs:9};
 const profile=profileForForm(result);
 applyDraft(profile,result);
 render(<ProfileEditor/>);
 expect(screen.getByLabelText('Business name')).toHaveValue('Clinic');
 expect(screen.getByLabelText('Services')).toHaveValue('Consultation');
 expect(screen.getByText('Locations: 2')).toBeVisible();
 expect(screen.getByRole('heading',{name:'Locations (2)'})).toBeVisible();
});

it('maps timezone aliases from JSON to the Time zone input',()=>{
 const profile=profileForForm({profile:{...EMPTY_PROFILE,timeZone:'America/Los_Angeles'} as typeof EMPTY_PROFILE,businessProfile:{timeZone:'invalid'}});
 expect(profile.timezone).toBe('America/Los_Angeles');
 draft.profile=profile;
 render(<ProfileEditor/>);
 expect(screen.getByLabelText('Time zone')).toHaveValue('America/Los_Angeles');
});

it('saves the timed AI schedule and custom delay',async()=>{
 const fetch=setup();render(<ScrapingPanel/>);await waitFor(()=>expect(screen.queryByText('Loading server settings…')).not.toBeInTheDocument());
 fireEvent.click(screen.getByRole('button',{name:'Configuration'}));
 fireEvent.change(screen.getByLabelText('AI start mode'),{target:{value:'after-delay'}});
 fireEvent.change(screen.getByLabelText('Start after (seconds)'),{target:{value:'3'}});
 fireEvent.click(screen.getByRole('button',{name:'Save settings *'}));
 await screen.findByText('Settings saved on the server.');
 const writes=fetch.mock.calls.filter(([path,init])=>String(path).endsWith('/settings')&&init?.method==='PUT');
 expect(JSON.parse(writes[0][1].body)).toMatchObject({ollamaSchedule:'after-delay',ollamaDelaySeconds:3});
});

it('starts the explicit hybrid mode and displays an AI outage without dropping form data',async()=>{
 const fetch=setup();render(<ScrapingPanel/>);await waitFor(()=>expect(screen.queryByText('Loading server settings…')).not.toBeInTheDocument());
 fireEvent.change(screen.getByLabelText('Mode'),{target:{value:'hybrid'}});
 fireEvent.change(screen.getByLabelText('Website, Google Maps URL, place ID, or company query'),{target:{value:'https://bevri.com'}});
 fireEvent.click(screen.getByRole('button',{name:'Run test'}));
 await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/scraping/test',expect.objectContaining({body:expect.stringContaining('"mode":"hybrid"')})));
 await act(async()=>Source.current.send('complete',{profile:{...EMPTY_PROFILE,businessName:'Bevri'},ai:{status:'unavailable',model:'qwen3:4b',detail:'Ollama is unavailable.'},metrics:{success:true,totalMs:10}}));
 expect(screen.getByLabelText('Business name')).toHaveValue('Bevri');
 expect(screen.getByText(/AI: unavailable/)).toBeVisible();
 expect(screen.getByRole('button',{name:'Apply to profile'})).toBeEnabled();
});

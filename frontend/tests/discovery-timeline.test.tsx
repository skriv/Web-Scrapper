import {render,screen} from '@testing-library/react';
import {expect,it} from 'vitest';
import DiscoveryTimeline from '@/components/discovery-timeline';
it('shows actual arrival times, Maps/Web/LLM origin, and separate template generation',()=>{
 render(<DiscoveryTimeline rows={[
  {sequence:1,field:'business_name',value:'Bevri',elapsedMs:700,source:'maps',sourceUrl:'https://maps.google.com/?cid=123',method:'sonagara',event:'found'},
  {sequence:2,field:'email',value:'eat@bevri.com',elapsedMs:2100,source:'web',sourceUrl:'https://bevri.com/contact',method:'html',event:'found'},
  {sequence:3,field:'description',value:'Published prose',elapsedMs:4000,source:'llm',sourceUrl:'https://bevri.com',method:'ollama',event:'found'},
  {sequence:4,field:'voice_greeting_phrase',value:'Hello',elapsedMs:700,source:'generated',sourceUrl:'',method:'generated-template',event:'found'},
 ]}/>);
 expect(screen.getByRole('table',{name:'Field discovery timeline'})).toBeVisible();
 expect(screen.getAllByText('0.700 s')).toHaveLength(2);
 for(const name of ['2.100 s','Maps','Web','LLM','Generated','eat@bevri.com'])expect(screen.getByText(name)).toBeVisible();
});

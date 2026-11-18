import {render,screen} from '@testing-library/react';
import {expect,it} from 'vitest';
import AIDebug from '@/components/ai-debug';
it('shows a live waiting stage and then reports measured model generation and zero additions',()=>{
 const debug={temporary:true,stage:'waiting-response',message:'Waiting for Ollama response.',phaseStartedMs:3000,phaseElapsedMs:8000,history:[{stage:'waiting-response',elapsedMs:3000,message:'Waiting for Ollama response.'}]};
 const {rerender}=render(<AIDebug ai={{status:'running',model:'qwen3:4b',scrapingComplete:true,debug}}/>);
 expect(screen.getByText(/Waiting for Ollama response/,{selector:'p'})).toBeVisible();
 expect(screen.getByText(/Current stage: 8.000 s/)).toBeVisible();
 expect(screen.getByText(/Scraping: finished/)).toBeVisible();
 rerender(<AIDebug ai={{status:'complete',model:'qwen3:4b',acceptedFields:1,fieldsAdded:[],debug:{...debug,stage:'complete',message:'Model processing finished.',modelStats:{loadMs:100,promptMs:1000,generationMs:9000,outputTokens:400}}}}/>);
 expect(screen.getByText(/generation 9.000 s/)).toBeVisible();
 expect(screen.getByText(/Accepted fields: 1 · Added fields: 0/)).toBeVisible();
});

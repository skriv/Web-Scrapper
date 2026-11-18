#!/usr/bin/env python3
"""Explicit real-source smoke test; writes only ignored project-local diagnostics."""
import argparse,asyncio,json,sys
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
async def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',required=True);parser.add_argument('--base',default='http://127.0.0.1:8002');parser.add_argument('--mode',choices=['automatic','hybrid','fast'],default='automatic');parser.add_argument('--output',type=Path,default=ROOT/'.runtime/live-check.json');args=parser.parse_args()
    async with httpx.AsyncClient(timeout=270,trust_env=False) as client:
        response=await client.post(args.base+'/api/scraping/test',json={'input':args.input,'mode':args.mode,'bypassCache':True});response.raise_for_status();job=response.json()['id']
        async with client.stream('GET',args.base+f'/api/scraping/jobs/{job}/events') as stream:
            stream.raise_for_status()
            async for _ in stream.aiter_lines():pass
        response=await client.get(args.base+f'/api/scraping/jobs/{job}');response.raise_for_status();outcome=response.json()
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(outcome,indent=2))
        result=outcome.get('result') or {}
        print(json.dumps({'jobId':job,'status':outcome['status'],'error':outcome.get('error'),'profile':result.get('profile'),'metrics':result.get('metrics'),'aiStatus':result.get('ai',{}).get('status'),'diagnostics':str(args.output)},indent=2))
        if outcome['status']!='complete' or not result.get('metrics',{}).get('success'):sys.exit(1)
if __name__=='__main__':asyncio.run(main())

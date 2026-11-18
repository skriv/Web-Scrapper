#!/usr/bin/env python3
"""Opt-in real same-input provider comparisons against a running local backend."""
import argparse,asyncio,json
from pathlib import Path
import httpx
async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs',nargs='+',help='Real URLs or company queries. No requests run until invoked.')
    parser.add_argument('--base',default='http://127.0.0.1:8002')
    parser.add_argument('--providers',nargs='+',default=['scrapling','crawl4ai'])
    parser.add_argument('--repeats',type=int,choices=range(1,6),default=3)
    parser.add_argument('--allow-browser',action='store_true')
    parser.add_argument('--output',type=Path,default=Path('benchmark-results.json'))
    args=parser.parse_args();results=[]
    async with httpx.AsyncClient(timeout=1300,trust_env=False) as client:
        for value in args.inputs:
            response=await client.post(args.base+'/api/scraping/compare',json={'input':value,'providers':args.providers,'repeats':args.repeats,'bypassCache':True,'renderJs':args.allow_browser})
            response.raise_for_status();job=response.json()['id']
            async with client.stream('GET',args.base+f'/api/scraping/jobs/{job}/events') as stream:
                async for line in stream.aiter_lines():
                    if line.startswith('event: '):print(value,line[7:],flush=True)
            outcome=(await client.get(args.base+f'/api/scraping/jobs/{job}')).json()
            results.append(outcome)
    args.output.write_text(json.dumps(results,indent=2));print(args.output)
if __name__=='__main__':asyncio.run(main())

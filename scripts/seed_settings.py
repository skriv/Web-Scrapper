"""Copy the exact reviewed native settings on first install only."""
import asyncio,json
from pathlib import Path
from app.configuration import ScrapingSettings
from app.storage import store
ROOT=Path(__file__).resolve().parents[1]
async def main():
    snapshot=ScrapingSettings.model_validate(json.loads((ROOT/'config/settings.json').read_text())).model_dump()
    existing=await store.get('settings','current')
    if existing is None:
        await store.put('settings','current',snapshot)
        print('Initialized all 32 settings from config/settings.json.')
    else:print('Existing settings preserved; no reset performed.')
if __name__=='__main__':asyncio.run(main())

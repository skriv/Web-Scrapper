"""Temporary observable AI diagnostics. No source text or model reasoning is exposed."""
from .configuration import trace_context

async def report_ai_progress(stage,message,**details):
    info=trace_context.get()
    callback=info.get('aiProgress') if info else None
    if callback:await callback(stage,message,**details)

import click
from app.schduler import scheduler
from fastapi import FastAPI
from . import api

app = FastAPI()
app.include_router(api.job_router)
app.include_router(api.spider_router)

def init_scheduler():
    scheduler.start()

@app.on_event("startup")
async def startup_event():
    init_scheduler()

@click.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8000)
def run(host: str, port: int):
    import uvicorn
    uvicorn.run(app, host=host, port=port)

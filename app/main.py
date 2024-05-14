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
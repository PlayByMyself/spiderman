from datetime import datetime
import logging
from typing import Annotated, List, Literal, Optional, Union
from fastapi import APIRouter
from pydantic import AfterValidator, BaseModel, Field, HttpUrl
from scrapy import spiderloader
from scrapy.utils import project
from scrapy.crawler import CrawlerProcess
from apscheduler.job import Job
from cron_validator import CronValidator
import multiprocessing
from .schduler import scheduler

job_router = APIRouter(prefix="/jobs")
spider_router = APIRouter(prefix="/spiders")

logger = logging.getLogger(__name__)


def run_spider(**kwargs):
    multiprocessing.set_start_method("spawn", force=True)
    p = multiprocessing.Process(target=run_spider_in_process, kwargs=kwargs)
    p.start()
    p.join()


def run_spider_in_process(name: str, **kwargs):
    settings = project.get_project_settings()
    spider_loader = spiderloader.SpiderLoader.from_settings(settings)
    spider_class = spider_loader.load(name)
    process = CrawlerProcess(settings)
    process.crawl(spider_class, kwargs=kwargs)
    process.start()


def spider_exists(name: Optional[str] = None):
    settings = project.get_project_settings()
    spider_loader = spiderloader.SpiderLoader.from_settings(settings)
    return name and name in spider_loader.list()


class DateTriggerIn(BaseModel):
    triger: Literal["date"]
    run_date: Union[datetime]


class IntervalTriggerIn(BaseModel):
    triger: Literal["interval"]
    weeks: float = Field(default=0)
    days: float = Field(default=0)
    hours: float = Field(default=0)
    minutes: float = Field(default=0)
    seconds: float = Field(default=0)
    microseconds: float = Field(default=0)
    start_time: Optional[Union[datetime]]
    end_time: Optional[Union[datetime]]


def check_cron_expression(expression: str):
    assert (
        CronValidator.parse(expression) is None
    ), f"Invalid cron expression {expression}"
    return expression


CronExpression = Annotated[str, AfterValidator(check_cron_expression)]


class CronTriggerIn(BaseModel):
    triger: Literal["cron"]
    expression: CronExpression


TriggerIn = Annotated[
    Union[DateTriggerIn, IntervalTriggerIn, CronTriggerIn],
    Field(discriminator="triger"),
]


def check_spider_exists(spiderName: str):
    if not spider_exists(spiderName):
        raise ValueError(f"Spider {spiderName} not found")
    return spiderName


SpiderName = Annotated[str, AfterValidator(check_spider_exists)]


class SpiderIn(BaseModel):
    name: SpiderName
    proxy: Optional[HttpUrl] = Field(default=None)


class JobOut(BaseModel):
    job_id: str
    trigger: TriggerIn
    spider: SpiderIn


def get_job_out(job: Job):
    return JobOut(job_id=job.id, trigger=job.kwargs, spider=job.kwargs)


@spider_router.post("/run")
def run(spider: SpiderIn):
    spider_kwargs = spider.model_dump()
    job = scheduler.add_job(
        run_spider,
        kwargs=spider_kwargs,
        next_run_time=datetime.now(),
    )
    return {"job_id": job.id}


@job_router.get("")
def get_jobs():
    jobs: List[Job] = scheduler.get_jobs()  # type: ignore
    return [
        {
            "job_id": job.id,
            "kwargs": job.kwargs,
            "trigger": repr(job.trigger),
            "next_run_time": job.next_run_time,
        }
        for job in jobs
    ]


@job_router.post("")
def add_job(spider: SpiderIn, trigger: TriggerIn):
    spider_kwargs = spider.model_dump()
    trigger_kwargs = trigger.model_dump(
        exclude={
            "triger",
        }
    )
    job = scheduler.add_job(
        run_spider, kwargs=spider_kwargs, trigger=trigger.triger, **trigger_kwargs
    )
    return {"job_id": job.id}


@spider_router.get("")
def get():
    settings = project.get_project_settings()
    spider_loader = spiderloader.SpiderLoader.from_settings(settings)
    return spider_loader.list()

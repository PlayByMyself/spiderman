import logging

from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler

jobstores = {
    'default': MemoryJobStore()
}

scheduler = BackgroundScheduler(jobstores=jobstores, timezone='Asia/Shanghai')

logging.basicConfig()
logging.getLogger('apscheduler').setLevel(logging.DEBUG)
from config.settings import *  # noqa: F403
from config.service_settings import service_settings


globals().update(service_settings("admin"))

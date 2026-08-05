#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
from bt_core.metabase import with_metaclass, MetaParams


class MetaLogger(MetaParams):

    def donew(cls, *args, **kwargs):
        _obj = super().donew(*args, **kwargs)

        # setup logger
        logger = logging.getLogger("metalogger")

        handler = logging.StreamHandler() if _obj.p.stream else logging.FileHandler(_obj.p.filename)
        handler.setLevel(_obj.p.level)
        formatter = logging.Formatter(_obj.p.format)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        _obj.logger = getattr(logger, _obj.p.level)
        return _obj, args, kwargs

    async def get_logger(self, message):
        return self.logger(message)
    

class Logger(with_metaclass(MetaLogger, logging.Logger)):

    params = (
        ('name', 'logger'),
        ('stream', False),
        ("level", 'info'),
        ("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"),
        ("datefmt", "%Y-%m-%d %H:%M:%S"),
    )


class BrokerLogger(with_metaclass(MetaLogger, logging.Logger)):
    
    params = (("name", "broker"),)


class EngineLogger(with_metaclass(MetaLogger, logging.Logger)):

    params = (("name", "engine"),)


class ServerLogger(with_metaclass(MetaLogger, logging.Logger)):

    params = (("name", "server"),)


class ConsoleLogger(with_metaclass(MetaLogger, logging.Logger)):
    # sys.stdout = io.StringIO() means redirect stdout to a string
    params = (
        ("name", "console"),
        ("stream", True),
        ("level", 'debug'),
    )


__all__ = ['BrokerLogger', 'EngineLogger', 'ServerLogger', 'ConsoleLogger']

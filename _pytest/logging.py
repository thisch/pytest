from __future__ import absolute_import, division, print_function

import datetime
import logging
from contextlib import closing, contextmanager
import sys
import warnings

import pytest
import py


DEFAULT_LOG_FORMAT = '%(filename)-25s %(lineno)4d %(levelname)-8s %(message)s'
DEFAULT_LOG_DATE_FORMAT = '%H:%M:%S'


def get_option_ini(config, name):
    ret = config.getoption(name)  # 'default' arg won't work as expected
    if ret is None:
        ret = config.getini(name)
    return ret


def pytest_addoption(parser):
    """Add options to control log capturing."""

    group = parser.getgroup('logging')

    def add_option_ini(option, dest, default=None, type=None, **kwargs):
        parser.addini(dest, default=default, type=type,
                      help='default value for ' + option)
        group.addoption(option, dest=dest, **kwargs)

    add_option_ini(
        '--no-print-logs',
        dest='log_print', action='store_const', const=False, default=True,
        type='bool',
        help='disable printing caught logs on failed tests.')
    add_option_ini(
        '--log-level',
        dest='log_level', default=None,
        help='logging level used by the logging module')
    add_option_ini(
        '--log-format',
        dest='log_format', default=DEFAULT_LOG_FORMAT,
        help='log format as used by the logging module.')
    add_option_ini(
        '--log-date-format',
        dest='log_date_format', default=DEFAULT_LOG_DATE_FORMAT,
        help='log date format as used by the logging module.')
    add_option_ini(
        '--log-cli-level',
        dest='log_cli_level', default=None,
        help='cli logging level.')
    add_option_ini(
        '--log-cli-format',
        dest='log_cli_format', default=None,
        help='log format as used by the logging module.')
    add_option_ini(
        '--log-cli-date-format',
        dest='log_cli_date_format', default=None,
        help='log date format as used by the logging module.')
    add_option_ini(
        '--log-file',
        dest='log_file', default=None,
        help='path to a file when logging will be written to.')
    add_option_ini(
        '--log-file-level',
        dest='log_file_level', default=None,
        help='log file logging level.')
    add_option_ini(
        '--log-file-format',
        dest='log_file_format', default=DEFAULT_LOG_FORMAT,
        help='log format as used by the logging module.')
    add_option_ini(
        '--log-file-date-format',
        dest='log_file_date_format', default=DEFAULT_LOG_DATE_FORMAT,
        help='log date format as used by the logging module.')


def get_logger_obj(logger=None):
    """Get a logger object that can be specified by its name, or passed as is.

    Defaults to the root logger.
    """
    if logger is None or isinstance(logger, py.builtin._basestring):
        logger = logging.getLogger(logger)
    return logger


@contextmanager
def logging_using_handler(handler, logger=None):
    """Context manager that safely registers a given handler."""
    logger = get_logger_obj(logger)

    if handler in logger.handlers:  # reentrancy
        # Adding the same handler twice would confuse logging system.
        # Just don't do that.
        yield
    else:
        logger.addHandler(handler)
        try:
            yield
        finally:
            logger.removeHandler(handler)


@contextmanager
def catching_logs(handler, formatter=None,
                  level=logging.NOTSET, logger=None):
    """Context manager that prepares the whole logging machinery properly."""
    logger = get_logger_obj(logger)

    if formatter is not None:
        handler.setFormatter(formatter)
    handler.setLevel(level)

    with logging_using_handler(handler, logger):
        orig_level = logger.level
        logger.setLevel(min(orig_level, level))
        try:
            yield handler
        finally:
            logger.setLevel(orig_level)


LOGCOLORS = {
    logging.CRITICAL: 'red',
    logging.ERROR:  'red',
    logging.WARNING: 'red',
    logging.WARN:  'purple',
    logging.INFO: 'green',
    logging.DEBUG: 'white',
    logging.NOTSET: 'red'
}


def _colorize(text, color):
    tw = py.io.TerminalWriter()
    tw.hasmarkup = True
    kw = {color: True}
    return tw.markup(text, **kw)


class PercentStyle:
    default_format = '%(message)s'
    asctime_format = '%(asctime)s'
    asctime_search = '%(asctime)'

    def __init__(self, fmt):
        self._fmt = fmt or self.default_format

    def usesTime(self):
        return self._fmt.find(self.asctime_search) >= 0

    def format(self, record):
        return self._fmt % record.__dict__


class ColoredFormatter(logging.Formatter):
    converter = datetime.datetime.fromtimestamp

    def __init__(self, *args, **kwargs):
        self.no_datetime = kwargs.pop('no_datetime', False)
        self.no_date = kwargs.pop('no_date', False)
        self.logger_name_fmt = kwargs.pop('logger_name_fmt',
                                          '%(name)-16s')
        super(ColoredFormatter, self).__init__(*args, **kwargs)

        self.separator = '| '
        prefix = ''
        if not self.no_datetime:
            if self.no_date:
                self.datefmt = '%H:%M:%S.%f'
            else:
                self.datefmt = '%d %b %Y %H:%M:%S.%f'
            prefix += '%(asctime)s '
        if prefix:
            prefix += '| '
        self._colored_levels = {
            k: _colorize('%8s' % logging.getLevelName(k), v)
            for k, v in LOGCOLORS.items()}
        self._fmt = (prefix +
                     '%(colored_levelname)8s ' +
                     self.separator +
                     self.logger_name_fmt +
                     self.separator +
                     '%(message)s')
        if sys.version_info[0] > 2:
            self._style = PercentStyle(self._fmt)
            self._fmt = self._style._fmt

        message = 'this is a test msg'
        lr = logging.LogRecord('name', logging.INFO,
                               'filename', 0,
                               message,
                               None, None)
        lr.colored_levelname = 'INFO'
        tmplogstr = logging.Formatter.format(self, lr)
        self.hlen = (
            tmplogstr.find(message) -
            len(self.separator))

    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        return ct.strftime(datefmt)[:-3]  # only show milliseconds

    def format(self, record):
        record.colored_levelname = self._colored_levels.get(
            record.levelno, 'unknown')
        logstr = logging.Formatter.format(self, record)
        logstr = logstr.replace(
            '\n',
            '\n' + ' ' * self.hlen + self.separator)
        return logstr


class LogCaptureHandler(logging.StreamHandler):
    """A logging handler that stores log records and the log text."""

    def __init__(self):
        """Creates a new log handler."""

        logging.StreamHandler.__init__(self, py.io.TextIO())
        self.records = []

    def emit(self, record):
        """Keep the log records in a list in addition to the log text."""

        self.records.append(record)
        logging.StreamHandler.emit(self, record)


class LogCaptureFixture(object):
    """Provides access and control of log capturing."""

    def __init__(self, item):
        """Creates a new funcarg."""
        self._item = item

    @property
    def handler(self):
        return self._item.catch_log_handler

    @property
    def text(self):
        """Returns the log text."""
        return self.handler.stream.getvalue()

    @property
    def records(self):
        """Returns the list of log records."""
        return self.handler.records

    @property
    def record_tuples(self):
        """Returns a list of a striped down version of log records intended
        for use in assertion comparison.

        The format of the tuple is:

            (logger_name, log_level, message)
        """
        return [(r.name, r.levelno, r.getMessage()) for r in self.records]

    def clear(self):
        """Reset the list of log records."""
        self.handler.records = []

    def set_level(self, level, logger=None):
        """Sets the level for capturing of logs.

        By default, the level is set on the handler used to capture
        logs. Specify a logger name to instead set the level of any
        logger.
        """

        obj = logger and logging.getLogger(logger) or self.handler
        obj.setLevel(level)

    @contextmanager
    def at_level(self, level, logger=None):
        """Context manager that sets the level for capturing of logs.

        By default, the level is set on the handler used to capture
        logs. Specify a logger name to instead set the level of any
        logger.
        """
        if logger is None:
            logger = self.handler
        else:
            logger = logging.getLogger(logger)

        orig_level = logger.level
        logger.setLevel(level)
        try:
            yield
        finally:
            logger.setLevel(orig_level)


@pytest.fixture
def caplog(request):
    """Access and control log capturing.

    Captured logs are available through the following methods::

    * caplog.text()          -> string containing formatted log output
    * caplog.records()       -> list of logging.LogRecord instances
    * caplog.record_tuples() -> list of (logger_name, level, message) tuples
    """
    return LogCaptureFixture(request.node)


def get_actual_log_level(config, setting_name):
    """Return the actual logging level."""
    log_level = get_option_ini(config, setting_name)
    if not log_level:
        return
    if isinstance(log_level, py.builtin.text):
        log_level = log_level.upper()
    try:
        return int(getattr(logging, log_level, log_level))
    except ValueError:
        # Python logging does not recognise this as a logging level
        raise pytest.UsageError(
            "'{0}' is not recognized as a logging level name for "
            "'{1}'. Please consider passing the "
            "logging level num instead.".format(
                log_level,
                setting_name))


def pytest_configure(config):
    config.pluginmanager.register(LoggingPlugin(config), 'loggingp')


class LoggingPlugin(object):
    """Attaches to the logging module and captures log messages for each test.
    """

    def __init__(self, config):
        """Creates a new plugin to capture log messages.

        The formatter can be safely shared across all handlers so
        create a single one for the entire test session here.
        """
        log_cli_level = get_actual_log_level(config, 'log_cli_level')
        if log_cli_level is None:
            # No specific CLI logging level was provided, let's check
            # log_level for a fallback
            log_cli_level = get_actual_log_level(config, 'log_level')
            if log_cli_level is None:
                # No log_level was provided, default to WARNING
                log_cli_level = logging.WARNING
        self.log_cli_level = log_cli_level

        self.print_logs = get_option_ini(config, 'log_print')
        self.formatter = logging.Formatter(
                get_option_ini(config, 'log_format'),
                get_option_ini(config, 'log_date_format'))

        log_cli_handler = logging.StreamHandler(sys.stderr)
        log_cli_format = get_option_ini(config, 'log_cli_format')
        if not log_cli_format:
            # No CLI specific format was provided, use log_format
            log_cli_format = get_option_ini(config, 'log_format')
        log_cli_date_format = get_option_ini(config, 'log_cli_date_format')
        if not log_cli_date_format:
            # No CLI specific date format was provided, use log_date_format
            log_cli_date_format = get_option_ini(config, 'log_date_format')
        # log_cli_formatter = logging.Formatter(
        #         log_cli_format,
        #         datefmt=log_cli_date_format)
        log_cli_formatter = ColoredFormatter(
            log_cli_format,
            datefmt=log_cli_date_format)
        self.log_cli_handler = log_cli_handler  # needed for a single unittest
        self.live_logs = catching_logs(log_cli_handler,
                                       formatter=log_cli_formatter,
                                       level=self.log_cli_level)

        log_file = get_option_ini(config, 'log_file')
        if log_file:
            log_file_level = get_actual_log_level(config, 'log_file_level')
            if log_file_level is None:
                # No log_level was provided, default to WARNING
                log_file_level = logging.WARNING
            self.log_file_level = log_file_level

            log_file_format = get_option_ini(config, 'log_file_format')
            if not log_file_format:
                # No log file specific format was provided, use log_format
                log_file_format = get_option_ini(config, 'log_format')
            log_file_date_format = get_option_ini(config, 'log_file_date_format')
            if not log_file_date_format:
                # No log file specific date format was provided, use log_date_format
                log_file_date_format = get_option_ini(config, 'log_date_format')
            self.log_file_handler = logging.FileHandler(
                log_file,
                # Each pytest runtests session will write to a clean logfile
                mode='w',
            )
            log_file_formatter = logging.Formatter(
                    log_file_format,
                    datefmt=log_file_date_format)
            self.log_file_handler.setFormatter(log_file_formatter)
        else:
            self.log_file_handler = None

    @contextmanager
    def _runtest_for(self, item, when):
        """Implements the internals of pytest_runtest_xxx() hook."""
        with catching_logs(LogCaptureHandler(),
                           formatter=self.formatter) as log_handler:
            item.catch_log_handler = log_handler
            try:
                yield  # run test
            finally:
                del item.catch_log_handler

            if self.print_logs:
                # Add a captured log section to the report.
                log = log_handler.stream.getvalue().strip()
                item.add_report_section(when, 'log', log)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_setup(self, item):
        with self._runtest_for(item, 'setup'):
            yield

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        with self._runtest_for(item, 'call'):
            yield

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_teardown(self, item):
        with self._runtest_for(item, 'teardown'):
            yield

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtestloop(self, session):
        """Runs all collected test items."""
        with self.live_logs:
            if self.log_file_handler is not None:
                with closing(self.log_file_handler):
                    with catching_logs(self.log_file_handler,
                                       level=self.log_file_level):
                        yield  # run all the tests
            else:
                yield  # run all the tests

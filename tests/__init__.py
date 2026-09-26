import logging

# Keep test output clean: the application's log lines aren't shown unless a
# test attaches its own handler.
logging.getLogger("auditor").addHandler(logging.NullHandler())
logging.getLogger("auditor").propagate = False

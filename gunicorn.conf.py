# The Gunicorn master and web workers are intentionally scheduler-free.
# Production background jobs run through scheduler_worker.py.

# Content Pack performs one bounded synchronous model request. Keep the worker
# deadline modestly above that operation's 35-second timeout.
timeout = 45

"""Hermes Line: a real phone line for Hermes.

Hermes phones a business for you (with your approval), navigates the phone
menu, waits on hold, then bridges you in or finishes the task itself, and
answers callbacks. See README.md for setup and disclosures.
"""


def register(ctx):
    from .line.engine.hermes_glue import register as _register

    _register(ctx)

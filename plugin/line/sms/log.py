"""The single logger every line.sms module uses (see masking.install_log_masking)."""
import logging

LOG = logging.getLogger("line.sms")
LOG.addHandler(logging.NullHandler())   # library etiquette: the host app decides where logs go

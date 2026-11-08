"""The single logger every smsline module uses (see masking.install_log_masking)."""
import logging

LOG = logging.getLogger("smsline")
LOG.addHandler(logging.NullHandler())   # library etiquette: the host app decides where logs go

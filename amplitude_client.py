"""Shared Amplitude client. Import `client` from here; never construct another."""

import os

from amplitude import Amplitude
from dotenv import load_dotenv

load_dotenv(".env.local")

_key = os.environ.get("AMPLITUDE_API_KEY")
if not _key:
    print("[amplitude] AMPLITUDE_API_KEY is not set — no events will be sent")
    client = None
else:
    client = Amplitude(_key)
    # server_zone defaults to "US"; set "EU" only for EU-residency projects.
    client.configuration.server_zone = "US"

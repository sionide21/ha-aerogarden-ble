"""Constants for the AeroGarden Bluetooth integration."""

from datetime import timedelta

DOMAIN = "aerogarden_ble"

LOCAL_NAME = "Stem GrowLight"

POLL_INTERVAL = timedelta(minutes=5)
RETRY_INTERVAL = timedelta(seconds=30)
# How often to check whether a poll is due.
POLL_CHECK_INTERVAL = timedelta(seconds=15)
# Give the light a moment to act on a schedule boundary before polling.
BOUNDARY_DELAY = timedelta(seconds=10)
# Seconds without an advertisement after which the light is taken to be busy
# with another connection. It advertises several times a second when free,
# and resumes about 3 s after a disconnect.
REACHABLE_TIMEOUT = 15.0

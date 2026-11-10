"""Exceptions raised by the Stem light client."""


class StemLightError(Exception):
    """Base class for Stem light errors."""


class StemLightConnectionError(StemLightError):
    """The light could not be reached or dropped the connection."""


class StemLightTimeoutError(StemLightError):
    """The light did not answer in time."""


class StemLightCommandError(StemLightError):
    """The light refused a command or sent something unexpected."""

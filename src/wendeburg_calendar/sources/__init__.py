# Importing submodules here triggers their @register(...) side effects, so
# `wendeburg_calendar.sources.registry.create(...)` can find them just by
# importing this package once.
from wendeburg_calendar.sources import peine_erleben, structured_html, wendeburg  # noqa: F401

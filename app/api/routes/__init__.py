"""Route registration.

We split the original monolithic `app/api/routes.py` into smaller modules.
This package exposes a single `router` and any helper functions that need to
be imported by `app.main`.
"""

from .router import router
from .cleanup import cleanup_all_unreferenced_images

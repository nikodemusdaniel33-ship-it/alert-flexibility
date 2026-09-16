from app.criteria.base import CriteriaProvider
from app.criteria.manual import ManualListProvider


def enabled_providers() -> list[CriteriaProvider]:
    """Providers that decide which projects get tracked.

    Add new sources here (exchange listings, tag/category feeds, other
    external lists) as they're implemented — the sync step and everything
    downstream (gaps, alerts, dashboard) is agnostic to where a project
    came from.
    """
    return [ManualListProvider()]

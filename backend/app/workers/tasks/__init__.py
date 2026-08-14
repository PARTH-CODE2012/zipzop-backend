"""Task modules, one per queue.

Real work lands here from M2 onward. The no-op task below exists so `make up`
can prove the whole chain — API → Redis → worker → result — before any feature
depends on it.
"""

from app.workers.tasks import billing, ingest

__all__ = ["billing", "ingest"]

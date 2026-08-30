"""The catalogues — contract §10.

**What a client is allowed to ask for, from the server that has to deliver it.**
The five colour looks were hardcoded in the frontend until M5, kept in step with
`color_analysis.LOOKS` by hand and by a test comparing two copies of the same
list. That works until it does not: a look added on the server and not in the
client is a recommendation the browser silently cannot draw, and one added on
the client is a grade the renderer has no file for.

Serving it makes the server the single source and the drift impossible.

Public, and deliberately: the catalogue is the same for everybody, contains
nothing about an account, and putting it behind auth would mean the pricing page
cannot show what the product does without signing up.
"""

from fastapi import APIRouter

from app.api.schemas.common import ApiModel
from app.services import luts
from app.services.color_analysis import LOOKS

router = APIRouter(prefix="/catalog", tags=["catalog"])


class LutEntry(ApiModel):
    """One look. `scene` is what the grade is *for*, which is what makes a
    picker choosable rather than five names and a shrug."""

    name: str
    scene: dict[str, str]


class LutCatalogue(ApiModel):
    luts: list[LutEntry]


@router.get("/luts", response_model=LutCatalogue, summary="The colour looks we can render")
async def list_luts() -> LutCatalogue:
    """Only the looks with a `.cube` on this server's disk.

    Filtered rather than listed from `LOOKS` alone, because a name without a
    file is precisely the failure this endpoint exists to prevent: the client
    would offer it, the browser would fetch a 404, and the picture would not
    change. `tests/test_luts.py` asserts the two agree, so in a healthy
    deployment this filter removes nothing — it is here for the deployment that
    is not healthy, where it degrades to a shorter list instead of a broken
    grade.
    """
    available = luts.available()
    return LutCatalogue(
        luts=[
            LutEntry(name=name, scene={str(k): str(v) for k, v in scene.items()})
            for name, scene in LOOKS.items()
            if name in available
        ]
    )

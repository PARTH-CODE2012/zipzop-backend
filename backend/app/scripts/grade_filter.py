"""Print the export renderer's grade filter, for the parity check to use.

**So there is one implementation of the grade and not two.** `e2e/lut-parity.mjs`
compares the browser's shader against the export renderer; if it built the
filter string itself, it would be comparing the browser against a *copy* of the
renderer, and the copy would be the thing kept correct.

That is not hypothetical. The parity check did carry its own copy for about an
hour, and in that hour it reported the blend-order bug as unfixed after the fix
had landed — because the copy still had it.

    python -m app.scripts.grade_filter --lut cyberpunk --strength 0.6
"""

import argparse

from app.api.schemas.project import MediaClip
from app.services import luts
from app.services.render_graph import _grade_filters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lut", required=True)
    parser.add_argument("--strength", type=float, required=True)
    args = parser.parse_args()

    clip = MediaClip.model_validate(
        {
            "id": "clp_parity",
            "assetId": "ast_parity",
            "startMs": 0,
            "durationMs": 1000,
            "effects": [{"type": "color_grade", "lut": args.lut, "strength": args.strength}],
        }
    )
    print(",".join(_grade_filters(clip, luts.path_for)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

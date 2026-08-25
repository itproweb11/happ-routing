#!/usr/bin/env python3
"""
Собирает итоговый Happ-профиль маршрутизации из шаблона:

HAPP/ROUTING.JSON
HAPP/ROUTING.DEEPLINK
HAPP/ROUTING.ONADD.DEEPLINK

Подставляет прямые ссылки GitHub на geosite/geoip
и обновляет LastUpdated.
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:

    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--template",
        default="config/routing-template.json"
    )

    ap.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="USER/REPO"
    )

    ap.add_argument(
        "--tag",
        required=True,
        help="release tag"
    )

    ap.add_argument(
        "--last-updated",
        default=str(int(time.time()))
    )

    ap.add_argument(
        "--outdir",
        default="HAPP"
    )

    args = ap.parse_args()


    if not args.repo or "/" not in args.repo:
        print(
            "Ошибка: укажите --repo USER/REPO",
            file=sys.stderr
        )
        return 1


    cfg = json.loads(
        Path(args.template)
        .read_text(encoding="utf-8")
    )


    # Прямые ссылки GitHub без jsdelivr-кэша

    base = (
        f"https://raw.githubusercontent.com/"
        f"{args.repo}/main/release"
    )


    cfg["Geositeurl"] = (
        f"{base}/geosite.dat"
    )

    cfg["Geoipurl"] = (
        f"{base}/geoip.dat"
    )


    cfg["LastUpdated"] = (
        args.last_updated
    )


    outdir = Path(args.outdir)

    outdir.mkdir(
        parents=True,
        exist_ok=True
    )


    json_text = json.dumps(
        cfg,
        ensure_ascii=False,
        indent=2
    ) + "\n"


    (
        outdir / "ROUTING.JSON"
    ).write_text(
        json_text,
        encoding="utf-8"
    )


    # deeplink add

    compact = json.dumps(
        cfg,
        ensure_ascii=False,
        separators=(",", ":")
    )


    b64 = base64.b64encode(
        compact.encode("utf-8")
    ).decode("ascii")


    (
        outdir / "ROUTING.DEEPLINK"
    ).write_text(
        f"happ://routing/add/{b64}\n",
        encoding="utf-8"
    )


    # deeplink auto add

    (
        outdir / "ROUTING.ONADD.DEEPLINK"
    ).write_text(
        f"happ://routing/onadd/{b64}\n",
        encoding="utf-8"
    )


    print(
        f"OK: профиль '{cfg.get('Name')}'"
    )

    print(
        f"Geo: {base}"
    )

    print(
        "Generated:"
    )

    print(
        " HAPP/ROUTING.JSON"
    )

    print(
        " HAPP/ROUTING.DEEPLINK"
    )

    print(
        " HAPP/ROUTING.ONADD.DEEPLINK"
    )


    return 0



if __name__ == "__main__":
    sys.exit(main())

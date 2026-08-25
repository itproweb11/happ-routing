#!/usr/bin/env python3
"""
Собирает итоговый Happ-профиль маршрутизации:

HAPP/ROUTING.JSON
HAPP/ROUTING.DEEPLINK
HAPP/ROUTING.ONADD.DEEPLINK

Генерирует ссылки:
- geosite.dat через jsDelivr (быстро)
- geoip.dat через GitHub raw (актуально)

Обновляет LastUpdated для принудительной загрузки Happ.
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--template",
        default="config/routing-template.json"
    )

    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="USER/REPO"
    )

    parser.add_argument(
        "--tag",
        required=True
    )

    parser.add_argument(
        "--last-updated",
        default=str(int(time.time()))
    )

    parser.add_argument(
        "--outdir",
        default="HAPP"
    )

    args = parser.parse_args()


    if not args.repo or "/" not in args.repo:
        print(
            "Ошибка: нужен USER/REPO",
            file=sys.stderr
        )
        return 1


    template = Path(args.template)

    if not template.exists():
        print(
            f"Нет шаблона: {template}",
            file=sys.stderr
        )
        return 1


    cfg = json.loads(
        template.read_text(
            encoding="utf-8"
        )
    )


    # ----------------------------
    # GEO URL
    # ----------------------------

    jsdelivr = (
        f"https://cdn.jsdelivr.net/gh/"
        f"{args.repo}@main/release"
    )

    github_raw = (
        f"https://raw.githubusercontent.com/"
        f"{args.repo}/main/release"
    )


    cfg["Geositeurl"] = (
        f"{jsdelivr}/geosite.dat"
    )


    cfg["Geoipurl"] = (
        f"{github_raw}/geoip.dat"
    )


    cfg["LastUpdated"] = (
        args.last_updated
    )


    # ----------------------------
    # SAVE JSON
    # ----------------------------

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


    (outdir / "ROUTING.JSON").write_text(
        json_text,
        encoding="utf-8"
    )


    # ----------------------------
    # BASE64 FOR HAPP
    # ----------------------------

    compact = json.dumps(
        cfg,
        ensure_ascii=False,
        separators=(",", ":")
    )


    b64 = base64.b64encode(
        compact.encode("utf-8")
    ).decode("ascii")


    (outdir / "ROUTING.DEEPLINK").write_text(
        f"happ://routing/add/{b64}\n",
        encoding="utf-8"
    )


    (outdir / "ROUTING.ONADD.DEEPLINK").write_text(
        f"happ://routing/onadd/{b64}\n",
        encoding="utf-8"
    )


    print(
        "OK: Happ routing generated"
    )

    print(
        f"Repository: {args.repo}"
    )

    print(
        f"Geosite: {jsdelivr}/geosite.dat"
    )

    print(
        f"Geoip: {github_raw}/geoip.dat"
    )

    print(
        f"Updated: {args.last_updated}"
    )


    return 0



if __name__ == "__main__":
    sys.exit(main())

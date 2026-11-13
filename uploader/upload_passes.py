#!/usr/bin/env python3
"""Upload SatDump passes (images + metadata) from the station PC to Supabase.

Usage:
    python upload_passes.py                 # upload everything new, then exit
    python upload_passes.py --watch         # keep running, check every 60 s
    python upload_passes.py --dry-run       # show what would be uploaded
    python upload_passes.py --dir "D:\\SatDump\\live_output"

Configuration comes from a ".env" file (see .env.example) or environment
variables: SUPABASE_URL, SUPABASE_SERVICE_KEY, SATDUMP_OUTPUT_DIR, STATION_NAME.

Each sub-folder of the SatDump output directory is treated as one pass.
Re-running is safe: passes and images that are already online are skipped.
"""
from __future__ import annotations

import argparse
import io
import json
import mimetypes
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BUCKET = "satellite-images"
IMAGE_EXT = {".png", ".jpg", ".jpeg"}
THUMB_MAX_PX = 640
SETTLE_SECONDS = 120  # skip folders still being written by SatDump


# ----------------------------------------------------------------- helpers
def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    here = Path(__file__).resolve().parent
    for candidate in (Path.cwd() / ".env", here / ".env", here.parent / ".env"):
        if candidate.exists():
            load_dotenv(candidate)
            return


def safe_key(text: str) -> str:
    """Storage keys only accept a limited character set."""
    text = text.replace("\\", "/")
    return re.sub(r"[^A-Za-z0-9._/-]+", "_", text).strip("_")


def read_pass_meta(folder: Path) -> dict:
    """Satellite name, time and frequency from dataset.json / the folder name."""
    satellite, pass_time, freq = None, None, None

    dataset = folder / "dataset.json"
    if dataset.exists():
        try:
            data = json.loads(dataset.read_text(encoding="utf-8", errors="ignore"))
            satellite = data.get("satellite") or data.get("satellite_name")
            ts = data.get("timestamp")
            if isinstance(ts, (int, float)) and ts > 0:
                pass_time = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            pass

    name = folder.name
    if pass_time is None:
        # e.g. "2026-11-10_08-32_meteor_m2-x_lrpt_137.9 MHz"
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})[_ T](\d{2})-(\d{2})(?:-(\d{2}))?", name)
        if m:
            y, mo, d, h, mi, s = (int(x) if x else 0 for x in m.groups())
            try:
                # SatDump names live folders in local time
                pass_time = datetime(y, mo, d, h, mi, s).astimezone(timezone.utc)
            except ValueError:
                pass
    if pass_time is None:
        pass_time = datetime.fromtimestamp(folder.stat().st_mtime, tz=timezone.utc)

    m = re.search(r"(\d{3}(?:\.\d+)?)\s*MHz", name, flags=re.IGNORECASE)
    if m:
        freq = float(m.group(1))

    if not satellite:
        satellite = "METEOR-M2" if "meteor" in name.lower() else "Unknown"

    return {"satellite": str(satellite), "pass_time": pass_time.isoformat(), "frequency_mhz": freq}


def find_images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXT)


def product_name(folder: Path, image: Path) -> str:
    rel = image.relative_to(folder)
    parts = list(rel.parts[:-1]) + [rel.stem]
    return " / ".join(parts)


def image_info(path: Path) -> tuple[int | None, int | None, bytes | None]:
    """(width, height, jpeg thumbnail bytes). Needs Pillow; degrades gracefully."""
    try:
        from PIL import Image
    except ImportError:
        return None, None, None
    try:
        Image.MAX_IMAGE_PIXELS = None  # satellite swaths are large on purpose
        with Image.open(path) as im:
            width, height = im.size
            if im.mode.startswith("I"):  # 16-bit greyscale channel -> 8-bit
                im = im.point(lambda v: v / 256).convert("L")
            elif im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            else:
                im = im.copy()
            im.thumbnail((THUMB_MAX_PX, THUMB_MAX_PX))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=80)
            return width, height, buf.getvalue()
    except Exception as exc:  # corrupt / partial image
        print(f"      ! could not read image ({exc})")
        return None, None, None


def is_settled(folder: Path) -> bool:
    newest = max((p.stat().st_mtime for p in folder.rglob("*") if p.is_file()), default=0)
    return (time.time() - newest) > SETTLE_SECONDS


# ------------------------------------------------------------------ upload
def upload_pass(sb, folder: Path, station: str, dry_run: bool) -> int:
    images = find_images(folder)
    if not images:
        return 0
    meta = read_pass_meta(folder)

    if dry_run:
        print(f"[dry-run] {folder.name}: {meta['satellite']} @ {meta['pass_time']} "
              f"- {len(images)} image(s)")
        for img in images:
            print(f"           {product_name(folder, img)}")
        return len(images)

    row = {"folder_name": folder.name, "station": station, **meta}
    res = sb.table("passes").upsert(row, on_conflict="folder_name").execute()
    pass_id = res.data[0]["id"]

    existing = sb.table("images").select("storage_path").eq("pass_id", pass_id).execute()
    done = {r["storage_path"] for r in existing.data}

    storage = sb.storage.from_(BUCKET)
    uploaded = 0
    for img in images:
        rel = img.relative_to(folder).as_posix()
        key = safe_key(f"{folder.name}/{rel}")
        if key in done:
            continue

        content_type = mimetypes.guess_type(img.name)[0] or "application/octet-stream"
        storage.upload(path=key, file=img.read_bytes(),
                       file_options={"content-type": content_type, "upsert": "true"})

        width, height, thumb = image_info(img)
        thumb_url = None
        if thumb:
            thumb_key = safe_key(f"thumbs/{folder.name}/{Path(rel).with_suffix('.jpg').as_posix()}")
            storage.upload(path=thumb_key, file=thumb,
                           file_options={"content-type": "image/jpeg", "upsert": "true"})
            thumb_url = storage.get_public_url(thumb_key).rstrip("?")

        sb.table("images").upsert({
            "pass_id": pass_id,
            "product": product_name(folder, img),
            "file_name": img.name,
            "storage_path": key,
            "public_url": storage.get_public_url(key).rstrip("?"),
            "thumb_url": thumb_url,
            "size_bytes": img.stat().st_size,
            "width": width,
            "height": height,
        }, on_conflict="storage_path").execute()
        uploaded += 1
        print(f"      + {rel}")

    if uploaded:
        print(f"  {folder.name}: {uploaded} new image(s) uploaded")
    return uploaded


def scan(sb, root: Path, station: str, dry_run: bool, only_settled: bool) -> int:
    total = 0
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if only_settled and not is_settled(folder):
            print(f"  {folder.name}: still being written, will retry")
            continue
        try:
            total += upload_pass(sb, folder, station, dry_run)
        except Exception as exc:  # keep going; next scan retries this pass
            print(f"  ! {folder.name}: failed ({exc})")
    return total


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description="Upload SatDump passes to Supabase")
    ap.add_argument("--dir", default=os.getenv("SATDUMP_OUTPUT_DIR"),
                    help="SatDump output folder (one sub-folder per pass)")
    ap.add_argument("--watch", action="store_true", help="keep running and upload new passes")
    ap.add_argument("--interval", type=int, default=60, help="seconds between scans in --watch")
    ap.add_argument("--dry-run", action="store_true", help="list what would be uploaded")
    args = ap.parse_args()

    if not args.dir:
        print("Set SATDUMP_OUTPUT_DIR in .env or pass --dir")
        return 2
    root = Path(args.dir).expanduser()
    if not root.is_dir():
        print(f"Folder not found: {root}")
        return 2

    sb = None
    if not args.dry_run:
        url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
        if not url or not key:
            print("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in .env")
            return 2
        from supabase import create_client
        sb = create_client(url, key)

    station = os.getenv("STATION_NAME", "Jazan University Ground Station")
    print(f"Scanning {root}")
    if not args.watch:
        n = scan(sb, root, station, args.dry_run, only_settled=False)
        print(f"Done. {n} image(s) {'found' if args.dry_run else 'uploaded'}.")
        return 0

    print(f"Watching every {args.interval}s. Press Ctrl+C to stop.")
    try:
        while True:
            scan(sb, root, station, args.dry_run, only_settled=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

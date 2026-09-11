"""Enkoduje referentni klip u sve varijante iz matrice.

Upotreba:
    python -m vqh.encode clips/ref.mp4 --out out/
    python -m vqh.encode clips/ref.mp4 --codecs h264 av1 --bitrates 1000 2000
    python -m vqh.encode clips/ref.mp4 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from vqh import ffmpeg
from vqh.variants import CODECS, Matrix, Variant


@dataclass
class EncodeResult:
    variant: str
    codec: str
    target_bitrate_kbps: int
    preset: str
    path: str
    encode_seconds: float
    actual_bitrate_kbps: float
    size_bytes: int
    frames: int

    @property
    def bitrate_error_pct(self) -> float:
        if not self.target_bitrate_kbps:
            return 0.0
        delta = self.actual_bitrate_kbps - self.target_bitrate_kbps
        return round(100 * delta / self.target_bitrate_kbps, 1)


def encode_one(src: Path, variant: Variant, out_dir: Path,
               *, deterministic: bool = False) -> EncodeResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / variant.filename

    started = time.perf_counter()
    ffmpeg.run(variant.ffmpeg_args(str(src), str(dst), deterministic=deterministic))
    elapsed = time.perf_counter() - started

    info = ffmpeg.probe(dst)
    return EncodeResult(
        variant=variant.name,
        codec=variant.codec,
        target_bitrate_kbps=variant.bitrate_kbps,
        preset=variant.preset,
        path=str(dst),
        encode_seconds=round(elapsed, 2),
        actual_bitrate_kbps=info.bitrate_kbps,
        size_bytes=info.size_bytes,
        frames=info.frames,
    )


def encode_all(src: Path, matrix: Matrix, out_dir: Path,
               *, verbose: bool = True,
               deterministic: bool = False) -> list[EncodeResult]:
    variants = matrix.expand()
    results: list[EncodeResult] = []

    for i, variant in enumerate(variants, start=1):
        if verbose:
            print(f"[{i}/{len(variants)}] {variant.name} ... ", end="", flush=True)
        try:
            result = encode_one(src, variant, out_dir, deterministic=deterministic)
        except ffmpeg.FFmpegError as exc:
            if verbose:
                print("PAO")
                print(f"    {exc}", file=sys.stderr)
            continue
        results.append(result)
        if verbose:
            print(
                f"{result.encode_seconds}s, "
                f"{result.actual_bitrate_kbps} kbps "
                f"({result.bitrate_error_pct:+.1f}%)"
            )
    return results


def write_manifest(results: list[EncodeResult], path: Path,
                   *, merge: bool = True) -> Path:
    """Manifest je ulaz za sledeci korak (merenje VMAF-a) i za CI artefakt.

    merge=True dopunjuje postojeci manifest umesto da ga prepise: novi zapisi
    zamenjuju istoimene, ostali se cuvaju. Bez toga bi enkodovanje jednog
    kodeka obrisalo zapise o svima ostalima, iako fajlovi i dalje postoje.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        r.variant: {**asdict(r), "bitrate_error_pct": r.bitrate_error_pct}
        for r in results
    }

    if merge and path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = []
        merged = {e["variant"]: e for e in existing if "variant" in e}
        merged.update(payload)
        payload = merged

    ordered = sorted(payload.values(), key=lambda e: e["variant"])
    path.write_text(json.dumps(ordered, indent=2), encoding="utf-8")
    return path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vqh.encode", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", type=Path, help="referentni (master) klip")
    p.add_argument("--out", type=Path, default=Path("out"), help="izlazni direktorijum")
    p.add_argument("--codecs", nargs="+", choices=sorted(CODECS), default=None)
    p.add_argument("--bitrates", nargs="+", type=int, default=None,
                   help="ciljni bitrate-ovi u kbps")
    p.add_argument("--presets", nargs="+", default=None)
    p.add_argument("--resolutions", nargs="+", default=None,
                   help="npr. 1280x720 640x360; izostavi da zadrzis izvornu")
    p.add_argument("--deterministic", action="store_true",
                   help="jedna nit po enkoderu — ponovljiv rezultat, sporije")
    p.add_argument("--no-merge", action="store_true",
                   help="prepisi manifest umesto da ga dopunis")
    p.add_argument("--dry-run", action="store_true",
                   help="ispisi ffmpeg komande bez izvrsavanja")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    matrix = Matrix()
    if args.codecs:
        matrix.codecs = args.codecs
    if args.bitrates:
        matrix.bitrates_kbps = args.bitrates
    if args.presets:
        matrix.presets = args.presets
    if args.resolutions:
        matrix.resolutions = list(args.resolutions)

    if args.dry_run:
        for v in matrix.expand():
            cmd = v.ffmpeg_args(str(args.source), str(args.out / v.filename),
                                deterministic=args.deterministic)
            print("ffmpeg -hide_banner -y " + " ".join(cmd))
        return 0

    if not args.source.exists():
        print(f"Nema izvornog fajla: {args.source}", file=sys.stderr)
        return 2

    info = ffmpeg.probe(args.source)
    print(f"Izvor: {args.source} — {info.resolution} {info.codec} "
          f"{info.duration:.1f}s {info.bitrate_kbps} kbps\n")

    results = encode_all(args.source, matrix, args.out,
                         deterministic=args.deterministic)
    manifest = write_manifest(results, args.out / "manifest.json",
                              merge=not args.no_merge)

    total = len(json.loads(manifest.read_text(encoding="utf-8")))
    print(f"\nGotovo: {len(results)} varijanti u ovom prolazu, "
          f"{total} ukupno u manifestu: {manifest}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())

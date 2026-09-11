"""Merenje kvaliteta enkodovanih varijanti u odnosu na referentni klip.

Koristi ffmpeg filter `libvmaf`, koji uz VMAF ume da izracuna i PSNR i SSIM
u istom prolazu — jedno dekodovanje umesto tri.

Upotreba:
    python -m vqh.quality clips/ref.mp4 --manifest out/manifest.json
    python -m vqh.quality clips/ref.mp4 --manifest out/manifest.json --subsample 5
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from vqh import ffmpeg


class VmafNotAvailable(RuntimeError):
    pass


def require_libvmaf() -> None:
    """Rana i jasna greska umesto zbunjujuceg pada usred merenja."""
    proc = ffmpeg.subprocess.run(
        [ffmpeg._require("ffmpeg"), "-hide_banner", "-filters"],
        capture_output=True, text=True,
    )
    if " libvmaf " not in proc.stdout:
        raise VmafNotAvailable(
            "Ovaj ffmpeg build nema filter 'libvmaf' (filter 'vmafmotion' nije "
            "zamena — racuna samo komponentu pokreta). Uzmi staticki build:\n"
            "  https://github.com/BtbN/FFmpeg-Builds/releases"
        )


def mini_gop_length(path: Path | str, *, sample_frames: int = 60) -> int | None:
    """Procenjuje duzinu mini-GOP-a iz razmaka izmedju referentnih frejmova.

    Sluzi samo za upozorenje o aliasingu — vraca None ako struktura nije pravilna.
    """
    proc = ffmpeg.subprocess.run(
        [ffmpeg._require("ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-read_intervals", f"%+#{sample_frames}",
         "-show_entries", "frame=pict_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    types = [t.strip().strip(",") for t in proc.stdout.split() if t.strip()]
    refs = [i for i, t in enumerate(types) if t in ("I", "P")]
    if len(refs) < 4:
        return None

    gaps = [b - a for a, b in zip(refs, refs[1:])]
    # Poslednji razmak je skracen jer je isecak presecen usred GOP-a — odbacujemo ga.
    gaps = gaps[:-1]
    if not gaps:
        return None

    common, count = Counter(gaps).most_common(1)[0]
    # Trazimo jasnu pravilnost; mesovita struktura ne daje upotrebljivo upozorenje.
    return common if count / len(gaps) >= 0.8 else None


def warn_on_subsample_aliasing(path: Path | str, subsample: int) -> str | None:
    """Vraca tekst upozorenja ako se korak odabiranja poklapa sa mini-GOP-om."""
    if subsample <= 1:
        return None
    gop = mini_gop_length(path)
    if gop and gop > 1 and subsample % gop == 0:
        return (
            f"UPOZORENJE: korak {subsample} je umnozak mini-GOP-a ({gop}) — "
            f"meri se uvek isti polozaj u nizu (I i P, nikad B) i rezultat "
            f"ispada previsok. Koristi prost korak (7, 11, 13) ili pun prolaz."
        )
    return None


@dataclass
class QualityResult:
    variant: str
    path: str
    frames: int
    vmaf_mean: float
    vmaf_min: float
    vmaf_p1: float          # 1. percentil — hvata najgore scene
    vmaf_harmonic: float    # kaznjava povremene propade jace od proseka
    psnr_y: float | None = None
    ssim: float | None = None
    # Popunjava se iz manifesta enkodiranja radi krive kvalitet/bitrate
    target_bitrate_kbps: int | None = None
    actual_bitrate_kbps: float | None = None
    codec: str | None = None
    preset: str | None = None
    encode_seconds: float | None = None


def _harmonic_mean(values: list[float]) -> float:
    """VMAF skala je 0-100; nula bi razbila harmonijsku sredinu, pa je pomeramo."""
    safe = [max(v, 0.1) for v in values]
    return statistics.harmonic_mean(safe)


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = max(0, min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1)))))
    return ordered[idx]


def _build_filter(log_path: Path, *, ref_w: int, ref_h: int,
                  threads: int, subsample: int, with_extra: bool) -> str:
    """Sklapa filtergraph za libvmaf.

    Prvi ulaz je ISPITIVANI klip, drugi je referenca — libvmaf ocekuje
    [main][reference] tim redom. Zamena mesta daje tiho pogresan rezultat.
    """
    opts = [
        "log_fmt=json",
        f"log_path={log_path}",
        f"n_threads={threads}",
    ]
    if subsample > 1:
        opts.append(f"n_subsample={subsample}")
    if with_extra:
        # Znak | mora biti eskejpovan unutar filtergraph-a
        opts.append(r"feature=name=psnr\|name=float_ssim")

    vmaf = "libvmaf=" + ":".join(opts)

    # Skaliramo ispitivani klip na rezoluciju reference. Bez ovoga poredjenje
    # varijanti nizih rezolucija ili puca ili meri pogresnu stvar.
    return (
        f"[0:v]scale={ref_w}:{ref_h}:flags=bicubic,setpts=PTS-STARTPTS[dist];"
        f"[1:v]setpts=PTS-STARTPTS[ref];"
        f"[dist][ref]{vmaf}"
    )


def measure(reference: Path, distorted: Path, *, threads: int = 4,
            subsample: int = 1, with_extra: bool = True) -> QualityResult:
    """Meri kvalitet jednog fajla u odnosu na referencu."""
    reference, distorted = Path(reference), Path(distorted)
    ref_info = ffmpeg.probe(reference)

    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "vmaf.json"
        graph = _build_filter(
            log_path, ref_w=ref_info.width, ref_h=ref_info.height,
            threads=threads, subsample=subsample, with_extra=with_extra,
        )
        ffmpeg.run([
            "-i", str(distorted),
            "-i", str(reference),
            "-lavfi", graph,
            "-f", "null", "-",
        ])
        data = json.loads(log_path.read_text(encoding="utf-8"))

    per_frame = [f["metrics"]["vmaf"] for f in data.get("frames", [])]
    pooled = data.get("pooled_metrics", {})

    def pooled_mean(key: str) -> float | None:
        entry = pooled.get(key)
        return round(entry["mean"], 3) if entry else None

    vmaf_pool = pooled.get("vmaf", {})
    return QualityResult(
        variant=distorted.stem,
        path=str(distorted),
        frames=len(per_frame),
        vmaf_mean=round(vmaf_pool.get("mean", 0.0), 3),
        vmaf_min=round(vmaf_pool.get("min", min(per_frame, default=0.0)), 3),
        vmaf_p1=round(_percentile(per_frame, 1), 3),
        vmaf_harmonic=round(_harmonic_mean(per_frame), 3) if per_frame else 0.0,
        psnr_y=pooled_mean("psnr_y"),
        ssim=pooled_mean("float_ssim"),
    )


def measure_manifest(reference: Path, manifest_path: Path, *, threads: int = 4,
                     subsample: int = 1, verbose: bool = True) -> list[QualityResult]:
    """Meri sve varijante iz manifesta i spaja podatke o bitrate-u."""
    entries = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    results: list[QualityResult] = []

    for i, entry in enumerate(entries, start=1):
        path = Path(entry["path"])
        if verbose:
            print(f"[{i}/{len(entries)}] {entry['variant']} ... ", end="", flush=True)
        if not path.exists():
            if verbose:
                print("NEMA FAJLA")
            continue
        try:
            result = measure(reference, path, threads=threads, subsample=subsample)
        except ffmpeg.FFmpegError as exc:
            if verbose:
                print("PAO")
                print(f"    {exc}", file=sys.stderr)
            continue

        result.target_bitrate_kbps = entry.get("target_bitrate_kbps")
        result.actual_bitrate_kbps = entry.get("actual_bitrate_kbps")
        result.codec = entry.get("codec")
        result.preset = entry.get("preset")
        result.encode_seconds = entry.get("encode_seconds")
        results.append(result)

        if verbose:
            print(f"VMAF {result.vmaf_mean} (min {result.vmaf_min}, "
                  f"p1 {result.vmaf_p1})")
            warning = warn_on_subsample_aliasing(path, subsample)
            if warning:
                print(f"    {warning}", file=sys.stderr)
    return results


def print_table(results: list[QualityResult]) -> None:
    if not results:
        return
    header = f"{'varijanta':<28} {'kbps':>8} {'VMAF':>7} {'p1':>7} {'PSNR-Y':>7} {'s':>6}"
    print("\n" + header)
    print("-" * len(header))
    for r in sorted(results, key=lambda x: (x.codec or "", x.actual_bitrate_kbps or 0)):
        psnr = f"{r.psnr_y:.2f}" if r.psnr_y is not None else "-"
        enc = f"{r.encode_seconds:.1f}" if r.encode_seconds is not None else "-"
        print(f"{r.variant:<28} {r.actual_bitrate_kbps or 0:>8.0f} "
              f"{r.vmaf_mean:>7.2f} {r.vmaf_p1:>7.2f} {psnr:>7} {enc:>6}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vqh.quality", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("reference", type=Path, help="referentni (master) klip")
    p.add_argument("--manifest", type=Path, default=Path("out/manifest.json"))
    p.add_argument("--out", type=Path, default=None,
                   help="izlazni JSON (podrazumevano pored manifesta)")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--subsample", type=int, default=1,
                   help="meri svaki N-ti frejm — bitno ubrzanje na slabijem CPU-u")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        require_libvmaf()
    except VmafNotAvailable as exc:
        print(exc, file=sys.stderr)
        return 3

    if not args.manifest.exists():
        print(f"Nema manifesta: {args.manifest} — prvo pokreni vqh.encode",
              file=sys.stderr)
        return 2

    results = measure_manifest(args.reference, args.manifest,
                               threads=args.threads, subsample=args.subsample)
    print_table(results)

    out = args.out or args.manifest.with_name("quality.json")
    out.write_text(json.dumps([asdict(r) for r in results], indent=2),
                   encoding="utf-8")
    print(f"\nRezultati: {out}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())

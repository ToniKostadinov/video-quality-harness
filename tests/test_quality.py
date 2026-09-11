"""Regresioni testovi kvaliteta.

Ovo je srce harness-a: enkoduj matricu, izmeri kvalitet, padni ako je
ispod praga. Pragovi su u thresholds.json da bi se menjali bez diranja koda.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vqh import ffmpeg
from vqh.encode import encode_one
from vqh.quality import VmafNotAvailable, measure, require_libvmaf
from vqh.variants import Variant

ROOT = Path(__file__).resolve().parents[1]

# Namerno thresholds.ci.json, ne thresholds.json: testovi rade na generisanom
# klipu, koji je znatno laksi od pravog materijala. Pragovi izvedeni iz Park Joy-a
# ovde ne bi imali smisla. Svrha ovih testova je detekcija regresije, ne ocena
# kodeka — za to se koristi vqh.quality na pravom klipu.
THRESHOLDS = json.loads((ROOT / "thresholds.ci.json").read_text(encoding="utf-8"))

# Matrica koju testiramo. Namerno mala — testovi treba da traju sekunde.
VARIANTS = [
    Variant(codec="h264", bitrate_kbps=300, preset="veryfast"),
    Variant(codec="h264", bitrate_kbps=1500, preset="veryfast"),
    Variant(codec="hevc", bitrate_kbps=300, preset="veryfast"),
    Variant(codec="hevc", bitrate_kbps=1500, preset="veryfast"),
    Variant(codec="av1", bitrate_kbps=1500, preset="10"),
]


def thresholds_for(variant_name: str) -> dict:
    merged = dict(THRESHOLDS["default"])
    merged.update(THRESHOLDS.get("variants", {}).get(variant_name, {}))
    return merged


@pytest.fixture(scope="session")
def vmaf_available():
    try:
        require_libvmaf()
    except VmafNotAvailable as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="session")
def reference_clip(tmp_path_factory):
    dest = tmp_path_factory.mktemp("clips") / "ref.mp4"
    return ffmpeg.make_test_clip(dest, seconds=2, size="640x360", fps=30)


@pytest.fixture(scope="session")
def encoded(reference_clip, tmp_path_factory):
    """Enkoduje celu matricu jednom za celu sesiju.

    deterministic=True: jedna nit po enkoderu. Sporije, ali isti ulaz daje isti
    izlaz, pa pragovi u CI-ju ne padaju nasumicno.
    """
    out_dir = tmp_path_factory.mktemp("out")
    return {v.name: encode_one(reference_clip, v, out_dir, deterministic=True)
            for v in VARIANTS}


@pytest.fixture(scope="session")
def measured(vmaf_available, reference_clip, encoded):
    """Meri kvalitet jednom; testovi samo proveravaju pragove."""
    return {
        name: measure(reference_clip, result.path, threads=4, subsample=1)
        for name, result in encoded.items()
    }


@pytest.mark.parametrize("variant", VARIANTS, ids=lambda v: v.name)
def test_vmaf_mean_above_threshold(measured, variant):
    limit = thresholds_for(variant.name)["vmaf_mean"]
    actual = measured[variant.name].vmaf_mean
    assert actual >= limit, f"{variant.name}: VMAF {actual} < prag {limit}"


@pytest.mark.parametrize("variant", VARIANTS, ids=lambda v: v.name)
def test_vmaf_worst_frames_above_threshold(measured, variant):
    """Prosek krije propade. 1. percentil hvata najgore scene."""
    limit = thresholds_for(variant.name)["vmaf_p1"]
    actual = measured[variant.name].vmaf_p1
    assert actual >= limit, f"{variant.name}: VMAF p1 {actual} < prag {limit}"


@pytest.mark.parametrize("variant", VARIANTS, ids=lambda v: v.name)
def test_bitrate_within_tolerance(encoded, variant):
    limit = thresholds_for(variant.name)["bitrate_error_pct"]
    actual = abs(encoded[variant.name].bitrate_error_pct)
    assert actual <= limit, (
        f"{variant.name}: odstupanje bitrate-a {actual}% > dozvoljenih {limit}%"
    )


def test_higher_bitrate_gives_higher_quality(measured):
    """Zdravorazumska provera: vise bitova mora dati bolji kvalitet.

    Ako ovo padne, greska je u harness-u ili u rate control-u, ne u kodeku.
    """
    for codec in ("h264", "hevc"):
        low = measured[f"{codec}_src_300k_pveryfast"].vmaf_mean
        high = measured[f"{codec}_src_1500k_pveryfast"].vmaf_mean
        assert high > low, f"{codec}: 1500k ({high}) nije bolji od 300k ({low})"


def test_metrics_are_populated(measured):
    """libvmaf u istom prolazu vraca i PSNR i SSIM — ako ne, opcija feature ne radi."""
    for name, result in measured.items():
        assert result.frames > 0, f"{name}: nema izmerenih frejmova"
        assert result.psnr_y is not None, f"{name}: PSNR nije izracunat"
        assert 0.0 < result.ssim <= 1.0, f"{name}: SSIM van opsega ({result.ssim})"

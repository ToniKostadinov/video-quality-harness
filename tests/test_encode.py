"""Smoke testovi: da li se varijante uopste enkoduju i pogadjaju li bitrate.

Pravi testovi kvaliteta (VMAF pragovi) dolaze u sledecem koraku.
"""




from __future__ import annotations

import pytest


from vqh.variants import encoder_available

def _hw(codec: str, preset: str):
    return pytest.param(
        codec, preset,
        marks=pytest.mark.skipif(not encoder_available(codec),
                                 reason=f"{codec} nije dostupan na ovoj masini"),
    )



from vqh import ffmpeg
from vqh.encode import encode_one
from vqh.variants import Matrix, Variant


@pytest.fixture(scope="session")
def reference_clip(tmp_path_factory):
    """Kratak sinteticki klip — dovoljno da testovi rade bez spoljnih fajlova."""
    dest = tmp_path_factory.mktemp("clips") / "ref.mp4"
    return ffmpeg.make_test_clip(dest, seconds=2, size="640x360", fps=30)


def test_probe_reads_source(reference_clip):
    info = ffmpeg.probe(reference_clip)
    assert info.resolution == "640x360"
    assert info.codec == "h264"
    assert info.frames == 60


def test_matrix_expands_to_expected_count():
    matrix = Matrix(codecs=["h264", "av1"], bitrates_kbps=[500, 1000])
    variants = matrix.expand()
    assert len(variants) == 4
    assert {v.name for v in variants} == {
        "h264_src_500k_pveryfast",
        "h264_src_1000k_pveryfast",
        "av1_src_500k_p10",
        "av1_src_1000k_p10",
    }


@pytest.mark.parametrize("codec,preset", [
    ("h264", "veryfast"),
    ("hevc", "veryfast"),
    _hw("h264_nvenc", "p4"),
    _hw("hevc_nvenc", "p4"),
])
def test_encode_produces_playable_output(reference_clip, tmp_path, codec, preset):
    variant = Variant(codec=codec, bitrate_kbps=800, preset=preset)
    result = encode_one(reference_clip, variant, tmp_path)

    assert result.frames == 60
    assert result.size_bytes > 0
    # Sirok prag: kratki klipovi imaju veliku gresku bitrate-a na startu GOP-a
    assert abs(result.bitrate_error_pct) < 60


def test_downscale_changes_resolution(reference_clip, tmp_path):
    variant = Variant(codec="h264", bitrate_kbps=400, preset="veryfast",
                      resolution="320x180")
    result = encode_one(reference_clip, variant, tmp_path)
    assert ffmpeg.probe(result.path).resolution == "320x180"

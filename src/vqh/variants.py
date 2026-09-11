"""Matrica varijanti: kodek x bitrate x preset x rezolucija.

Ovo je jedina tacka gde se definise sta se testira. Kad kasnije dodas
VMAF pragove, oni ce se vezivati za ime varijante (`Variant.name`).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

# Mapiranje logickog imena kodeka -> ffmpeg enkoder + specificni flagovi.
# Zamena softverskog enkodera hardverskim (h264_vaapi, hevc_nvenc, av1_qsv)
# se radi ovde i nigde drugde.
CODECS: dict[str, dict] = {
    "h264": {
        "encoder": "libx264",
        "presets": ["veryfast", "medium", "slow"],
        "extra": ["-pix_fmt", "yuv420p"],
        "maxrate_factor": 1.0,
        "deterministic": ["-x264-params", "threads=1:sliced-threads=0"],
    },
    "hevc": {
        "encoder": "libx265",
        "presets": ["veryfast", "medium", "slow"],
        "extra": ["-pix_fmt", "yuv420p", "-tag:v", "hvc1"],
        "maxrate_factor": 1.0,
        "deterministic": ["-x265-params", "pools=none:frame-threads=1"],
    },
    "av1": {
        "encoder": "libsvtav1",
        # SVT-AV1 koristi numericke presete (0 = najsporiji, 13 = najbrzi)
        "presets": ["10", "8", "6"],
        "extra": ["-pix_fmt", "yuv420p"],
        # SVT-AV1 (>= 2.x) prihvata maxrate ISKLJUCIVO u CRF rezimu:
        #   "Max Bitrate only supported with CRF mode"
        # Zato AV1 ide na cist VBR — samo -b:v, bez maxrate i bufsize.
        "maxrate_factor": None,
        "deterministic": ["-svtav1-params", "lp=1"],
    },
}


@dataclass(frozen=True)
class Variant:
    codec: str
    bitrate_kbps: int
    preset: str
    resolution: str | None = None  # None = zadrzi rezoluciju izvora
    gop: int = 60

    @property
    def name(self) -> str:
        res = self.resolution or "src"
        return f"{self.codec}_{res}_{self.bitrate_kbps}k_p{self.preset}"

    @property
    def filename(self) -> str:
        ext = "mp4"
        return f"{self.name}.{ext}"

    def ffmpeg_args(self, src: str, dst: str, *, deterministic: bool = False) -> list[str]:
        """Sklapa punu listu ffmpeg argumenata za ovu varijantu.

        deterministic=True obara enkodere na jednu nit. Sporije, ali isti ulaz
        daje bit-identican izlaz — bez toga se `vmaf_min` menja izmedju
        pokretanja i pragovi u CI-ju postaju nepouzdani.
        """
        spec = CODECS[self.codec]
        args: list[str] = ["-i", src]

        if self.resolution:
            args += ["-vf", f"scale={self.resolution.replace('x', ':')}:flags=bicubic"]

        args += ["-c:v", spec["encoder"]]
        args += list(spec["extra"])
        args += ["-preset", self.preset]

        # Ogranicen bitrate umesto CRF: zelimo poredivu tacku na krivoj
        # kvalitet/bitrate izmedju kodeka.
        args += ["-b:v", f"{self.bitrate_kbps}k"]

        factor = spec.get("maxrate_factor", 1.0)
        if factor is not None:
            maxrate = int(self.bitrate_kbps * factor)
            args += ["-maxrate", f"{maxrate}k", "-bufsize", f"{maxrate * 2}k"]

        args += ["-g", str(self.gop), "-an"]

        if deterministic and spec.get("deterministic"):
            args += list(spec["deterministic"])
        args += [dst]
        return args


@dataclass
class Matrix:
    """Dekartov proizvod kodeka, bitrate-a i preseta."""

    codecs: list[str] = field(default_factory=lambda: ["h264", "hevc", "av1"])
    bitrates_kbps: list[int] = field(default_factory=lambda: [500, 1000, 2000, 4000])
    presets: list[str] | None = None  # None = uzmi prvi preset po kodeku
    resolutions: list[str | None] = field(default_factory=lambda: [None])

    def expand(self) -> list[Variant]:
        out: list[Variant] = []
        for codec in self.codecs:
            presets = self.presets or [CODECS[codec]["presets"][0]]
            for bitrate, preset, res in itertools.product(
                self.bitrates_kbps, presets, self.resolutions
            ):
                out.append(
                    Variant(codec=codec, bitrate_kbps=bitrate, preset=preset, resolution=res)
                )
        return out


# Podrazumevana matrica koja se koristi iz CLI-ja
DEFAULT_MATRIX = Matrix()

"""Tanak sloj oko ffmpeg/ffprobe binarnih fajlova.

Namerno bez python-ffmpeg biblioteka: hocemo da vidimo tacnu komandu
koja se izvrsava, jer je to ono sto se debuguje kad enkod padne.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class FFmpegNotFound(RuntimeError):
    pass


class FFmpegError(RuntimeError):
    def __init__(self, cmd: list[str], returncode: int, stderr: str):
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        tail = "\n".join(stderr.strip().splitlines()[-15:])
        super().__init__(f"ffmpeg je vratio {returncode}\n$ {' '.join(cmd)}\n{tail}")


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if path is None:
        raise FFmpegNotFound(
            f"'{binary}' nije pronadjen u PATH. Instaliraj ffmpeg build sa libvmaf."
        )
    return path


def run(args: list[str], *, quiet: bool = True) -> subprocess.CompletedProcess:
    """Pokrece ffmpeg sa datim argumentima. Podize FFmpegError ako padne."""
    cmd = [_require("ffmpeg"), "-hide_banner", "-y"]
    if quiet:
        cmd += ["-loglevel", "error"]
    cmd += args
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode, proc.stderr)
    return proc


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    codec: str
    duration: float
    frames: int
    bitrate_kbps: float
    size_bytes: int

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


def probe(path: Path | str) -> MediaInfo:
    """Cita osnovne parametre video strima preko ffprobe."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    cmd = [
        _require("ffprobe"),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_streams",
        "-show_format",
        "-count_frames",
        "-of", "json",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode, proc.stderr)

    data = json.loads(proc.stdout)
    stream = data["streams"][0]
    fmt = data["format"]

    size = int(fmt.get("size", path.stat().st_size))
    duration = float(fmt.get("duration") or stream.get("duration") or 0.0)
    frames = int(stream.get("nb_read_frames") or stream.get("nb_frames") or 0)
    bitrate = (size * 8 / 1000 / duration) if duration else 0.0

    return MediaInfo(
        width=int(stream["width"]),
        height=int(stream["height"]),
        codec=stream["codec_name"],
        duration=duration,
        frames=frames,
        bitrate_kbps=round(bitrate, 1),
        size_bytes=size,
    )


def make_test_clip(dest: Path | str, *, seconds: int = 5, size: str = "1280x720",
                   fps: int = 30) -> Path:
    """Generise sinteticki referentni klip (testsrc2) za lokalni smoke test."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    run([
        "-f", "lavfi",
        "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
        str(dest),
    ])
    return dest

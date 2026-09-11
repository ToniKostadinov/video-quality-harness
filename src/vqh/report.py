"""Samostalan HTML izvestaj iz quality.json.

Bez CDN-a i bez JavaScript-a: jedan fajl koji se otvara offline i prezivljava
kao CI artefakt. Grafici su inline SVG.

Upotreba:
    python -m vqh.report --quality out/quality.json --out out/report.html
"""

from __future__ import annotations

import argparse
import html
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Boje serija izvedene iz SMPTE test slike — domenski, ne proizvoljno.
CODEC_STYLE = {
    "h264": {"color": "#C8A200", "label": "H.264"},
    "hevc": {"color": "#0090A8", "label": "HEVC"},
    "av1": {"color": "#B03A78", "label": "AV1"},
}
FALLBACK = {"color": "#5A6472", "label": "?"}


def style_for(codec: str | None) -> dict:
    return CODEC_STYLE.get(codec or "", FALLBACK)


@dataclass
class Row:
    variant: str
    codec: str
    preset: str
    bitrate: float
    vmaf: float
    p1: float
    psnr: float | None
    ssim: float | None
    seconds: float | None
    # popunjava se iz pragova
    limit_vmaf: float | None = None
    limit_p1: float | None = None

    @property
    def passed(self) -> bool:
        ok = True
        if self.limit_vmaf is not None:
            ok = ok and self.vmaf >= self.limit_vmaf
        if self.limit_p1 is not None:
            ok = ok and self.p1 >= self.limit_p1
        return ok

    @property
    def spread(self) -> float:
        """Razmak proseka i 1. percentila — koliko prosek krije."""
        return self.vmaf - self.p1


def load_rows(quality_path: Path, thresholds_path: Path | None) -> list[Row]:
    data = json.loads(quality_path.read_text(encoding="utf-8"))
    limits = {}
    defaults = {}
    if thresholds_path and thresholds_path.exists():
        th = json.loads(thresholds_path.read_text(encoding="utf-8"))
        defaults = th.get("default", {})
        limits = th.get("variants", {})

    rows = []
    for e in data:
        merged = {**defaults, **limits.get(e["variant"], {})}
        rows.append(Row(
            variant=e["variant"],
            codec=e.get("codec") or "",
            preset=str(e.get("preset") or ""),
            bitrate=e.get("actual_bitrate_kbps") or 0.0,
            vmaf=e.get("vmaf_mean") or 0.0,
            p1=e.get("vmaf_p1") or 0.0,
            psnr=e.get("psnr_y"),
            ssim=e.get("ssim"),
            seconds=e.get("encode_seconds"),
            limit_vmaf=merged.get("vmaf_mean"),
            limit_p1=merged.get("vmaf_p1"),
        ))
    return sorted(rows, key=lambda r: (r.codec, r.bitrate))


# --- SVG ---------------------------------------------------------------

def _log_scale(value: float, lo: float, hi: float, px0: float, px1: float) -> float:
    if value <= 0 or hi <= lo:
        return px0
    t = (math.log2(value) - math.log2(lo)) / (math.log2(hi) - math.log2(lo))
    return px0 + t * (px1 - px0)


def _lin_scale(value: float, lo: float, hi: float, px0: float, px1: float) -> float:
    t = (value - lo) / (hi - lo) if hi > lo else 0
    return px0 + t * (px1 - px0)


def curve_svg(rows: list[Row], *, width: int = 760, height: int = 420) -> str:
    """Kriva kvalitet/bitrate, logaritamska x-osa."""
    if not rows:
        return ""
    pad_l, pad_r, pad_t, pad_b = 58, 20, 24, 46
    x0, x1 = pad_l, width - pad_r
    y0, y1 = height - pad_b, pad_t

    bitrates = [r.bitrate for r in rows if r.bitrate > 0]
    lo = min(bitrates) * 0.85
    hi = max(bitrates) * 1.15
    vlo = max(0, min(r.vmaf for r in rows) - 8)
    vhi = 100

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="Kriva kvaliteta u odnosu na bitrate">']

    # horizontalne linije mreze
    for v in range(int(vlo // 10 * 10), 101, 10):
        if v < vlo:
            continue
        y = _lin_scale(v, vlo, vhi, y0, y1)
        parts.append(f'<line class="grid" x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}"/>')
        parts.append(f'<text class="axis" x="{x0 - 10}" y="{y + 4:.1f}" '
                     f'text-anchor="end">{v}</text>')

    # Oznake po nizu 1-2-5 — po jedna po tacki bi se preklapale.
    ticks: list[float] = []
    decade = 10 ** math.floor(math.log10(lo))
    while decade <= hi:
        for mult in (1, 2, 5):
            t = decade * mult
            if lo <= t <= hi:
                ticks.append(t)
        decade *= 10
    for t in ticks:
        x = _log_scale(t, lo, hi, x0, x1)
        parts.append(f'<line class="grid faint" x1="{x:.1f}" y1="{y0}" '
                     f'x2="{x:.1f}" y2="{y1}"/>')
        parts.append(f'<text class="axis" x="{x:.1f}" y="{y0 + 20}" '
                     f'text-anchor="middle">{t / 1000:g}</text>')

    parts.append(f'<text class="axis-title" x="{(x0 + x1) / 2:.0f}" y="{height - 8}" '
                 f'text-anchor="middle">bitrate (Mbps)</text>')
    parts.append(f'<text class="axis-title" transform="rotate(-90 16 {(y0 + y1) / 2:.0f})" '
                 f'x="16" y="{(y0 + y1) / 2:.0f}" text-anchor="middle">VMAF</text>')

    # serije po kodeku
    by_codec: dict[str, list[Row]] = {}
    for r in rows:
        by_codec.setdefault(r.codec, []).append(r)

    for codec, series in by_codec.items():
        st = style_for(codec)
        pts = [(_log_scale(r.bitrate, lo, hi, x0, x1),
                _lin_scale(r.vmaf, vlo, vhi, y0, y1)) for r in series if r.bitrate > 0]
        if len(pts) > 1:
            d = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts)
            parts.append(f'<path d="{d}" fill="none" stroke="{st["color"]}" '
                         f'stroke-width="2.5" stroke-linejoin="round"/>')
        for (x, y), r in zip(pts, series):
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" '
                         f'fill="{st["color"]}"><title>{html.escape(r.variant)}: '
                         f'VMAF {r.vmaf:.2f}</title></circle>')
        if pts:
            lx, ly = pts[-1]
            parts.append(f'<text class="series-label" x="{lx - 8:.1f}" y="{ly - 12:.1f}" '
                         f'text-anchor="end" fill="{st["color"]}">{st["label"]}</text>')

    parts.append("</svg>")
    return "\n".join(parts)


def tradeoff_svg(rows: list[Row], *, width: int = 760, height: int = 300) -> str:
    """Vreme enkodovanja naspram postignutog kvaliteta."""
    usable = [r for r in rows if r.seconds]
    if not usable:
        return ""
    pad_l, pad_r, pad_t, pad_b = 58, 20, 24, 46
    x0, x1 = pad_l, width - pad_r
    y0, y1 = height - pad_b, pad_t

    slo, shi = 0, max(r.seconds for r in usable) * 1.1
    vlo = max(0, min(r.vmaf for r in usable) - 8)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="Vreme enkodovanja naspram kvaliteta">']
    for v in range(int(vlo // 10 * 10), 101, 10):
        if v < vlo:
            continue
        y = _lin_scale(v, vlo, 100, y0, y1)
        parts.append(f'<line class="grid" x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}"/>')
        parts.append(f'<text class="axis" x="{x0 - 10}" y="{y + 4:.1f}" '
                     f'text-anchor="end">{v}</text>')
    for s in range(0, int(shi) + 1, max(5, int(shi // 6))):
        x = _lin_scale(s, slo, shi, x0, x1)
        parts.append(f'<text class="axis" x="{x:.1f}" y="{y0 + 20}" '
                     f'text-anchor="middle">{s}</text>')

    parts.append(f'<text class="axis-title" x="{(x0 + x1) / 2:.0f}" y="{height - 8}" '
                 f'text-anchor="middle">vreme enkodovanja (s)</text>')

    for r in usable:
        st = style_for(r.codec)
        x = _lin_scale(r.seconds, slo, shi, x0, x1)
        y = _lin_scale(r.vmaf, vlo, 100, y0, y1)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{st["color"]}" '
                     f'fill-opacity="0.85"><title>{html.escape(r.variant)}: '
                     f'{r.seconds:.1f}s, VMAF {r.vmaf:.2f}</title></circle>')
    parts.append("</svg>")
    return "\n".join(parts)


# --- HTML --------------------------------------------------------------

CSS = """
:root {
  --ink: #18212B;
  --ink-soft: #5A6472;
  --ground: #EDEFF2;
  --panel: #FFFFFF;
  --rule: #C9D0D8;
  --pass: #1F7A4D;
  --fail: #A32A2A;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--ground);
  color: var(--ink);
  font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  font-size: 16px;
  line-height: 1.55;
}
.wrap { max-width: 860px; margin: 0 auto; padding: 40px 24px 72px; }
header { border-bottom: 2px solid var(--ink); padding-bottom: 18px; margin-bottom: 32px; }
h1 { font-size: 30px; font-weight: 650; letter-spacing: -0.015em; margin: 0 0 6px; }
.meta { color: var(--ink-soft); font-size: 14px; margin: 0; }
.meta span + span::before { content: " / "; color: var(--rule); }
h2 { font-size: 19px; font-weight: 600; margin: 40px 0 6px; letter-spacing: -0.01em; }
h2:first-of-type { margin-top: 28px; }
.lede { color: var(--ink-soft); font-size: 14.5px; margin: 0 0 16px; max-width: 62ch; }
figure { margin: 0 0 8px; background: var(--panel); border: 1px solid var(--rule);
         border-radius: 3px; padding: 14px 10px 6px; }
svg { width: 100%; height: auto; display: block; }
.grid { stroke: var(--rule); stroke-width: 1; }
.grid.faint { stroke: var(--rule); stroke-opacity: 0.45; stroke-dasharray: 2 4; }
.axis { fill: var(--ink-soft); font-size: 11px;
        font-family: "SFMono-Regular", Consolas, monospace; }
.axis-title { fill: var(--ink-soft); font-size: 12px; }
.series-label { font-size: 13px; font-weight: 620; }
table { width: 100%; border-collapse: collapse; font-size: 14px;
        background: var(--panel); border: 1px solid var(--rule); }
caption { text-align: left; color: var(--ink-soft); font-size: 13.5px;
          padding: 0 0 8px; }
th, td { padding: 7px 10px; text-align: right; border-bottom: 1px solid var(--rule); }
th:first-child, td:first-child { text-align: left; }
thead th { font-weight: 600; font-size: 12.5px; color: var(--ink-soft);
           border-bottom: 1.5px solid var(--ink); }
tbody tr:last-child td { border-bottom: none; }
td.num { font-family: "SFMono-Regular", Consolas, monospace;
         font-variant-numeric: tabular-nums; }
.swatch { display: inline-block; width: 9px; height: 9px; border-radius: 2px;
          margin-right: 7px; vertical-align: baseline; }
.verdict { font-weight: 600; }
.verdict.ok { color: var(--pass); }
.verdict.no { color: var(--fail); }
.summary { display: flex; gap: 28px; flex-wrap: wrap; background: var(--panel);
           border: 1px solid var(--rule); border-radius: 3px; padding: 16px 20px;
           margin-bottom: 8px; }
.summary div { min-width: 118px; }
.summary dt { color: var(--ink-soft); font-size: 12.5px; margin: 0 0 2px; }
.summary dd { margin: 0; font-size: 22px; font-weight: 620;
              font-variant-numeric: tabular-nums; }
footer { margin-top: 44px; padding-top: 16px; border-top: 1px solid var(--rule);
         color: var(--ink-soft); font-size: 13px; }
footer p { margin: 0 0 6px; max-width: 68ch; }
@media (max-width: 620px) {
  .wrap { padding: 28px 16px 56px; }
  h1 { font-size: 24px; }
  table { font-size: 13px; }
  th, td { padding: 6px 7px; }
}
"""


def render(rows: list[Row], *, source: str, quality_path: Path) -> str:
    passed = sum(1 for r in rows if r.passed)
    best = max(rows, key=lambda r: r.vmaf) if rows else None
    fastest_at_90 = None
    at90 = [r for r in rows if r.vmaf >= 90 and r.seconds]
    if at90:
        fastest_at_90 = min(at90, key=lambda r: r.seconds)

    body = [f'<div class="wrap">']
    body.append("<header>")
    body.append("<h1>Kvalitet enkodiranja — rezultati merenja</h1>")
    body.append('<p class="meta">'
                f'<span>referenca: {html.escape(Path(source).name)}</span>'
                f'<span>{len(rows)} varijanti</span>'
                f'<span>{datetime.now():%d.%m.%Y. %H:%M}</span></p>')
    body.append("</header>")

    body.append('<div class="summary">')
    body.append(f'<div><dt>Prošlo pragove</dt><dd>{passed} / {len(rows)}</dd></div>')
    if best:
        body.append(f'<div><dt>Najviši VMAF</dt><dd>{best.vmaf:.1f}</dd></div>')
    if fastest_at_90:
        body.append(f'<div><dt>Najbrže do VMAF 90</dt>'
                    f'<dd>{fastest_at_90.seconds:.1f} s</dd></div>')
    body.append("</div>")

    body.append("<h2>Kvalitet po bitrate-u</h2>")
    body.append('<p class="lede">Logaritamska x-osa: svaki korak udvostručuje bitrate. '
                'Kodek čija kriva leži više daje isti kvalitet za manje bitova.</p>')
    body.append(f"<figure>{curve_svg(rows)}</figure>")

    trade = tradeoff_svg(rows)
    if trade:
        body.append("<h2>Cena kvaliteta u vremenu</h2>")
        body.append('<p class="lede">Tačke gore levo su najbolji izbor: visok kvalitet, '
                    'kratko enkodovanje. Ova osa se ne vidi u samoj krivi kvaliteta.</p>')
        body.append(f"<figure>{trade}</figure>")

    body.append("<h2>Sve varijante</h2>")
    body.append("<table>")
    body.append('<caption>Razmak je razlika proseka i 1. percentila — koliko prosek '
                'krije najgore scene. Manji je bolji.</caption>')
    body.append("<thead><tr><th>varijanta</th><th>kbps</th><th>VMAF</th>"
                "<th>p1</th><th>razmak</th><th>PSNR-Y</th><th>s</th>"
                "<th>ishod</th></tr></thead><tbody>")
    for r in rows:
        st = style_for(r.codec)
        psnr = f"{r.psnr:.2f}" if r.psnr is not None else "—"
        secs = f"{r.seconds:.1f}" if r.seconds is not None else "—"
        cls, mark = ("ok", "prošlo") if r.passed else ("no", "palo")
        body.append(
            f'<tr><td><span class="swatch" style="background:{st["color"]}"></span>'
            f'{html.escape(r.variant)}</td>'
            f'<td class="num">{r.bitrate:,.0f}</td>'
            f'<td class="num">{r.vmaf:.2f}</td>'
            f'<td class="num">{r.p1:.2f}</td>'
            f'<td class="num">{r.spread:.2f}</td>'
            f'<td class="num">{psnr}</td>'
            f'<td class="num">{secs}</td>'
            f'<td class="verdict {cls}">{mark}</td></tr>'
        )
    body.append("</tbody></table>")

    body.append("<footer>")
    body.append("<p>VMAF, PSNR i SSIM su izmereni u jednom prolazu kroz filter "
                "libvmaf. Pragovi dolaze iz thresholds.json; varijanta pada ako "
                "prosek ili 1. percentil padnu ispod svog praga.</p>")
    body.append("<p>Prag se namerno ne postavlja na najniži frejm — minimum je "
                "statistika jednog frejma i previše je nestabilan da bi bio kapija "
                "u CI-ju.</p>")
    body.append(f"<p>Izvor podataka: {html.escape(str(quality_path))}</p>")
    body.append("</footer>")
    body.append("</div>")

    return (
        "<!doctype html>\n"
        '<html lang="sr"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Kvalitet enkodiranja — rezultati merenja</title>\n"
        f"<style>{CSS}</style></head><body>\n"
        + "\n".join(body)
        + "\n</body></html>\n"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vqh.report", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--quality", type=Path, default=Path("out/quality.json"))
    p.add_argument("--thresholds", type=Path, default=Path("thresholds.json"))
    p.add_argument("--out", type=Path, default=Path("out/report.html"))
    p.add_argument("--source", default="referentni klip",
                   help="ime referentnog klipa za zaglavlje")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.quality.exists():
        print(f"Nema rezultata: {args.quality} — prvo pokreni vqh.quality")
        return 2

    rows = load_rows(args.quality, args.thresholds)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(rows, source=args.source, quality_path=args.quality),
                        encoding="utf-8")

    failed = [r.variant for r in rows if not r.passed]
    print(f"Izvestaj: {args.out}  ({len(rows)} varijanti, "
          f"{len(rows) - len(failed)} proslo)")
    for name in failed:
        print(f"  palo: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

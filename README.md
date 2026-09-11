# video-quality-harness

[![CI](https://github.com/KORISNIK/video-quality-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/KORISNIK/video-quality-harness/actions/workflows/ci.yml)

Automatizovani regresioni testovi kvaliteta video enkodiranja. Enkoduje referentni
klip u matricu varijanti (kodek × bitrate × preset × rezolucija), meri rezultat i
pada kad kvalitet padne ispod praga.

**Status:** Faza 1, koraci 1–4 gotovi — enkodovanje varijanti, merenje VMAF/PSNR/SSIM
i regresioni testovi sa pragovima. Sledeće: HTML izveštaj i CI.

## Instalacija

Potreban je `ffmpeg` sa `libx264`, `libx265` i `libsvtav1` enkoderima.
Za sledeći korak biće potreban i `libvmaf`.

```bash
ffmpeg -hide_banner -encoders | grep -E "libx264|libx265|libsvtav1"

python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Upotreba

```bash
# Napravi sintetički referentni klip (za probu, bez svog materijala)
python -c "from vqh import ffmpeg; ffmpeg.make_test_clip('clips/ref.mp4', seconds=10)"

# Enkoduj punu matricu
python -m vqh.encode clips/ref.mp4 --out out/

# Suzi matricu
python -m vqh.encode clips/ref.mp4 --codecs h264 av1 --bitrates 1000 2000

# Vidi ffmpeg komande bez izvršavanja
python -m vqh.encode clips/ref.mp4 --dry-run
```

Izlaz:

```
Izvor: clips/ref.mp4 — 1280x720 h264 3.0s 6403.7 kbps

[1/6] h264_src_500k_pveryfast ... 1.12s, 603.6 kbps (+20.7%)
[2/6] h264_src_2000k_pveryfast ... 1.18s, 2175.7 kbps (+8.8%)
[3/6] hevc_src_500k_pveryfast ... 3.52s, 535.5 kbps (+7.1%)
...
```

Uz fajlove se piše `out/manifest.json` — ulaz za merenje kvaliteta i artefakt za CI.

### Merenje kvaliteta

```bash
python -m vqh.quality clips/ref.mp4 --manifest out/manifest.json --threads 4

# Na slabijem CPU-u: meri svaki 5. frejm
python -m vqh.quality clips/ref.mp4 --subsample 5
```

```
varijanta                        kbps    VMAF      p1  PSNR-Y      s
--------------------------------------------------------------------
h264_src_300k_pveryfast           347   90.11   87.22   38.10    0.3
h264_src_1500k_pveryfast         1650   99.01   98.47   53.22    0.3
hevc_src_300k_pveryfast           322   90.85   86.85   37.97    1.1
hevc_src_1500k_pveryfast         1560   98.93   98.31   52.33    1.4
```

Rezultat ide u `out/quality.json`.

Zahteva ffmpeg build sa filterom `libvmaf`. Provera:

```bash
ffmpeg -hide_banner -filters | grep libvmaf
```

Ako se ispiše samo `vmafmotion`, to nije zamena — taj filter računa isključivo
komponentu pokreta, ne pun VMAF skor. Uzmi statički build sa
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases).

### Zašto ne samo prosečan VMAF

Prosek krije propade: klip koji je 95 kroz ceo materijal i 40 u jednoj sceni sa
brzim pokretom daje pristojan prosek, a gledaocu upada u oči baš ta scena. Zato se
prate i `vmaf_p1` (1. percentil) i harmonijska sredina, koja povremene propade
kažnjava jače od aritmetičke.

### HTML izveštaj

```bash
python -m vqh.report --quality out/quality.json --out out/report.html \
  --source park_joy_420_720p50.y4m
```

Jedan samostalan fajl: bez CDN-a, bez JavaScript-a, grafici su inline SVG. Otvara se
offline i preživljava kao CI artefakt. Sadrži krivu kvalitet/bitrate na logaritamskoj
osi, odnos vremena enkodovanja i kvaliteta, i tabelu sa ishodom po pragovima.

Izlazni kod je 0 i kad neka varijanta padne — izveštaj samo prikazuje stanje.
Kapija u CI-ju je `pytest`, ne ovaj korak.

### Zamka: korak odabiranja i struktura GOP-a

`--subsample N` ubrzava merenje, ali N ne sme da bude umnožak dužine mini-GOP-a.
Ako jeste, uzima se uvek isti položaj u nizu — po pravilu I i P frejmovi, nikad B —
pa rezultat ispada previsok.

Izmereno na x265 (`veryfast`, mini-GOP 5), odstupanje proseka VMAF od punog merenja:

```
korak   2      3      4      5      6      7      10     11
VMAF  -0.05  +0.06  -0.04  +1.54  +0.04  +0.09  +1.61  -0.04
```

Pogrešni su samo 5 i 10. Na pravom materijalu (Park Joy 720p50) greška je bila znatno
veća — do 6,9 poena, dovoljno da HEVC deluje mnogo bolji nego što jeste.

Poređenja radi, strukture su ove:

```
x264 veryfast:  I B B B P B B P ...   (mini-GOP 4)
x265 veryfast:  I B B B B P B B B B P ...  (mini-GOP 5)
```

Mehanizam je aliasing: korak odabiranja se poklopio sa periodom signala.

**Pravila:**

- Konačne brojeve i pragove izvodi iz punog merenja (`--subsample 1`).
- Za brzu iteraciju koristi prost korak — 7, 11, 13 — koji se teško poklapa sa
  bilo kojom strukturom.
- Frejmovi se pritom uparuju ispravno. Provereno: da poravnanje puca, svi koraci
  bi bili pomereni, a ne samo umnošci petice.

### Ponovljivost merenja

Broj niti menja bitstream. Isti ulaz, isti parametri, različit `-threads`:

```
threads=1: 944c9719...  816780 bajtova
threads=2: 030e0b4b...  824970 bajtova
threads=4: 36de095e...  826588 bajtova
```

Za isti broj niti x264 jeste ponovljiv — bar u testu na jednom jezgru. Ali kod
enkodovanja iste varijante u dva odvojena prolaza na 4-jezgarnom laptopu izmeren
je različit bitrate (16174,8 naspram 16170,2 kbps) i `vmaf_min` je odlutao za 10
poena, dok je prosek ostao stabilan u okviru 0,15. Uzrok te razlike još nije
utvrđen — otvoreno pitanje, ne zaključak.

Praktične posledice, nezavisno od uzroka:

- Za CI koristi `--deterministic` (jedna nit po enkoderu). Sporije, ali ponovljivo.
- Postavljaj prag na `vmaf_p1`, ne na `vmaf_min`. Minimum je statistika jednog
  frejma i previše je osetljiv da bi bio kapija u CI-ju.
- Zapiši broj niti uz rezultate ako porediš merenja sa različitih mašina.

```bash
python -m vqh.encode clips/ref.mp4 --deterministic --out out/
```

### Inkrementalni manifest

Manifest se podrazumevano **dopunjuje**: novi zapisi zamenjuju istoimene, ostali
ostaju. Tako možeš da doenkoduješ jedan kodek bez ponavljanja svega:

```bash
python -m vqh.encode clips/ref.mp4 --codecs h264 hevc --out out/
python -m vqh.encode clips/ref.mp4 --codecs av1 --out out/   # manifest sada ima sve tri
```

Za čist manifest od nule koristi `--no-merge`.

## Testovi

```bash
pytest
```

Testovi sami generišu referentni klip preko `testsrc2`, pa rade na čistom kloniranju
bez ijednog spoljnog fajla — bitno za CI.

## Struktura

```
src/vqh/ffmpeg.py       tanak wrapper oko ffmpeg/ffprobe (run, probe, make_test_clip)
src/vqh/variants.py     definicija kodeka i matrice — jedina tačka koju menjaš
src/vqh/encode.py       enkodovanje + CLI + manifest
src/vqh/quality.py      VMAF/PSNR/SSIM merenje + CLI
src/vqh/report.py       samostalan HTML izveštaj iz quality.json
thresholds.json         pragovi po varijanti — menjaju se bez diranja koda
tests/test_encode.py    smoke testovi enkodiranja
tests/test_quality.py   regresioni testovi kvaliteta
```

### Zašto ovakva podela

`variants.py` je jedino mesto gde se zna nešto o ffmpeg flagovima pojedinog kodeka.
Kad budeš dodavao hardversko ubrzanje (`h264_vaapi`, `hevc_nvenc`, `av1_qsv`), menjaš
samo mapu `CODECS` — ostatak koda ne zna razliku između softverskog i hardverskog
enkodera. To je i poenta: harness onda postaje alat za poređenje HW i SW enkodera
na istoj krivoj kvalitet/bitrate.

### Rate control po kodeku

Svaki kodek se drugačije ponaša na isti zahtev za bitrate-om. SVT-AV1 ne podržava CBR
u random-access modu i odbija `maxrate == bitrate`, pa mu se daje VBR prozor od 50%
(`maxrate_factor` u `CODECS`). Zato se u izlazu prati odstupanje od ciljnog bitrate-a —
to je već korisna metrika sama po sebi.

## CI

`.github/workflows/ci.yml` na svaki push: preuzima ffmpeg sa `libvmaf` (keširan),
pušta `pytest` kao kapiju, pa generiše izveštaj i kači ga kao artefakt.

Workflow eksplicitno proverava da build ima filter `libvmaf` i pada ako ga nema.
Bez te provere testovi kvaliteta bi se tiho preskočili i CI bi bio zelen a da
nijedna varijanta nije izmerena — najgora vrsta zelenog.

Enkodovanje u CI-ju ide sa `--deterministic`, da pragovi ne padaju nasumično.

### Dva skupa pragova

| fajl | materijal | svrha |
|---|---|---|
| `thresholds.ci.json` | generisan klip (`testsrc2`) | detekcija regresije, svaki push |
| `thresholds.json` | pravi materijal (Park Joy) | merenje performansi, ručno |

Razlika je bitna. Sintetički klip je znatno lakši za kodeke — na njemu H.264 daje
VMAF preko 90 pri 300 kbps, dok na Park Joy-u pri 500 kbps daje 16. Brojevi iz CI-ja
nisu ocena kodeka i ne treba ih tako čitati.

Podela je namerna: CI odgovara na pitanje „da li je izmena nešto pokvarila", a puna
merenja na pitanje „koliko bitova treba ovom kodeku za ovaj sadržaj". Drugo pitanje
zavisi od sadržaja i sintetika na njega ne može da odgovori.

Referentni klip se ne drži u repou: `git clone` ostaje brz, nema pitanja licence
oko tuđeg materijala, i CI ne zavisi od spoljnog servera.

## Pragovi

`thresholds.json` drži `default` blok i override po varijanti:

```json
{
  "default": { "vmaf_mean": 75.0, "vmaf_p1": 60.0, "bitrate_error_pct": 25.0 },
  "variants": {
    "h264_src_1500k_pveryfast": { "vmaf_mean": 96.0, "vmaf_p1": 94.0 }
  }
}
```

Postavi pragove tek pošto izmeriš stvarne vrednosti na svom referentnom klipu, i to
nekoliko poena ispod izmerenog. Prag koji nikad ne pada ne testira ništa.

## Sledeći koraci

1. Dockerfile + GitHub Actions, izveštaj kao artefakt
3. VAAPI enkoderi (`h264_vaapi`, `hevc_vaapi`) — poređenje HW i SW na istoj krivoj
4. Ponovljena merenja vremena enkodiranja zbog termalnog prigušivanja na laptopu

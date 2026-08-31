"""
Sesli mesaj -> metin (yerel konusma tanima).

NEDEN YEREL
-----------
Claude API ses girdisi KABUL ETMIYOR — goruntu ve PDF okunabiliyor ama audio
okunamiyor. Dolayisiyla sesli mesaj icin ayri bir konusma-metin katmani sart.

Neden whisper.cpp (Python paketi degil):
  * faster-whisper -> bagimliligi `av` (PyAV) Python 3.14 icin wheel sunmuyor,
    kaynaktan derlemesi Cython hatasiyla patliyor
  * mlx-whisper -> yalnizca Apple Silicon; bu makine Intel (x86_64)
  * openai-whisper -> torch cekiyor, ayni wheel sorunu riski
whisper.cpp native bir ikili; Python bagimliligi yok, bu sorunlarin hicbirine
takilmiyor. Ayrica tamamen yerel calisiyor: ses hicbir servise gitmiyor ve
ek API maliyeti yok.

KURULUM
-------
    brew install whisper-cpp
    curl -L -o data/models/ggml-small.bin \\
      https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class VoiceError(RuntimeError):
    pass


class VoiceTranscriber:
    def __init__(self, settings, model_path: str | Path | None = None):
        """
        `model_path` verilirse `voice.model_path` yerine O kullanilir.

        NEDEN OVERRIDE VAR: sesli mesaj ile video transkripti ayni isi
        yapmiyor. Sesli mesaj KISA ve kullanici BEKLIYOR — hiz onemli.
        Video transkripti uzun ve arkada calisiyor — DOGRULUK onemli.
        2026-08-31'de olculdu (113,8 sn Turkce finans sesi):

            small           0,35x gercek zaman  ->  BIR BOLUMU DUSURDU
            large-v3-turbo  0,84x gercek zaman  ->  tam
            medium          1,33x gercek zaman  ->  tam ama turbo'dan yavas

        Tek bir ayar olsaydi ya sesli mesaj yavaslardi ya video eksik
        okunurdu. Cagiran taraf secsin.
        """
        self.s = settings
        self.model_path = settings._resolve(
            model_path or settings.get("voice.model_path",
                                       "data/models/ggml-small.bin"))
        self.dil = settings.get("voice.language", "tr")
        self.binary = settings.get("voice.binary", "whisper-cli")

    # ------------------------------------------------------------------
    def hazir(self) -> tuple[bool, str]:
        """(kullanilabilir_mi, aciklama) — kurulum eksikse NE yapilacagini soyler."""
        if not self.s.get("voice.enabled", True):
            return False, "Sesli mesaj kapali (config: voice.enabled)."
        if not shutil.which(self.binary):
            return False, (f"'{self.binary}' bulunamadi.\n"
                           "Kurulum: brew install whisper-cpp")
        if not shutil.which("ffmpeg"):
            return False, ("ffmpeg bulunamadi (Telegram sesi OGG/Opus gonderiyor, "
                           "cozmek icin gerekli).\nKurulum: brew install ffmpeg")
        if not self.model_path.exists():
            # EKSIK OLAN MODELIN adini soyluyoruz. Sabit "ggml-small.bin"
            # yaziyordu; model yolu override edilebilir hale gelince bu
            # YANLIS TALIMAT oldu — turbo eksikken small indirtiyordu.
            ad = self.model_path.name
            return False, (f"Model dosyasi yok: {self.model_path}\n"
                           f"Indir: curl -L -o {self.model_path} \\\n"
                           "  https://huggingface.co/ggerganov/whisper.cpp/"
                           f"resolve/main/{ad}")
        return True, "hazir"

    # ------------------------------------------------------------------
    def cevir(self, ses_yolu: Path, timeout: int = 300) -> str:
        ok, sebep = self.hazir()
        if not ok:
            raise VoiceError(sebep)

        ses_yolu = Path(ses_yolu)
        if not ses_yolu.exists():
            raise VoiceError(f"ses dosyasi bulunamadi: {ses_yolu}")

        with tempfile.TemporaryDirectory() as gecici:
            wav = Path(gecici) / "ses16k.wav"
            # whisper.cpp 16 kHz mono PCM bekler; Telegram OGG/Opus gonderir.
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", str(ses_yolu),
                     "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
                    check=True, capture_output=True, timeout=120)
            except subprocess.CalledProcessError as e:
                raise VoiceError(
                    f"ses cozulemedi: {e.stderr.decode('utf-8', 'replace')[:200]}") from e
            except subprocess.TimeoutExpired as e:
                raise VoiceError("ses donusumu zaman asimina ugradi") from e

            try:
                r = subprocess.run(
                    [self.binary, "-m", str(self.model_path), "-l", self.dil,
                     "-nt",          # zaman damgasi yok, duz metin
                     "-np",          # ilerleme ciktisi yok
                     "-f", str(wav)],
                    check=True, capture_output=True, timeout=timeout)
            except subprocess.CalledProcessError as e:
                raise VoiceError(
                    f"transkripsiyon basarisiz: "
                    f"{e.stderr.decode('utf-8', 'replace')[:200]}") from e
            except subprocess.TimeoutExpired as e:
                raise VoiceError(
                    f"transkripsiyon {timeout} sn icinde bitmedi "
                    "(daha kisa konus veya daha kucuk model kullan)") from e

        metin = r.stdout.decode("utf-8", "replace").strip()
        # whisper.cpp bazen "[BLANK_AUDIO]" / "(sessizlik)" gibi isaretler basar
        if not metin or metin.startswith("[") and metin.endswith("]"):
            raise VoiceError("Seste anlasilir bir konusma bulamadim.")
        return metin

"""
Proaktif katman — sistem sorulmadan calisir.

    1. tarayici (screener)  deterministik, LLM YOK  -> signals
    2. panel    (agents)    4 ajan PARALEL, salt-okunur
    3. hakem    (agents)    celiskileri one cikarir
    4. defter   (journal)   tahminleri KAYDEDER ve PUANLAR

Dorduncusu en onemlisi: olculdu, gunluk al-satta %50 isabet ayda -%4.2,
%55 isabet +%5.6 getiriyor. Her sey bu oranin ne oldugunda dugumleniyor
ve VARSAYILAMAZ. Kendi isabetini olcmeyen tavsiye sistemi, sonradan
yalnizca tutan tahminleri hatirlar.
"""
from .journal import Defter
from .runner import Nabiz
from .screener import Tarayici

__all__ = ["Tarayici", "Panel", "Defter", "Nabiz"]


def __getattr__(ad):
    # Panel LLM SDK'sini import ediyor; tembel yukleniyor ki tarayici
    # ve defter SDK olmadan da kullanilabilsin.
    if ad == "Panel":
        from .agents import Panel
        return Panel
    raise AttributeError(ad)

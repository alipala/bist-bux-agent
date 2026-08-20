"""
Arsiv arama katmani.

Uc parca, uc ayri soru:

  normalize.py  Turkce metni LEKSIK karsilastirmaya hazirlar
  gomme.py      metni ANLAM uzayina tasir (Ollama / embeddinggemma)
  hibrit.py     iki siralamayi tek siralamada birlestirir (RRF)

Hicbiri veritabanini tanimaz; sorgu ve metin alir, siralama dondurur.
Depolama `storage/db.py`'de, cagri `bot/tools.py`'de.
"""

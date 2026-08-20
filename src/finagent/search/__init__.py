"""
Arsiv arama katmani.

Dort parca, dort ayri soru:

  normalize.py  Turkce metni LEKSIK karsilastirmaya hazirlar
  gomme.py      metni ANLAM uzayina tasir (Ollama / embeddinggemma)
  hibrit.py     iki siralamayi tek siralamada birlestirir (RRF)
  indeks.py     gomme ile veritabani arasindaki TEK kopru

Ilk uc modul veritabanini TANIMAZ; metin ve siralama ile calisirlar.
`indeks.py` iki tarafi da tanir ve bilerek ayri tutuldu: bu bilgi bir
uca sizarsa, o uc digerini test etmeden degistirilemez hale gelir.
Depolama `storage/db.py`'de, cagri `bot/tools.py`'de.
"""

# IBKR strateji evreni — ölçüm

**Üretildi:** 2026-08-27 · `scripts/evren_belgesi.py`  
**Kaynak belge:** `docs/finagent-strateji-motoru.md` (Adım 1)  
**Evren tanımı:** `ibkr.strateji.endeksler` = S&P 500, Nasdaq 100

Bu dosya ÜRETİLİR, elle yazılmaz. Her sayı veritabanından okundu; seri bulunamayan sembollerin sebebi Yahoo'ya tek tek SORULDU (tahmin edilmedi).

## Özet

| Ölçüt | Sayı | Oran |
|---|---|---|
| Endeks üyesi (tekil) | 518 | %100.0 |
| Fiyat serisi var | 511 | %98.6 |
| ≥1500 bar (backtest derinliği) | 486 | %93.8 |
| USD kote | 510 | %98.5 |
| USD kote + devir ≥ 1,000,000 | 510 | %98.5 |
| conid çözülmüş | 482 | %93.1 |

## Serisi olmayan 7 sembol — sebebiyle

| Sembol | Katalog adı | Sebep |
|---|---|---|
| `BEN` | Franklin Resources | ad eslesmedi (bizde 'Franklin Resources', Yahoo short='Franklin Templeton, Inc.' long='Franklin Templeton Inc.') |
| `BNY` | BNY Mellon | ad eslesmedi (bizde 'BNY Mellon', Yahoo short='The Bank of New York Mellon Cor' long='The Bank of New York Mellon Corporation') |
| `DECK` | Deckers Brands | ad eslesmedi (bizde 'Deckers Brands', Yahoo short='Deckers Outdoor Corporation' long='Deckers Outdoor Corporation') |
| `IBM` | IBM | ad eslesmedi (bizde 'IBM', Yahoo short='International Business Machines' long='International Business Machines Corporation') |
| `SLB` | Schlumberger | ad eslesmedi (bizde 'Schlumberger', Yahoo short='SLB Limited' long='SLB N.V.') |
| `SMCI` | Supermicro | ad eslesmedi (bizde 'Supermicro', Yahoo short='Super Micro Computer, Inc.' long='Super Micro Computer, Inc.') |
| `WAB` | Wabtec | ad eslesmedi (bizde 'Wabtec', Yahoo short='Westinghouse Air Brake Technolo' long='Westinghouse Air Brake Technologies Corporation') |

## 511 sembol — tam tablo

`devir` = son 20 barın (kapanış × hacim) medyanı, kotasyon para biriminde. `bar`/`ilk` `db.fiyat_serisi()`'nden — yani kaynak seçimi uygulanmış TEK seriden.

| Sembol | Ad | Endeks | Bar | İlk tarih | Son | Kur | Kaynak | Devir (medyan) | conid |
|---|---|---|---|---|---|---|---|---|---|
| `A` | Agilent Technologies | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 232,083,817 | 1715006 |
| `AAPL` | Apple Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 12,670,834,255 | 265598 |
| `ABBV` | AbbVie | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,142,693,849 | 118089500 |
| `ABNB` | Airbnb, Inc. | Nas/S&P | 1434 | 2020-12-10 | 2026-08-27 | USD | yahoo | 827,310,850 | 459530964 |
| `ABT` | Abbott Laboratories | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 793,760,736 | 4065 |
| `ACGL` | Arch Capital Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 143,037,974 | 10763362 |
| `ACN` | Accenture | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 908,776,253 | 67889930 |
| `ADBE` | Adobe Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,095,400,018 | 265768 |
| `ADI` | Analog Devices, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,255,161,383 | 4157 |
| `ADM` | Archer Daniels Midland | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 286,594,072 | 4165 |
| `ADP` | Automatic Data Processing, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 595,421,798 | 4661 |
| `ADSK` | Autodesk, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 419,252,931 | 265681 |
| `AEE` | Ameren | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 157,579,611 | 13181 |
| `AEP` | American Electric Power Company, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 406,777,388 | 4211 |
| `AES` | AES Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 103,536,516 | 2560358 |
| `AFL` | Aflac | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 237,154,334 | 4227 |
| `AIG` | American International Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 220,224,339 | 61319701 |
| `AIZ` | Assurant | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 106,865,522 | 27727424 |
| `AJG` | Arthur J. Gallagher & Co. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 336,099,769 | 4325 |
| `AKAM` | Akamai Technologies | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 370,618,371 | 6220356 |
| `ALAB` | Astera Labs, Inc. | Nas | 612 | 2024-03-20 | 2026-08-27 | USD | yahoo | 1,132,243,551 | 692196414 |
| `ALB` | Albemarle Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 275,928,130 | 4347 |
| `ALGN` | Align Technology | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 131,262,288 | 11459264 |
| `ALL` | Allstate | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 391,492,932 | 4357 |
| `ALLE` | Allegion | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 165,196,746 | 138438437 |
| `ALNY` | Alnylam Pharmaceuticals, Inc. | Nas | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 379,461,687 | 29442968 |
| `AMAT` | Applied Materials, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 3,471,093,991 | 266093 |
| `AMCR` | Amcor | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 161,971,002 | 845305949 |
| `AMD` | Advanced Micro Devices, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 9,167,521,880 | 4391 |
| `AME` | Ametek | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 316,678,938 | 4392 |
| `AMGN` | Amgen Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,032,095,959 | 266145 |
| `AMP` | Ameriprise Financial | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 210,888,787 | 35970258 |
| `AMT` | American Tower | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 314,533,196 | 99831145 |
| `AMZN` | Amazon.com, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 8,645,268,039 | 3691937 |
| `ANET` | Arista Networks | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,427,619,405 | 740948854 |
| `AON` | Aon plc | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 251,226,584 | 414366114 |
| `AOS` | A. O. Smith | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 77,873,383 | 12225 |
| `APA` | APA Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 235,740,014 | 474515500 |
| `APD` | Air Products | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 257,807,603 | 4487 |
| `APH` | Amphenol | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 939,418,412 | 4493 |
| `APO` | Apollo Global Management | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 498,026,211 | 535288388 |
| `APP` | AppLovin Corporation | Nas/S&P | 1349 | 2021-04-15 | 2026-08-27 | USD | yahoo | 1,864,527,436 | 481863646 |
| `APTV` | Aptiv | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 191,054,059 | 748351585 |
| `ARE` | Alexandria Real Estate Equities | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 103,823,493 | — |
| `ARES` | Ares Management | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 289,633,376 | 341904548 |
| `ARM` | Arm Holdings plc | Nas | 741 | 2023-09-14 | 2026-08-27 | USD | yahoo | 1,008,774,478 | 653400472 |
| `ASML` | ASML Holding N.V. | Nas | 2559 | 2016-08-29 | 2026-08-27 | EUR | yahoo_borsa | 813,594,291 | 117902840 |
| `ATO` | Atmos Energy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 170,832,179 | 4644 |
| `AVB` | AvalonBay Communities | S&P | 27 | 2026-07-17 | 2026-08-24 | USD | yahoo | 159,647,571 | 5026974 |
| `AVGO` | Broadcom Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 6,771,864,593 | 313130367 |
| `AVY` | Avery Dennison | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 126,547,277 | 4694 |
| `AWK` | American Water Works | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 265,149,362 | 50530752 |
| `AXON` | Axon Enterprise, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 509,175,186 | 272104927 |
| `AXP` | American Express | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 769,122,573 | 4721 |
| `AZO` | AutoZone | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 658,617,043 | 4750 |
| `BA` | Boeing | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,099,445,304 | 4762 |
| `BAC` | Bank of America | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,656,686,363 | 10098 |
| `BALL` | Ball Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 105,968,538 | 5052 |
| `BAX` | Baxter International | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 177,520,177 | 8111188 |
| `BBY` | Best Buy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 265,607,770 | 4817 |
| `BDX` | Becton Dickinson | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 305,631,717 | 4886 |
| `BF.B` | Brown–Forman | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 52,671,784 | — |
| `BG` | Bunge Global | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 132,156,292 | 663174498 |
| `BIIB` | Biogen | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 169,060,921 | 26734397 |
| `BKNG` | Booking Holdings Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 973,174,381 | 308728373 |
| `BKR` | Baker Hughes Company | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 356,785,393 | 281316445 |
| `BLDR` | Builders FirstSource | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 125,521,922 | 34851874 |
| `BLK` | BlackRock | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 636,730,004 | 729193719 |
| `BMY` | Bristol Myers Squibb | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 604,339,624 | 5111 |
| `BR` | Broadridge Financial Solutions | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 227,370,707 | — |
| `BRK.B` | Berkshire Hathaway | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 2,008,056,203 | — |
| `BRO` | Brown & Brown | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 126,008,940 | 6607613 |
| `BSX` | Boston Scientific | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 904,534,039 | 5270 |
| `BX` | Blackstone Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 528,621,366 | 372834689 |
| `BXP` | BXP, Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 78,805,985 | 5021340 |
| `C` | Citigroup | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,134,231,447 | 87335484 |
| `CAH` | Cardinal Health | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 410,462,138 | 5407 |
| `CARR` | Carrier Global | S&P | 1619 | 2020-03-19 | 2026-08-27 | USD | yahoo | 320,291,694 | 410755397 |
| `CASY` | Casey's | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 224,418,317 | 267265 |
| `CAT` | Caterpillar Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 2,057,526,451 | 5437 |
| `CB` | Chubb Limited | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 528,201,505 | 218939694 |
| `CBOE` | Cboe Global Markets | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 240,349,219 | 76227954 |
| `CBRE` | CBRE Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 196,879,474 | 95514904 |
| `CCEP` | Coca-Cola Europacific Partners PLC | Nas | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 179,403,500 | — |
| `CCI` | Crown Castle | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 171,603,554 | 176571182 |
| `CCL` | Carnival Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 428,300,241 | 878372298 |
| `CDNS` | Cadence Design Systems, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 656,214,926 | — |
| `CDW` | CDW Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 209,993,897 | 130432552 |
| `CEG` | Constellation Energy Corporation | Nas/S&P | 1156 | 2022-01-19 | 2026-08-27 | USD | yahoo | 652,221,797 | 538132976 |
| `CF` | CF Industries | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 276,052,221 | 35447205 |
| `CFG` | Citizens Financial Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 195,165,348 | 167955370 |
| `CHD` | Church & Dwight | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 180,991,264 | 5651 |
| `CHRW` | C.H. Robinson | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 201,695,308 | 36320731 |
| `CHTR` | Charter Communications | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 306,634,363 | 233674866 |
| `CI` | Cigna | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 442,194,966 | 346403540 |
| `CIEN` | Ciena | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 716,522,204 | 41045553 |
| `CINF` | Cincinnati Financial | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 73,644,279 | 267661 |
| `CL` | Colgate-Palmolive | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 358,385,700 | 5749 |
| `CLX` | Clorox | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 239,394,488 | 5794 |
| `CMCSA` | Comcast Corporation | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 548,113,296 | 267748 |
| `CME` | CME Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 447,070,193 | 45127739 |
| `CMG` | Chipotle Mexican Grill | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 567,131,794 | 37655664 |
| `CMI` | Cummins | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 511,932,965 | 6207 |
| `CMS` | CMS Energy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 270,726,948 | 5840 |
| `CNC` | Centene Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 230,557,742 | 13878278 |
| `CNP` | CenterPoint Energy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 232,000,904 | 8093 |
| `COF` | Capital One | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 635,275,977 | 5941 |
| `COHR` | Coherent Corp. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 2,075,601,128 | 584127832 |
| `COIN` | Coinbase | S&P | 1350 | 2021-04-14 | 2026-08-27 | USD | yahoo | 1,117,143,474 | 481691285 |
| `COO` | Cooper Companies (The) | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 117,189,250 | — |
| `COP` | ConocoPhillips | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 828,806,310 | 10885 |
| `COR` | Cencora | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 414,856,944 | 1714996 |
| `COST` | Costco Wholesale Corporation | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,582,944,260 | 272997 |
| `CPAY` | Corpay | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 210,377,184 | 691994611 |
| `CPRT` | Copart, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 279,653,067 | 4728523 |
| `CPT` | Camden Property Trust | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 83,596,529 | 6004 |
| `CRH` | CRH plc | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 416,183,735 | 655276609 |
| `CRL` | Charles River Laboratories | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 265,691,764 | 2730877 |
| `CRM` | Salesforce | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 2,282,432,335 | 29624264 |
| `CRWD` | CrowdStrike Holdings, Inc. | Nas/S&P | 1813 | 2019-06-12 | 2026-08-27 | USD | yahoo | 1,535,894,021 | 370757467 |
| `CRWV` | CoreWeave, Inc. | Nas | 356 | 2025-03-28 | 2026-08-27 | USD | yahoo | 1,922,520,448 | 771759702 |
| `CSCO` | Cisco Systems, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 2,048,080,402 | 268084 |
| `CSGP` | CoStar Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 176,885,427 | 6726677 |
| `CSX` | CSX Corporation | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 467,027,957 | 6150 |
| `CTAS` | Cintas Corporation | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 366,478,778 | 268149 |
| `CTSH` | Cognizant | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 437,771,256 | 4728759 |
| `CTVA` | Corteva | S&P | 1825 | 2019-05-24 | 2026-08-27 | USD | yahoo | 324,977,087 | 366239371 |
| `CVNA` | Carvana | S&P | 2346 | 2017-04-28 | 2026-08-27 | USD | yahoo | 623,441,791 | 274144952 |
| `CVS` | CVS Health | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 679,130,537 | 2585769 |
| `CVX` | Chevron Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,560,979,238 | 5684 |
| `D` | Dominion Energy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 197,844,805 | 6327 |
| `DAL` | Delta Air Lines | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 407,899,735 | 44001820 |
| `DASH` | DoorDash, Inc. | Nas/S&P | 1435 | 2020-12-09 | 2026-08-27 | USD | yahoo | 928,719,647 | 459309417 |
| `DD` | DuPont | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 181,307,798 | 887235923 |
| `DDOG` | Datadog, Inc. | Nas/S&P | 1744 | 2019-09-19 | 2026-08-27 | USD | yahoo | 1,060,652,314 | 383858515 |
| `DE` | Deere & Company | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 609,898,584 | 6393 |
| `DELL` | Dell Technologies | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 2,199,100,328 | 346218218 |
| `DG` | Dollar General | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 239,418,497 | 70212228 |
| `DGX` | Quest Diagnostics | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 184,029,463 | 2459465 |
| `DHI` | D. R. Horton | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 281,530,655 | — |
| `DHR` | Danaher Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 593,513,350 | 6442 |
| `DIS` | Walt Disney Company (The) | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 867,035,614 | 6459 |
| `DLR` | Digital Realty | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 353,839,277 | 31832526 |
| `DLTR` | Dollar Tree | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 248,522,651 | 49388062 |
| `DOC` | Healthpeak Properties | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 92,879,888 | 389700396 |
| `DOV` | Dover Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 183,772,790 | 6517 |
| `DOW` | Dow Inc. | S&P | 1871 | 2019-03-20 | 2026-08-27 | USD | yahoo | 261,929,454 | 356576040 |
| `DPZ` | Domino's | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 243,450,927 | 29831612 |
| `DRI` | Darden Restaurants | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 213,834,207 | 661440 |
| `DTE` | DTE Energy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 176,249,701 | 6585 |
| `DUK` | Duke Energy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 524,599,381 | 110176011 |
| `DVA` | DaVita | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 113,702,235 | 1035459 |
| `DVN` | Devon Energy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 420,585,274 | 754442 |
| `DXCM` | DexCom, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 417,023,627 | 33837089 |
| `EBAY` | eBay Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 441,690,377 | 4347086 |
| `ECHO` | EchoStar | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 273,064,116 | 47965865 |
| `ECL` | Ecolab | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 394,153,904 | 6682 |
| `ED` | Consolidated Edison | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 240,169,203 | 6691 |
| `EFX` | Equifax | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 197,240,220 | 6735 |
| `EG` | Everest Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 94,654,232 | 6477725 |
| `EIX` | Edison International | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 246,747,134 | — |
| `EL` | Estée Lauder Companies (The) | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 268,634,915 | — |
| `ELV` | Elevance Health | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 352,816,416 | 173962302 |
| `EME` | Emcor | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 279,458,813 | 6820 |
| `EMR` | Emerson Electric | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 409,251,171 | 6842 |
| `EOG` | EOG Resources | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 389,349,426 | 6890 |
| `EQIX` | Equinix | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 591,610,630 | 181764593 |
| `EQR` | Equity Residential | S&P | 15 | 2026-07-17 | 2026-08-21 | USD | yahoo | 195,920,803 | — |
| `EQT` | EQT Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 319,822,523 | 57698865 |
| `ERIE` | Erie Indemnity | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 58,195,157 | 4730254 |
| `ES` | Eversource Energy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 138,338,349 | 182880167 |
| `ESS` | Essex Property Trust | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 89,323,102 | 6946 |
| `ETN` | Eaton Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 747,295,983 | 118017869 |
| `ETR` | Entergy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 217,489,620 | 6969 |
| `EVRG` | Evergy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 151,504,035 | 320106069 |
| `EW` | Edwards Lifesciences | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 252,692,880 | 8101142 |
| `EXC` | Exelon Corporation | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 246,234,166 | 11000 |
| `EXE` | Expand Energy | S&P | 1393 | 2021-02-10 | 2026-08-27 | USD | yahoo | 231,731,356 | 470458975 |
| `EXPD` | Expeditors International | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 148,148,716 | — |
| `EXPE` | Expedia Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 440,886,020 | 98542021 |
| `EXR` | Extra Space Storage | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 118,317,001 | 30351205 |
| `F` | Ford Motor Company | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 562,864,266 | 9599491 |
| `FANG` | Diamondback Energy, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 316,606,368 | 115441080 |
| `FAST` | Fastenal Company | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 353,641,554 | 269120 |
| `FCX` | Freeport-McMoRan | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 827,625,178 | 7089 |
| `FDS` | FactSet | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 199,017,600 | 1847145 |
| `FDX` | FedEx | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 432,733,645 | 5100583 |
| `FDXF` | FedEx Freight | S&P | 65 | 2026-05-27 | 2026-08-27 | USD | yahoo | 147,653,667 | 884688186 |
| `FE` | FirstEnergy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 169,714,242 | 3265376 |
| `FER` | Ferrovial N.V. | Nas | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 74,383,022 | 702304865 |
| `FERG` | Ferguson Enterprises | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 409,103,908 | 717988170 |
| `FFIV` | F5, Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 221,256,684 | 6248713 |
| `FICO` | Fair Isaac | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 342,516,941 | 269280 |
| `FIS` | Fidelity National Information Services | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 208,267,357 | — |
| `FISV` | Fiserv | S&P | 2512 | 2016-08-29 | 2026-08-27 | USD | yahoo | 346,652,366 | 269315 |
| `FITB` | Fifth Third Bancorp | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 271,484,505 | 269318 |
| `FIX` | Comfort Systems USA | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 666,049,529 | 6607708 |
| `FLEX` | Flex Ltd. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 351,973,861 | 4596077 |
| `FOX` | Fox Corporation (Class B) | S&P | 1876 | 2019-03-13 | 2026-08-27 | USD | yahoo | 50,217,017 | 356858007 |
| `FOXA` | Fox Corporation (Class A) | S&P | 1877 | 2019-03-12 | 2026-08-27 | USD | yahoo | 345,126,365 | 356858008 |
| `FRT` | Federal Realty Investment Trust | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 74,714,527 | — |
| `FSLR` | First Solar | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 444,361,067 | 41622169 |
| `FTNT` | Fortinet, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 656,812,961 | 70236214 |
| `FTV` | Fortive | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 118,705,242 | 236074120 |
| `GD` | General Dynamics | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 310,977,999 | 7496 |
| `GDDY` | GoDaddy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 169,447,237 | 188986978 |
| `GE` | GE Aerospace | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,134,262,526 | — |
| `GEHC` | GE HealthCare Technologies Inc. | Nas/S&P | 927 | 2022-12-15 | 2026-08-27 | USD | yahoo | 190,779,875 | — |
| `GEN` | Gen Digital | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 148,215,810 | 390332324 |
| `GEV` | GE Vernova | S&P | 607 | 2024-03-27 | 2026-08-27 | USD | yahoo | 1,833,891,255 | 691984365 |
| `GILD` | Gilead Sciences, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 838,260,983 | 269753 |
| `GIS` | General Mills | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 312,587,105 | 7616 |
| `GL` | Globe Life | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 90,035,451 | 375868349 |
| `GLW` | Corning Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,553,620,432 | 7655 |
| `GM` | General Motors | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 417,800,233 | 80986742 |
| `GNRC` | Generac | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 181,107,922 | 72529783 |
| `GOOG` | Alphabet Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 5,204,478,097 | 208813720 |
| `GOOGL` | Alphabet Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 7,100,491,032 | 208813719 |
| `GPC` | Genuine Parts Company | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 146,817,966 | 7745 |
| `GPN` | Global Payments | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 271,503,805 | 11477774 |
| `GRMN` | Garmin | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 227,333,795 | 76791198 |
| `GS` | Goldman Sachs | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,771,186,953 | 4627828 |
| `GWW` | W. W. Grainger | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 282,311,695 | — |
| `HAL` | Halliburton | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 315,186,374 | 7890 |
| `HAS` | Hasbro | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 139,756,928 | 754956 |
| `HBAN` | Huntington Bancshares | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 349,573,668 | 270018 |
| `HCA` | HCA Healthcare | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 461,178,904 | 85076790 |
| `HD` | Home Depot (The) | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,130,886,216 | 7930 |
| `HIG` | Hartford (The) | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 165,299,979 | 1217009 |
| `HII` | Huntington Ingalls Industries | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 132,144,307 | — |
| `HLT` | Hilton Worldwide | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 639,924,822 | 257944570 |
| `HON` | Honeywell International Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 612,471,242 | 892744244 |
| `HONA` | Honeywell Aerospace Inc. | Nas/S&P | 52 | 2026-06-15 | 2026-08-27 | USD | yahoo | 639,843,529 | 891050670 |
| `HOOD` | Robinhood Markets | S&P | 1276 | 2021-07-29 | 2026-08-27 | USD | yahoo | 1,430,408,469 | 504546674 |
| `HPE` | Hewlett Packard Enterprise | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 786,730,211 | 209411798 |
| `HPQ` | HP Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 365,249,256 | 209411801 |
| `HRL` | Hormel Foods | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 82,339,647 | 8153 |
| `HSIC` | Henry Schein | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 93,152,390 | 3655640 |
| `HST` | Host Hotels & Resorts | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 231,911,780 | 8057 |
| `HSY` | Hershey Company (The) | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 201,968,508 | 8199 |
| `HUBB` | Hubbell Incorporated | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 207,891,092 | 216386790 |
| `HUM` | Humana | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 438,498,234 | 8251 |
| `HWM` | Howmet Aerospace | S&P | 2468 | 2016-11-01 | 2026-08-27 | USD | yahoo | 560,067,508 | 403121976 |
| `IBKR` | Interactive Brokers | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 392,524,452 | 43645865 |
| `ICE` | Intercontinental Exchange | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 567,597,054 | 136370275 |
| `IDXX` | IDEXX Laboratories, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 304,389,735 | 270413 |
| `IEX` | IDEX Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 115,844,468 | 8377 |
| `IFF` | International Flavors & Fragrances | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 126,103,742 | — |
| `INCY` | Incyte | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 158,975,744 | 4369855 |
| `INTC` | Intel Corporation | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 8,982,305,021 | 270639 |
| `INTU` | Intuit Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,173,173,456 | 270662 |
| `INVH` | Invitation Homes | S&P | 2406 | 2017-02-01 | 2026-08-27 | USD | yahoo | 91,162,388 | 264405214 |
| `IP` | International Paper | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 144,622,181 | 8511 |
| `IQV` | IQVIA | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 292,025,143 | 295757261 |
| `IR` | Ingersoll Rand | S&P | 2336 | 2017-05-12 | 2026-08-27 | USD | yahoo | 302,243,950 | 404509190 |
| `IRM` | Iron Mountain | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 175,194,388 | 180350774 |
| `ISRG` | Intuitive Surgical, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 933,498,047 | 9063285 |
| `IT` | Gartner | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 252,423,642 | 4457287 |
| `ITW` | Illinois Tool Works | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 267,900,276 | 8620 |
| `IVZ` | Invesco | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 132,329,611 | 47550606 |
| `J` | Jacobs Solutions | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 132,623,528 | 582332987 |
| `JBHT` | J.B. Hunt | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 198,148,320 | — |
| `JBL` | Jabil | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 331,465,752 | 4457282 |
| `JCI` | Johnson Controls | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 444,198,252 | 244164913 |
| `JKHY` | Jack Henry & Associates | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 149,539,201 | 270869 |
| `JNJ` | Johnson & Johnson | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,682,651,156 | 8719 |
| `JPM` | JPMorgan Chase | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,911,794,261 | 1520593 |
| `KDP` | Keurig Dr Pepper Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 369,472,367 | 324057162 |
| `KEY` | KeyCorp | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 189,007,106 | 8809 |
| `KEYS` | Keysight Technologies | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 327,046,333 | 170167160 |
| `KHC` | The Kraft Heinz Company | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 333,752,980 | 199169586 |
| `KIM` | Kimco Realty | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 82,583,501 | 8845 |
| `KKR` | KKR & Co. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 551,497,974 | 321328198 |
| `KLAC` | KLA Corporation | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,804,239,439 | 270957 |
| `KMB` | Kimberly-Clark | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 305,236,409 | 8864 |
| `KMI` | Kinder Morgan | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 311,943,439 | 83975037 |
| `KO` | Coca-Cola Company (The) | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,240,449,380 | 8894 |
| `KR` | Kroger | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 336,845,528 | 8908 |
| `KVUE` | Kenvue | S&P | 832 | 2023-05-04 | 2026-08-27 | USD | yahoo | 416,171,056 | 629043863 |
| `L` | Loews Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 66,134,566 | 9252 |
| `LDOS` | Leidos | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 188,546,891 | 134280621 |
| `LEN` | Lennar | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 177,861,473 | 9091 |
| `LH` | Labcorp | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 172,129,721 | 703660032 |
| `LHX` | L3Harris | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 390,498,327 | 371886040 |
| `LII` | Lennox International | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 215,879,677 | 6608113 |
| `LIN` | Linde plc | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,004,770,618 | 617155599 |
| `LITE` | Lumentum Holdings Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 3,841,837,791 | 201113895 |
| `LLY` | Lilly (Eli) | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 3,198,590,352 | 9160 |
| `LMT` | Lockheed Martin | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 601,294,600 | 611191 |
| `LNT` | Alliant Energy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 143,003,471 | 5026984 |
| `LOW` | Lowe's | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 540,561,793 | 9199 |
| `LRCX` | Lam Research Corporation | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 2,550,304,366 | 732440574 |
| `LULU` | Lululemon Athletica | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 332,189,421 | 45157951 |
| `LUV` | Southwest Airlines | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 182,270,494 | 9282 |
| `LVS` | Las Vegas Sands | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 182,972,935 | 32602412 |
| `LYB` | LyondellBasell | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 254,247,926 | 74866700 |
| `LYV` | Live Nation Entertainment | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 297,628,051 | 37511786 |
| `MA` | Mastercard | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,715,870,424 | 38685693 |
| `MAA` | Mid-America Apartment Communities | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 102,736,565 | — |
| `MAR` | Marriott International, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 560,571,255 | 9358 |
| `MAS` | Masco | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 147,868,905 | 9360 |
| `MCD` | McDonald's | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,143,077,868 | 575958317 |
| `MCHP` | Microchip Technology Incorporated | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 682,756,815 | 271568 |
| `MCK` | McKesson Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 825,778,229 | 9657 |
| `MCO` | Moody's Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 347,199,504 | 6497 |
| `MDLZ` | Mondelez International, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 432,080,610 | 113599091 |
| `MDT` | Medtronic | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 586,286,118 | 181387075 |
| `MELI` | MercadoLibre, Inc. | Nas | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 843,794,329 | 45602025 |
| `MET` | MetLife | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 281,719,426 | 8093509 |
| `META` | Meta Platforms, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 8,323,797,699 | 107113386 |
| `MGM` | MGM Resorts | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 83,985,835 | 9560 |
| `MKC` | McCormick & Company | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 121,498,262 | 271556 |
| `MLM` | Martin Marietta Materials | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 282,907,366 | 9690 |
| `MMM` | 3M | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 476,566,150 | 9720 |
| `MNST` | Monster Beverage Corporation | Nas/S&P | 2512 | 2016-08-29 | 2026-08-27 | USD | yahoo | 453,555,190 | 196986496 |
| `MO` | Altria | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 690,528,461 | 9769 |
| `MOS` | Mosaic Company (The) | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 171,266,540 | 88292752 |
| `MPC` | Marathon Petroleum | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 785,025,175 | 89495776 |
| `MPWR` | Monolithic Power Systems, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 808,756,084 | 32106121 |
| `MRK` | Merck & Co. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,334,297,229 | 70101545 |
| `MRNA` | Moderna, Inc. | S&P | 1940 | 2018-12-07 | 2026-08-27 | USD | yahoo | 411,325,409 | 344809106 |
| `MRSH` | Marsh McLennan | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 335,051,059 | 9705 |
| `MRVL` | Marvell Technology, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 4,338,849,544 | 483492393 |
| `MS` | Morgan Stanley | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 820,444,938 | 2841574 |
| `MSCI` | MSCI | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 351,071,905 | 47101335 |
| `MSFT` | Microsoft Corporation | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 11,607,015,294 | 272093 |
| `MSI` | Motorola Solutions | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 431,552,481 | 81581011 |
| `MSTR` | Strategy Inc | Nas | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,926,413,685 | 272110 |
| `MTB` | M&T Bank | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 198,777,496 | 2585323 |
| `MTD` | Mettler Toledo | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 199,525,465 | 6477908 |
| `MU` | Micron Technology, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 28,830,324,998 | 9939 |
| `NBIS` | Nebius Group N.V. | Nas | 464 | 2024-10-21 | 2026-08-27 | USD | yahoo | 4,857,623,032 | 88819736 |
| `NCLH` | Norwegian Cruise Line Holdings | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 240,593,625 | 120643512 |
| `NDAQ` | Nasdaq, Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 253,740,350 | 29380756 |
| `NDSN` | Nordson Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 108,772,948 | 272314 |
| `NEE` | NextEra Energy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 837,475,695 | 75960201 |
| `NEM` | Newmont | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 846,272,039 | 10174 |
| `NFLX` | Netflix, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 2,139,316,263 | 15124833 |
| `NI` | NiSource | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 232,354,256 | 10260 |
| `NKE` | Nike, Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 940,620,685 | 10291 |
| `NOC` | Northrop Grumman | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 356,401,009 | 10376 |
| `NOW` | ServiceNow | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 2,133,077,812 | 109911821 |
| `NRG` | NRG Energy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 312,720,896 | 10188791 |
| `NSC` | Norfolk Southern | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 341,793,809 | 10497 |
| `NTAP` | NetApp | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 422,102,847 | 49921110 |
| `NTRS` | Northern Trust | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 188,298,777 | 272565 |
| `NUE` | Nucor | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 324,614,699 | 10557 |
| `NVDA` | NVIDIA Corporation | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 24,664,584,573 | 4815747 |
| `NVR` | NVR, Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 205,202,555 | 755864 |
| `NWS` | News Corp (Class B) | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 29,926,602 | 129348130 |
| `NWSA` | News Corp (Class A) | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 94,439,498 | 129348126 |
| `NXPI` | NXP Semiconductors N.V. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 749,111,201 | 77791077 |
| `O` | Realty Income | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 312,444,109 | 10672 |
| `ODFL` | Old Dominion Freight Line, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 298,002,029 | 272667 |
| `OKE` | Oneok | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 312,995,291 | 10794 |
| `OMC` | Omnicom Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 180,595,138 | 10811 |
| `ON` | ON Semiconductor | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 606,846,985 | 8677881 |
| `ORCL` | Oracle Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 3,336,630,978 | 272800 |
| `ORLY` | O'Reilly Automotive, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 394,025,062 | 82457808 |
| `OTIS` | Otis Worldwide | S&P | 1619 | 2020-03-19 | 2026-08-27 | USD | yahoo | 218,101,011 | 410755402 |
| `OXY` | Occidental Petroleum | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 448,507,052 | 10880 |
| `PANW` | Palo Alto Networks, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,876,111,950 | 110619459 |
| `PAYX` | Paychex, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 284,923,666 | 272942 |
| `PCAR` | PACCAR Inc | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 347,308,842 | 272992 |
| `PCG` | PG&E Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 371,065,102 | — |
| `PDD` | PDD Holdings Inc. | Nas | 2033 | 2018-07-26 | 2026-08-27 | USD | yahoo | 478,532,451 | 326398585 |
| `PEG` | Public Service Enterprise Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 164,358,671 | 11005 |
| `PEP` | PepsiCo, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 875,339,082 | 11017 |
| `PFE` | Pfizer | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,000,975,499 | 11031 |
| `PFG` | Principal Financial Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 108,584,576 | 11032 |
| `PG` | Procter & Gamble | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,239,088,101 | 11054 |
| `PGR` | Progressive Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 419,318,213 | 11080 |
| `PH` | Parker Hannifin | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 584,048,160 | 11094 |
| `PHM` | PulteGroup | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 156,394,060 | 11109 |
| `PKG` | Packaging Corporation of America | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 122,136,031 | 7740411 |
| `PLD` | Prologis | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 445,992,673 | 88819288 |
| `PLTR` | Palantir Technologies Inc. | Nas/S&P | 1484 | 2020-09-30 | 2026-08-27 | USD | yahoo | 6,253,267,778 | 444857009 |
| `PM` | Philip Morris International | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 891,897,265 | 49588376 |
| `PNC` | PNC Financial Services | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 354,115,682 | 11240 |
| `PNR` | Pentair | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 166,659,153 | 153068487 |
| `PNW` | Pinnacle West Capital | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 108,765,942 | 11257 |
| `PODD` | Insulet Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 220,948,596 | 44061838 |
| `PPG` | PPG Industries | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 149,157,630 | 11295 |
| `PPL` | PPL Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 244,167,623 | 661547 |
| `PRU` | Prudential Financial | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 193,060,635 | 13865082 |
| `PSA` | Public Storage | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 229,288,076 | 12002 |
| `PSKY` | Paramount Skydance Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 105,362,502 | 804144296 |
| `PSX` | Phillips 66 | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 618,388,661 | 105669270 |
| `PTC` | PTC Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 177,147,307 | 121507047 |
| `PWR` | Quanta Services | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 734,698,376 | 6608432 |
| `PYPL` | PayPal Holdings, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 577,582,676 | 199169591 |
| `Q` | Qnity Electronics | S&P | 210 | 2025-10-27 | 2026-08-27 | USD | yahoo | 207,569,260 | 824788107 |
| `QCOM` | QUALCOMM Incorporated | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,478,037,852 | 273544 |
| `RCL` | Royal Caribbean Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 461,747,206 | 11520 |
| `REG` | Regency Centers | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 89,019,159 | 11574 |
| `REGN` | Regeneron Pharmaceuticals, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 588,626,953 | 273733 |
| `RF` | Regions Financial Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 221,723,844 | 273815 |
| `RJF` | Raymond James Financial | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 153,216,995 | 11656 |
| `RKLB` | Rocket Lab Corporation | Nas | 1445 | 2020-11-24 | 2026-08-27 | USD | yahoo | 1,232,650,116 | 787273575 |
| `RL` | Ralph Lauren Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 217,871,319 | 92306678 |
| `RMD` | ResMed| | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 316,295,331 | 4817352 |
| `ROK` | Rockwell Automation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 303,324,479 | 11697 |
| `ROL` | Rollins, Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 175,596,927 | 11700 |
| `ROP` | Roper Technologies, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 275,384,041 | 2585629 |
| `ROST` | Ross Stores, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 582,469,737 | 273939 |
| `RSG` | Republic Services | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 299,138,414 | 6478024 |
| `RTX` | RTX Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 912,877,212 | 415342104 |
| `RVTY` | Revvity | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 145,750,600 | 6737 |
| `SBAC` | SBA Communications | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 140,050,576 | 262397910 |
| `SBUX` | Starbucks Corporation | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 637,299,904 | 274105 |
| `SCHW` | Charles Schwab Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 725,371,135 | 11905 |
| `SHOP` | Shopify Inc. | Nas | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,030,102,707 | 195014116 |
| `SHW` | Sherwin-Williams | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 583,092,798 | 12120 |
| `SJM` | J.M. Smucker Company (The) | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 122,136,698 | — |
| `SNA` | Snap-on | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 111,441,789 | 12259 |
| `SNDK` | Sandisk Corporation | Nas/S&P | 386 | 2025-02-13 | 2026-08-27 | USD | yahoo | 19,981,239,315 | 760250490 |
| `SNPS` | Synopsys, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 559,452,798 | 274499 |
| `SO` | Southern Company | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 442,998,000 | 12300 |
| `SOLV` | Solventum | S&P | 608 | 2024-03-26 | 2026-08-27 | USD | yahoo | 98,860,186 | 691984360 |
| `SPCX` | Space Exploration Technologies Corp. | Nas | 53 | 2026-06-12 | 2026-08-27 | USD | yahoo | 14,028,626,121 | — |
| `SPG` | Simon Property Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 250,120,950 | 2009113 |
| `SPGI` | S&P Global | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 695,133,955 | 229629397 |
| `SRE` | Sempra | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 314,290,972 | 11020 |
| `STE` | Steris | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 154,410,118 | 358060563 |
| `STLD` | Steel Dynamics | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 264,629,347 | 6478095 |
| `STT` | State Street Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 254,966,772 | 2730980 |
| `STX` | Seagate Technology Holdings plc | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 4,158,202,803 | 491932113 |
| `STZ` | Constellation Brands | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 208,109,051 | 276066 |
| `SW` | Smurfit Westrock | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 197,324,346 | 713434903 |
| `SWK` | Stanley Black & Decker | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 165,340,036 | 73354293 |
| `SWKS` | Skyworks Solutions | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 301,873,025 | 4726021 |
| `SYF` | Synchrony Financial | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 239,728,048 | 162231797 |
| `SYK` | Stryker Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 726,912,373 | 4430948 |
| `SYY` | Sysco | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 240,361,817 | 12581 |
| `T` | AT&T | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 842,056,959 | 37018770 |
| `TAP` | Molson Coors Beverage Company | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 101,220,563 | 33191300 |
| `TDG` | TransDigm Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 527,864,437 | 38285434 |
| `TDY` | Teledyne Technologies | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 178,447,258 | 6340773 |
| `TECH` | Bio-Techne | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 183,382,847 | 172729810 |
| `TEL` | TE Connectivity | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 373,200,214 | 731466408 |
| `TER` | Teradyne, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 979,237,615 | 12729 |
| `TFC` | Truist Financial | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 293,705,549 | 393460310 |
| `TGT` | Target Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 716,989,122 | 6437 |
| `TJX` | TJX Companies | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 906,637,115 | 12814 |
| `TKO` | TKO Group Holdings | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 196,064,479 | 652841542 |
| `TMO` | Thermo Fisher Scientific | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 999,613,461 | 12869 |
| `TMUS` | T-Mobile US, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 746,221,608 | 125980227 |
| `TPL` | Texas Pacific Land Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 133,498,912 | 463654490 |
| `TPR` | Tapestry, Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 425,107,660 | 292491487 |
| `TRGP` | Targa Resources | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 308,205,417 | 81671838 |
| `TRI` | Thomson Reuters Corporation | Nas | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 145,288,582 | 879574622 |
| `TRMB` | Trimble Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 146,008,323 | 275294 |
| `TROW` | T. Rowe Price | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 174,007,030 | 275321 |
| `TRV` | Travelers Companies (The) | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 581,326,041 | — |
| `TSCO` | Tractor Supply | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 339,053,010 | 6858322 |
| `TSLA` | Tesla, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 10,530,853,479 | 76792991 |
| `TSN` | Tyson Foods | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 149,899,041 | 275405 |
| `TT` | Trane Technologies | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 406,630,665 | 406709270 |
| `TTD` | Trade Desk (The) | S&P | 2497 | 2016-09-21 | 2026-08-27 | USD | yahoo | 284,011,846 | 248755440 |
| `TTWO` | Take-Two Interactive Software, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 482,275,348 | — |
| `TXN` | Texas Instruments Incorporated | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,480,723,721 | 13096 |
| `TXT` | Textron | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 110,941,399 | 13101 |
| `TYL` | Tyler Technologies | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 223,643,092 | 13116 |
| `UAL` | United Airlines Holdings | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 336,321,494 | 79498203 |
| `UBER` | Uber | S&P | 1835 | 2019-05-10 | 2026-08-27 | USD | yahoo | 1,271,476,083 | 365207014 |
| `UDR` | UDR, Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 105,997,748 | 43274444 |
| `UHS` | Universal Health Services | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 110,236,925 | 13206 |
| `ULTA` | Ulta Beauty | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 297,241,965 | 46970422 |
| `UNH` | UnitedHealth Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 1,644,490,469 | 13272 |
| `UNP` | Union Pacific Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 685,720,310 | 13284 |
| `UPS` | United Parcel Service | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 460,027,424 | 6231002 |
| `URI` | United Rentals | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 438,449,721 | 3629755 |
| `USB` | U.S. Bancorp | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 377,629,100 | — |
| `V` | Visa Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 2,402,995,847 | 49462172 |
| `VEEV` | Veeva Systems | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 389,710,960 | 136254493 |
| `VICI` | Vici Properties | S&P | 2175 | 2018-01-02 | 2026-08-27 | USD | yahoo | 208,533,417 | 292080616 |
| `VLO` | Valero Energy | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 756,000,059 | 13497 |
| `VLTO` | Veralto | S&P | 727 | 2023-10-04 | 2026-08-27 | USD | yahoo | 133,920,994 | 655276622 |
| `VMC` | Vulcan Materials Company | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 215,627,792 | 13515 |
| `VRSK` | Verisk Analytics | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 225,227,149 | 69293559 |
| `VRSN` | Verisign | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 183,137,242 | 4173050 |
| `VRT` | Vertiv | S&P | 2028 | 2018-08-02 | 2026-08-27 | USD | yahoo | 1,055,324,093 | 402783527 |
| `VRTX` | Vertex Pharmaceuticals Incorporated | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 568,019,756 | 275850 |
| `VST` | Vistra Corp. | S&P | 2487 | 2016-10-05 | 2026-08-27 | USD | yahoo | 611,272,916 | 254457731 |
| `VTR` | Ventas | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 265,749,617 | 4044062 |
| `VTRS` | Viatris | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 150,706,790 | 454859695 |
| `VZ` | Verizon | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 871,673,599 | 4901 |
| `WAT` | Waters Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 301,732,778 | 1448494 |
| `WBD` | Warner Bros. Discovery, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 542,109,225 | 554208351 |
| `WDAY` | Workday, Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 656,159,230 | 115324104 |
| `WDC` | Western Digital Corporation | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 3,809,419,746 | 13681 |
| `WEC` | WEC Energy Group | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 227,060,502 | 198560237 |
| `WELL` | Welltower | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 674,407,572 | 207739931 |
| `WFC` | Wells Fargo | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 944,352,307 | 10375 |
| `WM` | Waste Management | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 420,729,429 | 13379 |
| `WMB` | Williams Companies | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 450,948,528 | — |
| `WMT` | Walmart Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 2,546,912,822 | 13824 |
| `WRB` | W. R. Berkley Corporation | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 105,217,761 | — |
| `WSM` | Williams-Sonoma, Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 206,397,580 | 5019164 |
| `WST` | West Pharmaceutical Services | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 167,732,594 | 13909 |
| `WTW` | Willis Towers Watson | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 163,993,177 | 217947855 |
| `WY` | Weyerhaeuser | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 123,046,868 | 13949 |
| `WYNN` | Wynn Resorts | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 140,515,843 | 16454492 |
| `XEL` | Xcel Energy Inc. | Nas/S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 380,209,954 | 10517 |
| `XOM` | ExxonMobil | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 2,228,966,046 | 895178251 |
| `XYL` | Xylem Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 175,684,442 | 95703740 |
| `XYZ` | Block, Inc. | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 336,135,215 | 212671971 |
| `YUM` | Yum! Brands | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 385,828,725 | 3206042 |
| `ZBH` | Zimmer Biomet | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 213,897,575 | 12851783 |
| `ZBRA` | Zebra Technologies | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 259,139,822 | 276304 |
| `ZTS` | Zoetis | S&P | 2513 | 2016-08-29 | 2026-08-27 | USD | yahoo | 391,358,208 | 121665622 |

# Umerjanje kamere (F9) — navodila za operaterja

Ta dokument razloži, **kaj narediti, ko sledenje paličici odpove** (npr. zunaj
na soncu, ob spremembi osvetlitve čez dan, ob pojemajoči bateriji paličice).
Umerjanje je dostopno kadarkoli s tipko **F9** in ne prekine trenutne seje —
ob zaprtju se aplikacija vrne natanko v stanje, iz katerega si prišel.

---

## 1. Kdaj poseči po F9

| Znak | Verjeten vzrok |
|---|---|
| Kazalec se riše sam od sebe / "zelena" povsod | barvni prag ne loči LED lučke od ozadja (trava, koža, sončna svetloba) |
| Kazalec izgine, čeprav je LED prižgana | prag prestrog, LED prešibka ali osvetlitev previsoka |
| Sledenje je čez dan vse slabše | baterija pojema, LED je vse šibkejša |
| Aplikacija je na novi lokaciji (zunaj, druga dvorana) | osvetlitev in barvna temperatura se razlikujeta od prejšnjega umerjanja |

**Pravilo:** vrednosti, umerjene v pisarni ali dvorani, na drugi lokaciji
skoraj vedno ne delujejo več. Vedno umerjaj **na kraju samem**, s pravo
paličico, v pravi svetlobi.

---

## 2. Kaj vidiš v oknu za umerjanje

Pritisni **F9**. Odpre se okno s tremi sličicami v živo:

- **v živo** — barvna slika iz ROI (območja zanimanja),
- **rdeča maska** — belo je vse, kar trenutno šteje za rdečo (mirovanje),
- **zelena maska** — belo je vse, kar trenutno šteje za zeleno (risanje).

Pod slikami je vrstica **HSV na piki** — pokaže H/S/V vrednost in stanje
(`riše` / `lebdi` / `izgubljen`) **samo, kadar aplikacija trenutno nekaj
zaznava**. Če je barva napačno nastavljena in ni zaznave, se vrstica ne
posodablja — to ni okvara, pomeni le, da moraš najprej razširiti prag (glej
spodaj), da se sploh kaj pojavi.

Pod tem sta dve skupini drsnikov — **Rdeča (mirovanje)** in **Zelena
(risanje)** — ter tretji stolpec z osvetlitvijo in velikostjo pike.

| Drsnik | Pomen |
|---|---|
| `H nizko` / `H visoko` | spodnja/zgornja meja odtenka (barve) |
| `S nizko` / `S visoko` | spodnja/zgornja meja nasičenosti (čistost barve) |
| `V nizko` / `V visoko` | spodnja/zgornja meja svetlosti |
| `H2 nizko` / `H2 visoko` | **samo pri rdeči** — drugi obseg odtenka, ker se rdeča ovije okoli 0/179 |
| `Osvetlitev` | osvetlitev kamere (enako kot gumb Osvetlitev −/+ na plošči) |
| `Najm. površina` | najmanjša velikost pike, da šteje za zaznavo |
| `Najm. okroglost x100` | kako okrogla mora biti pika (0 = katerakoli oblika, 100 = popoln krog) |

Vsak premik drsnika se **takoj** uporabi — maske in kazalec se posodobijo v
živo, ni treba ničesar potrditi.

Na dnu okna sta gumba:

- **Shrani v config.json** — trajno zapiše trenutne vrednosti drsnikov nazaj
  v `config.json`. Brez tega klika so spremembe samo začasne (za trenutno sejo).
- **Zapri** — zapre okno umerjanja in nadaljuje tam, kjer si ostal (risba
  uporabnika ostane nedotaknjena).

---

## 3. Postopek umerjanja korak za korakom

1. Zaženi aplikacijo na lokaciji, kjer se bo dejansko uporabljala, in pritisni
   **F9**.
2. Uporabnik naj drži paličico v **mirovanju** (sveti **rdeča** LED).
   - Če je **rdeča maska** prazna, drsnike rdeče barve začasno razširi: znižaj
     `S nizko` in `V nizko`, razširi `H nizko`/`H visoko` in `H2
     nizko`/`H2 visoko`, dokler se v rdeči maski ne pojavi bela pika in se
     vrstica **HSV na piki** ne začne posodabljati.
   - Preberi dejanske H/S/V vrednosti, ki jih javi odčitek.
   - Zoži `H nizko/visoko` (in `H2`), `S nizko`, `V nizko` nazaj okoli te
     dejanske vrednosti (z majhno rezervo), medtem ko gledaš rdečo masko —
     cilj je bela pika **samo** na mestu LED lučke, brez šuma v ozadju.
3. Uporabnik naj pritisne gumb na paličici (sveti **zelena** LED). Ponovi isti
   postopek za **zeleno** skupino drsnikov, tokrat gledaš **zeleno masko**.
4. Drsnik **Osvetlitev** počasi zniževaj, medtem ko gledaš sliko **v živo** —
   cilj je čim nižja osvetlitev, pri kateri je LED pika še vedno jasno vidna.
   Zunaj na soncu ozadja verjetno ne bo mogoče popolnoma potemniti tako kot v
   zaprtem prostoru — to je pričakovano; takrat mora ločevanje temeljiti bolj
   na H/S/V pragovih kot na sami osvetlitvi.
5. Z drsnikoma **Najm. površina** in **Najm. okroglost x100** odstrani
   preostale motnje v obeh maskah (drobni odsevi, tekstura trave, robovi), ne
   da bi izgubil pravo piko paličice.
6. Preveri: uporabnik naj nekajkrat izmenično sveti z rdečo in zeleno LED —
   vsaka maska naj se osvetli **samo** za svojo barvo, ne za obe hkrati.
7. Klikni **Shrani v config.json**, nato **Zapri**.

**Vrstni red ni naključen:** najprej nastavi osvetlitev (grobo), nato barve,
nato po potrebi še enkrat preveri barve — sprememba osvetlitve rahlo spremeni,
kako "prava" meja izgleda.

---

## 4. Posebej zunaj (sonce, dnevna svetloba)

V zaprtem prostoru trik deluje takole: osvetlitev kamere se zniža toliko, da
je slika skoraj črna, LED lučka pa je edina svetla stvar v kadru. Zunaj to ne
deluje enako dobro, ker je sončna svetloba veliko močnejša od katerekoli sobne
osvetlitve — kamera ne more "ugasniti" ozadja, ne da bi ugasnila tudi LED.

Zato zunaj:

- **Odtenek (H) sam po sebi ne loči vedno dovolj dobro** — trava in listje
  imata skoraj enak odtenek kot zelena LED, sončno zapečena koža in oranžna
  oblačila pa lahko padejo v isti obseg kot rdeča LED. HSV umerjanje tega ne
  reši v celoti.
- Pomaga **ožji ROI**, usmerjen stran od trave/neba/oblačil močnih barv, in po
  možnosti temnejše/nevtralno ozadje za uporabnikom.
- Pomaga senca (šotor, dežnik, streha) — manj neposrednega sonca na prizorišče
  pomeni, da se da osvetlitev vseeno znižati bližje "sobnim" razmeram.
- Višji `Najm. okroglost x100` pomaga zavreči nepravilne oblike (listje,
  odsevi) v primerjavi z okroglo piko paličice.
- Vrednosti, umerjene zjutraj v senci, čez dan **ne bodo** več točne, ko sonce
  premakne kot osvetlitve — če se sledenje čez dan poslabša, ponovno pritisni
  F9 in ponovi postopek.

---

## 5. Hitri popravki brez F9

- Gumb **Osvetlitev −/+** na nadzorni plošči spremeni osvetlitev brez
  odpiranja umerjanja — uporabno, ko baterija paličice čez dan pojema.
- Gumb **Prikaži masko (diagnostika)** pokaže obe barvni maski v živo med
  normalnim delom, brez odpiranja F9 — koristno za hitro oceno, ali gre za
  barvni problem, še preden pokličeš umerjanje.

---

## 6. Če se nič ne izboljša

Glej razdelek **Odpravljanje težav** v `README.md` — pokriva tudi primere, ko
sprememba osvetlitve nima učinka (kamera ignorira nastavitve), ko se pojavijo
naključne poteze čez ves prostor, in podobno.

"""Finnish and Estonian given names, and the shapes that are not names.

Measured on 260 names harvested live, asking whether the first word is a known
given name keeps 59 and reaches 44 of 45 real people. That is the single
strongest signal available, and it is what makes a model pass cheap: it ranks
the list rather than gating it.

It is deliberately *not* a gate. Its one miss in that measurement was "Maria
Heikkilä", because Maria was missing from the list — which is the permanent
failure mode of a hand-built vocabulary. Every name still reaches the model;
this only tells the model what the list thinks.
"""
from __future__ import annotations

import re
import unicodedata

_FI = """
aada aapo aaro aarne aatos ahti aija aila aimo aini aino ainoa airi aki aleksanteri
aleksi alex alina alisa alli amanda anders andreas aneta anita anja anna anne anneli
anni annika annukka anssi antero antti anu ari arja armas arto artturi arvi asko asta
atte aulis aune auli aura benjamin bertta birgitta camilla carita casper christian
daniel dani eeli eelis eero eeva eevi eija eila eino eliisa elias elina elisa eliisa
ella elle elli elmeri elsa emil emilia emma emmi enni erik erkki esa esko essi eveliina
fanni frans freija greta hanna hannele hanne hannu harri heidi heikki heini helena
heli helmi helvi henna henri henrik hilkka hilla hilma iida iina iiris ilkka ilmari
ilona ilpo inka inkeri irene irja irma isabella iina jaakko jaana jalmari jani janika
janina janne jari jarkko jarmo jarno jasmin jenna jenni jere jesse jimi joel johanna
johannes jonas joni jonna joona joonas jorma jouko jouni juha juhani juho jukka julia
julius jussi jyri jyrki kaarina kaarlo kai kaija kaisa kaisu kalervo kalevi kalle kari
karoliina katariina kati katja katri katriina kauko keijo kerttu kimmo kirsi kirsti
klaus konsta kristian kristiina krista kyllikki laila laura lauri leena leevi leo
liisa lilja linda lotta lauri luukas maaria maarit maija maiju maire maija-liisa manu
marraskuu marja marjaana marjatta marjo marjut mari maria marika marianne marika marko
markku markus martta martti matias matilda matti mauri meeri mervi mia miia mika mikael
mikko milja milla mimmi minna minttu mira mirja mirjami niina niilo niina niko nikolas
niki nina noora oiva olavi olli oona orvokki oskari otto outi paavo pauli pauliina
paula pekka pentti perttu petra petri petteri pia pihla piia pilvi pinja pirjo pirkko
pontus pyry päivi päivikki raija raila raimo raine rami rauha rauno reetta reijo riikka
riitta rikhard risto ritva roni roope rosa saana saara sakari salla sami sampo samu
samuli sanna sanni sari sasha satu sauli seija seppo siiri silja silja simo sini sinikka
sirkka sirpa sisu sofia soile sonja sirkku susanna taina taisto taito taneli tanja tapani
tapio tarja tarmo taru tauno teemu tellervo teppo tero terhi terttu tiia tiina timo tiina
toivo tomi tommi toni topi tuija tuomas tuomo tuula tuuli tytti ulla ulpu urho urpo usko
uuno vaino valtteri vappu veera veeti veikko venla verna vesa vieno vihtori viivi viktor
ville vilma vilho virpi voitto väinö yrjö
"""

_EE = """
aare aarne ado ago aigar ain aino aivar alar aleksander alar allan ander andres andrus
anna anne anni anton anu ants ardo argo ariel arno arvo aule ave berit brit egert eha
eiki einar elin ell ella elle elmar elts endel enn epp erik erki eve evelin gerda gert
grete hanna hannes heidi heigo heiki helen heli helle hendrik henn iida ilme ilmar imbi
indrek inga ingrid jaak jaan jaanika jaanus jaanika janar janek janne jarmo joosep juhan
juri jüri kadi kadri kaia kaido kairi kaisa kalev kalle karin karl karmen kaspar katrin
kaupo kerli kersti kert kertu kirke koit krista kristel kristi kristiina kristjan kristo
kulli laine lauri leelo leho lembit lenna liina liis liisa liivi lilian linda liina lisette
maarja madis mai maia maie mailis maire malle marek margit margus mari maria marika marju
mark marko marleen mart martin martti mati meelis meeli merike merle mihkel milvi mirjam
neeme olav oliver ott peep peeter piia piret priit rain raivo rasmus rein reelika riho
riina risto robert saima sander sandra siim signe silva silver siret sirje sulev taavi
taimi taivo tamara tanel tarmo tiia tiina tiit tiiu toomas triin tõnis tõnu ulvi urmas
vahur vaido valdur veiko vello viktor virve ülle ülo
"""

# Names seen live from outside the two languages; the market is not monolingual.
_OTHER = """
abdul adam alan alexander alexandra ali amir ana andrea andrei anastasia anton artur
carlos daniel david denis dmitri diego eduardo elena emilio erik fabio filippo francesco
gabriel george giulia gleb hassan igor ivan janis james jose juan julia karim karlis
kevin luca lucas luis marco maria mario martin mattia michael michele mohammad mohamed
natalia nikolai olga oleg omar oscar pablo paolo patrick paul pavel peter piotr rafael
raul ricardo robert roberto sergei sergey sofia stefan svetlana thomas tomasz valentina
victor vladimir yurii yuri
"""


def _fold(s: str) -> str:
    d = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in d if not unicodedata.combining(c)).lower()


GIVEN_NAMES: set[str] = {
    _fold(w) for block in (_FI, _EE, _OTHER) for w in block.split() if w
}

# Street-name endings, restricted to those that are not also surname endings.
# Deliberately excludes -ranta, -maki, -harju, -rinne, -silta, -niitty and
# -puisto: Kotiranta, Ylimaki and Makiharju are ordinary Finnish surnames, and
# flagging them would push the model away from real people.
_STREET_SUFFIX = (
    "katu", "tie", "kuja", "polku", "raitti", "vayla", "aukio", "puistotie",
    "esplanadi", "bulevardi", "gatan", "vagen", "tanav", "maantee", "puiestee",
)
# Whole words that only ever appear as a street type.
_STREET_WORD = {"tee", "pst", "mnt", "tn", "katu", "tie", "puiestee", "maantee"}


def is_given_name(token: str) -> bool:
    """True when a word is a known given name. Hyphenated forms count if
    either half does — Anna-Maija, Jaan-Kristjan, Marja-Liisa."""
    t = _fold(token).strip(".,:;")
    if not t:
        return False
    if t in GIVEN_NAMES:
        return True
    return any(part in GIVEN_NAMES for part in t.split("-") if part)


def looks_like_address(name: str) -> bool:
    """True when the string carries an unambiguous street type.

    A name whose surname merely ends in a landscape word is not an address:
    Kotiranta, Ylimaki and Makiharju are surnames, and the suffix list above
    is pruned accordingly.
    """
    tokens = _fold(name).split()
    for token in tokens:
        clean = token.strip(".,")
        if clean in _STREET_WORD:
            return True
        for suffix in _STREET_SUFFIX:
            if clean.endswith(suffix) and len(clean) > len(suffix) + 2:
                return True
    return False


def signals(name: str) -> dict:
    """Everything the deterministic side can say about a candidate.

    Handed to the model as evidence rather than used to decide, so a name
    missing from the list still gets a fair hearing.
    """
    tokens = [t for t in re.split(r"\s+", (name or "").strip()) if t]
    return {
        "tokens": len(tokens),
        "first_is_given_name": bool(tokens) and is_given_name(tokens[0]),
        "any_token_is_given_name": any(is_given_name(t) for t in tokens),
        "looks_like_address": looks_like_address(name),
        "all_capitalised": bool(tokens) and all(
            t[:1].isupper() for t in tokens),
    }


def hint(name: str) -> str:
    """One short line of evidence per name, for the prompt."""
    s = signals(name)
    bits = []
    bits.append("first word IS a known given name" if s["first_is_given_name"]
                else "first word is NOT in the given-name list")
    if not s["first_is_given_name"] and s["any_token_is_given_name"]:
        bits.append("but a later word is a given name")
    if s["looks_like_address"]:
        bits.append("contains a street-name ending")
    return "; ".join(bits)


# --- names hiding in the address ------------------------------------------

_LOCAL_SPLIT = re.compile(r"[._\-+]")


def name_from_email(email: str | None) -> str | None:
    """The person's name implied by an address, or None.

    On a live run this decoded 11% of rows, and it is better evidence than the
    surrounding page text: "petri.sahlsten@delicatessen.fi" names the person
    outright, where the text near it produced site furniture. A bare forename
    counts — "Hi Sini," is a perfectly good greeting.

    Requires the first part to be a known given name, so info@, myynti@ and
    booking@ produce nothing.
    """
    from .emails import is_generic_mailbox

    if not email or "@" not in email or is_generic_mailbox(email):
        return None
    local = email.split("@")[0].lower()
    parts = [p for p in _LOCAL_SPLIT.split(local) if p and not p.isdigit()]
    if not parts or not is_given_name(parts[0]):
        return None
    if len(parts) == 1:
        # A bare forename, but only when it is long enough not to be initials.
        return parts[0].capitalize() if len(parts[0]) > 2 else None
    if len(parts[1]) > 2:
        return f"{parts[0].capitalize()} {parts[1].capitalize()}"
    return parts[0].capitalize()

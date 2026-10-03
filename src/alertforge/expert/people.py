"""People in an expert world: who is on which rota, how they sign their Slack messages, how they write.

Every team with services in the repo has two or three people on its rota; the observability team owns the repo
(and hands the queue over), SRE watches every page, product writes the SLO notes. A page is acked by someone on
the rota that got it, and a thread about a team's service is mostly that team talking (R7)."""

from __future__ import annotations

import random
from dataclasses import dataclass

FIRST = {
    "eu": ["Marta", "Joris", "Ines", "Luca", "Pieter", "Anouk", "Tomás", "Sanne", "Bram", "Elena", "Mateo", "Lotte",
           "Ravi", "Chiara", "Dev", "Noor", "Femke", "Hugo", "Lucía", "Jeroen", "Camille", "Thijs", "Giulia", "Arnaud",
           "Maud", "Pablo", "Wouter", "Irene", "Bas", "Alba", "Koen", "Léa"],
    "uk": ["Callum", "Priya", "Hannah", "Rhys", "Siobhan", "Owen", "Imogen", "Tariq", "Ellie", "Fergus", "Niamh",
           "Arjun", "Beth", "Declan", "Maisie", "Kwame", "Ciara", "Rory", "Hollie", "Euan", "Saoirse", "Jamal", "Freya",
           "Gareth", "Aoife", "Nadia", "Tom", "Megan", "Ruairi", "Zara"],
    "us": ["Jordan", "Maya", "Tyler", "Keisha", "Ben", "Carla", "Devon", "Alyssa", "Marcus", "Rachel", "Andre",
           "Megan", "Luis", "Priyanka", "Sam", "Tasha", "Erin", "Darnell", "Kayla", "Hector", "Brooke", "Raj", "Kyle",
           "Vanessa", "Trevor", "Lena", "Omar", "Courtney", "Wes", "Jasmine"],
    "de": ["Jonas", "Lena", "Felix", "Katrin", "Tobias", "Miriam", "Stefan", "Anja", "Jan", "Sophie", "Tomasz",
           "Dilara", "Moritz", "Yusuf", "Carolin", "Emre", "Lukas", "Hannah", "Niklas", "Svenja", "Mehmet", "Julia",
           "Florian", "Annika", "Kai", "Merve", "Benedikt", "Nina", "Sascha", "Leonie"],
    "br": ["Rafael", "Camila", "Thiago", "Juliana", "Bruno", "Larissa", "Diego", "Fernanda", "Gustavo", "Aline",
           "Mateus", "Bianca", "Renan", "Paula", "Caio", "Letícia", "Vinícius", "Natália", "Felipe", "Priscila",
           "Rodrigo", "Tatiane", "Igor", "Beatriz", "Lucas", "Marina"],
    "nordic": ["Elin", "Mikko", "Sara", "Olli", "Johanna", "Anders", "Aino", "Henrik", "Linnea", "Jussi", "Maja",
               "Oskar", "Ville", "Ida", "Emil", "Hanna", "Tuomas", "Freja", "Lars", "Kaisa", "Viktor", "Saga",
               "Antti", "Ebba", "Petri", "Matilda"],
    "in": ["Aditi", "Rohan", "Sneha", "Karthik", "Ananya", "Vikram", "Meera", "Arun", "Divya", "Siddharth",
           "Pooja", "Nikhil", "Lakshmi", "Varun", "Harsha", "Ishaan", "Kavya", "Manish", "Nandini", "Pranav",
           "Ritu", "Sandeep", "Tanvi", "Vivek", "Yamini", "Abhishek"],
    "pl": ["Kasia", "Piotr", "Agnieszka", "Marek", "Zofia", "Tomasz", "Ola", "Bartek", "Magda", "Kuba", "Ewa",
           "Wojtek", "Michał", "Natalia", "Paweł", "Joanna", "Krzysztof", "Monika", "Łukasz", "Dorota", "Grzegorz",
           "Karolina", "Adam", "Iwona"],
}
LAST = {
    "eu": ["de Vries", "Jansen", "Rossi", "Moreau", "Bakker", "García", "Visser", "Ferrari", "Dubois", "Meijer",
           "López", "Smit", "Bianchi", "Mulder", "Laurent", "Peeters", "Romero", "van Dijk", "Conti", "Bos"],
    "uk": ["Hughes", "Patel", "Murphy", "Evans", "O'Brien", "Clarke", "Walsh", "Khan", "Price", "Doyle", "Begum",
           "Fraser", "Mensah", "Lloyd", "Byrne", "Shah", "Morgan", "Kerr", "Ahmed", "Taylor"],
    "us": ["Lindqvist", "Johnson", "Nguyen", "Ramirez", "Brooks", "Chen", "Washington", "Okafor", "Miller",
           "Hernandez", "Park", "Coleman", "Rivera", "Sullivan", "Kim", "Foster", "Alvarez", "Shah", "Bennett",
           "Reyes"],
    "de": ["Becker", "Wagner", "Yılmaz", "Hoffmann", "Schulz", "Kaiser", "Neumann", "Schröder", "Demir",
           "Zimmermann", "Krüger", "Hartmann", "Lange", "Arslan", "Werner", "Braun", "Vogel", "Kaya", "Fuchs",
           "Richter"],
    "br": ["Souza", "Oliveira", "Santos", "Pereira", "Lima", "Carvalho", "Ferreira", "Almeida", "Ribeiro",
           "Costa", "Gomes", "Martins", "Rocha", "Barbosa", "Araújo", "Mendes", "Nunes", "Teixeira"],
    "nordic": ["Lindgren", "Virtanen", "Nyström", "Korhonen", "Berg", "Mäkinen", "Holm", "Nieminen", "Sandberg",
               "Laine", "Lund", "Heikkinen", "Ekström", "Koskinen", "Dahl", "Järvinen", "Strand", "Salo"],
    "in": ["Iyer", "Sharma", "Reddy", "Nair", "Gupta", "Menon", "Rao", "Kulkarni", "Banerjee", "Pillai",
           "Joshi", "Desai", "Krishnan", "Mehta", "Chatterjee", "Bhat", "Agarwal", "Naidu"],
    "pl": ["Nowak", "Mazur", "Krawczyk", "Pawlak", "Król", "Wieczorek", "Kowalczyk", "Wójcik", "Kaczmarek",
           "Sikora", "Baran", "Dudek", "Wróbel", "Zając", "Kubiak", "Walczak", "Stępień", "Duda"],
}
ASCII = str.maketrans("áàâãäåçéèêëíìîïñóòôõöúùûüýłśźżćńąęşığ", "aaaaaaceeeeiiiinooooouuuuylszzcnaesig")
OBSERVABILITY, SRE, PRODUCT = "observability", "sre", "product"


@dataclass(frozen=True)
class Person:
    first: str
    handle: str       # Slack handle
    trait: str        # terse | chatty | asks: how they write in a thread
    last: str = ""
    team: str = ""    # the rota they are on (a service team, observability, sre or product)

    @property
    def full(self) -> str:
        """The name PagerDuty shows."""
        return f"{self.first} {self.last}".strip()


def _handle(rng: random.Random, first: str, last: str) -> str:
    f, l = first.lower().translate(ASCII), last.lower().replace(" ", "").replace("'", "").translate(ASCII)
    return rng.choice([f"{f}.{l[0]}", f"{f}{l[0]}", f, f"{f[0]}{l}", f"{f}.{l}", f"{f}_{l[:3]}"])


def people(rng: random.Random, region: str, teams, n_per: tuple[int, int] = (2, 3)) -> list[Person]:
    """Two or three people per service team (`teams` may be a count for old callers), three in observability, two
    in SRE and one product owner. Mostly names from the office's region, a few from elsewhere."""
    if isinstance(teams, int):
        teams = [f"team{i}" for i in range(max(1, teams // 2))]
    rotas = [(t, rng.randint(*n_per)) for t in teams] + [(OBSERVABILITY, 3), (SRE, 2), (PRODUCT, 1)]
    others = [(r, x) for r, xs in FIRST.items() if r != region for x in xs]
    out, used, handles = [], set(), set()
    for team, k in rotas:
        while k:
            if rng.random() < 0.82:
                reg, first = region, rng.choice(FIRST[region])
            else:
                reg, first = rng.choice(others)
            last = rng.choice(LAST[reg])
            if first in used:
                continue
            h = _handle(rng, first, last)
            if h in handles:
                continue
            used.add(first)
            handles.add(h)
            out.append(Person(first, h, rng.choice(["terse", "chatty", "asks"]), last, team))
            k -= 1
    return out


def rota(w, team: str | None) -> list[Person]:
    """The people on a team's rota (the whole roster when the team has nobody, e.g. a shared team)."""
    got = [p for p in w.people if p.team == team]
    return got or [p for p in w.people if p.team not in (PRODUCT,)]


def crew(w, rng: random.Random, team: str | None, k: int) -> list[Person]:
    """`k` distinct people for a thread about one of `team`'s services: its rota first, then SRE and
    observability, then anyone."""
    own = [p for p in w.people if p.team == team]
    rng.shuffle(own)
    rest = [p for p in w.people if p.team in (SRE, OBSERVABILITY) and p not in own]
    rng.shuffle(rest)
    tail = [p for p in w.people if p not in own and p not in rest and p.team != PRODUCT]
    rng.shuffle(tail)
    pool = own[:max(1, min(len(own), k - 1))] + rest + own[max(1, min(len(own), k - 1)):] + tail
    return pool[:k]


def by_trait(p: Person, **lines: str) -> str:
    """The line this person would write: `terse=`, `chatty=`, `asks=` variants (falls back to terse)."""
    return lines.get(p.trait) or lines.get("terse") or next(iter(lines.values()))


def product_owner(w) -> Person:
    return next((p for p in w.people if p.team == PRODUCT), w.people[0])

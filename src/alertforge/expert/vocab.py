"""Seed vocabularies for the expert tier: companies by industry, their services and the team each service
naturally belongs to, which teams were carved out of which, and how people in each industry talk about the
outside world. People and rotas live in `people.py`."""

from __future__ import annotations

import random
from dataclasses import dataclass

from .people import Person, people  # noqa: F401  (re-exported: older modules import them from here)


@dataclass(frozen=True)
class Company:
    name: str
    domain: str        # where the runbook site and the Jira bridge live (a reserved `.example` name, RFC 2606)
    industry: str
    tz: str            # IANA zone of the main office
    tz_label: str      # how people write it in Slack ("CEST", "ET", "IST")
    utc_offset: float  # hours, at the time of the incidents (late September); India is +5:30
    region: str        # name pool
    key: str           # ticket project key


# Seven per industry, 56 in all: one company per task in a 45-task block (families.company_for), so no company
# shows up twice with two different org charts. Names are a mix of plain, made-up and initialism styles.
COMPANIES = [
    Company("Harrow & Finch", "harrowfinch.example", "commerce", "Europe/London", "BST", 1, "uk", "PLAT"),
    Company("Pellacorn", "pellacorn.example", "commerce", "Europe/Amsterdam", "CEST", 2, "eu", "OPS"),
    Company("Carto & Hale", "corp.cartohale.example", "commerce", "America/New_York", "ET", -4, "us", "OBS"),
    Company("KDM Handel", "int.kdm-handel.example", "commerce", "Europe/Berlin", "CEST", 2, "de", "MON"),
    Company("Mercado Rila", "mercadorila.example", "commerce", "America/Sao_Paulo", "BRT", -3, "br", "INFRA"),
    Company("Basketwise", "basketwise.example", "commerce", "America/Chicago", "CT", -5, "us", "SRE"),
    Company("Halde Retail", "halde.example", "commerce", "Europe/Stockholm", "CEST", 2, "nordic", "OPS"),
    Company("Juniper Ledger", "juniperledger.example", "fintech", "Europe/London", "BST", 1, "uk", "SRE"),
    Company("Trevo Pagamentos", "int.trevopag.example", "fintech", "America/Sao_Paulo", "BRT", -3, "br", "INFRA"),
    Company("Kliro", "kliro.example", "fintech", "Europe/Berlin", "CEST", 2, "de", "OPS"),
    Company("FNX Clearing", "corp.fnxclearing.example", "fintech", "America/New_York", "ET", -4, "us", "OBS"),
    Company("Numera Pay", "numerapay.example", "fintech", "Europe/Warsaw", "CEST", 2, "pl", "PLAT"),
    Company("Penni", "penni.example", "fintech", "Asia/Kolkata", "IST", 5.5, "in", "SRE"),
    Company("Halvard Finans", "halvard.example", "fintech", "Europe/Helsinki", "EEST", 3, "nordic", "OPS"),
    Company("Brandt & Okafor Freight", "brandt-okafor.example", "logistics", "Europe/Berlin", "CEST", 2, "de", "OPS"),
    Company("Cobblestone Logistics", "cobblestone.example", "logistics", "America/Chicago", "CT", -5, "us", "SRE"),
    Company("Quayline Couriers", "quayline.example", "logistics", "Europe/Dublin", "IST", 1, "uk", "OPS"),
    Company("Fahrwerk Logistik", "int.fahrwerk.example", "logistics", "Europe/Berlin", "CEST", 2, "de", "LOG"),
    Company("Rotaway", "rotaway.example", "logistics", "America/Toronto", "ET", -4, "us", "OPS"),
    Company("MVL Express", "mvlexpress.example", "logistics", "Europe/Warsaw", "CEST", 2, "pl", "OPS"),
    Company("Vela Entregas", "velaentregas.example", "logistics", "America/Sao_Paulo", "BRT", -3, "br", "INFRA"),
    Company("Quillon Health", "quillon.example", "health", "America/New_York", "ET", -4, "us", "OBS"),
    Company("Tallis Claims", "corp.tallisclaims.example", "health", "America/Denver", "MT", -6, "us", "SRE"),
    Company("Linnea Vård", "linneavard.example", "health", "Europe/Stockholm", "CEST", 2, "nordic", "OPS"),
    Company("Ostra Health", "ostrahealth.example", "health", "America/New_York", "ET", -4, "us", "PLAT"),
    Company("Pratt Medical Billing", "prattbilling.example", "health", "America/Chicago", "CT", -5, "us", "SRE"),
    Company("Klinikkonto", "klinikkonto.example", "health", "Europe/Berlin", "CEST", 2, "de", "OPS"),
    Company("Nidaan Health", "nidaan.example", "health", "Asia/Kolkata", "IST", 5.5, "in", "SRE"),
    Company("Brightwell Travel", "brightwell.example", "travel", "Europe/Madrid", "CEST", 2, "eu", "OPS"),
    Company("Kaafila Travel", "int.kaafila.example", "travel", "Asia/Kolkata", "IST", 5.5, "in", "SRE"),
    Company("Fernhop", "fernhop.example", "travel", "Europe/Paris", "CEST", 2, "eu", "PLAT"),
    Company("TripNorth", "tripnorth.example", "travel", "Europe/Helsinki", "EEST", 3, "nordic", "OPS"),
    Company("Gatebox Travel", "gatebox.example", "travel", "Europe/London", "BST", 1, "uk", "OPS"),
    Company("Rota Viagens", "rotaviagens.example", "travel", "America/Sao_Paulo", "BRT", -3, "br", "INFRA"),
    Company("Inntrail", "corp.inntrail.example", "travel", "America/Denver", "MT", -6, "us", "SRE"),
    Company("Cobaltline Games", "cobaltline.example", "games", "Europe/Helsinki", "EEST", 3, "nordic", "OPS"),
    Company("Pixelmoor", "pixelmoor.example", "games", "America/Los_Angeles", "PT", -7, "us", "LIVE"),
    Company("Northpaw Studios", "northpaw.example", "games", "Europe/Warsaw", "CEST", 2, "pl", "OPS"),
    Company("Hexbloom", "hexbloom.example", "games", "Europe/Stockholm", "CEST", 2, "nordic", "LIVE"),
    Company("Quarrel Games", "int.quarrelgames.example", "games", "America/Los_Angeles", "PT", -7, "us", "LIVE"),
    Company("Sixty Frames", "sixtyframes.example", "games", "Europe/London", "BST", 1, "uk", "OPS"),
    Company("Lumora Interactive", "lumora.example", "games", "Europe/Berlin", "CEST", 2, "de", "OPS"),
    Company("Ashby Media", "ashbymedia.example", "media", "Europe/London", "BST", 1, "uk", "OPS"),
    Company("Fieldcast", "fieldcast.example", "media", "America/New_York", "ET", -4, "us", "SRE"),
    Company("Kinora", "kinora.example", "media", "Europe/Berlin", "CEST", 2, "de", "PLAT"),
    Company("Onda Play", "int.ondaplay.example", "media", "America/Sao_Paulo", "BRT", -3, "br", "INFRA"),
    Company("BRV Media", "corp.brvmedia.example", "media", "America/Toronto", "ET", -4, "us", "SRE"),
    Company("Telón Media", "telon.example", "media", "Europe/Madrid", "CEST", 2, "eu", "OPS"),
    Company("Kanal Nord", "kanalnord.example", "media", "Europe/Stockholm", "CEST", 2, "nordic", "OPS"),
    Company("Fieldnote", "fieldnote.example", "saas", "Europe/Dublin", "IST", 1, "uk", "OPS"),
    Company("Quarry Systems", "quarrysys.example", "saas", "America/Toronto", "ET", -4, "us", "SRE"),
    Company("Tessellate", "tessellate.example", "saas", "Asia/Kolkata", "IST", 5.5, "in", "OBS"),
    Company("Deskpilot", "deskpilot.example", "saas", "Europe/Amsterdam", "CEST", 2, "eu", "OPS"),
    Company("Brieflow", "corp.brieflow.example", "saas", "America/Chicago", "CT", -5, "us", "PLAT"),
    Company("Wekalo", "wekalo.example", "saas", "Europe/Warsaw", "CEST", 2, "pl", "OPS"),
    Company("ACR Software", "int.acr-software.example", "saas", "Europe/Berlin", "CEST", 2, "de", "OPS"),
]

def public_zone(domain: str) -> str:
    """The zone customers use: the company's internal zone without its `corp.`/`int.` prefix."""
    for pre in ("corp.", "int."):
        if domain.startswith(pre):
            return domain[len(pre):]
    return domain


# industry -> list of (service, kind, sli family); kind: api | worker | grpc | consumer | indexer
SERVICES = {
    "commerce": [("checkout-api", "api", "http"), ("cart-svc", "api", "http"), ("catalog-api", "api", "http"),
                 ("pricing-svc", "grpc", "grpc"), ("inventory-svc", "grpc", "grpc"), ("promo-worker", "worker", "http"),
                 ("order-events", "worker", "http"), ("returns-api", "api", "http"), ("search-indexer", "indexer", "http"),
                 ("warehouse-export", "consumer", "http"), ("receipts-mailer", "consumer", "http"),
                 ("quote-lookup", "api", "latency")],
    "fintech": [("ledger-api", "api", "http"), ("ledger-worker", "worker", "http"), ("payments-gw", "api", "http"),
                ("fraud-scorer", "grpc", "grpc"), ("kyc-svc", "api", "http"), ("payout-worker", "worker", "http"),
                ("wallet-svc", "api", "http"), ("fx-rates", "grpc", "grpc"), ("statements-export", "consumer", "http"),
                ("card-auth", "api", "http"), ("rate-lookup", "api", "latency"), ("search-indexer", "indexer", "http")],
    "logistics": [("dispatch-gw", "api", "http"), ("route-planner", "grpc", "grpc"), ("tracking-api", "api", "http"),
                  ("eta-svc", "grpc", "grpc"), ("manifest-worker", "worker", "http"), ("carrier-sync", "worker", "http"),
                  ("depot-api", "api", "http"), ("label-printer", "api", "http"), ("warehouse-export", "consumer", "http"),
                  ("rate-quote-svc", "api", "latency"), ("parcel-indexer", "indexer", "http")],
    "health": [("claims-api", "api", "http"), ("eligibility-svc", "grpc", "grpc"), ("adjudication-worker", "worker", "http"),
               ("provider-dir", "api", "http"), ("member-api", "api", "http"), ("prior-auth-svc", "api", "http"),
               ("remit-export", "consumer", "http"), ("benefits-lookup", "api", "latency"), ("claims-indexer", "indexer", "http"),
               ("fax-ingest", "worker", "http")],
    "travel": [("fare-quotes", "api", "http"), ("booking-api", "api", "http"), ("inventory-sync", "worker", "http"),
               ("pnr-worker", "worker", "http"), ("seatmap-svc", "grpc", "grpc"), ("loyalty-points", "api", "http"),
               ("search-reindexer", "consumer", "http"), ("rate-quote-svc", "api", "latency"), ("hotel-indexer", "indexer", "http"),
               ("checkin-api", "api", "http")],
    "games": [("matchmaker", "grpc", "grpc"), ("lobby-api", "api", "http"), ("profile-svc", "api", "http"),
              ("leaderboard-svc", "api", "http"), ("store-api", "api", "http"), ("telemetry-ingest", "worker", "http"),
              ("replay-export", "consumer", "http"), ("entitlements-lookup", "api", "latency"), ("chat-gw", "api", "http"),
              ("asset-indexer", "indexer", "http")],
    "media": [("playback-api", "api", "http"), ("catalog-svc", "grpc", "grpc"), ("transcode-worker", "worker", "http"),
              ("recs-svc", "grpc", "grpc"), ("subtitle-svc", "api", "http"), ("ingest-api", "api", "http"),
              ("search-indexer", "indexer", "http"), ("rights-lookup", "api", "latency"), ("receipts-mailer", "consumer", "http"),
              ("license-gw", "api", "http")],
    "saas": [("tenant-api", "api", "http"), ("billing-svc", "api", "http"), ("webhooks-dispatch", "worker", "http"),
             ("audit-export", "consumer", "http"), ("sso-gw", "api", "http"), ("reports-worker", "worker", "http"),
             ("search-indexer", "indexer", "http"), ("usage-lookup", "api", "latency"), ("notifier-svc", "grpc", "grpc"),
             ("files-api", "api", "http")],
}

# The team each service belongs to when nothing odd happened (R3). A service that shares a word with a team
# belongs to that team.
AFFINITY = {
    "commerce": {"checkout-api": "checkout-core", "order-events": "checkout-core", "receipts-mailer": "checkout-core",
                 "cart-svc": "storefront", "returns-api": "storefront", "catalog-api": "product-catalog",
                 "search-indexer": "product-catalog", "pricing-svc": "pricing", "quote-lookup": "pricing",
                 "inventory-svc": "fulfillment", "warehouse-export": "fulfillment", "promo-worker": "growth"},
    "fintech": {"ledger-api": "ledger", "ledger-worker": "ledger", "statements-export": "ledger",
                "payments-gw": "payments-core", "payout-worker": "payments-core", "wallet-svc": "payments-core",
                "fraud-scorer": "risk", "kyc-svc": "onboarding", "search-indexer": "onboarding", "fx-rates": "treasury",
                "rate-lookup": "treasury", "card-auth": "cards"},
    "logistics": {"dispatch-gw": "dispatch", "route-planner": "routing", "tracking-api": "tracking", "eta-svc": "tracking",
                  "parcel-indexer": "tracking", "manifest-worker": "depot-ops", "depot-api": "depot-ops",
                  "label-printer": "depot-ops", "carrier-sync": "carrier-integrations",
                  "rate-quote-svc": "carrier-integrations", "warehouse-export": "network-planning"},
    "health": {"claims-api": "claims", "adjudication-worker": "claims", "claims-indexer": "claims",
               "eligibility-svc": "eligibility", "benefits-lookup": "eligibility", "provider-dir": "provider-data",
               "member-api": "member-experience", "prior-auth-svc": "clinical-ops", "fax-ingest": "clinical-ops",
               "remit-export": "payer-integrations"},
    "travel": {"fare-quotes": "shopping", "search-reindexer": "shopping", "booking-api": "booking", "pnr-worker": "booking",
               "inventory-sync": "inventory", "seatmap-svc": "inventory", "loyalty-points": "loyalty",
               "rate-quote-svc": "partner-feeds", "hotel-indexer": "partner-feeds", "checkin-api": "airport-ops"},
    "games": {"matchmaker": "matchmaking", "lobby-api": "matchmaking", "profile-svc": "player-platform",
              "leaderboard-svc": "social", "chat-gw": "social", "store-api": "economy", "entitlements-lookup": "economy",
              "telemetry-ingest": "telemetry", "replay-export": "telemetry", "asset-indexer": "live-ops"},
    "media": {"playback-api": "playback", "catalog-svc": "discovery", "recs-svc": "discovery", "search-indexer": "discovery",
              "transcode-worker": "content-pipeline", "subtitle-svc": "content-pipeline", "ingest-api": "ingest",
              "rights-lookup": "rights", "license-gw": "rights", "receipts-mailer": "subscriptions"},
    "saas": {"tenant-api": "core-api", "files-api": "core-api", "billing-svc": "billing", "usage-lookup": "billing",
             "sso-gw": "identity", "webhooks-dispatch": "integrations", "notifier-svc": "integrations",
             "audit-export": "reporting", "reports-worker": "reporting", "search-indexer": "search"},
}
TEAMS = {ind: sorted(set(m.values())) for ind, m in AFFINITY.items()}

# (parent, child): the child team was carved out of the parent, taking its own services with it. Before the split
# the parent owned them, which is why they still sit in the parent's rule file.
SPLITS = {
    "commerce": [("product-catalog", "pricing"), ("checkout-core", "storefront"), ("storefront", "growth")],
    "fintech": [("payments-core", "cards"), ("ledger", "treasury"), ("risk", "onboarding")],
    "logistics": [("dispatch", "routing"), ("tracking", "carrier-integrations"), ("depot-ops", "network-planning")],
    "health": [("claims", "payer-integrations"), ("provider-data", "clinical-ops"), ("member-experience", "eligibility")],
    "travel": [("booking", "loyalty"), ("shopping", "partner-feeds"), ("inventory", "airport-ops")],
    "games": [("player-platform", "social"), ("live-ops", "telemetry"), ("player-platform", "economy")],
    "media": [("content-pipeline", "ingest"), ("playback", "subscriptions"), ("discovery", "rights")],
    "saas": [("core-api", "search"), ("core-api", "identity"), ("integrations", "reporting")],
}

# One historical oddity a world may carry (R3): a service that stayed with another team, and the line in
# ownership.yaml that says why. `{svc}`, `{team}` (current owner), `{home}` (where it would naturally live).
ODDITY_NOTES = [
    "{svc} stayed with {team} after the {year} rewrite; it goes to {home} once they have the capacity",
    "{team} kept {svc} when {home} was set up (nobody on {home} knew the code yet)",
    "{svc}: {team} until {home} has a second person on call for it",
    "historical: {team} wrote {svc}; {home} will take it over after the migration",
]

# teams that route through the shared Alertmanager but have no rules in this repo
SHARED_TEAMS = ["platform", "data-platform", "database-reliability", "network", "security-ops", "storage",
                "ml-platform", "build-infra", "edge", "messaging"]
OBS_TEAM = "observability"

# How people in each industry talk about the outside world in incident threads: who reports an outage
# (`reporter`, and what they say), which upstream flaps, what a lagging read replica serves, and what a latency
# lookup sits in front of.
FLAVOR = {
    "commerce": {"reporter": "a merchant", "report": "a merchant opened a P1, their shoppers get errors at payment",
                 "upstream": "the payments provider", "stale": "stale stock levels", "waits_on": "the checkout page"},
    "fintech": {"reporter": "a partner bank", "report": "a partner bank is on the phone about failed requests",
                "upstream": "the card network gateway", "stale": "stale balances", "waits_on": "the payment confirmation screen"},
    "logistics": {"reporter": "a carrier", "report": "a carrier says their integration gets 502s from us",
                  "upstream": "the carrier API", "stale": "stale parcel status", "waits_on": "the booking form"},
    "health": {"reporter": "a provider office", "report": "a provider office called the help desk, their portal is erroring",
               "upstream": "the clearinghouse", "stale": "stale claim status", "waits_on": "the front-desk check-in screen"},
    "travel": {"reporter": "a partner agency", "report": "a partner agency says bookings through us are erroring",
               "upstream": "the GDS", "stale": "stale seat availability", "waits_on": "the search results page"},
    "games": {"reporter": "community support", "report": "community support says players are getting error screens",
              "upstream": "the platform auth provider", "stale": "stale inventories", "waits_on": "the in-game store"},
    "media": {"reporter": "a device partner", "report": "a device partner says their app is getting errors from us",
              "upstream": "the CDN", "stale": "stale watch history", "waits_on": "the play button"},
    "saas": {"reporter": "an enterprise customer", "report": "an enterprise customer opened a P1 about API errors",
             "upstream": "the identity provider", "stale": "stale usage numbers", "waits_on": "the billing page"},
}


def flavor(industry: str) -> dict:
    return FLAVOR.get(industry, FLAVOR["saas"])


def company_for(master: int, family_index: int, seed: int) -> Company:
    """One company per task within a block of 45 (5 families x 9 seeds): a fixed permutation of the company list
    per master seed and block, so no company appears in two tasks of the same build with two different worlds."""
    block = (seed - 1) // 9
    order = list(range(len(COMPANIES)))
    random.Random(f"{master}:companies:{block}").shuffle(order)
    return COMPANIES[order[family_index * 9 + (seed - 1) % 9]]


def camel(service: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in service.replace("_", "-").split("-"))


def poss(name: str) -> str:
    """Possessive the way people write it: `payments'` for names ending in s, `ledger-api's` otherwise."""
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def and_list(xs: list) -> str:
    """`a`, `a and b`, `a, b and c`."""
    xs = list(xs)
    return " and ".join(xs) if len(xs) <= 2 else ", ".join(xs[:-1]) + " and " + xs[-1]


def verb(items: list, one: str, many: str) -> str:
    return one if len(items) == 1 else many

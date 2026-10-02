"""Generate the national synthetic catalog: 40 real US metros around the original SF sample.

    backend/.venv/Scripts/python backend/tools/gen_national_catalog.py --seed 20261002 --out backend/data/national

Deterministic for a given seed and scale. Writes catalog.json, aliases.json and catalog.meta.json
into --out, plus backend/data/catalog.meta.json for the SF catalog (which is read, never modified).
Names are drawn from frequency tables (US Census 2010 surnames, SSA first names); exact full-name
duplicates are never forced or prevented, only measured and reported.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
SF_CATALOG = DATA_DIR / "catalog.json"
SF_ALIASES = DATA_DIR / "aliases.json"

FULL_PROVIDERS = 5000
ROWS_RANGE = (120_000, 170_000)
OPTIONAL_TYPE_P = 0.5
ACCEPTING_P = 0.80
SITES_PER_PROVIDER = ((1, 55), (2, 30), (3, 12), (4, 3))
SPAN_P = 0.7  # share of multi-site providers in a paired metro whose last site is across the pair
CAPABILITY_RATES = (("imaging", 3 / 8), ("lab", 4 / 8), ("physical_therapy", 2 / 8), ("dental", 1 / 8),
                    ("surgery", 3 / 8))
CAPABILITY_ORDER = [c for c, _ in CAPABILITY_RATES]

SF_METRO = "san-francisco-ca"
OPHTHALMOLOGY_METROS = ("new-york-ny", "houston-tx", "boston-ma")
ZERO_IMAGING_METROS = ("albuquerque-nm", "salt-lake-city-ut", "new-orleans-la")
ZERO_DENTAL_METROS = ("raleigh-nc", "columbus-oh", "indianapolis-in", "kansas-city-mo", "cleveland-oh")
ADJACENT_PAIRS = (("san-francisco-ca", "oakland-ca"), ("dallas-tx", "fort-worth-tx"),
                  ("minneapolis-mn", "st-paul-mn"), ("washington-dc", "baltimore-md"))

# ---- geography --------------------------------------------------------------------------------
# The 8 original SF locations: (neighborhood, lat, lon, zip). Real coordinates of the neighborhood
# each name points at; "North Gate" and "Midtown" are not SF neighborhoods, so they get the closest
# real ones (Presidio Heights, Midtown Terrace).
SF_GEO = {
    "loc_000": ("Mission Bay", 37.7706, -122.3915, "94158"),
    "loc_001": ("Mission District", 37.7599, -122.4148, "94110"),
    "loc_002": ("North Beach", 37.8003, -122.4100, "94133"),
    "loc_003": ("Presidio Heights", 37.7886, -122.4466, "94118"),
    "loc_004": ("Downtown", 37.7880, -122.4075, "94108"),
    "loc_005": ("Midtown Terrace", 37.7530, -122.4530, "94131"),
    "loc_006": ("Sunset District", 37.7530, -122.4940, "94122"),
    "loc_007": ("Richmond District", 37.7800, -122.4780, "94121"),
}

# (id, name, state, area code, lat, lon, aliases, neighborhoods). One site per neighborhood line:
# "Name|lat|lon|zip3[|city[|state]]" (city/state given when the site is outside the core city).
METROS = [
    ("san-francisco-ca", "San Francisco", "CA", "415", 37.7749, -122.4194, ["sf", "san fran", "frisco"], ""),
    ("oakland-ca", "Oakland", "CA", "510", 37.8044, -122.2712, ["east bay"], """
        Downtown|37.8044|-122.2711|946
        Rockridge|37.8441|-122.2516|946
        Fruitvale|37.7748|-122.2241|946
        Berkeley|37.8716|-122.2727|947|Berkeley
        Alameda|37.7652|-122.2416|945|Alameda"""),
    ("san-jose-ca", "San Jose", "CA", "408", 37.3382, -121.8863, ["sj", "silicon valley"], """
        Downtown|37.3352|-121.8881|951
        Willow Glen|37.3083|-121.8990|951
        Evergreen|37.3197|-121.7660|951
        Almaden Valley|37.2200|-121.8620|951
        Santa Clara|37.3541|-121.9552|950|Santa Clara
        Sunnyvale|37.3688|-122.0363|940|Sunnyvale"""),
    ("los-angeles-ca", "Los Angeles", "CA", "213", 34.0522, -118.2437, ["la", "l a"], """
        Downtown|34.0407|-118.2468|900
        Hollywood|34.0928|-118.3287|900
        Koreatown|34.0618|-118.3004|900
        Echo Park|34.0782|-118.2606|900
        Boyle Heights|34.0339|-118.2054|900
        Westwood|34.0635|-118.4455|900
        South Los Angeles|33.9897|-118.2915|900
        Venice|33.9850|-118.4695|902
        Van Nuys|34.1867|-118.4490|914
        North Hollywood|34.1870|-118.3813|916
        Santa Monica|34.0195|-118.4912|904|Santa Monica
        Pasadena|34.1478|-118.1445|911|Pasadena
        Glendale|34.1425|-118.2551|912|Glendale
        Long Beach|33.7701|-118.1937|908|Long Beach
        Torrance|33.8358|-118.3406|905|Torrance
        Inglewood|33.9617|-118.3531|903|Inglewood"""),
    ("san-diego-ca", "San Diego", "CA", "619", 32.7157, -117.1611, ["sd"], """
        Downtown|32.7157|-117.1611|921
        Hillcrest|32.7480|-117.1660|921
        La Jolla|32.8328|-117.2713|920
        North Park|32.7406|-117.1295|921
        Mission Valley|32.7678|-117.1550|921
        Chula Vista|32.6401|-117.0842|919|Chula Vista
        Escondido|33.1192|-117.0864|920|Escondido"""),
    ("sacramento-ca", "Sacramento", "CA", "916", 38.5816, -121.4944, ["sac", "sactown"], """
        Downtown|38.5816|-121.4944|958
        Midtown|38.5730|-121.4790|958
        Natomas|38.6440|-121.5160|958
        Land Park|38.5440|-121.5040|958
        Elk Grove|38.4088|-121.3716|957|Elk Grove
        Roseville|38.7521|-121.2880|956|Roseville"""),
    ("seattle-wa", "Seattle", "WA", "206", 47.6062, -122.3321, [], """
        Downtown|47.6050|-122.3344|981
        Capitol Hill|47.6253|-122.3222|981
        Ballard|47.6687|-122.3843|981
        Queen Anne|47.6374|-122.3571|981
        University District|47.6615|-122.3138|981
        West Seattle|47.5667|-122.3868|981
        Bellevue|47.6101|-122.2015|980|Bellevue
        Renton|47.4829|-122.2171|980|Renton"""),
    ("portland-or", "Portland", "OR", "503", 45.5152, -122.6784, ["pdx"], """
        Downtown|45.5152|-122.6784|972
        Pearl District|45.5290|-122.6830|972
        Hawthorne|45.5121|-122.6250|972
        Alberta Arts District|45.5590|-122.6440|972
        St. Johns|45.5900|-122.7530|972
        Beaverton|45.4871|-122.8037|970|Beaverton"""),
    ("phoenix-az", "Phoenix", "AZ", "602", 33.4484, -112.0740, ["phx"], """
        Downtown|33.4484|-112.0740|850
        Arcadia|33.4980|-111.9850|850
        Ahwatukee|33.3400|-111.9840|850
        Maryvale|33.5050|-112.1760|850
        Deer Valley|33.6830|-112.1350|850
        Scottsdale|33.4942|-111.9261|852|Scottsdale
        Tempe|33.4255|-111.9400|852|Tempe
        Mesa|33.4152|-111.8315|852|Mesa
        Chandler|33.3062|-111.8413|852|Chandler
        Glendale|33.5387|-112.1860|853|Glendale"""),
    ("las-vegas-nv", "Las Vegas", "NV", "702", 36.1699, -115.1398, ["vegas"], """
        Downtown|36.1699|-115.1398|891
        Paradise|36.0972|-115.1467|891
        Summerlin|36.1580|-115.3320|891
        Spring Valley|36.1080|-115.2450|891
        Henderson|36.0395|-114.9817|890|Henderson
        North Las Vegas|36.1989|-115.1175|890|North Las Vegas"""),
    ("denver-co", "Denver", "CO", "303", 39.7392, -104.9903, [], """
        Downtown|39.7420|-104.9915|802
        Capitol Hill|39.7312|-104.9800|802
        Cherry Creek|39.7170|-104.9530|802
        Highlands|39.7620|-105.0110|802
        Central Park|39.7610|-104.8870|802
        Aurora|39.7294|-104.8319|800|Aurora
        Lakewood|39.7047|-105.0814|802|Lakewood"""),
    ("salt-lake-city-ut", "Salt Lake City", "UT", "801", 40.7608, -111.8910, ["slc", "salt lake"], """
        Downtown|40.7608|-111.8910|841
        Sugar House|40.7230|-111.8590|841
        The Avenues|40.7770|-111.8700|841
        West Valley City|40.6916|-112.0011|841|West Valley City"""),
    ("albuquerque-nm", "Albuquerque", "NM", "505", 35.0844, -106.6504, ["abq"], """
        Downtown|35.0844|-106.6504|871
        Nob Hill|35.0800|-106.6050|871
        Northeast Heights|35.1300|-106.5300|871"""),
    ("dallas-tx", "Dallas", "TX", "214", 32.7767, -96.7970, ["big d"], """
        Downtown|32.7801|-96.8005|752
        Uptown|32.8000|-96.8010|752
        Deep Ellum|32.7840|-96.7830|752
        Oak Lawn|32.8090|-96.8170|752
        Lake Highlands|32.8840|-96.7240|752
        Oak Cliff|32.7430|-96.8270|752
        Plano|33.0198|-96.6989|750|Plano
        Irving|32.8140|-96.9489|750|Irving
        Garland|32.9126|-96.6389|750|Garland
        Richardson|32.9483|-96.7299|750|Richardson"""),
    ("fort-worth-tx", "Fort Worth", "TX", "817", 32.7555, -97.3308, ["ft worth", "cowtown"], """
        Downtown|32.7555|-97.3308|761
        Near Southside|32.7330|-97.3240|761
        Cultural District|32.7480|-97.3640|761
        Arlington|32.7357|-97.1081|760|Arlington
        Keller|32.9346|-97.2517|762|Keller"""),
    ("houston-tx", "Houston", "TX", "713", 29.7604, -95.3698, ["h town"], """
        Downtown|29.7604|-95.3698|770
        Midtown|29.7420|-95.3800|770
        Montrose|29.7440|-95.3900|770
        The Heights|29.7980|-95.3980|770
        Medical Center|29.7070|-95.3970|770
        Alief|29.6960|-95.5950|770
        Uptown|29.7499|-95.4613|770
        Bellaire|29.7058|-95.4588|774|Bellaire
        Sugar Land|29.6197|-95.6349|774|Sugar Land
        Pasadena|29.6911|-95.2091|775|Pasadena
        The Woodlands|30.1658|-95.4613|773|The Woodlands
        Katy|29.7858|-95.8245|774|Katy"""),
    ("austin-tx", "Austin", "TX", "512", 30.2672, -97.7431, ["atx"], """
        Downtown|30.2672|-97.7431|787
        Riverside|30.2390|-97.7250|787
        Hyde Park|30.3070|-97.7320|787
        South Congress|30.2470|-97.7500|787
        East Austin|30.2640|-97.7200|787
        Mueller|30.2980|-97.7050|787
        Round Rock|30.5083|-97.6789|786|Round Rock
        Cedar Park|30.5052|-97.8203|786|Cedar Park"""),
    ("san-antonio-tx", "San Antonio", "TX", "210", 29.4241, -98.4936, ["satx"], """
        Downtown|29.4241|-98.4936|782
        Alamo Heights|29.4850|-98.4660|782
        Medical Center|29.5080|-98.5750|782
        Stone Oak|29.6400|-98.4900|782
        South San Antonio|29.3600|-98.5000|782
        New Braunfels|29.7030|-98.1245|781|New Braunfels"""),
    ("kansas-city-mo", "Kansas City", "MO", "816", 39.0997, -94.5786, ["kc"], """
        Downtown|39.0997|-94.5786|641
        Westport|39.0530|-94.5920|641
        Country Club Plaza|39.0420|-94.5930|641
        Northland|39.2000|-94.5700|641
        Overland Park|38.9822|-94.6708|662|Overland Park|KS
        Independence|39.0911|-94.4155|640|Independence"""),
    ("minneapolis-mn", "Minneapolis", "MN", "612", 44.9778, -93.2650, ["mpls"], """
        Downtown|44.9778|-93.2650|554
        Uptown|44.9490|-93.2980|554
        Northeast|45.0000|-93.2470|554
        Longfellow|44.9420|-93.2200|554
        Bloomington|44.8408|-93.2983|554|Bloomington
        Edina|44.8897|-93.3499|554|Edina"""),
    ("st-paul-mn", "St. Paul", "MN", "651", 44.9537, -93.0900, ["saint paul"], """
        Lowertown|44.9500|-93.0850|551
        Highland Park|44.9150|-93.1700|551
        Como|44.9800|-93.1400|551
        Woodbury|44.9239|-92.9594|551|Woodbury"""),
    ("chicago-il", "Chicago", "IL", "312", 41.8781, -87.6298, ["chi town", "chi"], """
        The Loop|41.8837|-87.6289|606
        River North|41.8920|-87.6340|606
        Lincoln Park|41.9214|-87.6513|606
        Lakeview|41.9400|-87.6530|606
        Wicker Park|41.9088|-87.6796|606
        Hyde Park|41.7943|-87.5907|606
        Pilsen|41.8560|-87.6560|606
        Chinatown|41.8520|-87.6320|606
        Uptown|41.9660|-87.6530|606
        Rogers Park|42.0100|-87.6700|606
        West Ridge|41.9990|-87.6950|606
        Evanston|42.0451|-87.6877|602|Evanston
        Oak Park|41.8850|-87.7845|603|Oak Park
        Naperville|41.7508|-88.1535|605|Naperville"""),
    ("detroit-mi", "Detroit", "MI", "313", 42.3314, -83.0458, ["motor city"], """
        Downtown|42.3314|-83.0458|482
        Midtown|42.3550|-83.0660|482
        Corktown|42.3310|-83.0700|482
        Hamtramck|42.3928|-83.0496|482|Hamtramck
        Dearborn|42.3223|-83.1763|481|Dearborn
        Southfield|42.4734|-83.2219|480|Southfield
        Troy|42.6064|-83.1498|480|Troy"""),
    ("st-louis-mo", "St. Louis", "MO", "314", 38.6270, -90.1994, ["saint louis", "stl"], """
        Downtown|38.6270|-90.1994|631
        Central West End|38.6440|-90.2600|631
        The Hill|38.6170|-90.2780|631
        Soulard|38.6080|-90.2100|631
        Clayton|38.6426|-90.3237|631|Clayton
        Chesterfield|38.6631|-90.5771|630|Chesterfield"""),
    ("indianapolis-in", "Indianapolis", "IN", "317", 39.7684, -86.1581, ["indy"], """
        Downtown|39.7684|-86.1581|462
        Broad Ripple|39.8670|-86.1420|462
        Fountain Square|39.7570|-86.1400|462
        Carmel|39.9784|-86.1180|460|Carmel
        Fishers|39.9568|-86.0134|460|Fishers"""),
    ("columbus-oh", "Columbus", "OH", "614", 39.9612, -82.9988, [], """
        Downtown|39.9612|-82.9988|432
        Short North|39.9790|-83.0040|432
        German Village|39.9480|-82.9940|432
        Clintonville|40.0300|-83.0150|432
        Dublin|40.0992|-83.1141|430|Dublin"""),
    ("cleveland-oh", "Cleveland", "OH", "216", 41.4993, -81.6944, [], """
        Downtown|41.4993|-81.6944|441
        University Circle|41.5080|-81.6050|441
        Ohio City|41.4840|-81.7070|441
        Lakewood|41.4820|-81.7982|441|Lakewood
        Parma|41.4048|-81.7229|441|Parma"""),
    ("nashville-tn", "Nashville", "TN", "615", 36.1627, -86.7816, [], """
        Downtown|36.1627|-86.7816|372
        East Nashville|36.1800|-86.7500|372
        The Gulch|36.1520|-86.7890|372
        Green Hills|36.1050|-86.8160|372
        Midtown|36.1520|-86.7950|372
        Franklin|35.9251|-86.8689|370|Franklin"""),
    ("atlanta-ga", "Atlanta", "GA", "404", 33.7490, -84.3880, ["atl"], """
        Downtown|33.7490|-84.3880|303
        Midtown|33.7810|-84.3830|303
        Buckhead|33.8380|-84.3790|303
        Virginia-Highland|33.7810|-84.3530|303
        Old Fourth Ward|33.7630|-84.3720|303
        West End|33.7360|-84.4130|303
        Decatur|33.7748|-84.2963|300|Decatur
        Marietta|33.9526|-84.5499|300|Marietta
        Duluth|34.0029|-84.1446|300|Duluth
        Sandy Springs|33.9304|-84.3733|303|Sandy Springs"""),
    ("charlotte-nc", "Charlotte", "NC", "704", 35.2271, -80.8431, ["clt"], """
        Uptown|35.2271|-80.8431|282
        South End|35.2120|-80.8590|282
        NoDa|35.2460|-80.8100|282
        Ballantyne|35.0530|-80.8480|282
        University City|35.3070|-80.7350|282
        Matthews|35.1168|-80.7237|281|Matthews"""),
    ("raleigh-nc", "Raleigh", "NC", "919", 35.7796, -78.6382, ["the triangle"], """
        Glenwood South|35.7860|-78.6460|276
        North Hills|35.8380|-78.6420|276
        Durham|35.9940|-78.8986|277|Durham
        Cary|35.7915|-78.7811|275|Cary
        Chapel Hill|35.9132|-79.0558|275|Chapel Hill"""),
    ("miami-fl", "Miami", "FL", "305", 25.7617, -80.1918, [], """
        Downtown|25.7743|-80.1937|331
        Brickell|25.7600|-80.1930|331
        Little Havana|25.7700|-80.2200|331
        Little Haiti|25.8300|-80.1920|331
        Kendall|25.6793|-80.3173|331
        Coral Gables|25.7215|-80.2684|331|Coral Gables
        Hialeah|25.8576|-80.2781|330|Hialeah
        Miami Beach|25.7907|-80.1300|331|Miami Beach
        Doral|25.8195|-80.3553|331|Doral
        Fort Lauderdale|26.1224|-80.1373|333|Fort Lauderdale"""),
    ("tampa-fl", "Tampa", "FL", "813", 27.9506, -82.4572, ["tampa bay"], """
        Downtown|27.9506|-82.4572|336
        Ybor City|27.9600|-82.4400|336
        Hyde Park|27.9370|-82.4730|336
        Westchase|28.0550|-82.6100|336
        St. Petersburg|27.7676|-82.6403|337|St. Petersburg
        Clearwater|27.9659|-82.8001|337|Clearwater
        Brandon|27.9378|-82.2859|335|Brandon"""),
    ("orlando-fl", "Orlando", "FL", "407", 28.5383, -81.3792, [], """
        Downtown|28.5383|-81.3792|328
        College Park|28.5700|-81.3900|328
        Lake Nona|28.4000|-81.2400|328
        Dr. Phillips|28.4500|-81.4900|328
        Winter Park|28.5999|-81.3392|327|Winter Park
        Kissimmee|28.2920|-81.4076|347|Kissimmee"""),
    ("new-orleans-la", "New Orleans", "LA", "504", 29.9511, -90.0715, ["nola"], """
        French Quarter|29.9584|-90.0644|701
        Uptown|29.9300|-90.1050|701
        Mid-City|29.9750|-90.0900|701
        Metairie|29.9841|-90.1529|700|Metairie"""),
    ("washington-dc", "Washington", "DC", "202", 38.9072, -77.0369, ["dc", "d c", "washington dc"], """
        Downtown|38.9007|-77.0290|200
        Capitol Hill|38.8899|-76.9905|200
        Georgetown|38.9097|-77.0654|200
        Adams Morgan|38.9215|-77.0422|200
        Navy Yard|38.8760|-77.0030|200
        Columbia Heights|38.9283|-77.0326|200
        Anacostia|38.8628|-76.9853|200
        Bethesda|38.9847|-77.0947|208|Bethesda|MD
        Silver Spring|38.9907|-77.0261|209|Silver Spring|MD
        Arlington|38.8816|-77.0910|222|Arlington|VA
        Alexandria|38.8048|-77.0469|223|Alexandria|VA"""),
    ("philadelphia-pa", "Philadelphia", "PA", "215", 39.9526, -75.1652, ["philly"], """
        Center City|39.9526|-75.1652|191
        Old City|39.9510|-75.1450|191
        Fishtown|39.9710|-75.1340|191
        University City|39.9522|-75.1932|191
        South Philadelphia|39.9250|-75.1700|191
        Chestnut Hill|40.0760|-75.2080|191
        Northeast Philadelphia|40.0550|-75.0450|191
        Cherry Hill|39.9348|-75.0307|080|Cherry Hill|NJ
        King of Prussia|40.0893|-75.3960|194|King of Prussia"""),
    ("new-york-ny", "New York", "NY", "212", 40.7128, -74.0060, ["nyc", "new york city"], """
        Midtown|40.7549|-73.9840|100
        Upper East Side|40.7736|-73.9566|100
        Upper West Side|40.7870|-73.9754|100
        Harlem|40.8116|-73.9465|100
        Washington Heights|40.8417|-73.9394|100
        Chelsea|40.7465|-74.0014|100
        Lower East Side|40.7150|-73.9843|100
        Financial District|40.7075|-74.0113|100
        Chinatown|40.7158|-73.9970|100
        Downtown Brooklyn|40.6955|-73.9890|112|Brooklyn
        Park Slope|40.6710|-73.9814|112|Brooklyn
        Williamsburg|40.7081|-73.9571|112|Brooklyn
        Bay Ridge|40.6264|-74.0299|112|Brooklyn
        Flushing|40.7675|-73.8331|113|Flushing
        Jackson Heights|40.7557|-73.8831|113|Jackson Heights
        Astoria|40.7644|-73.9235|111|Astoria
        Jamaica|40.7027|-73.7890|114|Jamaica
        Fordham|40.8615|-73.8905|104|Bronx"""),
    ("boston-ma", "Boston", "MA", "617", 42.3601, -71.0589, [], """
        Downtown|42.3555|-71.0605|021
        Back Bay|42.3503|-71.0810|021
        South End|42.3388|-71.0765|021
        Jamaica Plain|42.3097|-71.1151|021
        Dorchester|42.3016|-71.0676|021
        Longwood|42.3370|-71.1060|021
        Cambridge|42.3736|-71.1097|021|Cambridge
        Somerville|42.3876|-71.0995|021|Somerville
        Quincy|42.2529|-71.0023|021|Quincy
        Newton|42.3370|-71.2092|024|Newton"""),
    ("baltimore-md", "Baltimore", "MD", "410", 39.2904, -76.6122, ["bmore"], """
        Downtown|39.2904|-76.6122|212
        Fells Point|39.2830|-76.5930|212
        Mount Vernon|39.2980|-76.6150|212
        Hampden|39.3310|-76.6340|212
        Towson|39.4015|-76.6019|212|Towson
        Columbia|39.2037|-76.8610|210|Columbia"""),
]

STREETS = ("Main St", "Oak St", "Maple Ave", "Park Ave", "Washington Blvd", "Lincoln Ave", "Elm St", "Pine St",
           "Cedar St", "Lake St", "Hill St", "Center St", "Church St", "Market St", "Broadway", "1st Ave",
           "2nd St", "3rd St", "Grand Ave", "University Ave", "Medical Center Dr", "Highland Ave",
           "Jefferson St", "Madison Ave", "Franklin St", "Commerce St", "Ridge Rd", "Spring St")
SITE_SUFFIXES = (("Health Center", 40), ("Medical Group", 15), ("Family Clinic", 12), ("Community Clinic", 12),
                 ("Specialty Center", 10), ("Care Center", 11))
HOURS = (("Mon-Fri 8:00-17:00", 60), ("Mon-Fri 7:30-18:00", 10), ("Mon-Fri 9:00-17:00", 10),
         ("Mon-Sat 8:00-17:00", 10), ("Mon-Fri 7:00-19:00", 10))

# ---- names ------------------------------------------------------------------------------------
# US Census 2010 surnames in rank order (ranks 1..~1000). Weight per rank comes from the published
# per-100k frequencies at the anchors below, log-log interpolated between them. ":x" tags an ethnic
# association used for regional boosts, first-name pools and languages (see TAGS).
CENSUS_ANCHORS = ((1, 828.19), (2, 655.24), (3, 550.97), (4, 487.16), (5, 483.24), (6, 395.32), (7, 393.73),
                  (8, 378.45), (9, 371.19), (10, 359.40), (11, 353.68), (12, 296.47), (13, 285.11),
                  (14, 271.84), (15, 265.91), (20, 238.20), (30, 179.60), (40, 147.10), (50, 127.70),
                  (75, 92.0), (100, 64.5), (150, 44.0), (200, 32.5), (300, 22.0), (400, 16.6), (500, 13.3),
                  (700, 9.6), (1000, 6.7))

CENSUS_SURNAMES = """
Smith Johnson Williams Brown Jones Garcia:h Miller Davis Rodriguez:h Martinez:h Hernandez:h Lopez:h
Gonzalez:h Wilson Anderson:s Thomas Taylor Moore Jackson Martin Lee Perez:h Thompson White Harris
Sanchez:h Clark Ramirez:h Lewis Robinson Walker Young Allen King Wright Scott Torres:h Nguyen:v Hill
Flores:h Green Adams Nelson:s Baker Hall Rivera:h Campbell Mitchell Carter Roberts Gomez:h Phillips Evans
Turner Diaz:h Parker Cruz:h Edwards Collins Reyes:h Stewart Morris Morales:h Murphy:q Cook Rogers
Gutierrez:h Ortiz:h Morgan Cooper Peterson:s Bailey Reed Kelly:q Howard Ramos:h Kim:k Cox Ward
Richardson Watson Brooks Chavez:h Wood James Bennett Gray Mendoza:h Ruiz:h Hughes Price Alvarez:h
Castillo:h Sanders Patel:g Myers Long Ross Foster Jimenez:h Powell Jenkins Perry Russell Sullivan:q
Bell Coleman Butler Henderson Barnes Gonzales:h Fisher Vasquez:h Simmons Romero:h Jordan Patterson
Alexander Hamilton Graham Reynolds Griffin Wallace Moreno:h West Cole Hayes Bryant Herrera:h Gibson
Ellis Tran:v Medina:h Aguilar:h Stevens Murray Ford Castro:h Marshall Owens Harrison Fernandez:h
McDonald Woods Washington Kennedy:q Wells Vargas:h Henry Chen:c Freeman Webb Tucker Guzman:h Burns
Crawford Olson:s Simpson Porter Hunter Gordon Mendez:h Silva:o Shaw Snyder Mason Dixon Munoz:h Hunt
Hicks Holmes Palmer Wagner Black Robertson Boyd Rose Stone Salazar:h Fox Warren Mills Meyer Rice
Schmidt Garza:h Daniels Ferguson Nichols Stephens Soto:h Weaver Ryan:q Gardner Payne Grant Dunn
Kelley:q Spencer Hawkins Arnold Pierce Vazquez:h Hansen:s Peters Santos:f Hart Bradley Knight Elliott
Cunningham Duncan Armstrong Hudson Carroll:q Lane Riley:q Andrews Alvarado:h Ray Delgado:h Berry
Perkins Hoffman Johnston Matthews Pena:h Richards Contreras:h Willis Carpenter Lawrence Sandoval:h
Guerrero:h George Chapman Rios:h Estrada:h Ortega:h Watkins Greene Nunez:h Wheeler Valdez:h Harper
Burke:q Larson:s Santiago:h Maldonado:h Morrison Franklin Carlson:s Austin Dominguez:h Carr Lawson
Jacobs O'Brien:q Lynch:q Singh:u Vega:h Bishop Montgomery Oliver Jensen:s Harvey Williamson Gilbert
Dean Sims Espinoza:h Howell Li:c Wong:c Reid Hanson:s Le:v McCoy Garrett Burton Fuller Wang:c Weber
Welch Rojas:h Lucas Marquez:h Fields Park:k Yang:c Little Banks Padilla:h Day Walsh:q Bowman Schultz
Luna:h Fowler Mejia:h Davidson Acosta:h Brewer May Holland Juarez:h Newman Pearson Curtis Cortez:h
Douglas Schneider Joseph Barrett Navarro:h Figueroa:h Keller Avila:h Wade Molina:h Stanley Hopkins
Campos:h Barnett Bates Chambers Caldwell Beck Lambert Miranda:h Byrd Craig Ayala:h Lowe Frazier
Powers Neal Leonard Gregory Carrillo:h Sutton Fleming Rhodes Shelton Schwartz Norris Jennings Watts
Duran:h Walters Cohen McDaniel Moran Parks Steele Vaughn Becker Holt DeLeon:h Barker Terry Hale Leon:h
Benson Haynes Horton Miles Lyons Pham:v Graves Bush Thornton Wolfe Warner Cabrera:h McKinney
Mann Zimmerman Dawson Lara:h Fletcher Page McCarthy:q Love Robles:h Cervantes:h Solis:h Erickson:s
Reeves Chang:c Klein Salinas:h Fuentes:h Baldwin Daniel Simon Velasquez:h Hardy Higgins Aguirre:h
Lin:c Cummings Chandler Sharp Barber Bowen Ochoa:h Dennis Robbins Liu:c Ramsey Francis Griffith
Paul Blair O'Connor:q Cardenas:h Pacheco:h Cross Calderon:h Quinn:q Moss Swanson:s Chan:c Rivas:h
Khan:a Rodgers Serrano:h Fitzgerald:q Rosales:h Stevenson Christensen:s Manning Gill:u Curry McLaughlin:q
Harmon McGee Gross Doyle:q Garner Newton Burgess Reese Walton Blake Trujillo:h Adkins Brady:q
Goodman Roman:h Webster Goodwin Fischer Huang:c Potter Delacruz:f Montoya:h Todd Wu:c Hines
Mullins Castaneda:h Malone Cannon Tate Mack Sherman Hubbard Hodges Zhang:c Guerra:h Wolf Valencia:h
Saunders Franco:h Rowe Gallagher:q Farmer Hammond Hampton Townsend Ingram Wise Gallegos:h Clarke
Barton Schroeder Maxwell Waters Logan Camacho:h Strickland Norman Person Colon:h Parsons Frank
Harrington Glover Osborne Buchanan Casey:q Floyd Patton Ibarra:h Ball Tyler Suarez:h Bowers Orozco:h
Salas:h Cobb Gibbs Andrade:h Bauer Conner Moody Escobar:h McGuire Lloyd Mueller Hartman French
Kramer McBride Pope Lindsey Velazquez:h Norton McCormick Sparks Flynn:q Yates Hogan Marsh Macias:h
Villanueva:f Zamora:h Pratt Stokes Owen Ballard Lang Brock Villarreal:h Charles Drake Barrera:h
Cain Patrick Pineda:h Burnett Mercado:h Santana:h Shepherd Bautista:f Ali:a Shaffer Lamb Trevino:h
McKenzie Hess Olsen:s Cochran Morton Nash Wilkins Petersen:s Briggs Shah:g Roth Nicholson
Holloway Lozano:h Rangel:h Flowers Hoover Short Arias:h Mora:h Valenzuela:h Bryan Meyers Weiss
Underwood Bass Greer Summers Houston Carson Morrow Clayton Whitaker Decker Yoder Collier Zuniga:h
Carey Wilcox Melendez:h Poole Roberson Larsen:s Conley Davenport Copeland Massey Lam:c Huff Rocha:o
Cameron Jefferson Hood Monroe Anthony Pittman Huynh:v Randall Singleton Kirk Combs Mathis Christian
Skinner Bradford Richard Galvan:h Wall Boone Kirby Wilkinson Bridges Bruce Atkinson Velez:h Meza:h
Roy Vincent York Hodge Villa:h Abbott Allison Tapia:h Gates Chase Sosa:h Sweeney:q Farrell:q Wyatt
Dalton Horn Barron:h Phelps Yu:c Dickerson Heath Foley:q Atkins Mathews Bonilla:h Acevedo:h Benitez:h
Zavala:h Hensley Glenn Cisneros:h Harrell Shields Rubio:h Choi:k Huffman Boyer Garrison Arroyo:h
Bond Kane:q Hancock Callahan:q Dillon:q Cline Wiggins Grimes Arellano:h Melton O'Neill:q Savage Ho:v
Beltran:h Pitts Parrish Ponce:h Rich Booth Koch Golden Ware Brennan:q McDowell Marks Cantu:h Humphrey
Baxter Sawyer Clay Tanner Hutchinson Kaur:u Berg:s Wiley Gilmore Russo:t Villegas:h Hobbs Keith
Wilkerson Ahmed:a Beard McClain Montes:h Mata:h Rosario:h Vang Walter Henson O'Neal Mosley McClure
Beasley Stephenson Snow Huerta:h Preston Vance Barry:q Johns Eaton Blackwell Dyer Prince Macdonald
Solomon Guevara:h Stafford English Hurst Woodard Cortes:h Shannon:q Kemp Nolan:q McCullough Merritt
Murillo:h Moon:k Salgado:h Strong Kline Cordova:h Barajas:h Roach Rosas:h Winters Jacobson:s Lester
Knox Bullock Kerr Leach Meadows Davila:h Orr Whitehead Pruitt Kent Conway:q McKee Barr David DeJesus:h
Marin:h Berger McIntyre Blankenship Gaines Palacios:h Cuevas:h Bartlett Durham Dorsey McCall
O'Donnell:q Stein Browning Stout Lowery Sloan McLean Hendricks Calhoun Sexton Chung:k Gentry Hull
Duarte:h Ellison Nielsen:s Gillespie Buck Middleton Sellers Leblanc:n Esparza:h Hardin Bradshaw
McIntosh Howe Livingston Frost Glass Morse Knapp Herman Stark Bravo:h Noble Spears Weeks Corona:h
Frederick Buckley McFarland Hebert:n Enriquez:h Hickman Quintero:h Randolph Schaefer Walls Trejo:h
House Reilly:q Pennington Michael Conrad Giles Benjamin Crosby Fitzpatrick:q Donovan:q Mays Mahoney:q
Valentine Raymond Medrano:h Hahn McMillan Small Bentley Felix:h Peck Lucero:h Boyle:q Hanna:a Pace
Rush Hurley:q Harding McConnell Bernal:h Nava:h Ayers Everett Ventura:h Avery Pugh Mayer Bender
McMahon:q McCarty:q Kaiser Bean Herring Dougherty Joyner Lott Crane Kidd Cherry Holder Benton Odom
Nixon Whitley Cooley Workman Rollins Holcomb Lowry Dickson Blevins Bray Marlow Petty Valle:h Leal:h
Avalos:h Galvez:h Sierra:h Peralta:h Navarrete:h Correa:h Alonso:h Merchant Krueger Sweet Byers Mayo
Ewing Riddle Duke Abrams Waller Ashley Sheppard Booker Wolff Lindsay
"""

# Real but less common US surnames (Census ranks beyond ~1000, plus physician-workforce names).
# Together they carry the mass the top-1000 table does not cover, spread evenly.
TAIL_SURNAMES = """
Abernathy Ackerman Adair Agnew Ahern:q Albright Aldridge Alford Allred Alston Ambrose Amos Ashby Ashford
Ashworth Atwood Aycock Babcock Bachman Bader Bagley Bair Bales Ballinger Bancroft Barlow Barnhart Barr
Bartholomew Batchelor Baumann Beal Beatty Beaver Bedford Beebe Belcher Bellamy Benner Bergman:s Bertrand:n
Betts Bingham Birch Bixby Blackburn Blanchard:n Bledsoe Bloom Blum Boggs Bolton Bonner Borden Bosworth
Boucher:n Bourne Boutin:n Bowden Bowles Boyce Brandt Bratton Braun Breen:q Brennan? Brewster Bright Brinkley
Britt Brody Bronson Brooker Brough Buckner Buckingham Bunch Burch Burk Burris Burrows Bushnell Cagle Caldera:h
Calloway Calvert Canfield Cantrell Carmichael Carney:q Caruso:t Carver Cassidy:q Catalano:t Chaney
Chapin Cheek Childers Chisholm Christie Clancy:q Clements Clifton Cobbs Coffey Colby Colvin Compton Connolly:q
Conroy:q Cormier:n Cornell Costa:o Costello:t Cotton Coughlin:q Courtney Covington Crabtree Crandall Creech
Crockett Cromwell Crouch Crowe Crowley:q Culver Cutler Dahl:s Dailey Darby Darden Daugherty Deangelis:t
Delaney:q Demarco:t Denton Desai:g Devlin:q Dewitt Dickinson Dodson Doherty:q Donahue:q Donnelly:q
Dooley:q Dowd:q Downey:q Drummond Dudley Duffy:q Dugan:q Dunbar Dunlap Durant Eastman Eckert Edmonds
Ehrlich Elder Ellington Ellsworth Emerson Engel Engstrom:s Esposito:t Eubanks Fagan:q Fairbanks Falcone:t
Farley:q Faulkner Fay Feeney:q Feldman Fenton Ferraro:t Finch Finley Fiore:t Fitch Flanagan:q Fleischer
Forbes Forrest Fortier:n Foss Fowler? Frey Friedman Fritz Fry Fulton Funk Gaffney:q Gagnon:n Galloway
Gamble Gannon:q Gardiner Garland Gaudet:n Geiger Gerber Gilliam Giordano:t Glaser Gleason:q Goff Goldberg
Goldstein Gorman:q Gould Grady:q Graff Granger Grantham Greenberg Gregg Grenier:n Griggs Grossman Guidry:n
Gunn Haas Hadley Hagen:s Haley Halverson:s Hamlin Hammer Haney Hargrove Harlan Harmon? Hatch Hawley
Hayden Healy:q Heller Hendrix Hennessy:q Herzog Hester Hickey:q Hinton Hirsch Hitchcock Holbrook
Holden Hollis Holman Holt? Hooper Horowitz Hough Hubert:n Hughey Hyde Irwin Isaacs Jaffe Jarvis Jeffries
Jernigan Joyce:q Judd Kaplan Katz Kaufman Keane:q Kearney:q Keating:q Keegan:q Keenan:q Kendall Kendrick
Kenney:q Kessler Kilgore Kimball Kincaid Kinney Kirkland Kirkpatrick Kleinman Knowles Koenig Kohler Kovach
Krause Kruger Kuhn Lachance:n Lafleur:n Lambertson Landry:n Langley Lapointe:n Larkin:q Latham Lavoie:n
Lawler:q Leary:q Leavitt Lehman Lemieux:n Lennon:q Levesque:n Levine Levy Lindberg:s Lindquist:s
Lindstrom:s Lipscomb Lockhart Loftus:q Lombardi:t Lombardo:t Lord Lowell Lucero? Ludwig Lund:s Lundgren:s
Lyman Mabry Madden:q Magnuson:s Maguire:q Mahler Major Malloy:q Mancini:t Manley Marino:t Marquardt
Martell Mattson:s McAllister McCabe:q McCann:q McCartney McCaskill McClellan McCord McCracken McDermott:q
McDonough:q McGinnis:q McGovern:q McGrath:q McHugh:q McKenna:q McLeod McManus:q McNally:q McNamara:q
McNeil Meehan:q Mercer Merrill Messina:t Metcalf Milano:t Millard Milligan Moeller Monahan:q Montague
Mooney:q Moreau:n Moriarty:q Morin:n Morrissey:q Mosher Moyer Mulligan:q Murdock Musgrove Nadeau:n Nagle
Napier Nash? Needham Nesbitt Neumann Nevins Newell Newsome Noonan:q Nordstrom:s Norwood Nugent:q Nye
Oakley Ogden Olmstead Olsson:s Osgood Ouellette:n Overton Pagano:t Paquette:n Parent:n Parnell Partridge
Pelletier:n Pemberton Pendleton Perrin Pettit Pfeiffer Pickett Pike Pollard Poirier:n Pope? Portman
Posey Prentice Prescott Pressley Proctor Pulaski:l Putnam Quigley:q Quinlan:q Rademacher Rafferty:q
Rainey Ramsay Rankin Ratliff Rawlings Redmond:q Regan:q Reich Rendon:h Riggs Rinaldi:t Ritter Rizzo:t
Roche:q Rockwell Rooney:q Rosen Rosenberg Rosenthal Rossi:t Rourke:q Rowland Rudd Ruggiero:t Rutledge
Ryder Sabin Salerno:t Sampson Sanford Santoro:t Sargent Saxon Scanlon:q Schaffer Schiller Schofield
Schuster Schwab Seaton Seidel Shapiro Sheehan:q Sheridan:q Sherwood Shirley Shore Siegel Silverman
Simms Skelton Slater Slattery:q Sorensen:s Spangler Spence Sprague Stack Stanton Steen Steiner
Sterling Stoddard Strand:s Stratton Sturgeon Sundberg:s Swift Talbot Tanaka:j Tatum Teague Thayer
Thibodeaux:n Thorpe Tierney:q Tobin:q Toomey:q Townsend? Tracy Trahan:n Tremblay:n Trent Tuttle Tyson
Upton Vail Vaughan Vick Voss Wadsworth Waldron Walling Walsh? Warwick Waterman Watt Weinberg Weinstein
Wendt Westbrook Whalen:q Wheaton Whelan:q Whitfield Whitman Whitney Wilder Winslow Wolcott Woodward
Worley Wright? Yarbrough Yount Zeller Ziegler Zimmer
Kowalski:l Nowak:l Wisniewski:l Kaminski:l Lewandowski:l Zielinski:l Szymanski:l Wozniak:l Kozlowski:l
Jankowski:l Mazur:l Krawczyk:l Piotrowski:l Grabowski:l Pawlowski:l Michalski:l Nowicki:l Adamski:l
Dudek:l Zajac:l Wieczorek:l Jablonski:l Majewski:l Olszewski:l Stepien:l Malinowski:l Sobczak:l
Novak Horvath Kovacs Toth Nagy Szabo Molnar Varga Dvorak Svoboda Cerny Prochazka
Ivanov:r Petrov:r Smirnov:r Volkov:r Sokolov:r Popov:r Lebedev:r Kozlov:r Novikov:r Morozov:r Pavlov:r
Fedorov:r Orlov:r Kuznetsov:r Romanov:r Belov:r Vasiliev:r Zaitsev:r Medvedev:r Gordon? Rabinovich:r
Kaplanov? Levin Shulman Abramson Feinberg Lieberman Rubin Goldman Bernstein Klein? Weiss? Stern Wexler
Papadopoulos:e Pappas:e Georgiou:e Nikolaidis:e Constantine:e Demos:e Kostas:e Galanis:e Karras:e
Alexiou:e Theodorou:e Christakis:e Andreou:e Stavros:e Mavros:e Vlahos:e
Romano:t Ricci:t Bruno:t Gallo:t Conti:t DeLuca:t Mancuso:t Greco:t Barbieri:t Moretti:t Colombo:t
Ferrari:t Leone:t Fontana:t Sorrentino:t Vitale:t Bianchi:t Marchetti:t Palumbo:t Amato:t Orlando:t
Carbone:t Cirillo:t Russo? Battaglia:t Guerrieri:t Lombardozzi? Pellegrino:t DeSantis:t Caputo:t
Almeida:o Pereira:o Ferreira:o Oliveira:o Sousa:o Carvalho:o Gomes:o Martins:o Teixeira:o Cardoso:o
Medeiros:o Furtado:o Pacheco? Rebelo:o Viveiros:o Amaral:o Correia:o Moniz:o Tavares:o Raposo:o
Nakamura:j Yamamoto:j Watanabe:j Takahashi:j Kobayashi:j Ito:j Sato:j Suzuki:j Yamada:j Sasaki:j
Kato:j Yoshida:j Matsumoto:j Inoue:j Kimura:j Hayashi:j Shimizu:j Yamaguchi:j Mori:j Ikeda:j Hashimoto:j
Ishikawa:j Ogawa:j Okada:j Fujii:j Nishimura:j Fukuda:j Ota:j Miura:j Fujita:j Okamoto:j Matsuda:j
Nakagawa:j Nakano:j Harada:j Ono:j Tamura:j Takeuchi:j Kaneko:j Wada:j Higa:j Oshiro:j Kaneshiro:j
Zhou:c Zhao:c Xu:c Sun:c Ma:c Zhu:c Hu:c Guo:c He:c Gao:c Lin? Luo:c Zheng:c Liang:c Xie:c Song:c
Tang:c Feng:c Deng:c Han:c Cao:c Peng:c Zeng:c Xiao:c Tian:c Dong:c Pan:c Yuan:c Cai:c Jiang:c Yu?
Du:c Ye:c Cheng:c Wei:c Su:c Lu:c Ding:c Shen:c Ren:c Yao:c Lui:c Fong:c Leung:c Cheung:c Lau:c
Kwan:c Tsang:c Yip:c Chow:c Mak:c Tam:c Kwok:c Lo:c Szeto:c Hung:c Chiu:c Tsai:c Hsu:c Kuo:c
Liao:c Chao:c Shih:c Yeh:c Tseng:c Chu:c Lai:c Hsieh:c Kao:c Teng:c Fung:c Woo:c Louie:c Quan:c
Pham? Hoang:v Phan:v Vu:v Vo:v Dang:v Bui:v Do:v Ngo:v Duong:v Ly:v Truong:v Dinh:v Mai:v Trinh:v
Lam? Luong:v Nguyen? Cao? Ha:v Ta:v Lieu:v Quach:v Thai:v Diep:v Doan:v Tong:v Kieu:v La:v Chau:v
Lee? Kang:k Cho:k Yoon:k Jang:k Lim:k Han? Shin:k Seo:k Kwon:k Hwang:k Ahn:k Song? Jeon:k Hong:k Ko:k
Moon? Yang? Baek:k Heo:k Nam:k Noh:k Ha? Kwak:k Sung:k Cha:k Joo:k Ryu:k Jin:k Byun:k Oh:k Bae:k
Jung:k Choe:k Paik:k Yun:k Chun:k Hahn? Suh:k Rhee:k
Mehta:g Desai? Joshi:i Kumar:i Sharma:i Gupta:i Reddy:d Rao:d Iyer:d Krishnan:d Nair:d Menon:d
Pillai:d Subramanian:d Venkatesan:d Raman:d Srinivasan:d Raghavan:d Natarajan:d Chandrasekhar:d
Agarwal:i Agrawal:i Jain:i Bansal:i Goel:i Malhotra:u Kapoor:u Khanna:u Chopra:u Bhatia:u Sethi:u
Arora:u Sandhu:u Dhillon:u Grewal:u Sidhu:u Brar:u Sekhon:u Bajwa:u Mann? Chaudhary:i Chaudhry:a
Mishra:i Pandey:i Tiwari:i Dubey:i Shukla:i Trivedi:g Vyas:g Bhatt:g Dave:g Parikh:g Amin:g Modi:g
Thakkar:g Pandya:g Shah? Doshi:g Kothari:g Gandhi:g Sheth:g Chokshi:g Mistry:g Panchal:g Naik:i
Kulkarni:i Deshpande:i Patil:i Kamath:d Shetty:d Hegde:d Bhat:d Prasad:d Murthy:d Sastry:d Varma:d
Banerjee:i Chatterjee:i Mukherjee:i Ghosh:i Bose:i Das:i Dutta:i Sen:i Roy? Chakraborty:i Saha:i
Mahmoud:a Hassan:a Hussein:a Ibrahim:a Abdullah:a Haddad:a Khoury:a Saleh:a Nasser:a Farah:a Aziz:a
Rahman:a Hamdan:a Mansour:a Najjar:a Sabbagh:a Habib:a Kassab:a Bitar:a Daher:a Hakim:a Jaber:a
Karam:a Malik:a Qureshi:a Siddiqui:a Hashmi:a Rizvi:a Mirza:a Baig:a Sheikh:a Syed:a Hussain:a
Akhtar:a Iqbal:a Butt:a Rashid:a Elias:a Shamoun:a Yousif:a Kashat:a
Tehrani:w Hosseini:w Rahimi:w Karimi:w Mohammadi:w Ahmadi:w Rezaei:w Moradi:w Jafari:w Sadeghi:w
Ghorbani:w Shirazi:w Esfahani:w Farahani:w Nazari:w Azizi:w Kazemi:w Abbasi:w Rostami:w Tabrizi:w
Petrosyan:m Sarkisian:m Hovsepian:m Avakian:m Grigoryan:m Hakobyan:m Manukyan:m Harutyunyan:m
Karapetyan:m Gasparian:m Mardirossian:m Kevorkian:m Bedrosian:m Gharibian:m Arakelian:m Simonian:m
Okafor:b Okonkwo:b Adeyemi:b Okeke:b Eze:b Nwosu:b Adebayo:b Oyelaran:b Obi:b Chukwu:b Ogunleye:b
Afolabi:b Balogun:b Okoro:b Uzoma:b Onyeka:b Ezeh:b Nwachukwu:b Olaniyan:b Akinola:b Ogundipe:b
Mensah:b Asante:b Owusu:b Boateng:b Osei:b Addo:b Tesfaye:b Haile:b Bekele:b Girma:b Kebede:b
Mendoza? Aquino:f Manalo:f Pascual:f Mercado? Soriano:f Ramos? Dizon:f Bernardo:f Tolentino:f
Gonzaga:f Javier:f Lacson:f Macapagal:f Panganiban:f Salvador:f Samson:f Sison:f Tan:f Yap:f
Ocampo:f Agustin:f Abad:f Cabrera? Domingo:f Galang:f Ignacio:f Lagman:f Magno:f Quiambao:f
Cuellar:h Esquivel:h Ledesma:h Olivares:h Ontiveros:h Quintana:h Saldana:h Sepulveda:h Tellez:h
Urbina:h Villalobos:h Zepeda:h Alcala:h Becerra:h Cardona:h Carrasco:h Cervantez:h Escamilla:h
Galindo:h Gamez:h Granados:h Hinojosa:h Jaramillo:h Lira:h Loera:h Lucio:h Madrigal:h Magana:h
Montalvo:h Montano:h Nieves:h Ojeda:h Olvera:h Oropeza:h Pantoja:h Paredes:h Portillo:h Prado:h
Quiroz:h Renteria:h Rocha? Saenz:h Sauceda:h Segura:h Solano:h Tamez:h Toledo:h Uribe:h Vela:h
Benavides:h Bustamante:h Ceballos:h Echeverria:h Espinosa:h Fajardo:h Iglesias:h Lugo:h Maestas:h
Baca:h Archuleta:h Chacon:h Gurule:h Lujan:h Montano? Romero? Sisneros:h Tafoya:h Trujillo? Ulibarri:h
Gaspard:n Jean:n Pierre:n Joseph? Celestin:n Dorval:n Etienne:n Fleurant:n Jean-Baptiste:n Louis:n
Toussaint:n Laguerre:n Charlemagne:n Desir:n
Lindgren:s Nyberg:s Sjoberg:s Holmberg:s Engberg:s Lindahl:s Hedlund:s Sandberg:s Bjork:s Ostrom:s
Haugen:s Solberg:s Moen:s Dahlberg:s Hovland:s Aune:s Rudd? Thorson:s Iverson:s Gunderson:s Halvorsen:s
Knutson:s Ness:s Rasmussen:s Mortensen:s Andersen:s Kristensen:s Madsen:s Sorenson:s Thorsen:s
Aiken Akers Albers Alcorn Alden Alder Allard Allman Alvey Amsden Anson Appleby Archer Arden Armitage Arnett
Asher Ashcraft Askew Atherton Atwell Austen Avant Axelrod Ayres Bachelder Backus Bagwell Bainbridge Baird Bakker Balch
Baldridge Ballantine Bannister Barclay Barfield Barksdale Barnard Barnum Barrow Bartley Bascom Bassett Batson Baugh Baxley Beall
Beaman Beauchamp Beckett Beckham Beckman Beecher Belk Bellows Benfield Bennington Bentz Berkley Berman Berryman Bevan Bigelow
Biggs Birdsong Bissell Blackman Blackstone Blaine Blakely Blalock Bland Bliss Blodgett Blount Bloomfield Boatwright Bodine Bogart
Boland Bollinger Bolling Bonham Bost Bostic Boswell Boudreau Bowie Bowling Boyett Brackett Bradbury Braden Bragg Brainard
Bramlett Branch Brannon Brantley Braswell Breckenridge Breedlove Brenner Brewington Bridger Brinson Briscoe Brittain Broadbent Brockman Bromley
Brookshire Broughton Brownell Brownlee Broyles Bruner Brunson Bryson Buchholz Buckland Buell Buford Bumgarner Burleson Burnside Burrell
Burroughs Burt Bussey Butterfield Buxton Byrne Cabot Cadwell Calder Callaway Camden Camp Canady Capps Cardwell Carlisle
Carlton Carmack Carnes Carrington Cartwright Caskey Castleberry Caudill Causey Chadwick Chamberlain Champion Chesney Chilton Chipman Church
Churchill Clapp Claybrook Clement Cleveland Clifford Coates Coburn Cochrane Cody Coffman Cogswell Coker Colburn Collard Colley
Colquitt Conklin Connell Cooke Coombs Corbett Corbin Corley Cornwell Cosby Cottrell Coulter Covey Cowan Cowley Crain
Cranston Crawley Creighton Crenshaw Crews Criswell Crocker Crowder Crump Culbertson Cullen Culp Cundiff Currie Cushing Custer
Dabney Dahlgren Dale Daley Dalrymple Danforth Darling Darnell Davey Dawes Deal Dearborn Deaton Deering Delk Dempsey
Denham Denney Denning Derby Devine Dewey Dickens Dickey Dill Dillard Dinsmore Dobbins Dockery Dodd Dodge Dolan
Donaldson Dorman Dorr Doss Dotson Doty Dowling Downing Drew Driscoll Dryden Dubois Duckworth Dumont Dunaway Dunham
Dunning Dupree Durbin Dutton Dwyer Dykes Eads Earle Easley Eberhardt Echols Eddy Edgar Edison Eggleston Elmore
Ely Embry Emery Endicott England Ennis Epperson Epps Erwin Eskridge Estes Etheridge Everhart Ewell Fairchild Fairfax
Falk Fancher Fann Farnsworth Farris Faulk Fawcett Felton Fenwick Ferrell Fielder Fife Fillmore Fincher Fink Fishburn
Fiske Fitts Flagg Fleck Flint Flood Fogarty Folger Folsom Foote Forde Forman Forsyth Fortune Foust Foy
Frame Frankel Franks Fraser Freed Freedman Freer Frick Friend Frye Fugate Fullerton Fulmer Furman Gaddis Gage
Galbraith Gale Gallant Gant Garfield Garvey Gaskin Gatlin Gay Gerhardt Gibbons Gifford Gilchrist Gillette Gilliland Gilman
Gilpin Gist Givens Gladstone Glasser Glazer Glick Glynn Goddard Godfrey Godwin Goldsmith Gooch Goode Goodrich Goodson
Gore Gorham Gossett Gottlieb Grace Grafton Grayson Greenfield Greenwood Gresham Grier Griswold Grove Grover Guest Guthrie
Gwinn Hackett Hagan Hager Haggard Haines Halbert Halsey Ham Hamblin Hamm Hand Handley Hanley Hanes Hankins
Hannon Hardaway Hardesty Hardwick Hargis Harkins Harley Harlow Harned Hartley Hartsfield Harwell Haskell Haskins Hastings Hatcher
Hatfield Haverty Hawk Hawthorne Hay Haynie Hays Hazard Head Heard Hearn Heaton Hedrick Heflin Hefner Helms
Hemphill Henley Hennessey Herndon Herrick Hewitt Hibbard Hickok Highsmith Hilliard Hilton Hinkle Hinson Hobart Hobson Hodgson
Hogue Holcombe Holley Holliday Hollingsworth Holloman Holton Honeycutt Hooker Hooks Hope Hopper Hornsby Horne Horner Horsley
Horst Hoskins Hostetler Houck Houser Howland Hoyt Hubbell Huber Huddleston Hudgins Huggins Hulse Hume Humphries Hunnicutt
Hurd Huston Hutchins Hutton Ingle Ingalls Inman Irby Isbell Ivey Jacobsen Jameson Jamison Jansen Jarrett Jeter
Jewell Jolly Jorgensen Judge Kaminsky Kay Kearns Keeler Keen Keene Keil Kellogg Kemper Kennard Kersey Kesler
Ketchum Key Kilpatrick Kimbrough Kingsley Kinsey Kirchner Kitchen Kite Knott Kunkel Kurtz Lacy Ladd Laird Lake
Lamar Lamont Landis Lanier Lankford Larue Lassiter Lattimore Laughlin Lavender Layne Leake Ledford Lefebvre Leggett Leland
Lemon Lenz Lerner Lett Lewin Lightfoot Lilly Lind Lindley Linton Lipton Lister Litton Lively Locke Lockwood
Loftin Logue Longo Loomis Loveless Lovett Lowman Loy Luce Lumpkin Lusk Lyle Lynn Mabe Macey Mackey
Maddox Magee Mahan Maier Main Mallory Maloney Manes Mangum Mansfield Marcus Markham Marlowe Marr Marrs Massengale
Mathers Maupin Mauldin Maynard Mayfield McAdams McAfee McArthur McBee McCain McCaleb McCauley McClendon McCloud McCollum McCrary
McCurdy McCutcheon McElroy McEwen McFadden McGill McGowan McGregor McKay McKeever McKinley McKnight McLain McLendon McMurray McNair
McNeal McPherson McQueen McRae McVay Meacham Mead Meador Means Medley Meeks Meier Melvin Mendenhall Merriman Messer
Metz Michaels Middlebrook Milburn Milner Minor Mize Moffett Moffitt Moll Monk Moorman Morehead Morey Morrell Mortimer
Moseley Motley Mott Moulton Mount Muir Mundy Munn Munson Murdoch Musser Myrick Nall Neely Neff Nesmith
Nettles Newberry Newcomb Newkirk Newsom Nicholls Noel Nunnally Oakes Oates Odell Ogle Oldham Oliphant Orme Osborn
Osteen Otis Overby Owings Padgett Painter Palmore Pardue Parham Parkinson Parr Pate Patten Paxton Peabody Peacock
Pearce Peden Peek Pegram Pell Pender Penn Pennell Perdue Persons Petit Pettigrew Pettus Peyton Philbrick Pippin
Platt Plummer Poe Pogue Polk Pool Poston Poteet Prather Presley Prewitt Prichard Pritchett Pryor Puckett Pullen
Purcell Purdy Pyle Quarles Raines Rand Ransom Rawls Rayburn Read Redd Redding Reece Reedy Register Reinhart
Renfro Revell Rhoades Rhyne Rickard Ricks Riddick Ridley Rigby Rigsby Riker Rinehart Ritchie Rives Roark Robb
Robey Roderick Rodman Roe Rook Rooks Roper Rountree Rowell Royal Royer Ruff Rumsey Runyon Rupp Rust
Ruth Rutherford Sadler Sanborn Sapp Satterfield Saucier Saxton Scarborough Schenck Schmitt Schott Schreiber Scruggs Seabrook Sealy
Searcy Sears Seay Sewell Seymour Shackelford Shanks Sharpe Shaver Shea Shearer Sheffield Shell Shelby Shepard Sherrill
Shipley Shirk Shockley Shook Shoemaker Shriver Shumaker Sikes Silvers Simmonds Sinclair Singletary Sisk Skaggs Slade Sledge
Slocum Smalley Smart Smithson Snead Snell Snodgrass Sommers Southard Spain Sparkman Speer Spellman Spicer Spivey Spurlock
Stacy Stahl Stallings Stamper Standley Starling Starnes Staton Steadman Stearns Steed Stegall Stidham Stiles Stinson Stockton
Stoner Story Stovall Strader Strange Stringer Strother Stroud Stubbs Sturgis Suggs Sumner Swain Swann Swearingen Sweat
Swope Talley Tarver Teal Templeton Terrell Tharp Thigpen Thorn Thornburg Thrasher Thurman Tibbs Tidwell Tillman Timmons
Tinsley Tipton Toler Toney Toole Totten Towns Trammell Travers Treadwell Trimble Triplett Trotter Truitt Tubbs Tull
Turley Turnbull Tuck Tunstall Twitty Tyree Upshaw Usher Vandiver Vann Vanover Varner Vaught Veal Venable Vernon
Vinson Waddell Wagoner Walden Waldrop Wallis Waring Warfield Warrick Washburn Watters Weatherford Weathers Welborn Welles Wentworth
Wesley Westfall Weston Wharton Whatley Whitcomb Whitehurst Whiting Whitlock Whitmore Whitt Whittaker Whittington Wicker Wickham Wiggs
Wilbanks Wilburn Wilde Wiles Willard Willett Willingham Wills Wilmot Wimberly Winfield Wingate Winn Winstead Wiseman Witt
Womack Wooden Woodruff Woolsey Wooten Worthington Wray Wren Wyman Wynn Yancey Yeager Yoakum Yost Youngblood Abramowitz
Adler Altman Appel Auerbach Bachrach Baer Bauman Baumgartner Beckerman Behrens Berkowitz Birnbaum Blumberg Blumenthal Brodsky Dreyfus
Eisenberg Eisner Engelhardt Epstein Fein Feldstein Finkelstein Fleischman Freund Fried Friedland Fuchs Gartner Geller Glassman Gold
Goldfarb Gottfried Greenblatt Greenspan Grunwald Haber Halpern Hartmann Hauser Hertz Hochberg Hoffmann Holtz Horwitz Jacobi Kahn
Kalman Kantor Kessel Kirsch Klinger Kohn Kornberg Kraus Kremer Kroll Lachman Landau Lederman Lehrer Lerman Levitt
Lichtman Liebman Lindner Loeb Lowenstein Mandel Marx Meisner Metzger Mintz Moskowitz Nagel Neuman Oppenheimer Orenstein Pearlman
Pinsky Plotkin Pollack Rabin Reichert Reisman Resnick Richter Rosenblum Rosenfeld Rothman Rothstein Sachs Salzman Schechter Scheer
Schiff Schlesinger Schoen Schorr Schreiner Segal Seligman Silberman Singer Sobel Spector Spiegel Steinberg Steinman Stolz Strauss
Sussman Teitelbaum Tobias Ullman Vogel Waldman Wasserman Weil Weinberger Weissman Werner Wolfson Zeitlin Zucker Vandenberg Vanderpool
Vanhorn Vandyke Schuyler Abrego:h Aceves:h Adame:h Alanis:h Almanza:h Amador:h Anaya:h Aragon:h Arredondo:h Arteaga:h Baeza:h Banda:h Barragan:h
Bermudez:h Bustos:h Caballero:h Cadena:h Calvillo:h Canales:h Carbajal:h Carmona:h Casas:h Casillas:h Castellanos:h Cazares:h Chapa:h Cornejo:h Corral:h Cota:h
Covarrubias:h Davalos:h Duenas:h Elizondo:h Enciso:h Escalante:h Espino:h Farias:h Favela:h Frias:h Gallardo:h Garay:h Gaytan:h Godinez:h Grijalva:h Guajardo:h
Haro:h Hurtado:h Jaimes:h Lerma:h Limon:h Llamas:h Lozoya:h Manzo:h Marroquin:h Meraz:h Millan:h Montemayor:h Montez:h Moya:h Munguia:h Murguia:h
Najera:h Negrete:h Noriega:h Olivas:h Orosco:h Osorio:h Palomo:h Perales:h Pina:h Plascencia:h Puente:h Quezada:h Quinonez:h Razo:h Robledo:h Rodarte:h
Roque:h Ruelas:h Saavedra:h Salcedo:h Sanabria:h Santillan:h Serna:h Sotelo:h Tejada:h Terrazas:h Toro:h Torrez:h Ulloa:h Valadez:h Varela:h Velarde:h
Venegas:h Vera:h Verdugo:h Villasenor:h Yanez:h Ybarra:h Zaragoza:h Zarate:h Bai:c Chai:c Chiang:c Chou:c Chuang:c Dai:c Fan:c Fu:c
Gong:c Gu:c Hou:c Hsiao:c Ji:c Jia:c Kong:c Lian:c Lou:c Mao:c Meng:c Mo:c Ou:c Qian:c Qiao:c Qin:c
Qiu:c Shao:c Shi:c Sheng:c Wan:c Wen:c Weng:c Xia:c Xiang:c Xiong:c Yan:c Yin:c Ying:c Zhan:c Zhong:c Zou:c
An:k Bang:k Chae:k Chin:k Chong:k Goh:k Hyun:k Im:k Jee:k Jo:k Ju:k Ku:k Maeng:k Min:k Na:k Pyo:k
Ra:k Roh:k Seong:k Shim:k Sohn:k Son:k Tak:k Won:k Yeo:k Yim:k Yoo:k Banh:v Dao:v Dau:v Giang:v Hua:v
Khuu:v Mac:v Nghiem:v Phung:v Tu:v Vuong:v Lac:v Luu:v Mach:v Nhan:v Trieu:v Khuc:v Hau:v Vong:v Huong:v Ahuja:i
Arya:i Bajaj:i Bhagat:i Bhalla:i Bhandari:i Bhargava:i Bhasin:i Chadha:u Chandra:i Chawla:u Chugh:u Dhawan:u Dixit:i Gokhale:i Iyengar:d Jha:i
Juneja:u Kakkar:u Kalra:u Kapur:u Kaul:i Khurana:u Kohli:u Krishnamurthy:d Lal:i Luthra:u Mahajan:u Mathur:i Mehra:u Mittal:i Nagpal:u Nanda:u
Narang:u Narayan:d Oberoi:u Pai:d Puri:u Rajan:d Rastogi:i Saxena:i Sehgal:u Seth:i Shenoy:d Sinha:i Soni:g Sood:u Srivastava:i Suri:u
Talwar:u Tandon:u Thakur:i Vaidya:g Verma:i Wadhwa:u Walia:u Yadav:i Raju:d Ramaswamy:d Balasubramanian:d Venkataraman:d Gopalan:d Ganesan:d Sundaram:d Rangarajan:d
Chandran:d Abboud:a Abdo:a Antoun:a Asmar:a Atallah:a Awad:a Azar:a Bazzi:a Beydoun:a Boulos:a Chami:a Darwish:a Fakhoury:a Ghanem:a Haidar:a
Hamad:a Hammoud:a Harb:a Issa:a Jabbour:a Kanaan:a Khalil:a Khatib:a Maalouf:a Mikhail:a Mourad:a Nahas:a Nassar:a Rahal:a Sabra:a Saad:a
Salem:a Samaha:a Shaheen:a Tannous:a Yassin:a Zaki:a Zayed:a Adeleke:b Adewale:b Agbaje:b Akande:b Ajayi:b Alabi:b Amadi:b Anyanwu:b Bello:b
Emenike:b Ibe:b Igwe:b Madu:b Nnamdi:b Nwankwo:b Obiora:b Odukoya:b Ogbonna:b Ojo:b Okoye:b Olawale:b Olowu:b Osagie:b Oyebanji:b Udeh:b
Ugwu:b Umeh:b Uzor:b Asare:b Darko:b Frimpong:b Ampofo:b Abebe:b Alemu:b Desta:b Getachew:b Tadesse:b Worku:b Yohannes:b Alcantara:f Arceo:f
Balagtas:f Baluyot:f Buenaventura:f Catacutan:f Concepcion:f Cuenca:f Dumlao:f Esguerra:f Estrella:f Gatchalian:f Guevarra:f Ilagan:f Laxamana:f Legaspi:f Macaraeg:f Malabanan:f
Manansala:f Marasigan:f Nepomuceno:f Ong:f Pangilinan:f Quizon:f Sarmiento:f Tuazon:f Umali:f Velasco:f Viray:f Abe:j Akiyama:j Aoki:j Arai:j Endo:j
Fujimoto:j Goto:j Hamada:j Hara:j Hasegawa:j Hirano:j Ishida:j Ishii:j Iwasaki:j Kikuchi:j Kondo:j Kubo:j Maeda:j Masuda:j Miyamoto:j Mizuno:j
Murakami:j Nagai:j Nakajima:j Noguchi:j Saito:j Sakai:j Sakamoto:j Shibata:j Sugiyama:j Takagi:j Takano:j Taniguchi:j Ueda:j Uchida:j Yamashita:j Yamazaki:j
Yokoyama:j Yoshimura:j Afshar:w Alavi:w Amini:w Ansari:w Bagheri:w Bahrami:w Daneshvar:w Ebrahimi:w Ghaffari:w Hashemi:w Javadi:w Kamali:w Mahdavi:w Majidi:w
Mousavi:w Najafi:w Rahmani:w Rashidi:w Safavi:w Salehi:w Sharifi:w Soltani:w Taheri:w Vaziri:w Yazdani:w Abrahamian:m Babayan:m Baghdasarian:m Danielian:m Davtyan:m
Galstyan:m Ghazarian:m Keshishian:m Khachatryan:m Melikian:m Minasian:m Nalbandian:m Papazian:m Sahakian:m Sargsyan:m Terzian:m Vartanian:m Zakarian:m Abramov:r Andreev:r Antonov:r
Baranov:r Bogdanov:r Borisov:r Egorov:r Frolov:r Gusev:r Karpov:r Kiselev:r Komarov:r Kovalev:r Makarov:r Markov:r Mironov:r Nikitin:r Polyakov:r Semenov:r
Sidorov:r Sorokin:r Stepanov:r Tarasov:r Titov:r Yakovlev:r Zakharov:r Kovalenko:r Shevchenko:r Bondarenko:r Tkachenko:r Kravchenko:r Melnyk:r Boyko:r Lysenko:r Bielski:l
Borkowski:l Brzezinski:l Chmielewski:l Cieslak:l Czarnecki:l Gorski:l Kaczmarek:l Kalinowski:l Kowalczyk:l Krol:l Kubiak:l Kwiatkowski:l Marciniak:l Nowakowski:l Ostrowski:l Pietrzak:l
Rutkowski:l Sadowski:l Sikora:l Sokolowski:l Tomaszewski:l Walczak:l Wojcik:l Wrobel:l Zawadzki:l Angelos:e Antonakos:e Athanasiou:e Bakas:e Chronis:e Diamantis:e Economou:e
Galanos:e Hatzis:e Kouris:e Lambros:e Makris:e Manolis:e Nikas:e Panagiotou:e Papageorge:e Petrakis:e Spanos:e Stamos:e Xenakis:e Zervas:e Abate:t Agostino:t
Albanese:t Amoroso:t Bellini:t Benedetto:t Bernardi:t Bonanno:t Calabrese:t Cappello:t Castellano:t Cavallo:t Coppola:t Costanzo:t Delvecchio:t Fabbri:t Falco:t Ferrara:t
Gentile:t Giuliano:t Grasso:t Iannone:t Lanza:t Lucchese:t Marchese:t Mazza:t Napoli:t Nardone:t Palermo:t Parisi:t Pastore:t Pellegrini:t Piazza:t Romeo:t
Rossetti:t Sabatino:t Santangelo:t Serra:t Silvestri:t Testa:t Tedesco:t Valenti:t Zito:t Abreu:o Afonso:o Antunes:o Arruda:o Baptista:o Barbosa:o Bettencourt:o
Borges:o Botelho:o Cabral:o Couto:o Cunha:o Dias:o Fagundes:o Faria:o Fernandes:o Fonseca:o Freitas:o Lima:o Lopes:o Machado:o Magalhaes:o Matos:o
Melo:o Mendes:o Mota:o Neves:o Nogueira:o Paiva:o Pinto:o Reis:o Resendes:o Rodrigues:o Saraiva:o Simoes:o Soares:o Vieira:o Arceneaux:n Babineaux:n
Benoit:n Bergeron:n Boudreaux:n Broussard:n Comeaux:n Doucet:n Dugas:n Fontenot:n Guillory:n Leger:n Melancon:n Mouton:n Prejean:n Robichaux:n Sonnier:n Theriot:n
Arsenault:n Beaulieu:n Bouchard:n Charbonneau:n Dion:n Duchesne:n Gauthier:n Girard:n Lacroix:n Leclerc:n Marchand:n Michaud:n Paradis:n Simard:n Thibault:n Berglund:s
Dahlquist:s Ekstrom:s Gustafson:s Hagberg:s Hedberg:s Holm:s Johansen:s Larsson:s Lindholm:s Lofgren:s Lundberg:s Nilsen:s Nordberg:s Olander:s Quist:s Sandvik:s
Skoglund:s Stenberg:s Torgerson:s Wahlstrom:s Westberg:s Ahearn:q Brannigan:q Callaghan:q Corcoran:q Daly:q Egan:q Finnegan:q Flaherty:q Galvin:q Geraghty:q Hanrahan:q
Heffernan:q Kavanagh:q Keogh:q Lonergan:q Mulcahy:q O'Connell:q O'Hara:q O'Rourke:q O'Sullivan:q Phelan:q Scully:q Sheehy:q Tully:q
"""

# SSA first names, births roughly 1960-1995 (the practicing-clinician cohort), in rank order.
SSA_ANCHORS_M = ((1, 3300), (2, 2500), (3, 2350), (4, 2300), (5, 2200), (6, 2050), (10, 1500), (15, 1100),
                 (20, 850), (30, 600), (50, 380), (75, 250), (100, 180), (150, 110), (200, 75), (300, 42),
                 (400, 27), (500, 18))
SSA_ANCHORS_F = ((1, 2400), (2, 1600), (3, 1400), (5, 1200), (10, 900), (20, 600), (30, 450), (50, 300),
                 (100, 160), (150, 100), (200, 70), (300, 40), (400, 26), (500, 17))

SSA_MALE = """
Michael Christopher David James John Robert Matthew Joseph Daniel William Jason Joshua Brian Richard Andrew
Thomas Ryan Steven Kevin Mark Anthony Eric Jeffrey Timothy Justin Scott Brandon Charles Jonathan Paul
Nicholas Jacob Benjamin Adam Gregory Kenneth Stephen Aaron Patrick Sean Jose Tyler Jeremy Nathan Zachary
Kyle Donald Travis Jesse Gary Dustin Bradley Samuel Edward Shawn Juan Peter Chad Jared Carlos Austin
Derek George Keith Luis Shane Cody Ronald Dennis Marcus Raymond Alexander Jamie Larry Gabriel Antonio
Craig Todd Jordan Frank Corey Phillip Erik Cory Joel Victor Brent Trevor Mario Jeffery Douglas Evan
Russell Ian Philip Bryan Derrick Randy Christian Wesley Vincent Billy Martin Curtis Jesus Nathaniel
Johnny Casey Lucas Henry Manuel Ricardo Alex Allen Seth Brett Kristopher Terry Jerry Bobby Isaac Spencer
Darren Garrett Clinton Roger Logan Blake Lance Francisco Louis Miguel Walter Johnathan Alan Jimmy Adrian
Kurt Albert Willie Jay Arthur Roberto Danny Joe Javier Rodney Troy Lawrence Grant Ruben Edwin Jack Marc
Ernest Calvin Colin Devin Ross Hector Tony Fernando Dylan Joey Mathew Randall Jon Dominic Jorge Bruce
Kelly Clayton Brendan Carl Omar Ramon Jerome Andre Eddie Leonard Rafael Frederick Max Raul Jake Darrell
Jeremiah Angel Damon Ricky Edgar Glenn Harold Gerald Wayne Ray Fred Neil Marvin Eugene Shaun Alberto
Pedro Ivan Dale Lee Ralph Darryl Clifford Leon Elijah Eduardo Jaime Andres Sergio Oscar Alejandro Cesar
Julio Armando Enrique Caleb Connor Ethan Noah Hunter Cameron Mason Jackson Owen Luke Gavin Chase Cole
Dalton Colton Wyatt Tanner Bryce Brady Riley Parker Hayden Carter Preston Liam Aidan Tristan Josiah
Isaiah Elias Levi Micah Collin Dakota Devon Darius Jalen Malik Terrell Lamar Jermaine Tyrone Dwayne
Reginald Maurice Darnell Cedric Byron Antoine Marlon Rashad Demetrius Quentin Marquis Jamal Kendrick
Kareem Desmond Roderick Terrance Terrence Clarence Howard Bernard Herbert Lewis Melvin Earl Leroy Norman
Stanley Francis Gordon Dean Lloyd Warren Floyd Glen Gene Barry Allan Duane Kirk Stuart Neal Wade Kent
Rory Trent Heath Brock Clint Reid Drew Graham Brennan Kendall Mitchell Morgan Dillon Tommy Alec Marshall
Nolan Elliott Simon Theodore Harrison Oliver Sebastian Xavier Damian Dominick Giovanni Salvatore Angelo
Dante Emilio Leonardo Stefan Gerardo Rolando Alfonso Ignacio Lane Clay Cooper Blaine Chance Colby Grady
Quinn Reed Weston Zane Bennett Beau Dane Kaleb Keegan Tobias Wilson Winston Jarrod Shannon Darin Lester
Tracy Jody Lorenzo Felix Hugo Rene Ernesto Arturo Alfredo Gilbert Abel Moses Abraham Israel Julian
Marco Diego Gustavo Mauricio Emmanuel Nicolas Josue Ismael Rodolfo Pablo Felipe Guillermo Roland Gregg
Jonathon Zachery Nickolas Kristian Benny Sam Ben Kurtis Morris Elmer Leo Milton Vernon Wallace Harvey
Lionel Everett Ivan? Rocky Dewayne Lamont Torrey Antwan Cornelius Reuben Sidney Irving Seymour Jeffry
"""

SSA_FEMALE = """
Jennifer Jessica Amanda Sarah Melissa Michelle Elizabeth Ashley Stephanie Nicole Heather Lisa Amy Rebecca
Kimberly Angela Emily Laura Lauren Rachel Christina Megan Samantha Kelly Mary Amber Danielle Andrea Erin
Tiffany Crystal Shannon Katherine Julie Hannah Christine Brittany Kristen Karen Maria Sara Emma Jamie
Patricia Susan Kristin Allison Lindsey Vanessa Victoria Kathryn Natalie Anna Alicia Erica Courtney April
Holly Dana Monica Cynthia Tara Catherine Kathleen Katie Brandy Erika Kristina Alexis Olivia Abigail
Madison Jillian Leslie Veronica Teresa Sandra Denise Tracy Margaret Kayla Chelsea Diana Valerie Melanie
Jacqueline Kristy Jenna Krystal Carrie Tina Tammy Natasha Jasmine Kara Stacy Kendra Sabrina Molly
Cassandra Whitney Lindsay Caitlin Brooke Meghan Kathy Robin Wendy Dawn Rachael Adrienne Bethany Gina
Theresa Linda Deborah Carolyn Pamela Donna Nancy Barbara Sharon Debra Brenda Kristi Renee Felicia Leah
Misty Jill Rebekah Jaclyn Tonya Sheila Stacey Mandy Colleen Katrina Nichole Caroline Desiree Audrey
Morgan Alexandra Claire Grace Sydney Taylor Hailey Haley Alyssa Kaitlyn Destiny Brianna Jordan Paige
Mackenzie Shelby Sierra Gabrielle Marissa Brittney Kelsey Mallory Kirsten Heidi Joanna Rose Ruth
Virginia Carol Janet Diane Joyce Cheryl Gloria Julia Marie Ann Anne Jane Joy Sophia Isabella Ava Chloe
Lily Ella Zoe Mia Natalia Ana Carmen Rosa Gabriela Daniela Adriana Claudia Alejandra Mariana Yesenia
Marisol Lorena Monique Latoya Tamika Ebony Keisha Shanice Aaliyah Imani Kiara Jada Ciara Raven Brandi
Candace Candice Christy Kari Kerri Shana Stacie Tracey Traci Mindy Nikki Jodi Jody Toni Sherry Terri
Tanya Sonya Sonia Ramona Angelica Angie Annie Belinda Betty Beverly Bonnie Charlene Charlotte Cindy
Connie Darlene Doris Elaine Ellen Evelyn Frances Helen Irene Jean Jeanette Joan Josephine Judith Judy
Kay Kim Lori Lynn Marilyn Martha Maureen Paula Peggy Phyllis Rhonda Rita Roberta Sally Shirley Suzanne
Sylvia Vicki Yolanda Yvonne Lydia Miranda Meredith Naomi Nina Priscilla Rachelle Regina Rochelle Shawna
Tabitha Tamara Tasha Trisha Ashlee Ashleigh Kristine Krista Lacey Leigh Marcia Maggie Abby Bailey Cara
Celeste Deanna Eva Faith Hope Iris Ivy Jocelyn Kate Kaitlin Katelyn Kendall Laurie Lucy Melinda Nora
Robyn Savannah Serena Sophie Stella Summer Tessa Vivian Allyson Alison Amelia Ariel Aubrey Autumn
Bridget Camille Cecilia Christa Clara Daisy Elise Ellie Esther Gwendolyn Hillary Ingrid Isabel Jenny
Jessie Johanna Kaylee Lena Lillian Lorraine Lucia Maya Michaela Rosemary Selena Simone Trina Wanda
Yvette Leticia Norma Guadalupe Beatriz Silvia Alma Elena Sofia Paola Valeria Carolina Ariana Bianca
Brianne Colette Danica Dominique Elisa Francesca Gianna Giselle Janelle Janine Jeanne Juliana Justine
Kristal Larissa Lauryn Leanne Lindy Mara Marlene Maribel Marina Maxine Megan? Melody Mercedes Noelle
Olga Patrice Penny Raquel Rebeca Rosalind Roxanne Sabina Sasha Shari Sheryl Sondra Stefanie Susana
"""

# Less common real given names; they carry the birth mass the top lists do not cover, spread evenly.
SSA_MALE_TAIL = """
Abram Ace Adolfo Agustin Ahmad Al Alden Alfonzo Alonzo Alvin Ambrose Amos Anderson Andy Angus Archie Ari Arlen
Armand Arnold Asa Ashton August Augustus Avery Barrett Bart Basil Baxter Benito Bernie Bert Bertram Bill Bjorn Blair
Bo Bob Boyd Brad Bradford Brant Braxton Brenden Brendon Bret Broderick Bronson Bryant Buck Burton Carey Carlton Carlo
Carmelo Carroll Carson Cary Cecil Charley Chester Chet Chris Claude Clement Cleveland Cliff Clifton Coby Conrad Cornell Coy
Cristian Cruz Cyrus Dallas Damion Damien Dan Darian Darrel Darrick Darwin Daryl Davis Dax Deandre Delbert Demarcus Denis
Denny Denver Deon Derick Devan Dewey Dexter Dirk Don Donnell Donovan Doug Doyle Duncan Dusty Dwight Earnest Easton
Eddy Edmund Efrain Eli Elliot Ellis Elvis Emanuel Emerson Emery Emil Erich Errol Ervin Esteban Ezekiel Ezra Fabian
Federico Fletcher Forrest Foster Frankie Franklin Freddie Gabe Galen Garland Garrison Garry Geoffrey Gerard Gideon Gil Gilberto Gonzalo
Grayson Greg Griffin Guy Hal Hank Hans Harlan Harley Harry Heriberto Herman Hiram Holden Homer Horace Houston Hubert
Humberto Irvin Isaias Jacques Jamar Jameson Jan Jarred Jarrett Jasper Jayson Jean Jed Jefferson Jens Jerald Jeramy Jeremey
Jerrod Jess Jim Johnnie Josef Judson Jules Julius Junior Kade Kane Karl Keenan Kelvin Ken Kendell Kenny Kermit
Kerry Kip Kirby Klaus Kraig Kristofer Landon Lars Lawson Leif Leland Leonel Leopold Les Lincoln Lindsey Linwood Lon
Lorne Lowell Loyd Luciano Luther Lyle Mack Malcolm Marcel Marcelo Marcos Mariano Marion Markus Marty Matt Mauro Maxwell
Merle Mickey Mike Miles Mitch Monte Moises Murray Myron Nathanael Ned Nelson Nestor Nick Noe Noel Norberto Octavio
Odell Orlando Orville Otis Otto Pat Percy Perry Pete Phil Pierce Porter Quincy Rand Randal Randolph Reggie Reinaldo
Rex Rhett Rich Rick Rickey Rob Robby Robin Rod Rodger Rogelio Rolf Ron Ronnie Roosevelt Roscoe Rowan Royce
Rudolph Rudy Rufus Rusty Sal Sammy Santos Saul Scot Sherman Silas Skyler Sol Solomon Sonny Stan Stanford Sterling
Steve Stevie Stewart Sylvester Taylor Ted Teddy Terence Thad Thaddeus Thurman Tim Toby Tod Tom Tommie Tomas Tracey
Trey Truman Tyson Ulysses Val Van Vance Vaughn Vern Virgil Ward Warner Webster Wendell Wiley Will Willard Willis
Woodrow Zach Zachariah
"""

SSA_FEMALE_TAIL = """
Adele Adrianna Agnes Aimee Alana Alba Alberta Alexa Alexandria Alice Alisa Alissa Allie Alyson Alysia Amalia Amie Anastasia
Andria Angel Angelina Angelique Anita Annette Antoinette Antonia Arlene Audra Barbra Bernadette Bertha Beth Bettina Billie Blanca Brandie
Breanna Bree Brenna Bridgett Britney Callie Camilla Candy Carina Carla Carley Carly Carmela Caryn Casey Cassie Catalina Cathy
Celia Chandra Chantal Charity Charmaine Chasity Cherie Cheyenne Christi Christie Clarissa Claudette Constance Cora Corinne Corrine Cristina Daphne
Darla Dayna Deana Debbie Deena Delia Della Delores Devon Diann Dina Dixie Dolores Dora Doreen Dorothy Edith Edna
Eileen Elaina Eleanor Elisabeth Eliza Elsa Elsie Emilie Emilia Esmeralda Estela Estelle Ethel Eunice Fallon Fatima Fiona Flora
Florence Gail Genevieve Georgia Geraldine Gertrude Gia Gillian Ginger Gisela Glenda Greta Gretchen Gwen Hallie Harriet Hazel Helena
Hilary Hollie Ida Ilene Imelda Inez Irma Isabelle Jackie Jacklyn Jaime Jami Jan Jana Janae Janell Janice Janie
Jeannie Jenifer Jenni Jennie Jeri Jewel Jo Joann Joanne Jodie Jolene Jordana Josefina Josie Juanita Julianne Juliet June
Justina Kaci Karin Karina Karla Karyn Kasey Katharine Kathi Katy Kelley Kellie Kerry Kia Kimberley Kristie Kristyn Kylie
Lacy Lana Lara Latasha Latisha Laurel Lauri Lea Leann Leanna Lee Leila Lesley Lila Lillie Liz Liza Lois
Lola Loretta Louise Luann Lucille Luz Lyndsay Mabel Madeline Mae Mandi Marcella Margie Margarita Mariah Marianne Marjorie Marla
Marlena Marsha Maryann Matilda Maura Mavis Meagan Meghann Melisa Michele Mildred Millie Minnie Mirna Misti Mollie Muriel Myra
Myrna Nadine Nanette Nellie Nicola Nicolette Noemi Nona Olive Opal Patsy Paulette Pauline Pearl Petra Polly Rae Raina
Reba Rena Rhea Rhoda Ricki Rosalie Rosalyn Rosanna Rosario Roseann Rosemarie Roxana Ruby Sadie Sallie Samara Sandi Sandy
Selma Shanna Sharla Shauna Shayla Sheena Shelia Shelly Sheri Sherri Sherrie Stacia Staci Sue Susanna Susie Suzette Tamera
Tammi Tamra Tania Tatum Tawny Terra Terry Thelma Tia Tiara Tiffani Tisha Tonia Tori Tricia Trudy Valarie Velma
Vera Verna Vicky Viola Violet Wilma Winifred Yasmin Zelda
"""

# Given-name pools for clinicians whose surname carries an ethnic tag (immigrant and second-generation
# clinicians often keep heritage names). Ranked loosely by commonness.
HERITAGE_FIRST = {
    "h": ("Jose Juan Carlos Luis Miguel Jorge Francisco Antonio Alejandro Javier Ricardo Eduardo Fernando "
          "Roberto Manuel Rafael Sergio Raul Hector Oscar Arturo Andres Diego Gustavo Enrique Pedro Mario "
          "Ramon Alberto Victor Jesus Julio Cesar Rodrigo Ernesto Guillermo Felipe Pablo Ignacio Santiago",
          "Maria Ana Carmen Rosa Gabriela Daniela Adriana Claudia Alejandra Mariana Patricia Veronica Laura "
          "Monica Diana Elena Lucia Sofia Isabel Marisol Lorena Yesenia Paola Valeria Andrea Carolina "
          "Natalia Beatriz Leticia Guadalupe Alma Silvia Esperanza Graciela Norma"),
    "v": ("Minh Tuan Thanh Duc Hung Quang Khanh Long Hieu Hoang Nam Phong Tri Vinh Dung Huy Bao Thinh Phuc Kiet "
          "An Binh Cuong Dat Hai Kha Loc Luan Nghia Nhan Quoc Son Tam Thang Thien Toan Trung Viet",
          "Linh Huong Trang Anh Lan Mai Thao Hoa Ngoc Thuy Hanh Phuong Hien Uyen Nga Hang Yen Vy Tram Diem "
          "Bich Chau Dao Giang Ha Hong Huyen Kim Loan My Nhung Oanh Quynh Thu Tuyet Van Xuan"),
    "c": ("Wei Jian Ming Jun Hao Lei Tao Yong Bin Feng Gang Peng Qiang Chao Kai Jie Bo Xin Yu Zhen",
          "Mei Ying Hui Jing Yan Fang Ling Xin Hong Lan Juan Min Na Qing Ting Yun Xue Lu Shan Li"),
    "k": ("Sung Hyun Jae Seung Young Jin Joon Tae Kyung Sang Chul Woo Seok Hoon Yong Min Dong Jun",
          "Eun Hye Ji Soo Mi Sun Yuna Hana Jiyoung Minji Seoyeon Jiwon Eunji Sora Hyejin Yoon"),
    "i": ("Raj Rajesh Sanjay Anil Vijay Amit Rahul Ravi Suresh Ashok Sunil Arun Sandeep Vikram Deepak Manish "
          "Nikhil Rohit Ajay Ramesh Arjun Kiran Pradeep Sameer Anand Harish Mahesh Prakash Gaurav Vivek Naveen "
          "Abhishek Aditya Akash Alok Amar Aniket Ankur Anupam Ashish Atul Bharat Chetan Dinesh Ganesh Gopal Hari "
          "Hemant Jatin Karan Krishna Kunal Lalit Mohan Mukesh Nitin Pankaj Paresh Piyush Rakesh Rohan Sachin "
          "Sanjeev Satish Shankar Shyam Siddharth Sudhir Tarun Uday Varun Vinay Vinod Yogesh",
          "Priya Anjali Neha Pooja Deepa Sunita Kavita Anita Lakshmi Meera Shilpa Swati Divya Asha Rekha Nisha "
          "Ritu Preeti Sangeeta Shreya Aarti Radha Usha Sneha Smita Madhavi Archana Rupa Vandana Pallavi "
          "Aishwarya Alka Ananya Aparna Bhavna Chitra Deepika Geeta Hema Indira Jaya Jyoti Kalpana Kamala Lata "
          "Malini Manisha Mamta Nandini Padma Parul Poonam Rachna Rashmi Rina Roshni Sadhana Seema Shalini "
          "Sharmila Shobha Sonal Sujata Suman Sushma Tanvi Uma Vidya"),
    "j": ("Kenji Hiroshi Takeshi Kazuo Satoshi Daisuke Yuji Koji Akira Hideo Kenta Shinji",
          "Yuki Akiko Keiko Naoko Yumi Emiko Mariko Sachiko Tomoko Hiromi Kaori Aiko"),
    "a": ("Mohammed Ahmed Omar Ali Hassan Khalid Mahmoud Tarek Karim Sami Rami Yousef Ibrahim Faisal Nabil "
          "Bilal Hussein Walid Jamal Ziad Adel Amr Ayman Bassam Fadi Fouad Ghassan Hadi Hani Hisham Imad Kamal Majid "
          "Marwan Mazen Mounir Nader Osama Rafiq Raed Saad Salim Samir Tariq Wael Yasser",
          "Fatima Layla Nadia Amira Yasmin Rania Leila Hana Mona Noor Dina Samira Zainab Maryam Lina Reem "
          "Salma Huda Aisha Farah Abeer Dalia Ghada Hala Hanan Iman Jumana Lamia Lubna Maha Manal Maysa Nada "
          "Najwa Nawal Nisreen Ola Rana Randa Rasha Rawan Ruba Sana Suha Wafa"),
    "r": ("Dmitri Sergei Alexei Yuri Igor Mikhail Vladimir Andrei Nikolai Pavel Oleg Boris Maxim Ivan Konstantin",
          "Natalia Olga Elena Irina Svetlana Tatiana Yelena Marina Ekaterina Anastasia Oksana Galina Ludmila "
          "Inna Vera"),
    "w": ("Reza Mehdi Babak Arash Farhad Ali Hamid Kamran Dariush Payam Behrouz Kourosh Navid Saeed Amir",
          "Shirin Leila Parisa Nasrin Mitra Roya Azadeh Maryam Shabnam Neda Golnar Mahsa Ladan Yasaman Afsaneh"),
    "m": ("Armen Aram Arman Hovik Gor Tigran Vahe Ara Hagop Raffi Sarkis Levon",
          "Ani Anahit Lilit Nare Arpi Talin Lusine Nairi Siranush Hasmik Mariam Tamar"),
    "b": ("Chinedu Emeka Oluwaseun Adebayo Olumide Tunde Uchenna Ikechukwu Babatunde Obinna Chukwuemeka Kunle "
          "Femi Kelechi Segun Kwame Kofi Yaw Dawit Tesfaye",
          "Ngozi Chioma Folake Ifeoma Adaeze Oluwakemi Funmilayo Nkechi Chiamaka Yetunde Amaka Bisola Titilayo "
          "Uche Adaora Akosua Abena Ama Selam Hiwot"),
}
for _alias, _base in (("g", "i"), ("u", "i"), ("d", "i"), ("f", "h")):
    HERITAGE_FIRST[_alias] = HERITAGE_FIRST[_base]

# tag -> (languages [(language, p)], heritage given-name p, clinician-workforce multiplier,
#         default regional multiplier, {metro: regional multiplier})
TAGS = {
    "h": ([("Spanish", 0.75)], 0.45, 0.5, 0.6,
          {"dallas-tx": 2.2, "fort-worth-tx": 2.0, "houston-tx": 2.5, "austin-tx": 2.2, "san-antonio-tx": 4.0,
           "los-angeles-ca": 3.0, "san-diego-ca": 2.2, "san-jose-ca": 1.8, "sacramento-ca": 1.5,
           "san-francisco-ca": 1.2, "oakland-ca": 1.5, "phoenix-az": 2.0, "las-vegas-nv": 1.8,
           "albuquerque-nm": 3.5, "denver-co": 1.6, "miami-fl": 4.0, "orlando-fl": 1.8, "tampa-fl": 1.4,
           "chicago-il": 1.4, "new-york-ny": 1.6}),
    "v": ([("Vietnamese", 0.75)], 0.55, 1.3, 0.4,
          {"houston-tx": 4.0, "san-jose-ca": 5.0, "los-angeles-ca": 2.5, "dallas-tx": 2.0, "oakland-ca": 1.5,
           "san-francisco-ca": 1.5, "seattle-wa": 2.0, "san-diego-ca": 1.5, "sacramento-ca": 1.5,
           "atlanta-ga": 1.2, "new-orleans-la": 2.0}),
    "c": ([("Mandarin", 0.45), ("Cantonese", 0.25)], 0.35, 2.0, 0.6,
          {"san-francisco-ca": 4.0, "oakland-ca": 2.5, "san-jose-ca": 3.0, "los-angeles-ca": 2.0,
           "new-york-ny": 2.5, "seattle-wa": 2.0, "boston-ma": 1.5, "houston-tx": 1.3}),
    "k": ([("Korean", 0.6)], 0.35, 2.0, 0.5,
          {"los-angeles-ca": 3.0, "new-york-ny": 2.0, "atlanta-ga": 1.8, "seattle-wa": 1.5, "dallas-tx": 1.3,
           "washington-dc": 1.5}),
    "i": ([("Hindi", 0.5)], 0.6, 2.2, 1.0,
          {"chicago-il": 2.0, "new-york-ny": 2.0, "houston-tx": 2.0, "dallas-tx": 2.0, "san-jose-ca": 3.0,
           "atlanta-ga": 1.5, "washington-dc": 1.3, "philadelphia-pa": 1.5, "raleigh-nc": 1.5,
           "detroit-mi": 1.5}),
    "g": ([("Gujarati", 0.5), ("Hindi", 0.3)], 0.6, 2.2, 1.0,
          {"chicago-il": 2.5, "new-york-ny": 3.0, "philadelphia-pa": 2.0, "houston-tx": 2.0, "dallas-tx": 1.5,
           "atlanta-ga": 1.5}),
    "u": ([("Punjabi", 0.5), ("Hindi", 0.3)], 0.6, 2.2, 1.0,
          {"sacramento-ca": 3.0, "san-jose-ca": 2.0, "new-york-ny": 1.8, "chicago-il": 1.5,
           "seattle-wa": 1.5, "oakland-ca": 1.5}),
    "d": ([("Telugu", 0.35), ("Tamil", 0.25)], 0.6, 2.2, 1.0,
          {"san-jose-ca": 3.0, "dallas-tx": 2.0, "houston-tx": 1.5, "raleigh-nc": 2.0, "chicago-il": 1.5,
           "new-york-ny": 1.5, "atlanta-ga": 1.5}),
    "f": ([("Tagalog", 0.5)], 0.1, 1.5, 0.6,
          {"san-francisco-ca": 2.0, "oakland-ca": 2.0, "los-angeles-ca": 2.0, "san-diego-ca": 3.0,
           "las-vegas-nv": 2.5, "seattle-wa": 1.5, "chicago-il": 1.2, "new-york-ny": 1.2}),
    "j": ([("Japanese", 0.25)], 0.15, 1.5, 0.6,
          {"seattle-wa": 2.0, "los-angeles-ca": 2.0, "san-francisco-ca": 1.5, "san-jose-ca": 1.5,
           "sacramento-ca": 1.5}),
    "a": ([("Arabic", 0.6)], 0.7, 1.3, 0.8,
          {"detroit-mi": 4.0, "los-angeles-ca": 1.3, "new-york-ny": 1.3, "houston-tx": 1.2, "chicago-il": 1.3}),
    "r": ([("Russian", 0.5)], 0.5, 1.5, 0.7,
          {"new-york-ny": 2.5, "boston-ma": 1.5, "chicago-il": 1.3, "seattle-wa": 1.2, "sacramento-ca": 1.5}),
    "l": ([("Polish", 0.4)], 0.0, 1.0, 0.8,
          {"chicago-il": 3.0, "detroit-mi": 2.5, "cleveland-oh": 2.0, "new-york-ny": 1.2, "baltimore-md": 1.3,
           "philadelphia-pa": 1.3}),
    "e": ([("Greek", 0.3)], 0.0, 1.2, 0.8,
          {"boston-ma": 2.0, "chicago-il": 2.0, "new-york-ny": 1.5, "tampa-fl": 1.5}),
    "t": ([("Italian", 0.1)], 0.0, 1.2, 0.8,
          {"new-york-ny": 2.5, "boston-ma": 2.0, "philadelphia-pa": 2.5, "cleveland-oh": 1.5,
           "baltimore-md": 1.3}),
    "o": ([("Portuguese", 0.4)], 0.0, 1.0, 0.5,
          {"boston-ma": 3.0, "san-jose-ca": 1.5, "new-york-ny": 1.5, "miami-fl": 1.5}),
    "w": ([("Farsi", 0.5)], 0.6, 1.3, 0.5,
          {"los-angeles-ca": 5.0, "san-jose-ca": 1.5, "washington-dc": 1.5, "houston-tx": 1.3}),
    "m": ([("Armenian", 0.5)], 0.4, 1.0, 0.3,
          {"los-angeles-ca": 6.0, "boston-ma": 2.0}),
    "b": ([("Yoruba", 0.15), ("Igbo", 0.15), ("Amharic", 0.1)], 0.6, 2.0, 0.7,
          {"houston-tx": 3.0, "atlanta-ga": 2.5, "washington-dc": 2.5, "baltimore-md": 2.0, "dallas-tx": 2.0,
           "new-york-ny": 1.5}),
    "s": ([], 0.0, 1.0, 0.8,
          {"minneapolis-mn": 4.0, "st-paul-mn": 4.0, "seattle-wa": 2.0, "portland-or": 1.5,
           "salt-lake-city-ut": 1.5, "chicago-il": 1.2}),
    "q": ([], 0.0, 1.0, 1.0,
          {"boston-ma": 2.5, "philadelphia-pa": 1.5, "new-york-ny": 1.5, "chicago-il": 1.3}),
    "n": ([("French", 0.3)], 0.0, 1.0, 0.7,
          {"new-orleans-la": 4.0, "miami-fl": 1.5, "boston-ma": 1.3}),
}
SPANISH_METROS = {"miami-fl": 0.25, "san-antonio-tx": 0.2, "houston-tx": 0.15, "los-angeles-ca": 0.15,
                  "albuquerque-nm": 0.2, "phoenix-az": 0.12, "dallas-tx": 0.12, "fort-worth-tx": 0.1,
                  "austin-tx": 0.12, "san-diego-ca": 0.12, "las-vegas-nv": 0.1, "orlando-fl": 0.1}
OTHER_LANGUAGES = ("Spanish", "French", "Mandarin", "Arabic", "Russian", "Portuguese", "Hindi", "Tagalog",
                   "Vietnamese", "Korean", "German", "American Sign Language")

# ---- specialties and appointment types --------------------------------------------------------
SPECIALTY = {
    "GEN": "General", "FM": "Family Medicine", "IM": "Internal Medicine", "PED": "Pediatrics",
    "CAR": "Cardiology", "DER": "Dermatology", "ORT": "Orthopedics", "NEU": "Neurology", "OB": "OB/GYN",
    "OPH": "Ophthalmology", "ENT": "ENT", "GI": "Gastroenterology", "END": "Endocrinology",
    "PSY": "Psychiatry", "RAD": "Radiology", "PT": "Physical Therapy", "LAB": "Lab", "DEN": "Dental",
    "URO": "Urology", "PUL": "Pulmonology", "ALL": "Allergy/Immunology", "SPM": "Sports Medicine",
    "RHE": "Rheumatology", "NEP": "Nephrology", "ONC": "Oncology", "PAIN": "Pain Management",
    "POD": "Podiatry", "PCAR": "Pediatric Cardiology", "PNEU": "Pediatric Neurology",
    "PGI": "Pediatric Gastroenterology", "GS": "General Surgery", "CHI": "Chiropractic",
    "INT": "Integrative Medicine",
}

# abbr weight female-share title-mix [capability every site-1 must have]
PROVIDER_SPECIALTIES = """
FM 16 .50 primary
IM 14 .42 primary
PED 8 .66 primary
OB 5 .62 obgyn
PSY 5 .47 psych
CAR 3.5 .16 specialist
ORT 3.5 .08 surgical
DER 2.5 .52 derm
RAD 3 .28 radiology imaging
GI 2 .20 specialist
NEU 2 .38 specialist
END 1.2 .55 specialist
PUL 1.2 .30 specialist
ENT 1.2 .20 surgical
URO 1.2 .10 surgical
ALL .8 .50 specialist
DEN 2.5 .38 allied dental
PT 2.5 .62 allied physical_therapy
LAB 1 .60 allied lab
OPH 3 .30 surgical
SPM 1.5 .30 specialist
RHE .8 .55 specialist
NEP .8 .32 specialist
ONC 1.5 .38 specialist
PAIN 1 .20 specialist
POD 1.2 .30 specialist
PCAR .3 .40 specialist
PNEU .3 .55 specialist
PGI .3 .50 specialist
GS 1.5 .25 surgical
"""
TITLE_MIX = {
    "primary": (("MD", 45), ("DO", 15), ("NP", 27), ("PA", 13)),
    "obgyn": (("MD", 62), ("DO", 12), ("NP", 18), ("PA", 8)),
    "psych": (("MD", 55), ("DO", 10), ("NP", 33), ("PA", 2)),
    "specialist": (("MD", 72), ("DO", 12), ("NP", 9), ("PA", 7)),
    "surgical": (("MD", 72), ("DO", 8), ("NP", 4), ("PA", 16)),
    "derm": (("MD", 68), ("DO", 7), ("NP", 7), ("PA", 18)),
    "radiology": (("MD", 86), ("DO", 10), ("NP", 2), ("PA", 2)),
    "allied": (("MD", 40), ("DO", 30), ("NP", 15), ("PA", 15)),
}
FEMALE_SHARE_BY_TITLE = {"NP": 0.88, "PA": 0.67}

# Original types offered by new specialties in addition to their own.
EXTRA_ORIGINAL_MENU = {"SPM": ("appt_019", "appt_033"), "PAIN": ("appt_033",), "RHE": ("appt_033",)}

# name|specialty|minutes|referral|new patients|capability|c=core o=optional x=unoffered|menu (provider specialties)
NEW_TYPES = """
Medicare Annual Wellness Visit|IM|40|N|Y|-|c|FM,IM
Annual Physical - Established Patient|FM|30|N|N|-|o|FM,IM
Same-Day Sick Visit|FM|20|N|Y|-|o|FM,IM,PED
Telehealth Visit|GEN|20|N|Y|-|o|FM,IM,PED,PSY,END,DER
Hypertension Follow-up|IM|20|N|N|-|c|FM,IM
Blood Pressure Check|FM|15|N|N|-|o|FM,IM
Chronic Care Management Visit|IM|30|N|N|-|o|FM,IM
Transitional Care Visit|IM|40|N|N|-|o|FM,IM
Geriatric Assessment|IM|60|Y|Y|-|o|IM
Advance Care Planning|IM|30|N|N|-|o|FM,IM
Weight Management Consultation|FM|30|N|Y|-|o|FM,IM,END
Nutrition Counseling|FM|45|N|Y|-|o|FM,IM,END
Smoking Cessation Counseling|FM|20|N|Y|-|o|FM,IM,PUL
Depression Screening Visit|FM|30|N|Y|-|o|FM,IM
Women's Health Visit|FM|30|N|Y|-|o|FM
Men's Health Visit|FM|30|N|Y|-|o|FM,IM
DOT Physical|FM|30|N|Y|-|o|FM
Pre-Employment Physical|FM|30|N|Y|-|o|FM,IM
Minor Procedure Visit|FM|30|N|N|surgery|o|FM
Ear Wax Removal|FM|15|N|Y|-|o|FM,IM,PED
TB Skin Test|FM|10|N|Y|-|o|FM,IM
Shingles Vaccine|FM|10|N|Y|-|o|FM,IM
STI Testing|FM|20|N|Y|lab|o|FM,IM,OB
Adolescent Well Visit|PED|30|N|Y|-|c|PED,FM
ADHD Evaluation|PED|60|N|Y|-|o|PED
ADHD Follow-up|PED|20|N|N|-|o|PED,PSY
Developmental Screening|PED|30|N|Y|-|o|PED
Pediatric Vaccination Visit|PED|15|N|Y|-|c|PED
Lactation Consultation|PED|45|N|Y|-|o|PED,OB
Cardiac Follow-up|CAR|20|N|N|-|c|CAR
Hypertension Consultation|CAR|40|Y|Y|-|o|CAR,NEP
Heart Failure Clinic Visit|CAR|30|Y|N|-|o|CAR
Arrhythmia Consultation|CAR|40|Y|Y|-|o|CAR
Lipid Clinic Consultation|CAR|30|Y|Y|-|o|CAR,END
Coronary Calcium Score CT|CAR|30|Y|N|imaging|o|CAR,RAD
Nuclear Stress Test|CAR|90|Y|N|imaging|o|CAR,RAD
Event Monitor Fitting|CAR|20|Y|N|-|o|CAR
Cardiac Rehab Intake|CAR|60|Y|N|physical_therapy|o|CAR,PT
Skin Biopsy|DER|30|Y|N|surgery|o|DER
Rash Evaluation|DER|20|N|Y|-|c|DER
Psoriasis Follow-up|DER|20|N|N|-|o|DER
Eczema Consultation|DER|30|N|Y|-|o|DER,ALL
Wart Removal|DER|15|N|Y|-|o|DER,FM
Hair Loss Consultation|DER|30|N|Y|-|o|DER
Botox Treatment|DER|30|N|N|-|o|DER
Mohs Surgery|DER|120|Y|N|surgery|o|DER
Annual Skin Check|DER|20|N|N|-|c|DER
Acne Consultation|DER|30|N|Y|-|c|DER
Tattoo Removal Consultation|DER|30|N|Y|-|x|-
Hair Transplant Consultation|DER|45|N|Y|-|x|-
Knee Injury Evaluation|ORT|30|N|Y|-|c|ORT,SPM
Back Pain Evaluation|ORT|30|N|Y|-|c|ORT
Shoulder Pain Evaluation|ORT|30|N|Y|-|c|ORT,SPM
Hip Pain Evaluation|ORT|30|N|Y|-|o|ORT
Hand and Wrist Consultation|ORT|40|Y|Y|-|o|ORT
Foot and Ankle Consultation|ORT|40|Y|Y|-|o|ORT,POD
Spine Consultation|ORT|40|Y|Y|-|o|ORT,PAIN
Joint Replacement Consultation|ORT|45|Y|Y|-|o|ORT
Post-operative Follow-up|ORT|20|N|N|-|c|ORT,GS
Carpal Tunnel Evaluation|ORT|30|Y|Y|-|o|ORT,NEU
Sports Medicine Consultation|SPM|40|N|Y|-|c|SPM
Sports Injury Evaluation|SPM|30|N|Y|-|c|SPM,ORT
Sprain and Strain Evaluation|SPM|30|N|Y|-|c|SPM,ORT,FM
Concussion Evaluation|SPM|45|N|Y|-|c|SPM,NEU,PNEU
Running Injury Clinic Visit|SPM|40|N|Y|-|o|SPM
Ultrasound-Guided Injection|SPM|30|Y|N|imaging|o|SPM,PAIN,RHE
Return-to-Play Assessment|SPM|30|N|N|-|o|SPM
Sports Medicine Follow-up|SPM|20|N|N|-|c|SPM
Neurology Follow-up|NEU|20|N|N|-|c|NEU
Migraine Consultation|NEU|45|Y|Y|-|o|NEU
EMG / Nerve Conduction Study|NEU|60|Y|N|-|o|NEU
Seizure Clinic Visit|NEU|30|Y|N|-|o|NEU
Multiple Sclerosis Follow-up|NEU|30|N|N|-|o|NEU
Dizziness and Vertigo Evaluation|NEU|45|Y|Y|-|o|NEU,ENT
Prenatal Ultrasound|OB|30|N|N|imaging|o|OB,RAD
Anatomy Scan (20-Week Ultrasound)|OB|60|N|N|imaging|o|OB,RAD
Postpartum Visit|OB|30|N|N|-|c|OB
Pregnancy Confirmation Visit|OB|20|N|Y|-|c|OB,FM
IUD Insertion|OB|30|N|N|-|o|OB,FM
Menopause Consultation|OB|30|N|Y|-|o|OB
Pelvic Pain Evaluation|OB|30|N|Y|-|o|OB
Infertility Consultation|OB|60|Y|Y|-|o|OB
Colposcopy|OB|30|Y|N|-|o|OB
High-Risk Pregnancy Visit|OB|30|Y|N|-|o|OB
IVF Consultation|OB|60|Y|Y|-|x|-
Prenatal Genetic Counseling|OB|60|Y|Y|-|x|-
Diabetic Eye Exam|OPH|30|N|Y|-|c|OPH
Pediatric Eye Exam|OPH|30|N|Y|-|c|OPH
Dry Eye Evaluation|OPH|30|N|Y|-|o|OPH
LASIK Consultation|OPH|45|N|Y|-|o|OPH
Retina Consultation|OPH|45|Y|Y|-|o|OPH
Pink Eye Visit|OPH|20|N|Y|-|c|OPH
Ear Cleaning|ENT|15|N|Y|-|c|ENT
Tonsil Evaluation|ENT|30|Y|Y|-|o|ENT
Hearing Aid Evaluation|ENT|60|N|Y|-|o|ENT
Sleep Apnea Evaluation|ENT|40|Y|Y|-|o|ENT,PUL
Balance Testing|ENT|60|Y|N|-|o|ENT
Tinnitus Evaluation|ENT|40|Y|Y|-|o|ENT
ENT Follow-up|ENT|20|N|N|-|c|ENT
Cochlear Implant Evaluation|ENT|120|Y|Y|-|x|-
GI Follow-up|GI|20|N|N|-|c|GI
Colonoscopy|GI|60|Y|N|surgery|c|GI
Upper Endoscopy (EGD)|GI|45|Y|N|surgery|o|GI
Hepatology Consultation|GI|40|Y|Y|-|o|GI
IBS Consultation|GI|40|Y|Y|-|o|GI
GERD Consultation|GI|30|Y|Y|-|o|GI
Celiac Disease Evaluation|GI|40|Y|Y|-|o|GI,PGI
Endocrinology Follow-up|END|20|N|N|-|c|END
Thyroid Ultrasound|END|30|Y|N|imaging|o|END,RAD
Thyroid Nodule Consultation|END|40|Y|Y|-|o|END
Osteoporosis Consultation|END|40|Y|Y|-|o|END,RHE
Insulin Pump Training|END|60|N|N|-|o|END
Diabetes Education Class|END|60|N|Y|-|o|END,FM,IM
Psychiatric Follow-up|PSY|30|N|N|-|c|PSY
Psychiatric Intake Assessment|PSY|60|N|Y|-|c|PSY
Telepsychiatry Visit|PSY|30|N|N|-|o|PSY
Adult ADHD Evaluation|PSY|60|Y|Y|-|o|PSY
Individual Therapy|PSY|50|N|Y|-|o|PSY
Couples Therapy|PSY|50|N|Y|-|o|PSY
Group Therapy|PSY|90|N|N|-|o|PSY
Substance Use Evaluation|PSY|60|N|Y|-|o|PSY
Child Psychiatry Evaluation|PSY|60|Y|Y|-|o|PSY
Eating Disorder Evaluation|PSY|60|Y|Y|-|o|PSY
MRI - Shoulder|RAD|45|Y|N|imaging|c|RAD
MRI - Hip|RAD|45|Y|N|imaging|c|RAD
MRI - Ankle|RAD|45|Y|N|imaging|o|RAD
MRI - Abdomen|RAD|60|Y|N|imaging|o|RAD
CT - Head|RAD|30|Y|N|imaging|o|RAD
CT - Chest|RAD|30|Y|N|imaging|o|RAD
CT - Abdomen and Pelvis|RAD|30|Y|N|imaging|o|RAD
Lung Cancer Screening CT|RAD|20|Y|Y|imaging|o|RAD
Ultrasound - Abdomen|RAD|45|Y|N|imaging|o|RAD
Ultrasound - Pelvic|RAD|30|Y|N|imaging|o|RAD,OB
Breast Ultrasound|RAD|30|Y|N|imaging|o|RAD
Diagnostic Mammogram|RAD|45|Y|N|imaging|c|RAD
3D Mammogram (Tomosynthesis)|RAD|30|N|Y|imaging|o|RAD
Chest X-Ray|RAD|15|Y|N|imaging|c|RAD
Knee X-Ray|RAD|15|Y|N|imaging|o|RAD,ORT,SPM
Spine X-Ray|RAD|20|Y|N|imaging|o|RAD,ORT
PET Scan|RAD|120|Y|N|imaging|x|-
Physical Therapy Re-evaluation|PT|30|N|N|physical_therapy|c|PT
Occupational Therapy Evaluation|PT|60|Y|Y|physical_therapy|o|PT
Occupational Therapy Session|PT|45|N|N|physical_therapy|o|PT
Vestibular Therapy|PT|45|Y|N|physical_therapy|o|PT
Pelvic Floor Therapy|PT|60|Y|Y|physical_therapy|o|PT
Sports Rehab Session|PT|45|N|N|physical_therapy|o|PT,SPM
Post-surgical Rehab Session|PT|45|Y|N|physical_therapy|o|PT
Massage Therapy|PT|60|N|Y|-|x|-
Urinalysis|LAB|10|N|Y|lab|c|LAB
Lipid Panel|LAB|15|N|Y|lab|c|LAB
A1C Test|LAB|10|N|Y|lab|o|LAB,END
Drug Screening|LAB|15|N|Y|lab|o|LAB
Strep Test|LAB|10|N|Y|lab|o|LAB,PED
Dental Filling|DEN|60|N|N|dental|c|DEN
Root Canal|DEN|90|Y|N|dental|o|DEN
Tooth Extraction|DEN|45|N|N|dental|o|DEN
Wisdom Tooth Consultation|DEN|30|N|Y|dental|o|DEN
Emergency Dental Visit|DEN|30|N|Y|dental|c|DEN
Dental X-Rays|DEN|20|N|Y|dental|c|DEN
Orthodontic Consultation|DEN|45|N|Y|dental|o|DEN
Urology Follow-up|URO|20|N|N|-|c|URO
Vasectomy Consultation|URO|30|N|Y|-|o|URO
Kidney Stone Evaluation|URO|40|Y|Y|-|o|URO
Prostate Screening (PSA)|URO|20|N|Y|-|o|URO,FM,IM
Urinary Incontinence Evaluation|URO|40|Y|Y|-|o|URO,OB
Cystoscopy|URO|30|Y|N|surgery|o|URO
Lithotripsy|URO|60|Y|N|surgery|x|-
Pulmonology Follow-up|PUL|20|N|N|-|c|PUL
Asthma Management Visit|PUL|30|N|N|-|o|PUL,ALL
COPD Follow-up|PUL|30|N|N|-|o|PUL
Sleep Study Consultation|PUL|40|Y|Y|-|o|PUL
CPAP Follow-up|PUL|20|N|N|-|o|PUL
Overnight Sleep Study|PUL|480|Y|N|-|x|-
Allergy Shots (Immunotherapy)|ALL|20|N|N|-|c|ALL
Allergy Follow-up|ALL|20|N|N|-|c|ALL
Food Allergy Evaluation|ALL|60|Y|Y|-|o|ALL
Patch Testing|ALL|30|Y|N|-|o|ALL,DER
Rheumatology Consultation|RHE|60|Y|Y|-|c|RHE
Rheumatology Follow-up|RHE|20|N|N|-|c|RHE
Arthritis Evaluation|RHE|45|Y|Y|-|c|RHE
Lupus Follow-up|RHE|30|N|N|-|o|RHE
Gout Consultation|RHE|30|Y|Y|-|o|RHE
Infusion Therapy Visit|RHE|120|Y|N|-|o|RHE,GI
Nephrology Consultation|NEP|45|Y|Y|-|c|NEP
Kidney Disease Follow-up|NEP|30|N|N|-|c|NEP
Dialysis Evaluation|NEP|60|Y|N|-|o|NEP
Kidney Ultrasound|NEP|30|Y|N|imaging|o|NEP,RAD,URO
Kidney Transplant Evaluation|NEP|90|Y|N|-|x|-
Oncology Consultation|ONC|60|Y|Y|-|c|ONC
Oncology Follow-up|ONC|30|N|N|-|c|ONC
Chemotherapy Infusion|ONC|180|Y|N|-|o|ONC
Hematology Consultation|ONC|45|Y|Y|-|o|ONC
Anemia Evaluation|ONC|40|Y|Y|-|o|ONC
Iron Infusion|ONC|90|Y|N|-|o|ONC
Second Opinion Consultation|ONC|60|Y|Y|-|o|ONC
Pain Management Consultation|PAIN|45|Y|Y|-|c|PAIN
Pain Management Follow-up|PAIN|20|N|N|-|c|PAIN
Chronic Back Pain Evaluation|PAIN|45|Y|Y|-|c|PAIN
Epidural Steroid Injection|PAIN|45|Y|N|imaging|o|PAIN
Nerve Block|PAIN|45|Y|N|imaging|o|PAIN
Trigger Point Injection|PAIN|20|N|N|-|o|PAIN,SPM
Pain Medication Management|PAIN|20|N|N|-|o|PAIN
Spinal Cord Stimulator Consultation|PAIN|45|Y|N|-|x|-
Podiatry Consultation|POD|30|N|Y|-|c|POD
Diabetic Foot Exam|POD|20|N|Y|-|c|POD
Ingrown Toenail Removal|POD|30|N|Y|surgery|o|POD
Plantar Fasciitis Evaluation|POD|30|N|Y|-|o|POD,SPM
Custom Orthotics Fitting|POD|30|N|N|-|o|POD
Bunion Consultation|POD|30|N|Y|-|o|POD
Foot Injury Evaluation|POD|30|N|Y|-|o|POD
Pediatric Cardiology Consultation|PCAR|45|Y|Y|-|c|PCAR
Heart Murmur Evaluation|PCAR|40|Y|Y|-|c|PCAR
Pediatric Echocardiogram|PCAR|45|Y|N|imaging|o|PCAR
Pediatric Neurology Consultation|PNEU|60|Y|Y|-|c|PNEU
Pediatric Seizure Follow-up|PNEU|30|N|N|-|o|PNEU
Autism Evaluation|PNEU|90|Y|Y|-|o|PNEU,PED
Pediatric GI Consultation|PGI|45|Y|Y|-|c|PGI
Pediatric GI Follow-up|PGI|20|N|N|-|c|PGI
Pediatric Feeding Evaluation|PGI|60|Y|Y|-|o|PGI
General Surgery Consultation|GS|40|Y|Y|-|c|GS
Hernia Consultation|GS|30|Y|Y|-|o|GS
Gallbladder Consultation|GS|30|Y|Y|-|o|GS
Lump or Mass Evaluation|GS|30|Y|Y|-|o|GS
Post-operative Wound Check|GS|15|N|N|-|c|GS
Bariatric Surgery Consultation|GS|60|Y|Y|-|o|GS
Abscess Drainage|GS|30|N|Y|surgery|o|GS,FM
Varicose Vein Consultation|GS|30|N|Y|-|o|GS
Wound Care Visit|GS|30|Y|N|-|o|GS,POD
Hyperbaric Oxygen Therapy Consultation|GS|45|Y|Y|-|x|-
Chiropractic Evaluation|CHI|45|N|Y|-|x|-
Chiropractic Adjustment|CHI|15|N|N|-|x|-
Acupuncture Session|INT|60|N|Y|-|x|-
"""

# ---- aliases ----------------------------------------------------------------------------------
# New phrases, keyed by type NAME (resolved to ids at build time so a renamed type fails loudly).
NEW_ALIASES = {
    "medicare annual wellness": {"Medicare Annual Wellness Visit": 1.0, "Annual Wellness Visit": 0.8},
    "established patient physical": {"Annual Physical - Established Patient": 1.0},
    "same day appointment": {"Same-Day Sick Visit": 1.0, "Urgent Care Visit": 0.8},
    "virtual visit": {"Telehealth Visit": 1.0, "Telehealth Follow-up": 0.8},
    "blood pressure": {"Hypertension Follow-up": 1.0, "Blood Pressure Check": 0.8, "Hypertension Consultation": 0.6},
    "high blood pressure": {"Hypertension Follow-up": 1.0, "Hypertension Consultation": 0.8},
    "hypertension": {"Hypertension Follow-up": 1.0, "Hypertension Consultation": 0.8},
    "blood pressure check": {"Blood Pressure Check": 1.0},
    "after the hospital": {"Transitional Care Visit": 1.0},
    "hospital follow up": {"Transitional Care Visit": 1.0},
    "weight loss": {"Weight Management Consultation": 1.0, "Bariatric Surgery Consultation": 0.5},
    "lose weight": {"Weight Management Consultation": 1.0},
    "nutritionist": {"Nutrition Counseling": 1.0},
    "dietitian": {"Nutrition Counseling": 1.0},
    "quit smoking": {"Smoking Cessation Counseling": 1.0},
    "dot physical": {"DOT Physical": 1.0},
    "work physical": {"Pre-Employment Physical": 1.0, "DOT Physical": 0.7},
    "job physical": {"Pre-Employment Physical": 1.0},
    "ear wax": {"Ear Wax Removal": 1.0, "Ear Cleaning": 1.0},
    "tb test": {"TB Skin Test": 1.0},
    "shingles shot": {"Shingles Vaccine": 1.0},
    "std test": {"STI Testing": 1.0},
    "sti test": {"STI Testing": 1.0},
    "teen checkup": {"Adolescent Well Visit": 1.0, "Well-Child Visit": 0.7},
    "adhd": {"ADHD Evaluation": 1.0, "Adult ADHD Evaluation": 1.0},
    "adhd follow up": {"ADHD Follow-up": 1.0},
    "breastfeeding help": {"Lactation Consultation": 1.0},
    "lactation": {"Lactation Consultation": 1.0},
    "heart failure": {"Heart Failure Clinic Visit": 1.0},
    "irregular heartbeat": {"Arrhythmia Consultation": 1.0},
    "afib": {"Arrhythmia Consultation": 1.0},
    "high cholesterol": {"Lipid Clinic Consultation": 1.0, "Lipid Panel": 0.6},
    "calcium score": {"Coronary Calcium Score CT": 1.0},
    "nuclear stress test": {"Nuclear Stress Test": 1.0},
    "cardiac rehab": {"Cardiac Rehab Intake": 1.0},
    "biopsy": {"Skin Biopsy": 1.0},
    "psoriasis": {"Psoriasis Follow-up": 1.0},
    "wart": {"Wart Removal": 1.0},
    "hair loss": {"Hair Loss Consultation": 1.0},
    "mohs": {"Mohs Surgery": 1.0},
    "tattoo removal": {"Tattoo Removal Consultation": 1.0},
    "hair transplant": {"Hair Transplant Consultation": 1.0},
    "hurt my knee": {"Knee Injury Evaluation": 1.0, "Sports Injury Evaluation": 1.0, "Orthopedic Consultation": 0.7},
    "knee injury": {"Knee Injury Evaluation": 1.0, "Sports Injury Evaluation": 0.9},
    "torn acl": {"Knee Injury Evaluation": 1.0, "Sports Injury Evaluation": 0.8},
    "back pain": {"Back Pain Evaluation": 1.0, "Chronic Back Pain Evaluation": 0.8},
    "lower back pain": {"Back Pain Evaluation": 1.0, "Chronic Back Pain Evaluation": 0.8},
    "hurt my back": {"Back Pain Evaluation": 1.0},
    "sciatica": {"Back Pain Evaluation": 1.0, "Chronic Back Pain Evaluation": 1.0},
    "hip pain": {"Hip Pain Evaluation": 1.0},
    "carpal tunnel": {"Carpal Tunnel Evaluation": 1.0},
    "wrist pain": {"Hand and Wrist Consultation": 1.0},
    "joint replacement": {"Joint Replacement Consultation": 1.0},
    "knee replacement": {"Joint Replacement Consultation": 1.0},
    "sports medicine": {"Sports Medicine Consultation": 1.0},
    "sports doctor": {"Sports Medicine Consultation": 1.0},
    "injury": {"Sports Injury Evaluation": 1.0, "Sprain and Strain Evaluation": 0.8},
    "playing football": {"Sports Injury Evaluation": 1.0, "Knee Injury Evaluation": 0.7},
    "hurt playing": {"Sports Injury Evaluation": 1.0},
    "sprained ankle": {"Sprain and Strain Evaluation": 1.0, "Foot and Ankle Consultation": 0.8},
    "twisted my ankle": {"Sprain and Strain Evaluation": 1.0, "Foot and Ankle Consultation": 0.8},
    "sprain": {"Sprain and Strain Evaluation": 1.0},
    "concussion": {"Concussion Evaluation": 1.0},
    "hit my head": {"Concussion Evaluation": 1.0},
    "tennis elbow": {"Sports Injury Evaluation": 1.0},
    "runners knee": {"Running Injury Clinic Visit": 1.0, "Knee Injury Evaluation": 0.8},
    "shin splints": {"Running Injury Clinic Visit": 1.0},
    "migraine": {"Migraine Consultation": 1.0, "Neurology Consultation": 0.8},
    "emg": {"EMG / Nerve Conduction Study": 1.0},
    "nerve test": {"EMG / Nerve Conduction Study": 1.0},
    "multiple sclerosis": {"Multiple Sclerosis Follow-up": 1.0},
    "dizziness": {"Dizziness and Vertigo Evaluation": 1.0, "Balance Testing": 0.6},
    "vertigo": {"Dizziness and Vertigo Evaluation": 1.0},
    "pregnancy ultrasound": {"Prenatal Ultrasound": 1.0, "Anatomy Scan (20-Week Ultrasound)": 0.8},
    "anatomy scan": {"Anatomy Scan (20-Week Ultrasound)": 1.0},
    "postpartum": {"Postpartum Visit": 1.0},
    "pregnancy test": {"Pregnancy Confirmation Visit": 1.0},
    "menopause": {"Menopause Consultation": 1.0},
    "infertility": {"Infertility Consultation": 1.0, "IVF Consultation": 0.8},
    "ivf": {"IVF Consultation": 1.0},
    "high risk pregnancy": {"High-Risk Pregnancy Visit": 1.0},
    "diabetic eye exam": {"Diabetic Eye Exam": 1.0},
    "kids eye exam": {"Pediatric Eye Exam": 1.0},
    "dry eyes": {"Dry Eye Evaluation": 1.0},
    "lasik": {"LASIK Consultation": 1.0},
    "retina": {"Retina Consultation": 1.0},
    "pink eye": {"Pink Eye Visit": 1.0},
    "hearing aids": {"Hearing Aid Evaluation": 1.0},
    "snoring": {"Sleep Apnea Evaluation": 1.0, "Sleep Study Consultation": 0.8},
    "sleep apnea": {"Sleep Apnea Evaluation": 1.0, "Sleep Study Consultation": 1.0},
    "sleep study": {"Sleep Study Consultation": 1.0, "Overnight Sleep Study": 1.0},
    "cpap": {"CPAP Follow-up": 1.0},
    "ringing in my ears": {"Tinnitus Evaluation": 1.0},
    "tinnitus": {"Tinnitus Evaluation": 1.0},
    "cochlear implant": {"Cochlear Implant Evaluation": 1.0},
    "egd": {"Upper Endoscopy (EGD)": 1.0},
    "liver doctor": {"Hepatology Consultation": 1.0},
    "ibs": {"IBS Consultation": 1.0},
    "gerd": {"GERD Consultation": 1.0},
    "celiac": {"Celiac Disease Evaluation": 1.0},
    "thyroid nodule": {"Thyroid Nodule Consultation": 1.0, "Thyroid Ultrasound": 0.8},
    "osteoporosis": {"Osteoporosis Consultation": 1.0, "Bone Density Scan (DEXA)": 0.7},
    "insulin pump": {"Insulin Pump Training": 1.0},
    "diabetes class": {"Diabetes Education Class": 1.0},
    "psychiatric intake": {"Psychiatric Intake Assessment": 1.0},
    "couples counseling": {"Couples Therapy": 1.0},
    "marriage counseling": {"Couples Therapy": 1.0},
    "group therapy": {"Group Therapy": 1.0},
    "addiction": {"Substance Use Evaluation": 1.0},
    "substance use": {"Substance Use Evaluation": 1.0},
    "eating disorder": {"Eating Disorder Evaluation": 1.0},
    "shoulder mri": {"MRI - Shoulder": 1.0},
    "hip mri": {"MRI - Hip": 1.0},
    "ankle mri": {"MRI - Ankle": 1.0},
    "chest xray": {"Chest X-Ray": 1.0},
    "chest x ray": {"Chest X-Ray": 1.0},
    "knee xray": {"Knee X-Ray": 1.0},
    "lung cancer screening": {"Lung Cancer Screening CT": 1.0},
    "3d mammogram": {"3D Mammogram (Tomosynthesis)": 1.0},
    "pet scan": {"PET Scan": 1.0},
    "occupational therapy": {"Occupational Therapy Evaluation": 1.0, "Occupational Therapy Session": 0.9},
    "pelvic floor": {"Pelvic Floor Therapy": 1.0},
    "massage": {"Massage Therapy": 1.0},
    "urine test": {"Urinalysis": 1.0},
    "cholesterol": {"Lipid Panel": 1.0, "Lipid Clinic Consultation": 0.6},
    "drug test": {"Drug Screening": 1.0},
    "strep test": {"Strep Test": 1.0},
    "filling": {"Dental Filling": 1.0},
    "cavity": {"Dental Filling": 1.0},
    "root canal": {"Root Canal": 1.0},
    "pull a tooth": {"Tooth Extraction": 1.0},
    "wisdom teeth": {"Wisdom Tooth Consultation": 1.0},
    "braces": {"Orthodontic Consultation": 1.0},
    "vasectomy": {"Vasectomy Consultation": 1.0},
    "psa test": {"Prostate Screening (PSA)": 1.0},
    "incontinence": {"Urinary Incontinence Evaluation": 1.0},
    "allergy shots": {"Allergy Shots (Immunotherapy)": 1.0},
    "food allergy": {"Food Allergy Evaluation": 1.0},
    "rheumatologist": {"Rheumatology Consultation": 1.0},
    "arthritis": {"Arthritis Evaluation": 1.0, "Rheumatology Consultation": 0.8},
    "rheumatoid arthritis": {"Rheumatology Consultation": 1.0, "Arthritis Evaluation": 0.8},
    "lupus": {"Rheumatology Consultation": 1.0, "Lupus Follow-up": 0.7},
    "gout": {"Gout Consultation": 1.0},
    "kidney doctor": {"Nephrology Consultation": 1.0},
    "nephrologist": {"Nephrology Consultation": 1.0},
    "kidney disease": {"Nephrology Consultation": 1.0, "Kidney Disease Follow-up": 0.7},
    "dialysis": {"Dialysis Evaluation": 1.0},
    "kidney transplant": {"Kidney Transplant Evaluation": 1.0},
    "oncologist": {"Oncology Consultation": 1.0},
    "cancer doctor": {"Oncology Consultation": 1.0},
    "chemo": {"Chemotherapy Infusion": 1.0},
    "hematologist": {"Hematology Consultation": 1.0},
    "anemia": {"Anemia Evaluation": 1.0},
    "iron infusion": {"Iron Infusion": 1.0},
    "second opinion": {"Second Opinion Consultation": 1.0},
    "pain doctor": {"Pain Management Consultation": 1.0},
    "pain management": {"Pain Management Consultation": 1.0},
    "chronic pain": {"Pain Management Consultation": 1.0, "Chronic Back Pain Evaluation": 0.6},
    "epidural": {"Epidural Steroid Injection": 1.0},
    "nerve block": {"Nerve Block": 1.0},
    "trigger point": {"Trigger Point Injection": 1.0},
    "podiatrist": {"Podiatry Consultation": 1.0},
    "foot doctor": {"Podiatry Consultation": 1.0},
    "foot pain": {"Podiatry Consultation": 1.0, "Foot and Ankle Consultation": 1.0},
    "ingrown toenail": {"Ingrown Toenail Removal": 1.0},
    "heel pain": {"Plantar Fasciitis Evaluation": 1.0},
    "plantar fasciitis": {"Plantar Fasciitis Evaluation": 1.0},
    "bunion": {"Bunion Consultation": 1.0},
    "orthotics": {"Custom Orthotics Fitting": 1.0},
    "diabetic foot": {"Diabetic Foot Exam": 1.0},
    "pediatric cardiologist": {"Pediatric Cardiology Consultation": 1.0},
    "heart murmur": {"Heart Murmur Evaluation": 1.0},
    "pediatric neurologist": {"Pediatric Neurology Consultation": 1.0},
    "autism": {"Autism Evaluation": 1.0},
    "pediatric gi": {"Pediatric GI Consultation": 1.0},
    "picky eater": {"Pediatric Feeding Evaluation": 1.0},
    "surgeon": {"General Surgery Consultation": 1.0},
    "hernia": {"Hernia Consultation": 1.0},
    "gallbladder": {"Gallbladder Consultation": 1.0},
    "lump": {"Lump or Mass Evaluation": 1.0},
    "wound check": {"Post-operative Wound Check": 1.0},
    "weight loss surgery": {"Bariatric Surgery Consultation": 1.0},
    "abscess": {"Abscess Drainage": 1.0},
    "varicose veins": {"Varicose Vein Consultation": 1.0},
    "wound care": {"Wound Care Visit": 1.0},
    "hyperbaric": {"Hyperbaric Oxygen Therapy Consultation": 1.0},
    "chiropractor": {"Chiropractic Evaluation": 1.0, "Chiropractic Adjustment": 0.9},
    "adjustment": {"Chiropractic Adjustment": 1.0},
    "acupuncture": {"Acupuncture Session": 1.0},
}
# Extra weights merged into existing SF phrases (SF weights are kept as they are).
SF_ALIAS_EXTRAS = {
    "medicare wellness": {"Medicare Annual Wellness Visit": 1.0},
    "telehealth": {"Telehealth Visit": 1.0},
    "video visit": {"Telehealth Visit": 1.0},
    "telemedicine": {"Telehealth Visit": 1.0},
    "sick visit": {"Same-Day Sick Visit": 0.8},
    "same day": {"Same-Day Sick Visit": 0.9},
    "chest pain": {"Cardiology Consultation": 1.0},
    "skin check": {"Annual Skin Check": 1.0},
    "acne": {"Acne Consultation": 1.0},
    "rash": {"Rash Evaluation": 1.0},
    "eczema": {"Eczema Consultation": 1.0},
    "knee pain": {"Knee Injury Evaluation": 1.0},
    "shoulder pain": {"Shoulder Pain Evaluation": 1.0},
    "sports injury": {"Sports Injury Evaluation": 1.0},
    "migraines": {"Migraine Consultation": 1.0},
    "headaches": {"Migraine Consultation": 0.8},
    "pregnant": {"Pregnancy Confirmation Visit": 0.9},
    "eye doctor": {"Diabetic Eye Exam": 0.4},
    "ear infection": {"Ear Cleaning": 0.3},
    "colonoscopy": {"Colonoscopy": 1.0},
    "endoscopy": {"Upper Endoscopy (EGD)": 1.0},
    "acid reflux": {"GERD Consultation": 1.0},
    "thyroid": {"Thyroid Nodule Consultation": 0.6},
    "diabetes": {"Diabetes Education Class": 0.6},
    "therapy": {"Individual Therapy": 1.0},
    "therapist": {"Individual Therapy": 1.0},
    "counseling": {"Individual Therapy": 1.0},
    "xray": {"Chest X-Ray": 0.6, "Knee X-Ray": 0.6, "Spine X-Ray": 0.6},
    "x ray": {"Chest X-Ray": 0.6, "Knee X-Ray": 0.6, "Spine X-Ray": 0.6},
    "mri": {"MRI - Shoulder": 1.0, "MRI - Hip": 1.0, "MRI - Ankle": 1.0, "MRI - Abdomen": 1.0},
    "ct": {"CT - Head": 0.8, "CT - Chest": 0.8, "CT - Abdomen and Pelvis": 0.8},
    "cat scan": {"CT - Head": 0.8, "CT - Chest": 0.8, "CT - Abdomen and Pelvis": 0.8},
    "sonogram": {"Ultrasound - Abdomen": 0.7, "Ultrasound - Pelvic": 0.7},
    "mammo": {"Diagnostic Mammogram": 0.8, "3D Mammogram (Tomosynthesis)": 0.8},
    "physical therapy": {"Physical Therapy Re-evaluation": 0.6},
    "blood test": {"Lipid Panel": 0.6},
    "cholesterol test": {"Lipid Panel": 1.0},
    "toothache": {"Emergency Dental Visit": 1.0},
    "kidney stones": {"Kidney Stone Evaluation": 1.0},
    "asthma": {"Asthma Management Visit": 0.8},
    "allergies": {"Food Allergy Evaluation": 0.6},
}
NEW_LAY_TERMS = {
    "sports": "Sports Medicine", "athlete": "Sports Medicine", "injury": "Orthopedics", "injured": "Orthopedics",
    "foot": "Podiatry", "feet": "Podiatry", "toe": "Podiatry", "toenail": "Podiatry", "heel": "Podiatry",
    "cancer": "Oncology", "tumor": "Oncology", "chemo": "Oncology",
    "kidneys": "Nephrology", "renal": "Nephrology",
    "arthritis": "Rheumatology", "lupus": "Rheumatology", "rheumatology": "Rheumatology",
    "spine": "Pain Management", "hernia": "General Surgery", "surgeon": "General Surgery",
    "hurt my knee": "Orthopedics", "hurt my back": "Orthopedics", "back pain": "Orthopedics",
    "playing football": "Sports Medicine", "sprained ankle": "Sports Medicine",
    "twisted my ankle": "Sports Medicine", "sports injury": "Sports Medicine", "chest pain": "Cardiology",
    "kidney disease": "Nephrology", "blood pressure": "Internal Medicine", "chronic pain": "Pain Management",
    "my child": "Pediatrics", "my kid": "Pediatrics",
}
NEW_SPECIALTY_DEFAULT = {
    "Sports Medicine": "Sports Medicine Consultation", "Rheumatology": "Rheumatology Consultation",
    "Nephrology": "Nephrology Consultation", "Oncology": "Oncology Consultation",
    "Pain Management": "Pain Management Consultation", "Podiatry": "Podiatry Consultation",
    "Pediatric Cardiology": "Pediatric Cardiology Consultation",
    "Pediatric Neurology": "Pediatric Neurology Consultation",
    "Pediatric Gastroenterology": "Pediatric GI Consultation", "General Surgery": "General Surgery Consultation",
    "Chiropractic": "Chiropractic Evaluation", "Integrative Medicine": "Acupuncture Session",
}
NEW_SPECIALTY_SPOKEN = {
    "Sports Medicine": "sports medicine", "Rheumatology": "rheumatology", "Nephrology": "kidney care",
    "Oncology": "cancer care", "Pain Management": "pain management", "Podiatry": "foot care",
    "Pediatric Cardiology": "pediatric cardiology", "Pediatric Neurology": "pediatric neurology",
    "Pediatric Gastroenterology": "pediatric GI care", "General Surgery": "general surgery",
    "Chiropractic": "chiropractic care", "Integrative Medicine": "acupuncture",
}

# ---- table parsing ----------------------------------------------------------------------------


def _ranked(text: str) -> list[tuple[str, str]]:
    """Whitespace-separated "Name" or "Name:tag" tokens, first occurrence wins. A trailing "?"
    marks a name kept in the list only as a reminder that it already appears earlier."""
    out, seen = [], set()
    for tok in text.split():
        if tok.endswith("?"):
            continue
        name, _, tag = tok.partition(":")
        if name.lower() in seen:
            raise ValueError(f"duplicate name in table: {name}")
        if tag and tag not in TAGS:
            raise ValueError(f"unknown tag {tag!r} on {name}")
        seen.add(name.lower())
        out.append((name, tag))
    return out


def _interp(rank: int, anchors) -> float:
    for (r0, v0), (r1, v1) in zip(anchors, anchors[1:]):
        if r0 <= rank <= r1:
            t = (math.log(rank) - math.log(r0)) / (math.log(r1) - math.log(r0))
            return math.exp(math.log(v0) + t * (math.log(v1) - math.log(v0)))
    (r0, v0), (r1, v1) = anchors[-2], anchors[-1]
    slope = (math.log(v1) - math.log(v0)) / (math.log(r1) - math.log(r0))
    return math.exp(math.log(v1) + slope * (math.log(rank) - math.log(r1)))


def surname_table() -> list[tuple[str, str, float]]:
    """(surname, tag, weight per 100k). The tail carries the population mass outside the top table."""
    top = _ranked(CENSUS_SURNAMES)
    top_keys = {n.lower() for n, _ in top}
    tail = [(n, t) for n, t in _ranked(TAIL_SURNAMES) if n.lower() not in top_keys]
    rows = [(n, t, _interp(i + 1, CENSUS_ANCHORS)) for i, (n, t) in enumerate(top)]
    tail_mass = 100_000 - sum(w for _, _, w in rows)
    rows += [(n, t, tail_mass / len(tail)) for n, t in tail]
    return rows


def first_name_table(text: str, tail_text: str, anchors) -> list[tuple[str, float]]:
    rows = [(n, _interp(i + 1, anchors)) for i, (n, _) in enumerate(_ranked(text))]
    tail = _ranked(tail_text)
    tail_mass = 100_000 - sum(w for _, w in rows)
    return rows + [(n, tail_mass / len(tail)) for n, _ in tail]


def parse_specialties() -> dict[str, dict]:
    out = {}
    for line in PROVIDER_SPECIALTIES.strip().splitlines():
        parts = line.split()
        out[parts[0]] = {"weight": float(parts[1]), "female": float(parts[2]), "titles": parts[3],
                         "site_capability": parts[4] if len(parts) > 4 else None}
    return out


def parse_new_types() -> list[dict]:
    rows = []
    for line in NEW_TYPES.strip().splitlines():
        name, spec, minutes, ref, new, cap, flag, menu = line.split("|")
        rows.append({"name": name, "specialty": SPECIALTY[spec], "duration_min": int(minutes),
                     "requires_referral": ref == "Y", "new_patients_allowed": new == "Y",
                     "required_capability": None if cap == "-" else cap, "flag": flag,
                     "menu": [] if menu == "-" else menu.split(",")})
    return rows


class Sampler:
    """Weighted draw over a fixed list via cumulative weights (deterministic for a given rng)."""

    def __init__(self, items, weights):
        self.items = list(items)
        self.cum = []
        total = 0.0
        for w in weights:
            total += w
            self.cum.append(total)

    def __call__(self, rng: random.Random):
        return self.items[bisect.bisect_right(self.cum, rng.random() * self.cum[-1])]


def _pick(rng: random.Random, pairs):
    return Sampler([p[0] for p in pairs], [p[1] for p in pairs])(rng)


# ---- generation -------------------------------------------------------------------------------


def build_metros() -> list[dict]:
    out = []
    for mid, name, state, area, lat, lon, aliases, hoods in METROS:
        sites = []
        for line in hoods.strip().splitlines():
            f = line.strip().split("|")
            sites.append({"neighborhood": f[0], "lat": float(f[1]), "lon": float(f[2]), "zip3": f[3],
                          "city": f[4] if len(f) > 4 else name, "state": f[5] if len(f) > 5 else state})
        out.append({"id": mid, "name": name, "state": state, "area": area, "lat": lat, "lon": lon,
                    "aliases": aliases, "sites": sites})
    return out


def build_locations(rng: random.Random, metros: list[dict], sf_locations: list[dict]) -> list[dict]:
    locations = []
    for loc in sf_locations:
        hood, lat, lon, zip5 = SF_GEO[loc["id"]]
        locations.append({**loc, "state": "CA", "zip": zip5, "neighborhood": hood, "lat": lat, "lon": lon,
                          "metro_id": SF_METRO})
    n = len(locations)
    for metro in metros:
        if metro["id"] == SF_METRO:
            continue
        metro_locs = []
        for site in metro["sites"]:
            suffix = "Health Center" if site["neighborhood"] == "Downtown" else _pick(rng, SITE_SUFFIXES)
            caps = [c for c, rate in CAPABILITY_RATES if rng.random() < rate]
            metro_locs.append({
                "id": f"loc_{n:03d}", "name": f"{site['neighborhood']} {suffix}",
                "address": f"{rng.randint(100, 9899)} {rng.choice(STREETS)}", "city": site["city"],
                "phone": f"({metro['area']}) 555-{rng.randint(1000, 9999)}", "hours": _pick(rng, HOURS),
                "capabilities": caps, "state": site["state"], "zip": f"{site['zip3']}{rng.randint(1, 60):02d}",
                "neighborhood": site["neighborhood"],
                # ~0.3 mi of scatter so two sites never sit on the neighborhood's exact centroid
                "lat": round(site["lat"] + rng.gauss(0, 0.004), 4),
                "lon": round(site["lon"] + rng.gauss(0, 0.005), 4), "metro_id": metro["id"],
            })
            n += 1
        for cap, zero in (("imaging", ZERO_IMAGING_METROS), ("dental", ZERO_DENTAL_METROS)):
            if metro["id"] in zero:
                for loc in metro_locs:
                    loc["capabilities"] = [c for c in loc["capabilities"] if c != cap]
            elif not any(cap in loc["capabilities"] for loc in metro_locs):
                rng.choice(metro_locs)["capabilities"].append(cap)
        for loc in metro_locs:
            loc["capabilities"] = [c for c in CAPABILITY_ORDER if c in loc["capabilities"]]
        locations += metro_locs
    return locations


def build_types(sf_types: list[dict]) -> tuple[list[dict], dict[str, list[str]], dict[str, list[str]]]:
    """All types plus each provider specialty's core and optional menus (lists of type ids)."""
    types = [dict(t) for t in sf_types]
    core: dict[str, list[str]] = defaultdict(list)
    optional: dict[str, list[str]] = defaultdict(list)
    for i, row in enumerate(parse_new_types(), start=len(sf_types)):
        tid = f"appt_{i:03d}"
        t = {"id": tid, "name": row["name"], "specialty": row["specialty"], "duration_min": row["duration_min"],
             "requires_referral": row["requires_referral"], "new_patients_allowed": row["new_patients_allowed"]}
        if row["required_capability"]:
            t["required_capability"] = row["required_capability"]
        types.append(t)
        if row["flag"] == "x":
            continue
        for abbr in row["menu"]:
            (core if row["flag"] == "c" else optional)[abbr].append(tid)
    return types, dict(core), dict(optional)


def original_menus(sf: dict) -> dict[str, list[str]]:
    """Specialty abbr -> original type ids: the SF providers' menu for that specialty, else the
    original types filed under it (Ophthalmology, Urology, Physical Therapy have no SF providers)."""
    by_name = {v: k for k, v in SPECIALTY.items()}
    menus: dict[str, list[str]] = {}
    for p in sf["providers"]:
        ids = list(p["appointment_type_ids"])
        if menus.setdefault(by_name[p["specialty"]], ids) != ids:
            raise AssertionError(f"SF {p['specialty']} providers disagree on their menu")
    by_specialty: dict[str, list[str]] = defaultdict(list)
    for t in sf["appointment_types"]:
        by_specialty[by_name[t["specialty"]]].append(t["id"])
    for abbr in parse_specialties():
        if abbr not in menus and abbr in by_specialty:
            menus[abbr] = by_specialty[abbr]
    for abbr, ids in EXTRA_ORIGINAL_MENU.items():
        menus[abbr] = list(ids)
    return menus


class NameSampler:
    def __init__(self, metros: list[dict]):
        self.surnames = surname_table()
        self.tag_of = {n: t for n, t, _ in self.surnames}
        self.by_metro = {}
        for m in metros:
            weights = []
            for _, tag, w in self.surnames:
                if tag:
                    _, _, phys, default, regional = TAGS[tag]
                    w *= phys * regional.get(m["id"], default)
                weights.append(w)
            self.by_metro[m["id"]] = Sampler([n for n, _, _ in self.surnames], weights)
        self.ssa = {"M": self._sampler(first_name_table(SSA_MALE, SSA_MALE_TAIL, SSA_ANCHORS_M)),
                    "F": self._sampler(first_name_table(SSA_FEMALE, SSA_FEMALE_TAIL, SSA_ANCHORS_F))}
        self.heritage = {}
        for tag, (male, female) in HERITAGE_FIRST.items():
            for sex, text in (("M", male), ("F", female)):
                names = text.split()
                self.heritage[(tag, sex)] = Sampler(names, [1 / (i + 8) for i in range(len(names))])

    @staticmethod
    def _sampler(table):
        return Sampler([n for n, _ in table], [w for _, w in table])

    def draw(self, rng: random.Random, metro_id: str, sex: str) -> tuple[str, str]:
        last = self.by_metro[metro_id](rng)
        tag = self.tag_of[last]
        if tag and rng.random() < TAGS[tag][1]:
            first = self.heritage[(tag, sex)](rng)
        else:
            first = self.ssa[sex](rng)
        return first, last


def languages_for(rng: random.Random, tag: str, metro_id: str) -> list[str]:
    langs = ["English"]
    if tag:
        for lang, p in TAGS[tag][0]:
            if rng.random() < p:
                langs.append(lang)
    if "Spanish" not in langs and rng.random() < SPANISH_METROS.get(metro_id, 0.05):
        langs.append("Spanish")
    if rng.random() < 0.06:
        extra = rng.choice(OTHER_LANGUAGES)
        if extra not in langs:
            langs.append(extra)
    return langs


def allocate_providers(metros: list[dict], locations: list[dict], total: int, sf_count: int) -> dict[str, int]:
    """Providers per metro, proportional to its site count (largest remainder). SF keeps its originals."""
    sites = Counter(loc["metro_id"] for loc in locations)
    n_sites = sum(sites.values())
    sf_target = max(sf_count, round(total * sites[SF_METRO] / n_sites))
    rest = total - sf_target
    others = [m["id"] for m in metros if m["id"] != SF_METRO]
    other_sites = sum(sites[m] for m in others)
    exact = {m: rest * sites[m] / other_sites for m in others}
    alloc = {m: math.floor(v) for m, v in exact.items()}
    for m in sorted(others, key=lambda m: (-(exact[m] - alloc[m]), m))[: rest - sum(alloc.values())]:
        alloc[m] += 1
    return {SF_METRO: sf_target, **alloc}


def build_providers(rng: random.Random, metros: list[dict], locations: list[dict], sf: dict,
                    types: list[dict], core: dict, optional: dict, total: int) -> list[dict]:
    specs = parse_specialties()
    menus = original_menus(sf)
    type_by_id = {t["id"]: t for t in types}
    names = NameSampler(metros)
    loc_by_id = {loc["id"]: loc for loc in locations}
    locs_by_metro: dict[str, list[str]] = defaultdict(list)
    for loc in locations:
        locs_by_metro[loc["metro_id"]].append(loc["id"])
    partner = {}
    for a, b in ADJACENT_PAIRS:
        partner[a], partner[b] = b, a

    providers = [dict(p) for p in sf["providers"]]
    alloc = allocate_providers(metros, locations, total, len(providers))
    alloc[SF_METRO] -= len(providers)
    for metro in metros:
        mid = metro["id"]
        site_ids = locs_by_metro[mid]
        caps_here = {c for lid in site_ids for c in loc_by_id[lid]["capabilities"]}
        eligible = [(abbr, s["weight"]) for abbr, s in specs.items()
                    if (abbr != "OPH" or mid in OPHTHALMOLOGY_METROS)
                    and (s["site_capability"] is None or s["site_capability"] in caps_here)]
        spec_sampler = Sampler([a for a, _ in eligible], [w for _, w in eligible])
        for _ in range(alloc[mid]):
            # One child stream per provider, identity drawn first: tuning sites or menus never renames anyone.
            r = random.Random(rng.getrandbits(64))
            abbr = spec_sampler(r)
            spec = specs[abbr]
            title = _pick(r, TITLE_MIX[spec["titles"]])
            sex = "F" if r.random() < FEMALE_SHARE_BY_TITLE.get(title, spec["female"]) else "M"
            first, last = names.draw(r, mid, sex)

            k = min(_pick(r, SITES_PER_PROVIDER), len(site_ids))
            need = spec["site_capability"]
            pool = [lid for lid in site_ids if need is None or need in loc_by_id[lid]["capabilities"]]
            home = r.choice(pool)
            sites = [home] + r.sample([lid for lid in site_ids if lid != home], k - 1)
            if k >= 2 and mid in partner and r.random() < SPAN_P:
                sites[-1] = r.choice(locs_by_metro[partner[mid]])
            site_caps = {c for lid in sites for c in loc_by_id[lid]["capabilities"]}

            offered = list(menus.get(abbr, [])) + core.get(abbr, [])
            offered += [tid for tid in optional.get(abbr, []) if r.random() < OPTIONAL_TYPE_P]
            offered = [tid for tid in offered
                       if type_by_id[tid].get("required_capability") in (None, *site_caps)]

            providers.append({
                "id": f"prov_{len(providers):03d}", "name": f"Dr. {first} {last}", "title": title,
                "specialty": SPECIALTY[abbr], "location_ids": sorted(sites),
                "accepting_new_patients": r.random() < ACCEPTING_P,
                "languages": languages_for(r, names.tag_of[last], mid),
                "appointment_type_ids": sorted(set(offered)),
            })
    return providers


def build_aliases(sf_aliases: dict, types: list[dict]) -> dict:
    id_of = {t["name"]: t["id"] for t in types}

    def ids(weights: dict) -> dict:
        missing = [n for n in weights if n not in id_of]
        if missing:
            raise AssertionError(f"alias references unknown type names {missing}")
        return {id_of[n]: w for n, w in weights.items()}

    aliases = {}
    for phrase, weights in sf_aliases["aliases"].items():
        extra = ids(SF_ALIAS_EXTRAS.get(phrase, {}))
        aliases[phrase] = {**weights, **{k: v for k, v in extra.items() if k not in weights}}
    unknown = sorted(set(SF_ALIAS_EXTRAS) - set(sf_aliases["aliases"]))
    if unknown:
        raise AssertionError(f"SF_ALIAS_EXTRAS names phrases SF does not have: {unknown}")
    for phrase, weights in NEW_ALIASES.items():
        if phrase in aliases:
            raise AssertionError(f"new alias {phrase!r} collides with an SF alias")
        aliases[phrase] = ids(weights)
    lay = dict(sf_aliases["lay_terms"])
    for k, v in NEW_LAY_TERMS.items():
        lay.setdefault(k, v)
    default = dict(sf_aliases["specialty_default"])
    default.update({spec: id_of[name] for spec, name in NEW_SPECIALTY_DEFAULT.items()})
    spoken = dict(sf_aliases["specialty_spoken"])
    spoken.update(NEW_SPECIALTY_SPOKEN)
    for spec in sorted({t["specialty"] for t in types} - set(spoken)):
        raise AssertionError(f"no spoken form for specialty {spec!r}")
    return {"_doc": sf_aliases["_doc"], "aliases": aliases, "lay_terms": lay, "specialty_default": default,
            "specialty_spoken": spoken}


def generate(seed: int, scale: float = 1.0) -> tuple[dict, dict]:
    sf = json.loads(SF_CATALOG.read_text(encoding="utf-8"))
    sf_aliases = json.loads(SF_ALIASES.read_text(encoding="utf-8"))
    metros = build_metros()
    locations = build_locations(random.Random(f"{seed}:locations"), metros, sf["locations"])
    types, core, optional = build_types(sf["appointment_types"])
    total = max(len(sf["providers"]) + len(metros), round(FULL_PROVIDERS * scale))
    providers = build_providers(random.Random(f"{seed}:providers"), metros, locations, sf, types, core, optional,
                                total)
    catalog = {
        "policies": list(sf["policies"]),
        "metros": [{"id": m["id"], "name": m["name"], "state": m["state"], "aliases": m["aliases"],
                    "lat": m["lat"], "lon": m["lon"]} for m in metros],
        "locations": locations,
        "providers": providers,
        "appointment_types": types,
    }
    return catalog, build_aliases(sf_aliases, types)


# ---- checks and stats -------------------------------------------------------------------------


def bookable_rows(catalog: dict) -> list[tuple[str, str, str]]:
    locs = {loc["id"]: loc for loc in catalog["locations"]}
    types = {t["id"]: t for t in catalog["appointment_types"]}
    rows = []
    for p in catalog["providers"]:
        for tid in p["appointment_type_ids"]:
            cap = types[tid].get("required_capability")
            for lid in p["location_ids"]:
                if cap is None or cap in locs[lid]["capabilities"]:
                    rows.append((tid, p["id"], lid))
    return rows


def check(catalog: dict, scale: float) -> list[tuple[str, str, str]]:
    sf = json.loads(SF_CATALOG.read_text(encoding="utf-8"))
    metros = {m["id"] for m in catalog["metros"]}
    locs = {loc["id"]: loc for loc in catalog["locations"]}
    types = {t["id"]: t for t in catalog["appointment_types"]}
    partner = {a: b for a, b in ADJACENT_PAIRS} | {b: a for a, b in ADJACENT_PAIRS}
    _, core, optional = build_types(sf["appointment_types"])
    menus = original_menus(sf)
    abbr_of = {v: k for k, v in SPECIALTY.items()}

    assert catalog["policies"] == sf["policies"]
    assert len(metros) == len(METROS) == 40
    for loc in catalog["locations"]:
        assert all(loc.get(k) not in (None, "") for k in ("state", "zip", "neighborhood", "lat", "lon")), loc["id"]
        assert len(loc["zip"]) == 5 and loc["zip"].isdigit() and loc["metro_id"] in metros, loc["id"]
    for orig in sf["locations"]:
        assert {k: locs[orig["id"]][k] for k in orig} == orig, orig["id"]
    assert catalog["appointment_types"][: len(sf["appointment_types"])] == sf["appointment_types"]
    assert catalog["providers"][: len(sf["providers"])] == sf["providers"]

    for p in catalog["providers"][len(sf["providers"]):]:
        home = locs[p["location_ids"][0]]["metro_id"]
        p_metros = {locs[lid]["metro_id"] for lid in p["location_ids"]}
        assert all(lid in locs for lid in p["location_ids"]) and all(t in types for t in p["appointment_type_ids"])
        assert len(p_metros) == 1 or (len(p_metros) == 2 and sorted(p_metros) == sorted({home, partner.get(home)})), p
        abbr = abbr_of[p["specialty"]]
        allowed = set(menus.get(abbr, [])) | set(core.get(abbr, [])) | set(optional.get(abbr, []))
        assert set(p["appointment_type_ids"]) <= allowed, p["id"]
        site_caps = {c for lid in p["location_ids"] for c in locs[lid]["capabilities"]}
        for tid in p["appointment_type_ids"]:
            cap = types[tid].get("required_capability")
            assert cap is None or cap in site_caps, (p["id"], tid)
        if p["specialty"] == "Ophthalmology":
            assert p_metros <= set(OPHTHALMOLOGY_METROS)

    rows = bookable_rows(catalog)
    if scale == 1.0:
        assert ROWS_RANGE[0] <= len(rows) <= ROWS_RANGE[1], len(rows)
        assert len(catalog["providers"]) == FULL_PROVIDERS
    return rows


def name_stats(catalog: dict) -> dict:
    locs = {loc["id"]: loc for loc in catalog["locations"]}
    provs = catalog["providers"]
    names = Counter(p["name"] for p in provs)
    dup_names = {n: c for n, c in names.items() if c > 1}
    metros_of = defaultdict(set)
    surname_metro = Counter()
    for p in provs:
        metro = locs[p["location_ids"][0]]["metro_id"]
        metros_of[p["name"]].add(metro)
        surname_metro[(metro, p["name"].split()[-1])] += 1
    return {
        "providers": len(provs),
        "distinct_names": len(names),
        "extra_copies": len(provs) - len(names),
        "extra_copy_rate": (len(provs) - len(names)) / len(provs),
        "providers_sharing_a_name": sum(dup_names.values()),
        "sharing_rate": sum(dup_names.values()) / len(provs),
        "dup_names": sorted(dup_names.items(), key=lambda kv: (-kv[1], kv[0])),
        "cross_metro_dup_names": sorted(n for n in dup_names if len(metros_of[n]) > 1),
        "metros_of": {n: sorted(metros_of[n]) for n in dup_names},
        "distinct_surnames": len({p["name"].split()[-1] for p in provs}),
        "top_surname_in_metro": surname_metro.most_common(10),
    }


def report(catalog: dict, rows: list, aliases: dict, meta: dict) -> str:
    locs = {loc["id"]: loc for loc in catalog["locations"]}
    types = catalog["appointment_types"]
    provs = catalog["providers"]
    offered = {t for p in provs for t in p["appointment_type_ids"]}
    bookable_types = {r[0] for r in rows}
    unoffered = [t["name"] for t in types if t["id"] not in bookable_types]
    span = sum(1 for p in provs if len({locs[l]["metro_id"] for l in p["location_ids"]}) > 1)
    rows_by_metro = Counter(locs[r[2]]["metro_id"] for r in rows)
    oph_metros = sorted({locs[r[2]]["metro_id"] for r in rows if r[0] in {t["id"] for t in types
                                                                          if t["specialty"] == "Ophthalmology"}})
    caps = Counter(c for loc in catalog["locations"] for c in loc["capabilities"])
    downtown = sum(1 for loc in catalog["locations"] if loc["name"] == "Downtown Health Center")
    ns = name_stats(catalog)
    lines = [
        f"metros={len(catalog['metros'])} locations={len(catalog['locations'])} providers={len(provs)} "
        f"appointment_types={len(types)} policies={len(catalog['policies'])}",
        f"bookable_rows={len(rows)} (per metro min={min(rows_by_metro.values())} max={max(rows_by_metro.values())})",
        f"naive_tokens={meta['naive_tokens']} sha256={meta['sha256']}",
        f"unoffered types={len(unoffered)} ({len(unoffered) / len(types):.1%}): {unoffered}",
        f"offered-but-capability-blocked types={sorted(offered - bookable_types)}",
        f"ophthalmology metros={oph_metros}",
        f"capabilities across sites={dict(sorted(caps.items()))}; 'Downtown Health Center' sites={downtown}",
        f"providers spanning two metros={span} ({span / len(provs):.1%}); "
        f"accepting={sum(p['accepting_new_patients'] for p in provs) / len(provs):.1%}",
        f"titles={dict(Counter(p['title'] for p in provs).most_common())}",
        f"specialties(top 12)={Counter(p['specialty'] for p in provs).most_common(12)}",
        f"sites per provider={dict(sorted(Counter(len(p['location_ids']) for p in provs).items()))}",
        f"aliases={len(aliases['aliases'])} lay_terms={len(aliases['lay_terms'])}",
        f"exact full-name duplicates: {ns['extra_copies']} extra copies ({ns['extra_copy_rate']:.2%} of providers); "
        f"{ns['providers_sharing_a_name']} providers share a name ({ns['sharing_rate']:.2%}); "
        f"{len(ns['cross_metro_dup_names'])} duplicated names span metros",
        "duplicated names: " + "; ".join(f"{n} x{c} {ns['metros_of'][n]}" for n, c in ns["dup_names"]),
        f"distinct surnames={ns['distinct_surnames']}; top same-surname-within-metro: "
        + ", ".join(f"{m}:{s}={c}" for (m, s), c in ns["top_surname_in_metro"]),
    ]
    return "\n".join(lines)


# ---- output -----------------------------------------------------------------------------------


def dump_catalog(catalog: dict) -> str:
    """One record per line: diffable, and far fewer whitespace tokens than indent=2."""
    parts = []
    for key, records in catalog.items():
        body = ",\n".join(f"    {json.dumps(r)}" for r in records)
        parts.append(f'  "{key}": [\n{body}\n  ]')
    return "{\n" + ",\n".join(parts) + "\n}\n"


def dump_aliases(aliases: dict) -> str:
    parts = [f'  "_doc": {json.dumps(aliases["_doc"])}']
    for key in ("aliases", "lay_terms", "specialty_default", "specialty_spoken"):
        body = ",\n".join(f"    {json.dumps(k)}: {json.dumps(v)}" for k, v in aliases[key].items())
        parts.append(f'  "{key}": {{\n{body}\n  }}')
    return "{\n" + ",\n".join(parts) + "\n}\n"


def naive_tokens(text: str) -> int:
    import tiktoken  # venv-only dependency; generation itself is stdlib

    return len(tiktoken.get_encoding("o200k_base").encode(text))


def meta_for(text: str, label: str, catalog: dict, rows: list, **extra) -> dict:
    counts = {k: len(catalog[k]) for k in ("metros", "locations", "providers", "appointment_types") if k in catalog}
    counts["bookable_rows"] = len(rows)
    return {"label": label, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            # Compact JSON, the cheapest way to paste the catalog into a prompt (matches eval/naive_baseline_tokens.py).
            "naive_tokens": naive_tokens(json.dumps(catalog, separators=(",", ":"), ensure_ascii=False)),
            "counts": counts, **extra}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=20261002)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--scale", type=float, default=1.0, help="provider count multiplier (tests use < 1)")
    ap.add_argument("--skip-sf-meta", action="store_true", help="do not write backend/data/catalog.meta.json")
    args = ap.parse_args(argv)

    catalog, aliases = generate(args.seed, args.scale)
    rows = check(catalog, args.scale)
    text = dump_catalog(catalog)
    meta = meta_for(text, "National (synthetic, 40 metros)", catalog, rows, seed=args.seed, scale=args.scale)
    write(args.out / "catalog.json", text)
    write(args.out / "aliases.json", dump_aliases(aliases))
    write(args.out / "catalog.meta.json", json.dumps(meta, indent=2) + "\n")
    if not args.skip_sf_meta:
        sf_text = SF_CATALOG.read_bytes().decode("utf-8")
        sf = json.loads(sf_text)
        sf_meta = meta_for(sf_text, "SF sample (provided)", sf, bookable_rows(sf))
        write(SF_CATALOG.with_name("catalog.meta.json"), json.dumps(sf_meta, indent=2) + "\n")
    print(report(catalog, rows, aliases, meta))
    return meta


if __name__ == "__main__":
    main()

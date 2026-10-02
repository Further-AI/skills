# Classification traps common in insurance submissions

Each entry: the trap, the right code, how to tell. Verify codes by searching the reference CSVs — this list is guidance, the tables are the source of truth.

## Real estate & habitational
- **Condo / HOA / co-op owners' associations** → NAICS **813990** (Census index: "Condominium owners' associations", "Homeowners' associations"), SIC **8641** (SIC manual lists condominium and homeowners' associations under Civic, Social, and Fraternal Associations).
  Trap: **531311 Residential Property Managers** / SIC **6531**. That's the management *company* the association hires. Only use it when the insured is the management firm.
  Trap: **531110 Lessors of Residential Buildings** / SIC **6513**. That's an apartment owner renting units; an association doesn't lease units.
- **Apartment building owner** → 531110 / 6513. Look for rent rolls, single owner entity, "LLC" per property.
- **Commercial condo association** → still 813990 / 8641; the commercial nature shows up in the property/GL class, not NAICS.
- **Lessor's risk only (LRO) building owner, commercial tenants** → 531120 Lessors of Nonresidential Buildings / SIC 6512.

## Construction
- **General contractor vs. for-sale builder**: 236115/236116 build for others; 236117 builds on own land for sale. Evidence: who owns the land.
- **Specialty trades** live in 238xxx. A "construction company" that only does roofing is 238160, not 236xxx.
- **Remodelers**: 236118 residential remodelers.

## Services
- **Janitorial** 561720 / SIC 7349. **Pest control** 561710 / SIC 7342. **Landscaping** 561730 / SIC 0782.
- **Junk / debris removal**: usually 562119 Other Waste Collection (or 562111 Solid Waste Collection if municipal-style hauling); demolition debris tied to construction may be 238910. Ask.
- **Staffing / PEO** 561320 / 561330 — the insured's employees' actual work drives WC class, not NAICS.

## Product businesses
- **Manufacturer vs. wholesaler vs. retailer of the same product** → 31–33 makes it, 42 sells to businesses, 44–45 sells to consumers. The product is the same; the activity decides. Evidence: a plant and machinery (manufacturer), sales to resellers or contractors (wholesaler), a storefront or consumer website (retailer).
  Trap: classifying a distributor as a manufacturer because the submission describes the product in detail.

## Hospitality
- **Bar vs. restaurant** → 722410 Drinking Places / SIC 5813 when alcohol is the main business; 722511 Full-Service Restaurants / SIC 5812 when food is. Evidence: liquor receipts as a share of sales. This drives liquor liability, so ask if receipts aren't given.
- **Hotel vs. bed-and-breakfast** → 721110 Hotels and Motels / 721191 Bed-and-Breakfast Inns; both SIC 7011. Evidence: room count, whether meals come with the stay, owner-occupied.

## Transportation
- **For-hire trucking vs. own fleet** → 484xxx only when the insured hauls other people's freight for pay. A business delivering its own goods (a lumber yard's trucks) keeps its own industry code; the fleet shows up in the auto exposure, not NAICS.

## Non-profits
- Classify by what the organization does (a non-profit hospital is 622110, a food bank 624210), not by its tax-exempt status. 8641 / 813410 is for civic and social clubs, not every 501(c)(3).

## Holding / multi-entity
- A parent with several operating subsidiaries: classify each named insured separately. Don't average.
- "Holding company" 551112 only when the entity truly has no operations of its own.

## Signals the submission itself is inconsistent
- DBAs in unrelated industries attached to a non-profit association
- SOV rows in states/countries outside the described footprint
- Large stock/inventory values on an entity that shouldn't hold inventory
- Year built / stories / square footage values that are clearly swapped columns
When you see these, keep classifying the named insured on its best evidence and put the inconsistency in `flags` with a broker question. Never let a garbage schedule change the class.

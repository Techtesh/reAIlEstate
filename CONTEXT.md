# Mumbai New-Construction Discovery Platform

This platform helps Mumbai Metropolitan Region home buyers discover and compare new or under-construction residential projects. It turns buyer intent and heterogeneous project evidence into transparent, evidence-backed recommendations.

## Participants

**Buyer**:
A person seeking a home who defines requirements, receives recommendations, compares projects, and maintains a shortlist.
_Avoid_: Customer, client, user (when the buyer role is meant)

**Co-buyer**:
A person granted permission to review or edit a Buyer's shared requirement profile or shortlist.
_Avoid_: Collaborator, secondary buyer

**Developer representative**:
An authorized person who submits project information and evidence for an Organization.
_Avoid_: Seller, agent

**Organization**:
The developer entity responsible for one or more Projects and their submitted information.
_Avoid_: Builder (except when referring to the market-facing persona)

**Data reviewer**:
A platform role that resolves evidence conflicts and approves project facts for publication.
_Avoid_: Moderator, administrator

## Buyer intent and recommendations

**Requirement profile**:
A Buyer's versioned statement of housing needs, constraints, preferences, budget, and consent-backed personal data.
_Avoid_: Search, query, questionnaire

**Requirement version**:
The immutable submitted version of a Requirement profile used to generate a Recommendation snapshot.
_Avoid_: Draft (unless it has not been submitted)

**Frequent place**:
A destination relevant to a Buyer's routine, together with travel preferences and tolerance.
_Avoid_: Address, location (when the destination concept is intended)

**Hard constraint**:
A requirement that eliminates a Project before ranking.
_Avoid_: Preference, priority

**Preference**:
A desirable characteristic that influences ranking without disqualifying an otherwise eligible Project.
_Avoid_: Hard constraint

**Recommendation snapshot**:
A time-bound, reproducible ranked result for one Requirement version, including the evidence and method version used to produce it.
_Avoid_: Search result, live score

**Recommendation item**:
One Project's rank, score, confidence, and explanation within a Recommendation snapshot.
_Avoid_: Listing (unless referring to its presentation)

**Shortlist**:
A Buyer's saved set of Projects for later comparison or sharing.
_Avoid_: Recommendation list

## Project catalogue and evidence

**Project**:
A new or under-construction residential development represented by normalized, publishable facts and associated evidence.
_Avoid_: Property, listing

**Home configuration**:
A Project's offered residential unit type and its comparable characteristics, including carpet area.
_Avoid_: Flat type, inventory

**Carpet area**:
The canonical area measure used to compare Home configurations; other area definitions remain explicitly labelled.
_Avoid_: Built-up area, super-built-up area

**Evidence document**:
An original submitted or authoritative document that supports one or more candidate facts about a Project.
_Avoid_: Brochure (unless describing that specific document type)

**Fact candidate**:
A proposed Project value derived from Evidence or an authoritative source, retaining provenance, confidence, and review state.
_Avoid_: Project fact, verified fact

**Project fact**:
A reviewed, selected, versioned value that represents a Project for publication or decision support.
_Avoid_: Claim, candidate

**Fact conflict**:
A disagreement between Fact candidates for the same Project attribute that requires a resolution.
_Avoid_: Error

**Review task**:
Work assigned to a Data reviewer to resolve evidence, verification, or publication uncertainty.
_Avoid_: Ticket

## Verification, metrics, and trust

**MahaRERA verification**:
The comparison of a Project against an authoritative MahaRERA record, with a result and timestamp; it is not legal certification.
_Avoid_: Legal approval, guaranteed verification

**Verification snapshot**:
A time-stamped record of authoritative-source data used to assess a Project's MahaRERA verification.
_Avoid_: Project fact

**Project metric**:
A versioned, time-bound calculation about a Project, retaining its definition, inputs, confidence, and freshness.
_Avoid_: Fact, guarantee

**Evidence confidence**:
The stated reliability of a fact or calculation based on its provenance, review status, and freshness.
_Avoid_: Accuracy, certainty

**Nearby amenity**:
An amenity outside a Project that may contribute to a preference only when it is within 5 km; it is never equivalent to an in-project amenity.
_Avoid_: In-society amenity

**Amenity substitution**:
The explicit comparison of an in-project amenity with an acceptable nearby alternative; the alternative is never treated as identical.
_Avoid_: Amenity match

**Local transaction evidence**:
Licensed, normalized records of completed transactions used to provide market context, distinctly from asking prices.
_Avoid_: Sale price, market value

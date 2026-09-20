import { z } from 'zod'

export const weightKeys = ['commute', 'budget', 'space', 'societyAmenities', 'nearbyAmenities', 'microMarket', 'projectConfidence'] as const
export type WeightKey = typeof weightKeys[number]
export type Weights = Record<WeightKey, number>
export const weightLabels: Record<WeightKey, string> = {
  commute: 'Commute', budget: 'Budget', space: 'Space', societyAmenities: 'Society amenities',
  nearbyAmenities: 'Nearby amenities', microMarket: 'Micro-market', projectConfidence: 'Evidence confidence',
}
export const defaults: Weights = { commute: .30, budget: .25, space: .20, societyAmenities: .10, nearbyAmenities: .05, microMarket: .05, projectConfidence: .05 }
export function allocatedWeightPoints(weights: Weights) {
  return weightKeys.reduce((total, key) => total + Math.round(weights[key] * 100), 0)
}

const positive = z.number().positive().max(100000).optional()
const money = z.number().int().positive().max(Number.MAX_SAFE_INTEGER).optional()
export const placesSchema = z.array(z.object({
    placeId: z.string().trim().min(1).max(255), label: z.string().trim().min(2).max(100), precision: z.enum(['locality', 'exact']),
    latitude: z.number().min(-90).max(90).optional(), longitude: z.number().min(-180).max(180).optional(),
    frequencyPerWeek: z.number().int().min(0).max(28), modes: z.array(z.enum(['car', 'transit', 'walking', 'cycling'])).min(1),
    maxOneWayMinutes: z.number().int().min(5).max(300),
  }).superRefine((place, context) => {
    if ((place.latitude == null) !== (place.longitude == null) || (place.precision === 'exact' && place.latitude == null)) context.addIssue({ code: 'custom', message: 'Set both latitude and longitude for an exact destination.' })
  })).max(10).superRefine((places, context) => {
    const labels = places.map(place => place.label.trim().toLocaleLowerCase())
    if (new Set(labels).size !== labels.length || new Set(places.map(place => place.placeId)).size !== places.length) context.addIssue({ code: 'custom', message: 'Combine repeated destinations into one entry.' })
  })
export type CommutePlace = z.infer<typeof placesSchema>[number]
export type LocatedPlace = Pick<CommutePlace, 'placeId' | 'label'> & { precision: 'exact'; latitude: number; longitude: number }
export const draftSchema = z.object({
  places: placesSchema,
  home: z.object({
    configurations: z.array(z.string()).min(1, 'Choose at least one home configuration.'),
    carpetAreaSqFt: z.object({ minimum: positive, preferred: positive, maximum: positive }),
    bathroomsMinimum: z.number().int().min(0).max(10), bathroomsIsHardConstraint: z.boolean(),
  }),
  societyAmenities: z.array(z.object({ code: z.string(), importance: z.enum(['required', 'preferred']), substituteAccepted: z.boolean() })),
  nearbyAmenities: z.array(z.object({ code: z.string(), importance: z.enum(['required', 'preferred']), maximumDistanceMeters: z.number().int().min(100).max(20000) })),
  microMarket: z.object({ preferredLocalities: z.array(z.string()) }),
  budget: z.object({ currency: z.literal('INR'), purchaseMinimum: money, purchaseMaximum: money, allInMaximum: money, isHardConstraint: z.boolean() }),
  weights: z.object({ commute: z.number().min(0).max(1), budget: z.number().min(0).max(1), space: z.number().min(0).max(1), societyAmenities: z.number().min(0).max(1), nearbyAmenities: z.number().min(0).max(1), microMarket: z.number().min(0).max(1), projectConfidence: z.number().min(0).max(1) }),
  additionalNeeds: z.string().max(2000),
}).superRefine((draft, context) => {
  const area = draft.home.carpetAreaSqFt
  if (area.minimum && area.maximum && area.minimum > area.maximum) context.addIssue({ code: 'custom', path: ['home', 'carpetAreaSqFt'], message: 'Maximum carpet area must be at least the minimum.' })
  if (area.preferred && ((area.minimum && area.preferred < area.minimum) || (area.maximum && area.preferred > area.maximum))) context.addIssue({ code: 'custom', path: ['home', 'carpetAreaSqFt'], message: 'Preferred carpet area must be within your range.' })
  const budget = draft.budget
  if (!budget.purchaseMaximum && !budget.allInMaximum) context.addIssue({ code: 'custom', path: ['budget'], message: 'Enter a purchase or all-in maximum budget.' })
  if (budget.purchaseMinimum && budget.purchaseMaximum && budget.purchaseMinimum > budget.purchaseMaximum) context.addIssue({ code: 'custom', path: ['budget'], message: 'Maximum purchase budget must be at least the minimum.' })
  if (budget.allInMaximum && budget.purchaseMaximum && budget.allInMaximum < budget.purchaseMaximum) context.addIssue({ code: 'custom', path: ['budget'], message: 'All-in maximum must cover your purchase maximum.' })
  const weightTotal = Object.values(draft.weights).reduce((sum, value) => sum + value, 0)
  if (weightTotal <= 1e-9) context.addIssue({ code: 'custom', path: ['weights'], message: 'Give at least one priority a value above zero.' })
  if (weightTotal > 1 + 1e-9) context.addIssue({ code: 'custom', path: ['weights'], message: 'Priority points cannot exceed 100.' })
})
export type Draft = z.infer<typeof draftSchema>
export function newDraft(): Draft {
  return { places: [], home: { configurations: [], carpetAreaSqFt: {}, bathroomsMinimum: 0, bathroomsIsHardConstraint: true }, societyAmenities: [], nearbyAmenities: [], microMarket: { preferredLocalities: [] }, budget: { currency: 'INR', isHardConstraint: true }, weights: { ...defaults }, additionalNeeds: '' }
}
export type User = { id: number; username: string; email: string; isStaff?: boolean }
export type Profile = { id: string; status: string; version: number; sections: Partial<Draft>; updatedAt: string }
export function resumeDraft(sections: Partial<Draft>): Draft {
  const base = newDraft()
  return { ...base, ...sections, home: { ...base.home, ...sections.home }, budget: { ...base.budget, ...sections.budget }, weights: { ...base.weights, ...sections.weights }, microMarket: { ...base.microMarket, ...sections.microMarket } }
}
export type Configuration = { id?: string; name?: string; roomRectangleSumSqFt?: number | null; configurationType: string; carpetAreaMin: string | null; carpetAreaMax: string | null; priceMin: string | null; priceMax: string | null; bathrooms: number | null }
export function calculatedRoomArea(area: number | null | undefined) {
  return area != null && Number.isFinite(area) && area > 0 ? `${area.toLocaleString('en-IN', { maximumFractionDigits: 2 })} sq ft` : 'Unknown'
}
export function configurationArea(configuration: Configuration) {
  const minimum = Number(configuration.carpetAreaMin), maximum = Number(configuration.carpetAreaMax)
  if (!Number.isFinite(minimum) || minimum <= 0) return 'Unknown'
  return !Number.isFinite(maximum) || maximum <= minimum ? `${minimum.toLocaleString('en-IN')} sq ft` : `${minimum.toLocaleString('en-IN')} - ${maximum.toLocaleString('en-IN')} sq ft`
}
export type MapStation = {
  id: string; name: string; kind: 'metro' | 'railway'; line: string;
  location: { latitude: number; longitude: number };
  serviceStatus: 'unverified' | 'operational' | 'planned' | 'under_construction';
  source: 'user_candidate' | 'saved_reference' | 'authority'; asOf: string | null; sourceUrl: string | null;
}
export type MapLine = {
  id: string; name: string; kind: MapStation['kind']; stationIds: string[];
  geometryStatus: 'schematic_unverified'; source: 'user_candidate';
}
export type MapConnection = Omit<MapLine, 'source'> & { source: 'user_requested'; lineNames: string[] }
export type Project = {
  projectPic?: string | null;
  priceMin: string | null; priceMax: string | null;
  id: string; submittedName: string; locality: string; status: string; mahaReraId: string;
  verificationStatus: string; verificationCheckedAt: string | null; updatedAt: string;
  latitude?: string | null; longitude?: string | null; configurations: Configuration[]; amenities: string[];
  locationStatus?: 'published' | 'reviewed' | 'provisional' | 'unavailable';
  facts?: { code: string; value: unknown; unit: string; sourceClass: string; reviewedAt: string | null; status?: string }[];
}
export type Component = { code: string; weight: number; score?: number; status: string; destinations?: { status: string; durationMinutes?: number; mode?: string }[] }
export type Match = { projectId: string; rank: number; score: number; confidence: number; components: Component[]; reasonCodes: string[]; freshness: { projectFactsAsOf: string } }
export type Snapshot = { id: string; status: string; profileVersion: number; algorithmVersion: string; generatedAt: string | null; expiresAt: string | null; items: Match[]; excludedSummary: { reasonCode: string; count: number }[] }
export type Shortlist = { id: string; name: string; entries: { project: string; note: string }[] }
export function coordinates(project: { latitude?: string | number | null; longitude?: string | number | null }): [number, number] | null {
  if (project.latitude == null || project.longitude == null || String(project.latitude).trim() === '' || String(project.longitude).trim() === '') return null
  const latitude = Number(project.latitude), longitude = Number(project.longitude)
  return Number.isFinite(latitude) && Number.isFinite(longitude) && Math.abs(latitude) <= 90 && Math.abs(longitude) <= 180 ? [latitude, longitude] : null
}
export function lowestPrice(project: Project) {
  return priceValue(project.priceMin)
}
export function priceValue(value: string | number | null | undefined) {
  if (value == null || String(value).trim() === '') return null
  const amount = Number(value)
  return Number.isFinite(amount) && amount > 0 ? amount : null
}
export function projectPrice(project: Pick<Project, 'priceMin' | 'priceMax'>, compact = false) {
  const minimum = priceValue(project.priceMin), maximum = priceValue(project.priceMax)
  if (minimum == null || (maximum != null && maximum < minimum)) return 'contact to know'
  const format = compact ? (value: number) => value >= 100000 ? `INR ${(value / 10000000).toFixed(2)} Cr` : inr(value) : inr
  return maximum == null || maximum === minimum ? format(minimum) : `${format(minimum)} - ${format(maximum)}`
}
export function configurationPrice(configuration: Pick<Configuration, 'priceMin' | 'priceMax'>) {
  return projectPrice(configuration)
}
export function projectStatusLabel(project: Project) {
  return project.status === 'published' ? 'Published project' : project.status === 'needs_review' ? 'In progress: awaiting review' : 'Processing listing'
}
export const inr = (value: number | null | undefined) => value == null ? 'contact to know' : new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(value)
export const dateLabel = (value: string | null | undefined) => value ? new Date(value).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata' }) : 'Unknown'
export function componentLabel(code: string) { return Object.entries(weightLabels).find(([key]) => key.toUpperCase() === code)?.[1] ?? code.replaceAll('_', ' ').toLowerCase() }
export function reasonLabel(code: string) {
  const reasons: Record<string, string> = { ALL_HARD_CONSTRAINTS_MET: 'Passed the available hard-constraint checks', GOOD_WEIGHTED_COMMUTE: 'Good weighted commute fit', CONFIGURATION_UNAVAILABLE: 'Requested configuration unavailable', BELOW_HARD_CARPET_AREA: 'Below minimum carpet area', INSUFFICIENT_BATHROOMS: 'Below minimum bathroom count', OVER_HARD_BUDGET: 'Above your hard budget', REQUIRED_AMENITY_UNAVAILABLE: 'Required society amenity unavailable' }
  return reasons[code] ?? code.replaceAll('_', ' ').toLowerCase()
}
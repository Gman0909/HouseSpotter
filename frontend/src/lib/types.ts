export interface Profile {
  id: number
  name: string
  mode: 'buy' | 'rent'
  active: boolean
  criteria_version: number
  min_price: number | null
  max_price: number | null
  min_beds: number | null
  max_beds: number | null
  min_baths: number | null
  property_types: string[]
  tenures: string[]
  locations: { label: string; lat: number; lng: number; radius_km: number }[]
  must_haves: Record<string, boolean>
  nice_to_haves: { key?: string; text?: string; kind: string; weight: number }[]
  commutes: { label: string; lat: number; lng: number; max_minutes: number; mode: string }[]
  qol_weights: Record<string, number>
  brief: string
  alert_threshold: number
  alert_channels: string[]
  alert_digest: boolean
  quiet_hours: { start: string; end: string } | null
}

export interface PropertyCard {
  id: number
  address: string
  postcode: string | null
  lat: number | null
  lng: number | null
  beds: number | null
  baths: number | null
  property_type: string | null
  tenure: string | null
  epc: string | null
  image: string | null
  price: number | null
  price_qualifier: string | null
  mode: string | null
  status: string | null
  url: string | null
  portal: string | null
  first_seen: string | null
  price_history: { date: string; price: number }[]
  score: number | null
  passed_filters: boolean | null
  rationale: string | null
  access_score: number | null
  access_peak: number | null
  access_offpeak: number | null
  viewed: boolean
}

export interface MilestoneInfo {
  id: number
  label: string
  place: string
  lat: number
  lng: number
  weight: number
}

export interface TravelMode {
  minutes: number | null
  km: number | null
  provider: 'ors' | 'estimate' | 'none'
}

export interface TravelRow {
  id: number
  label: string
  weight: number
  lat: number
  lng: number
  modes: { car: TravelMode; cycle: TravelMode; walk: TravelMode }
}

export interface TravelInfo {
  milestones: TravelRow[]
  access_score: number | null
  access_peak: number | null
  access_offpeak: number | null
  avg_car_minutes: number | null
}

export interface BreakdownEntry {
  label: string
  kind: string
  weight: number
  satisfaction: number
  reason?: string
}

export interface Match {
  score: number
  passed_filters: boolean
  breakdown: BreakdownEntry[]
  rationale: string
}

export interface PropertyDetail {
  property: {
    id: number
    address: string
    postcode: string | null
    lat: number | null
    lng: number | null
    beds: number | null
    baths: number | null
    property_type: string | null
    tenure: string | null
    floor_area_sqm: number | null
    epc: string | null
    features: string[]
    description: string
    image_urls: string[]
    floorplan_urls: string[]
  }
  listings: {
    id: number
    portal: string
    url: string
    price: number | null
    price_qualifier: string | null
    status: string
    mode: string
    first_seen: string
    last_seen: string
    price_history: { date: string; price: number }[]
  }[]
  match: Match | null
}

export interface SavedListInfo {
  id: number
  name: string
  count: number
}

export interface ResearchStatus {
  state: 'idle' | 'running' | 'done' | 'error'
  progress?: string | null
  error?: string | null
  areas?: number
  started_at?: string
  finished_at?: string
}

export interface AreaSearchInfo {
  id: number
  profile_id: number
  name: string
  source: 'profile' | 'custom'
  locations: { label: string; lat: number; lng: number; radius_km: number }[]
  stale: boolean
  created_at: string
  last_run_at: string | null
  status: ResearchStatus
  result_count: number
}

export interface AreaInfo {
  id: number
  code: string
  name: string
  lat: number | null
  lng: number | null
  metrics: Record<string, unknown>
  scores: Record<string, number>
  narrative: string
  listing_stats: { listing_count?: number; median_price?: number | null; in_budget?: boolean | null }
  match_count: number
  refreshed_at: string
}

export interface ScrapeRunInfo {
  id: number
  portal: string
  started_at: string
  finished_at: string | null
  found: number
  new: number
  updated: number
  blocked: boolean
  error: string
}

export type Role = "ADMIN" | "USER";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: User;
}

export interface InterpretedQuery {
  raw_query: string;
  category?: string | null;
  category_slug?: string | null;
  location?: string | null;
  confidence: "high" | "medium" | "low";
  notes: string[];
}

export interface SearchInterpretResponse {
  interpreted: InterpretedQuery;
  estimated_max_results: number;
}

export interface JobProgressStats {
  total: number;
  completed: number;
  failed: number;
  emails_found: number;
  phones_found: number;
  websites_found: number;
}

export interface Job {
  id: string;
  query_raw: string;
  parsed_category: string;
  parsed_location: string;
  status:
    | "queued"
    | "discovering"
    | "enriching"
    | "crawling"
    | "extracting"
    | "deduplicating"
    | "generating_report"
    | "completed"
    | "failed"
    | "cancelled";
  stage_detail: string;
  progress: number;
  provider: string;
  max_results: number;
  /** What the user originally asked for (before server-side clamping). */
  requested_results?: number;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error_message: string;
  progress_stats: JobProgressStats;
}

export interface Business {
  id: string;
  name: string;
  category: string;
  address: string;
  phone: string;
  phone_normalized: string;
  phone_source: string;
  email: string;
  email_confidence: string;
  website: string;
  website_normalized: string;
  rating?: number | null;
  review_count?: number | null;
  maps_url: string;
  dedup_status: string;
  source: string;
  crawl_summary?: string;
  /** Every distinct email discovered for this lead, primary first. */
  emails: string[];
  /** Every distinct phone discovered for this lead, primary first. */
  phones: string[];
  /** len(emails) + len(phones) — how much contact data this lead carries. */
  contact_count: number;
}

export interface Contact {
  id: string;
  email: string;
  phone: string;
  phone_normalized: string;
  contact_name: string;
  source_url: string;
  source: string;
  confidence: string;
  verified: boolean;
}

export interface CrawlPage {
  id: string;
  url: string;
  page_type: string;
  http_status?: number | null;
  status: string;
  emails_found: number;
  phones_found: number;
  duration_ms: number;
}

export interface BusinessDetail extends Business {
  latitude?: number | null;
  longitude?: number | null;
  opening_hours: string;
  contacts: Contact[];
  crawl_pages: CrawlPage[];
}

export interface PaginatedBusinesses {
  items: Business[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  /** User-requested count; 0 = unknown (legacy jobs). */
  requested_results?: number;
}

export interface Summary {
  total: number;
  unique: number;
  duplicates_removed: number;
  with_phone: number;
  with_website: number;
  with_email: number;
  high_confidence_emails: number;
  failed_websites: number;
  /** Distinct email addresses collected across the whole search. */
  emails_total: number;
  /** Distinct phone numbers collected across the whole search. */
  phones_total: number;
}

export interface AdminUserRow extends User {
  created_at?: string | null;
  job_count: number;
}

export interface AdminJobRow {
  id: string;
  user_email: string;
  query_raw: string;
  status: string;
  progress: number;
  total: number;
  emails_found: number;
  provider: string;
  created_at?: string | null;
  finished_at?: string | null;
}

export interface UsageSummary {
  today_requests: number;
  today_cost: number;
  month_requests: number;
  month_cost: number;
  by_provider: { provider: string; requests: number; estimated_cost: number }[];
}

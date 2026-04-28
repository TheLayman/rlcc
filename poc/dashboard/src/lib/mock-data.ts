export interface SaleLine {
  line_timestamp?: string;
  line_number?: number;
  item_id?: string;
  item_description: string;
  item_quantity: number;
  item_unit_price: number;
  total_amount: number;
  scan_attribute?: string;
  item_attribute?: string;
  discount_type?: string;
  discount?: number;
  granted_by?: string;
}

export interface PaymentLine {
  line_timestamp?: string;
  line_number?: number;
  line_attribute?: string;
  payment_description?: string;
  amount: number;
  card_type?: string;
  approval_code?: string;
}

export interface TotalLine {
  line_timestamp?: string;
  line_number?: number;
  line_attribute: string;
  amount: number;
}

export interface TransactionEvent {
  line_timestamp?: string;
  line_attribute?: string;
  event_description?: string;
}

export interface TimelineEvent {
  ts: string;
  source: string;
  type: string;
  data: Record<string, unknown>;
}

export interface Transaction {
  id: string;
  shop_id: string;
  shop_name?: string;
  cam_id: string;
  pos_id: string;
  cashier_name: string;
  timestamp: Date;
  started_at?: string;
  committed_at?: string;
  transaction_total: number;
  risk_level: 'High' | 'Medium' | 'Low';
  triggered_rules?: string[];
  status?: string;
  fraud_category?: string;
  notes?: string;
  source?: string;
  bill_number?: string;
  transaction_number?: string;
  transaction_type?: string;
  employee_purchase?: boolean;
  clip_url?: string | null;
  clip_reason?: string;
  timeline_url?: string;
  receipt_status?: 'generated' | 'not_generated' | 'unknown';
  items?: SaleLine[];
  payments?: PaymentLine[];
  totals?: TotalLine[];
  events?: TransactionEvent[];
  cv_non_seller_present?: boolean | null;
  cv_non_seller_count?: number;
  cv_receipt_detected?: boolean | null;
  cv_confidence?: string;
}

export type ClipStatus =
  | 'available'
  | 'pending'
  | 'outside_buffer'
  | 'camera_unmapped'
  | 'retention_expired'
  | 'not_recorded'
  | 'unknown';

export type CVConfidence = 'LOW' | 'MEDIUM' | 'HIGH' | 'VERY_HIGH';

export interface Alert {
  id: string;
  transaction_id: string;
  shop_id: string;
  shop_name?: string;
  cashier_name: string;
  risk_level: 'High' | 'Medium' | 'Low';
  triggered_rules?: string[];
  timestamp: Date;
  status: string;
  cam_id?: string;
  pos_id?: string;
  clip_url?: string | null;
  clip_status?: ClipStatus;
  clip_reason?: string;
  cv_confidence?: CVConfidence | '';
  remarks?: string;
  source?: string;
}

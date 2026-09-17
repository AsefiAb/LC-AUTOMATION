export type Platform = "doordash" | "ubereats" | "grubhub";

export type IssueType =
  | "missing_item"
  | "wrong_item"
  | "quality"
  | "never_delivered"
  | "whole_order_refund"
  | "other";

export interface ErrorCharge {
  id: string;
  platform: Platform;
  storeName: string;
  orderId: string;
  orderDate: string; // ISO date
  chargeDate: string; // ISO date the deduction hit the payout
  issueType: IssueType;
  itemsAffected: string[];
  amount: number; // dollars clawed back from the restaurant
  customerId: string; // pseudonymous handle for repeat-abuse detection
  customerNote: string;
  hasPhotoEvidence: boolean; // did the platform attach customer photo proof
  disputeDeadline: string; // ISO date after which it can't be disputed
}

export type Priority = "high" | "medium" | "low" | "none";

export interface Assessment {
  disputable: boolean;
  priority: Priority;
  recoverable: number;
  reasons: string[];
  daysLeft: number;
}

export type Decision = "pending" | "approved" | "skipped" | "exported";

export interface DisputeItem {
  charge: ErrorCharge;
  assessment: Assessment;
  draft: string;
  decision: Decision;
}

import { useEffect, useMemo, useRef, useState } from 'react';
import { Video } from 'lucide-react';

import type { TimelineEvent, Transaction } from '@/lib/mock-data';
import { Badge } from '@/app/components/ui/badge';
import { Button } from '@/app/components/ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/app/components/ui/sheet';
import { BACKEND_BASE } from '@/lib/runtime-config';

interface TransactionDetailDrawerProps {
  transaction: Transaction | null;
  billData: any | null;
  open: boolean;
  onClose: () => void;
}

function riskBadgeClasses(level: string): string {
  switch (level) {
    case 'High':
      return 'bg-red-50 text-red-700 border border-red-200';
    case 'Medium':
      return 'bg-amber-50 text-amber-700 border border-amber-200';
    case 'Low':
      return 'bg-green-50 text-green-700 border border-green-200';
    default:
      return 'bg-gray-50 text-gray-700 border border-gray-200';
  }
}

function statusBadgeClasses(status: string): string {
  switch (status) {
    case 'fraudulent':
    case 'Fraudulent':
      return 'bg-red-50 text-red-700 border border-red-200';
    case 'suspicious':
    case 'reviewing':
      return 'bg-amber-50 text-amber-700 border border-amber-200';
    case 'genuine':
    case 'resolved':
    case 'Genuine':
      return 'bg-green-50 text-green-700 border border-green-200';
    default:
      return 'bg-gray-50 text-gray-600 border border-gray-200';
  }
}

function parseDate(value?: string | Date | null): Date | null {
  if (!value) return null;
  if (value instanceof Date) return value;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function TransactionDetailDrawer({ transaction, billData, open, onClose }: TransactionDetailDrawerProps) {
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [videoLoadError, setVideoLoadError] = useState('');
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    if (!transaction || !open) {
      return;
    }
    setTimelineLoading(true);
    fetch(`${BACKEND_BASE}/api/transactions/${transaction.id}`)
      .then(response => response.json())
      .then(payload => setTimeline(payload.timeline || []))
      .catch(() => setTimeline([]))
      .finally(() => setTimelineLoading(false));
  }, [transaction, open]);

  const items = useMemo(() => transaction?.items ?? billData?.items ?? [], [transaction, billData]);
  const payModes = useMemo(() => transaction?.payments ?? billData?.payModes ?? [], [transaction, billData]);
  const totals = useMemo(() => transaction?.totals ?? Object.entries(billData?.totals ?? {}).map(([line_attribute, amount]) => ({ line_attribute, amount })), [transaction, billData]);
  const clipUrl = transaction?.clip_url ? `${BACKEND_BASE}${transaction.clip_url}` : null;

  useEffect(() => {
    setVideoLoadError('');
    if (!open && videoRef.current) {
      videoRef.current.pause();
    }
  }, [clipUrl, open]);

  const clipStart = useMemo(() => {
    const startedAt = parseDate(transaction?.started_at);
    if (!startedAt) return null;
    return new Date(startedAt.getTime() - 30_000);
  }, [transaction?.started_at]);

  const seekToTimestamp = (value?: string) => {
    if (!videoRef.current || !clipStart || !value) return;
    const target = parseDate(value);
    if (!target) return;
    const seconds = Math.max(0, (target.getTime() - clipStart.getTime()) / 1000);
    videoRef.current.currentTime = seconds;
    videoRef.current.play().catch(() => {});
  };

  if (!transaction) return null;

  return (
    <Sheet open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <SheetContent side="right" className="w-full sm:max-w-2xl overflow-y-auto p-0">
        <SheetHeader className="sticky top-0 z-10 bg-white border-b border-gray-200 px-6 py-4">
          <div className="flex items-center justify-between pr-6">
            <SheetTitle className="text-blue-900 text-lg font-semibold">
              {transaction.id}
            </SheetTitle>
            {transaction.status && (
              <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ${statusBadgeClasses(transaction.status)}`}>
                {transaction.status}
              </span>
            )}
          </div>
        </SheetHeader>

        <div className="flex flex-col gap-4 p-6">
          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <h3 className="mb-3 text-sm font-semibold text-blue-700 uppercase tracking-wide">Store & Device Info</h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              <div>
                <dt className="text-gray-500">Store</dt>
                <dd className="font-medium text-gray-900">{transaction.shop_name || transaction.shop_id}</dd>
                {transaction.shop_name && <dd className="text-xs text-gray-400 font-mono">{transaction.shop_id}</dd>}
              </div>
              <div>
                <dt className="text-gray-500">POS ID</dt>
                <dd className="font-medium text-gray-900">{transaction.pos_id}</dd>
              </div>
              <div>
                <dt className="text-gray-500">Camera ID</dt>
                <dd className="font-medium text-gray-900">{transaction.cam_id || 'Unmapped'}</dd>
              </div>
              <div>
                <dt className="text-gray-500">Receipt Status</dt>
                <dd className="font-medium text-gray-900 capitalize">{transaction.receipt_status || 'unknown'}</dd>
              </div>
            </dl>
          </section>

          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <h3 className="mb-3 text-sm font-semibold text-blue-700 uppercase tracking-wide">Transaction Details</h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              <div>
                <dt className="text-gray-500">Cashier</dt>
                <dd className="font-medium text-gray-900">{transaction.cashier_name}</dd>
              </div>
              <div>
                <dt className="text-gray-500">Timestamp</dt>
                <dd className="font-medium text-gray-900">
                  {new Date(transaction.timestamp).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}
                </dd>
              </div>
              <div>
                <dt className="text-gray-500">Bill No</dt>
                <dd className="font-medium text-gray-900">{transaction.bill_number || 'Pending'}</dd>
              </div>
              <div>
                <dt className="text-gray-500">Type</dt>
                <dd className="font-medium text-gray-900">{transaction.transaction_type || 'CompletedNormally'}</dd>
              </div>
              <div>
                <dt className="text-gray-500">Employee Purchase</dt>
                <dd className="font-medium text-gray-900">{transaction.employee_purchase ? 'Yes' : 'No'}</dd>
              </div>
              <div>
                <dt className="text-gray-500">Total</dt>
                <dd className="font-medium text-gray-900">₹{transaction.transaction_total.toLocaleString('en-IN')}</dd>
              </div>
            </dl>
          </section>

          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <h3 className="mb-3 text-sm font-semibold text-blue-700 uppercase tracking-wide">Payments & Totals</h3>
            <div className="grid grid-cols-3 gap-3 mb-3">
              <div className="bg-gray-50 rounded-lg p-3">
                <span className="text-xs text-gray-500">Paid</span>
                <p className="text-lg font-bold text-gray-900">₹{transaction.transaction_total.toLocaleString('en-IN')}</p>
              </div>
              <div className="bg-blue-50 rounded-lg p-3">
                <span className="text-xs text-blue-600">Payment lines</span>
                <p className="text-lg font-bold text-blue-900">{payModes.length}</p>
              </div>
              <div className="bg-amber-50 rounded-lg p-3">
                <span className="text-xs text-amber-600">Rule hits</span>
                <p className="text-lg font-bold text-amber-900">{transaction.triggered_rules?.length || 0}</p>
              </div>
            </div>
            <div className="space-y-2">
              {payModes.map((payment, index) => (
                <div key={index} className="flex justify-between text-sm bg-gray-50 rounded px-3 py-2">
                  <span>{payment.line_attribute || payment.payment_description || 'Unknown'}</span>
                  <span className="font-medium">₹{Number(payment.amount || 0).toLocaleString('en-IN')}</span>
                </div>
              ))}
              {totals.map((total, index) => (
                <div key={index} className="flex justify-between text-sm text-gray-700 bg-white border border-gray-100 rounded px-3 py-2">
                  <span>{total.line_attribute}</span>
                  <span className="font-medium">₹{Number(total.amount || 0).toLocaleString('en-IN')}</span>
                </div>
              ))}
            </div>
          </section>

          {items.length > 0 && (
            <section className="rounded-lg border border-gray-200 bg-white p-4">
              <h3 className="mb-3 text-sm font-semibold text-blue-700 uppercase tracking-wide">
                Items ({items.length})
              </h3>
              <div className="space-y-2">
                {items.map((item, index) => (
                  <button
                    key={index}
                    type="button"
                    className="w-full text-left flex items-start justify-between gap-3 p-3 bg-gray-50 rounded-lg hover:bg-blue-50"
                    onClick={() => seekToTimestamp(item.line_timestamp)}
                  >
                    <div className="flex-1">
                      <div className="text-sm font-medium text-gray-900">{item.item_description || '—'}</div>
                      <div className="flex gap-2 mt-1 flex-wrap">
                        {item.scan_attribute && <Badge className="bg-sky-50 text-sky-700 border-sky-200">{item.scan_attribute}</Badge>}
                        {item.item_attribute && item.item_attribute !== 'None' && <Badge className="bg-amber-50 text-amber-700 border-amber-200">{item.item_attribute}</Badge>}
                        {item.discount_type && item.discount_type !== 'NoLineDiscount' && <Badge className="bg-violet-50 text-violet-700 border-violet-200">{item.discount_type}</Badge>}
                        {item.granted_by && <Badge className="bg-gray-50 text-gray-700 border-gray-200">Granted by {item.granted_by}</Badge>}
                      </div>
                      {item.line_timestamp && (
                        <div className="mt-2 text-xs text-blue-600">Click to seek to {new Date(item.line_timestamp).toLocaleTimeString('en-IN')}</div>
                      )}
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-semibold text-gray-900">
                        ₹{Number(item.total_amount || 0).toLocaleString('en-IN')}
                      </div>
                      <div className="text-xs text-gray-400">
                        {item.item_quantity || 1} x ₹{Number(item.item_unit_price || 0).toLocaleString('en-IN')}
                      </div>
                      {!!item.discount && (
                        <div className="text-xs text-amber-600">-₹{Number(item.discount).toLocaleString('en-IN')}</div>
                      )}
                    </div>
                  </button>
                ))}
              </div>
            </section>
          )}

          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <h3 className="mb-3 text-sm font-semibold text-blue-700 uppercase tracking-wide">Risk Assessment</h3>
            <div className="mb-3 flex items-center gap-3">
              <span className="text-sm text-gray-500">Risk Level</span>
              <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${riskBadgeClasses(transaction.risk_level)}`}>
                {transaction.risk_level}
              </span>
            </div>
            {!!transaction.triggered_rules?.length && (
              <ul className="space-y-1">
                {transaction.triggered_rules.map((rule, index) => (
                  <li key={index} className="flex items-start gap-2 rounded-md bg-gray-50 px-3 py-2 text-sm text-gray-700">
                    <span className="mt-0.5 block h-1.5 w-1.5 flex-shrink-0 rounded-full bg-red-400" />
                    {rule}
                  </li>
                ))}
              </ul>
            )}
            {transaction.notes && (
              <div className="mt-3 rounded-md bg-gray-50 p-3 text-sm text-gray-600">
                <span className="font-medium text-gray-700">Notes: </span>
                {transaction.notes}
              </div>
            )}
          </section>

          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-blue-700 uppercase tracking-wide">Tagged Video & Timeline</h3>
              {clipUrl && (
                <Button variant="outline" asChild className="gap-2 border-blue-200 text-blue-700 hover:bg-blue-50">
                  <a href={clipUrl} target="_blank" rel="noreferrer">
                    <Video className="h-4 w-4" />
                    Open Clip
                  </a>
                </Button>
              )}
            </div>
            {clipUrl ? (
              <div className="space-y-3">
                <video
                  key={clipUrl}
                  ref={videoRef}
                  controls
                  playsInline
                  preload="metadata"
                  className="w-full rounded-lg border border-gray-200 bg-black"
                  onLoadedData={() => setVideoLoadError('')}
                  onError={() => setVideoLoadError('Clip could not be loaded in the browser. Try Open Clip to verify the stream response.')}
                >
                  <source src={clipUrl} type="video/mp4" />
                </video>
                {videoLoadError && (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                    {videoLoadError}
                  </div>
                )}
              </div>
            ) : (
              <div className="text-center py-4 text-sm text-gray-400 border border-dashed border-gray-200 rounded-lg">
                Clip not available for this transaction yet
              </div>
            )}
            <div className="mt-4 space-y-2 max-h-72 overflow-auto">
              {timelineLoading ? (
                <div className="text-sm text-gray-400">Loading timeline…</div>
              ) : timeline.length === 0 ? (
                <div className="text-sm text-gray-400">No timeline events available</div>
              ) : (
                timeline.map((event, index) => (
                  <button
                    key={`${event.ts}-${index}`}
                    type="button"
                    className="w-full text-left rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 hover:bg-blue-50"
                    onClick={() => seekToTimestamp(event.ts)}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm font-medium text-gray-800">{event.type.replace(/_/g, ' ')}</span>
                      <span className="text-xs text-gray-500">{new Date(event.ts).toLocaleTimeString('en-IN')}</span>
                    </div>
                    <div className="text-xs text-gray-500 mt-1">{event.source}</div>
                  </button>
                ))
              )}
            </div>
          </section>
        </div>
      </SheetContent>
    </Sheet>
  );
}

import { format } from 'date-fns';
import { Bell, PlayCircle, Search, ShoppingBag, Video } from 'lucide-react';

import type { Alert, Transaction } from '@/lib/mock-data';
import { Badge } from '@/app/components/ui/badge';
import { Button } from '@/app/components/ui/button';
import { Card } from '@/app/components/ui/card';

interface VideoReviewViewProps {
  alerts: Alert[];
  transactions: Transaction[];
  onOpenAlert: (alert: Alert) => void;
  onOpenTransaction: (transaction: Transaction) => void;
}

interface VideoItem {
  id: string;
  type: 'alert' | 'transaction';
  title: string;
  shopName: string;
  cashierName: string;
  posId: string;
  camId: string;
  riskLevel: string;
  status: string;
  timestamp: Date;
  tags: string[];
  alert?: Alert;
  transaction?: Transaction;
}

function badgeClassForRisk(level: string) {
  if (level === 'High') return 'border-red-200 bg-red-50 text-red-700';
  if (level === 'Medium') return 'border-amber-200 bg-amber-50 text-amber-700';
  return 'border-green-200 bg-green-50 text-green-700';
}

export function VideoReviewView({
  alerts,
  transactions,
  onOpenAlert,
  onOpenTransaction,
}: VideoReviewViewProps) {
  const transactionMap = new Map(transactions.map(transaction => [transaction.id, transaction]));

  const coveredTransactions = new Set<string>();
  const items: VideoItem[] = [];

  alerts.forEach(alert => {
    const linkedTransaction = alert.transaction_id && alert.transaction_id !== 'N/A'
      ? transactionMap.get(alert.transaction_id)
      : undefined;
    const hasClip = Boolean(alert.clip_url || linkedTransaction?.clip_url);

    if (!hasClip) {
      return;
    }

    if (linkedTransaction) {
      coveredTransactions.add(linkedTransaction.id);
    }

    items.push({
      id: `alert:${alert.id}`,
      type: 'alert',
      title: alert.transaction_id && alert.transaction_id !== 'N/A' ? alert.transaction_id : alert.id,
      shopName: alert.shop_name || alert.shop_id,
      cashierName: alert.cashier_name || linkedTransaction?.cashier_name || 'Unknown',
      posId: alert.pos_id || linkedTransaction?.pos_id || 'Unknown',
      camId: alert.cam_id || linkedTransaction?.cam_id || 'Unmapped',
      riskLevel: alert.risk_level,
      status: alert.status,
      timestamp: alert.timestamp,
      tags: alert.triggered_rules || linkedTransaction?.triggered_rules || [],
      alert,
      transaction: linkedTransaction,
    });
  });

  transactions.forEach(transaction => {
    if (!transaction.clip_url || coveredTransactions.has(transaction.id)) {
      return;
    }

    items.push({
      id: `transaction:${transaction.id}`,
      type: 'transaction',
      title: transaction.id,
      shopName: transaction.shop_name || transaction.shop_id,
      cashierName: transaction.cashier_name || 'Unknown',
      posId: transaction.pos_id || 'Unknown',
      camId: transaction.cam_id || 'Unmapped',
      riskLevel: transaction.risk_level,
      status: transaction.status || 'pending',
      timestamp: transaction.timestamp,
      tags: transaction.triggered_rules || [],
      transaction,
    });
  });

  items.sort((left, right) => right.timestamp.getTime() - left.timestamp.getTime());

  const alertClipCount = items.filter(item => item.type === 'alert').length;
  const transactionClipCount = items.filter(item => item.type === 'transaction').length;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Card className="border-gray-200 p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm text-gray-500">All Videos</div>
              <div className="text-3xl font-bold text-gray-800">{items.length}</div>
            </div>
            <Video className="h-8 w-8 text-blue-500" />
          </div>
        </Card>
        <Card className="border-red-200 bg-red-50 p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm text-red-600">Alert Videos</div>
              <div className="text-3xl font-bold text-red-700">{alertClipCount}</div>
            </div>
            <Bell className="h-8 w-8 text-red-500" />
          </div>
        </Card>
        <Card className="border-emerald-200 bg-emerald-50 p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm text-emerald-600">Transaction Clips</div>
              <div className="text-3xl font-bold text-emerald-700">{transactionClipCount}</div>
            </div>
            <ShoppingBag className="h-8 w-8 text-emerald-500" />
          </div>
        </Card>
      </div>

      <Card className="border-gray-200 shadow-sm">
        <div className="flex items-center justify-between border-b border-gray-200 p-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-800">Video Review</h3>
            <p className="text-sm text-gray-500">Open recorded clips from alerts and completed transactions.</p>
          </div>
          <Badge variant="outline" className="border-gray-200 text-gray-500">
            {items.length} clips
          </Badge>
        </div>

        <div className="max-h-[720px] divide-y divide-gray-100 overflow-y-auto">
          {items.length === 0 ? (
            <div className="py-16 text-center text-gray-400">
              <Search className="mx-auto mb-3 h-12 w-12 opacity-30" />
              <p>No videos available yet</p>
              <p className="mt-1 text-sm">Clips will appear here when alerts or transactions have saved footage.</p>
            </div>
          ) : (
            items.map(item => (
              <div key={item.id} className="flex items-start justify-between gap-4 p-4 hover:bg-gray-50">
                <div className="min-w-0 flex-1">
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm font-medium text-gray-800">{item.title}</span>
                    <Badge className={item.type === 'alert' ? 'border-red-200 bg-red-50 text-red-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}>
                      {item.type === 'alert' ? 'Alert Clip' : 'Transaction Clip'}
                    </Badge>
                    <Badge className={badgeClassForRisk(item.riskLevel)}>{item.riskLevel}</Badge>
                    <Badge variant="outline" className="border-gray-200 text-gray-600 capitalize">
                      {item.status}
                    </Badge>
                  </div>

                  <div className="text-sm text-gray-600">
                    Store: {item.shopName} | Cashier: {item.cashierName} | POS: {item.posId}
                  </div>
                  <div className="mt-1 text-xs text-gray-400">
                    {format(item.timestamp, 'MMM dd, yyyy HH:mm:ss')} | Camera: {item.camId}
                  </div>

                  {item.tags.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {item.tags.slice(0, 4).map(tag => (
                        <Badge key={`${item.id}-${tag}`} variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>

                <Button
                  size="sm"
                  className="shrink-0 gap-1.5 bg-blue-600 text-white hover:bg-blue-700"
                  onClick={() => {
                    if (item.type === 'alert' && item.alert) {
                      onOpenAlert(item.alert);
                      return;
                    }
                    if (item.transaction) {
                      onOpenTransaction(item.transaction);
                    }
                  }}
                >
                  <PlayCircle className="h-3.5 w-3.5" />
                  View Video
                </Button>
              </div>
            ))
          )}
        </div>
      </Card>
    </div>
  );
}

import { useMemo } from 'react';
import { Card } from '@/app/components/ui/card';
import { Badge } from '@/app/components/ui/badge';
import { Transaction } from '@/lib/mock-data';
import { ShieldCheck, ShieldAlert, AlertTriangle } from 'lucide-react';

interface HeatmapViewProps {
  transactions: Transaction[];
  storeNames?: Record<string, string>;
}

interface StoreData {
  store_id: string;
  store_name: string;
  total: number;
  genuine: number;
  suspicious: number;
  fraudulent: number;
  flagged: number;
  flag_rate: number;
  revenue: number;
  avg_ticket: number;
}

export function HeatmapView({ transactions, storeNames = {} }: HeatmapViewProps) {

  const stores = useMemo(() => {
    const map: Record<string, StoreData> = {};

    transactions.forEach(t => {
      if (!map[t.shop_id]) {
        map[t.shop_id] = {
          store_id: t.shop_id,
          store_name: t.shop_name || storeNames[t.shop_id] || t.shop_id,
          total: 0, genuine: 0, suspicious: 0, fraudulent: 0,
          flagged: 0, flag_rate: 0, revenue: 0, avg_ticket: 0,
        };
      }
      const s = map[t.shop_id];
      s.total++;
      s.revenue += t.transaction_total;
      if (t.status === 'genuine') s.genuine++;
      else if (t.status === 'suspicious') { s.suspicious++; s.flagged++; }
      else if (t.status === 'fraudulent') { s.fraudulent++; s.flagged++; }
      else s.flagged++; // unknown status counts as flagged
    });

    return Object.values(map).map(s => ({
      ...s,
      flag_rate: s.total > 0 ? (s.flagged / s.total) * 100 : 0,
      avg_ticket: s.total > 0 ? s.revenue / s.total : 0,
    })).sort((a, b) => b.total - a.total);
  }, [transactions, storeNames]);

  const maxTotal = Math.max(...stores.map(s => s.total), 1);

  const getHeatBg = (flagRate: number) => {
    if (flagRate >= 30) return 'bg-red-50 border-red-200';
    if (flagRate >= 15) return 'bg-amber-50 border-amber-200';
    if (flagRate >= 5) return 'bg-yellow-50 border-yellow-200';
    return 'bg-green-50 border-green-200';
  };

  const getHeatIcon = (flagRate: number) => {
    if (flagRate >= 15) return <ShieldAlert className="h-5 w-5 text-red-500" />;
    if (flagRate >= 5) return <AlertTriangle className="h-5 w-5 text-amber-500" />;
    return <ShieldCheck className="h-5 w-5 text-green-500" />;
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-gray-800 mb-1">Store Heatmap</h2>
        <p className="text-sm text-gray-500">Store risk overview — sized by volume, colored by flag rate</p>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 text-xs">
        <span className="text-gray-500">Risk level:</span>
        <div className="flex items-center gap-1.5"><div className="w-3 h-3 rounded bg-green-200 border border-green-300" /><span className="text-gray-600">Low (&lt;5%)</span></div>
        <div className="flex items-center gap-1.5"><div className="w-3 h-3 rounded bg-yellow-200 border border-yellow-300" /><span className="text-gray-600">Moderate (5-15%)</span></div>
        <div className="flex items-center gap-1.5"><div className="w-3 h-3 rounded bg-amber-200 border border-amber-300" /><span className="text-gray-600">High (15-30%)</span></div>
        <div className="flex items-center gap-1.5"><div className="w-3 h-3 rounded bg-red-200 border border-red-300" /><span className="text-gray-600">Critical (&gt;30%)</span></div>
      </div>

      {stores.length === 0 ? (
        <div className="text-center py-16 text-gray-400">No transaction data available</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {stores.map(store => (
            <Card
              key={store.store_id}
              className={`border p-4 transition-all hover:shadow-md ${getHeatBg(store.flag_rate)}`}
            >
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h3 className="font-semibold text-gray-800">{store.store_name}</h3>
                  <span className="text-xs text-gray-400 font-mono">{store.store_id}</span>
                </div>
                {getHeatIcon(store.flag_rate)}
              </div>

              {/* Volume bar */}
              <div className="mb-3">
                <div className="flex justify-between text-xs text-gray-500 mb-1">
                  <span>{store.total} transactions</span>
                  <span>{'\u20B9'}{store.revenue.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</span>
                </div>
                <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-400 rounded-full"
                    style={{ width: `${(store.total / maxTotal) * 100}%` }}
                  />
                </div>
              </div>

              {/* Status breakdown */}
              <div className="grid grid-cols-3 gap-2 mb-3">
                <div className="text-center bg-white/60 rounded p-1.5">
                  <div className="text-sm font-bold text-green-700">{store.genuine}</div>
                  <div className="text-[10px] text-gray-500">Genuine</div>
                </div>
                <div className="text-center bg-white/60 rounded p-1.5">
                  <div className="text-sm font-bold text-amber-700">{store.suspicious}</div>
                  <div className="text-[10px] text-gray-500">Suspicious</div>
                </div>
                <div className="text-center bg-white/60 rounded p-1.5">
                  <div className="text-sm font-bold text-red-700">{store.fraudulent}</div>
                  <div className="text-[10px] text-gray-500">Fraudulent</div>
                </div>
              </div>

              {/* Footer stats */}
              <div className="flex items-center justify-between text-xs">
                <Badge className={`${store.flag_rate >= 15 ? 'bg-red-100 text-red-700 border-red-200' : store.flag_rate >= 5 ? 'bg-amber-100 text-amber-700 border-amber-200' : 'bg-green-100 text-green-700 border-green-200'}`}>
                  {store.flag_rate.toFixed(1)}% flagged
                </Badge>
                <span className="text-gray-500">Avg {'\u20B9'}{store.avg_ticket.toLocaleString('en-IN', { maximumFractionDigits: 0 })}/txn</span>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/app/components/ui/table';
import { Card } from '@/app/components/ui/card';
import { Badge } from '@/app/components/ui/badge';
import { TrendingUp, TrendingDown, AlertCircle, Store } from 'lucide-react';
import { Transaction } from '@/lib/mock-data';
import { useMemo } from 'react';

interface EmployeeScorecardViewProps {
  transactions: Transaction[];
  storeNames?: Record<string, string>;
}

interface StoreScorecard {
  store_id: string;
  store_name: string;
  total_transactions: number;
  flagged_transactions: number;
  genuine_count: number;
  suspicious_count: number;
  fraudulent_count: number;
  fraud_rate: number;
  high_risk: number;
  medium_risk: number;
  total_value: number;
  manual_entry_count: number;
  void_count: number;
  discount_count: number;
  manual_entry_rate: number;
  void_rate: number;
  discount_rate: number;
}

export function EmployeeScorecardView({ transactions, storeNames = {} }: EmployeeScorecardViewProps) {

  const storeScores = useMemo(() => {
    const storeMap: Record<string, StoreScorecard> = {};

    transactions.forEach(t => {
      if (!storeMap[t.shop_id]) {
        storeMap[t.shop_id] = {
          store_id: t.shop_id,
          store_name: t.shop_name || storeNames[t.shop_id] || t.shop_id,
          total_transactions: 0,
          flagged_transactions: 0,
          genuine_count: 0,
          suspicious_count: 0,
          fraudulent_count: 0,
          fraud_rate: 0,
          high_risk: 0,
          medium_risk: 0,
          total_value: 0,
          manual_entry_count: 0,
          void_count: 0,
          discount_count: 0,
          manual_entry_rate: 0,
          void_rate: 0,
          discount_rate: 0,
        };
      }
      const s = storeMap[t.shop_id];
      s.total_transactions++;
      s.total_value += t.transaction_total;
      if (t.risk_level === 'High') { s.flagged_transactions++; s.high_risk++; }
      else if (t.risk_level === 'Medium') { s.flagged_transactions++; s.medium_risk++; }
      if (t.status === 'genuine') s.genuine_count++;
      else if (t.status === 'suspicious') s.suspicious_count++;
      else if (t.status === 'fraudulent') s.fraudulent_count++;
      if (t.triggered_rules?.some(rule => rule.includes('manual_entry'))) s.manual_entry_count++;
      if (t.triggered_rules?.some(rule => rule.includes('void') || rule.includes('cancel'))) s.void_count++;
      if (t.triggered_rules?.some(rule => rule.includes('discount'))) s.discount_count++;
    });

    return Object.values(storeMap).map(s => ({
      ...s,
      fraud_rate: s.total_transactions > 0
        ? (s.flagged_transactions / s.total_transactions) * 100
        : 0,
      manual_entry_rate: s.total_transactions > 0 ? (s.manual_entry_count / s.total_transactions) * 100 : 0,
      void_rate: s.total_transactions > 0 ? (s.void_count / s.total_transactions) * 100 : 0,
      discount_rate: s.total_transactions > 0 ? (s.discount_count / s.total_transactions) * 100 : 0,
    })).sort((a, b) => b.fraud_rate - a.fraud_rate);
  }, [transactions, storeNames]);

  const totalStores = storeScores.length;
  const avgFraudRate = totalStores > 0
    ? storeScores.reduce((acc, s) => acc + s.fraud_rate, 0) / totalStores
    : 0;
  const highRiskStores = storeScores.filter(s => s.fraud_rate >= 30).length;

  const getFraudRateBadge = (rate: number) => {
    if (rate >= 30) return <Badge className="bg-red-50 text-red-700 border-red-200">{rate.toFixed(1)}%</Badge>;
    if (rate >= 15) return <Badge className="bg-amber-50 text-amber-700 border-amber-200">{rate.toFixed(1)}%</Badge>;
    return <Badge className="bg-green-50 text-green-700 border-green-200">{rate.toFixed(1)}%</Badge>;
  };

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="bg-white border-gray-200 p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-500">Active Stores</p>
              <p className="text-2xl font-bold text-gray-800">{totalStores}</p>
            </div>
            <div className="p-3 bg-blue-50 rounded-lg"><Store className="h-6 w-6 text-blue-600" /></div>
          </div>
        </Card>
        <Card className="bg-white border-gray-200 p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-500">Avg Flag Rate</p>
              <p className="text-2xl font-bold text-gray-800">{avgFraudRate.toFixed(1)}%</p>
            </div>
            <div className="p-3 bg-amber-50 rounded-lg"><TrendingUp className="h-6 w-6 text-amber-600" /></div>
          </div>
        </Card>
        <Card className="bg-white border-gray-200 p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-gray-500">High Risk Stores</p>
              <p className="text-2xl font-bold text-gray-800">{highRiskStores}</p>
            </div>
            <div className="p-3 bg-red-50 rounded-lg"><AlertCircle className="h-6 w-6 text-red-600" /></div>
          </div>
        </Card>
      </div>

      <Card className="bg-white border-gray-200 shadow-sm">
        <div className="p-4 border-b border-gray-200">
          <h3 className="text-lg font-semibold text-gray-800">Store Performance & Risk Assessment</h3>
          <p className="text-sm text-gray-500 mt-1">Sorted by flag rate (highest risk first)</p>
        </div>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="bg-gray-50 hover:bg-gray-50 border-gray-200">
                <TableHead className="text-gray-600">Store</TableHead>
                <TableHead className="text-right text-gray-600">Total</TableHead>
                <TableHead className="text-right text-gray-600">Genuine</TableHead>
                <TableHead className="text-right text-gray-600">Suspicious</TableHead>
                <TableHead className="text-right text-gray-600">Fraudulent</TableHead>
                <TableHead className="text-gray-600">Flag Rate</TableHead>
                <TableHead className="text-right text-gray-600">Manual %</TableHead>
                <TableHead className="text-right text-gray-600">Void %</TableHead>
                <TableHead className="text-right text-gray-600">Discount %</TableHead>
                <TableHead className="text-right text-gray-600">Revenue</TableHead>
                <TableHead className="text-gray-600">Risk</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {storeScores.map((store, index) => (
                <TableRow
                  key={store.store_id}
                  className={`border-gray-100 hover:bg-blue-50/50 ${index % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}`}
                >
                  <TableCell>
                    <div className="font-medium text-gray-800">{store.store_name}</div>
                    <div className="text-xs text-gray-400 font-mono">{store.store_id}</div>
                  </TableCell>
                  <TableCell className="text-right font-mono text-gray-700">{store.total_transactions}</TableCell>
                  <TableCell className="text-right font-mono text-green-600">{store.genuine_count}</TableCell>
                  <TableCell className="text-right font-mono text-amber-600">{store.suspicious_count}</TableCell>
                  <TableCell className="text-right font-mono text-red-600">{store.fraudulent_count}</TableCell>
                  <TableCell>{getFraudRateBadge(store.fraud_rate)}</TableCell>
                  <TableCell className="text-right font-mono text-gray-700">{store.manual_entry_rate.toFixed(1)}%</TableCell>
                  <TableCell className="text-right font-mono text-gray-700">{store.void_rate.toFixed(1)}%</TableCell>
                  <TableCell className="text-right font-mono text-gray-700">{store.discount_rate.toFixed(1)}%</TableCell>
                  <TableCell className="text-right font-mono text-gray-700">
                    {'\u20B9'}{store.total_value.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                  </TableCell>
                  <TableCell>
                    {store.fraud_rate >= 30 ? (
                      <div className="flex items-center gap-1 text-red-600">
                        <TrendingUp className="h-3.5 w-3.5" />
                        <span className="text-xs font-semibold">High</span>
                      </div>
                    ) : store.fraud_rate >= 15 ? (
                      <div className="flex items-center gap-1 text-amber-600">
                        <AlertCircle className="h-3.5 w-3.5" />
                        <span className="text-xs font-semibold">Medium</span>
                      </div>
                    ) : (
                      <div className="flex items-center gap-1 text-green-600">
                        <TrendingDown className="h-3.5 w-3.5" />
                        <span className="text-xs font-semibold">Low</span>
                      </div>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Card>
    </div>
  );
}
